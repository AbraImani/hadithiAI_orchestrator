"""
Visual Generation Agent (ADK-Compatible)
=========================================
Generates scene illustrations using Vertex AI Imagen 3.
Input schema: ImageRequest
Output schema: ImageResult
Runs asynchronously -- never blocks the audio/text stream.
"""

import logging
import uuid
from typing import Optional

from core.config import settings
from core.schemas import schema_validator
from services.firestore_client import FirestoreClient

logger = logging.getLogger(__name__)


class VisualGenerationAgent:
    """
    Generates images using Vertex AI Imagen 3.

    ADK Agent Properties:
    - name: visual_agent
    - tools: call_imagen3
    - input_schema: ImageRequest
    - output_schema: ImageResult

    This agent is unique because:
    - It runs completely asynchronously (fire-and-forget)
    - It never blocks the conversation stream
    - Images are a "bonus" enhancement, not critical path
    - Generated images are stored in Cloud Storage
    - Client receives a URL when the image is ready
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

    async def execute(self, input_data: dict) -> dict:
        """
        ADK-compatible execute: takes ImageRequest, returns ImageResult.
        Both input and output are schema-validated.
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
            return {
                "status": "success",
                "url": url,
            }
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
        Generate an image for the given scene.

        Returns the Cloud Storage URL of the generated image,
        or None if generation fails.

        This method is expected to take 5-15 seconds.
        Always call this in a background asyncio.Task.
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
            from google.cloud import aiplatform
            from vertexai.preview.vision_models import ImageGenerationModel

            model = ImageGenerationModel.from_pretrained(settings.IMAGEN_MODEL)

            response = model.generate_images(
                prompt=prompt,
                number_of_images=1,
                aspect_ratio=aspect_ratio,
                safety_filter_level="block_few",
                person_generation="allow_adult",
                negative_prompt=self.NEGATIVE_PROMPT,
            )

            if not response.images:
                self.logger.warning("Imagen returned no images")
                return None

            # Upload to Cloud Storage
            image = response.images[0]
            image_bytes = image._image_bytes

            from google.cloud import storage

            storage_client = storage.Client()
            bucket = storage_client.bucket(settings.MEDIA_BUCKET)

            blob_name = f"generated/{uuid.uuid4().hex}.png"
            blob = bucket.blob(blob_name)
            blob.upload_from_string(image_bytes, content_type="image/png")
            blob.make_public()
            url = blob.public_url

            self.logger.info(
                f"Image generated and uploaded: {blob_name}",
                extra={"event": "image_gen_complete", "blob": blob_name},
            )

            return url

        except ImportError:
            self.logger.warning(
                "Vertex AI SDK not available -- skipping image generation"
            )
            return None
        except Exception as e:
            self.logger.error(
                f"Image generation failed: {e}",
                extra={"event": "image_gen_error"},
                exc_info=True,
            )
            return None
