"""
Cultural Grounding Agent
========================
Validates and enriches all AI outputs for cultural authenticity.
This agent sits in the HOT PATH — every response chunk passes through it.
Designed for minimal latency while catching cultural inaccuracies.
"""

import logging
from typing import AsyncIterator, Optional

from agents.base_agent import BaseAgent
from core.config import settings
from core.models import AgentRequest, AgentResponse
from services.gemini_client import GeminiClientPool

logger = logging.getLogger(__name__)


# ─── Pre-curated Cultural Knowledge Base ─────────────────────────────
# This is loaded into memory for instant validation without AI calls.
# For a production system, this would be in Firestore with much more data.

CULTURAL_KNOWLEDGE = {
    "story_openings": {
        "swahili": "Hadithi, hadithi! Hadithi njoo, uwongo njoo, utamu kolea. (Story, story! Story come, fiction come, let sweetness increase.)",
        "yoruba": "Àlọ́ o! (Response: Àlọ́!) — The traditional Yoruba story opening",
        "zulu": "Kwesukesukela... (Once upon a time...) — The Zulu story opening",
        "kikuyu": "Rũciĩ rũmwe... (One day...) — The Kikuyu story opening",
        "ashanti": "We do not really mean, we do not really mean, that what we are about to say is true...",
        "igbo": "Once upon a time... Nwanne m (my sibling), gather close...",
        "maasai": "In the time before memory, when the earth was still young...",
        "wolof": "Lëbbu am na... (There was a story...) — The Wolof opening",
        "hausa": "Ga ta nan, ga ta nanku... (Here it is, here it is for you...)",
    },
    "story_closings": {
        "swahili": "Hadithi yangu imeisha, kama nzuri kama mbaya. (My story is done, whether good or bad.)",
        "yoruba": "Ìtàn mi dópin. Àdúrà mi ní kí a jẹ́ aṣẹ́. (My story ends. My prayer is that we prosper.)",
        "zulu": "Cosu cosu iyaphela. (And so the story ends.)",
        "ashanti": "This is my story which I have related. If it be sweet, or if it be not sweet, take some elsewhere, and let some come back to me.",
    },
    "trickster_figures": {
        "yoruba": "Anansi (originally Ashanti, but widespread) / Tortoise (Ìjàpá)",
        "ashanti": "Anansi the Spider — the original trickster figure",
        "zulu": "uNogwaja (Hare) — the clever trickster",
        "kikuyu": "Hare (Njoki) — known for wit and cunning",
        "hausa": "Gizo (Spider) — the Hausa trickster",
    },
    "proverbs": {
        "swahili": [
            "Haraka haraka haina baraka. (Hurry hurry has no blessing.)",
            "Mti hauendi ila kwa nyenzo. (A tree doesn't move without wind.)",
            "Asiyefunzwa na mamaye hufunzwa na ulimwengu. (He who is not taught by his mother will be taught by the world.)",
        ],
        "yoruba": [
            "Àgbà kì í wà lọ́jà, kí orí ọmọ títún wọ́. (An elder does not stay in the market and let a child's head go awry.)",
            "Bí a bá ń lọ ọ̀nà jìn, a kì í fi ìdí hàn ìlú. (When going on a long journey, don't show your backside to the town.)",
        ],
        "zulu": [
            "Umuntu ngumuntu ngabantu. (A person is a person through people.)",
            "Indlela ibuzwa kwabaphambili. (The way is asked from those who have gone before.)",
        ],
        "ashanti": [
            "Obi nkyerɛ abɔfra Nyame. (Nobody teaches a child about God.)",
            "Sɛ wo werɛ fi na wosankofa a, yenkyi. (It is not wrong to go back for what you forgot.)",
        ],
    },
}


class CulturalGroundingAgent(BaseAgent):
    """
    Validates and enriches content for cultural authenticity.
    
    Two modes:
    1. validate_chunk(): Lightweight inline validation (hot path)
       - Uses pattern matching + knowledge base (no AI call)
       - Falls back to AI for uncertain cases
       - Target: < 50ms per chunk
    
    2. generate(): Full cultural context generation (cold path)
       - Used when user explicitly asks about culture
       - Full Gemini call with rich cultural prompting
    """

    AGENT_NAME = "cultural"

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
- Be honest about what you're uncertain about

CRITICAL RULES:
- When in doubt, FLAG it — never let uncertain claims through
- Prefer removing a claim over letting a wrong one through
- Never conflate different African cultures
- Always distinguish between specific ethnic traditions"""

    def __init__(self, gemini_pool: GeminiClientPool):
        super().__init__(gemini_pool)
        self.knowledge = CULTURAL_KNOWLEDGE

    async def validate_chunk(self, chunk: AgentResponse) -> AgentResponse:
        """
        Lightweight cultural validation for a single chunk.
        
        Strategy:
        1. Check against local knowledge base (instant)
        2. Apply basic pattern checks (instant)
        3. For uncertain content, do a quick AI validation (50-100ms)
        4. Return validated chunk with confidence score
        """
        text = chunk.content
        confidence = 1.0
        corrections = []

        # ── Level 1: Knowledge Base Checks (instant) ──

        # Check for misattributed proverbs
        for culture, proverbs in self.knowledge["proverbs"].items():
            for proverb in proverbs:
                proverb_text = proverb.split("(")[0].strip()
                if proverb_text.lower() in text.lower():
                    # Proverb found — verify it's attributed correctly
                    if culture not in text.lower():
                        # Might be misattributed
                        confidence *= 0.8
                        self.logger.warning(
                            f"Proverb '{proverb_text}' may be misattributed"
                        )

        # Check for trickster figure / culture consistency
        for culture, figure in self.knowledge["trickster_figures"].items():
            figure_name = figure.split("—")[0].split("(")[0].strip().lower()
            if figure_name in text.lower() and culture not in text.lower():
                confidence *= 0.85

        # ── Level 2: Pattern Checks (instant) ──

        # Flag if mixing multiple cultures in one sentence
        cultures_mentioned = []
        for culture in ["yoruba", "zulu", "kikuyu", "ashanti", "maasai", "igbo", "hausa"]:
            if culture in text.lower():
                cultures_mentioned.append(culture)
        if len(cultures_mentioned) > 2:
            confidence *= 0.7
            corrections.append("Multiple cultures mentioned — verify intentional")

        # Flag if using absolute claims about cultural practices
        absolute_markers = [
            "all africans", "every african", "africans always",
            "in africa they always", "african culture is",
        ]
        for marker in absolute_markers:
            if marker in text.lower():
                confidence *= 0.6
                corrections.append(f"Overly broad cultural claim: '{marker}'")

        # ── Level 3: AI Validation (only if confidence is low) ──
        if confidence < settings.CULTURAL_CONFIDENCE_THRESHOLD:
            # Quick AI check
            try:
                ai_result = await self._quick_ai_validate(text)
                confidence = min(confidence, ai_result.get("confidence", 0.5))
                if ai_result.get("corrected_text"):
                    text = ai_result["corrected_text"]
            except Exception:
                # AI validation failed — use pattern-based confidence
                pass

        # ── Apply Results ──
        if confidence < settings.CULTURAL_REJECT_THRESHOLD:
            # Too risky — hedge the content
            text = self._add_hedging(text)

        chunk.content = text
        chunk.cultural_confidence = confidence
        chunk.metadata["corrections"] = corrections

        return chunk

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
If you're unsure about details, say so honestly."""

    async def _quick_ai_validate(self, text: str) -> dict:
        """Quick AI validation for uncertain content."""
        prompt = f"""Quickly validate the cultural accuracy of this text:

"{text}"

Respond in JSON format:
{{"confidence": 0.0-1.0, "issues": ["list of issues"], "corrected_text": null or "corrected version"}}

Only flag serious cultural inaccuracies, not style preferences."""

        result_text = ""
        async for chunk in self.gemini_pool.generate_text_stream(
            prompt=prompt,
            system_instruction="You are a cultural accuracy validator. Respond only in JSON.",
        ):
            result_text += chunk

        try:
            import json
            return json.loads(result_text)
        except (json.JSONDecodeError, Exception):
            return {"confidence": 0.7, "issues": [], "corrected_text": None}

    @staticmethod
    def _add_hedging(text: str) -> str:
        """Add hedging language to uncertain cultural claims."""
        # Simple hedging — prepend qualifier
        hedging_phrases = [
            "In some traditions, ",
            "It is often said that ",
            "According to some accounts, ",
        ]
        import random
        return random.choice(hedging_phrases) + text[0].lower() + text[1:]
