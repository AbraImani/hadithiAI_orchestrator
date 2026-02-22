"""
Primary Orchestrator Agent
==========================
The brain of HadithiAI Live. Manages the conversation state machine,
detects intent via Gemini Live API function calling, dispatches to
sub-agents, merges responses, and controls streaming output.

Architecture:
  Client audio → Gemini Live API (with function declarations)
  Gemini Live → function_call events → Agent Dispatcher
  Agent Dispatcher → Sub-agents → Cultural Grounding → Response Merger
  Response Merger → Streaming Controller → Client
"""

import asyncio
import logging
import time
import uuid
from typing import Optional

from core.config import settings
from core.models import (
    AgentRequest,
    AgentResponse,
    IntentType,
    OrchestratorState,
    ServerMessage,
    ServerMessageType,
    ConversationTurn,
    A2ATask,
    A2ATaskState,
)
from orchestrator.agent_dispatcher import AgentDispatcher
from orchestrator.a2a_router import create_a2a_task, dispatch_with_schema_enforcement
from orchestrator.streaming_controller import StreamingController
from services.firestore_client import FirestoreClient
from services.gemini_client import GeminiClientPool, GeminiLiveSession
from services.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


# ─── System Instruction for Gemini Live Session ──────────────────────

SYSTEM_INSTRUCTION = """You are HadithiAI, the world's first African Immersive Oral AI Agent.

IDENTITY:
- You are a master storyteller (Griot) in the African oral tradition
- You speak with warmth, rhythm, and cultural authenticity
- You naturally use call-and-response patterns
- You weave proverbs and wisdom into conversation
- You adapt your language and tone to the listener

BEHAVIOR:
- Begin conversations with a culturally appropriate greeting
- Always ground stories in specific African cultures (name them)
- Use traditional story openings from the relevant culture
- Include moral lessons naturally, never forced
- Encourage listener participation (questions, responses)
- If interrupted, gracefully incorporate the interruption

TOOLS:
When the user's request matches one of these categories, call the corresponding function:
- tell_story: When the user wants to hear a story or tale
- pose_riddle: When the user wants a riddle, puzzle, or game
- generate_scene_image: When the user wants to see or visualize a scene
- get_cultural_context: When you need specific cultural details or facts

CONSTRAINTS:
- Never fabricate cultural facts — use get_cultural_context if unsure
- Never mix cultures inappropriately
- Always credit the cultural origin of stories and riddles
- Keep responses conversational, not academic
- Maintain the oral tradition feel — this is spoken, not written

LANGUAGE:
- Default to English with cultural phrases mixed in
- If the user speaks Swahili, Yoruba, Zulu, or other African languages,
  respond in that language with English support
- Use phonetic pronunciation guides for non-English phrases"""


# ─── Function Declarations for Gemini Live ───────────────────────────

TOOL_DECLARATIONS = [
    {
        "name": "tell_story",
        "description": "Generate an African oral tradition story. Call this when the user wants to hear a story, tale, or narrative from African traditions.",
        "parameters": {
            "type": "object",
            "properties": {
                "culture": {
                    "type": "string",
                    "description": "The African culture/tradition to draw from (e.g., Yoruba, Zulu, Kikuyu, Ashanti, Maasai)"
                },
                "theme": {
                    "type": "string",
                    "description": "Story theme (e.g., trickster, creation, wisdom, courage, love, origin)"
                },
                "complexity": {
                    "type": "string",
                    "enum": ["child", "teen", "adult"],
                    "description": "Target audience complexity level"
                },
            },
            "required": ["culture", "theme"],
        },
    },
    {
        "name": "pose_riddle",
        "description": "Generate an interactive African riddle. Call this when the user wants a riddle, puzzle, or word game.",
        "parameters": {
            "type": "object",
            "properties": {
                "culture": {
                    "type": "string",
                    "description": "The African culture to draw the riddle from"
                },
                "difficulty": {
                    "type": "string",
                    "enum": ["easy", "medium", "hard"],
                    "description": "Difficulty level of the riddle"
                },
            },
            "required": ["culture"],
        },
    },
    {
        "name": "generate_scene_image",
        "description": "Create a visual illustration of the current story scene. Call this when the user wants to see or visualize something.",
        "parameters": {
            "type": "object",
            "properties": {
                "scene_description": {
                    "type": "string",
                    "description": "Detailed description of the scene to illustrate"
                },
                "culture": {
                    "type": "string",
                    "description": "Cultural context for art style"
                },
            },
            "required": ["scene_description"],
        },
    },
    {
        "name": "get_cultural_context",
        "description": "Retrieve cultural background information. Call this when you need specific facts about African traditions, customs, or history.",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "The cultural topic to look up"
                },
                "culture": {
                    "type": "string",
                    "description": "The specific African culture"
                },
            },
            "required": ["topic"],
        },
    },
]


class PrimaryOrchestrator:
    """
    Central orchestrator that manages the entire conversation lifecycle.
    
    Flow:
    1. Receives audio/text from client via WebSocket handler
    2. Pipes to Gemini Live API session
    3. Gemini responds with text/audio OR function calls
    4. Function calls → dispatched to specialized sub-agents
    5. Sub-agent results → fed back to Gemini for speech synthesis
    6. Final audio/text streamed to client
    """

    def __init__(
        self,
        session_id: str,
        firestore: FirestoreClient,
        gemini_pool: GeminiClientPool,
        output_queue: asyncio.Queue,
    ):
        self.session_id = session_id
        self.firestore = firestore
        self.gemini_pool = gemini_pool
        self.output_queue = output_queue

        # State
        self.state = OrchestratorState.IDLE
        self.gemini_session: Optional[GeminiLiveSession] = None
        self.current_turn_id: Optional[str] = None
        self._active_tasks: list[asyncio.Task] = []

        # Sub-components
        self.memory = MemoryManager(session_id, firestore)
        self.dispatcher = AgentDispatcher(session_id, firestore, gemini_pool)
        self.stream_controller = StreamingController(output_queue, session_id)

        self._logger = logger.getChild(f"session.{session_id}")

    async def initialize(self):
        """Initialize the orchestrator and open Gemini Live session."""
        start = time.time()

        # Create session in Firestore
        await self.memory.create_session()

        # Acquire a Gemini Live session from the pool
        self.gemini_session = await self.gemini_pool.acquire(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=TOOL_DECLARATIONS,
        )

        # Start the Gemini response listener
        asyncio.create_task(
            self._gemini_response_listener(),
            name=f"gemini-listener-{self.session_id}",
        )

        self.state = OrchestratorState.IDLE
        elapsed = (time.time() - start) * 1000
        self._logger.info(
            f"Orchestrator initialized in {elapsed:.0f}ms",
            extra={"event": "orchestrator_init", "latency_ms": elapsed},
        )

    async def handle_audio_chunk(self, audio_b64: str, seq: int):
        """Handle incoming audio chunk from client."""
        if self.state in (OrchestratorState.IDLE, OrchestratorState.LISTENING):
            self.state = OrchestratorState.LISTENING
            if not self.current_turn_id:
                self.current_turn_id = f"turn_{uuid.uuid4().hex[:8]}"

        # Forward audio directly to Gemini Live session
        if self.gemini_session:
            await self.gemini_session.send_audio(audio_b64)

    async def handle_video_frame(self, frame_b64: str, width: int, height: int, seq: int):
        """
        Handle incoming video frame from client.

        Video frames are forwarded to the Gemini Live session so the model
        can see what the user is showing (book pages, objects, gestures).
        """
        if self.gemini_session:
            await self.gemini_session.send_video_frame(frame_b64, width, height)

    async def handle_text_input(self, text: str, seq: int):
        """Handle text input from client."""
        self.current_turn_id = f"turn_{uuid.uuid4().hex[:8]}"
        self.state = OrchestratorState.PROCESSING

        self._logger.info(
            f"Text input: {text[:100]}",
            extra={
                "event": "text_input",
                "turn_id": self.current_turn_id,
            },
        )

        # Save user turn
        await self.memory.save_turn(
            ConversationTurn(
                turn_id=self.current_turn_id,
                role="user",
                content=text,
            )
        )

        # Send text to Gemini Live session
        if self.gemini_session:
            await self.gemini_session.send_text(text)

    async def handle_interrupt(self):
        """Handle user interruption."""
        self._logger.info(
            "User interrupted",
            extra={"event": "interrupt", "turn_id": self.current_turn_id},
        )

        prev_state = self.state
        self.state = OrchestratorState.INTERRUPTED

        # Cancel active sub-agent tasks
        for task in self._active_tasks:
            if not task.done():
                task.cancel()
        self._active_tasks.clear()

        # Tell Gemini Live to stop current generation
        if self.gemini_session:
            await self.gemini_session.send_interrupt()

        # Drain output queue
        while not self.output_queue.empty():
            try:
                self.output_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        self.state = OrchestratorState.LISTENING
        self.current_turn_id = f"turn_{uuid.uuid4().hex[:8]}"

    async def handle_control(self, action: str, value):
        """Handle control messages (language change, preferences, etc.)."""
        self._logger.info(
            f"Control: {action}={value}",
            extra={"event": "control"},
        )

        match action:
            case "set_language":
                await self.memory.update_preferences({"language_pref": value})
            case "set_age_group":
                await self.memory.update_preferences({"age_group": value})
            case "set_region":
                await self.memory.update_preferences({"region_pref": value})

    async def restore_session(self, session_id: str):
        """Restore a previous session for continuity."""
        restored = await self.memory.load_session(session_id)
        if restored:
            self._logger.info(
                f"Session restored: {session_id}",
                extra={"event": "session_restore"},
            )

    async def _gemini_response_listener(self):
        """
        Listen for responses from the Gemini Live API session.
        
        Gemini sends:
        - text chunks → stream to client
        - audio chunks → stream to client
        - function_call → dispatch to sub-agent
        - interrupted → handle interruption
        - turn_complete → end of turn
        """
        try:
            async for event in self.gemini_session.receive_events():
                match event["type"]:
                    case "text":
                        # Stream text chunk to client
                        self.state = OrchestratorState.STREAMING
                        await self.stream_controller.send_text_chunk(
                            event["data"], agent="orchestrator"
                        )

                    case "audio":
                        # Stream audio chunk to client
                        self.state = OrchestratorState.STREAMING
                        await self.stream_controller.send_audio_chunk(
                            event["data"]
                        )

                    case "function_call":
                        # Gemini wants to invoke a sub-agent
                        self.state = OrchestratorState.PROCESSING
                        task = asyncio.create_task(
                            self._handle_function_call(event)
                        )
                        self._active_tasks.append(task)

                    case "interrupted":
                        # Gemini detected user started speaking
                        await self.handle_interrupt()
                        await self.output_queue.put(
                            ServerMessage(
                                type=ServerMessageType.INTERRUPTED,
                                session_id=self.session_id,
                            )
                        )

                    case "turn_complete":
                        # Gemini finished its turn
                        self.state = OrchestratorState.IDLE
                        await self.stream_controller.send_turn_end()

                        # Save agent turn
                        if self.current_turn_id:
                            self.current_turn_id = None

                    case "error":
                        self._logger.error(
                            f"Gemini error: {event.get('message')}",
                            extra={"event": "gemini_error"},
                        )
                        await self.stream_controller.send_error(
                            event.get("message", "AI processing error")
                        )
                        self.state = OrchestratorState.IDLE

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._logger.error(
                f"Gemini listener crashed: {e}",
                extra={"event": "gemini_listener_crash"},
                exc_info=True,
            )

    async def _handle_function_call(self, event: dict):
        """
        Handle a function call from Gemini Live.

        Flow (A2A):
        1. Parse the function call
        2. Create an A2A Task with schema-validated input
        3. Dispatch to the appropriate sub-agent via A2A router
        4. Collect schema-validated output
        5. Send result back to Gemini for speech synthesis
        """
        func_name = event.get("name", "")
        func_args = event.get("args", {})
        func_id = event.get("id", "")

        self._logger.info(
            f"Function call: {func_name}({func_args})",
            extra={
                "event": "function_call",
                "agent": func_name,
                "turn_id": self.current_turn_id,
            },
        )

        start = time.time()

        # Map function call to intent and A2A schema
        intent_map = {
            "tell_story": IntentType.REQUEST_STORY,
            "pose_riddle": IntentType.REQUEST_RIDDLE,
            "generate_scene_image": IntentType.REQUEST_IMAGE,
            "get_cultural_context": IntentType.ASK_CULTURAL,
        }
        schema_map = {
            "tell_story": ("story_agent", "StoryRequest"),
            "pose_riddle": ("riddle_agent", "RiddleRequest"),
            "generate_scene_image": ("visual_agent", "ImageRequest"),
            "get_cultural_context": ("cultural_grounding", None),
        }
        intent = intent_map.get(func_name, IntentType.UNKNOWN)
        agent_name, input_schema = schema_map.get(func_name, (None, None))

        # Build agent request (legacy path, still needed by dispatcher)
        context_summary = await self.memory.get_context_summary()
        request = AgentRequest(
            intent=intent,
            user_input=str(func_args),
            culture=func_args.get("culture"),
            theme=func_args.get("theme"),
            age_group=func_args.get("complexity", "adult"),
            session_context=context_summary,
            turn_id=self.current_turn_id or "",
            session_id=self.session_id,
        )

        # Try A2A schema-enforced dispatch for story/riddle/image
        if agent_name and input_schema:
            try:
                a2a_input = dict(func_args)
                if context_summary:
                    a2a_input["session_context"] = context_summary

                task = create_a2a_task(
                    task_type=input_schema,
                    payload=a2a_input,
                    source_agent="orchestrator",
                    target_agent=agent_name,
                )

                # Dispatch to the sub-agent via the legacy dispatcher
                # (which now uses schema validation internally)
                full_result = []
                async for chunk in self.dispatcher.dispatch(request):
                    full_result.append(chunk.content)
                    if chunk.visual_moment:
                        asyncio.create_task(
                            self._trigger_image_generation(
                                chunk.visual_moment, request.culture
                            )
                        )

                result_text = "".join(full_result)
                task.state = A2ATaskState.COMPLETED
                self._logger.info(
                    f"A2A task {task.task_id} completed for {agent_name}",
                    extra={"event": "a2a_task_complete", "agent": agent_name},
                )

            except Exception as e:
                self._logger.warning(
                    f"A2A dispatch failed, falling back to legacy: {e}",
                    extra={"event": "a2a_fallback"},
                )
                full_result = []
                async for chunk in self.dispatcher.dispatch(request):
                    full_result.append(chunk.content)
                result_text = "".join(full_result)
        else:
            # Legacy dispatch for cultural context and unknown intents
            full_result = []
            async for chunk in self.dispatcher.dispatch(request):
                full_result.append(chunk.content)
            result_text = "".join(full_result)

        # Send the complete result back to Gemini as function response
        if self.gemini_session:
            await self.gemini_session.send_function_response(
                func_id, func_name, result_text
            )

        elapsed = (time.time() - start) * 1000
        self._logger.info(
            f"Function call {func_name} completed in {elapsed:.0f}ms",
            extra={
                "event": "function_call_complete",
                "agent": func_name,
                "latency_ms": elapsed,
            },
        )

    async def _trigger_image_generation(
        self, scene_description: str, culture: Optional[str]
    ):
        """Trigger async image generation — never blocks the conversation."""
        try:
            image_url = await self.dispatcher.generate_image(
                scene_description, culture
            )
            if image_url:
                await self.stream_controller.send_image_ready(image_url)
        except Exception as e:
            self._logger.warning(
                f"Image generation failed: {e}",
                extra={"event": "image_gen_failed"},
            )
            # Non-critical — just skip the image

    async def shutdown(self):
        """Clean shutdown of orchestrator and all sub-components."""
        self._logger.info("Orchestrator shutting down")

        # Cancel active tasks
        for task in self._active_tasks:
            if not task.done():
                task.cancel()

        # Release Gemini session back to pool
        if self.gemini_session:
            await self.gemini_pool.release(self.gemini_session)

        # Final session save
        await self.memory.finalize_session()
