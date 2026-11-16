output "api_gateway_url" {
  description = "URL pública del Google Cloud API Gateway (pasar a AWS y Azure)"
  value       = "https://${google_api_gateway_gateway.telecorp_gw.default_hostname}/v1"
}

output "cloud_run_dashboard_url" {
  description = "URL del Dashboard de Data Science y Consola de Analítica"
  value       = google_cloud_run_v2_service.analytics_api.uri
}

output "bigquery_dataset_id" {
  description = "Dataset de BigQuery creado para conectar con Looker Studio"
  value       = google_bigquery_dataset.telecorp.dataset_id
}
