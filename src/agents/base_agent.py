"""
Base Agent (ADK-Compatible)
============================
Abstract base class for all HadithiAI sub-agents.
Provides ADK-compatible interface, schema-validated output,
common streaming, logging, and error handling.

Each sub-agent is designed to be usable as an ADK Agent
when the google-adk package is available, with a fallback
to our own base class for environments without ADK.
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Optional

from core.models import AgentRequest, AgentResponse
from core.schemas import schema_validator, SchemaViolationError
from services.gemini_client import GeminiClientPool


class BaseAgent(ABC):
    """
    Base class for all HadithiAI sub-agents.

    Sub-agents are specialized modules that handle specific types
    of requests (stories, riddles, cultural context, etc.).

    ADK Compatibility:
    - Each agent declares its output_schema name
    - Output is validated against JSON schema contracts
    - Agents can be registered as ADK Agent sub-agents
    - Callbacks (before/after) are supported for tracing

    Flow:
    1. Receive an AgentRequest from the dispatcher
    2. Call Gemini (text model, not Live) with a specialized prompt
    3. Stream AgentResponse chunks back
    4. Each chunk is optionally validated against output_schema
    """

    AGENT_NAME: str = "base"
    OUTPUT_SCHEMA: Optional[str] = None  # Schema name from SCHEMA_REGISTRY

    def __init__(self, gemini_pool: GeminiClientPool):
        self.gemini_pool = gemini_pool
        self.logger = logging.getLogger(f"agents.{self.AGENT_NAME}")

    @abstractmethod
    async def generate(self, request: AgentRequest) -> AsyncIterator[AgentResponse]:
        """
        Generate a streaming response for the given request.
        Yields AgentResponse chunks as they become available.
        """
        ...

    async def execute(self, input_data: dict) -> dict:
        """
        ADK-compatible execute: takes a dict, returns a dict.
        Validates output against OUTPUT_SCHEMA if declared.
        Used by A2A router for schema-enforced dispatch.
        """
        raise NotImplementedError(
            f"{self.AGENT_NAME} does not implement execute(). "
            f"Override this method for schema-validated dict I/O."
        )

    async def execute_streaming(self, input_data: dict) -> AsyncIterator[dict]:
        """
        ADK-compatible streaming execute: takes a dict, yields dicts.
        Each yielded dict is validated against OUTPUT_SCHEMA.
        """
        raise NotImplementedError(
            f"{self.AGENT_NAME} does not implement execute_streaming(). "
            f"Override this method for schema-validated streaming dict I/O."
        )

    def _build_prompt(self, request: AgentRequest) -> str:
        """Build the prompt for this agent. Override in subclasses."""
        raise NotImplementedError

    async def _stream_from_gemini(
        self, prompt: str, system_instruction: str
    ) -> AsyncIterator[AgentResponse]:
        """
        Common helper: stream text from Gemini text model.

        Uses Gemini 2.0 Flash (text mode, not Live) for sub-agent calls.
        This is faster than Live for pure text generation.
        """
        try:
            async for text_chunk in self.gemini_pool.generate_text_stream(
                prompt=prompt,
                system_instruction=system_instruction,
            ):
                yield AgentResponse(
                    agent_name=self.AGENT_NAME,
                    content=text_chunk,
                    is_final=False,
                )
        except Exception as e:
            self.logger.error(f"Gemini text generation failed: {e}", exc_info=True)
            yield AgentResponse(
                agent_name=self.AGENT_NAME,
                content="I seem to have lost my train of thought... Let me try again.",
                is_final=True,
            )

        # Final marker
        yield AgentResponse(
            agent_name=self.AGENT_NAME,
            content="",
            is_final=True,
        )

    async def _generate_structured_json(
        self, prompt: str, system_instruction: str
    ) -> str:
        """
        Generate a full (non-streaming) text response from Gemini.
        Used when the agent needs to produce a complete JSON object.
        """
        result_parts = []
        try:
            async for text_chunk in self.gemini_pool.generate_text_stream(
                prompt=prompt,
                system_instruction=system_instruction,
            ):
                result_parts.append(text_chunk)
        except Exception as e:
            self.logger.error(f"Structured generation failed: {e}", exc_info=True)
        return "".join(result_parts)
