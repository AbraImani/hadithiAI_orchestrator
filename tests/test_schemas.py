"""
Schema Contract Tests
=====================
Validates that all A2A JSON schema contracts are correct,
that the validator enforces them, and that agents produce
schema-compliant output.
"""

import pytest
from core.schemas import (
    schema_validator,
    SchemaViolationError,
    STORY_REQUEST_SCHEMA,
    STORY_CHUNK_SCHEMA,
    VALIDATED_CHUNK_SCHEMA,
    RIDDLE_REQUEST_SCHEMA,
    RIDDLE_PAYLOAD_SCHEMA,
    IMAGE_REQUEST_SCHEMA,
    IMAGE_RESULT_SCHEMA,
)


# -- Schema Registry Tests --


class TestSchemaRegistry:
    """Ensure all schemas are registered and accessible."""

    def test_all_schemas_registered(self):
        expected = [
            "StoryRequest",
            "StoryChunk",
            "ValidatedChunk",
            "RiddleRequest",
            "RiddlePayload",
            "ImageRequest",
            "ImageResult",
        ]
        registered = schema_validator.list_schemas()
        for name in expected:
            assert name in registered, f"Schema '{name}' not registered"

    def test_unknown_schema_returns_invalid(self):
        is_valid, errors = schema_validator.validate("NonExistent", {})
        assert not is_valid


# -- StoryRequest Validation --


class TestStoryRequestSchema:
    """Validate StoryRequest schema enforcement."""

    def test_valid_minimal(self):
        data = {"culture": "Yoruba", "theme": "trickster"}
        schema_validator.validate_or_reject("StoryRequest", data)

    def test_valid_full(self):
        data = {
            "culture": "Zulu",
            "theme": "creation",
            "complexity": "child",
            "continuation": True,
            "session_context": "User wants a bedtime story",
        }
        schema_validator.validate_or_reject("StoryRequest", data)

    def test_missing_required_culture(self):
        with pytest.raises(SchemaViolationError):
            schema_validator.validate_or_reject("StoryRequest", {"theme": "wisdom"})

    def test_missing_required_theme(self):
        with pytest.raises(SchemaViolationError):
            schema_validator.validate_or_reject("StoryRequest", {"culture": "Ashanti"})

    def test_invalid_theme_enum(self):
        with pytest.raises(SchemaViolationError):
            schema_validator.validate_or_reject(
                "StoryRequest",
                {"culture": "Maasai", "theme": "sci-fi"},
            )

    def test_invalid_complexity_enum(self):
        with pytest.raises(SchemaViolationError):
            schema_validator.validate_or_reject(
                "StoryRequest",
                {"culture": "Kikuyu", "theme": "wisdom", "complexity": "expert"},
            )


# -- StoryChunk Validation --


class TestStoryChunkSchema:
    """Validate StoryChunk schema enforcement."""

    def test_valid_story_chunk(self):
        data = {
            "text": "Paukwa! Long ago, in the land of Zulu...",
            "culture": "Zulu",
            "is_final": False,
            "cultural_claims": [
                {"claim": "Paukwa is a Swahili story opening", "category": "language"}
            ],
        }
        schema_validator.validate_or_reject("StoryChunk", data)

    def test_valid_minimal(self):
        data = {
            "text": "Once upon a time",
            "culture": "Yoruba",
            "is_final": True,
        }
        schema_validator.validate_or_reject("StoryChunk", data)

    def test_missing_text(self):
        with pytest.raises(SchemaViolationError):
            schema_validator.validate_or_reject(
                "StoryChunk",
                {"culture": "Zulu", "is_final": False},
            )


# -- RiddleRequest Validation --


class TestRiddleRequestSchema:
    """Validate RiddleRequest schema enforcement."""

    def test_valid_minimal(self):
        data = {"culture": "Ashanti"}
        schema_validator.validate_or_reject("RiddleRequest", data)

    def test_valid_full(self):
        data = {"culture": "Yoruba", "difficulty": "hard"}
        schema_validator.validate_or_reject("RiddleRequest", data)

    def test_invalid_difficulty(self):
        with pytest.raises(SchemaViolationError):
            schema_validator.validate_or_reject(
                "RiddleRequest",
                {"culture": "Zulu", "difficulty": "impossible"},
            )


# -- RiddlePayload Validation --


class TestRiddlePayloadSchema:
    """Validate RiddlePayload schema enforcement."""

    def test_valid_riddle(self):
        data = {
            "opening": "Kitendawili!",
            "riddle_text": "What has roots nobody sees?",
            "answer": "A tree",
            "hints": ["It grows tall", "Birds sit on it", "It is very old"],
            "culture": "Ashanti",
            "explanation": "A traditional Ashanti nature riddle",
        }
        schema_validator.validate_or_reject("RiddlePayload", data)

    def test_missing_answer(self):
        with pytest.raises(SchemaViolationError):
            schema_validator.validate_or_reject(
                "RiddlePayload",
                {
                    "opening": "Kitendawili!",
                    "riddle_text": "What has roots nobody sees?",
                    "hints": ["a", "b", "c"],
                    "culture": "Ashanti",
                },
            )


# -- ImageRequest Validation --


class TestImageRequestSchema:
    """Validate ImageRequest schema enforcement."""

    def test_valid_request(self):
        data = {
            "scene_description": "A bustling African marketplace at dawn",
            "culture": "Yoruba",
        }
        schema_validator.validate_or_reject("ImageRequest", data)

    def test_valid_with_all_fields(self):
        data = {
            "scene_description": "Anansi the spider weaving his web",
            "culture": "Ashanti",
            "aspect_ratio": "16:9",
        }
        schema_validator.validate_or_reject("ImageRequest", data)

    def test_missing_scene_description(self):
        with pytest.raises(SchemaViolationError):
            schema_validator.validate_or_reject("ImageRequest", {"culture": "Yoruba"})


# -- ImageResult Validation --


class TestImageResultSchema:
    """Validate ImageResult schema enforcement."""

    def test_success_result(self):
        data = {
            "status": "success",
            "url": "https://storage.googleapis.com/bucket/img.png",
        }
        schema_validator.validate_or_reject("ImageResult", data)

    def test_failure_result(self):
        data = {
            "status": "failed",
            "error": "Imagen API unavailable",
        }
        schema_validator.validate_or_reject("ImageResult", data)

    def test_invalid_status(self):
        with pytest.raises(SchemaViolationError):
            schema_validator.validate_or_reject(
                "ImageResult",
                {"status": "unknown", "url": "http://example.com"},
            )


# -- ValidatedChunk Validation --


class TestValidatedChunkSchema:
    """Validate ValidatedChunk schema enforcement."""

    def test_valid_chunk(self):
        data = {
            "text": "The great Anansi once tricked the sky god",
            "is_final": False,
            "confidence": 0.95,
            "corrections": [],
        }
        schema_validator.validate_or_reject("ValidatedChunk", data)

    def test_with_modifications(self):
        data = {
            "text": "The great Anansi once tricked the sky god (Nyame)",
            "is_final": False,
            "confidence": 0.85,
            "corrections": ["Added deity name for accuracy"],
        }
        schema_validator.validate_or_reject("ValidatedChunk", data)

    def test_missing_confidence(self):
        with pytest.raises(SchemaViolationError):
            schema_validator.validate_or_reject(
                "ValidatedChunk",
                {
                    "text": "Some text",
                    "is_final": True,
                    "corrections": [],
                },
            )


# -- A2A Router Tests --


class TestA2ARouter:
    """Tests for the A2A task router."""

    def test_create_task(self):
        from orchestrator.a2a_router import create_a2a_task

        task = create_a2a_task(
            task_type="StoryRequest",
            payload={"culture": "Yoruba", "theme": "trickster"},
            source_agent="orchestrator",
            target_agent="story_agent",
        )
        assert task.target_agent == "story_agent"
        assert task.state.value == "pending"

    def test_create_task_rejects_invalid_input(self):
        from orchestrator.a2a_router import create_a2a_task

        with pytest.raises(SchemaViolationError):
            create_a2a_task(
                task_type="StoryRequest",
                payload={"theme": "trickster"},  # missing culture
                source_agent="orchestrator",
                target_agent="story_agent",
            )

    def test_agent_cards_available(self):
        from orchestrator.a2a_router import list_agent_cards, get_agent_card

        cards = list_agent_cards()
        assert len(cards) > 0

        story_card = get_agent_card("story_agent")
        assert story_card is not None
        assert "StoryChunk" in story_card["capabilities"]["output_schemas"]

    def test_get_unknown_agent_card(self):
        from orchestrator.a2a_router import get_agent_card

        assert get_agent_card("nonexistent_agent") is None

