"""
Riddle Agent (ADK-Compatible)
==============================
Generates and manages interactive African riddles.
Outputs RiddlePayload schema-compliant dicts.
Supports multi-turn riddle sessions with hints and scoring.
"""

import json
import logging
from typing import AsyncIterator

from agents.base_agent import BaseAgent
from core.models import AgentRequest, AgentResponse
from core.schemas import schema_validator

logger = logging.getLogger(__name__)


class RiddleAgent(BaseAgent):
    """
    Generates interactive African cultural riddles.

    ADK Agent Properties:
    - name: riddle_agent
    - model: gemini-2.0-flash
    - output_schema: RiddlePayload
    - tools: riddle_db_lookup

    Features:
    - Authentic riddles from various African traditions
    - Progressive hint system (3 hints per riddle)
    - Cultural explanation after answer is revealed
    - Multi-turn riddle game management
    - Difficulty adaptation
    """

    AGENT_NAME = "riddle"
    OUTPUT_SCHEMA = "RiddlePayload"

    SYSTEM_INSTRUCTION = """You are the Riddle Master of HadithiAI,
specializing in African riddle traditions.

In many African cultures, riddles (called "vitendawili" in Swahili,
"alo" in Yoruba, "izaga" in Zulu) are a beloved form of oral tradition
used to teach wisdom, sharpen wit, and entertain.

Your riddles must:
1. Use the traditional riddle-opening of the specified culture
   - Swahili: "Kitendawili!" (audience responds: "Tega!")
   - Yoruba: "Alo o!" (audience responds: "Alo!")
   - Zulu: "Qagela!" (audience responds: "Qagela!")
2. Be culturally relevant and grounded
3. Include a real or authentically-inspired riddle
4. Have 3 progressive hints (easy, medium, obvious)
5. Include a cultural explanation connecting the riddle to tradition

Format your response EXACTLY as:

[OPENING]
<Traditional riddle opening phrase and call-and-response>

[RIDDLE]
<The riddle text, delivered dramatically>

[HINTS]
Hint 1: <Subtle, thematic hint>
Hint 2: <More direct hint>
Hint 3: <Almost gives it away>

[ANSWER]
<The answer>

[EXPLANATION]
<Cultural context: Why this riddle matters in the tradition,
what it teaches, and how it connects to daily life>

Anti-hallucination rules:
- If using a traditional riddle, name the specific culture
- If creating a new riddle, say "Inspired by {culture} tradition"
- Never attribute a riddle to a culture it does not belong to
- Use authentic traditional openings only if you are certain"""

    STRUCTURED_INSTRUCTION = """You are the Riddle Master of HadithiAI.

Generate a riddle as a JSON object with this exact structure:
{
  "opening": "Traditional riddle opening in the culture's language",
  "riddle_text": "The riddle itself, delivered dramatically",
  "answer": "The answer to the riddle",
  "hints": ["Subtle hint", "More direct hint", "Almost gives it away"],
  "explanation": "Cultural context and significance of this riddle",
  "culture": "the specific culture",
  "is_traditional": true or false
}

RULES:
- "hints" MUST have exactly 3 items
- "is_traditional" is true only for riddles you know are authentic
- For created riddles, set is_traditional to false
- The opening MUST use the real traditional phrase for the culture

Respond ONLY with valid JSON. No markdown, no code blocks."""

    async def generate(self, request: AgentRequest) -> AsyncIterator[AgentResponse]:
        """Generate a riddle with full interactive structure (streaming)."""
        prompt = self._build_prompt(request)

        self.logger.info(
            f"Generating riddle: culture={request.culture}",
            extra={"event": "riddle_start"},
        )

        current_section = ""
        buffer = ""

        async for response in self._stream_from_gemini(prompt, self.SYSTEM_INSTRUCTION):
            if response.is_final:
                if buffer.strip():
                    yield AgentResponse(
                        agent_name=self.AGENT_NAME,
                        content=buffer.strip(),
                        is_final=False,
                        metadata={"section": current_section},
                    )
                yield response
                continue

            buffer += response.content

            for marker in ["[OPENING]", "[RIDDLE]", "[HINTS]", "[ANSWER]", "[EXPLANATION]"]:
                if marker in buffer:
                    parts = buffer.split(marker, 1)
                    if parts[0].strip():
                        yield AgentResponse(
                            agent_name=self.AGENT_NAME,
                            content=parts[0].strip() + "\n\n",
                            is_final=False,
                            metadata={"section": current_section},
                        )
                    current_section = marker.strip("[]").lower()
                    buffer = parts[1] if len(parts) > 1 else ""

            if len(buffer) > 100 and buffer.rstrip().endswith((".", "!", "?")):
                yield AgentResponse(
                    agent_name=self.AGENT_NAME,
                    content=buffer.strip() + " ",
                    is_final=False,
                    metadata={"section": current_section},
                )
                buffer = ""

    async def execute(self, input_data: dict) -> dict:
        """
        ADK-compatible execute: returns a RiddlePayload dict.
        Validates output against RiddlePayload schema.
        """
        culture = input_data.get("culture", "East African")
        difficulty = input_data.get("difficulty", "medium")
        context = input_data.get("session_context", "")
        correction = input_data.get("_correction", "")

        prompt = self._build_structured_prompt(
            culture, difficulty, context, correction
        )

        raw = await self._generate_structured_json(
            prompt, self.STRUCTURED_INSTRUCTION
        )

        payload = self._parse_riddle_payload(raw, culture)

        is_valid, errors = schema_validator.validate("RiddlePayload", payload)
        if not is_valid:
            self.logger.warning(
                f"RiddlePayload validation failed: {errors}",
                extra={"event": "riddle_schema_invalid"},
            )
            # Fix common issues
            payload = self._fix_riddle_payload(payload, culture)

        return payload

    def _build_prompt(self, request: AgentRequest) -> str:
        """Build the riddle generation prompt for streaming."""
        culture = request.culture or "East African"
        difficulty = request.preferences.get("difficulty", "medium")

        context_section = ""
        if request.session_context:
            context_section = f"""
CONVERSATION CONTEXT:
{request.session_context}
If there's an ongoing riddle game, continue it.
Avoid repeating riddles already used in this session."""

        return f"""Generate an interactive African riddle experience.

PARAMETERS:
- Culture/Tradition: {culture}
- Difficulty: {difficulty}
- Language: English with {culture} phrases
{context_section}

Create a riddle now, following the exact format specified in your instructions.
Make it engaging and dramatic, as if presenting to a live audience."""

    def _build_structured_prompt(
        self,
        culture: str,
        difficulty: str,
        context: str,
        correction: str,
    ) -> str:
        """Build prompt for structured JSON output."""
        parts = [
            f"Generate an African riddle as structured JSON.",
            f"Culture: {culture}",
            f"Difficulty: {difficulty}",
        ]
        if context:
            parts.append(f"Context: {context}")
        if correction:
            parts.append(f"CORRECTION: {correction}")
        return "\n".join(parts)

    def _parse_riddle_payload(self, raw_text: str, default_culture: str) -> dict:
        """Parse Gemini output into a RiddlePayload dict."""
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()

        try:
            payload = json.loads(cleaned)
            if isinstance(payload, dict):
                payload.setdefault("culture", default_culture)
                return payload
        except json.JSONDecodeError:
            pass

        # Fallback: construct from raw text
        return {
            "opening": "A riddle for you...",
            "riddle_text": raw_text.strip()[:500] if raw_text.strip() else "What travels without legs?",
            "answer": "A story",
            "hints": [
                "It moves from mouth to ear.",
                "It can cross mountains and rivers.",
                "Everyone carries it differently.",
            ],
            "explanation": f"A riddle inspired by {default_culture} oral tradition.",
            "culture": default_culture,
            "is_traditional": False,
        }

    @staticmethod
    def _fix_riddle_payload(payload: dict, culture: str) -> dict:
        """Fix common schema issues in a riddle payload."""
        fixed = dict(payload)
        fixed.setdefault("opening", "A riddle for you...")
        fixed.setdefault("riddle_text", "What has no beginning and no end?")
        fixed.setdefault("answer", "A circle")
        fixed.setdefault("culture", culture)

        # Ensure exactly 3 hints
        hints = fixed.get("hints", [])
        if not isinstance(hints, list):
            hints = []
        while len(hints) < 3:
            hints.append("Think carefully...")
        fixed["hints"] = hints[:3]

        fixed.setdefault("explanation", f"A riddle from {culture} tradition.")
        fixed.setdefault("is_traditional", False)

        return fixed
