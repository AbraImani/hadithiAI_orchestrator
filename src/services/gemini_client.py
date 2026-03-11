"""
Gemini Client & Session Pool
=============================
Manages connections to the Gemini Live API (bidiGenerateContent)
and Gemini text generation API for sub-agent calls.

Architecture:
  - GeminiLiveSession: Single persistent WebSocket session to the
    Gemini Multimodal Live API. Handles bidirectional audio/text/video
    streaming with function calling.
  - GeminiClientPool: Manages Live sessions + text generation client.
    Each WebSocket connection gets its own Live session (stateful).

Key integration patterns (per Google AI Studio reference):
  - Uses api_version="v1beta" via http_options
  - Configures context_window_compression for long conversations
  - Sets speech_config with voice selection
  - Handles interruptions via audio queue drain
  - Uses media_resolution for video frames
"""

import asyncio
import base64
import logging
import uuid
from typing import AsyncIterator, Optional

from core.config import settings

logger = logging.getLogger(__name__)


class GeminiLiveSession:
    """
    Persistent Gemini Live API (bidiGenerateContent) WebSocket session.

    Bidirectional streaming:
      Send: audio chunks (PCM 16kHz), text, video frames, function responses
      Receive: audio chunks (PCM 24kHz), text, function calls, turn signals

    Follows the patterns from the Google AI Studio reference:
      - context_window_compression with sliding_window for long sessions
      - speech_config with configurable voice
      - Interruption handling via queue drain
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._session = None
        self._live = None
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._listener_task: Optional[asyncio.Task] = None
        self._is_connected = False
        self._logger = logger.getChild(f"live.{session_id[:8]}")

    @property
    def is_connected(self) -> bool:
        return self._is_connected

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

        Configures:
          - response_modalities=["AUDIO"] (native audio model)
          - context_window_compression (sliding window for long chats)
          - speech_config with voice selection
          - http_options with api_version="v1beta"
        """
        try:
            from google import genai
            from google.genai import types

            # -- Authentication --
            api_key = settings.GEMINI_API_KEY
            if api_key:
                client = genai.Client(
                    api_key=api_key,
                    http_options={"api_version": "v1beta"},
                )
                self._logger.info("Auth: API key + v1beta")
            else:
                client = genai.Client(
                    vertexai=True,
                    project=project_id,
                    location=region,
                    http_options={"api_version": "v1beta"},
                )
                self._logger.info("Auth: Vertex AI ADC + v1beta")

            # -- Build LiveConnectConfig --
            config_kwargs = {
                "response_modalities": ["AUDIO"],
                "system_instruction": types.Content(
                    parts=[types.Part(text=system_instruction)]
                ),
                "tools": [types.Tool(function_declarations=[
                    types.FunctionDeclaration(**tool) for tool in tools
                ])],
            }

            # Context window compression (prevents crash on long sessions)
            try:
                config_kwargs["context_window_compression"] = (
                    types.ContextWindowCompressionConfig(
                        trigger_tokens=104857,
                        sliding_window=types.SlidingWindow(
                            target_tokens=52428,
                        ),
                    )
                )
            except (AttributeError, TypeError):
                self._logger.debug(
                    "SDK does not support context_window_compression, skipping"
                )

            # Speech config with voice (may not be supported on all models)
            try:
                voice_name = getattr(settings, "GEMINI_VOICE", "Zephyr")
                config_kwargs["speech_config"] = types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=voice_name,
                        )
                    )
                )
            except (AttributeError, TypeError):
                self._logger.debug("SDK does not support speech_config, skipping")

            config = types.LiveConnectConfig(**config_kwargs)

            # -- Connect --
            self._session = client.aio.live.connect(
                model=model,
                config=config,
            )
            self._live = await self._session.__aenter__()
            self._is_connected = True

            # Start background listener
            self._listener_task = asyncio.create_task(
                self._listen(),
                name=f"gemini-rx-{self.session_id[:8]}",
            )

            self._logger.info(
                "Live session connected",
                extra={"model": model},
            )

        except Exception as e:
            self._logger.error(f"Connect failed: {e}", exc_info=True)
            raise

    async def _listen(self):
        """
        Background listener for Gemini Live API server messages.

        Normalises LiveServerMessage into simple dicts on the event queue:
          {"type": "text"|"audio"|"function_call"|"turn_complete"|"interrupted"|"error", ...}

        Handles both raw server_content and SDK convenience properties (.text, .data).
        On connection loss: puts a terminal error event and exits.
        """
        try:
            while self._is_connected:
                try:
                    async for response in self._live.receive():
                        await self._process_response(response)
                except asyncio.CancelledError:
                    break
                except StopAsyncIteration:
                    # Server closed the stream gracefully
                    self._logger.info("Live stream ended (server closed)")
                    await self._event_queue.put({
                        "type": "error",
                        "message": "Gemini session ended by server",
                        "fatal": True,
                    })
                    break
                except Exception as e:
                    if self._is_connected:
                        err_str = str(e)
                        self._logger.error(
                            f"Receive error: {e}", exc_info=True
                        )
                        # Fatal connection errors — stop retrying
                        is_fatal = (
                            "1008" in err_str
                            or "ConnectionClosed" in type(e).__name__
                            or "policy violation" in err_str.lower()
                        )
                        await self._event_queue.put({
                            "type": "error",
                            "message": err_str,
                            "fatal": is_fatal,
                        })
                        if is_fatal:
                            break
                        await asyncio.sleep(0.5)
                    else:
                        break
        except asyncio.CancelledError:
            pass
        finally:
            self._is_connected = False

    async def _process_response(self, response):
        """
        Parse a single LiveServerMessage into queued events.

        Order:
          1. server_content.model_turn.parts (text + inline audio)
          2. server_content.turn_complete / interrupted
          3. tool_call.function_calls
          4. Fallback: convenience .text / .data
        """
        handled_text = False
        handled_audio = False

        # -- 1. server_content --
        sc = getattr(response, "server_content", None)
        if sc is not None:
            model_turn = getattr(sc, "model_turn", None)
            if model_turn:
                for part in getattr(model_turn, "parts", None) or []:
                    # Text
                    text_val = getattr(part, "text", None)
                    if text_val:
                        handled_text = True
                        await self._event_queue.put({
                            "type": "text",
                            "data": text_val,
                        })
                    # Inline audio
                    inline = getattr(part, "inline_data", None)
                    if inline:
                        raw = getattr(inline, "data", None)
                        if raw:
                            handled_audio = True
                            await self._event_queue.put({
                                "type": "audio",
                                "data": base64.b64encode(raw).decode(),
                            })

            if getattr(sc, "turn_complete", False):
                await self._event_queue.put({"type": "turn_complete"})
            if getattr(sc, "interrupted", False):
                await self._event_queue.put({"type": "interrupted"})

        # -- 2. Convenience properties (newer SDK) --
        if not handled_text:
            text_val = getattr(response, "text", None)
            if text_val:
                await self._event_queue.put({"type": "text", "data": text_val})

        if not handled_audio:
            data_val = getattr(response, "data", None)
            if data_val and isinstance(data_val, (bytes, bytearray)):
                await self._event_queue.put({
                    "type": "audio",
                    "data": base64.b64encode(data_val).decode(),
                })

        # -- 3. Function (tool) calls --
        tc = getattr(response, "tool_call", None)
        if tc:
            for fc in getattr(tc, "function_calls", None) or []:
                await self._event_queue.put({
                    "type": "function_call",
                    "id": getattr(fc, "id", ""),
                    "name": getattr(fc, "name", ""),
                    "args": dict(fc.args) if getattr(fc, "args", None) else {},
                })

    async def receive_events(self) -> AsyncIterator[dict]:
        """Yield events from the Live session event queue."""
        while self._is_connected or not self._event_queue.empty():
            try:
                event = await asyncio.wait_for(
                    self._event_queue.get(), timeout=60.0
                )
                yield event
                # If fatal error, stop iterating
                if event.get("type") == "error" and event.get("fatal"):
                    break
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    async def send_audio(self, audio_b64: str):
        """Send a PCM 16kHz audio chunk to the Live session."""
        if not self._is_connected:
            return
        try:
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
            self._logger.error(f"send_audio failed: {e}")

    async def send_text(self, text: str):
        """Send text input with end_of_turn=True."""
        if not self._is_connected:
            return
        try:
            await self._live.send(input=text, end_of_turn=True)
        except Exception as e:
            self._logger.error(f"send_text failed: {e}")

    async def send_video_frame(
        self, frame_b64: str, width: int = 640, height: int = 480
    ):
        """
        Send a video frame (JPEG/PNG) to the Live session.
        The model uses this for vision understanding (book pages, objects, etc.).
        """
        if not self._is_connected:
            return
        try:
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
            self._logger.error(f"send_video_frame failed: {e}")

    async def send_function_response(
        self, func_id: str, func_name: str, result: str
    ):
        """Send a function call response back to the Live session."""
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
            self._logger.error(f"send_function_response failed: {e}")

    def drain_event_queue(self):
        """
        Drain all pending events from the queue (interruption pattern).
        Called when the user interrupts — discard buffered audio/text
        so the model response starts fresh.
        """
        drained = 0
        while not self._event_queue.empty():
            try:
                self._event_queue.get_nowait()
                drained += 1
            except asyncio.QueueEmpty:
                break
        if drained:
            self._logger.debug(f"Drained {drained} events on interrupt")

    async def close(self):
        """Close the Live session and cancel the listener task."""
        self._is_connected = False

        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except (asyncio.CancelledError, Exception):
                pass
            self._listener_task = None

        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception as e:
                self._logger.debug(f"Session close error (expected): {e}")
            self._session = None
            self._live = None

        self._logger.info("Live session closed")


class GeminiClientPool:
    """
    Pool of Gemini client resources.

    Manages:
      - Gemini Live sessions (WebSocket, one per connection)
      - Gemini text generation client (shared, for sub-agents)
    """

    def __init__(self, project_id: str, region: str, pool_size: int = 3):
        self.project_id = project_id
        self.region = region
        self.pool_size = pool_size
        self._text_client = None
        self._logger = logger.getChild("pool")

    async def warm_up(self):
        """Pre-initialize the text generation client."""
        try:
            from google import genai

            api_key = settings.GEMINI_API_KEY
            if api_key:
                self._text_client = genai.Client(api_key=api_key)
                self._logger.info("Text client ready (API key)")
            else:
                self._text_client = genai.Client(
                    vertexai=True,
                    project=self.project_id,
                    location=self.region,
                )
                self._logger.info("Text client ready (Vertex AI)")
        except Exception as e:
            self._logger.warning(f"Warm-up failed (will retry): {e}")

    async def acquire(
        self,
        system_instruction: str,
        tools: list[dict],
    ) -> GeminiLiveSession:
        """
        Create a new Gemini Live session.

        Each WebSocket connection gets its own stateful Live session.
        """
        session = GeminiLiveSession(uuid.uuid4().hex[:8])
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
        Stream text from Gemini text model (non-Live).
        Used by sub-agents for text generation tasks.

        Strategy:
        1. Try Vertex AI first (uses GCP billing, better quotas)
        2. Fallback to API key if Vertex AI fails
        3. Retry once on 429 errors with backoff
        """
        from google import genai
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.8,
            top_p=0.95,
            max_output_tokens=2048,
        )

        # Try up to 2 client strategies: Vertex AI first, then API key
        clients_to_try = []

        # Strategy 1: Vertex AI (on Cloud Run, uses service account)
        if self.project_id and self.region:
            try:
                vertex_client = genai.Client(
                    vertexai=True,
                    project=self.project_id,
                    location=self.region,
                )
                clients_to_try.append(("vertexai", vertex_client))
            except Exception as e:
                self._logger.debug(f"Vertex AI client init failed: {e}")

        # Strategy 2: API key (free tier, may hit quota)
        api_key = settings.GEMINI_API_KEY
        if api_key:
            try:
                key_client = genai.Client(api_key=api_key)
                clients_to_try.append(("api_key", key_client))
            except Exception as e:
                self._logger.debug(f"API key client init failed: {e}")

        last_error = None
        for strategy_name, client in clients_to_try:
            for attempt in range(2):  # retry once on 429
                try:
                    response = await client.aio.models.generate_content_stream(
                        model=settings.GEMINI_TEXT_MODEL,
                        contents=prompt,
                        config=config,
                    )

                    async for chunk in response:
                        if chunk.text:
                            yield chunk.text

                    # Success — cache this client for future calls
                    self._text_client = client
                    self._logger.debug(
                        f"Text gen succeeded with {strategy_name}"
                    )
                    return  # exit on success

                except Exception as e:
                    last_error = e
                    error_str = str(e)
                    self._logger.warning(
                        f"Text gen {strategy_name} attempt {attempt+1} failed: "
                        f"{type(e).__name__}: {error_str[:200]}"
                    )
                    # Retry on 429 with backoff
                    if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                        if attempt == 0:
                            import asyncio
                            await asyncio.sleep(5)
                            continue
                    break  # non-retryable error, try next strategy

        # All strategies failed
        self._logger.error(
            f"Text generation failed (all strategies): {last_error}",
            exc_info=True,
        )
        yield "[Generation error — please try again]"

    async def close_all(self):
        """Close all clients."""
        self._text_client = None
        self._logger.info("All clients closed")
