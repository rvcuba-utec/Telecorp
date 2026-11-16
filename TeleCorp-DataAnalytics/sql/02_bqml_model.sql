-- ============================================================================
-- Red TeleCorp - Entrenamiento y Predicción en BigQuery ML (RF-GCP-14 a 17)
-- ============================================================================

-- 1. Entrenar / Actualizar el modelo predictivo de fallas (RF-GCP-14, RF-GCP-17)
CREATE OR REPLACE MODEL `telecorp_analytics.failure_predictor_model`
OPTIONS(
  model_type = 'LOGISTIC_REG',
  input_label_cols = ['label_will_fail'],
  auto_class_weights = TRUE,
  max_iterations = 20
) AS
SELECT
  rolling_avg_1h,
  rolling_stddev_1h,
  prior_drops_7d,
  prior_saturations_7d,
  hour_of_day,
  label_will_fail
FROM `telecorp_analytics.vw_ml_features`;

-- 2. Ejecutar inferencia sobre la ventana reciente y guardar en failure_predictions (RF-GCP-15, RF-GCP-16)
INSERT INTO `telecorp_analytics.failure_predictions` (
  prediction_id,
  antenna_id,
  zone_id,
  failure_probability,
  predicted_state,
  horizon_hours,
  model_version,
  predicted_at,
  valid_until
)
WITH latest_features AS (
  SELECT * EXCEPT(rn)
  FROM (
    SELECT
      f.*,
      ROW_NUMBER() OVER (PARTITION BY f.antenna_id ORDER BY f.occurred_at DESC) AS rn
    FROM `telecorp_analytics.vw_ml_features` f
  )
  WHERE rn = 1
),
predictions AS (
  SELECT
    antenna_id,
    zone_id,
    rolling_avg_1h,
    (SELECT prob FROM UNNEST(predicted_label_will_fail_probs) WHERE label = 1) AS prob_fail
  FROM ML.PREDICT(
    MODEL `telecorp_analytics.failure_predictor_model`,
    TABLE latest_features
  )
)
SELECT
  GENERATE_UUID() AS prediction_id,
  antenna_id,
  zone_id,
  ROUND(prob_fail, 4) AS failure_probability,
  CASE
    WHEN prob_fail >= 0.50 AND rolling_avg_1h > 110 THEN 'SATURADA'
    WHEN prob_fail >= 0.50 THEN 'CAIDA'
    ELSE 'NORMAL'
  END AS predicted_state,
  6 AS horizon_hours,
  'bqml-logreg-v1.0' AS model_version,
  CURRENT_TIMESTAMP() AS predicted_at,
  TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 6 HOUR) AS valid_until
FROM predictions;
