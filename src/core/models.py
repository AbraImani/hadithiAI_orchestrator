"""
Message Models
==============
Pydantic models for all WebSocket message types,
internal events, and agent communication.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Optional, Any
from pydantic import BaseModel, Field


# ─── WebSocket Message Types ─────────────────────────────────────────

class ClientMessageType(str, Enum):
    """Messages from client to server."""
    AUDIO_CHUNK = "audio_chunk"
    TEXT_INPUT = "text_input"
    VIDEO_FRAME = "video_frame"
    INTERRUPT = "interrupt"
    CONTROL = "control"
    SESSION_INIT = "session_init"
    PING = "ping"


class ServerMessageType(str, Enum):
    """Messages from server to client."""
    AUDIO_CHUNK = "audio_chunk"
    TEXT_CHUNK = "text_chunk"
    IMAGE_READY = "image_ready"
    AGENT_STATE = "agent_state"
    TURN_END = "turn_end"
    INTERRUPTED = "interrupted"
    ERROR = "error"
    SESSION_CREATED = "session_created"
    PONG = "pong"


class ClientMessage(BaseModel):
    """Incoming WebSocket message from client."""
    type: ClientMessageType
    data: Optional[str] = None          # base64 audio/video or text content
    seq: int = 0                         # sequence number
    action: Optional[str] = None         # for control messages
    value: Optional[Any] = None          # for control messages
    session_id: Optional[str] = None     # for session resumption
    width: Optional[int] = None          # for video_frame
    height: Optional[int] = None         # for video_frame


class ServerMessage(BaseModel):
    """Outgoing WebSocket message to client."""
    type: ServerMessageType
    data: Optional[str] = None           # base64 audio or text content
    seq: int = 0
    url: Optional[str] = None            # for image_ready
    agent: Optional[str] = None          # which agent generated this
    state: Optional[str] = None          # agent state
    error: Optional[str] = None          # error message
    session_id: Optional[str] = None     # for session_created
    timestamp: float = Field(default_factory=time.time)


# ─── Internal Event Types ────────────────────────────────────────────

class OrchestratorState(str, Enum):
    """State machine states for the orchestrator."""
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    STREAMING = "streaming"
    INTERRUPTED = "interrupted"
    ERROR = "error"


class IntentType(str, Enum):
    """Detected user intents."""
    REQUEST_STORY = "request_story"
    REQUEST_RIDDLE = "request_riddle"
    ANSWER_RIDDLE = "answer_riddle"
    REQUEST_IMAGE = "request_image"
    ASK_CULTURAL = "ask_cultural"
    CONTINUE = "continue"
    GREETING = "greeting"
    FAREWELL = "farewell"
    CLARIFICATION = "clarification"
    UNKNOWN = "unknown"


class AgentRequest(BaseModel):
    """Request from Orchestrator to a sub-agent."""
    intent: IntentType
    user_input: str
    culture: Optional[str] = None
    theme: Optional[str] = None
    age_group: str = "adult"
    session_context: Optional[str] = None
    preferences: dict = Field(default_factory=dict)
    turn_id: str = ""
    session_id: str = ""


class AgentResponse(BaseModel):
    """Response chunk from a sub-agent."""
    agent_name: str
    content: str
    is_final: bool = False
    metadata: dict = Field(default_factory=dict)
    cultural_confidence: float = 1.0
    visual_moment: Optional[str] = None   # scene description for image gen


# ─── Session Models ──────────────────────────────────────────────────

class SessionMetadata(BaseModel):
    """Session metadata stored in Firestore."""
    session_id: str
    created_at: float = Field(default_factory=time.time)
    last_active: float = Field(default_factory=time.time)
    language_pref: str = "en"
    region_pref: Optional[str] = None
    age_group: str = "adult"
    turn_count: int = 0


class ConversationTurn(BaseModel):
    """A single turn in the conversation."""
    turn_id: str
    role: str               # "user" or "agent"
    content: str
    agent: Optional[str] = None
    timestamp: float = Field(default_factory=time.time)
    intent: Optional[str] = None
    cultural_confidence: Optional[float] = None


# -- A2A Task Models --------------------------------------------------

class A2ATaskState(str, Enum):
    """States for an A2A task."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class A2ATask(BaseModel):
    """
    Represents an Agent-to-Agent communication task.
    Every inter-agent message is wrapped in a Task with a typed schema.
    """
    task_id: str
    task_type: str              # Schema name (e.g., "StoryRequest")
    payload: dict               # Must validate against task_type schema
    source_agent: str           # Agent that created the task
    target_agent: str           # Agent that should handle the task
    state: A2ATaskState = A2ATaskState.PENDING
    created_at: float = Field(default_factory=time.time)
    result: Optional[dict] = None
    error: Optional[str] = None
