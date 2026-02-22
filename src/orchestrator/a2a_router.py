"""
A2A Task Router
===============
Routes Agent-to-Agent tasks with strict JSON schema enforcement.
Every dispatch validates input/output against registered schemas.
Schema violations trigger retry with corrective instructions.
"""

import asyncio
import logging
import time
import uuid
from typing import Any, AsyncIterator, Optional

from core.models import A2ATask, A2ATaskState, IntentType
from core.schemas import (
    SchemaViolationError,
    schema_validator,
)

logger = logging.getLogger(__name__)


# -- Agent Card definitions (capabilities each agent advertises) --

AGENT_CARDS: dict[str, dict] = {
    "story_agent": {
        "name": "story_agent",
        "description": "Generates culturally-rooted African oral tradition stories",
        "version": "1.0.0",
        "capabilities": {
            "input_schemas": ["StoryRequest"],
            "output_schemas": ["StoryChunk"],
            "streaming": True,
            "max_latency_ms": 500,
        },
    },
    "riddle_agent": {
        "name": "riddle_agent",
        "description": "Generates interactive African riddles with hints and scoring",
        "version": "1.0.0",
        "capabilities": {
            "input_schemas": ["RiddleRequest"],
            "output_schemas": ["RiddlePayload"],
            "streaming": False,
            "max_latency_ms": 500,
        },
    },
    "cultural_grounding": {
        "name": "cultural_grounding",
        "description": "Validates cultural claims and enriches content",
        "version": "1.0.0",
        "capabilities": {
            "input_schemas": ["StoryChunk"],
            "output_schemas": ["ValidatedChunk"],
            "streaming": True,
            "max_latency_ms": 50,
        },
    },
    "visual_agent": {
        "name": "visual_agent",
        "description": "Generates culturally appropriate scene illustrations",
        "version": "1.0.0",
        "capabilities": {
            "input_schemas": ["ImageRequest"],
            "output_schemas": ["ImageResult"],
            "streaming": False,
            "max_latency_ms": 15000,
        },
    },
    "memory_agent": {
        "name": "memory_agent",
        "description": "Persists conversation turns and manages session context",
        "version": "1.0.0",
        "capabilities": {
            "input_schemas": [],
            "output_schemas": [],
            "streaming": False,
            "max_latency_ms": 200,
        },
    },
}


def create_a2a_task(
    task_type: str,
    payload: dict,
    source_agent: str,
    target_agent: str,
) -> A2ATask:
    """
    Create a new A2A task with schema validation on the payload.
    Raises SchemaViolationError if payload does not match the schema.
    """
    schema_validator.validate_or_reject(task_type, payload)

    return A2ATask(
        task_id=f"task_{uuid.uuid4().hex[:12]}",
        task_type=task_type,
        payload=payload,
        source_agent=source_agent,
        target_agent=target_agent,
        state=A2ATaskState.PENDING,
    )


async def dispatch_with_schema_enforcement(
    agent_fn,
    input_data: dict,
    input_schema: str,
    output_schema: str,
    agent_name: str = "unknown",
    max_retries: int = 2,
) -> dict:
    """
    Dispatch to an agent function with schema enforcement and retry.

    1. Validates input against input_schema
    2. Calls agent_fn(input_data)
    3. Validates output against output_schema
    4. On schema violation, retries with corrective instruction
    5. After max_retries, returns a safe fallback

    Args:
        agent_fn: Async callable that takes a dict and returns a dict.
        input_data: The payload to send to the agent.
        input_schema: Schema name for input validation.
        output_schema: Schema name for output validation.
        agent_name: Name for logging.
        max_retries: Number of retry attempts on schema violation.

    Returns:
        Schema-validated output dict.
    """
    start = time.time()

    # Validate input
    schema_validator.validate_or_reject(input_schema, input_data)

    for attempt in range(max_retries + 1):
        try:
            result = await agent_fn(input_data)

            is_valid, errors = schema_validator.validate(output_schema, result)
            if is_valid:
                elapsed = (time.time() - start) * 1000
                logger.info(
                    f"A2A dispatch to {agent_name} succeeded in {elapsed:.0f}ms",
                    extra={
                        "event": "a2a_dispatch_success",
                        "agent": agent_name,
                        "latency_ms": elapsed,
                        "attempt": attempt + 1,
                    },
                )
                return result

            # Schema violation -- retry with correction
            if attempt < max_retries:
                logger.warning(
                    f"Schema violation from {agent_name}, retrying "
                    f"(attempt {attempt + 1}/{max_retries}): {errors}",
                    extra={
                        "event": "a2a_schema_violation",
                        "agent": agent_name,
                        "errors": errors,
                        "attempt": attempt + 1,
                    },
                )
                input_data = dict(input_data)
                input_data["_correction"] = (
                    f"Your previous output had schema errors: {errors}. "
                    f"Fix them and respond again with valid JSON."
                )
            else:
                logger.error(
                    f"Agent {agent_name} failed schema after "
                    f"{max_retries} retries: {errors}",
                    extra={
                        "event": "a2a_schema_failure",
                        "agent": agent_name,
                        "errors": errors,
                    },
                )
                return _generate_safe_fallback(output_schema)

        except SchemaViolationError:
            raise
        except Exception as e:
            logger.error(
                f"Agent {agent_name} execution error: {e}",
                extra={
                    "event": "a2a_agent_error",
                    "agent": agent_name,
                    "attempt": attempt + 1,
                },
                exc_info=True,
            )
            if attempt >= max_retries:
                return _generate_safe_fallback(output_schema)

    return _generate_safe_fallback(output_schema)


async def dispatch_streaming_with_schema(
    agent_stream_fn,
    input_data: dict,
    input_schema: str,
    output_schema: str,
    agent_name: str = "unknown",
) -> AsyncIterator[dict]:
    """
    Dispatch to a streaming agent with per-chunk schema validation.

    Each yielded chunk is validated against output_schema.
    Invalid chunks are logged and skipped (not retried per-chunk
    because we cannot re-stream partial results).
    """
    schema_validator.validate_or_reject(input_schema, input_data)

    chunk_count = 0
    violation_count = 0
    start = time.time()

    async for chunk in agent_stream_fn(input_data):
        chunk_count += 1
        is_valid, errors = schema_validator.validate(output_schema, chunk)

        if is_valid:
            yield chunk
        else:
            violation_count += 1
            logger.warning(
                f"Streaming chunk {chunk_count} from {agent_name} "
                f"failed schema: {errors}",
                extra={
                    "event": "a2a_stream_violation",
                    "agent": agent_name,
                    "chunk": chunk_count,
                    "errors": errors,
                },
            )
            # Attempt to fix by providing minimum required fields
            patched = _attempt_chunk_fix(chunk, output_schema)
            if patched:
                yield patched

    elapsed = (time.time() - start) * 1000
    logger.info(
        f"Streaming dispatch to {agent_name} completed: "
        f"{chunk_count} chunks, {violation_count} violations, "
        f"{elapsed:.0f}ms",
        extra={
            "event": "a2a_stream_complete",
            "agent": agent_name,
            "chunk_count": chunk_count,
            "violation_count": violation_count,
            "latency_ms": elapsed,
        },
    )


def _generate_safe_fallback(schema_name: str) -> dict:
    """Generate a minimal valid response for the given schema."""
    fallbacks = {
        "StoryChunk": {
            "text": "In some traditions, the story continues in ways "
                    "that words alone cannot capture...",
            "culture": "african",
            "cultural_claims": [],
            "is_final": True,
        },
        "ValidatedChunk": {
            "text": "Let me continue with what I know to be true...",
            "confidence": 0.5,
            "corrections": ["Fallback response due to validation failure"],
            "rejected_claims": [],
            "is_final": True,
        },
        "RiddlePayload": {
            "opening": "A riddle for you...",
            "riddle_text": "What has roots that nobody sees, "
                          "is taller than trees, yet never grows?",
            "answer": "A mountain",
            "hints": [
                "It stands very still.",
                "It touches the sky.",
                "You can climb it.",
            ],
            "explanation": "A classic riddle found in many oral traditions.",
            "culture": "african",
            "is_traditional": False,
        },
        "ImageResult": {
            "status": "skipped",
            "error": "Image generation unavailable",
        },
    }
    result = fallbacks.get(schema_name)
    if result:
        return result

    # Generic fallback
    return {"error": f"No fallback for schema {schema_name}"}


def _attempt_chunk_fix(chunk: dict, schema_name: str) -> Optional[dict]:
    """Try to fix a malformed chunk by adding missing required fields."""
    if not isinstance(chunk, dict):
        return None

    if schema_name == "StoryChunk":
        fixed = dict(chunk)
        if "text" not in fixed:
            return None
        if "culture" not in fixed:
            fixed["culture"] = "african"
        return fixed

    if schema_name == "ValidatedChunk":
        fixed = dict(chunk)
        if "text" not in fixed:
            return None
        if "confidence" not in fixed:
            fixed["confidence"] = 0.5
        return fixed

    return None


def get_agent_card(agent_name: str) -> Optional[dict]:
    """Return the Agent Card for a given agent."""
    return AGENT_CARDS.get(agent_name)


def list_agent_cards() -> list[dict]:
    """Return all registered Agent Cards."""
    return list(AGENT_CARDS.values())
