"""
Memory Manager
==============
Manages session state, conversation history, user preferences,
and context summarization. Acts as the "memory" of the orchestrator.

Design:
- In-memory cache for active session (fast reads)
- Firestore for persistence (survives restarts)
- Background summarization for long conversations
"""

import asyncio
import logging
import time
from typing import Optional

from core.config import settings
from core.models import ConversationTurn, SessionMetadata
from services.firestore_client import FirestoreClient

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    Manages conversation memory and session state.
    
    Strategy:
    - Keep last N turns in memory (fast access for context)
    - Persist all turns to Firestore (async, non-blocking)
    - Summarize older turns periodically (prevent context overflow)
    - Track user preferences learned from conversation
    """

    MAX_MEMORY_TURNS = 20      # Turns kept in active memory
    SUMMARIZE_THRESHOLD = 15   # Summarize when this many turns accumulate

    def __init__(self, session_id: str, firestore: FirestoreClient):
        self.session_id = session_id
        self.firestore = firestore

        # In-memory state
        self._turns: list[ConversationTurn] = []
        self._metadata: Optional[SessionMetadata] = None
        self._context_summary: str = ""
        self._preferences: dict = {}

        self._logger = logger.getChild(f"memory.{session_id[:8]}")

    async def create_session(self):
        """Create a new session in Firestore and memory."""
        self._metadata = SessionMetadata(session_id=self.session_id)
        self._turns = []
        self._context_summary = ""
        self._preferences = {}

        # Persist to Firestore (async, non-blocking)
        asyncio.create_task(
            self.firestore.create_session(
                self.session_id,
                self._metadata.model_dump(),
            )
        )

        self._logger.info("Session created")

    async def load_session(self, session_id: str) -> bool:
        """Load an existing session from Firestore."""
        session_data = await self.firestore.get_session(session_id)
        if not session_data:
            return False

        self._metadata = SessionMetadata(**session_data)
        self._turns = []

        # Load recent conversation history
        turns_data = await self.firestore.get_recent_turns(
            session_id, limit=self.MAX_MEMORY_TURNS
        )
        for turn_data in turns_data:
            self._turns.append(ConversationTurn(**turn_data))

        self._logger.info(f"Session loaded: {len(self._turns)} turns")
        return True

    async def save_turn(self, turn: ConversationTurn):
        """Save a conversation turn to memory and Firestore."""
        # Add to in-memory list
        self._turns.append(turn)

        # Trim memory if needed
        if len(self._turns) > self.MAX_MEMORY_TURNS:
            # Summarize old turns before removing
            if len(self._turns) >= self.SUMMARIZE_THRESHOLD:
                asyncio.create_task(self._summarize_old_turns())
            self._turns = self._turns[-self.MAX_MEMORY_TURNS:]

        # Persist to Firestore (async, non-blocking)
        asyncio.create_task(
            self.firestore.save_turn(
                self.session_id,
                turn.turn_id,
                turn.model_dump(),
            )
        )

    async def get_context_summary(self) -> str:
        """
        Get a summary of the conversation for sub-agent context.
        
        Returns a compact string that sub-agents can use to understand
        the conversation history without consuming too many tokens.
        """
        parts = []

        # Include the summary of older turns
        if self._context_summary:
            parts.append(f"Earlier conversation summary: {self._context_summary}")

        # Include recent turns (compact format)
        recent = self._turns[-10:]  # Last 10 turns for immediate context
        if recent:
            parts.append("Recent conversation:")
            for turn in recent:
                role_label = "User" if turn.role == "user" else "HadithiAI"
                content_preview = turn.content[:150]
                parts.append(f"  {role_label}: {content_preview}")

        # Include preferences
        if self._preferences:
            prefs = ", ".join(f"{k}={v}" for k, v in self._preferences.items())
            parts.append(f"User preferences: {prefs}")

        return "\n".join(parts) if parts else "New conversation, no history yet."

    async def update_preferences(self, updates: dict):
        """Update user preferences (language, age group, etc.)."""
        self._preferences.update(updates)

        if self._metadata:
            for key, value in updates.items():
                if hasattr(self._metadata, key):
                    setattr(self._metadata, key, value)

        # Persist to Firestore
        asyncio.create_task(
            self.firestore.update_session(self.session_id, updates)
        )

    async def finalize_session(self):
        """Final session save on disconnect."""
        if self._metadata:
            await self.firestore.update_session(
                self.session_id,
                {
                    "last_active": time.time(),
                    "turn_count": len(self._turns),
                    "final_summary": self._context_summary,
                },
            )
        self._logger.info(f"Session finalized: {len(self._turns)} turns")

    async def _summarize_old_turns(self):
        """
        Summarize old turns to prevent context window overflow.
        
        This runs in the background and doesn't block the conversation.
        Uses a lightweight Gemini call to compress history.
        """
        try:
            old_turns = self._turns[:self.SUMMARIZE_THRESHOLD]
            text = "\n".join(
                f"{'User' if t.role == 'user' else 'Agent'}: {t.content[:200]}"
                for t in old_turns
            )

            # For now, simple truncation-based summarization
            # In production, you'd call Gemini to summarize
            self._context_summary = (
                f"The conversation covered: "
                f"{len(old_turns)} turns discussing African stories and culture. "
                f"Key topics: {self._extract_topics(old_turns)}"
            )

            self._logger.info(
                f"Summarized {len(old_turns)} old turns",
                extra={"event": "memory_summarize"},
            )
        except Exception as e:
            self._logger.warning(f"Summarization failed: {e}")

    @staticmethod
    def _extract_topics(turns: list[ConversationTurn]) -> str:
        """Extract key topics from turns (simple keyword extraction)."""
        all_text = " ".join(t.content for t in turns).lower()
        topics = []
        keywords = [
            "story", "riddle", "yoruba", "zulu", "swahili", "kikuyu",
            "ashanti", "maasai", "anansi", "trickster", "proverb",
            "wisdom", "creation", "ancestors", "animals",
        ]
        for keyword in keywords:
            if keyword in all_text:
                topics.append(keyword)
        return ", ".join(topics[:5]) if topics else "general African culture"
