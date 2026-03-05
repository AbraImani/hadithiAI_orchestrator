"""
HadithiAI Live — Main Entry Point
=================================
Cloud Run service entry point. Initializes the FastAPI app with
WebSocket support, configures middleware, and starts the server.
"""

import os
import logging
import uvicorn
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Resolve paths relative to the project root (parent of src/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATIC_DIR = _PROJECT_ROOT / "static"

from gateway.websocket_handler import router as ws_router
from gateway.health import router as health_router
from gateway.rest_api import router as rest_router
from core.config import settings
from core.logging_config import setup_logging
from services.firestore_client import FirestoreClient
from services.gemini_client import GeminiClientPool

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown hooks."""
    # ── STARTUP ──
    setup_logging()
    logger.info("🌍 HadithiAI Live starting up...")

    # Initialize shared services (warm them up)
    app.state.firestore = FirestoreClient()
    app.state.gemini_pool = GeminiClientPool(
        project_id=settings.PROJECT_ID,
        region=settings.REGION,
        pool_size=settings.GEMINI_POOL_SIZE,
    )
    await app.state.gemini_pool.warm_up()
    logger.info("✅ Gemini client pool warmed up with %d sessions", settings.GEMINI_POOL_SIZE)

    logger.info("✅ HadithiAI Live ready to serve")
    yield

    # ── SHUTDOWN ──
    logger.info("🛑 HadithiAI Live shutting down...")
    await app.state.gemini_pool.close_all()
    logger.info("✅ All Gemini sessions closed")


app = FastAPI(
    title="HadithiAI Live",
    description="The First African Immersive Oral AI Agent",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS (allow web client from any origin for hackathon) ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ──
app.include_router(health_router, tags=["Health"])
app.include_router(ws_router, tags=["WebSocket"])
app.include_router(rest_router)

# ── Static files (web client) ──
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        log_level="info",
        ws="websockets",
        # Don't use reload in production
        reload=settings.DEBUG,
    )
