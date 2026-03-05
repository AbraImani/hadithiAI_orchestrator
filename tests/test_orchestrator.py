"""
Orchestrator Unit Tests
=======================
Tests for the core orchestration logic, circuit breaker,
streaming controller, agent dispatch, and REST API.
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

    def test_a2a_task_model(self):
        from core.models import A2ATask, A2ATaskState
        
        task = A2ATask(
            task_id="task_abc123",
            task_type="StoryRequest",
            payload={"culture": "Yoruba", "theme": "trickster"},
            source_agent="orchestrator",
            target_agent="story_agent",
        )
        assert task.state == A2ATaskState.PENDING
        assert task.error is None


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

    @pytest.mark.asyncio
    async def test_audio_chunk_no_buffering(self, controller):
        """Audio chunks should pass through immediately (no buffering)."""
        await controller.send_audio_chunk("base64audiodata==")
        assert not controller.output_queue.empty()
        msg = controller.output_queue.get_nowait()
        assert msg.type.value == "audio_chunk"
        assert msg.data == "base64audiodata=="

    @pytest.mark.asyncio
    async def test_metrics_tracking(self, controller):
        """Verify TTFB and chunk count metrics are tracked."""
        await controller.send_audio_chunk("audio1")
        await controller.send_audio_chunk("audio2")
        await controller.send_text_chunk("Hello.", agent="story")
        
        assert controller._audio_chunks_sent == 2
        assert controller._text_chunks_sent == 1
        assert controller._chunks_sent == 3
        assert controller._first_byte_time > 0

    @pytest.mark.asyncio
    async def test_turn_end_resets_metrics(self, controller):
        """Metrics should reset after turn_end."""
        await controller.send_audio_chunk("audio1")
        await controller.send_turn_end()
        
        assert controller._chunks_sent == 0
        assert controller._audio_chunks_sent == 0
        assert controller._stream_start_time == 0


# ─── GeminiLiveSession Tests ────────────────────────


class TestGeminiLiveSession:
    """Tests for the Gemini Live session management."""

    def test_session_initial_state(self):
        from services.gemini_client import GeminiLiveSession
        
        session = GeminiLiveSession("test-id")
        assert not session.is_connected
        assert session.session_id == "test-id"

    def test_drain_event_queue(self):
        from services.gemini_client import GeminiLiveSession
        
        session = GeminiLiveSession("test-id")
        # Put some events
        session._event_queue.put_nowait({"type": "audio", "data": "a"})
        session._event_queue.put_nowait({"type": "audio", "data": "b"})
        session._event_queue.put_nowait({"type": "text", "data": "c"})
        
        assert session._event_queue.qsize() == 3
        session.drain_event_queue()
        assert session._event_queue.qsize() == 0


# ─── Cultural Agent Tests ────────────────────────────


class TestCulturalKnowledge:
    """Tests for the cultural knowledge base."""

    def test_knowledge_base_populated(self):
        from agents.cultural_agent import CULTURAL_KNOWLEDGE

        assert "story_openings" in CULTURAL_KNOWLEDGE
        swahili_openings = CULTURAL_KNOWLEDGE["story_openings"]["swahili"]
        yoruba_openings = CULTURAL_KNOWLEDGE["story_openings"]["yoruba"]
        zulu_openings = CULTURAL_KNOWLEDGE["story_openings"]["zulu"]
        assert len(swahili_openings) > 0
        assert len(yoruba_openings) > 0
        assert len(zulu_openings) > 0

    def test_proverbs_available(self):
        from agents.cultural_agent import CULTURAL_KNOWLEDGE

        proverbs = CULTURAL_KNOWLEDGE["proverbs"]
        assert len(proverbs["swahili"]) >= 2
        assert len(proverbs["yoruba"]) >= 1
        assert len(proverbs["zulu"]) >= 2

    def test_trickster_figures(self):
        from agents.cultural_agent import CULTURAL_KNOWLEDGE

        figures = CULTURAL_KNOWLEDGE["trickster_figures"]
        # Each entry is a dict with 'name', 'type', 'verified' fields
        assert "anansi" in figures["ashanti"]["name"].lower()
        assert "hare" in figures["zulu"]["type"].lower()

    def test_story_closings(self):
        from agents.cultural_agent import CULTURAL_KNOWLEDGE
        
        closings = CULTURAL_KNOWLEDGE["story_closings"]
        assert "swahili" in closings
        assert "yoruba" in closings
        assert closings["swahili"]["verified"] is True


# ─── Config Tests ─────────────────────────────────────


class TestConfig:
    """Tests for configuration loading."""

    def test_default_settings(self):
        from core.config import Settings
        
        s = Settings()
        assert "gemini" in s.GEMINI_MODEL.lower()
        assert "native-audio" in s.GEMINI_MODEL.lower()
        assert s.AUDIO_SAMPLE_RATE_INPUT == 16000
        assert s.AUDIO_SAMPLE_RATE_OUTPUT == 24000
        assert s.STREAM_BUFFER_HIGH_WATERMARK == 50
        assert s.CULTURAL_CONFIDENCE_THRESHOLD == 0.7

    def test_env_prefix(self):
        """Settings should use HADITHI_ prefix for env vars."""
        from core.config import Settings
        
        assert Settings.model_config.get("env_prefix") == "HADITHI_"

    def test_gemini_voice_default(self):
        """Gemini voice should default to Zephyr."""
        from core.config import Settings
        
        s = Settings()
        assert s.GEMINI_VOICE == "Zephyr"


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
        assert ClientMessageType.VIDEO_FRAME
        
        # Server must support
        assert ServerMessageType.AUDIO_CHUNK
        assert ServerMessageType.TEXT_CHUNK
        assert ServerMessageType.IMAGE_READY
        assert ServerMessageType.TURN_END
        assert ServerMessageType.ERROR
        assert ServerMessageType.INTERRUPTED


# ─── REST API Tests ───────────────────────────────────


class TestRestAPIModels:
    """Test REST API request/response models."""

    def test_create_session_request(self):
        from gateway.rest_api import CreateSessionRequest
        
        req = CreateSessionRequest(language="sw", region="east-africa")
        assert req.language == "sw"
        assert req.age_group == "adult"

    def test_create_session_response(self):
        from gateway.rest_api import CreateSessionResponse
        
        resp = CreateSessionResponse(
            session_id="abc123",
            websocket_url="wss://example.com/ws?session_id=abc123",
            created_at=time.time(),
        )
        assert resp.session_id == "abc123"
        assert "ws" in resp.websocket_url

    def test_text_input_request_validation(self):
        from gateway.rest_api import TextInputRequest
        from pydantic import ValidationError
        
        # Valid
        req = TextInputRequest(text="Tell me a story")
        assert req.text == "Tell me a story"
        
        # Too short (empty)
        with pytest.raises(ValidationError):
            TextInputRequest(text="")

    def test_health_detail_response(self):
        from gateway.rest_api import HealthDetailResponse
        
        resp = HealthDetailResponse(
            status="healthy",
            service="hadithiai-live",
            version="2.0.0",
            uptime_seconds=120.5,
            active_sessions=3,
            gemini_pool_ready=True,
        )
        assert resp.version == "2.0.0"
        assert resp.gemini_pool_ready is True
