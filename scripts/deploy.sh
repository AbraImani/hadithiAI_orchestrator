#!/bin/bash
# ─────────────────────────────────────────────────
# HadithiAI Live — One-Command Deployment Script
# ─────────────────────────────────────────────────
set -euo pipefail

# ── Configuration ──
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="hadithiai-gateway"
IMAGE_NAME="${REGION}-docker.pkg.dev/${PROJECT_ID}/hadithiai/gateway"

echo "🌍 HadithiAI Live — Deployment"
echo "================================"
echo "Project: ${PROJECT_ID}"
echo "Region:  ${REGION}"
echo ""

# ── Validate ──
if [ -z "$PROJECT_ID" ]; then
    echo "❌ Error: Set GOOGLE_CLOUD_PROJECT environment variable"
    exit 1
fi

# ── Step 1: Enable APIs ──
echo "📡 Enabling Google Cloud APIs..."
gcloud services enable \
    run.googleapis.com \
    firestore.googleapis.com \
    storage.googleapis.com \
    aiplatform.googleapis.com \
    artifactregistry.googleapis.com \
    --project="${PROJECT_ID}" \
    --quiet

# ── Step 2: Create Artifact Registry (if not exists) ──
echo "📦 Setting up Artifact Registry..."
gcloud artifacts repositories create hadithiai \
    --repository-format=docker \
    --location="${REGION}" \
    --project="${PROJECT_ID}" \
    --quiet 2>/dev/null || echo "  (already exists)"

# ── Step 3: Create Cloud Storage bucket (if not exists) ──
echo "🪣 Setting up Cloud Storage..."
gsutil mb -p "${PROJECT_ID}" -l "${REGION}" \
    "gs://${PROJECT_ID}-hadithiai-media" 2>/dev/null || echo "  (already exists)"
gsutil iam ch allUsers:objectViewer \
    "gs://${PROJECT_ID}-hadithiai-media" 2>/dev/null || true

# ── Step 4: Build and push Docker image ──
echo "🐳 Building Docker image..."
gcloud builds submit \
    --tag "${IMAGE_NAME}:latest" \
    --project="${PROJECT_ID}" \
    --quiet

# ── Step 5: Deploy to Cloud Run ──
echo "🚀 Deploying to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
    --image "${IMAGE_NAME}:latest" \
    --region "${REGION}" \
    --project="${PROJECT_ID}" \
    --min-instances 1 \
    --max-instances 10 \
    --timeout 3600 \
    --cpu 2 \
    --memory 2Gi \
    --concurrency 80 \
    --allow-unauthenticated \
    --session-affinity \
    --cpu-boost \
    --no-cpu-throttling \
    --set-env-vars "HADITHI_PROJECT_ID=${PROJECT_ID},HADITHI_REGION=${REGION},HADITHI_MEDIA_BUCKET=${PROJECT_ID}-hadithiai-media" \
    --quiet

# ── Step 6: Get service URL ──
SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" \
    --region="${REGION}" \
    --project="${PROJECT_ID}" \
    --format='value(status.url)')

echo ""
echo "✅ Deployment Complete!"
echo "================================"
echo "🌐 Web Client:  ${SERVICE_URL}"
echo "🔌 WebSocket:   wss://${SERVICE_URL#https://}/ws"
echo "❤️  Health:      ${SERVICE_URL}/health"
echo ""
echo "Open ${SERVICE_URL} in your browser to start talking to HadithiAI!"
