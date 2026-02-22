"""
Logging Configuration
=====================
Sets up structured logging for Cloud Logging integration
and local development.
"""

import logging
import sys
import json
from datetime import datetime, timezone


class StructuredFormatter(logging.Formatter):
    """JSON structured log formatter for Cloud Logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "component": record.name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        # Add extra fields if present
        for key in ("session_id", "turn_id", "agent", "latency_ms", "event"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)
        
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


class DevFormatter(logging.Formatter):
    """Human-readable formatter for local development."""

    FORMAT = "%(asctime)s │ %(levelname)-8s │ %(name)-30s │ %(message)s"

    def __init__(self):
        super().__init__(fmt=self.FORMAT, datefmt="%H:%M:%S")


def setup_logging():
    """Configure logging based on environment."""
    from core.config import settings

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.LOG_LEVEL))

    # Remove existing handlers
    root_logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)

    if settings.DEBUG:
        handler.setFormatter(DevFormatter())
    else:
        handler.setFormatter(StructuredFormatter())

    root_logger.addHandler(handler)

    # Suppress noisy loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("google.auth").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
