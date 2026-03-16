"""
Story Agent (ADK-Compatible)
=============================
Generates culturally-rooted African stories in the oral tradition style.
Outputs StoryChunk schema-compliant dicts with explicit cultural_claims[].
Streams stories paragraph-by-paragraph for natural pacing.
"""

import json
import logging
import re
from typing import AsyncIterator

from agents.base_agent import BaseAgent
from core.models import AgentRequest, AgentResponse
from core.schemas import schema_validator

logger = logging.getLogger(__name__)


class StoryAgent(BaseAgent):
    """
    Generates immersive African oral tradition stories.

    ADK Agent Properties:
    - name: story_agent
    - model: gemini-2.0-flash
    - output_schema: StoryChunk
    - tools: cultural_lookup

    Features:
    - Streams in paragraph-sized chunks for natural pacing
    - Each chunk includes explicit cultural_claims[] for validation
    - Adapts tone/complexity based on audience
    - Embeds proverbs, call-and-response, and moral lessons
    - Marks visual moments for optional image generation
    """

    AGENT_NAME = "story"
    OUTPUT_SCHEMA = "StoryChunk"

    SYSTEM_INSTRUCTION = """You are a master African oral storyteller (Griot).

CRITICAL RULE: Output ONLY the story narration itself. NEVER output
planning, reasoning, JSON, structural markers, meta-commentary, or
any text that is not part of the story being told. Every word you
produce will be spoken aloud to the listener.

Your stories must:
1. Begin with the traditional opening of the specified culture
2. Include 2-3 culturally authentic characters with meaningful names
3. Embed at least one genuine proverb from the tradition
4. Include call-and-response moments naturally woven into the narration
5. Build to a moral lesson that emerges naturally from the narrative
6. End with the traditional closing of the culture

Style requirements:
- Write as if speaking aloud to a gathered audience
- Use "..." for dramatic pauses
- Include sensory details (sounds, smells, sights of the setting)
- Weave in local language phrases with pronunciation hints

FORBIDDEN in your output:
- JSON, code blocks, or structured data
- Labels like "Scene:", "Note:", "Theme:", "Cultural claim:"
- Square bracket markers like [SCENE_BREAK] or [VISUAL:]
- Planning text like "I will now..." or "Let me create..."
- Meta-commentary about the story structure

Anti-hallucination rules:
- Only use cultural elements you are confident about
- If referencing a specific tradition, it must be real
- Prefix uncertain claims with "In some tellings..."
- Do not invent proverbs -- use known ones or mark as "inspired by"
- Name the specific ethnic group, not just the country"""

    STRUCTURED_INSTRUCTION = """You are the Story Generation Engine of HadithiAI.

Generate a story chunk as a JSON object with this exact structure:
{
  "text": "The story text for this chunk",
  "culture": "the culture this references",
  "cultural_claims": [
    {"claim": "specific cultural assertion", "category": "character|proverb|custom|location|language|historical"}
  ],
  "scene_description": "optional visual scene description or null",
  "is_final": false
}

CRITICAL: Every cultural assertion in the text MUST be listed in cultural_claims.
If you mention a character, proverb, custom, or tradition, declare it explicitly.
This forces you to be conscious of what you are asserting.

Categories for claims:
- "character": Named figures, tricksters, heroes
- "proverb": Sayings, wisdom quotes
- "custom": Cultural practices, ceremonies
- "location": Places, geographical references
- "language": Words, phrases in local languages
- "historical": Historical events or periods

Respond ONLY with valid JSON. No markdown, no code blocks."""

    async def generate(self, request: AgentRequest) -> AsyncIterator[AgentResponse]:
        """Generate a streaming story based on the request."""
        prompt = self._build_prompt(request)

        self.logger.info(
            f"Generating story: culture={request.culture}, theme={request.theme}",
            extra={"event": "story_start"},
        )

        current_chunk = ""
        async for response in self._stream_from_gemini(prompt, self.SYSTEM_INSTRUCTION):
            if response.is_final:
                yield response
                continue

            current_chunk += response.content

            # Strip any structural markers that might have leaked through
            current_chunk = self._clean_narration(current_chunk)

            # Yield when we hit a natural boundary
            if self._is_chunk_boundary(current_chunk):
                yield AgentResponse(
                    agent_name=self.AGENT_NAME,
                    content=current_chunk.strip() + " ",
                    is_final=False,
                )
                current_chunk = ""

        # Flush remaining
        if current_chunk.strip():
            yield AgentResponse(
                agent_name=self.AGENT_NAME,
                content=self._clean_narration(current_chunk).strip(),
                is_final=False,
            )

    async def execute_streaming(self, input_data: dict) -> AsyncIterator[dict]:
        """
        ADK-compatible streaming: yields StoryChunk dicts.
        Each chunk includes explicit cultural_claims[] for validation.
        """
        culture = input_data.get("culture", "african")
        theme = input_data.get("theme", "wisdom")
        complexity = input_data.get("complexity", "adult")
        context = input_data.get("session_context", "")
        correction = input_data.get("_correction", "")

        prompt = self._build_structured_prompt(
            culture, theme, complexity, context, correction
        )

        full_text = await self._generate_structured_json(
            prompt, self.STRUCTURED_INSTRUCTION
        )

        # Parse JSON chunks from the response
        chunks = self._parse_story_chunks(full_text, culture)

        for i, chunk in enumerate(chunks):
            chunk["is_final"] = (i == len(chunks) - 1)
            # Validate before yielding
            is_valid, errors = schema_validator.validate("StoryChunk", chunk)
            if is_valid:
                yield chunk
            else:
                self.logger.warning(
                    f"StoryChunk validation failed: {errors}",
                    extra={"event": "story_chunk_invalid"},
                )
                # Fix minimally and yield
                yield {
                    "text": chunk.get("text", "The story continues..."),
                    "culture": culture,
                    "cultural_claims": [],
                    "is_final": chunk.get("is_final", False),
                }

    async def execute(self, input_data: dict) -> dict:
        """ADK-compatible single-shot: returns a full StoryChunk."""
        chunks = []
        async for chunk in self.execute_streaming(input_data):
            chunks.append(chunk)

        if chunks:
            # Merge all chunks into one
            merged_text = " ".join(
                self._clean_narration(c.get("text", "")) for c in chunks
            )
            all_claims = []
            for c in chunks:
                all_claims.extend(c.get("cultural_claims", []))
            return {
                "text": merged_text,
                "culture": input_data.get("culture", "african"),
                "cultural_claims": all_claims,
                "is_final": True,
            }

        return {
            "text": "The story awaits...",
            "culture": input_data.get("culture", "african"),
            "cultural_claims": [],
            "is_final": True,
        }

    def _build_prompt(self, request: AgentRequest) -> str:
        """Build the story generation prompt for streaming mode."""
        culture = request.culture or "a West African"
        theme = request.theme or "wisdom"
        complexity = request.age_group or "adult"

        context_section = ""
        if request.session_context:
            context_section = f"""
CONVERSATION CONTEXT:
{request.session_context}
Continue the conversation naturally. If there's an ongoing story,
continue it rather than starting a new one unless asked."""

        return f"""Generate an immersive African oral tradition story.

PARAMETERS:
- Culture/Tradition: {culture}
- Theme: {theme}
- Audience complexity: {complexity}
- Language: English with {culture} phrases mixed in
{context_section}

Remember: You are speaking this aloud to a listener. Make it vivid,
rhythmic, and engaging. Use the oral tradition patterns of the {culture} people.

Begin the story now:"""

    def _build_structured_prompt(
        self,
        culture: str,
        theme: str,
        complexity: str,
        context: str,
        correction: str,
    ) -> str:
        """Build prompt for structured JSON output."""
        parts = [
            f"Generate an African oral tradition story as structured JSON.",
            f"Culture: {culture}",
            f"Theme: {theme}",
            f"Audience: {complexity}",
        ]
        if context:
            parts.append(f"Context: {context}")
        if correction:
            parts.append(f"CORRECTION: {correction}")

        parts.append(
            "Generate 3-5 JSON chunks, each a complete paragraph. "
            "Every cultural reference MUST appear in cultural_claims[]."
        )

        return "\n".join(parts)

    def _parse_story_chunks(self, raw_text: str, default_culture: str) -> list[dict]:
        """Parse Gemini output into StoryChunk dicts."""
        chunks = []

        # Try to parse as JSON array
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            # Remove markdown code blocks
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()

        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                chunks = parsed
            elif isinstance(parsed, dict):
                chunks = [parsed]
        except json.JSONDecodeError:
            # Try to find JSON objects in the text
            json_pattern = re.compile(r'\{[^{}]*\}', re.DOTALL)
            matches = json_pattern.findall(cleaned)
            for match in matches:
                try:
                    obj = json.loads(match)
                    if "text" in obj:
                        chunks.append(obj)
                except json.JSONDecodeError:
                    continue

        # If no valid JSON found, wrap raw text as a single chunk
        if not chunks:
            cleaned = self._clean_narration(raw_text)
            chunks = [{
                "text": cleaned.strip() if cleaned.strip() else "The story begins...",
                "culture": default_culture,
                "cultural_claims": [],
            }]

        # Ensure all chunks have required fields
        for chunk in chunks:
            chunk["text"] = self._clean_narration(str(chunk.get("text", "")))
            chunk.setdefault("culture", default_culture)
            chunk.setdefault("cultural_claims", [])

        return chunks

    @staticmethod
    def _is_chunk_boundary(text: str) -> bool:
        """Check if we're at a natural boundary to yield a chunk."""
        text = text.rstrip()
        if not text:
            return False

        if text.endswith("\n\n"):
            return True

        sentence_enders = (".", "!", "?", '..."')
        if len(text) > 80 and any(text.endswith(e) for e in sentence_enders):
            return True

        if len(text) > 300:
            return True

        return False

    # Gemini 2.5 Flash thinking section headers (bold gerund phrases)
    _THINKING_SECTION_RE = re.compile(
        r"\*\*(?:Consider|Craft|Generat|Defin|Refin|Plan|Structur|Evaluat|"
        r"Analyz|Review|Synthes|Explor|Identif|Assess|Develop|Build|Formulat|"
        r"Outlin|Creat|Design|Map|Gather|Validat|Select|Execut|Initializ|"
        r"Process|Describ|Establish|Organiz|Implement|Determin|Prepar)[a-z]*"
        r"\b[^*\n]*\*\*[^\n]*",
        re.IGNORECASE,
    )
    _MONOLOGUE_LINE_RE = re.compile(
        r"(?im)^(?:I(?:'m| am| will| have| need| want| plan|'ve) now?\b|"
        r"My goal (?:is|here)\b|The (?:user|model|story) (?:wants|needs|requires)\b|"
        r"I (?:should|must|can|would|could)\b|Let me (?:now |first |also )?\b|"
        r"I'(?:ll|d) (?:now |then |also )?\b)[^\n]*$"
    )

    @staticmethod
    def _clean_narration(text: str) -> str:
        """Strip structural markers and meta-text from narration output.

        Ensures only pure spoken narration reaches Gemini for speech
        synthesis. Removes [VISUAL:...], [SCENE_BREAK], [CALL_RESPONSE],
        Gemini 2.5 Flash thinking sections, and any other markers that
        should not be read aloud.
        """
        # Remove XML-like thought blocks
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<thought>.*?</thought>', '', text, flags=re.DOTALL | re.IGNORECASE)

        # Strip Gemini 2.5 Flash bold thinking section headers
        # e.g. "**Considering Story Framework**", "**Crafting Initial Story**"
        text = StoryAgent._THINKING_SECTION_RE.sub('', text)

        # Strip first-person internal-monologue lines
        text = StoryAgent._MONOLOGUE_LINE_RE.sub('', text)

        # Strip inline tool-call code references leaked into narration
        text = re.sub(r'\b\w+\([^)]{0,120}\)', '', text)

        # Remove common reasoning/meta lines that should never be spoken
        text = re.sub(
            r'(?im)^\s*(thought|reasoning|analysis|chain\s*of\s*thought|internal\s*monologue)\s*:\s*.*$',
            '',
            text,
        )
        text = re.sub(r"(?im)^\s*let'?s\s+think\b.*$", '', text)
        text = re.sub(r'(?im)^\s*i\s+should\b.*$', '', text)

        # Remove [VISUAL: ...] markers
        text = re.sub(r'\[VISUAL:[^\]]*\]', '', text)
        # Remove other bracket markers
        text = re.sub(r'\[SCENE_BREAK\]', '', text)
        text = re.sub(r'\[CALL_RESPONSE\]', '', text)
        # Remove any remaining [MARKER_STYLE] tags
        text = re.sub(r'\[[A-Z_]{3,}\]', '', text)
        # Remove any ```json or ``` code block markers
        text = re.sub(r'```\w*', '', text)
        # Collapse multiple spaces/newlines
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'  +', ' ', text)
        return text
