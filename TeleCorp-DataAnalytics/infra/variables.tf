variable "project_id" {
  description = "ID de tu único proyecto de Google Cloud"
  type        = string
}

variable "region" {
  description = "Región de despliegue Serverless (compatible con Cloud Run y API Gateway)"
  type        = string
  default     = "us-central1"
}

variable "dataset_id" {
  description = "ID del Dataset en BigQuery"
  type        = string
  default     = "telecorp_analytics"
}

variable "container_image" {
  description = "URL de la imagen Docker del servicio Cloud Run en Artifact Registry / GCR"
  type        = string
}

variable "aws_predictions_webhook_url" {
  description = "URL del endpoint POST /predictions en AWS para alertas preventivas (RF-GCP-19)"
  type        = string
  default     = ""
}

variable "aws_api_key" {
  description = "API Key para autenticar el envío desde GCP hacia AWS"
  type        = string
  default     = ""
  sensitive   = true
}
