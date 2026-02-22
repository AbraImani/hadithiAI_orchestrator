"""
Cultural Grounding Agent (ADK-Compatible)
==========================================
Validates and enriches all AI outputs for cultural authenticity.
This agent sits in the HOT PATH -- every response chunk passes through it.
Designed for minimal latency while catching cultural inaccuracies.

Input schema: StoryChunk (text, culture, cultural_claims[])
Output schema: ValidatedChunk (text, confidence, corrections[], rejected_claims[])
"""

import json
import logging
import random
from typing import AsyncIterator, Optional

from agents.base_agent import BaseAgent
from core.config import settings
from core.models import AgentRequest, AgentResponse
from core.schemas import schema_validator
from services.gemini_client import GeminiClientPool

logger = logging.getLogger(__name__)


# -- Pre-curated Cultural Knowledge Base --
# Loaded into memory for instant validation without AI calls.
# For production, this would be in Firestore with much more data.

CULTURAL_KNOWLEDGE = {
    "story_openings": {
        "swahili": {
            "text": "Hadithi, hadithi!",
            "response": "Hadithi njoo, uwongo njoo, utamu kolea.",
            "translation": "Story, story! Story come, fiction come, let sweetness increase.",
            "verified": True,
        },
        "yoruba": {
            "text": "Alo o!",
            "response": "Alo!",
            "translation": "The traditional Yoruba story opening.",
            "verified": True,
        },
        "zulu": {
            "text": "Kwesukesukela...",
            "response": "",
            "translation": "Once upon a time...",
            "verified": True,
        },
        "kikuyu": {
            "text": "Ruciini rumwe...",
            "response": "",
            "translation": "One day...",
            "verified": True,
        },
        "ashanti": {
            "text": "We do not really mean, we do not really mean, "
                    "that what we are about to say is true...",
            "response": "",
            "translation": "The Ashanti/Akan story disclaimer.",
            "verified": True,
        },
        "igbo": {
            "text": "Nwanne m, gather close...",
            "response": "",
            "translation": "My sibling, gather close...",
            "verified": True,
        },
        "maasai": {
            "text": "In the time before memory, when the earth was still young...",
            "response": "",
            "translation": "",
            "verified": True,
        },
        "wolof": {
            "text": "Lebbu am na...",
            "response": "",
            "translation": "There was a story...",
            "verified": True,
        },
        "hausa": {
            "text": "Ga ta nan, ga ta nanku...",
            "response": "",
            "translation": "Here it is, here it is for you...",
            "verified": True,
        },
    },
    "story_closings": {
        "swahili": {
            "text": "Hadithi yangu imeisha, kama nzuri kama mbaya.",
            "translation": "My story is done, whether good or bad.",
            "verified": True,
        },
        "yoruba": {
            "text": "Itan mi dopin.",
            "translation": "My story ends.",
            "verified": True,
        },
        "zulu": {
            "text": "Cosu cosu iyaphela.",
            "translation": "And so the story ends.",
            "verified": True,
        },
        "ashanti": {
            "text": "This is my story which I have related. If it be sweet, "
                    "or if it be not sweet, take some elsewhere, "
                    "and let some come back to me.",
            "translation": "",
            "verified": True,
        },
    },
    "trickster_figures": {
        "ashanti": {"name": "Anansi", "type": "Spider", "verified": True},
        "yoruba": {"name": "Ijapa", "type": "Tortoise", "verified": True},
        "zulu": {"name": "uNogwaja", "type": "Hare", "verified": True},
        "kikuyu": {"name": "Hare", "type": "Hare", "verified": True},
        "hausa": {"name": "Gizo", "type": "Spider", "verified": True},
    },
    "proverbs": {
        "swahili": [
            {
                "text": "Haraka haraka haina baraka.",
                "translation": "Hurry hurry has no blessing.",
                "verified": True,
            },
            {
                "text": "Mti hauendi ila kwa nyenzo.",
                "translation": "A tree does not move without wind.",
                "verified": True,
            },
            {
                "text": "Asiyefunzwa na mamaye hufunzwa na ulimwengu.",
                "translation": "He who is not taught by his mother will be taught by the world.",
                "verified": True,
            },
        ],
        "yoruba": [
            {
                "text": "Agba kii wa loja, ki ori omo titun wo.",
                "translation": "An elder does not stay in the market and let a child's head go awry.",
                "verified": True,
            },
        ],
        "zulu": [
            {
                "text": "Umuntu ngumuntu ngabantu.",
                "translation": "A person is a person through people.",
                "verified": True,
            },
            {
                "text": "Indlela ibuzwa kwabaphambili.",
                "translation": "The way is asked from those who have gone before.",
                "verified": True,
            },
        ],
        "ashanti": [
            {
                "text": "Obi nkyere abofra Nyame.",
                "translation": "Nobody teaches a child about God.",
                "verified": True,
            },
            {
                "text": "Se wo were fi na wosankofa a, yenkyi.",
                "translation": "It is not wrong to go back for what you forgot.",
                "verified": True,
            },
        ],
    },
}


class CulturalGroundingAgent(BaseAgent):
    """
    Validates and enriches content for cultural authenticity.

    ADK Agent Properties:
    - name: cultural_grounding
    - model: gemini-2.0-flash
    - output_schema: ValidatedChunk

    Two modes:
    1. validate_chunk(dict): Lightweight inline validation (hot path)
       - Input: StoryChunk schema dict
       - Output: ValidatedChunk schema dict
       - Uses pattern matching + knowledge base (no AI call for most)
       - Falls back to AI for uncertain cases
       - Target: <50ms per chunk for 90% of chunks

    2. generate(): Full cultural context generation (cold path)
       - Used when user explicitly asks about culture
       - Full Gemini call with rich cultural prompting
    """

    AGENT_NAME = "cultural"
    OUTPUT_SCHEMA = "ValidatedChunk"

    SYSTEM_INSTRUCTION = """You are the Cultural Grounding Agent of HadithiAI.
Your role is to validate and enrich content for cultural authenticity.

You have deep knowledge of African oral traditions, proverbs, customs,
and storytelling practices across the continent.

When VALIDATING content:
- Check if cultural references are accurate
- Verify proverb attributions
- Ensure character names fit the stated culture
- Confirm geographical accuracy
- Check that cultural practices are described correctly
- Assess overall tone for respect and authenticity

When GENERATING cultural context:
- Provide rich, specific information
- Always name the specific ethnic group, not just the country
- Include local language terms with pronunciation
- Connect to broader cultural themes
- Be honest about what you are uncertain about

CRITICAL RULES:
- When in doubt, FLAG it -- never let uncertain claims through
- Prefer removing a claim over letting a wrong one through
- Never conflate different African cultures
- Always distinguish between specific ethnic traditions"""

    def __init__(self, gemini_pool: GeminiClientPool):
        super().__init__(gemini_pool)
        self.knowledge = CULTURAL_KNOWLEDGE

    async def validate_chunk(self, chunk: dict) -> dict:
        """
        Validate a StoryChunk dict against cultural knowledge.
        Returns a ValidatedChunk dict.

        Performance budget: <50ms for 90% of chunks.

        Args:
            chunk: A dict matching StoryChunk schema
                   {text, culture, cultural_claims[], ...}

        Returns:
            A dict matching ValidatedChunk schema
            {text, confidence, corrections[], rejected_claims[], is_final}
        """
        text = chunk.get("text", "")
        culture = chunk.get("culture", "")
        claims = chunk.get("cultural_claims", [])
        confidence = 1.0
        corrections = []
        rejected = []

        # -- Level 1: Knowledge Base Checks (instant, <1ms) --
        for claim_obj in claims:
            claim_text = claim_obj.get("claim", "") if isinstance(claim_obj, dict) else str(claim_obj)
            category = claim_obj.get("category", "custom") if isinstance(claim_obj, dict) else "custom"

            kb_result = self._check_knowledge_base(claim_text, culture, category)
            if kb_result == "confirmed":
                pass  # Confidence stays high
            elif kb_result == "contradicted":
                confidence *= 0.3
                rejected.append(claim_text)
            elif kb_result == "unknown":
                confidence *= 0.85

        # -- Level 2: Pattern Heuristics (<5ms) --
        if self._has_overgeneralization(text):
            confidence *= 0.6
            corrections.append("Overly broad cultural claim detected")

        if self._has_culture_mixing(text, culture):
            confidence *= 0.7
            corrections.append("Possible culture mixing detected")

        # -- Level 3: AI Validation (only if confidence < threshold, ~50ms) --
        if confidence < settings.CULTURAL_CONFIDENCE_THRESHOLD:
            try:
                ai_result = await self._quick_ai_validate(text, culture)
                confidence = min(confidence, ai_result.get("confidence", 0.5))
                if ai_result.get("corrected_text"):
                    text = ai_result["corrected_text"]
                    corrections.extend(ai_result.get("corrections", []))
            except Exception:
                # AI validation failed -- use pattern-based confidence
                pass

        # -- Level 4: Apply Policy --
        if confidence < settings.CULTURAL_REJECT_THRESHOLD:
            text = self._add_hedging(text)

        return {
            "text": text,
            "confidence": confidence,
            "corrections": corrections,
            "rejected_claims": rejected,
            "is_final": chunk.get("is_final", False),
        }

    async def validate_agent_response(self, chunk: AgentResponse) -> AgentResponse:
        """
        Legacy interface: validate an AgentResponse chunk.
        Wraps validate_chunk() for backward compatibility with AgentDispatcher.
        """
        story_chunk = {
            "text": chunk.content,
            "culture": chunk.metadata.get("culture", ""),
            "cultural_claims": chunk.metadata.get("cultural_claims", []),
        }

        validated = await self.validate_chunk(story_chunk)

        chunk.content = validated["text"]
        chunk.cultural_confidence = validated["confidence"]
        chunk.metadata["corrections"] = validated.get("corrections", [])
        chunk.metadata["rejected_claims"] = validated.get("rejected_claims", [])

        return chunk

    async def execute(self, input_data: dict) -> dict:
        """ADK-compatible execute: validates a StoryChunk, returns ValidatedChunk."""
        return await self.validate_chunk(input_data)

    async def generate(self, request: AgentRequest) -> AsyncIterator[AgentResponse]:
        """Generate detailed cultural context (when user asks about culture)."""
        prompt = self._build_prompt(request)

        async for response in self._stream_from_gemini(prompt, self.SYSTEM_INSTRUCTION):
            yield response

    def _build_prompt(self, request: AgentRequest) -> str:
        """Build cultural context prompt."""
        topic = request.user_input
        culture = request.culture or "African"

        return f"""Provide rich cultural context about: {topic}

Culture/Region: {culture}

Include:
- Historical background
- Connection to oral traditions
- Local language terms with pronunciation
- How this connects to daily life and values
- Related proverbs or sayings

Be specific to the ethnic group, not just the country or continent.
If you are unsure about details, say so honestly."""

    def _check_knowledge_base(
        self, claim: str, culture: str, category: str
    ) -> str:
        """
        Check a claim against the knowledge base.
        Returns: "confirmed", "contradicted", or "unknown".
        """
        claim_lower = claim.lower()
        culture_lower = culture.lower()

        # Check trickster figures
        if category == "character":
            for kb_culture, figure in self.knowledge["trickster_figures"].items():
                figure_name = figure["name"].lower()
                if figure_name in claim_lower:
                    if kb_culture == culture_lower:
                        return "confirmed"
                    else:
                        # Figure exists but possibly wrong culture
                        if culture_lower not in claim_lower:
                            return "contradicted"

        # Check proverbs
        if category == "proverb":
            for kb_culture, proverbs in self.knowledge["proverbs"].items():
                for proverb in proverbs:
                    proverb_text = proverb["text"].lower()
                    if proverb_text[:20] in claim_lower or claim_lower[:20] in proverb_text:
                        if kb_culture == culture_lower:
                            return "confirmed"
                        else:
                            return "contradicted"

        # Check story openings
        if category in ("language", "custom"):
            openings = self.knowledge["story_openings"]
            if culture_lower in openings:
                opening = openings[culture_lower]
                if opening["text"].lower()[:15] in claim_lower:
                    return "confirmed"

        return "unknown"

    @staticmethod
    def _has_overgeneralization(text: str) -> bool:
        """Detect overly broad cultural claims."""
        markers = [
            "all africans", "every african", "africans always",
            "in africa they always", "african culture is",
            "all of africa", "the african way",
        ]
        text_lower = text.lower()
        return any(marker in text_lower for marker in markers)

    @staticmethod
    def _has_culture_mixing(text: str, target_culture: str) -> bool:
        """Detect if multiple cultures are mixed inappropriately."""
        cultures = [
            "yoruba", "zulu", "kikuyu", "ashanti", "maasai",
            "igbo", "hausa", "wolof", "swahili",
        ]
        text_lower = text.lower()
        target_lower = target_culture.lower()

        mentioned = [c for c in cultures if c in text_lower and c != target_lower]
        # More than 1 other culture mentioned in a single chunk is suspicious
        return len(mentioned) > 1

    async def _quick_ai_validate(self, text: str, culture: str) -> dict:
        """Quick AI validation for uncertain content (~50ms target)."""
        prompt = f"""Quickly validate the cultural accuracy of this text:

"{text}"

Culture context: {culture}

Respond in JSON format:
{{"confidence": 0.0-1.0, "corrections": ["list of issues"], "corrected_text": null or "corrected version"}}

Only flag serious cultural inaccuracies, not style preferences."""

        result_text = ""
        async for chunk in self.gemini_pool.generate_text_stream(
            prompt=prompt,
            system_instruction="You are a cultural accuracy validator. Respond only in JSON.",
        ):
            result_text += chunk

        try:
            return json.loads(result_text)
        except (json.JSONDecodeError, Exception):
            return {"confidence": 0.7, "corrections": [], "corrected_text": None}

    @staticmethod
    def _add_hedging(text: str) -> str:
        """Add hedging language to uncertain cultural claims."""
        hedging_phrases = [
            "In some traditions, ",
            "It is often said that ",
            "According to some accounts, ",
        ]
        return random.choice(hedging_phrases) + text[0].lower() + text[1:]
