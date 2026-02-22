"""
Firestore Client
================
Manages all Firestore operations: session storage, conversation
history, cache, and cultural knowledge base.
"""

import logging
import time
from typing import Optional

from core.config import settings

logger = logging.getLogger(__name__)


class FirestoreClient:
    """
    Firestore operations for HadithiAI Live.
    
    Collections:
    - sessions/{session_id}                  → Session metadata
    - sessions/{session_id}/conversation     → Turn-by-turn history
    - cache/stories/{culture}_{theme}        → Pre-generated story fragments
    - knowledge/{culture}                    → Cultural knowledge base
    """

    def __init__(self):
        self._db = None
        self._initialized = False

    def _get_db(self):
        """Lazy-initialize Firestore client."""
        if self._db is None:
            try:
                from google.cloud import firestore
                self._db = firestore.AsyncClient(
                    project=settings.PROJECT_ID,
                    database=settings.FIRESTORE_DATABASE,
                )
                self._initialized = True
                logger.info("Firestore client initialized")
            except Exception as e:
                logger.warning(f"Firestore unavailable: {e}")
                return None
        return self._db

    async def create_session(self, session_id: str, metadata: dict) -> bool:
        """Create a new session document."""
        db = self._get_db()
        if not db:
            return False

        try:
            doc_ref = db.collection("sessions").document(session_id)
            await doc_ref.set({
                **metadata,
                "created_at": time.time(),
                "last_active": time.time(),
                "turn_count": 0,
            })
            logger.info(f"Session created: {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to create session: {e}")
            return False

    async def update_session(self, session_id: str, updates: dict) -> bool:
        """Update session metadata."""
        db = self._get_db()
        if not db:
            return False

        try:
            doc_ref = db.collection("sessions").document(session_id)
            await doc_ref.update({
                **updates,
                "last_active": time.time(),
            })
            return True
        except Exception as e:
            logger.error(f"Failed to update session: {e}")
            return False

    async def get_session(self, session_id: str) -> Optional[dict]:
        """Retrieve session metadata."""
        db = self._get_db()
        if not db:
            return None

        try:
            doc_ref = db.collection("sessions").document(session_id)
            doc = await doc_ref.get()
            return doc.to_dict() if doc.exists else None
        except Exception as e:
            logger.error(f"Failed to get session: {e}")
            return None

    async def save_turn(self, session_id: str, turn_id: str, turn_data: dict) -> bool:
        """Save a conversation turn."""
        db = self._get_db()
        if not db:
            return False

        try:
            doc_ref = (
                db.collection("sessions")
                .document(session_id)
                .collection("conversation")
                .document(turn_id)
            )
            await doc_ref.set({
                **turn_data,
                "timestamp": time.time(),
            })

            # Increment turn count
            session_ref = db.collection("sessions").document(session_id)
            from google.cloud.firestore import Increment
            await session_ref.update({"turn_count": Increment(1)})

            return True
        except Exception as e:
            logger.error(f"Failed to save turn: {e}")
            return False

    async def get_recent_turns(
        self, session_id: str, limit: int = 20
    ) -> list[dict]:
        """Get the most recent conversation turns."""
        db = self._get_db()
        if not db:
            return []

        try:
            query = (
                db.collection("sessions")
                .document(session_id)
                .collection("conversation")
                .order_by("timestamp", direction="DESCENDING")
                .limit(limit)
            )
            docs = await query.get()
            turns = [doc.to_dict() for doc in docs]
            turns.reverse()  # Chronological order
            return turns
        except Exception as e:
            logger.error(f"Failed to get turns: {e}")
            return []

    async def get_cached_content(self, cache_key: str) -> Optional[str]:
        """Retrieve cached content (pre-generated stories, etc.)."""
        db = self._get_db()
        if not db:
            return None

        try:
            doc_ref = db.collection("cache").document(cache_key)
            doc = await doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                return data.get("content")
            return None
        except Exception as e:
            logger.error(f"Cache read failed: {e}")
            return None

    async def set_cached_content(self, cache_key: str, content: str, ttl_hours: int = 24):
        """Cache content with TTL."""
        db = self._get_db()
        if not db:
            return

        try:
            doc_ref = db.collection("cache").document(cache_key)
            await doc_ref.set({
                "content": content,
                "created_at": time.time(),
                "expires_at": time.time() + (ttl_hours * 3600),
            })
        except Exception as e:
            logger.error(f"Cache write failed: {e}")
