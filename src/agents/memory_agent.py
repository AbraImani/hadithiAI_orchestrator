"""
Memory Agent
============
ADK-style agent responsible for session memory persistence,
context summarization, and conversation history management.
Runs in the ParallelAgent enrichment pipeline (non-blocking).
"""

import asyncio
import logging
import time
from typing import Optional

from core.models import ConversationTurn
from services.firestore_client import FirestoreClient
from services.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


class MemoryAgent:
    """
    Manages session memory and conversation context.

    Responsibilities:
    - Persist conversation turns to Firestore (fire-and-forget)
    - Summarize conversation history for context windows
    - Track user preferences and cultural affinities
    - Provide context summaries for sub-agent prompts

    This agent runs in the enrichment ParallelAgent pipeline
    alongside the VisualAgent. It never blocks the story stream.
    """

    AGENT_NAME = "memory"

    def __init__(self, session_id: str, firestore: FirestoreClient):
        self.session_id = session_id
        self.memory_manager = MemoryManager(session_id, firestore)
        self._logger = logger.getChild(f"memory.{session_id[:8]}")

    async def initialize(self):
        """Create or restore session in Firestore."""
        await self.memory_manager.create_session()
        self._logger.info("Memory agent initialized")

    async def persist_turn(
        self,
        role: str,
        content: str,
        agent: Optional[str] = None,
        intent: Optional[str] = None,
        cultural_confidence: Optional[float] = None,
    ):
        """
        Persist a conversation turn. Fire-and-forget safe.
        Errors are logged but never propagated.
        """
        start = time.time()
        try:
            import uuid
            turn = ConversationTurn(
                turn_id=f"turn_{uuid.uuid4().hex[:8]}",
                role=role,
                content=content,
                agent=agent,
                intent=intent,
                cultural_confidence=cultural_confidence,
            )
            await self.memory_manager.save_turn(turn)

            elapsed = (time.time() - start) * 1000
            self._logger.debug(
                f"Turn persisted in {elapsed:.0f}ms",
                extra={
                    "event": "memory_persist",
                    "latency_ms": elapsed,
                    "role": role,
                },
            )
        except Exception as e:
            self._logger.warning(
                f"Failed to persist turn (non-critical): {e}",
                extra={"event": "memory_persist_error"},
            )

    async def get_context_summary(self) -> str:
        """
        Get a summary of the conversation so far.
        Used to populate session_context in agent requests.
        """
        try:
            return await self.memory_manager.get_context_summary()
        except Exception as e:
            self._logger.warning(f"Failed to get context summary: {e}")
            return ""

    async def update_preferences(self, preferences: dict):
        """Update user preferences (language, age group, region)."""
        try:
            await self.memory_manager.update_preferences(preferences)
        except Exception as e:
            self._logger.warning(f"Failed to update preferences: {e}")

    async def get_preferences(self) -> dict:
        """Get current user preferences."""
        try:
            return await self.memory_manager.get_preferences()
        except Exception:
            return {}

    async def finalize(self):
        """Finalize the session (called on disconnect)."""
        try:
            await self.memory_manager.finalize_session()
        except Exception as e:
            self._logger.warning(f"Failed to finalize session: {e}")
