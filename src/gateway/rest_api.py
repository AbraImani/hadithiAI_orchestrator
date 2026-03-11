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
  GET    /api/v1/sessions/{id}/history → Get conversation history
  POST   /api/v1/sessions/{id}/preferences → Update preferences
  GET    /api/v1/health                → Detailed health check
  GET    /api/v1/agents                → List available agent capabilities
  POST   /api/v1/stories/generate      → Generate story catalog entries
  POST   /api/v1/riddles/generate      → Generate a riddle (RiddleModel)
  POST   /api/v1/riddles/{id}/answer   → Check a riddle answer
"""

import asyncio
import logging
import time
import uuid
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.config import settings
from core.models import (
    ServerMessageType,
    SessionMetadata,
    RiddleModel,
    StoryCategoryModel,
)
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


class GenerateStoriesRequest(BaseModel):
    """Request to generate story catalog entries."""
    culture: str = "african"
    region: str = ""
    count: int = Field(default=5, ge=1, le=20)
    language: str = "en"


class GenerateRiddleRequest(BaseModel):
    """Request to generate a riddle matching Flutter RiddleModel."""
    culture: str = "East African"
    difficulty: str = "medium"
    language: str = "en"


class CheckAnswerRequest(BaseModel):
    """Request to check a riddle answer."""
    selected_answer: str


class CheckAnswerResponse(BaseModel):
    """Response after checking a riddle answer."""
    correct: bool
    correct_answer: str
    explanation: str = ""


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
    # Cloud Run terminates TLS at load balancer, so check X-Forwarded-Proto
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    scheme = "wss" if proto == "https" else "ws"
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


# ─── Story Catalog Endpoints ─────────────────────────────────────

# In-memory cache for generated stories (cleared on restart)
_story_cache: dict[str, list[dict]] = {}


@router.post("/stories/generate", response_model=List[StoryCategoryModel])
async def generate_stories(req: GenerateStoriesRequest, request: Request):
    """
    Generate story catalog entries matching Flutter StoryCategoryModel.

    Returns a list of story metadata (title, description, imageUrl,
    day, month, region) for the mobile UI story browser.
    """
    cache_key = f"{req.culture}_{req.region}_{req.language}_{req.count}"
    if cache_key in _story_cache:
        return _story_cache[cache_key]

    gemini_pool = request.app.state.gemini_pool

    prompt = f"""Generate exactly {req.count} African story catalog entries as a JSON array.
Each entry must have this exact structure:
{{
  "title": "Story title (short, evocative)",
  "description": "2-3 sentence description of the story",
  "imageUrl": "",
  "day": <day number 1-30>,
  "month": "<month name>",
  "region": "{req.region or req.culture}"
}}

Requirements:
- Stories must be from {req.culture} tradition
- Region: {req.region or req.culture}
- Language: {req.language}
- Each story should have a unique theme (wisdom, trickster, creation, courage, love, origin, moral)
- Titles should be evocative and culturally authentic
- Descriptions should make the reader want to hear the story
- Distribute days across the month

Respond ONLY with a valid JSON array. No markdown, no code blocks."""

    system = "You are a story catalog generator. Output only valid JSON arrays."

    try:
        result_parts = []
        async for chunk in gemini_pool.generate_text_stream(
            prompt=prompt, system_instruction=system
        ):
            result_parts.append(chunk)
        raw = "".join(result_parts)

        # Parse JSON
        import json
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()

        stories_data = json.loads(cleaned)
        if not isinstance(stories_data, list):
            stories_data = [stories_data]

        stories = []
        for s in stories_data[:req.count]:
            stories.append(StoryCategoryModel(
                title=s.get("title", "Untitled Story"),
                description=s.get("description", "A tale from the oral tradition."),
                imageUrl=s.get("imageUrl", ""),
                day=s.get("day", 1),
                month=s.get("month", ""),
                region=s.get("region", req.region or req.culture),
            ))

        # Cache for performance
        _story_cache[cache_key] = stories
        return stories

    except Exception as e:
        logger.error(f"Story catalog generation failed: {e}", exc_info=True)
        # Return a minimal fallback
        return [
            StoryCategoryModel(
                title="The Wisdom of Anansi",
                description="Anansi the spider outsmarts all the animals "
                            "to collect the world's wisdom in a pot.",
                imageUrl="",
                day=1,
                month="January",
                region=req.region or req.culture,
            )
        ]


# ─── Riddle Game Endpoints ───────────────────────────────────────

# In-memory riddle session store (maps riddle_id → riddle data)
_active_riddles: dict[str, dict] = {}


@router.post("/riddles/generate", response_model=RiddleModel)
async def generate_riddle(req: GenerateRiddleRequest, request: Request):
    """
    Generate a riddle matching Flutter RiddleModel.

    Returns: {id, question, choices: [{text: bool}], tip, help, language}
    The Flutter app displays 4 choices and the user picks one.
    """
    from agents.riddle_agent import RiddleAgent

    gemini_pool = request.app.state.gemini_pool
    firestore = request.app.state.firestore

    agent = RiddleAgent(gemini_pool)
    result = await agent.execute({
        "culture": req.culture,
        "difficulty": req.difficulty,
    })

    # Build RiddleModel from agent output
    riddle = RiddleModel(
        id=result.get("id", f"riddle_{uuid.uuid4().hex[:8]}"),
        question=result.get("question", result.get("riddle_text", "")),
        choices=result.get("choices", []),
        tip=result.get("tip"),
        help=result.get("help"),
        language=result.get("language", req.language),
    )

    # Store for answer checking
    _active_riddles[riddle.id] = result

    logger.info(
        f"Riddle generated: {riddle.id}",
        extra={"event": "riddle_generated", "culture": req.culture},
    )

    return riddle


@router.post("/riddles/{riddle_id}/answer", response_model=CheckAnswerResponse)
async def check_riddle_answer(riddle_id: str, req: CheckAnswerRequest):
    """
    Check if the selected answer to a riddle is correct.

    The client sends the answer text they selected; we check
    it against the stored riddle data.
    """
    riddle_data = _active_riddles.get(riddle_id)
    if not riddle_data:
        raise HTTPException(status_code=404, detail="Riddle not found or expired")

    choices = riddle_data.get("choices", [])
    correct_answer = ""
    is_correct = False

    for choice in choices:
        if isinstance(choice, dict):
            for text, correct in choice.items():
                if correct is True:
                    correct_answer = text
                if text == req.selected_answer and correct is True:
                    is_correct = True

    return CheckAnswerResponse(
        correct=is_correct,
        correct_answer=correct_answer,
        explanation=riddle_data.get("explanation", ""),
    )
