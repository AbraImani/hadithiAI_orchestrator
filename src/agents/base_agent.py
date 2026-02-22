"""
Base Agent
==========
Abstract base class for all HadithiAI sub-agents.
Provides common streaming, logging, and error handling.
"""

import logging
from abc import ABC, abstractmethod
from typing import AsyncIterator

from core.models import AgentRequest, AgentResponse
from services.gemini_client import GeminiClientPool


class BaseAgent(ABC):
    """
    Base class for all sub-agents.
    
    Sub-agents are specialized modules that handle specific types
    of requests (stories, riddles, cultural context, etc.).
    
    They:
    1. Receive an AgentRequest from the dispatcher
    2. Call Gemini (text model, not Live) with a specialized prompt
    3. Stream AgentResponse chunks back
    """

    AGENT_NAME: str = "base"

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
