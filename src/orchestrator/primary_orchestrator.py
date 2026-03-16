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
from services.vad import VoiceActivityDetector

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

    Lifecycle:
      initialize() → [handle_audio/text/video/interrupt/control] → shutdown()
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
        self._listener_task: Optional[asyncio.Task] = None
        self._interrupted = False  # suppress stale events after interrupt
        self._interrupt_at: float = 0.0
        self._pending_func_call: Optional[tuple] = None  # (func_id, func_name)

        # Sub-components
        self.memory = MemoryManager(session_id, firestore)
        self.dispatcher = AgentDispatcher(session_id, firestore, gemini_pool)
        self.stream_controller = StreamingController(output_queue, session_id)
        self.vad = VoiceActivityDetector(
            sample_rate=settings.AUDIO_SAMPLE_RATE_INPUT,
            energy_threshold=settings.VAD_ENERGY_THRESHOLD,
            energy_threshold_low=settings.VAD_ENERGY_THRESHOLD_LOW,
            zcr_max=settings.VAD_ZCR_MAX,
            speech_frames_trigger=settings.VAD_SPEECH_FRAMES_TRIGGER,
            silence_frames_trigger=settings.VAD_SILENCE_FRAMES_TRIGGER,
        )

        self._logger = logger.getChild(f"session.{session_id}")

    async def initialize(self, story_context: Optional[dict] = None):
        """Initialize the orchestrator and open Gemini Live session.

        Args:
            story_context: Optional dict with keys id, title, summary,
                           region, language, and content.
                           When provided the Live session system instruction is
                           extended so the Griot knows which story to narrate.
        """
        start = time.time()

        # Preserve metadata created by REST /sessions (story context,
        # region/language preferences). Only create if missing.
        existing = await self.firestore.get_session(self.session_id)
        if existing:
            await self.memory.load_session(self.session_id)
        else:
            await self.memory.create_session()

        # Build system instruction — inject story context when provided
        system_instruction = SYSTEM_INSTRUCTION
        if story_context and (story_context.get("title") or story_context.get("summary")):
            title = story_context.get("title", "")
            summary = story_context.get("summary", "")
            region = story_context.get("region", "")
            language = story_context.get("language", "")
            story_content = story_context.get("content", "")
            story_block = (
                f"\n\nACTIVE STORY CONTEXT:\n"
                f"The user has opened the story titled \"{title}\".\n"
            )
            if summary:
                story_block += f"Story summary: {summary}\n"
            if region:
                story_block += f"Story region: {region}\n"
            if language:
                story_block += f"Narrate in language: {language}\n"
            if story_content:
                story_block += (
                    "Authoritative story text (use this exact narrative as the "
                    "source when narrating this day story):\n"
                    f"{story_content}\n"
                )
            story_block += (
                "When the user asks you to start, continue, or read the story, "
                "narrate exactly this story using your Griot voice. "
                "Do not invent a different story. The user may also ask "
                "questions, clarifications, or discussion points about this "
                "same story; answer conversationally while staying grounded "
                "in this story context."
            )
            system_instruction = SYSTEM_INSTRUCTION + story_block
            self._logger.info(
                f"Story context injected: '{title}'",
                extra={"event": "story_context_injected"},
            )

        # Acquire a Gemini Live session from the pool
        self.gemini_session = await self.gemini_pool.acquire(
            system_instruction=system_instruction,
            tools=TOOL_DECLARATIONS,
        )

        # Start the Gemini response listener (tracked!)
        self._listener_task = asyncio.create_task(
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
        """Handle incoming audio chunk from client.

        Audio is gated by the Voice Activity Detector: only chunks
        containing human speech are forwarded to Gemini Live.  This
        prevents ambient noise (fans, traffic, typing) from
        triggering Gemini's built-in interruption detection.
        """
        # ── VAD gate: drop non-speech audio ──
        is_speech = self.vad.process_audio(audio_b64)
        if not is_speech:
            return  # Ambient noise — do not forward

        # If an interrupt happened recently and Gemini never sent a
        # turn_complete, force-clear suppression after a short window
        # so audio-only follow-up turns do not get stuck.
        if self._interrupted:
            elapsed = time.time() - self._interrupt_at
            if elapsed >= settings.INTERRUPT_SUPPRESSION_MAX_SECONDS:
                self._logger.warning(
                    "Force-clearing interrupt suppression for new audio turn",
                    extra={"event": "interrupt_force_clear", "elapsed": elapsed},
                )
                self._interrupted = False
                if self.gemini_session:
                    self.gemini_session.drain_event_queue()

        if self.state in (OrchestratorState.IDLE, OrchestratorState.LISTENING):
            self.state = OrchestratorState.LISTENING
            if not self.current_turn_id:
                self.current_turn_id = f"turn_{uuid.uuid4().hex[:8]}"

        # Forward speech audio to Gemini Live session
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

        # Clear interrupt flag — we're starting a new turn and want
        # to receive the response.  If there was a stale turn still
        # in-flight, clearing now is safe because we're about to
        # send new input anyway.
        self._interrupted = False

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
        """Handle user interruption — drain queues, cancel active work.

        Sets _interrupted=True so the listener discards all remaining
        events from the old turn (audio/text/turn_complete) until Gemini
        finishes.  This prevents a stale turn_complete from being sent
        to the client as if it were the response to the next input.
        """
        self._logger.info(
            "User interrupted",
            extra={"event": "interrupt", "turn_id": self.current_turn_id},
        )

        prev_state = self.state
        self.state = OrchestratorState.INTERRUPTED

        # Tell the listener to drop events until the old turn completes
        self._interrupted = True
        self._interrupt_at = time.time()

        # Cancel active sub-agent tasks
        for task in self._active_tasks:
            if not task.done():
                task.cancel()
        self._active_tasks.clear()

        # Drain the Gemini event queue (discard already-buffered events)
        if self.gemini_session:
            self.gemini_session.drain_event_queue()

        # If a function call was in-flight, send a dummy response
        # so Gemini isn't stuck waiting for tool_response forever.
        if self._pending_func_call and self.gemini_session:
            fid, fname = self._pending_func_call
            self._pending_func_call = None
            self._logger.debug(
                f"Sending dummy tool response for interrupted {fname}"
            )
            try:
                await self.gemini_session.send_function_response(
                    fid, fname, "[interrupted by user]"
                )
            except Exception:
                pass  # best-effort

        # Drain the output queue (discard unsent messages to client)
        while not self.output_queue.empty():
            try:
                self.output_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Reset streaming metrics without sending turn_end
        self.stream_controller.reset_metrics()

        # Reset VAD state for clean new turn
        self.vad.reset()

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
                # ── Drop stale events after interrupt ──
                if self._interrupted:
                    if event["type"] == "turn_complete":
                        # The interrupted turn finished — Gemini is
                        # now ready for new input.  Clear the flag
                        # WITHOUT sending turn_end to the client.
                        self._interrupted = False
                        self._logger.debug(
                            "Suppressed stale turn_complete from interrupted turn"
                        )
                    else:
                        # Discard leftover audio/text from the old turn
                        pass
                    continue

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
                        # Clean up completed tasks
                        self._active_tasks = [
                            t for t in self._active_tasks if not t.done()
                        ]
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
                        # Fatal errors (connection closed) — stop listening
                        if event.get("fatal"):
                            self._logger.warning("Fatal Gemini error, stopping listener")
                            return

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

        # Track so handle_interrupt() can send dummy response
        self._pending_func_call = (func_id, func_name)

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

        # Send the complete result back to Gemini as function response.
        # Clean the text to ensure only narration reaches Gemini for
        # speech synthesis — no JSON, markers, or structural content.
        self._pending_func_call = None
        result_text = self._clean_tool_response(result_text, func_name)
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

        # Cancel the Gemini listener task
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except (asyncio.CancelledError, Exception):
                pass
            self._listener_task = None

        # Cancel active sub-agent tasks
        for task in self._active_tasks:
            if not task.done():
                task.cancel()
        self._active_tasks.clear()

        # Release Gemini session back to pool
        if self.gemini_session:
            await self.gemini_pool.release(self.gemini_session)
            self.gemini_session = None

        # Final session save
        await self.memory.finalize_session()

    @staticmethod
    def _clean_tool_response(text: str, func_name: str) -> str:
        """Clean agent result text before sending to Gemini as tool_response.

        Strips JSON, structural markers, and meta-commentary so Gemini
        only receives pure narration text to synthesize into speech.
        """
        import re

        if not text or not text.strip():
            return "The story continues..."

        # If the text looks like JSON, try to extract the narrative
        cleaned = text.strip()
        if cleaned.startswith("{") or cleaned.startswith("["):
            try:
                import json
                parsed = json.loads(cleaned)
                if isinstance(parsed, dict) and "text" in parsed:
                    cleaned = parsed["text"]
                elif isinstance(parsed, list):
                    parts = [
                        item.get("text", "") for item in parsed
                        if isinstance(item, dict) and "text" in item
                    ]
                    if parts:
                        cleaned = " ".join(parts)
            except (json.JSONDecodeError, TypeError):
                pass

        # Strip bracket markers
        cleaned = re.sub(r'\[VISUAL:[^\]]*\]', '', cleaned)
        cleaned = re.sub(r'\[[A-Z_]{3,}[^\]]*\]', '', cleaned)
        # Strip XML-like thought traces
        cleaned = re.sub(r'<think>.*?</think>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r'<thought>.*?</thought>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
        # Strip explicit reasoning/meta lines
        cleaned = re.sub(
            r'(?im)^\s*(thought|reasoning|analysis|chain\s*of\s*thought|internal\s*monologue)\s*:\s*.*$',
            '',
            cleaned,
        )
        cleaned = re.sub(r"(?im)^\s*let'?s\s+think\b.*$", '', cleaned)
        cleaned = re.sub(r'(?im)^\s*i\s+should\b.*$', '', cleaned)
        # Strip markdown code blocks
        cleaned = re.sub(r'```\w*\n?', '', cleaned)
        # Strip JSON-like key-value fragments
        cleaned = re.sub(r'"cultural_claims"\s*:\s*\[.*?\]', '', cleaned, flags=re.DOTALL)
        cleaned = re.sub(r'"(culture|scene_description|is_final|category|claim)"\s*:\s*"[^"]*"', '', cleaned)
        # Collapse whitespace
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        cleaned = re.sub(r'  +', ' ', cleaned)

        return cleaned.strip() if cleaned.strip() else "The story continues..."
