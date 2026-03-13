"""
Regression tests for interrupt recovery behavior.
"""

import base64
import math
import struct
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.primary_orchestrator import PrimaryOrchestrator


def _make_pcm16_tone_b64(num_samples: int = 3200, freq: float = 220.0, amplitude: int = 5000, sample_rate: int = 16000) -> str:
    """Generate a base64 PCM16 tone that VAD classifies as speech."""
    samples = []
    for i in range(num_samples):
        t = i / sample_rate
        sample = int(amplitude * math.sin(2 * math.pi * freq * t))
        samples.append(max(-32768, min(32767, sample)))
    pcm = struct.pack(f"<{num_samples}h", *samples)
    return base64.b64encode(pcm).decode()


@pytest.mark.asyncio
async def test_audio_followup_force_clears_interrupt_suppression():
    """
    If an interrupt suppression window expires without turn_complete,
    first valid speech chunk should clear suppression and be forwarded.
    """
    output_queue = AsyncMock()
    firestore = AsyncMock()
    gemini_pool = AsyncMock()

    orch = PrimaryOrchestrator(
        session_id="s1",
        firestore=firestore,
        gemini_pool=gemini_pool,
        output_queue=output_queue,
    )

    # Mock a live session to verify forwarding + queue drain
    fake_session = AsyncMock()
    fake_session.send_audio = AsyncMock()
    fake_session.drain_event_queue = MagicMock()
    orch.gemini_session = fake_session

    # Simulate stale interrupt state (no turn_complete arrived)
    orch._interrupted = True
    orch._interrupt_at = time.time() - 5.0

    speech_b64 = _make_pcm16_tone_b64()
    await orch.handle_audio_chunk(speech_b64, seq=1)

    assert orch._interrupted is False
    fake_session.drain_event_queue.assert_called_once()
    fake_session.send_audio.assert_called_once()
