# PulsoRed — Requerimientos funcionales (v3, alineados con PLAN.md)

> Documento **interno**. No subir al repo de entrega (el enunciado anula informes). Reemplaza las versiones anteriores.
> **P**: **M** = MVP obligatorio · **E** = extra (solo con el MVP estable).

## 0. Principio de diseño y reparto

**Principio:** cada nube posee un *contexto autónomo*. La ruta crítica de cada historia de usuario vive **dentro de una sola nube**; entre nubes solo viajan **eventos asíncronos** con reintentos, y cada dependencia externa tiene **degradación definida**. Así, si cae una nube (o el Lab de AWS se apaga), las otras siguen sirviendo.

| Nube | Contexto | Por qué esta nube |
|---|---|---|
| **AWS** | **Infraestructura y automatización**: telemetría, estado de antenas, eventos, **Step Functions**, recepción de predicciones, secretos y configuración global | Step Functions es requisito de la rúbrica; Secrets Manager/SSM es requisito del enunciado; DynamoDB on-demand + Lambda + SQS absorben ráfagas de telemetría y escalan a cero |
| **Azure** | **Cliente e incidencias**: portal, reportes, incidencias colectivas, correlación, priorización, notificaciones, consola | Vive **fuera del Lab de AWS** (que se apaga/pierde créditos): los usuarios siguen reportando y recibiendo avisos. Cosmos DB (change feed + free tier), Functions en consumo, Static Web Apps con auth, Communication Services (email + SMS con una sola API) |
| **GCP** | **Inteligencia y analítica**: predicción de fallas, IA generativa, histórico, dashboards | Es donde la IA y los datos son más fuertes y más baratos: BigQuery + **BigQuery ML**, Vertex AI (Gemini, embeddings), Looker Studio gratis |

### Decisiones de dominio

| Tema | Decisión |
|---|---|
| Zona | **Catálogo único** `contracts/zones.json` versionado en el repo (~12 zonas, ~5 antenas c/u): `zone_id`, nombre, tipo (`RESIDENCIAL`, `COMERCIAL`, `INDUSTRIAL`, `EDUCATIVA`, `SALUD`, `CRITICA`), centroide, sitios críticos |
| Antena | `== 0` INHABILITADA · `0 < x < 60` CAIDA · `60 ≤ x ≤ 129` NORMAL · `x > 129` SATURADA |
| Incidente colectivo | ≥ 10 usuarios **distintos**, misma zona, ventana deslizante **< 24 h**. Cualquier tipo de problema cuenta; modo configurable `ZONA` (defecto) o `ZONA_Y_CATEGORIA` |
| Anti-ruido | Anomalía confirmada tras **2 muestras** consecutivas; recuperación tras **3 muestras NORMAL**; intermitencia = ≥ 4 cambios en 30 min |
| Severidad | INHABILITADA → CRITICA · CAIDA → ALTA · SATURADA → MEDIA · preventiva → MEDIA (p ≥ 0.9: ALTA) · recuperación → INFO |
| Alerta preventiva | Predicción con `probabilidad ≥ 0.70` y `horizonte ≤ 24 h` (configurable) |
| Recurrencia | ≥ 3 episodios en 7 días o ≥ 5 en 30 días → problema estructural |
| Tipos de incidencia | `COLECTIVA`, `INFRAESTRUCTURA`, `PREVENTIVA`, `ESTRUCTURAL` |
| Estados | `REGISTRADA → EN_REVISION → EN_ATENCION → SOLUCIONADA → CERRADA` (+ reapertura). Etiquetas en la interfaz como el enunciado: Registrada, En revisión, **Siendo atendida**, Solucionada, Cerrada |
| Configuración global | Fuente única en AWS (SSM); Azure y GCP la leen con caché y valores por defecto |

---

## 1. Módulo AWS — Infraestructura y automatización

### A. Ingesta y estado de antenas
| ID | Requerimiento | P |
|---|---|---|
| RF-AWS-01 | Recibir periódicamente métricas de tráfico (GB/min) por antena, individualmente o por lote, mediante API autenticada. | M |
| RF-AWS-02 | Identificar cada antena con ID único y asociarla a su zona según el catálogo único. | M |
| RF-AWS-03 | Validar cada métrica (antena existente, valor numérico ≥ 0, marca temporal razonable); registrar las rechazadas. | M |
| RF-AWS-04 | Clasificar el estado de la antena con los umbrales de la configuración global. | M |
| RF-AWS-05 | Almacenar métricas con antena y marca temporal (datos recientes con expiración automática; histórico completo en GCP). | M |
| RF-AWS-06 | Registrar cada cambio de estado: anterior, nuevo y momento. | M |
| RF-AWS-07 | Consultar estado actual y métricas recientes por antena y por zona. | M |
| RF-AWS-08 | **Simulador de telemetría** con escenarios: normal, caída, saturación, apagón de zona, intermitente y **degradación progresiva previa a una falla**; se activa y desactiva por API/CLI. | M |

### B. Alertas y eventos
| ID | Requerimiento | P |
|---|---|---|
| RF-AWS-09 | Detectar condiciones anormales: cambio de estado confirmado, saturación prolongada (≥ 5 min), intermitencia; con anti-ruido. | M |
| RF-AWS-10 | Asignar severidad a cada evento según la tabla de decisiones. | M |
| RF-AWS-11 | Emitir a Azure los eventos de infraestructura (antena, zona, métricas, estado, severidad, `first_anomaly_at`, tipo: falla, degradación, saturación, recuperación, preventiva, estructural, prolongada) con el sobre común (§4). | M |
| RF-AWS-12 | **Outbox durable**: conservar los eventos pendientes si Azure/GCP no responden, reintentar con espera creciente, aislar los fallidos en una cola de fallos y reenviarlos al restablecerse la comunicación sin duplicados. | M |

### C. Orquestación (Step Functions)
| ID | Requerimiento | P |
|---|---|---|
| RF-AWS-13 | **`antenna-incident-lifecycle`**: al confirmar una anomalía, enriquecer con contexto de zona, emitir el evento, esperar y verificar la recuperación (3 muestras NORMAL), emitir la recuperación y escalar si supera un tiempo máximo. Reintentos y manejo de errores. | M |
| RF-AWS-14 | **`preventive-alert`**: ante una predicción, evaluar el riesgo, evitar duplicados vigentes, registrar la alerta y emitirla. | M |
| RF-AWS-15 | **`recurrence-analysis`** (programado cada noche): evaluar episodios por antena (`Map`) y emitir evento **estructural** sin duplicar uno abierto. | M |
| RF-AWS-16 | **`event-redelivery`** (programado y manual): reprocesar la cola de fallos hacia Azure/GCP. | M |

### D. Escalabilidad
| ID | Requerimiento | P |
|---|---|---|
| RF-AWS-17 | Procesar métricas de múltiples antenas de forma concurrente y por lotes. | M |
| RF-AWS-18 | Adaptarse a picos (apagón regional, catástrofe) sin perder métricas: capacidad bajo demanda, colas, limitación de tasa, respuesta `429` con `Retry-After`. | M |

### E. Integración con IA (GCP)
| ID | Requerimiento | P |
|---|---|---|
| RF-AWS-19 | Enviar a GCP, en lotes periódicos e idempotentes, las métricas y los cambios de estado. Si GCP no responde, acumular y reintentar. | M |
| RF-AWS-20 | Recibir predicciones por antena (probabilidad, horizonte, `model_version`, marca temporal), validarlas y almacenarlas. | M |
| RF-AWS-21 | Generar alertas preventivas cuando cumplan las condiciones de riesgo. | M |
| RF-AWS-22 | Consultar predicciones **vigentes** (no vencidas) por antena o zona, ordenadas por riesgo. | M |

### F. Secretos, configuración y operación
| ID | Requerimiento | P |
|---|---|---|
| RF-AWS-23 | Guardar las credenciales de integración entre nubes en **Secrets Manager** y la configuración (umbrales, ventanas, pesos) en **SSM Parameter Store**. | M |
| RF-AWS-24 | Exponer `GET /config` versionado (autenticado) para que Azure y GCP lean la configuración global. | M |
| RF-AWS-25 | Alarmas (errores de Lambda, ejecuciones fallidas, cola de fallos no vacía, 5xx) y tablero de salud; avisar a los operadores por SNS. | M |

---

## 2. Módulo Azure — Cliente e incidencias

### A. Reportes de usuarios
| ID | Requerimiento | P |
|---|---|---|
| RF-AZ-01 | Portal público para registrar reportes de problemas del servicio. | M |
| RF-AZ-02 | Capturar: identificación del usuario, teléfono, correo, ubicación aproximada (zona o coordenadas → zona), fecha y hora del inconveniente, tipo/categoría y descripción. | M |
| RF-AZ-03 | Asignar **ID único** por reporte con idempotencia ante reenvíos (clave generada por el cliente). | M |
| RF-AZ-04 | Permitir al usuario consultar estado e historial de su reporte con su código y un dato de verificación. | M |
| RF-AZ-05 | Administrar el estado (Registrada, En revisión, En atención, Solucionada, Cerrada), rechazando transiciones inválidas. | M |
| RF-AZ-06 | Historial inmutable de cambios de estado y acciones, con marca temporal y actor (sistema / IA / operador). | M |
| RF-AZ-07 | Validar entradas y limitar abusos (tasa por usuario/teléfono, duplicados evidentes). | M |

### B. Incidencias colectivas
| ID | Requerimiento | P |
|---|---|---|
| RF-AZ-08 | Identificar reportes relacionados por zona y proximidad temporal (modo configurable). | M |
| RF-AZ-09 | Detectar incidencia colectiva con **≥ 10 usuarios distintos** en la misma zona dentro de una ventana deslizante **< 24 h** (varios reportes del mismo usuario cuentan uno). | M |
| RF-AZ-10 | Agrupar los reportes bajo la incidencia, conservando la relación con los originales. | M |
| RF-AZ-11 | Actualizar usuarios afectados y última actividad con cada nuevo reporte, **sin duplicar incidencias** bajo concurrencia. | M |
| RF-AZ-12 | Reabrir la incidencia si llegan reportes nuevos tras marcarla solucionada. | M |
| RF-AZ-13 | Cerrar automáticamente las incidencias solucionadas tras un periodo sin reportes nuevos. | M |

### C. Integración con infraestructura
| ID | Requerimiento | P |
|---|---|---|
| RF-AZ-14 | Recibir eventos de AWS de forma idempotente, tolerando duplicados y orden alterado (por `occurred_at`), y mantener una **proyección local** del estado de antenas por zona (sin consultar a AWS en la ruta crítica). | M |
| RF-AZ-15 | Correlacionar eventos con reportes e incidencias por zona y ventana de tiempo; registrar el origen de la detección (`ANTENA`, `USUARIOS`, `AMBOS`) y calcular el **tiempo de detección** (primera anomalía vs. primer reporte). | M |
| RF-AZ-16 | Crear/actualizar incidencias a partir de eventos: falla → evidencia; recuperación → propone "Solucionada"; preventiva y estructural → incidencia del tipo correspondiente. | M |
| RF-AZ-17 | Reconciliar periódicamente la proyección de antenas contra `GET /antennas` de AWS (tarea en segundo plano, tolerante a fallos). | E |

### D. Priorización y gestión
| ID | Requerimiento | P |
|---|---|---|
| RF-AZ-18 | Priorizar (CRÍTICA/ALTA/MEDIA/BAJA) con puntaje explicable: usuarios afectados, tipo de zona, sitios críticos (incluida la señal de IA), evidencia de antena, duración y recurrencia; recalcular al cambiar cualquier factor y registrar cada cambio. | M |
| RF-AZ-19 | **Consola de operación**: bandeja por prioridad, filtros, mapa, detalle con línea de tiempo, estado de antenas, riesgos predictivos y resumen de IA. | M |
| RF-AZ-20 | Acciones del operador: confirmar, descartar (falso positivo), cambiar estado o prioridad, con registro. | M |
| RF-AZ-21 | Restringir la consola a operadores autenticados. | M |
| RF-AZ-22 | Actualización en tiempo real de la consola (SignalR); el MVP usa sondeo corto. | E |

### E. Notificaciones
| ID | Requerimiento | P |
|---|---|---|
| RF-AZ-23 | Notificar a los usuarios que reportaron, en tres etapas: **evaluando**, **incidencia identificada en la zona**, **servicio restablecido**. | M |
| RF-AZ-24 | Enviar por **correo electrónico** (Communication Services) y **SMS** con degradación a solo correo si el SMS no está disponible. | M |
| RF-AZ-25 | **Un mensaje por usuario por etapa** (idempotencia), con envío limitado por tasa, reintentos y estado de entrega visible en el historial. | M |
| RF-AZ-26 | Texto del mensaje generado por IA a partir de la plantilla (con plantilla fija como respaldo). | E |

### F. Escalabilidad, alta disponibilidad e integraciones
| ID | Requerimiento | P |
|---|---|---|
| RF-AZ-27 | Absorber **picos de reportes** sin pérdida: la API encola y responde `202`; procesamiento asíncrono e idempotente con reintentos ante `429` de la BD. | M |
| RF-AZ-28 | Respaldo continuo y restauración de reportes e incidencias. | M |
| RF-AZ-29 | **Modo degradado**: si AWS o GCP no responden, se siguen registrando reportes, detectando colectivas y notificando. | M |
| RF-AZ-30 | **Enriquecimiento por IA asíncrono**: tras guardar el reporte, pedir a GCP clasificación/extracción; si falla, clasificar por reglas y marcar la fuente (`ia`/`reglas`). Nunca se envían documento ni teléfono a la IA. | M |
| RF-AZ-31 | Solicitar a GCP el resumen operativo y la hipótesis de causa de una incidencia. | M |
| RF-AZ-32 | Exportar a GCP reportes e incidencias (usuarios afectados, zona, tipo, prioridad, estado, tiempos, tiempo de detección) de forma idempotente y con reintentos. | M |
| RF-AZ-33 | Leer la configuración global de AWS con caché y valores por defecto. | M |

---

## 3. Módulo GCP — Inteligencia y analítica

> **Esta sección está reemplazada por `GCP.md`**, que usa la numeración del README del equipo (RF-GCP-01…20) y añade RF-GCP-21…31. Si hay diferencias, manda `GCP.md` (incluye la tabla de equivalencias de IDs).

### A. Datos
| ID | Requerimiento | P |
|---|---|---|
| RF-GCP-01 | Recibir y almacenar de AWS métricas y cambios de estado (idempotente; filas inválidas a una tabla de rechazos). | M |
| RF-GCP-02 | Recibir y almacenar de Azure reportes e incidencias y su evolución (idempotente). | M |
| RF-GCP-03 | **Histórico sintético de ~30 días** con patrones realistas (incluida degradación previa a fallas), compartido con el simulador de AWS. | M |
| RF-GCP-04 | Modelo de datos particionado por fecha y agrupado por zona/antena para minimizar el costo de consulta. | M |

### B. IA predictiva
| ID | Requerimiento | P |
|---|---|---|
| RF-GCP-05 | Entrenar un modelo de **predicción de falla por antena** (BigQuery ML: tendencia, volatilidad, caídas previas, saturaciones) que devuelva probabilidad y horizonte. | M |
| RF-GCP-06 | Ejecutar la predicción periódicamente (cada ~15 min) sobre la ventana reciente de cada antena. | M |
| RF-GCP-07 | **Registrar** cada predicción (antena, probabilidad, estado previsto, horizonte, `model_version`, marca temporal) y publicar a AWS las que cumplan las condiciones de alerta, con reintentos. | M |
| RF-GCP-08 | Evaluar el modelo (precision/recall en datos reservados) y comparar predicciones contra fallas reales posteriores. | E |
| RF-GCP-09 | **Detección de anomalías no supervisada** (ARIMA_PLUS) sobre el tráfico por antena, como segunda señal. | E |
| RF-GCP-10 | **Pronóstico** de volumen de incidencias por zona para apoyar decisiones operativas. | E |

### C. IA generativa
| ID | Requerimiento | P |
|---|---|---|
| RF-GCP-11 | Clasificar reportes con Gemini: categoría normalizada, urgencia, mención de sitios críticos, salida JSON validada. | M |
| RF-GCP-12 | Generar resumen operativo, hipótesis de causa y acción recomendada de una incidencia. | M |
| RF-GCP-13 | Control de calidad de IA: validación de esquema, tiempo de espera corto, caché, límite de cuota, `model_version` registrado y minimización de datos personales. | M |
| RF-GCP-14 | Redactar el texto de las notificaciones por etapa. | E |
| RF-GCP-15 | **Embeddings** y búsqueda por similitud para agrupar reportes semejantes cuando la ubicación es imprecisa. | E |
| RF-GCP-16 | **Consultas en lenguaje natural** sobre el histórico (NL → SQL, solo lectura, con límites). | E |

### D. Analítica
| ID | Requerimiento | P |
|---|---|---|
| RF-GCP-17 | Vistas: zonas con más incidencias, antenas más problemáticas, tipos más reportados, duración y usuarios por incidencia, frecuencia por elemento, tiempo de detección. | M |
| RF-GCP-18 | Dashboard (Looker Studio) incrustable en la consola y consultable solo por usuarios autorizados. | M |

### E. Cobertura añadida tras revisar el README del módulo GCP del equipo
| ID | Requerimiento | P |
|---|---|---|
| RF-GCP-19 | **Validar los datos recibidos**: estructura, integridad y consistencia (IDs de antenas, zonas e incidencias contra el catálogo único; marcas temporales válidas); lo inválido va a la tabla de rechazos. | M |
| RF-GCP-20 | **Integrar las fuentes** de AWS y Azure en un modelo unificado que relacione métricas de antenas con incidencias por zona y periodo. | M |
| RF-GCP-21 | **Análisis de correlación** entre métricas operativas e incidencias reportadas (p. ej., caída de tráfico vs. aumento de reportes y desfase entre ambos). | M |
| RF-GCP-22 | **Visualización geoespacial**: mapa de antenas e incidencias que muestre zonas críticas y concentraciones. | M |
| RF-GCP-23 | **Preparar los datos para ML**: tabla de características reproducible a partir del histórico de infraestructura e incidencias. | M |
| RF-GCP-24 | **Actualizar y reentrenar** el modelo con datos nuevos, con versión por entrenamiento (MVP: script/consulta bajo demanda; programado como extra). | M |
| RF-GCP-25 | **Exponer las predicciones** para que AWS las consulte (`GET /predictions`), además del envío; cada registro incluye antena, probabilidad, **estado previsto**, horizonte y `model_version`. | M |

---

## 4. Contratos de integración

**Sobre común** (JSON): `event_id` (UUID), `type`, `schema_version`, `source`, `occurred_at` (ISO-8601 UTC), `correlation_id`, `payload`. Entrega **al menos una vez**; todo consumidor es idempotente por `event_id`. Reintentos con espera creciente solo ante 5xx/timeout. Autenticación por API key en cabecera; las claves viven en AWS Secrets Manager.

| ID | Sentido | Medio | Contenido | Si falla |
|---|---|---|---|---|
| INT-01 | AWS → Azure | `POST /events/infra` | eventos de antena | Outbox + DLQ + `event-redelivery` |
| INT-02 | AWS → GCP | `POST /ingest/telemetry`, `/ingest/events` (lotes) | métricas y cambios de estado | Acumular y reintentar |
| INT-03 | GCP → AWS (push) y AWS → GCP (consulta) | `POST /predictions` en AWS · `GET /predictions` en GCP | antena, probabilidad, estado previsto, horizonte, `model_version` | GCP reintenta; sin predicciones solo hay alertas reactivas |
| INT-04 | Azure → GCP | `POST /ai/classify-report`, `/ai/summarize-incident` | texto sin datos personales → JSON | Reglas / sin resumen |
| INT-05 | Azure → GCP | `POST /ingest/incidents` | reportes e incidencias agregadas | Reintento por *change feed* |
| INT-06 | Azure/GCP → AWS | `GET /config` | configuración global | Caché / valores por defecto |
| INT-07 | Azure → AWS | `GET /antennas`, `/predictions` | consulta para la consola y la reconciliación (no ruta crítica) | Consola muestra último dato conocido |

### Dónde vive cada dato (persistencia en ≥ 2 nubes)
| Dato | Principal | Copia |
|---|---|---|
| Métricas y eventos de antena | AWS (DynamoDB, PITR) | Azure (proyección de estado) · GCP (histórico completo) |
| Reportes e incidencias | Azure (Cosmos, respaldo continuo) | GCP (histórico completo) |
| Predicciones | GCP (BigQuery) | AWS (vigentes) |
| Configuración y secretos | AWS (SSM / Secrets Manager) | Caché en Azure y GCP |

---

## 5. Requerimientos no funcionales

| ID | Requerimiento |
|---|---|
| RNF-01 | **100 % serverless**: sin servidores, VPC con NAT ni infraestructura permanente; todo escala a cero. |
| RNF-02 | **Costo mínimo**: créditos del Lab, capas gratuitas de GCP/Azure; simulador apagado salvo pruebas. |
| RNF-03 | **Secretos y variables globales en servicios de AWS**; nada sensible en el repo. |
| RNF-04 | **Persistencia y HA**: PITR (DynamoDB), respaldo continuo (Cosmos), copias entre nubes (§4), colas de fallos en todo flujo asíncrono. |
| RNF-05 | **Degradación elegante**: la caída de una nube no detiene la ruta crítica de otra (IA → reglas; AWS caído → Azure sigue; GCP caído → sin predicciones/IA). |
| RNF-06 | **Idempotencia**: reintentar cualquier operación no duplica datos ni mensajes. |
| RNF-07 | **Objetivos**: reporte aceptado < 3 s · visible < 5 s · colectiva detectada < 60 s tras el 10.º reporte · evento de antena entregado < 60 s · notificación encolada < 30 s. |
| RNF-08 | **Observabilidad**: logs JSON con `correlation_id`, alarmas por nube y tablero de salud. |
| RNF-09 | **Seguridad y privacidad**: validación de entradas, CORS restringido, operadores autenticados, mínimo privilegio, datos personales fuera de prompts de IA. |
| RNF-10 | **Pruebas**: contratos con mocks desde el día 0, script `e2e` por integración y prueba de carga de 2 000 reportes en 60 s sin pérdida. |

---

## 6. Reparto del equipo

| Persona | Responsabilidad | Requerimientos |
|---|---|---|
| **P1** | **AWS** + automatización de pruebas (`e2e`, carga) | RF-AWS-01…25, RNF-10 |
| **P2** | **Azure** backend + portal de usuario + notificaciones | RF-AZ-01…33 |
| **P3** | **GCP** + **consola de operación** (SPA) + diagrama + video | RF-GCP-01…25, RF-AZ-19 |

Justificación del equilibrio: P2 tiene más requerimientos pero son homogéneos (CRUD y reglas); P3 tiene menos, pero el modelo, la IA y la consola son lo más "visible" y lo más expuesto al riesgo; P1 tiene orquestación y pruebas. Quien termine antes ayuda en la tarea bloqueante, no agrega features.

## 7. Cobertura de la rúbrica

| Criterio | Requerimientos |
|---|---|
| Problemas resueltos (0–3) | RF-AZ-01…25, RF-AWS-09…15, RF-GCP-17 |
| Persistencia y HA (0–2) | RNF-04, RF-AWS-12, RF-AZ-28/29, §4 "Dónde vive cada dato" |
| Step Functions (0–2) | RF-AWS-13…16 |
| 3 nubes (0–3) | Contextos autónomos + INT-01…07 |
| Arquitectura (0–3) | Principio de diseño, §4, RNF-05/06/08 |
| Serverless (0–2) | RNF-01 |
| IA (0–3) | RF-GCP-05…07, 11…13 (+ 08…10, 14…16) · RF-AWS-20…22 · RF-AZ-30/31 |
| Pitch (0–2) | Escenarios de demo en PLAN.md §8 |
