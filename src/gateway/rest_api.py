"""
Flutter REST API Gateway
========================
REST endpoints designed for the Flutter mobile application.
Complements the WebSocket endpoint for operations that don't
require persistent bidirectional streaming.

Endpoints:
  POST   /api/v1/sessions              → Create a new session
  GET    /api/v1/sessions/{id}         → Get session metadata
  DELETE /api/v1/sessions/{id}         → End a session
  POST   /api/v1/sessions/{id}/text    → Send text (non-streaming)
  GET    /api/v1/sessions/{id}/history → Get conversation history
  POST   /api/v1/sessions/{id}/preferences → Update preferences
  GET    /api/v1/health                → Detailed health check
  GET    /api/v1/agents                → List available agent capabilities
"""

import asyncio
import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.config import settings
from core.models import ServerMessageType, SessionMetadata
from orchestrator.a2a_router import list_agent_cards

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["Flutter REST API"])


# ─── Request / Response Models ────────────────────────────────────

class CreateSessionRequest(BaseModel):
    """Request to create a new conversation session."""
    language: str = "en"
    region: Optional[str] = None
    age_group: str = "adult"


class CreateSessionResponse(BaseModel):
    """Response after session creation."""
    session_id: str
    websocket_url: str
    created_at: float


class TextInputRequest(BaseModel):
    """Send a text message (non-streaming, returns full response)."""
    text: str = Field(..., min_length=1, max_length=2000)


class TextInputResponse(BaseModel):
    """Response to a text input."""
    session_id: str
    response_text: str
    agent: str = "orchestrator"
    latency_ms: float


class PreferencesRequest(BaseModel):
    """Update user preferences."""
    language: Optional[str] = None
    age_group: Optional[str] = None
    region: Optional[str] = None


class SessionInfoResponse(BaseModel):
    """Session metadata response."""
    session_id: str
    created_at: float
    last_active: float
    turn_count: int
    language: str
    region: Optional[str] = None
    age_group: str


class HealthDetailResponse(BaseModel):
    """Detailed health information."""
    status: str
    service: str
    version: str
    uptime_seconds: float
    active_sessions: int
    gemini_pool_ready: bool


# ─── Module state ─────────────────────────────────────────────────

_start_time = time.time()


# ─── Endpoints ────────────────────────────────────────────────────

@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session(req: CreateSessionRequest, request: Request):
    """
    Create a new conversation session.

    Returns the session_id and websocket_url for the Flutter client
    to establish a bidirectional audio stream.
    """
    session_id = uuid.uuid4().hex[:12]

    # Persist initial session metadata
    firestore = request.app.state.firestore
    await firestore.create_session(session_id, {
        "language_pref": req.language,
        "region_pref": req.region,
        "age_group": req.age_group,
    })

    # Build WebSocket URL relative to the server
    host = request.headers.get("host", "localhost:8080")
    scheme = "wss" if request.url.scheme == "https" else "ws"
    ws_url = f"{scheme}://{host}/ws?session_id={session_id}"

    logger.info(
        f"Session created via REST: {session_id}",
        extra={"event": "rest_session_create", "session_id": session_id},
    )

    return CreateSessionResponse(
        session_id=session_id,
        websocket_url=ws_url,
        created_at=time.time(),
    )


@router.get("/sessions/{session_id}", response_model=SessionInfoResponse)
async def get_session(session_id: str, request: Request):
    """Get session metadata."""
    firestore = request.app.state.firestore
    data = await firestore.get_session(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionInfoResponse(
        session_id=session_id,
        created_at=data.get("created_at", 0),
        last_active=data.get("last_active", 0),
        turn_count=data.get("turn_count", 0),
        language=data.get("language_pref", "en"),
        region=data.get("region_pref"),
        age_group=data.get("age_group", "adult"),
    )


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    """End and clean up a session."""
    firestore = request.app.state.firestore
    data = await firestore.get_session(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")

    await firestore.update_session(session_id, {
        "ended": True,
        "ended_at": time.time(),
    })

    logger.info(
        f"Session ended via REST: {session_id}",
        extra={"event": "rest_session_end", "session_id": session_id},
    )

    return {"status": "ended", "session_id": session_id}


@router.get("/sessions/{session_id}/history")
async def get_history(session_id: str, request: Request, limit: int = 50):
    """Get conversation history for a session."""
    firestore = request.app.state.firestore
    data = await firestore.get_session(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")

    turns = await firestore.get_recent_turns(session_id, limit=limit)
    return {
        "session_id": session_id,
        "turns": turns,
        "total": len(turns),
    }


@router.post("/sessions/{session_id}/preferences")
async def update_preferences(
    session_id: str, req: PreferencesRequest, request: Request
):
    """Update user preferences for a session."""
    firestore = request.app.state.firestore
    data = await firestore.get_session(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")

    updates = {}
    if req.language:
        updates["language_pref"] = req.language
    if req.age_group:
        updates["age_group"] = req.age_group
    if req.region:
        updates["region_pref"] = req.region

    if updates:
        await firestore.update_session(session_id, updates)

    return {"status": "updated", "session_id": session_id, "updates": updates}


@router.get("/health", response_model=HealthDetailResponse)
async def detailed_health(request: Request):
    """Detailed health check with service metrics."""
    from gateway.websocket_handler import active_connections

    gemini_ready = hasattr(request.app.state, "gemini_pool") and \
                   request.app.state.gemini_pool is not None

    return HealthDetailResponse(
        status="healthy",
        service="hadithiai-live",
        version="2.0.0",
        uptime_seconds=time.time() - _start_time,
        active_sessions=len(active_connections),
        gemini_pool_ready=gemini_ready,
    )


@router.get("/agents")
async def list_agents():
    """
    List available agent capabilities (Agent Cards).
    Useful for Flutter UI to show what the app can do.
    """
    return {
        "agents": list_agent_cards(),
        "total": len(list_agent_cards()),
    }
