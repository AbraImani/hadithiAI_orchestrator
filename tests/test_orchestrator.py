"""
Orchestrator Unit Tests
=======================
Tests for the core orchestration logic, circuit breaker,
streaming controller, and agent dispatch.
"""

import asyncio
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch

# ─── Circuit Breaker Tests ────────────────────────────


class TestCircuitBreaker:
    """Tests for the circuit breaker fault tolerance pattern."""

    def setup_method(self):
        from orchestrator.circuit_breaker import CircuitBreaker
        self.breaker = CircuitBreaker("test_agent", max_failures=3, reset_timeout=1.0)

    def test_starts_closed(self):
        assert not self.breaker.is_open()
        assert self.breaker.state.value == "closed"

    def test_opens_after_max_failures(self):
        for _ in range(3):
            self.breaker.record_failure()
        assert self.breaker.is_open()
        assert self.breaker.state.value == "open"

    def test_does_not_open_below_threshold(self):
        self.breaker.record_failure()
        self.breaker.record_failure()
        assert not self.breaker.is_open()

    def test_success_resets_failures(self):
        self.breaker.record_failure()
        self.breaker.record_failure()
        self.breaker.record_success()
        assert self.breaker.failure_count == 0
        assert not self.breaker.is_open()

    def test_half_open_after_timeout(self):
        # Trip the breaker
        for _ in range(3):
            self.breaker.record_failure()
        assert self.breaker.is_open()

        # Fast-forward past reset timeout
        self.breaker.last_failure_time = time.time() - 2.0
        
        # Should transition to half-open (returns False = allow test call)
        assert not self.breaker.is_open()
        assert self.breaker.state.value == "half_open"

    def test_half_open_success_closes(self):
        for _ in range(3):
            self.breaker.record_failure()
        self.breaker.last_failure_time = time.time() - 2.0
        self.breaker.is_open()  # Transitions to half-open

        self.breaker.record_success()
        assert self.breaker.state.value == "closed"

    def test_half_open_failure_reopens(self):
        for _ in range(3):
            self.breaker.record_failure()
        self.breaker.last_failure_time = time.time() - 2.0
        self.breaker.is_open()  # Transitions to half-open

        self.breaker.record_failure()
        assert self.breaker.state.value == "open"

    def test_get_status(self):
        status = self.breaker.get_status()
        assert status["name"] == "test_agent"
        assert status["state"] == "closed"
        assert status["failure_count"] == 0


# ─── Models Tests ─────────────────────────────────────


class TestModels:
    """Tests for Pydantic data models."""

    def test_client_message_parsing(self):
        from core.models import ClientMessage, ClientMessageType
        
        msg = ClientMessage(
            type=ClientMessageType.TEXT_INPUT,
            data="Tell me a story",
            seq=1,
        )
        assert msg.type == ClientMessageType.TEXT_INPUT
        assert msg.data == "Tell me a story"

    def test_server_message_serialization(self):
        from core.models import ServerMessage, ServerMessageType
        
        msg = ServerMessage(
            type=ServerMessageType.TEXT_CHUNK,
            data="Once upon a time...",
            agent="story",
        )
        d = msg.model_dump(exclude_none=True)
        assert d["type"] == "text_chunk"
        assert d["data"] == "Once upon a time..."
        assert "timestamp" in d

    def test_agent_request(self):
        from core.models import AgentRequest, IntentType
        
        req = AgentRequest(
            intent=IntentType.REQUEST_STORY,
            user_input="Tell me a Yoruba story",
            culture="yoruba",
            theme="trickster",
        )
        assert req.intent == IntentType.REQUEST_STORY
        assert req.culture == "yoruba"

    def test_orchestrator_states(self):
        from core.models import OrchestratorState
        
        assert OrchestratorState.IDLE.value == "idle"
        assert OrchestratorState.STREAMING.value == "streaming"
        assert OrchestratorState.INTERRUPTED.value == "interrupted"


# ─── Streaming Controller Tests ──────────────────────


class TestStreamingController:
    """Tests for the streaming output controller."""

    @pytest.fixture
    def controller(self):
        from orchestrator.streaming_controller import StreamingController
        queue = asyncio.Queue(maxsize=50)
        return StreamingController(queue, "test-session")

    @pytest.mark.asyncio
    async def test_text_chunk_buffering(self, controller):
        """Text should be buffered until sentence boundary."""
        await controller.send_text_chunk("Hello, ", agent="story")
        # Should not flush yet (no sentence boundary)
        assert controller.output_queue.empty()

        await controller.send_text_chunk("this is a test.", agent="story")
        # Now should flush (period = sentence boundary)
        assert not controller.output_queue.empty()
        
        msg = controller.output_queue.get_nowait()
        assert "Hello, this is a test." in msg.data

    @pytest.mark.asyncio
    async def test_force_flush_on_long_buffer(self, controller):
        """Should force flush when buffer exceeds max length."""
        long_text = "a" * 250
        await controller.send_text_chunk(long_text, agent="story")
        assert not controller.output_queue.empty()

    @pytest.mark.asyncio
    async def test_turn_end_flushes_buffer(self, controller):
        """Turn end should flush remaining buffer."""
        await controller.send_text_chunk("Leftover text", agent="story")
        await controller.send_turn_end()
        
        # Should have flushed text + turn_end message
        messages = []
        while not controller.output_queue.empty():
            messages.append(controller.output_queue.get_nowait())
        
        assert len(messages) >= 1  # At least turn_end


# ─── Cultural Agent Tests ────────────────────────────


class TestCulturalKnowledge:
    """Tests for the cultural knowledge base."""

    def test_knowledge_base_populated(self):
        from agents.cultural_agent import CULTURAL_KNOWLEDGE
        
        assert "story_openings" in CULTURAL_KNOWLEDGE
        assert "swahili" in CULTURAL_KNOWLEDGE["story_openings"]
        assert "yoruba" in CULTURAL_KNOWLEDGE["story_openings"]
        assert "zulu" in CULTURAL_KNOWLEDGE["story_openings"]

    def test_proverbs_available(self):
        from agents.cultural_agent import CULTURAL_KNOWLEDGE
        
        proverbs = CULTURAL_KNOWLEDGE["proverbs"]
        assert len(proverbs["swahili"]) >= 2
        assert len(proverbs["yoruba"]) >= 2
        assert len(proverbs["zulu"]) >= 2

    def test_trickster_figures(self):
        from agents.cultural_agent import CULTURAL_KNOWLEDGE
        
        figures = CULTURAL_KNOWLEDGE["trickster_figures"]
        assert "anansi" in figures["ashanti"].lower()
        assert "hare" in figures["zulu"].lower()


# ─── Config Tests ─────────────────────────────────────


class TestConfig:
    """Tests for configuration loading."""

    def test_default_settings(self):
        from core.config import Settings
        
        s = Settings()
        assert s.GEMINI_MODEL == "gemini-2.0-flash-live"
        assert s.AUDIO_SAMPLE_RATE_INPUT == 16000
        assert s.AUDIO_SAMPLE_RATE_OUTPUT == 24000
        assert s.STREAM_BUFFER_HIGH_WATERMARK == 50
        assert s.CULTURAL_CONFIDENCE_THRESHOLD == 0.7

    def test_env_prefix(self):
        """Settings should use HADITHI_ prefix for env vars."""
        from core.config import Settings
        
        assert Settings.model_config.get("env_prefix") == "HADITHI_"


# ─── Integration Flow Test ───────────────────────────


class TestIntegrationFlow:
    """Conceptual integration test verifying the flow connects."""

    def test_intent_to_agent_mapping(self):
        """Verify intent types map to correct agents."""
        from core.models import IntentType
        from orchestrator.agent_dispatcher import AgentDispatcher
        
        mapping = {
            IntentType.REQUEST_STORY: "story",
            IntentType.REQUEST_RIDDLE: "riddle",
            IntentType.ASK_CULTURAL: "cultural",
            IntentType.REQUEST_IMAGE: "visual",
        }
        
        for intent, expected_agent in mapping.items():
            assert AgentDispatcher._get_agent_name(intent) == expected_agent

    def test_message_types_complete(self):
        """Verify all required message types exist."""
        from core.models import ClientMessageType, ServerMessageType
        
        # Client must support
        assert ClientMessageType.AUDIO_CHUNK
        assert ClientMessageType.TEXT_INPUT
        assert ClientMessageType.INTERRUPT
        assert ClientMessageType.CONTROL
        
        # Server must support
        assert ServerMessageType.AUDIO_CHUNK
        assert ServerMessageType.TEXT_CHUNK
        assert ServerMessageType.IMAGE_READY
        assert ServerMessageType.TURN_END
        assert ServerMessageType.ERROR
