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

            # Create client
            client = genai.Client(
                vertexai=True,
                project=project_id,
                location=region,
            )

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

            # Connect to Live API
            self._session = client.aio.live.connect(
                model=f"models/{model}",
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
        
        Translates raw API events into our internal event format
        and puts them on the event queue.
        """
        try:
            while self._is_connected:
                try:
                    async for response in self._live.receive():
                        # Handle different response types
                        if hasattr(response, 'text') and response.text:
                            await self._event_queue.put({
                                "type": "text",
                                "data": response.text,
                            })

                        if hasattr(response, 'data') and response.data:
                            # Audio data (bytes)
                            import base64
                            audio_b64 = base64.b64encode(response.data).decode()
                            await self._event_queue.put({
                                "type": "audio",
                                "data": audio_b64,
                            })

                        # Function calls
                        if hasattr(response, 'tool_call') and response.tool_call:
                            for fc in response.tool_call.function_calls:
                                await self._event_queue.put({
                                    "type": "function_call",
                                    "id": fc.id,
                                    "name": fc.name,
                                    "args": dict(fc.args) if fc.args else {},
                                })

                        # Turn complete
                        if hasattr(response, 'server_content') and response.server_content:
                            if response.server_content.turn_complete:
                                await self._event_queue.put({
                                    "type": "turn_complete",
                                })

                        # Interrupted
                        if hasattr(response, 'server_content') and response.server_content:
                            if response.server_content.interrupted:
                                await self._event_queue.put({
                                    "type": "interrupted",
                                })

                except Exception as e:
                    if self._is_connected:
                        self._logger.error(f"Gemini Live receive error: {e}")
                        await self._event_queue.put({
                            "type": "error",
                            "message": str(e),
                        })
                    break

        except asyncio.CancelledError:
            pass

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

            self._text_client = genai.Client(
                vertexai=True,
                project=self.project_id,
                location=self.region,
            )
            self._logger.info("Gemini text client initialized")
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
                self._text_client = genai.Client(
                    vertexai=True,
                    project=self.project_id,
                    location=self.region,
                )

            response = self._text_client.aio.models.generate_content_stream(
                model=f"models/{settings.GEMINI_TEXT_MODEL}",
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
