# ─────────────────────────────────────────────────
# HadithiAI Live — PowerShell Deployment Script
# For Windows users
# ─────────────────────────────────────────────────

$ErrorActionPreference = "Stop"

# ── Configuration ──
$PROJECT_ID = $env:GOOGLE_CLOUD_PROJECT
$REGION = if ($env:REGION) { $env:REGION } else { "us-central1" }
$SERVICE_NAME = "hadithiai-gateway"
$IMAGE_NAME = "${REGION}-docker.pkg.dev/${PROJECT_ID}/hadithiai/gateway"

Write-Host "🌍 HadithiAI Live — Deployment" -ForegroundColor Cyan
Write-Host "================================"
Write-Host "Project: $PROJECT_ID"
Write-Host "Region:  $REGION"
Write-Host ""

if (-not $PROJECT_ID) {
    Write-Host "❌ Error: Set GOOGLE_CLOUD_PROJECT environment variable" -ForegroundColor Red
    exit 1
}

# ── Step 1: Enable APIs ──
Write-Host "📡 Enabling Google Cloud APIs..." -ForegroundColor Yellow
gcloud services enable `
    run.googleapis.com `
    firestore.googleapis.com `
    storage.googleapis.com `
    aiplatform.googleapis.com `
    artifactregistry.googleapis.com `
    --project="$PROJECT_ID" `
    --quiet

# ── Step 2: Create Artifact Registry ──
Write-Host "📦 Setting up Artifact Registry..." -ForegroundColor Yellow
try {
    gcloud artifacts repositories create hadithiai `
        --repository-format=docker `
        --location="$REGION" `
        --project="$PROJECT_ID" `
        --quiet 2>$null
} catch {
    Write-Host "  (already exists)"
}

# ── Step 3: Create Cloud Storage bucket ──
Write-Host "🪣 Setting up Cloud Storage..." -ForegroundColor Yellow
try {
    gsutil mb -p "$PROJECT_ID" -l "$REGION" "gs://${PROJECT_ID}-hadithiai-media" 2>$null
} catch {
    Write-Host "  (already exists)"
}
gsutil iam ch allUsers:objectViewer "gs://${PROJECT_ID}-hadithiai-media" 2>$null

# ── Step 4: Build Docker image ──
Write-Host "🐳 Building Docker image..." -ForegroundColor Yellow
gcloud builds submit `
    --tag "${IMAGE_NAME}:latest" `
    --project="$PROJECT_ID" `
    --quiet

# ── Step 5: Deploy to Cloud Run ──
Write-Host "🚀 Deploying to Cloud Run..." -ForegroundColor Yellow
gcloud run deploy "$SERVICE_NAME" `
    --image "${IMAGE_NAME}:latest" `
    --region "$REGION" `
    --project="$PROJECT_ID" `
    --min-instances 1 `
    --max-instances 10 `
    --timeout 3600 `
    --cpu 2 `
    --memory 2Gi `
    --concurrency 80 `
    --allow-unauthenticated `
    --session-affinity `
    --cpu-boost `
    --no-cpu-throttling `
    --set-env-vars "HADITHI_PROJECT_ID=${PROJECT_ID},HADITHI_REGION=${REGION},HADITHI_MEDIA_BUCKET=${PROJECT_ID}-hadithiai-media" `
    --quiet

# ── Step 6: Get service URL ──
$SERVICE_URL = gcloud run services describe "$SERVICE_NAME" `
    --region="$REGION" `
    --project="$PROJECT_ID" `
    --format='value(status.url)'

Write-Host ""
Write-Host "✅ Deployment Complete!" -ForegroundColor Green
Write-Host "================================"
Write-Host "🌐 Web Client:  $SERVICE_URL"
Write-Host "🔌 WebSocket:   wss://$($SERVICE_URL -replace 'https://','')/ws"
Write-Host "❤️  Health:      $SERVICE_URL/health"
Write-Host ""
Write-Host "Open $SERVICE_URL in your browser to start talking to HadithiAI!"
