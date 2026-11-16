terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.30.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 5.30.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

# ============================================================================
# 1. Capa de Almacenamiento y Analítica: BigQuery Dataset y Tablas Bronce
# ============================================================================

resource "google_bigquery_dataset" "telecorp" {
  dataset_id                  = var.dataset_id
  friendly_name               = "Red TeleCorp Analytics & ML"
  description                 = "Data Warehouse Serverless y BigQuery ML para Red TeleCorp"
  location                    = "US"
  delete_contents_on_destroy  = true
}

resource "google_bigquery_table" "telemetry_metrics" {
  dataset_id          = google_bigquery_dataset.telecorp.dataset_id
  table_id            = "telemetry_metrics"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "occurred_at"
  }

  clustering = ["zone_id", "antenna_id"]

  schema = jsonencode([
    { name = "event_id", type = "STRING", mode = "REQUIRED" },
    { name = "antenna_id", type = "STRING", mode = "REQUIRED" },
    { name = "zone_id", type = "STRING", mode = "REQUIRED" },
    { name = "traffic_gb_min", type = "FLOAT64", mode = "REQUIRED" },
    { name = "status", type = "STRING", mode = "REQUIRED" },
    { name = "occurred_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "ingested_at", type = "TIMESTAMP", mode = "REQUIRED" }
  ])
}

resource "google_bigquery_table" "antenna_events" {
  dataset_id          = google_bigquery_dataset.telecorp.dataset_id
  table_id            = "antenna_events"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "occurred_at"
  }

  clustering = ["zone_id", "antenna_id"]

  schema = jsonencode([
    { name = "event_id", type = "STRING", mode = "REQUIRED" },
    { name = "antenna_id", type = "STRING", mode = "REQUIRED" },
    { name = "zone_id", type = "STRING", mode = "REQUIRED" },
    { name = "event_type", type = "STRING", mode = "REQUIRED" },
    { name = "previous_status", type = "STRING", mode = "NULLABLE" },
    { name = "new_status", type = "STRING", mode = "REQUIRED" },
    { name = "severity", type = "STRING", mode = "REQUIRED" },
    { name = "traffic_gb_min", type = "FLOAT64", mode = "NULLABLE" },
    { name = "first_anomaly_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "occurred_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "ingested_at", type = "TIMESTAMP", mode = "REQUIRED" }
  ])
}

resource "google_bigquery_table" "incidents" {
  dataset_id          = google_bigquery_dataset.telecorp.dataset_id
  table_id            = "incidents"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "created_at"
  }

  clustering = ["zone_id", "status"]

  schema = jsonencode([
    { name = "event_id", type = "STRING", mode = "REQUIRED" },
    { name = "incident_id", type = "STRING", mode = "REQUIRED" },
    { name = "incident_type", type = "STRING", mode = "REQUIRED" },
    { name = "zone_id", type = "STRING", mode = "REQUIRED" },
    { name = "category", type = "STRING", mode = "REQUIRED" },
    { name = "priority", type = "STRING", mode = "REQUIRED" },
    { name = "status", type = "STRING", mode = "REQUIRED" },
    { name = "affected_users", type = "INT64", mode = "REQUIRED" },
    { name = "detection_source", type = "STRING", mode = "NULLABLE" },
    { name = "detection_time_sec", type = "INT64", mode = "NULLABLE" },
    { name = "created_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "updated_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "resolved_at", type = "TIMESTAMP", mode = "NULLABLE" }
  ])
}

resource "google_bigquery_table" "failure_predictions" {
  dataset_id          = google_bigquery_dataset.telecorp.dataset_id
  table_id            = "failure_predictions"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "predicted_at"
  }

  clustering = ["zone_id", "antenna_id"]

  schema = jsonencode([
    { name = "prediction_id", type = "STRING", mode = "REQUIRED" },
    { name = "antenna_id", type = "STRING", mode = "REQUIRED" },
    { name = "zone_id", type = "STRING", mode = "REQUIRED" },
    { name = "failure_probability", type = "FLOAT64", mode = "REQUIRED" },
    { name = "predicted_state", type = "STRING", mode = "REQUIRED" },
    { name = "horizon_hours", type = "INT64", mode = "REQUIRED" },
    { name = "model_version", type = "STRING", mode = "REQUIRED" },
    { name = "predicted_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "valid_until", type = "TIMESTAMP", mode = "REQUIRED" }
  ])
}

resource "google_bigquery_table" "ingest_rejections" {
  dataset_id          = google_bigquery_dataset.telecorp.dataset_id
  table_id            = "ingest_rejections"
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "rejected_at"
  }

  schema = jsonencode([
    { name = "rejection_id", type = "STRING", mode = "REQUIRED" },
    { name = "endpoint", type = "STRING", mode = "REQUIRED" },
    { name = "error_reason", type = "STRING", mode = "REQUIRED" },
    { name = "raw_payload", type = "STRING", mode = "REQUIRED" },
    { name = "rejected_at", type = "TIMESTAMP", mode = "REQUIRED" }
  ])
}

# ============================================================================
# 2. Capa de Ingesta Desacoplada: Cloud Pub/Sub + DLQ + Suscripciones a BigQuery
# ============================================================================

resource "google_pubsub_topic" "telemetry" {
  name = "telecorp-telemetry-topic"
}

resource "google_pubsub_topic" "events" {
  name = "telecorp-events-topic"
}

resource "google_pubsub_topic" "incidents" {
  name = "telecorp-incidents-topic"
}

resource "google_pubsub_topic" "dlq" {
  name = "telecorp-ingest-dlq-topic"
}

data "google_project" "current" {}

resource "google_bigquery_table_iam_member" "pubsub_bq_telemetry" {
  dataset_id = google_bigquery_dataset.telecorp.dataset_id
  table_id   = google_bigquery_table.telemetry_metrics.table_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_bigquery_table_iam_member" "pubsub_bq_events" {
  dataset_id = google_bigquery_dataset.telecorp.dataset_id
  table_id   = google_bigquery_table.antenna_events.table_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_bigquery_table_iam_member" "pubsub_bq_incidents" {
  dataset_id = google_bigquery_dataset.telecorp.dataset_id
  table_id   = google_bigquery_table.incidents.table_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_pubsub_subscription" "telemetry_to_bq" {
  name  = "telecorp-telemetry-bq-sub"
  topic = google_pubsub_topic.telemetry.id

  bigquery_config {
    table = "${var.project_id}:${google_bigquery_dataset.telecorp.dataset_id}.${google_bigquery_table.telemetry_metrics.table_id}"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dlq.id
    max_delivery_attempts = 5
  }

  depends_on = [google_bigquery_table_iam_member.pubsub_bq_telemetry]
}

resource "google_pubsub_subscription" "events_to_bq" {
  name  = "telecorp-events-bq-sub"
  topic = google_pubsub_topic.events.id

  bigquery_config {
    table = "${var.project_id}:${google_bigquery_dataset.telecorp.dataset_id}.${google_bigquery_table.antenna_events.table_id}"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dlq.id
    max_delivery_attempts = 5
  }

  depends_on = [google_bigquery_table_iam_member.pubsub_bq_events]
}

resource "google_pubsub_subscription" "incidents_to_bq" {
  name  = "telecorp-incidents-bq-sub"
  topic = google_pubsub_topic.incidents.id

  bigquery_config {
    table = "${var.project_id}:${google_bigquery_dataset.telecorp.dataset_id}.${google_bigquery_table.incidents.table_id}"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dlq.id
    max_delivery_attempts = 5
  }

  depends_on = [google_bigquery_table_iam_member.pubsub_bq_incidents]
}

# ============================================================================
# 3. Servicio Serverless en Cloud Run (Escala a Cero: min_instance_count = 0)
# ============================================================================

resource "google_cloud_run_v2_service" "analytics_api" {
  name     = "telecorp-analytics-api"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    scaling {
      min_instance_count = 0
      max_instance_count = 4
    }

    containers {
      image = var.container_image

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "BQ_DATASET"
        value = google_bigquery_dataset.telecorp.dataset_id
      }
      env {
        name  = "PUBSUB_TELEMETRY_TOPIC"
        value = google_pubsub_topic.telemetry.name
      }
      env {
        name  = "PUBSUB_EVENTS_TOPIC"
        value = google_pubsub_topic.events.name
      }
      env {
        name  = "PUBSUB_INCIDENTS_TOPIC"
        value = google_pubsub_topic.incidents.name
      }
      env {
        name  = "PUBSUB_DLQ_TOPIC"
        value = google_pubsub_topic.dlq.name
      }
      env {
        name  = "AWS_PREDICTIONS_WEBHOOK_URL"
        value = var.aws_predictions_webhook_url
      }
      env {
        name  = "AWS_API_KEY"
        value = var.aws_api_key
      }
    }
  }
}

# ============================================================================
# 4. Puerta de Enlace: Google Cloud API Gateway (x-api-key)
# ============================================================================

resource "google_service_account" "apigw_sa" {
  account_id   = "telecorp-apigw-invoker"
  display_name = "API Gateway Invoker for TeleCorp Cloud Run"
}

resource "google_cloud_run_v2_service_iam_member" "apigw_invoker" {
  name     = google_cloud_run_v2_service.analytics_api.name
  location = google_cloud_run_v2_service.analytics_api.location
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.apigw_sa.email}"
}

resource "google_api_gateway_api" "telecorp_gw_api" {
  provider = google-beta
  api_id   = "telecorp-analytics-gw"
}

resource "google_api_gateway_api_config" "telecorp_gw_cfg" {
  provider      = google-beta
  api           = google_api_gateway_api.telecorp_gw_api.api_id
  api_config_id = "telecorp-cfg-v1"

  openapi_documents {
    document {
      path = "openapi.json"
      contents = base64encode(replace(
        file("${path.module}/../gateway/openapi.json"),
        "https://telecorp-analytics-api-placeholder.a.run.app",
        google_cloud_run_v2_service.analytics_api.uri
      ))
    }
  }

  gateway_config {
    backend_config {
      google_service_account = google_service_account.apigw_sa.email
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_api_gateway_gateway" "telecorp_gw" {
  provider   = google-beta
  api_config = google_api_gateway_api_config.telecorp_gw_cfg.id
  gateway_id = "telecorp-gateway"
  region     = var.region
}

# ============================================================================
# 5. Orquestación Periódica de Predicciones (Cada 15 minutos): Cloud Scheduler
# ============================================================================

resource "google_cloud_scheduler_job" "run_predictions_15m" {
  name        = "telecorp-bqml-predictions-15m"
  description = "Ejecuta cada 15 min la predicción de fallas en BigQuery ML y envía prob >= 0.50 a AWS"
  schedule    = "*/15 * * * *"
  time_zone   = "UTC"
  region      = var.region

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.analytics_api.uri}/predictions/run"
    oidc_token {
      service_account_email = google_service_account.apigw_sa.email
    }
  }
}
