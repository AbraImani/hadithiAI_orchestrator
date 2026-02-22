# ─────────────────────────────────────────────────
# HadithiAI Live — Terraform Infrastructure
# Google Cloud deployment configuration
# ─────────────────────────────────────────────────

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  # Optional: Remote state in GCS
  # backend "gcs" {
  #   bucket = "hadithiai-terraform-state"
  #   prefix = "terraform/state"
  # }
}

# ─── Variables ────────────────────────────────────

variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "GCP Region"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Environment (dev, staging, prod)"
  type        = string
  default     = "dev"
}

# ─── Provider ─────────────────────────────────────

provider "google" {
  project = var.project_id
  region  = var.region
}

# ─── Enable Required APIs ─────────────────────────

resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "firestore.googleapis.com",
    "storage.googleapis.com",
    "aiplatform.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "cloudtrace.googleapis.com",
    "artifactregistry.googleapis.com",
  ])

  project = var.project_id
  service = each.value

  disable_dependent_services = false
  disable_on_destroy         = false
}

# ─── Firestore Database ──────────────────────────

resource "google_firestore_database" "main" {
  project     = var.project_id
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  depends_on = [google_project_service.apis]
}

# Firestore TTL policy for session cleanup
resource "google_firestore_field" "session_ttl" {
  project    = var.project_id
  database   = google_firestore_database.main.name
  collection = "sessions"
  field      = "expires_at"

  ttl_config {}

  depends_on = [google_firestore_database.main]
}

# ─── Cloud Storage Bucket ────────────────────────

resource "google_storage_bucket" "media" {
  name          = "${var.project_id}-hadithiai-media"
  location      = var.region
  force_destroy = true # Hackathon convenience — remove for production

  uniform_bucket_level_access = true

  cors {
    origin          = ["*"]
    method          = ["GET", "HEAD"]
    response_header = ["Content-Type"]
    max_age_seconds = 3600
  }

  lifecycle_rule {
    condition {
      age = 7 # Delete generated images after 7 days
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.apis]
}

# Make bucket publicly readable (for serving generated images)
resource "google_storage_bucket_iam_member" "public_read" {
  bucket = google_storage_bucket.media.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}

# ─── Artifact Registry (Docker images) ───────────

resource "google_artifact_registry_repository" "docker" {
  location      = var.region
  repository_id = "hadithiai"
  format        = "DOCKER"
  description   = "HadithiAI Live Docker images"

  depends_on = [google_project_service.apis]
}

# ─── Cloud Run Service ───────────────────────────

resource "google_cloud_run_v2_service" "gateway" {
  name     = "hadithiai-gateway"
  location = var.region

  template {
    # ── Scaling ──
    scaling {
      min_instance_count = 1  # Always warm
      max_instance_count = 10
    }

    # ── Execution environment ──
    execution_environment = "EXECUTION_ENVIRONMENT_GEN2"
    session_affinity      = true  # Sticky sessions for WebSocket

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/hadithiai/gateway:latest"

      # ── Resources ──
      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
        cpu_idle = false  # CPU always allocated (for WebSocket)
        startup_cpu_boost = true
      }

      # ── Environment Variables ──
      env {
        name  = "HADITHI_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "HADITHI_REGION"
        value = var.region
      }
      env {
        name  = "HADITHI_MEDIA_BUCKET"
        value = google_storage_bucket.media.name
      }
      env {
        name  = "HADITHI_DEBUG"
        value = var.environment == "dev" ? "true" : "false"
      }
      env {
        name  = "HADITHI_LOG_LEVEL"
        value = var.environment == "dev" ? "DEBUG" : "INFO"
      }

      # ── Ports ──
      ports {
        container_port = 8080
      }

      # ── Health Checks ──
      startup_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        initial_delay_seconds = 5
        period_seconds        = 3
        failure_threshold     = 10
      }

      liveness_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        period_seconds    = 30
        failure_threshold = 3
      }
    }

    # ── Timeout (1 hour for long conversations) ──
    timeout = "3600s"

    # ── Max concurrent requests per instance ──
    max_instance_request_concurrency = 80
  }

  # ── Traffic (100% to latest) ──
  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  depends_on = [
    google_project_service.apis,
    google_artifact_registry_repository.docker,
  ]
}

# ── Public Access (no auth required for hackathon demo) ──

resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.gateway.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ─── Outputs ──────────────────────────────────────

output "service_url" {
  description = "Cloud Run service URL"
  value       = google_cloud_run_v2_service.gateway.uri
}

output "websocket_url" {
  description = "WebSocket endpoint URL"
  value       = "wss://${replace(google_cloud_run_v2_service.gateway.uri, "https://", "")}/ws"
}

output "media_bucket" {
  description = "Cloud Storage bucket for media"
  value       = google_storage_bucket.media.name
}

output "firestore_database" {
  description = "Firestore database name"
  value       = google_firestore_database.main.name
}
