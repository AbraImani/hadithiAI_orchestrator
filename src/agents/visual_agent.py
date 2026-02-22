"""
Visual Generation Agent
=======================
Generates scene illustrations using Vertex AI Imagen 3.
Runs asynchronously — never blocks the audio/text stream.
"""

import logging
from typing import Optional

from core.config import settings
from services.firestore_client import FirestoreClient

logger = logging.getLogger(__name__)


class VisualGenerationAgent:
    """
    Generates images using Vertex AI Imagen 3.
    
    This agent is unique because:
    - It runs completely asynchronously (fire-and-forget)
    - It never blocks the conversation stream
    - Images are a "bonus" enhancement, not critical path
    - Generated images are stored in Cloud Storage
    - Client receives a URL when the image is ready
    """

    AGENT_NAME = "visual"

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

    async def generate_image(
        self,
        scene_description: str,
        culture: Optional[str] = None,
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
            extra={"event": "image_gen_start"},
        )

        try:
            # ── Call Vertex AI Imagen 3 ──
            from google.cloud import aiplatform
            from vertexai.preview.vision_models import ImageGenerationModel

            model = ImageGenerationModel.from_pretrained(settings.IMAGEN_MODEL)
            
            response = model.generate_images(
                prompt=prompt,
                number_of_images=1,
                aspect_ratio="16:9",         # Widescreen for storytelling scenes
                safety_filter_level="block_few",
                person_generation="allow_adult",
                negative_prompt=self.NEGATIVE_PROMPT,
            )

            if not response.images:
                self.logger.warning("Imagen returned no images")
                return None

            # ── Upload to Cloud Storage ──
            image = response.images[0]
            image_bytes = image._image_bytes

            from google.cloud import storage

            storage_client = storage.Client()
            bucket = storage_client.bucket(settings.MEDIA_BUCKET)
            
            import uuid
            blob_name = f"generated/{uuid.uuid4().hex}.png"
            blob = bucket.blob(blob_name)
            blob.upload_from_string(image_bytes, content_type="image/png")
            
            # Make publicly accessible (for hackathon simplicity)
            blob.make_public()
            url = blob.public_url

            self.logger.info(
                f"Image generated and uploaded: {blob_name}",
                extra={"event": "image_gen_complete"},
            )

            return url

        except ImportError:
            self.logger.warning(
                "Vertex AI SDK not available — skipping image generation"
            )
            return None
        except Exception as e:
            self.logger.error(
                f"Image generation failed: {e}",
                extra={"event": "image_gen_error"},
                exc_info=True,
            )
            return None
