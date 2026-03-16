"""
Visual Generation Agent (ADK-Compatible)
=========================================
Generates scene illustrations using Google GenAI Imagen 3.
Uses the google-genai SDK with API key authentication
(compatible with Cloud Run, no Vertex AI service account needed).

Input schema: ImageRequest
Output schema: ImageResult
Runs asynchronously -- never blocks the audio/text stream.
"""

import asyncio
import base64
import logging
import uuid
from typing import Optional

from core.config import settings
from core.schemas import schema_validator
from services.firestore_client import FirestoreClient

logger = logging.getLogger(__name__)


class VisualGenerationAgent:
    """
    Generates images using Google GenAI Imagen 3.

    Uses the google-genai SDK with API key authentication,
    which works on Cloud Run without service account setup.

    ADK Agent Properties:
    - name: visual_agent
    - tools: call_imagen3
    - input_schema: ImageRequest
    - output_schema: ImageResult
    """

    AGENT_NAME = "visual"
    OUTPUT_SCHEMA = "ImageResult"

    # Base prompt template for culturally appropriate imagery
    PROMPT_TEMPLATE = (
        "African oral tradition illustration, {scene}, "
        "in the style of contemporary African art, warm earth tones, "
        "vibrant colors, cultural authenticity, {culture} visual elements, "
        "digital painting, storytelling scene, detailed, beautiful"
    )

    NEGATIVE_PROMPT = (
        "stereotypical, offensive, caricature, Western-centric, "
        "colonial imagery, unrealistic skin tones, cartoonish, "
        "low quality, blurry, text, watermark"
    )

    def __init__(self, firestore: FirestoreClient):
        self.firestore = firestore
        self.logger = logger.getChild("visual")
        self._client = None

    def _get_client(self):
        """Lazy-initialize the GenAI client."""
        if self._client is None:
            try:
                from google import genai
                self._client = genai.Client(
                    api_key=settings.GEMINI_API_KEY
                )
                self.logger.info("GenAI Imagen client initialized")
            except Exception as e:
                self.logger.warning(f"Failed to init GenAI client: {e}")
        return self._client

    async def execute(self, input_data: dict) -> dict:
        """
        ADK-compatible execute: takes ImageRequest, returns ImageResult.
        """
        scene = input_data.get("scene_description", "")
        culture = input_data.get("culture", "African")
        aspect_ratio = input_data.get("aspect_ratio", "16:9")

        url = await self.generate_image(
            scene_description=scene,
            culture=culture,
            aspect_ratio=aspect_ratio,
        )

        if url:
            return {"status": "success", "url": url}
        else:
            return {
                "status": "failed",
                "error": "Image generation unavailable or failed",
            }

    async def generate_image(
        self,
        scene_description: str,
        culture: Optional[str] = None,
        aspect_ratio: str = "16:9",
    ) -> Optional[str]:
        """
        Generate an image for the given scene using google-genai SDK.

        Returns the Cloud Storage URL of the generated image,
        or None if generation fails.

        Uses Imagen 3 via the GenAI API (API key auth).
        Falls back to returning None silently on any failure.
        """
        culture = culture or "African"
        prompt = self.PROMPT_TEMPLATE.format(
            scene=scene_description,
            culture=culture,
        )

        self.logger.info(
            f"Generating image: {scene_description[:80]}...",
            extra={"event": "image_gen_start", "culture": culture},
        )

        try:
            client = self._get_client()
            if not client:
                self.logger.warning("GenAI client not available")
                return None

            from google import genai
            from google.genai import types

            # Run the blocking SDK call in a thread executor
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.models.generate_images(
                    model=settings.IMAGEN_MODEL,
                    prompt=prompt,
                    config=types.GenerateImagesConfig(
                        number_of_images=1,
                        aspect_ratio=aspect_ratio,
                        safety_filter_level="BLOCK_LOW_AND_ABOVE",
                        person_generation="ALLOW_ADULT",
                        negative_prompt=self.NEGATIVE_PROMPT,
                    ),
                ),
            )

            if not response.generated_images:
                self.logger.warning("Imagen returned no images")
                return None

            # Get image bytes from the response
            image = response.generated_images[0]
            image_bytes = image.image.image_bytes

            # Try uploading to Cloud Storage
            url = await self._upload_to_storage(image_bytes)
            if url:
                return url

            # Fallback: return full base64 data URL when storage upload fails
            b64 = base64.b64encode(image_bytes).decode("utf-8")
            return f"data:image/png;base64,{b64}"

        except ImportError as e:
            self.logger.warning(f"GenAI SDK import error: {e}")
            return None
        except Exception as e:
            self.logger.error(
                f"Image generation failed: {e}",
                extra={"event": "image_gen_error"},
                exc_info=True,
            )
            return None

    async def _upload_to_storage(self, image_bytes: bytes) -> Optional[str]:
        """Upload image bytes to Cloud Storage, return public URL."""
        try:
            from google.cloud import storage

            def _upload():
                storage_client = storage.Client()
                bucket = storage_client.bucket(settings.MEDIA_BUCKET)
                blob_name = f"generated/{uuid.uuid4().hex}.png"
                blob = bucket.blob(blob_name)
                blob.upload_from_string(image_bytes, content_type="image/png")
                blob.make_public()
                return blob.public_url

            url = await asyncio.get_event_loop().run_in_executor(None, _upload)
            self.logger.info(
                f"Image uploaded to storage",
                extra={"event": "image_upload_complete"},
            )
            return url

        except Exception as e:
            self.logger.warning(f"Storage upload failed: {e}")
            return None
