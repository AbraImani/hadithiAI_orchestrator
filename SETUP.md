# HadithiAI Live -- Setup Guide

Step-by-step instructions to get the application running locally and deploy it to Google Cloud.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Clone the Repository](#2-clone-the-repository)
3. [Python Environment Setup](#3-python-environment-setup)
4. [Google Cloud Project Setup](#4-google-cloud-project-setup)
5. [Environment Variables](#5-environment-variables)
6. [Run Locally](#6-run-locally)
7. [Run Tests](#7-run-tests)
8. [Deploy to Google Cloud Run](#8-deploy-to-google-cloud-run)
9. [Deploy with Terraform (Alternative)](#9-deploy-with-terraform-alternative)
10. [Verify the Deployment](#10-verify-the-deployment)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Prerequisites

Install these tools **before** starting:

| Tool | Version | Purpose | Install Link |
|------|---------|---------|-------------|
| **Python** | 3.12+ | Application runtime | https://www.python.org/downloads/ |
| **Git** | 2.x+ | Version control | https://git-scm.com/downloads |
| **Google Cloud CLI** (`gcloud`) | latest | GCP authentication & deployment | https://cloud.google.com/sdk/docs/install |
| **Docker** (optional) | 24+ | Local container testing | https://docs.docker.com/get-docker/ |
| **Terraform** (optional) | 1.5+ | Infrastructure-as-Code deployment | https://developer.hashicorp.com/terraform/install |

Verify installations:

```bash
python --version    # Should show 3.12.x or higher
git --version       # Should show 2.x
gcloud --version    # Should show Google Cloud SDK
```

---

## 2. Clone the Repository

```bash
git clone https://github.com/AbraImani/hadithiAI_orchestrator.git
cd hadithiAI_orchestrator
```

---

## 3. Python Environment Setup

### Windows (PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

You should see packages including `fastapi`, `uvicorn`, `google-genai`, `google-cloud-firestore`, `jsonschema`, etc.

Verify the install:

```bash
pip list | grep fastapi
# fastapi  0.115.6
```

---

## 4. Google Cloud Project Setup

### 4.1 Create or Select a GCP Project

```bash
# Create a new project (or skip if you already have one)
gcloud projects create hadithiai-live --name="HadithiAI Live"

# Set it as active
gcloud config set project hadithiai-live
```

### 4.2 Enable Billing

Billing must be enabled for Cloud Run, Vertex AI, and Firestore.
Go to: https://console.cloud.google.com/billing

### 4.3 Enable Required APIs

```bash
gcloud services enable \
    run.googleapis.com \
    firestore.googleapis.com \
    storage.googleapis.com \
    aiplatform.googleapis.com \
    artifactregistry.googleapis.com \
    cloudresourcemanager.googleapis.com
```

On Windows PowerShell:

```powershell
gcloud services enable `
    run.googleapis.com `
    firestore.googleapis.com `
    storage.googleapis.com `
    aiplatform.googleapis.com `
    artifactregistry.googleapis.com `
    cloudresourcemanager.googleapis.com
```

### 4.4 Create Firestore Database

```bash
gcloud firestore databases create --location=us-central1
```

### 4.5 Create Cloud Storage Bucket

```bash
gsutil mb -p hadithiai-live -l us-central1 gs://hadithiai-media
```

### 4.6 Authenticate for Local Development

```bash
gcloud auth application-default login
```

This creates local credentials that the app uses to talk to GCP services (Gemini, Firestore, Storage, Vertex AI).

---

## 5. Environment Variables

### 5.1 Create `.env` File

```bash
# From the project root
cp .env.example .env
```

On Windows:

```powershell
copy .env.example .env
```

### 5.2 Edit `.env`

Open `.env` in your editor and fill in the required values:

```ini
# -- Required --
HADITHI_PROJECT_ID=hadithiai-live          # Your GCP project ID
HADITHI_REGION=us-central1                 # Your GCP region

# -- Optional (API Key auth instead of ADC) --
# HADITHI_GEMINI_API_KEY=AIza...           # Only if not using gcloud auth

# -- Optional Overrides --
HADITHI_DEBUG=true                         # Enable hot-reload for local dev
HADITHI_LOG_LEVEL=DEBUG                    # Verbose logging
HADITHI_MEDIA_BUCKET=hadithiai-media       # Cloud Storage bucket name
```

The full list of variables is in `.env.example`. All variables have sensible defaults -- you only need to set `HADITHI_PROJECT_ID` at minimum.

---

## 6. Run Locally

### 6.1 Start the Server

```bash
cd src
python main.py
```

The server starts on **http://localhost:8080**.

Expected output:

```
INFO:     HadithiAI Live starting up...
INFO:     Gemini client pool warmed up with 3 sessions
INFO:     HadithiAI Live ready to serve
INFO:     Uvicorn running on http://0.0.0.0:8080
```

### 6.2 Open the Web Client

Open your browser and go to:

```
http://localhost:8080/static/index.html
```

### 6.3 Test Health Endpoint

```bash
curl http://localhost:8080/health
```

Expected: `{"status": "healthy"}` (or similar JSON response).

### 6.4 Test WebSocket (Optional)

Using a WebSocket client (e.g., `websocat`, browser console, or Postman):

```
ws://localhost:8080/ws
```

---

## 7. Run Tests

From the project root (with the virtual environment activated):

```bash
# Run all tests
python -m pytest tests/ -v

# Run schema contract tests only
python -m pytest tests/test_schemas.py -v

# Run orchestrator tests only
python -m pytest tests/test_orchestrator.py -v
```

All tests should pass with no external services required (they use mocks).

---

## 8. Deploy to Google Cloud Run

### Option A: Using the Deployment Script (Recommended)

**Windows PowerShell:**

```powershell
$env:GOOGLE_CLOUD_PROJECT = "hadithiai-live"
.\scripts\deploy.ps1
```

**Linux / macOS:**

```bash
export GOOGLE_CLOUD_PROJECT="hadithiai-live"
chmod +x scripts/deploy.sh
./scripts/deploy.sh
```

The script handles:
1. Enabling APIs
2. Creating Artifact Registry
3. Creating Cloud Storage bucket
4. Building Docker image via Cloud Build
5. Deploying to Cloud Run
6. Printing the service URL

### Option B: Manual Deployment

```bash
# 1. Set variables
export PROJECT_ID="hadithiai-live"
export REGION="us-central1"
export IMAGE="us-central1-docker.pkg.dev/$PROJECT_ID/hadithiai/gateway"

# 2. Create Artifact Registry repo
gcloud artifacts repositories create hadithiai \
    --repository-format=docker \
    --location=$REGION

# 3. Build the image
gcloud builds submit --tag $IMAGE:latest

# 4. Deploy to Cloud Run
gcloud run deploy hadithiai-gateway \
    --image $IMAGE:latest \
    --region $REGION \
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
    --set-env-vars "HADITHI_PROJECT_ID=$PROJECT_ID,HADITHI_REGION=$REGION,HADITHI_MEDIA_BUCKET=$PROJECT_ID-hadithiai-media"
```

---

## 9. Deploy with Terraform (Alternative)

```bash
cd infrastructure

# Initialize Terraform
terraform init

# Preview what will be created
terraform plan -var="project_id=hadithiai-live"

# Apply
terraform apply -var="project_id=hadithiai-live"
```

Terraform manages: Cloud Run service, Firestore, Cloud Storage, IAM, and networking.

---

## 10. Verify the Deployment

After deployment, the script prints the service URL. Verify:

```bash
# Health check
curl https://YOUR-SERVICE-URL/health

# Open the web client
# https://YOUR-SERVICE-URL/static/index.html

# WebSocket endpoint
# wss://YOUR-SERVICE-URL/ws
```

---

## 11. Troubleshooting

### "ModuleNotFoundError" when running `python main.py`

Make sure you activated the virtual environment:

```bash
# Windows
.venv\Scripts\activate

# Linux/Mac
source .venv/bin/activate
```

And installed dependencies:

```bash
pip install -r requirements.txt
```

### "Could not automatically determine credentials"

Run:

```bash
gcloud auth application-default login
```

Or set an API key in `.env`:

```ini
HADITHI_GEMINI_API_KEY=AIza...
```

### "Firestore database does not exist"

Create the default database:

```bash
gcloud firestore databases create --location=us-central1
```

### "Permission denied" on Cloud Storage

Make sure the bucket exists and your service account has access:

```bash
gsutil mb -p hadithiai-live -l us-central1 gs://hadithiai-media
```

### Port already in use

Change the port:

```bash
PORT=8081 python main.py
```

Or on Windows PowerShell:

```powershell
$env:PORT = "8081"
python main.py
```

### Tests fail with import errors

Run tests from the **project root**, not from `src/`:

```bash
cd hadithiAI_orchestrator
python -m pytest tests/ -v
```

### Docker build fails locally

```bash
docker build -t hadithiai-local .
docker run -p 8080:8080 --env-file .env hadithiai-local
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Activate venv (Windows) | `.venv\Scripts\activate` |
| Activate venv (Linux/Mac) | `source .venv/bin/activate` |
| Install dependencies | `pip install -r requirements.txt` |
| Run locally | `cd src && python main.py` |
| Run tests | `python -m pytest tests/ -v` |
| Deploy (Windows) | `$env:GOOGLE_CLOUD_PROJECT="your-id"; .\scripts\deploy.ps1` |
| Deploy (Linux/Mac) | `GOOGLE_CLOUD_PROJECT=your-id ./scripts/deploy.sh` |
| View logs | `gcloud run services logs read hadithiai-gateway --region=us-central1` |

---

*For the full architecture documentation, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).*
