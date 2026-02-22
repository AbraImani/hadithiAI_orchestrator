"""
Riddle Agent
============
Generates and manages interactive African riddles.
Supports multi-turn riddle sessions with hints and scoring.
"""

from typing import AsyncIterator

from agents.base_agent import BaseAgent
from core.models import AgentRequest, AgentResponse


class RiddleAgent(BaseAgent):
    """
    Generates interactive African cultural riddles.
    
    Features:
    - Authentic riddles from various African traditions
    - Progressive hint system (3 hints per riddle)
    - Cultural explanation after answer is revealed
    - Multi-turn riddle game management
    - Difficulty adaptation
    """

    AGENT_NAME = "riddle"

    SYSTEM_INSTRUCTION = """You are the Riddle Master of HadithiAI,
specializing in African riddle traditions.

In many African cultures, riddles (called "vitendawili" in Swahili,
"àlọ́" in Yoruba, "izaga" in Zulu) are a beloved form of oral tradition
used to teach wisdom, sharpen wit, and entertain.

Your riddles must:
1. Use the traditional riddle-opening of the specified culture
   - Swahili: "Kitendawili!" (audience responds: "Tega!")
   - Yoruba: "Àlọ́ o!" (audience responds: "Àlọ́!")
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
- Never attribute a riddle to a culture it doesn't belong to
- Use authentic traditional openings only if you're certain"""

    async def generate(self, request: AgentRequest) -> AsyncIterator[AgentResponse]:
        """Generate a riddle with full interactive structure."""
        prompt = self._build_prompt(request)

        self.logger.info(
            f"Generating riddle: culture={request.culture}",
            extra={"event": "riddle_start"},
        )

        # For riddles, we stream the opening and riddle, but buffer
        # the hints and answer (they should be revealed on demand)
        current_section = ""
        buffer = ""

        async for response in self._stream_from_gemini(prompt, self.SYSTEM_INSTRUCTION):
            if response.is_final:
                # Flush remaining buffer
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

            # Detect section markers and yield appropriately
            for marker in ["[OPENING]", "[RIDDLE]", "[HINTS]", "[ANSWER]", "[EXPLANATION]"]:
                if marker in buffer:
                    # Yield content before the marker
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

            # Yield at sentence boundaries within a section
            if len(buffer) > 100 and buffer.rstrip().endswith((".", "!", "?")):
                yield AgentResponse(
                    agent_name=self.AGENT_NAME,
                    content=buffer.strip() + " ",
                    is_final=False,
                    metadata={"section": current_section},
                )
                buffer = ""

    def _build_prompt(self, request: AgentRequest) -> str:
        """Build the riddle generation prompt."""
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
