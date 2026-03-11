"""
Voice Activity Detection (VAD)
==============================
Pure-Python energy-based VAD to filter non-speech audio before
forwarding to Gemini Live API. Prevents ambient noise (fans,
traffic, typing) from triggering Gemini's interruption detection.

Design:
  - RMS energy threshold to detect loud enough audio
  - Zero-crossing rate filter to reject high-frequency noise
  - Hysteresis (speech/silence frame counters) to prevent toggling
  - No external C dependencies — works on any platform
"""

import base64
import logging
import math
import struct
from typing import Optional

logger = logging.getLogger(__name__)


class VoiceActivityDetector:
    """
    Energy-based Voice Activity Detector.

    Analyzes PCM16 audio frames to distinguish human speech
    from ambient noise. Uses a two-stage filter:
      1. RMS energy must exceed ENERGY_THRESHOLD (filters quiet noise)
      2. Zero-crossing rate must be below ZCR_MAX (filters hissing/static)

    Hysteresis prevents rapid on/off toggling:
      - Need SPEECH_FRAMES_TRIGGER consecutive speech frames to start
      - Need SILENCE_FRAMES_TRIGGER consecutive silence frames to stop

    Usage:
        vad = VoiceActivityDetector(sample_rate=16000)
        is_speech = vad.process_audio(audio_b64)
        if is_speech:
            # Forward to Gemini
    """

    # ─── Tunable Parameters ───────────────────────────────────────
    ENERGY_THRESHOLD = 250       # RMS energy threshold (PCM16 range: 0-32768)
    ENERGY_THRESHOLD_LOW = 150   # Lower threshold when already speaking (hysteresis)
    ZCR_MAX = 80                 # Max zero-crossing rate per frame (filters static)
    SPEECH_FRAMES_TRIGGER = 2    # Consecutive speech frames to start forwarding
    SILENCE_FRAMES_TRIGGER = 20  # Consecutive silence frames to stop (~600ms)
    FRAME_DURATION_MS = 30       # Frame size in milliseconds

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.frame_samples = int(sample_rate * self.FRAME_DURATION_MS / 1000)
        self.frame_bytes = self.frame_samples * 2  # 2 bytes per PCM16 sample

        # State
        self._buffer = b""
        self._speech_count = 0
        self._silence_count = 0
        self._is_speaking = False
        self._total_frames = 0
        self._speech_frames_total = 0

        self._logger = logger.getChild("vad")

    def process_audio(self, audio_b64: str) -> bool:
        """
        Process a base64-encoded PCM16 audio chunk.

        Returns True if the audio likely contains human speech
        and should be forwarded to Gemini Live API.
        Returns False if it's ambient noise and should be dropped.

        The audio is analyzed frame-by-frame (30ms frames).
        State is maintained across calls for continuity.
        """
        try:
            audio_bytes = base64.b64decode(audio_b64)
        except Exception:
            # If we can't decode, pass through (safety)
            return True

        self._buffer += audio_bytes
        speech_detected_in_chunk = False

        while len(self._buffer) >= self.frame_bytes:
            frame = self._buffer[:self.frame_bytes]
            self._buffer = self._buffer[self.frame_bytes:]

            is_speech = self._analyze_frame(frame)
            self._total_frames += 1

            if is_speech:
                self._speech_count += 1
                self._silence_count = 0
                self._speech_frames_total += 1

                if self._speech_count >= self.SPEECH_FRAMES_TRIGGER:
                    if not self._is_speaking:
                        self._logger.debug(
                            "Speech detected — starting audio forwarding"
                        )
                    self._is_speaking = True
                    speech_detected_in_chunk = True
            else:
                self._silence_count += 1
                self._speech_count = 0

                if self._silence_count >= self.SILENCE_FRAMES_TRIGGER:
                    if self._is_speaking:
                        self._logger.debug(
                            "Silence detected — stopping audio forwarding"
                        )
                    self._is_speaking = False

            # If currently speaking, mark this chunk as speech
            if self._is_speaking:
                speech_detected_in_chunk = True

        return speech_detected_in_chunk

    def _analyze_frame(self, frame: bytes) -> bool:
        """
        Analyze a single audio frame for speech characteristics.

        Uses two metrics:
        1. RMS energy — must be above threshold (speech is louder than noise)
        2. Zero-crossing rate — must be reasonable (pure noise has very high ZCR)

        Returns True if the frame likely contains speech.
        """
        try:
            n = len(frame) // 2
            samples = struct.unpack(f"<{n}h", frame)
        except struct.error:
            return True  # On decode error, pass through

        # ── RMS Energy ──
        sum_sq = sum(s * s for s in samples)
        rms = math.sqrt(sum_sq / n) if n > 0 else 0

        # Use lower threshold if already speaking (hysteresis)
        threshold = (
            self.ENERGY_THRESHOLD_LOW if self._is_speaking
            else self.ENERGY_THRESHOLD
        )

        if rms < threshold:
            return False

        # ── Zero-Crossing Rate ──
        zcr = sum(
            1 for i in range(1, n)
            if (samples[i] >= 0) != (samples[i - 1] >= 0)
        )

        # Very high ZCR with moderate energy = static/hiss, not speech
        if zcr > self.ZCR_MAX:
            return False

        return True

    def reset(self):
        """Reset VAD state (e.g., after interrupt or new turn)."""
        self._buffer = b""
        self._speech_count = 0
        self._silence_count = 0
        self._is_speaking = False

    @property
    def is_speaking(self) -> bool:
        """Whether the VAD currently considers the user to be speaking."""
        return self._is_speaking

    def get_stats(self) -> dict:
        """Return VAD statistics for logging/debugging."""
        return {
            "total_frames": self._total_frames,
            "speech_frames": self._speech_frames_total,
            "speech_ratio": (
                self._speech_frames_total / self._total_frames
                if self._total_frames > 0 else 0
            ),
            "is_speaking": self._is_speaking,
        }
