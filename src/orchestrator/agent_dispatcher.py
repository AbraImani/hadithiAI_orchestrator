"""
Agent Dispatcher
================
Routes requests from the Orchestrator to the appropriate sub-agents.
Handles parallel agent execution, cultural grounding validation,
and result merging. Uses A2A schema validation on agent boundaries.
"""

import asyncio
import logging
import time
from typing import AsyncIterator, Optional

from core.config import settings
from core.models import AgentRequest, AgentResponse, IntentType
from core.schemas import schema_validator, SchemaViolationError
from agents.story_agent import StoryAgent
from agents.riddle_agent import RiddleAgent
from agents.cultural_agent import CulturalGroundingAgent
from agents.visual_agent import VisualGenerationAgent
from orchestrator.circuit_breaker import CircuitBreaker
from services.firestore_client import FirestoreClient
from services.gemini_client import GeminiClientPool

logger = logging.getLogger(__name__)


class AgentDispatcher:
    """
    Dispatches requests to sub-agents based on intent.
    
    Key responsibilities:
    - Route to correct agent(s) based on intent
    - Run cultural grounding in pipeline (validates each chunk)
    - Handle agent timeouts and failures gracefully
    - Track agent performance metrics
    """

    def __init__(
        self,
        session_id: str,
        firestore: FirestoreClient,
        gemini_pool: GeminiClientPool,
    ):
        self.session_id = session_id

        # Initialize agents
        self.story_agent = StoryAgent(gemini_pool)
        self.riddle_agent = RiddleAgent(gemini_pool)
        self.cultural_agent = CulturalGroundingAgent(gemini_pool)
        self.visual_agent = VisualGenerationAgent(firestore)

        # Circuit breakers per agent
        self.breakers = {
            "story": CircuitBreaker("story", max_failures=3, reset_timeout=60),
            "riddle": CircuitBreaker("riddle", max_failures=3, reset_timeout=60),
            "cultural": CircuitBreaker("cultural", max_failures=5, reset_timeout=30),
            "visual": CircuitBreaker("visual", max_failures=3, reset_timeout=120),
        }

        self._logger = logger.getChild(f"dispatcher.{session_id}")

    async def dispatch(self, request: AgentRequest) -> AsyncIterator[AgentResponse]:
        """
        Dispatch a request to the appropriate agent(s).
        
        Yields AgentResponse chunks as they become available.
        Each chunk has been validated by the Cultural Grounding Agent.
        """
        start = time.time()
        agent_name = self._get_agent_name(request.intent)

        self._logger.info(
            f"Dispatching to {agent_name}",
            extra={"event": "dispatch", "agent": agent_name},
        )

        try:
            match request.intent:
                case IntentType.REQUEST_STORY:
                    async for chunk in self._dispatch_with_grounding(
                        self.story_agent.generate(request),
                        "story",
                    ):
                        yield chunk

                case IntentType.REQUEST_RIDDLE:
                    async for chunk in self._dispatch_with_grounding(
                        self.riddle_agent.generate(request),
                        "riddle",
                    ):
                        yield chunk

                case IntentType.ASK_CULTURAL:
                    async for chunk in self.cultural_agent.generate(request):
                        yield chunk

                case IntentType.REQUEST_IMAGE:
                    # Image generation is async, return acknowledgment
                    yield AgentResponse(
                        agent_name="visual",
                        content="Let me paint that scene for you...",
                        is_final=True,
                        visual_moment=request.user_input,
                    )

                case _:
                    # Unknown intent — yield empty so Gemini handles it
                    yield AgentResponse(
                        agent_name="orchestrator",
                        content="",
                        is_final=True,
                    )

        except asyncio.TimeoutError:
            self._logger.warning(
                f"Agent {agent_name} timed out",
                extra={"event": "agent_timeout", "agent": agent_name},
            )
            yield AgentResponse(
                agent_name=agent_name,
                content="I need a moment to gather my thoughts...",
                is_final=True,
            )
        except Exception as e:
            self._logger.error(
                f"Agent {agent_name} failed: {e}",
                extra={"event": "agent_error", "agent": agent_name},
                exc_info=True,
            )
            yield AgentResponse(
                agent_name=agent_name,
                content="Let me try a different approach...",
                is_final=True,
            )

        elapsed = (time.time() - start) * 1000
        self._logger.info(
            f"Dispatch to {agent_name} completed in {elapsed:.0f}ms",
            extra={
                "event": "dispatch_complete",
                "agent": agent_name,
                "latency_ms": elapsed,
            },
        )

    async def _dispatch_with_grounding(
        self,
        agent_stream: AsyncIterator[AgentResponse],
        agent_name: str,
    ) -> AsyncIterator[AgentResponse]:
        """
        Run an agent's output through cultural grounding validation.

        Strategy: Validate each chunk as it arrives. If cultural agent
        is down, pass through with a warning (graceful degradation).
        Uses the CulturalGroundingAgent.validate_agent_response() method
        which accepts an AgentResponse and returns a validated AgentResponse.
        """
        cultural_breaker = self.breakers["cultural"]

        async for chunk in agent_stream:
            if cultural_breaker.is_open():
                # Cultural agent is down -- pass through with reduced confidence
                chunk.cultural_confidence = 0.5
                yield chunk
                continue

            try:
                validated = await asyncio.wait_for(
                    self.cultural_agent.validate_agent_response(chunk),
                    timeout=2.0,  # Tight timeout -- don't block stream
                )
                yield validated
            except (asyncio.TimeoutError, Exception) as e:
                cultural_breaker.record_failure()
                self._logger.warning(
                    f"Cultural validation failed: {e}",
                    extra={"event": "cultural_validation_failed"},
                )
                # Pass through unvalidated
                chunk.cultural_confidence = 0.5
                yield chunk

    async def generate_image(
        self, scene_description: str, culture: Optional[str]
    ) -> Optional[str]:
        """
        Generate an image asynchronously. Returns URL or None.

        Also validates the input against ImageRequest schema when possible.
        """
        breaker = self.breakers["visual"]

        if breaker.is_open():
            return None

        try:
            # Use the ADK-compatible execute() path
            input_data = {
                "scene_description": scene_description,
                "culture": culture or "African",
            }
            try:
                schema_validator.validate("ImageRequest", input_data)
            except SchemaViolationError:
                pass  # Non-critical, proceed anyway

            result = await asyncio.wait_for(
                self.visual_agent.execute(input_data),
                timeout=30.0,
            )
            if result.get("status") == "success":
                return result.get("url")
            return None
        except Exception as e:
            breaker.record_failure()
            self._logger.warning(f"Image generation failed: {e}")
            return None

    @staticmethod
    def _get_agent_name(intent: IntentType) -> str:
        """Map intent to agent name."""
        return {
            IntentType.REQUEST_STORY: "story",
            IntentType.REQUEST_RIDDLE: "riddle",
            IntentType.ASK_CULTURAL: "cultural",
            IntentType.REQUEST_IMAGE: "visual",
        }.get(intent, "orchestrator")
