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
  POST   /api/v1/stories/daystory      → Generate full story narration (content only)
  POST   /api/v1/stories/daystory/image → Generate story illustration (image only)
  POST   /api/v1/riddles/generate      → Generate a riddle (RiddleModel)
  POST   /api/v1/riddles/{id}/answer   → Check a riddle answer
"""

import asyncio
import logging
import re
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
    # Optional story context — when set, the Live audio session will
    # narrate this specific story when the user starts talking.
    story_id: Optional[str] = None
    story_title: Optional[str] = None
    story_summary: Optional[str] = None


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


class DayStoryRequest(BaseModel):
    """Request to generate or retrieve day-story content/image.

    These 4 fields come from the Flutter StoryModel and identify
    the story. The backend uses them to generate content and/or image.
    """
    id: str = Field(..., description="Story id from Flutter StoryModel")
    title: str = Field(..., description="Story title")
    summary: str = Field(..., description="Short summary / teaser")
    language: str = Field(default="fr", description="Language code, e.g. 'fr', 'en', 'sw'")


class DayStoryContentResponse(BaseModel):
    """Full story narrative returned by POST /stories/daystory."""
    id: str
    content: str  # Complete narration text — used by Live session too


class DayStoryImageResponse(BaseModel):
    """Story illustration returned by POST /stories/daystory/image."""
    id: str
    image_url: str = ""   # Cloud Storage public URL when available
    image_base64: str = ""  # data:image/png;base64,... fallback


# ─── Module state ─────────────────────────────────────────────────

_start_time = time.time()


# Gerund-form reasoning section titles emitted by Gemini 2.5 Flash thinking mode.
_THINKING_SECTION_RE = re.compile(
    r"\*\*(?:Consider|Craft|Generat|Defin|Refin|Plan|Structur|Evaluat|"
    r"Analyz|Review|Synthes|Explor|Identif|Assess|Develop|Build|Formulat|"
    r"Outlin|Creat|Design|Map|Gather|Validat|Select|Execut|Initializ|"
    r"Process|Describ|Establish|Organiz|Implement|Determin|Prepar)[a-z]*"
    r"\b[^*\n]*\*\*[^\n]*",
    re.IGNORECASE,
)
# First-person internal-monologue lines produced by thinking models.
_MONOLOGUE_LINE_RE = re.compile(
    r"(?im)^(?:I(?:'m| am| will| have| need| want| plan|'ve) now?\b|"
    r"My goal (?:is|here)\b|The (?:user|model|story) (?:wants|needs|requires)\b|"
    r"I (?:should|must|can|would|could)\b|Let me (?:now |first |also )?\b|"
    r"I'(?:ll|d) (?:now |then |also )?\b)[^\n]*$"
)


def _sanitize_story_text(text: str) -> str:
    """Remove model reasoning/thought traces from story fields."""
    if not text:
        return ""

    cleaned = text.strip()

    # Remove XML-like thought blocks
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<thought>.*?</thought>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)

    # Strip Gemini 2.5 Flash thinking section headers
    # e.g. "**Considering Story Framework**", "**Crafting Initial Story**"
    cleaned = _THINKING_SECTION_RE.sub("", cleaned)

    # Strip first-person internal-monologue lines
    cleaned = _MONOLOGUE_LINE_RE.sub("", cleaned)

    # Strip inline tool-call code references the model leaks into text
    # e.g. tell_story(culture='San', theme='wisdom', complexity='child')
    cleaned = re.sub(r"\b\w+\([^)]{0,120}\)", "", cleaned)

    # Remove common reasoning prefixes/lines
    cleaned = re.sub(
        r"(?im)^\s*(thought|reasoning|analysis|chain\s*of\s*thought|internal\s*monologue)\s*:\s*.*$",
        "",
        cleaned,
    )
    cleaned = re.sub(r"(?im)^\s*let'?s\s+think\b.*$", "", cleaned)
    cleaned = re.sub(r"(?im)^\s*i\s+should\b.*$", "", cleaned)

    # Remove markdown/code fences and collapse whitespace
    cleaned = re.sub(r"```\w*", "", cleaned)
    cleaned = re.sub(r"(?is)<\s*/?analysis\s*>", "", cleaned)
    cleaned = re.sub(r"(?is)<\s*/?reasoning\s*>", "", cleaned)
    cleaned = re.sub(r"(?im)^\s*(scratchpad|deliberation|notes?)\s*:\s*.*$", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)

    return cleaned.strip()


def _extract_json_array(text: str) -> str:
    """Extract the first balanced top-level JSON array from text."""
    start = text.find("[")
    if start < 0:
        return ""

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]

        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    return ""


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
    # Persist initial session metadata (include story context if provided
    # so the WebSocket handler can inject it into the Live system prompt).
    session_meta: dict = {
        "language_pref": req.language,
        "region_pref": req.region,
        "age_group": req.age_group,
    }
    if req.story_id:
        session_meta["story_id"] = req.story_id
    if req.story_title:
        session_meta["story_title"] = req.story_title
    if req.story_summary:
        session_meta["story_summary"] = req.story_summary

    await firestore.create_session(session_id, session_meta)

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


@router.get("/debug/text-gen")
async def debug_text_generation(request: Request):
    """
    Debug endpoint: test Gemini text generation through the pool
    and directly against both client strategies to expose raw errors.
    """
    import traceback
    from google import genai
    from google.genai import types
    from core.config import settings

    gemini_pool = request.app.state.gemini_pool
    result = {
        "status": "unknown",
        "chunks": [],
        "error": None,
        "pool_result": None,
        "vertex_direct": {},
        "apikey_direct": {},
    }

    try:
        chunks = []
        async for chunk in gemini_pool.generate_text_stream(
            prompt="Say hello in exactly 3 words.",
            system_instruction="Be brief. Respond with exactly 3 words.",
        ):
            chunks.append(chunk)
        result["chunks"] = chunks
        result["full_text"] = "".join(chunks)
        if "[Generation error" in result["full_text"]:
            result["status"] = "generation_error"
        else:
            result["status"] = "success"
        result["pool_result"] = result["status"]
    except Exception as e:
        result["status"] = "exception"
        result["error"] = f"{type(e).__name__}: {str(e)}"
        result["traceback"] = traceback.format_exc()

    # Direct Vertex test
    try:
        vertex_client = genai.Client(
            vertexai=True,
            project=settings.PROJECT_ID,
            location=settings.REGION,
        )
        resp = await vertex_client.aio.models.generate_content(
            model=settings.GEMINI_TEXT_MODEL,
            contents="Say hello in exactly 3 words.",
            config=types.GenerateContentConfig(
                system_instruction="Be brief. Respond with exactly 3 words.",
                max_output_tokens=64,
            ),
        )
        result["vertex_direct"] = {
            "ok": True,
            "text": getattr(resp, "text", ""),
        }
    except Exception as e:
        result["vertex_direct"] = {
            "ok": False,
            "error": f"{type(e).__name__}: {str(e)}",
        }

    # Direct API key test
    try:
        key_client = genai.Client(api_key=settings.GEMINI_API_KEY)
        resp = await key_client.aio.models.generate_content(
            model=settings.GEMINI_TEXT_MODEL,
            contents="Say hello in exactly 3 words.",
            config=types.GenerateContentConfig(
                system_instruction="Be brief. Respond with exactly 3 words.",
                max_output_tokens=64,
            ),
        )
        result["apikey_direct"] = {
            "ok": True,
            "text": getattr(resp, "text", ""),
        }
    except Exception as e:
        result["apikey_direct"] = {
            "ok": False,
            "error": f"{type(e).__name__}: {str(e)}",
        }

    result["configured_text_model"] = settings.GEMINI_TEXT_MODEL
    result["project_id"] = settings.PROJECT_ID
    result["region"] = settings.REGION

    return result


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
- Absolutely forbidden: reasoning traces, thought process, analysis text, planning notes, XML tags like <think>, markdown

Respond ONLY with a valid JSON array. No markdown, no code blocks."""

    system = (
        "You are a story catalog generator. "
        "Output only valid JSON arrays with user-facing story content. "
        "Never include reasoning, thought traces, or internal analysis."
    )

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
        cleaned = _sanitize_story_text(cleaned)
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()

        # If the model wrapped JSON in extra text/reasoning, extract first
        # balanced top-level array safely.
        if not cleaned.startswith("["):
            extracted = _extract_json_array(cleaned)
            if extracted:
                cleaned = extracted

        stories_data = json.loads(cleaned)
        if not isinstance(stories_data, list):
            stories_data = [stories_data]

        stories = []
        for s in stories_data[:req.count]:
            title = _sanitize_story_text(str(s.get("title", "Untitled Story")))
            description = _sanitize_story_text(
                str(s.get("description", "A tale from the oral tradition."))
            )

            # Safety fallback if output is still contaminated by reasoning traces
            if re.search(r"(?i)\b(thought|reasoning|analysis|chain of thought)\b", f"{title} {description}"):
                title = "Untitled Story"
                description = "A tale from the oral tradition."

            day = s.get("day", 1)
            if not isinstance(day, int):
                try:
                    day = int(day)
                except Exception:
                    day = 1
            day = max(1, min(day, 30))

            stories.append(StoryCategoryModel(
                title=title,
                description=description,
                imageUrl=s.get("imageUrl", ""),
                day=day,
                month=_sanitize_story_text(str(s.get("month", ""))),
                region=_sanitize_story_text(str(s.get("region", req.region or req.culture))),
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


# ─── Day Story Endpoints ─────────────────────────────────────────
# These two endpoints are intentionally separate so Flutter can call
# them in parallel: one for narrative text, one for illustration.
# Flutter provides the story identity; the backend generates only content.

# In-memory caches keyed by story id (cleared on restart / cold start)
_daystory_content_cache: dict[str, DayStoryContentResponse] = {}
_daystory_image_cache: dict[str, DayStoryImageResponse] = {}


@router.post("/stories/daystory", response_model=DayStoryContentResponse)
async def get_daystory_content(req: DayStoryRequest, request: Request):
    """
    Generate the full story narration for a given story.

    Flutter sends {id, title, summary, language}; this endpoint returns
    the complete narrative text.  The same text is used as context by
    the Live audio session so the Griot narrates exactly this story.

    Responses are cached by story id — subsequent calls are instant.
    """
    if req.id in _daystory_content_cache:
        return _daystory_content_cache[req.id]

    gemini_pool = request.app.state.gemini_pool

    prompt = (
        f"You are a master African oral storyteller (Griot).\n"
        f"Based on the title and summary below, write the complete story\n"
        f"narration that will be spoken aloud to a listener.\n\n"
        f"Title: {req.title}\n"
        f"Summary: {req.summary}\n"
        f"Language: {req.language}\n\n"
        f"Rules:\n"
        f"- Write in {req.language}\n"
        f"- Use the oral tradition style: warm, rhythmic, culturally authentic\n"
        f"- Begin with the traditional opening of the relevant culture\n"
        f"- Include the moral lesson naturally at the end\n"
        f"- Length: 400-700 words\n"
        f"- Output ONLY the story text. No title heading, no labels, "
        f"no reasoning, no meta-commentary whatsoever."
    )
    system = (
        "You are a master African Griot. "
        "Output only pure story narration text, nothing else."
    )

    try:
        parts: list[str] = []
        async for chunk in gemini_pool.generate_text_stream(
            prompt=prompt, system_instruction=system
        ):
            parts.append(chunk)

        content = _sanitize_story_text("".join(parts))
        if not content or content.startswith("[Generation error"):
            raise ValueError("Empty or error response from model")

        result = DayStoryContentResponse(id=req.id, content=content)
        _daystory_content_cache[req.id] = result

        logger.info(
            f"DayStory content generated: {req.id}",
            extra={"event": "daystory_content_generated", "story_id": req.id},
        )
        return result

    except Exception as e:
        logger.error(f"DayStory content generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Story content generation failed")


@router.post("/stories/daystory/image", response_model=DayStoryImageResponse)
async def get_daystory_image(req: DayStoryRequest, request: Request):
    """
    Generate the story illustration for a given story.

    Flutter sends {id, title, summary, language}; this endpoint returns
    the image URL (Cloud Storage) or base64 data URL as fallback.

    Responses are cached by story id — subsequent calls are instant.
    This endpoint is intentionally independent from /stories/daystory
    so Flutter can call both in parallel without one blocking the other.
    """
    if req.id in _daystory_image_cache:
        return _daystory_image_cache[req.id]

    from agents.visual_agent import VisualGenerationAgent

    firestore = request.app.state.firestore
    agent = VisualGenerationAgent(firestore)

    # Build a rich scene description from title + summary
    scene = f"{req.title}. {req.summary}"

    try:
        url = await agent.generate_image(
            scene_description=scene,
            culture="African",
            aspect_ratio="16:9",
        )

        if url and url.startswith("data:image"):
            result = DayStoryImageResponse(id=req.id, image_base64=url)
        elif url:
            result = DayStoryImageResponse(id=req.id, image_url=url)
        else:
            result = DayStoryImageResponse(id=req.id)

        _daystory_image_cache[req.id] = result

        logger.info(
            f"DayStory image generated: {req.id}",
            extra={"event": "daystory_image_generated", "story_id": req.id},
        )
        return result

    except Exception as e:
        logger.error(f"DayStory image generation failed: {e}", exc_info=True)
        # Return empty rather than 500 — image is optional, story still works
        return DayStoryImageResponse(id=req.id)


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
