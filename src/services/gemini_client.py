"""
Gemini Client & Session Pool
=============================
Manages connections to Gemini 2.0 Flash Live API and text generation.
Implements connection pooling for warm sessions and streaming helpers.

Two modes of interaction:
1. Gemini Live (WebSocket) — for the Orchestrator's bidirectional audio/text
2. Gemini Text (REST) — for sub-agent text generation (streaming)
"""

import asyncio
import logging
import json
from typing import AsyncIterator, Optional

from core.config import settings

logger = logging.getLogger(__name__)


class GeminiLiveSession:
    """
    Represents a single persistent Gemini Live API WebSocket session.
    
    The Gemini Live API (Multimodal Live API) provides bidirectional
    streaming over WebSocket:
    - Send: audio chunks (PCM 16kHz), text, function responses
    - Receive: audio chunks (PCM 24kHz), text, function calls, events
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._ws = None
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._listener_task: Optional[asyncio.Task] = None
        self._is_connected = False
        self._logger = logger.getChild(f"gemini_live.{session_id[:8]}")

    async def connect(
        self,
        system_instruction: str,
        tools: list[dict],
        project_id: str,
        region: str,
        model: str,
    ):
        """
        Open a Gemini Live API WebSocket session.
        
        Uses the google-genai SDK for Gemini Live API.
        """
        try:
            from google import genai
            from google.genai import types

            # Support both API key and Vertex AI authentication
            api_key = settings.GEMINI_API_KEY
            if api_key:
                client = genai.Client(api_key=api_key)
                self._logger.info("Using API key authentication")
            else:
                client = genai.Client(
                    vertexai=True,
                    project=project_id,
                    location=region,
                )
                self._logger.info("Using Vertex AI (ADC) authentication")

            # Configure Live API session
            config = types.LiveConnectConfig(
                response_modalities=["AUDIO", "TEXT"],
                system_instruction=types.Content(
                    parts=[types.Part(text=system_instruction)]
                ),
                tools=[types.Tool(function_declarations=[
                    types.FunctionDeclaration(**tool) for tool in tools
                ])],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name="Aoede"  # Warm, storytelling voice
                        )
                    )
                ),
            )

            # Connect to Live API (SDK handles model path internally)
            self._session = client.aio.live.connect(
                model=model,
                config=config,
            )
            self._live = await self._session.__aenter__()
            self._is_connected = True

            # Start background listener
            self._listener_task = asyncio.create_task(self._listen())

            self._logger.info("Gemini Live session connected")

        except Exception as e:
            self._logger.error(f"Failed to connect Gemini Live: {e}", exc_info=True)
            raise

    async def _listen(self):
        """
        Background listener for Gemini Live API events.

        Parses LiveServerMessage responses and normalises them into
        simple dicts on the event queue.  Handles both the raw
        server_content/tool_call structure and convenience properties
        (.text, .data) that newer SDK versions expose.
        """
        import base64
        try:
            while self._is_connected:
                try:
                    async for response in self._live.receive():
                        await self._process_live_response(response, base64)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    if self._is_connected:
                        self._logger.error(
                            f"Gemini Live receive error: {e}", exc_info=True
                        )
                        await self._event_queue.put({
                            "type": "error",
                            "message": str(e),
                        })
                        # Brief pause before retrying the receive loop
                        await asyncio.sleep(0.5)
                    else:
                        break
        except asyncio.CancelledError:
            pass

    async def _process_live_response(self, response, base64):
        """
        Parse a single LiveServerMessage into internal events.

        Priority order:
        1. server_content.model_turn.parts  (text and audio parts)
        2. server_content.turn_complete / interrupted  (turn signals)
        3. tool_call.function_calls  (function invocations)
        4. Convenience .text / .data  (fallback for newer SDK)
        """
        handled_text = False
        handled_audio = False

        # -- 1. Raw server_content (works on all SDK versions) --
        sc = getattr(response, 'server_content', None)
        if sc is not None:
            model_turn = getattr(sc, 'model_turn', None)
            if model_turn:
                for part in (getattr(model_turn, 'parts', None) or []):
                    # Text part
                    text_val = getattr(part, 'text', None)
                    if text_val:
                        handled_text = True
                        await self._event_queue.put({
                            "type": "text",
                            "data": text_val,
                        })
                    # Inline audio data
                    inline = getattr(part, 'inline_data', None)
                    if inline:
                        raw = getattr(inline, 'data', None)
                        if raw:
                            handled_audio = True
                            audio_b64 = base64.b64encode(raw).decode()
                            await self._event_queue.put({
                                "type": "audio",
                                "data": audio_b64,
                            })

            # Turn-level signals
            if getattr(sc, 'turn_complete', False):
                await self._event_queue.put({"type": "turn_complete"})
            if getattr(sc, 'interrupted', False):
                await self._event_queue.put({"type": "interrupted"})

        # -- 2. Convenience properties (newer SDK only) --
        if not handled_text:
            text_val = getattr(response, 'text', None)
            if text_val:
                await self._event_queue.put({
                    "type": "text",
                    "data": text_val,
                })
        if not handled_audio:
            data_val = getattr(response, 'data', None)
            if data_val and isinstance(data_val, (bytes, bytearray)):
                audio_b64 = base64.b64encode(data_val).decode()
                await self._event_queue.put({
                    "type": "audio",
                    "data": audio_b64,
                })

        # -- 3. Function (tool) calls --
        tc = getattr(response, 'tool_call', None)
        if tc:
            for fc in (getattr(tc, 'function_calls', None) or []):
                await self._event_queue.put({
                    "type": "function_call",
                    "id": getattr(fc, 'id', ''),
                    "name": getattr(fc, 'name', ''),
                    "args": dict(fc.args) if getattr(fc, 'args', None) else {},
                })

    async def receive_events(self) -> AsyncIterator[dict]:
        """Yield events from the Gemini Live session."""
        while self._is_connected:
            try:
                event = await asyncio.wait_for(
                    self._event_queue.get(), timeout=60.0
                )
                yield event
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    async def send_audio(self, audio_b64: str):
        """Send an audio chunk to Gemini Live."""
        if not self._is_connected:
            return
        try:
            import base64
            from google.genai import types

            audio_bytes = base64.b64decode(audio_b64)
            await self._live.send(
                input=types.LiveClientRealtimeInput(
                    media_chunks=[types.Blob(
                        data=audio_bytes,
                        mime_type="audio/pcm;rate=16000",
                    )]
                )
            )
        except Exception as e:
            self._logger.error(f"Failed to send audio: {e}")

    async def send_text(self, text: str):
        """Send text input to Gemini Live."""
        if not self._is_connected:
            return
        try:
            await self._live.send(input=text, end_of_turn=True)
        except Exception as e:
            self._logger.error(f"Failed to send text: {e}")

    async def send_video_frame(self, frame_b64: str, width: int = 640, height: int = 480):
        """
        Send a video frame to Gemini Live for vision understanding.

        The frame is base64-encoded JPEG or PNG. The Gemini Live API
        accepts inline image data as part of realtime input so the model
        can see what the user is showing (book pages, cultural objects).
        """
        if not self._is_connected:
            return
        try:
            import base64
            from google.genai import types

            frame_bytes = base64.b64decode(frame_b64)
            await self._live.send(
                input=types.LiveClientRealtimeInput(
                    media_chunks=[types.Blob(
                        data=frame_bytes,
                        mime_type="image/jpeg",
                    )]
                )
            )
        except Exception as e:
            self._logger.error(f"Failed to send video frame: {e}")

    async def send_function_response(self, func_id: str, func_name: str, result: str):
        """Send a function call response back to Gemini Live."""
        if not self._is_connected:
            return
        try:
            from google.genai import types

            await self._live.send(
                input=types.LiveClientToolResponse(
                    function_responses=[types.FunctionResponse(
                        id=func_id,
                        name=func_name,
                        response={"result": result},
                    )]
                )
            )
        except Exception as e:
            self._logger.error(f"Failed to send function response: {e}")

    async def send_interrupt(self):
        """Signal interruption to Gemini Live."""
        # The Gemini Live API handles interruption automatically
        # when new audio input arrives during generation.
        # This is a placeholder for explicit interrupt if needed.
        pass

    async def close(self):
        """Close the Gemini Live session."""
        self._is_connected = False
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
        self._logger.info("Gemini Live session closed")


class GeminiClientPool:
    """
    Pool of Gemini client resources.
    
    Manages:
    - Gemini Live sessions (WebSocket, long-lived)
    - Gemini text generation (for sub-agents)
    """

    def __init__(self, project_id: str, region: str, pool_size: int = 3):
        self.project_id = project_id
        self.region = region
        self.pool_size = pool_size
        self._text_client = None
        self._logger = logger.getChild("gemini_pool")

    async def warm_up(self):
        """Pre-initialize clients for faster first request."""
        try:
            from google import genai

            api_key = settings.GEMINI_API_KEY
            if api_key:
                self._text_client = genai.Client(api_key=api_key)
                self._logger.info("Gemini text client initialized (API key)")
            else:
                self._text_client = genai.Client(
                    vertexai=True,
                    project=self.project_id,
                    location=self.region,
                )
                self._logger.info("Gemini text client initialized (Vertex AI)")
        except Exception as e:
            self._logger.warning(f"Gemini warm-up failed (will retry on demand): {e}")

    async def acquire(
        self,
        system_instruction: str,
        tools: list[dict],
    ) -> GeminiLiveSession:
        """
        Create a new Gemini Live session.
        
        Each WebSocket connection gets its own Live session
        because Live sessions are stateful (conversation context).
        """
        import uuid
        session = GeminiLiveSession(str(uuid.uuid4())[:8])
        await session.connect(
            system_instruction=system_instruction,
            tools=tools,
            project_id=self.project_id,
            region=self.region,
            model=settings.GEMINI_MODEL,
        )
        return session

    async def release(self, session: GeminiLiveSession):
        """Release (close) a Gemini Live session."""
        await session.close()

    async def generate_text_stream(
        self,
        prompt: str,
        system_instruction: str,
    ) -> AsyncIterator[str]:
        """
        Stream text generation from Gemini 2.0 Flash (text mode).
        
        Used by sub-agents for non-realtime text generation.
        This is faster than Live API for pure text tasks.
        """
        try:
            from google import genai
            from google.genai import types

            if not self._text_client:
                api_key = settings.GEMINI_API_KEY
                if api_key:
                    self._text_client = genai.Client(api_key=api_key)
                else:
                    self._text_client = genai.Client(
                        vertexai=True,
                        project=self.project_id,
                        location=self.region,
                    )

            response = self._text_client.aio.models.generate_content_stream(
                model=settings.GEMINI_TEXT_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.8,     # Creative but grounded
                    top_p=0.95,
                    max_output_tokens=2048,
                ),
            )

            async for chunk in response:
                if chunk.text:
                    yield chunk.text

        except Exception as e:
            self._logger.error(f"Text generation failed: {e}", exc_info=True)
            yield "[Generation error — please try again]"

    async def close_all(self):
        """Close all clients."""
        self._text_client = None
        self._logger.info("All Gemini clients closed")
