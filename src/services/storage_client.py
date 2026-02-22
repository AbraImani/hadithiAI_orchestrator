"""
Cloud Storage Client
====================
Manages Cloud Storage operations for generated images,
audio assets, and other media files.
"""

import logging
import uuid
from typing import Optional

from core.config import settings

logger = logging.getLogger(__name__)


class StorageClient:
    """
    Cloud Storage operations for HadithiAI media.

    Handles:
    - Uploading generated images from Imagen 3
    - Generating signed/public URLs for client access
    - Managing the media bucket lifecycle
    """

    def __init__(self):
        self._client = None
        self._bucket = None
        self._logger = logger.getChild("storage")

    async def initialize(self):
        """Initialize the Cloud Storage client."""
        try:
            from google.cloud import storage

            self._client = storage.Client()
            self._bucket = self._client.bucket(settings.MEDIA_BUCKET)
            self._logger.info(
                f"Storage client initialized: bucket={settings.MEDIA_BUCKET}"
            )
        except Exception as e:
            self._logger.warning(
                f"Storage client init failed (image upload will be unavailable): {e}"
            )

    async def upload_image(
        self,
        image_bytes: bytes,
        content_type: str = "image/png",
        folder: str = "generated",
    ) -> Optional[str]:
        """
        Upload an image to Cloud Storage.

        Returns the public URL or None on failure.
        """
        if not self._bucket:
            self._logger.warning("Storage bucket not initialized")
            return None

        try:
            blob_name = f"{folder}/{uuid.uuid4().hex}.png"
            blob = self._bucket.blob(blob_name)
            blob.upload_from_string(image_bytes, content_type=content_type)
            blob.make_public()
            url = blob.public_url

            self._logger.info(
                f"Image uploaded: {blob_name}",
                extra={"event": "storage_upload", "blob": blob_name},
            )
            return url

        except Exception as e:
            self._logger.error(
                f"Image upload failed: {e}",
                extra={"event": "storage_upload_error"},
                exc_info=True,
            )
            return None

    async def delete_blob(self, blob_name: str):
        """Delete a blob from Cloud Storage."""
        if not self._bucket:
            return
        try:
            blob = self._bucket.blob(blob_name)
            blob.delete()
        except Exception as e:
            self._logger.warning(f"Blob deletion failed: {e}")
