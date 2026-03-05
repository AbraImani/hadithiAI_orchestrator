"""
Streaming Controller
====================
Manages the output stream to the client. Handles buffering,
pacing, backpressure, and message formatting.

Design priorities:
  - Ultra-low latency for audio chunks (no buffering)
  - Text buffered to sentence boundaries (natural reading)
  - Backpressure via queue watermarks
  - Metrics for observability
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
    - Audio chunks: pass through immediately (zero buffering)
    - Text tokens: buffer until sentence boundaries for natural TTS
    - Manage output queue backpressure
    - Interleave text, audio, and metadata messages
    - Track streaming metrics (TTFB, throughput, drops)
    """

    def __init__(self, output_queue: asyncio.Queue, session_id: str):
        self.output_queue = output_queue
        self.session_id = session_id
        self._text_buffer = ""
        self._chunks_sent = 0
        self._audio_chunks_sent = 0
        self._text_chunks_sent = 0
        self._dropped_count = 0
        self._stream_start_time: float = 0
        self._first_byte_time: float = 0
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
            await self._flush_text_buffer(agent)

    async def _flush_text_buffer(self, agent: str):
        """Flush the text buffer to the output queue."""
        if not self._text_buffer.strip():
            return

        text = self._text_buffer
        self._text_buffer = ""

        self._record_first_byte()

        msg = ServerMessage(
            type=ServerMessageType.TEXT_CHUNK,
            data=text,
            agent=agent,
        )

        await self._enqueue(msg)
        self._chunks_sent += 1
        self._text_chunks_sent += 1

    async def send_audio_chunk(self, audio_b64: str):
        """
        Send an audio chunk to the client.
        Audio is NEVER buffered — direct pass-through for lowest latency.
        """
        self._record_first_byte()

        msg = ServerMessage(
            type=ServerMessageType.AUDIO_CHUNK,
            data=audio_b64,
        )
        await self._enqueue(msg)
        self._chunks_sent += 1
        self._audio_chunks_sent += 1

    async def send_image_ready(self, url: str):
        """Send image URL to client when generation completes."""
        msg = ServerMessage(
            type=ServerMessageType.IMAGE_READY,
            url=url,
            agent="visual",
        )
        await self._enqueue(msg)
        self._logger.info("Image sent to client", extra={"event": "image_sent"})

    async def send_agent_state(self, agent: str, state: str):
        """Notify client about agent state changes (UX feedback)."""
        msg = ServerMessage(
            type=ServerMessageType.AGENT_STATE,
            agent=agent,
            state=state,
        )
        await self._enqueue(msg)

    async def send_turn_end(self):
        """Signal the end of an agent turn with metrics."""
        # Flush any remaining text
        if self._text_buffer:
            await self._flush_text_buffer("orchestrator")

        msg = ServerMessage(type=ServerMessageType.TURN_END)
        await self._enqueue(msg)

        # Log streaming metrics
        if self._stream_start_time > 0:
            total_time = (time.time() - self._stream_start_time) * 1000
            ttfb = (
                (self._first_byte_time - self._stream_start_time) * 1000
                if self._first_byte_time > 0 else 0
            )
            self._logger.info(
                f"Turn complete: {self._chunks_sent} chunks "
                f"({self._audio_chunks_sent} audio, {self._text_chunks_sent} text) "
                f"in {total_time:.0f}ms, TTFB={ttfb:.0f}ms, "
                f"dropped={self._dropped_count}",
                extra={
                    "event": "turn_complete",
                    "chunks_sent": self._chunks_sent,
                    "audio_chunks": self._audio_chunks_sent,
                    "text_chunks": self._text_chunks_sent,
                    "latency_ms": total_time,
                    "ttfb_ms": ttfb,
                    "dropped": self._dropped_count,
                },
            )

        # Reset metrics for next turn
        self._chunks_sent = 0
        self._audio_chunks_sent = 0
        self._text_chunks_sent = 0
        self._dropped_count = 0
        self._stream_start_time = 0
        self._first_byte_time = 0

    async def send_error(self, message: str):
        """Send an error message to the client."""
        msg = ServerMessage(
            type=ServerMessageType.ERROR,
            error=message,
        )
        await self._enqueue(msg)

    def _record_first_byte(self):
        """Track time-to-first-byte for the current turn."""
        now = time.time()
        if self._stream_start_time == 0:
            self._stream_start_time = now
        if self._first_byte_time == 0:
            self._first_byte_time = now

    async def _enqueue(self, msg: ServerMessage):
        """
        Enqueue a message with backpressure handling.
        
        If the queue is full, apply backpressure by waiting.
        Prevents memory overflow if the client can't keep up.
        """
        try:
            self.output_queue.put_nowait(msg)
        except asyncio.QueueFull:
            self._logger.warning(
                "Output queue full — applying backpressure",
                extra={"event": "backpressure"},
            )
            try:
                await asyncio.wait_for(
                    self.output_queue.put(msg), timeout=5.0
                )
            except asyncio.TimeoutError:
                self._dropped_count += 1
                self._logger.error(
                    "Output queue timeout — dropping message",
                    extra={
                        "event": "message_dropped",
                        "msg_type": msg.type.value,
                    },
                )
