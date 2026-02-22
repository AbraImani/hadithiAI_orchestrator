"""
Streaming Controller
====================
Manages the output stream to the client. Handles buffering,
pacing, backpressure, and message formatting.
"""

import asyncio
import logging
import time

from core.models import ServerMessage, ServerMessageType
from core.config import settings

logger = logging.getLogger(__name__)


class StreamingController:
    """
    Controls the output stream to the client WebSocket.
    
    Responsibilities:
    - Buffer text tokens until sentence boundaries for natural TTS
    - Manage output queue backpressure
    - Interleave text and audio messages
    - Track streaming metrics
    """

    def __init__(self, output_queue: asyncio.Queue, session_id: str):
        self.output_queue = output_queue
        self.session_id = session_id
        self._text_buffer = ""
        self._chunks_sent = 0
        self._stream_start_time: float = 0
        self._logger = logger.getChild(f"stream.{session_id}")

    async def send_text_chunk(self, text: str, agent: str = "orchestrator"):
        """
        Send a text chunk to the client.
        
        Buffers until a sentence boundary for smoother delivery.
        """
        self._text_buffer += text

        # Flush at sentence boundaries for natural reading
        sentence_enders = (".", "!", "?", "...", "\n")
        if any(self._text_buffer.rstrip().endswith(e) for e in sentence_enders):
            await self._flush_text_buffer(agent)
        elif len(self._text_buffer) > 200:
            # Force flush if buffer gets too large
            await self._flush_text_buffer(agent)

    async def _flush_text_buffer(self, agent: str):
        """Flush the text buffer to the output queue."""
        if not self._text_buffer.strip():
            return

        text = self._text_buffer
        self._text_buffer = ""

        if self._chunks_sent == 0:
            self._stream_start_time = time.time()

        msg = ServerMessage(
            type=ServerMessageType.TEXT_CHUNK,
            data=text,
            agent=agent,
        )

        await self._enqueue(msg)
        self._chunks_sent += 1

    async def send_audio_chunk(self, audio_b64: str):
        """Send an audio chunk to the client."""
        msg = ServerMessage(
            type=ServerMessageType.AUDIO_CHUNK,
            data=audio_b64,
        )
        await self._enqueue(msg)

    async def send_image_ready(self, url: str):
        """Send image URL to client when generation completes."""
        msg = ServerMessage(
            type=ServerMessageType.IMAGE_READY,
            url=url,
            agent="visual",
        )
        await self._enqueue(msg)
        self._logger.info(
            f"Image sent to client",
            extra={"event": "image_sent"},
        )

    async def send_agent_state(self, agent: str, state: str):
        """Notify client about agent state changes (UX feedback)."""
        msg = ServerMessage(
            type=ServerMessageType.AGENT_STATE,
            agent=agent,
            state=state,
        )
        await self._enqueue(msg)

    async def send_turn_end(self):
        """Signal the end of an agent turn."""
        # Flush any remaining text
        if self._text_buffer:
            await self._flush_text_buffer("orchestrator")

        msg = ServerMessage(type=ServerMessageType.TURN_END)
        await self._enqueue(msg)

        # Log streaming metrics
        if self._stream_start_time > 0:
            total_time = (time.time() - self._stream_start_time) * 1000
            self._logger.info(
                f"Turn complete: {self._chunks_sent} chunks in {total_time:.0f}ms",
                extra={
                    "event": "turn_complete",
                    "chunks_sent": self._chunks_sent,
                    "latency_ms": total_time,
                },
            )

        self._chunks_sent = 0
        self._stream_start_time = 0

    async def send_error(self, message: str):
        """Send an error message to the client."""
        msg = ServerMessage(
            type=ServerMessageType.ERROR,
            error=message,
        )
        await self._enqueue(msg)

    async def _enqueue(self, msg: ServerMessage):
        """
        Enqueue a message with backpressure handling.
        
        If the queue is full, we apply backpressure by waiting.
        This prevents memory overflow if the client can't keep up.
        """
        try:
            # Try non-blocking first
            self.output_queue.put_nowait(msg)
        except asyncio.QueueFull:
            self._logger.warning(
                "Output queue full — applying backpressure",
                extra={"event": "backpressure"},
            )
            # Wait with timeout — if client is completely stuck, give up
            try:
                await asyncio.wait_for(
                    self.output_queue.put(msg), timeout=5.0
                )
            except asyncio.TimeoutError:
                self._logger.error(
                    "Output queue timeout — dropping message",
                    extra={"event": "message_dropped"},
                )
