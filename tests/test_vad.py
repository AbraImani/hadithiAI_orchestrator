"""
Tests for Voice Activity Detection (VAD)
"""
import base64
import math
import struct
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from services.vad import VoiceActivityDetector


def _make_pcm16_silence(num_samples: int) -> bytes:
    """Generate silence (all zeros) as PCM16 bytes."""
    return struct.pack(f"<{num_samples}h", *([0] * num_samples))


def _make_pcm16_tone(num_samples: int, freq: float = 440, amplitude: int = 5000,
                      sample_rate: int = 16000) -> bytes:
    """Generate a sine tone as PCM16 bytes."""
    samples = []
    for i in range(num_samples):
        t = i / sample_rate
        sample = int(amplitude * math.sin(2 * math.pi * freq * t))
        samples.append(max(-32768, min(32767, sample)))
    return struct.pack(f"<{num_samples}h", *samples)


def _make_pcm16_noise(num_samples: int, amplitude: int = 50) -> bytes:
    """Generate low-amplitude random-ish noise."""
    import random
    random.seed(42)
    samples = [random.randint(-amplitude, amplitude) for _ in range(num_samples)]
    return struct.pack(f"<{num_samples}h", *samples)


class TestVAD:
    """Test VoiceActivityDetector."""

    def test_silence_not_detected_as_speech(self):
        vad = VoiceActivityDetector(sample_rate=16000)
        silence = _make_pcm16_silence(1600)  # 100ms
        b64 = base64.b64encode(silence).decode()
        assert vad.process_audio(b64) is False

    def test_loud_tone_detected_as_speech(self):
        vad = VoiceActivityDetector(sample_rate=16000)
        # Need enough frames to trigger (SPEECH_FRAMES_TRIGGER = 2)
        tone = _make_pcm16_tone(3200, freq=300, amplitude=5000)
        b64 = base64.b64encode(tone).decode()
        result = vad.process_audio(b64)
        assert result is True

    def test_low_noise_not_detected(self):
        vad = VoiceActivityDetector(sample_rate=16000)
        noise = _make_pcm16_noise(3200, amplitude=30)
        b64 = base64.b64encode(noise).decode()
        assert vad.process_audio(b64) is False

    def test_hysteresis_prevents_toggling(self):
        vad = VoiceActivityDetector(sample_rate=16000)
        # Start speaking
        tone = _make_pcm16_tone(3200, freq=300, amplitude=5000)
        b64_speech = base64.b64encode(tone).decode()
        vad.process_audio(b64_speech)
        assert vad.is_speaking is True

        # One frame of quiet should NOT stop speaking
        quiet = _make_pcm16_silence(480)  # 30ms
        b64_quiet = base64.b64encode(quiet).decode()
        vad.process_audio(b64_quiet)
        assert vad.is_speaking is True  # Still speaking due to hysteresis

    def test_reset_clears_state(self):
        vad = VoiceActivityDetector(sample_rate=16000)
        tone = _make_pcm16_tone(3200, freq=300, amplitude=5000)
        b64 = base64.b64encode(tone).decode()
        vad.process_audio(b64)
        assert vad.is_speaking is True

        vad.reset()
        assert vad.is_speaking is False

    def test_get_stats(self):
        vad = VoiceActivityDetector(sample_rate=16000)
        stats = vad.get_stats()
        assert "total_frames" in stats
        assert "speech_frames" in stats
        assert "is_speaking" in stats

    def test_invalid_base64_passes_through(self):
        vad = VoiceActivityDetector(sample_rate=16000)
        # Invalid base64 should be treated as speech (safety pass-through)
        result = vad.process_audio("not-valid-base64!!!")
        assert result is True


class TestRiddleModel:
    """Test the new Flutter-compatible RiddleModel."""

    def test_riddle_model_structure(self):
        from core.models import RiddleModel
        riddle = RiddleModel(
            id="r1",
            question="What has roots?",
            choices=[
                {"A tree": True},
                {"A river": False},
                {"Wind": False},
                {"Cloud": False},
            ],
            tip="It grows",
            help="Think of forests",
            language="Swahili",
        )
        assert riddle.id == "r1"
        assert len(riddle.choices) == 4
        assert riddle.tip == "It grows"

    def test_story_category_model(self):
        from core.models import StoryCategoryModel
        story = StoryCategoryModel(
            title="Anansi and the Pot",
            description="Anansi collects all wisdom.",
            imageUrl="https://example.com/img.png",
            day=5,
            month="March",
            region="West Africa",
        )
        assert story.title == "Anansi and the Pot"
        assert story.day == 5
