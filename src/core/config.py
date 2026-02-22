"""
Core Configuration
==================
Central configuration using Pydantic settings.
Loads from environment variables with sensible defaults.
"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ── Google Cloud ──
    PROJECT_ID: str = "hadithiai-live"
    REGION: str = "us-central1"
    
    # ── Gemini ──
    GEMINI_MODEL: str = "gemini-2.0-flash-live"
    GEMINI_TEXT_MODEL: str = "gemini-2.0-flash"
    GEMINI_POOL_SIZE: int = 3
    GEMINI_API_KEY: Optional[str] = None  # If using API key auth instead of ADC

    # ── Vertex AI (Imagen) ──
    IMAGEN_MODEL: str = "imagen-3.0-generate-002"
    IMAGEN_ENDPOINT: Optional[str] = None

    # ── Firestore ──
    FIRESTORE_DATABASE: str = "(default)"
    SESSION_TTL_HOURS: int = 24

    # ── Cloud Storage ──
    MEDIA_BUCKET: str = "hadithiai-media"

    # ── Application ──
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    MAX_SESSION_TURNS: int = 100
    MAX_CONCURRENT_SESSIONS: int = 200

    # ── Streaming ──
    AUDIO_CHUNK_DURATION_MS: int = 100
    AUDIO_SAMPLE_RATE_INPUT: int = 16000
    AUDIO_SAMPLE_RATE_OUTPUT: int = 24000
    STREAM_BUFFER_HIGH_WATERMARK: int = 50
    STREAM_BUFFER_LOW_WATERMARK: int = 10
    AGENT_TIMEOUT_SECONDS: float = 5.0

    # ── Cultural Grounding ──
    CULTURAL_CONFIDENCE_THRESHOLD: float = 0.7
    CULTURAL_REJECT_THRESHOLD: float = 0.4

    class Config:
        env_file = ".env"
        env_prefix = "HADITHI_"
        case_sensitive = True


settings = Settings()
