"""
JSON Schema Contracts (A2A)
===========================
Strict JSON schema definitions for all Agent-to-Agent communication.
Every agent boundary validates input/output against these schemas.
Schema violations trigger retry with corrective instructions.
"""

import jsonschema
import logging
from typing import Any

logger = logging.getLogger(__name__)


# -- StoryRequest: Orchestrator -> Story Agent --

STORY_REQUEST_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "StoryRequest",
    "type": "object",
    "required": ["culture", "theme"],
    "properties": {
        "culture": {
            "type": "string",
            "description": "African ethnic group or tradition",
        },
        "theme": {
            "type": "string",
            "enum": [
                "trickster", "creation", "wisdom", "courage",
                "love", "origin", "moral",
            ],
        },
        "complexity": {
            "type": "string",
            "enum": ["child", "teen", "adult"],
            "default": "adult",
        },
        "continuation": {
            "type": "boolean",
            "default": False,
        },
        "session_context": {
            "type": "string",
        },
    },
    "additionalProperties": False,
}

# -- StoryChunk: Story Agent output (per chunk) --

STORY_CHUNK_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "StoryChunk",
    "type": "object",
    "required": ["text", "culture"],
    "properties": {
        "text": {
            "type": "string",
            "minLength": 1,
        },
        "culture": {
            "type": "string",
        },
        "cultural_claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": [
                            "proverb", "custom", "character",
                            "location", "language", "historical",
                        ],
                    },
                },
                "required": ["claim", "category"],
            },
        },
        "scene_description": {
            "type": "string",
        },
        "is_final": {
            "type": "boolean",
            "default": False,
        },
    },
    "additionalProperties": False,
}

# -- ValidatedChunk: Cultural Agent output --

VALIDATED_CHUNK_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "ValidatedChunk",
    "type": "object",
    "required": ["text", "confidence"],
    "properties": {
        "text": {
            "type": "string",
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "corrections": {
            "type": "array",
            "items": {"type": "string"},
        },
        "rejected_claims": {
            "type": "array",
            "items": {"type": "string"},
        },
        "is_final": {
            "type": "boolean",
            "default": False,
        },
    },
    "additionalProperties": False,
}

# -- RiddleRequest: Orchestrator -> Riddle Agent --

RIDDLE_REQUEST_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "RiddleRequest",
    "type": "object",
    "required": ["culture"],
    "properties": {
        "culture": {
            "type": "string",
        },
        "difficulty": {
            "type": "string",
            "enum": ["easy", "medium", "hard"],
            "default": "medium",
        },
        "session_context": {
            "type": "string",
        },
    },
    "additionalProperties": False,
}

# -- RiddlePayload: Riddle Agent output --

RIDDLE_PAYLOAD_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "RiddlePayload",
    "type": "object",
    "required": ["opening", "riddle_text", "answer", "culture"],
    "properties": {
        "opening": {
            "type": "string",
        },
        "riddle_text": {
            "type": "string",
        },
        "answer": {
            "type": "string",
        },
        "hints": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 3,
        },
        "explanation": {
            "type": "string",
        },
        "culture": {
            "type": "string",
        },
        "is_traditional": {
            "type": "boolean",
        },
    },
    "additionalProperties": False,
}

# -- ImageRequest: Orchestrator -> Visual Agent --

IMAGE_REQUEST_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "ImageRequest",
    "type": "object",
    "required": ["scene_description", "culture"],
    "properties": {
        "scene_description": {
            "type": "string",
            "minLength": 10,
        },
        "culture": {
            "type": "string",
        },
        "aspect_ratio": {
            "type": "string",
            "enum": ["16:9", "1:1", "9:16"],
            "default": "16:9",
        },
    },
    "additionalProperties": False,
}

# -- ImageResult: Visual Agent output --

IMAGE_RESULT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "ImageResult",
    "type": "object",
    "required": ["status"],
    "properties": {
        "status": {
            "type": "string",
            "enum": ["success", "failed", "skipped"],
        },
        "url": {
            "type": "string",
        },
        "error": {
            "type": "string",
        },
    },
    "additionalProperties": False,
}


# -- Schema Registry --

SCHEMA_REGISTRY: dict[str, dict] = {
    "StoryRequest": STORY_REQUEST_SCHEMA,
    "StoryChunk": STORY_CHUNK_SCHEMA,
    "ValidatedChunk": VALIDATED_CHUNK_SCHEMA,
    "RiddleRequest": RIDDLE_REQUEST_SCHEMA,
    "RiddlePayload": RIDDLE_PAYLOAD_SCHEMA,
    "ImageRequest": IMAGE_REQUEST_SCHEMA,
    "ImageResult": IMAGE_RESULT_SCHEMA,
}


class SchemaViolationError(Exception):
    """Raised when an A2A message fails schema validation."""

    def __init__(self, schema_name: str, errors: list[str]):
        self.schema_name = schema_name
        self.errors = errors
        super().__init__(
            f"Schema '{schema_name}' violation: {'; '.join(errors)}"
        )


class A2ASchemaValidator:
    """
    Validates A2A messages against registered JSON schemas.

    Usage:
        validator = A2ASchemaValidator()
        is_valid, errors = validator.validate("StoryChunk", data)
        validated = validator.validate_or_reject("StoryChunk", data)
    """

    def __init__(self):
        self._schemas = dict(SCHEMA_REGISTRY)
        # Pre-compile validators for performance
        self._validators: dict[str, jsonschema.Draft7Validator] = {}
        for name, schema in self._schemas.items():
            jsonschema.Draft7Validator.check_schema(schema)
            self._validators[name] = jsonschema.Draft7Validator(schema)

    def register(self, name: str, schema: dict):
        """Register a new schema at runtime."""
        jsonschema.Draft7Validator.check_schema(schema)
        self._schemas[name] = schema
        self._validators[name] = jsonschema.Draft7Validator(schema)

    def validate(self, schema_name: str, data: dict) -> tuple[bool, list[str]]:
        """
        Validate data against a named schema.
        Returns (is_valid, list_of_error_messages).
        """
        validator = self._validators.get(schema_name)
        if not validator:
            return False, [f"Unknown schema: {schema_name}"]

        errors = [e.message for e in validator.iter_errors(data)]
        return len(errors) == 0, errors

    def validate_or_reject(self, schema_name: str, data: dict) -> dict:
        """Validate and raise SchemaViolationError on failure."""
        is_valid, errors = self.validate(schema_name, data)
        if not is_valid:
            raise SchemaViolationError(schema_name, errors)
        return data

    def list_schemas(self) -> list[str]:
        """Return names of all registered schemas."""
        return list(self._schemas.keys())


# Module-level singleton
schema_validator = A2ASchemaValidator()
