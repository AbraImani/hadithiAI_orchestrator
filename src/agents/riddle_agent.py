"""
Riddle Agent (ADK-Compatible)
==============================
Generates interactive African riddles with 4 answer choices.
Output matches the Flutter RiddleModel:
  {id, question, choices: [{text: bool}], tip, help, language}
"""

import json
import logging
import uuid
from typing import AsyncIterator

from agents.base_agent import BaseAgent
from core.models import AgentRequest, AgentResponse
from core.schemas import schema_validator

logger = logging.getLogger(__name__)


class RiddleAgent(BaseAgent):
    """
    Generates interactive African cultural riddles with 4 choices.

    Output matches Flutter RiddleModel exactly:
    - id: unique riddle identifier
    - question: the riddle text
    - choices: 4 answer options [{answer_text: true/false}]
    - tip: a subtle hint
    - help: a more detailed clue
    - language: culture/language code
    """

    AGENT_NAME = "riddle"
    OUTPUT_SCHEMA = "RiddlePayload"

    SYSTEM_INSTRUCTION = """You are a master Riddle Master of African oral traditions.

When asked for a riddle, present it dramatically as a Griot would:
- Start with the traditional riddle opening of the culture
- Present the riddle question dramatically
- Build suspense and engagement

CRITICAL: Output ONLY the riddle narration. Never output JSON, structural
markers, or planning text. Everything you say will be spoken aloud.

Include the traditional call-and-response opening:
- Swahili: "Kitendawili!" (audience responds: "Tega!")
- Yoruba: "Alo o!" (audience responds: "Alo!")
- Zulu: "Qagela!" (audience responds: "Qagela!")

After presenting the riddle, tell the listener they have 4 choices
and encourage them to think carefully."""

    STRUCTURED_INSTRUCTION = """You are the Riddle Master of HadithiAI.

Generate a riddle as a JSON object with this EXACT structure:
{
  "id": "unique_riddle_id",
  "question": "The riddle question, culturally authentic",
  "choices": [
    {"correct answer text": true},
    {"wrong answer 1": false},
    {"wrong answer 2": false},
    {"wrong answer 3": false}
  ],
  "tip": "A subtle hint that nudges toward the answer",
  "help": "A more detailed clue that makes the answer clearer",
  "language": "culture name (e.g. Swahili, Yoruba, Zulu)",
  "culture": "the specific African culture",
  "explanation": "Cultural context: why this riddle matters in the tradition",
  "is_traditional": true or false
}

STRICT RULES:
- "choices" MUST have EXACTLY 4 items
- EXACTLY 1 choice must be true, the other 3 must be false
- Each choice is an object with ONE key (the answer text) and ONE boolean value
- "tip" is a SHORT subtle hint (1 sentence)
- "help" is a LONGER clue that makes it easier (2-3 sentences)
- "is_traditional" is true only for real traditional riddles
- Use riddles that are culturally authentic to the specified tradition

Respond ONLY with valid JSON. No markdown, no code blocks, no extra text."""

    async def generate(self, request: AgentRequest) -> AsyncIterator[AgentResponse]:
        """Generate a riddle narration for streaming to the client."""
        prompt = self._build_prompt(request)

        self.logger.info(
            f"Generating riddle: culture={request.culture}",
            extra={"event": "riddle_start"},
        )

        buffer = ""
        async for response in self._stream_from_gemini(prompt, self.SYSTEM_INSTRUCTION):
            if response.is_final:
                if buffer.strip():
                    yield AgentResponse(
                        agent_name=self.AGENT_NAME,
                        content=buffer.strip(),
                        is_final=False,
                    )
                yield response
                continue

            buffer += response.content

            # Yield at natural sentence boundaries
            if len(buffer) > 80 and buffer.rstrip().endswith((".", "!", "?", "!")):
                yield AgentResponse(
                    agent_name=self.AGENT_NAME,
                    content=buffer.strip() + " ",
                    is_final=False,
                )
                buffer = ""

        # Flush remaining
        if buffer.strip():
            yield AgentResponse(
                agent_name=self.AGENT_NAME,
                content=buffer.strip(),
                is_final=False,
            )

    async def execute(self, input_data: dict) -> dict:
        """
        ADK-compatible execute: returns a RiddlePayload dict
        matching Flutter RiddleModel.
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
            payload = self._fix_riddle_payload(payload, culture)

        return payload

    def _build_prompt(self, request: AgentRequest) -> str:
        """Build the riddle generation prompt for streaming narration."""
        culture = request.culture or "East African"
        difficulty = request.preferences.get("difficulty", "medium")

        context_section = ""
        if request.session_context:
            context_section = f"""
CONVERSATION CONTEXT:
{request.session_context}
If there's an ongoing riddle game, continue it.
Avoid repeating riddles already used in this session."""

        return f"""Present an interactive African riddle experience.

PARAMETERS:
- Culture/Tradition: {culture}
- Difficulty: {difficulty}
- Language: English with {culture} phrases
{context_section}

Present the riddle dramatically, as if speaking to a live audience.
Use the traditional opening for the culture. After the riddle,
tell the listener they have 4 choices to pick from."""

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
            f"The riddle must have exactly 4 answer choices (1 correct, 3 wrong).",
            f"Include a tip (subtle hint) and help (detailed clue).",
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
                # Ensure id is present
                payload.setdefault("id", f"riddle_{uuid.uuid4().hex[:8]}")
                payload.setdefault("language", default_culture)
                payload.setdefault("culture", default_culture)
                # Convert 'question' field if missing but riddle_text exists
                if "question" not in payload and "riddle_text" in payload:
                    payload["question"] = payload.pop("riddle_text")
                return payload
        except json.JSONDecodeError:
            pass

        # Fallback: construct from raw text
        return self._build_fallback_riddle(raw_text, default_culture)

    @staticmethod
    def _build_fallback_riddle(raw_text: str, culture: str) -> dict:
        """Build a fallback riddle when JSON parsing fails."""
        return {
            "id": f"riddle_{uuid.uuid4().hex[:8]}",
            "question": raw_text.strip()[:500] if raw_text.strip()
                        else "What travels without legs?",
            "choices": [
                {"A story": True},
                {"The wind": False},
                {"A shadow": False},
                {"A dream": False},
            ],
            "tip": "It moves from mouth to ear.",
            "help": "Think about what a Griot carries across "
                    "mountains and rivers, passing it from "
                    "generation to generation.",
            "language": culture,
            "culture": culture,
            "explanation": f"A riddle inspired by {culture} oral tradition.",
            "is_traditional": False,
        }

    @staticmethod
    def _fix_riddle_payload(payload: dict, culture: str) -> dict:
        """Fix common schema issues to match Flutter RiddleModel."""
        fixed = dict(payload)
        fixed.setdefault("id", f"riddle_{uuid.uuid4().hex[:8]}")

        # Ensure question field exists
        if "question" not in fixed:
            fixed["question"] = fixed.pop("riddle_text",
                                          "What has no beginning and no end?")

        # Ensure exactly 4 choices in correct format
        choices = fixed.get("choices", [])
        if not isinstance(choices, list) or len(choices) != 4:
            answer = fixed.pop("answer", "A circle")
            choices = [
                {answer: True},
                {"The sun": False},
                {"A river": False},
                {"Time": False},
            ]
        # Validate each choice is {str: bool}
        valid_choices = []
        for c in choices:
            if isinstance(c, dict) and len(c) == 1:
                key = list(c.keys())[0]
                val = c[key]
                if isinstance(val, bool):
                    valid_choices.append(c)
                    continue
            # Invalid choice — skip
        while len(valid_choices) < 4:
            valid_choices.append({"Unknown": False})
        fixed["choices"] = valid_choices[:4]

        # Ensure exactly 1 correct answer
        correct_count = sum(1 for c in fixed["choices"]
                           for v in c.values() if v is True)
        if correct_count == 0:
            # Make the first one correct
            key = list(fixed["choices"][0].keys())[0]
            fixed["choices"][0] = {key: True}
        elif correct_count > 1:
            # Keep only the first correct one
            seen_correct = False
            for i, c in enumerate(fixed["choices"]):
                key = list(c.keys())[0]
                if c[key] is True:
                    if seen_correct:
                        fixed["choices"][i] = {key: False}
                    seen_correct = True

        fixed.setdefault("tip", "Think carefully...")
        fixed.setdefault("help", f"This riddle comes from {culture} tradition. "
                                  "Consider what the elders would say.")
        fixed.setdefault("language", culture)
        fixed.setdefault("culture", culture)
        fixed.setdefault("explanation", f"A riddle from {culture} tradition.")
        fixed.setdefault("is_traditional", False)

        return fixed
