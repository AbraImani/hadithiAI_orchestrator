"""
Story Agent
===========
Generates culturally-rooted African stories in the oral tradition style.
Streams stories paragraph-by-paragraph for natural pacing.
"""

from typing import AsyncIterator

from agents.base_agent import BaseAgent
from core.models import AgentRequest, AgentResponse


class StoryAgent(BaseAgent):
    """
    Generates immersive African oral tradition stories.
    
    Features:
    - Streams in paragraph-sized chunks for natural pacing
    - Adapts tone/complexity based on audience
    - Embeds proverbs, call-and-response, and moral lessons
    - Marks visual moments for optional image generation
    """

    AGENT_NAME = "story"

    SYSTEM_INSTRUCTION = """You are the Story Generation Engine of HadithiAI, 
a master African oral storyteller (Griot).

Your stories must:
1. Begin with the traditional opening of the specified culture
2. Include 2-3 culturally authentic characters with meaningful names
3. Embed at least one genuine proverb from the tradition
4. Include a call-and-response moment (mark with [CALL_RESPONSE])
5. Build to a moral lesson that emerges naturally from the narrative
6. End with the traditional closing of the culture

Style requirements:
- Write as if speaking aloud to a gathered audience
- Use "..." for dramatic pauses
- Use CAPS sparingly for emphasis
- Include sensory details (sounds, smells, sights of the setting)
- Weave in local language phrases with pronunciation hints

Streaming instructions:
- Generate in natural paragraph-sized chunks
- Each chunk should be a complete thought (1-3 sentences)
- Mark scene transitions with [SCENE_BREAK]
- Mark visually rich moments with [VISUAL: brief description]

Anti-hallucination rules:
- Only use cultural elements you are confident about
- If referencing a specific tradition, it must be real
- Prefix uncertain claims with "In some tellings..."
- Do not invent proverbs — use known ones or mark as "inspired by"
- Name the specific ethnic group, not just the country"""

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

            # Detect visual moments and extract them
            visual_moment = None
            if "[VISUAL:" in current_chunk:
                # Extract the visual description
                start_idx = current_chunk.index("[VISUAL:")
                end_idx = current_chunk.index("]", start_idx)
                if end_idx > start_idx:
                    visual_desc = current_chunk[start_idx + 8:end_idx].strip()
                    visual_moment = visual_desc
                    # Remove the marker from the text
                    current_chunk = (
                        current_chunk[:start_idx] + current_chunk[end_idx + 1:]
                    )

            # Yield when we hit a natural boundary
            if self._is_chunk_boundary(current_chunk):
                yield AgentResponse(
                    agent_name=self.AGENT_NAME,
                    content=current_chunk.strip() + " ",
                    is_final=False,
                    visual_moment=visual_moment,
                )
                current_chunk = ""

        # Flush remaining
        if current_chunk.strip():
            yield AgentResponse(
                agent_name=self.AGENT_NAME,
                content=current_chunk.strip(),
                is_final=False,
            )

    def _build_prompt(self, request: AgentRequest) -> str:
        """Build the story generation prompt."""
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

    @staticmethod
    def _is_chunk_boundary(text: str) -> bool:
        """Check if we're at a natural boundary to yield a chunk."""
        text = text.rstrip()
        if not text:
            return False

        # Paragraph break
        if text.endswith("\n\n"):
            return True

        # Scene break marker
        if "[SCENE_BREAK]" in text:
            return True

        # Call-and-response marker
        if "[CALL_RESPONSE]" in text:
            return True

        # Sentence end with minimum length
        sentence_enders = (".", "!", "?", '..."')
        if len(text) > 80 and any(text.endswith(e) for e in sentence_enders):
            return True

        # Force break at max length
        if len(text) > 300:
            return True

        return False
