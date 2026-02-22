"""
Health Check Endpoint
=====================
Simple health and readiness probes for Cloud Run.
"""

from fastapi import APIRouter
from gateway.websocket_handler import active_connections

router = APIRouter()


@router.get("/health")
async def health():
    """Liveness probe — is the process alive?"""
    return {"status": "healthy", "service": "hadithiai-live"}


@router.get("/ready")
async def readiness():
    """Readiness probe — is the service ready to accept traffic?"""
    return {
        "status": "ready",
        "active_connections": len(active_connections),
    }
