-- ============================================================================
-- Red TeleCorp (PulsoRed) - Esquema Completo BigQuery y BigQuery ML
-- Dataset: telecorp_analytics
-- ============================================================================

-- 1. Catálogo Geoespacial de Zonas y Antenas (RF-GCP-12)
CREATE TABLE IF NOT EXISTS `telecorp_analytics.dim_zones_antennas` (
  antenna_id STRING NOT NULL,
  zone_id STRING NOT NULL,
  zone_name STRING NOT NULL,
  city STRING NOT NULL,
  zone_type STRING NOT NULL,
  antenna_lat FLOAT64 NOT NULL,
  antenna_lng FLOAT64 NOT NULL,
  antenna_model STRING,
  critical_sites STRING
)
CLUSTER BY zone_id, antenna_id;

-- 2. Tabla Histórica de Telemetría de Antenas (RF-GCP-01, RF-GCP-04)
CREATE TABLE IF NOT EXISTS `telecorp_analytics.telemetry_metrics` (
  event_id STRING NOT NULL,
  antenna_id STRING NOT NULL,
  zone_id STRING NOT NULL,
  traffic_gb_min FLOAT64 NOT NULL,
  status STRING NOT NULL, -- INHABILITADA (0), CAIDA (0-60), NORMAL (60-129), SATURADA (>129)
  occurred_at TIMESTAMP NOT NULL,
  ingested_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(occurred_at)
CLUSTER BY zone_id, antenna_id;

-- 3. Tabla de Cambios de Estado y Eventos de Infraestructura (RF-GCP-01, RF-GCP-04)
CREATE TABLE IF NOT EXISTS `telecorp_analytics.antenna_events` (
  event_id STRING NOT NULL,
  antenna_id STRING NOT NULL,
  zone_id STRING NOT NULL,
  event_type STRING NOT NULL, -- falla, degradacion, saturacion, recuperacion, preventiva, estructural, prolongada
  previous_status STRING,
  new_status STRING NOT NULL,
  severity STRING NOT NULL,   -- CRITICA, ALTA, MEDIA, INFO
  traffic_gb_min FLOAT64,
  first_anomaly_at TIMESTAMP,
  occurred_at TIMESTAMP NOT NULL,
  ingested_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(occurred_at)
CLUSTER BY zone_id, antenna_id;

-- 4. Tabla de Incidencias y Reportes Agregados (RF-GCP-02, RF-GCP-04)
CREATE TABLE IF NOT EXISTS `telecorp_analytics.incidents` (
  event_id STRING NOT NULL,
  incident_id STRING NOT NULL,
  incident_type STRING NOT NULL, -- INDIVIDUAL, COLECTIVA, INFRAESTRUCTURA, PREVENTIVA, ESTRUCTURAL
  zone_id STRING NOT NULL,
  category STRING NOT NULL,
  priority STRING NOT NULL,      -- CRITICA, ALTA, MEDIA, BAJA
  status STRING NOT NULL,        -- REGISTRADA, EN_REVISION, EN_ATENCION, SOLUCIONADA, CERRADA
  affected_users INT64 NOT NULL,
  detection_source STRING,       -- ANTENA, USUARIOS, AMBOS
  detection_time_sec INT64,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  resolved_at TIMESTAMP
)
PARTITION BY DATE(created_at)
CLUSTER BY zone_id, status;

-- 5. Tabla de Predicciones de Fallas - BigQuery ML (RF-GCP-15, RF-GCP-16)
CREATE TABLE IF NOT EXISTS `telecorp_analytics.failure_predictions` (
  prediction_id STRING NOT NULL,
  antenna_id STRING NOT NULL,
  zone_id STRING NOT NULL,
  failure_probability FLOAT64 NOT NULL,
  predicted_state STRING NOT NULL, -- CAIDA, SATURADA, NORMAL
  horizon_hours INT64 NOT NULL,
  model_version STRING NOT NULL,
  predicted_at TIMESTAMP NOT NULL,
  valid_until TIMESTAMP NOT NULL
)
PARTITION BY DATE(predicted_at)
CLUSTER BY zone_id, antenna_id;

-- 6. Tabla de Rechazos / Dead-Letter Analítico (RF-GCP-03)
CREATE TABLE IF NOT EXISTS `telecorp_analytics.ingest_rejections` (
  rejection_id STRING NOT NULL,
  endpoint STRING NOT NULL,
  error_reason STRING NOT NULL,
  raw_payload STRING NOT NULL,
  rejected_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(rejected_at);

-- ============================================================================
-- VISTAS ANALÍTICAS (Nivel Plata / Oro - RF-GCP-05 al RF-GCP-12)
-- ============================================================================

-- Vista 1: Zonas Críticas, Duración Activa (MTTR) y Tiempo de Detección (RF-GCP-07, RF-GCP-12)
CREATE OR REPLACE VIEW `telecorp_analytics.vw_critical_zones` AS
WITH zone_meta AS (
  SELECT DISTINCT zone_id, zone_name, city, zone_type, critical_sites,
         AVG(antenna_lat) AS zone_lat, AVG(antenna_lng) AS zone_lng
  FROM `telecorp_analytics.dim_zones_antennas`
  GROUP BY zone_id, zone_name, city, zone_type, critical_sites
),
incident_stats AS (
  SELECT
    zone_id,
    COUNT(DISTINCT incident_id) AS total_incidents,
    COUNTIF(incident_type = 'COLECTIVA') AS collective_incidents,
    SUM(affected_users) AS total_affected_users,
    ROUND(AVG(TIMESTAMP_DIFF(COALESCE(resolved_at, CURRENT_TIMESTAMP()), created_at, MINUTE)), 1) AS avg_active_duration_min,
    ROUND(AVG(detection_time_sec), 1) AS avg_detection_time_sec
  FROM `telecorp_analytics.incidents`
  GROUP BY zone_id
)
SELECT
  z.zone_id,
  z.zone_name,
  z.city,
  z.zone_type,
  z.critical_sites,
  z.zone_lat,
  z.zone_lng,
  CONCAT(CAST(ROUND(z.zone_lat, 6) AS STRING), ',', CAST(ROUND(z.zone_lng, 6) AS STRING)) AS zone_coords,
  COALESCE(i.total_incidents, 0) AS total_incidents,
  COALESCE(i.collective_incidents, 0) AS collective_incidents,
  COALESCE(i.total_affected_users, 0) AS total_affected_users,
  COALESCE(i.avg_active_duration_min, 0.0) AS avg_active_duration_min,
  COALESCE(i.avg_detection_time_sec, 0.0) AS avg_detection_time_sec
FROM zone_meta z
LEFT JOIN incident_stats i USING (zone_id);

-- Vista 2: Antenas Problemáticas y Fallas Estructurales por Falsa Recuperación (RF-GCP-08, RF-GCP-09)
CREATE OR REPLACE VIEW `telecorp_analytics.vw_problematic_antennas` AS
WITH daily_anomalies AS (
  SELECT
    antenna_id,
    zone_id,
    COUNTIF(status IN ('CAIDA', 'INHABILITADA')) AS failure_readings,
    COUNTIF(status = 'SATURADA') AS saturation_readings,
    COUNT(DISTINCT CASE WHEN status != 'NORMAL' AND occurred_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY) THEN DATE(occurred_at) END) AS distinct_failure_days_7d,
    COUNT(DISTINCT CASE WHEN status != 'NORMAL' AND occurred_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY) THEN DATE(occurred_at) END) AS distinct_failure_days_30d,
    ROUND(AVG(traffic_gb_min), 2) AS avg_traffic_gb_min
  FROM `telecorp_analytics.telemetry_metrics`
  GROUP BY antenna_id, zone_id
)
SELECT
  d.antenna_id,
  d.zone_id,
  m.zone_name,
  m.zone_type,
  m.antenna_lat,
  m.antenna_lng,
  d.failure_readings,
  d.saturation_readings,
  d.distinct_failure_days_7d,
  d.distinct_failure_days_30d,
  d.avg_traffic_gb_min,
  CASE
    WHEN d.distinct_failure_days_7d >= 3 OR d.distinct_failure_days_30d >= 5 THEN TRUE
    ELSE FALSE
  END AS is_structural_failure_candidate
FROM daily_anomalies d
LEFT JOIN `telecorp_analytics.dim_zones_antennas` m USING (antenna_id, zone_id);

-- Vista 3: Correlación entre Métricas de Antena e Incidencias de Usuarios (RF-GCP-05, RF-GCP-10)
CREATE OR REPLACE VIEW `telecorp_analytics.vw_correlation_infra_incidents` AS
WITH hourly_telemetry AS (
  SELECT
    zone_id,
    TIMESTAMP_TRUNC(occurred_at, HOUR) AS hour_bucket,
    ROUND(AVG(traffic_gb_min), 2) AS avg_traffic_gb_min,
    ROUND(MIN(traffic_gb_min), 2) AS min_traffic_gb_min,
    COUNTIF(status != 'NORMAL') AS abnormal_samples
  FROM `telecorp_analytics.telemetry_metrics`
  GROUP BY zone_id, hour_bucket
),
hourly_incidents AS (
  SELECT
    zone_id,
    TIMESTAMP_TRUNC(created_at, HOUR) AS hour_bucket,
    COUNT(DISTINCT incident_id) AS reported_incidents,
    SUM(affected_users) AS affected_users_sum
  FROM `telecorp_analytics.incidents`
  GROUP BY zone_id, hour_bucket
)
SELECT
  t.zone_id,
  t.hour_bucket,
  t.avg_traffic_gb_min,
  t.min_traffic_gb_min,
  t.abnormal_samples,
  COALESCE(i.reported_incidents, 0) AS reported_incidents,
  COALESCE(i.affected_users_sum, 0) AS affected_users_sum
FROM hourly_telemetry t
LEFT JOIN hourly_incidents i USING (zone_id, hour_bucket);

-- Vista 4: Preparación de Features para Machine Learning (RF-GCP-13)
CREATE OR REPLACE VIEW `telecorp_analytics.vw_ml_features` AS
WITH base_windows AS (
  SELECT
    t.antenna_id,
    t.zone_id,
    t.occurred_at,
    t.traffic_gb_min,
    t.status,
    AVG(t.traffic_gb_min) OVER (PARTITION BY t.antenna_id ORDER BY UNIX_SECONDS(t.occurred_at) RANGE BETWEEN 3600 PRECEDING AND CURRENT ROW) AS rolling_avg_1h,
    STDDEV(t.traffic_gb_min) OVER (PARTITION BY t.antenna_id ORDER BY UNIX_SECONDS(t.occurred_at) RANGE BETWEEN 3600 PRECEDING AND CURRENT ROW) AS rolling_stddev_1h,
    COUNTIF(t.status IN ('CAIDA', 'INHABILITADA')) OVER (PARTITION BY t.antenna_id ORDER BY UNIX_SECONDS(t.occurred_at) RANGE BETWEEN 604800 PRECEDING AND 60 PRECEDING) AS prior_drops_7d,
    COUNTIF(t.status = 'SATURADA') OVER (PARTITION BY t.antenna_id ORDER BY UNIX_SECONDS(t.occurred_at) RANGE BETWEEN 604800 PRECEDING AND 60 PRECEDING) AS prior_saturations_7d,
    LEAD(t.status, 1) OVER (PARTITION BY t.antenna_id ORDER BY t.occurred_at) AS next_status
  FROM `telecorp_analytics.telemetry_metrics` t
)
SELECT
  antenna_id,
  zone_id,
  occurred_at,
  ROUND(COALESCE(rolling_avg_1h, traffic_gb_min), 2) AS rolling_avg_1h,
  ROUND(COALESCE(rolling_stddev_1h, 0.0), 2) AS rolling_stddev_1h,
  prior_drops_7d,
  prior_saturations_7d,
  EXTRACT(HOUR FROM occurred_at) AS hour_of_day,
  CASE WHEN next_status IN ('CAIDA', 'INHABILITADA', 'SATURADA') THEN 1 ELSE 0 END AS label_will_fail
FROM base_windows
WHERE next_status IS NOT NULL;
