# ─────────────────────────────────────────────────
# HadithiAI Live — Docker Image
# Multi-stage build for minimal image size
# ─────────────────────────────────────────────────

# ── Stage 1: Build ──
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: Runtime ──
FROM python:3.12-slim

# Security: non-root user
RUN groupadd -r hadithiai && useradd -r -g hadithiai hadithiai

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY src/ ./
COPY static/ ./static/

# Set ownership
RUN chown -R hadithiai:hadithiai /app

# Switch to non-root user
USER hadithiai

# Environment
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

# Start server
# --ws websockets: required for WebSocket support
# --workers 1: single worker for WebSocket state management
# --timeout-keep-alive 600: long keep-alive for WebSocket connections
CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8080", \
     "--ws", "websockets", \
     "--workers", "1", \
     "--timeout-keep-alive", "600", \
     "--log-level", "info"]
