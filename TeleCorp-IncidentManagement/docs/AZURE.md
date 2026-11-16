# Módulo Azure — Especificación (Cliente e incidencias)

> Documento **interno** (se borra `docs/` antes de entregar: el enunciado anula a quien entregue un informe).
> Es el documento pilar del módulo Azure, equivalente a `docs/AWS.md` del módulo AWS. Si contradice a `contracts/`, manda `contracts/`; si contradice a `docs/global/`, manda este archivo para lo que es de Azure.
> Fuentes: `docs/global/REQUISITOS.md` (RF-AZ-01…33), `docs/global/PLAN.md` (v3), `docs/global/GCP.md` (contratos con GCP), `contracts/` (contratos con AWS y GCP).

## 0. Estado y decisiones adoptadas

**Estado (2026-11-15):** el código está **adaptado a los contratos compartidos** y probado en local (56 pruebas, simulación de punta a punta y servidor de desarrollo). **Nada está desplegado.** Las integraciones con AWS y GCP funcionan en modo simulado hasta que se completen sus URLs y claves en `.env` (§10).

Decisiones del equipo que Azure asume (`docs/AWS.md` §0):

| Decisión | Valor | Efecto en Azure |
|---|---|---|
| IA generativa (Gemini) | Sí | Azure llama a `/ai/classify-report` y `/ai/summarize-incident` de GCP |
| Reportes relacionados | Misma zona + tiempo, **cualquier tipo** | `CORRELATION_MODE=ZONA` (por defecto); `ZONA_Y_CATEGORIA` opcional |
| Tipos de evento de AWS | **7**: FALLA, SATURACION, DEGRADACION, RECUPERACION, PREVENTIVA, ESTRUCTURAL, PROLONGADA | Azure acepta los 7 |
| Recurrencia | AWS la detecta y emite ESTRUCTURAL; GCP la analiza | Azure crea una incidencia `ESTRUCTURAL` y la usa en el factor R |

## 1. Alcance

Azure es el **plano de cliente e incidencias**: portal de usuario, reportes, detección de incidencias colectivas, correlación con eventos de antenas, priorización, ciclo de vida, notificaciones y API para la consola. Vive fuera del Lab de AWS para seguir funcionando si este se apaga (RNF-05).

**No hago:** telemetría, estado de antenas, Step Functions, predicciones (AWS) · modelos, analítica, IA generativa en sí (GCP).
**Consola de operación (SPA):** según el reparto la construye P3 (RF-AZ-19). Azure le da los datos por API y el hosting. `frontend/consola.html` es una consola funcional que P3 puede usar de base.

**Principio:** nada de lo mío depende de AWS ni de GCP para funcionar. Si ellos caen, sigo registrando reportes, detectando colectivas y notificando (RF-AZ-29).

## 2. Requisitos y estado

✅ cumple (probado en local, sin desplegar) · 🟡 parcial · ❌ falta · ➕ extra

| ID | Estado | Cómo |
|---|---|---|
| RF-AZ-01 Portal público | ✅ | `frontend/index.html` |
| RF-AZ-02 Campos del reporte | ✅ | Documento, celular, correo, zona **o coordenadas → zona más cercana**, fecha, categoría (`enums.json`), descripción |
| RF-AZ-03 ID único + idempotencia | ✅ | `REP-<zona>-<hash>` determinista desde `idempotency_key` |
| RF-AZ-04 Consulta con dato de verificación | ✅ | Código + últimos 4 dígitos del celular; sin eso responde 404 |
| RF-AZ-05 Estados + transiciones inválidas | ✅ | `409` y queda en el historial |
| RF-AZ-06 Historial inmutable con actor | ✅ | USUARIO / SISTEMA / IA / OPERADOR |
| RF-AZ-07 Validación y límite de abusos | ✅ | JSON Schema, tope de tamaño, **`RATE_LIMIT_PER_HOUR` por usuario** (`429` + `Retry-After`) |
| RF-AZ-08 Reportes relacionados | ✅ | Modo `ZONA` (cualquier categoría) o `ZONA_Y_CATEGORIA` |
| RF-AZ-09 ≥10 usuarios distintos, ventana <24 h | ✅ | `user_key = sha256(documento\|celular)`; umbral y ventana configurables |
| RF-AZ-10 Agrupar conservando relación | ✅ | |
| RF-AZ-11 Sin duplicar bajo concurrencia | ✅ | ID determinista + `create_item` (409) |
| RF-AZ-12 Reabrir | ✅ | Hasta 5 ciclos |
| RF-AZ-13 Cierre automático | ✅ | |
| RF-AZ-14 Eventos idempotentes + proyección local | ✅ | Sobre común validado contra `contracts/events`; orden por `detected_at`; `GET /api/antennas` usa la proyección si AWS no responde |
| RF-AZ-15 Correlación, origen y tiempo de detección | ✅ | `detected_by` = ANTENA/USUARIOS/AMBOS, `detection_lag_s` |
| RF-AZ-16 Incidencias desde eventos | ✅ | FALLA/SATURACION/PROLONGADA → evidencia; RECUPERACION → propone Solucionada; **PREVENTIVA y ESTRUCTURAL → incidencia de su tipo** |
| RF-AZ-17 Reconciliar con `GET /antennas` | 🟡 ➕ | La consola consulta `GET /antennas` de AWS; no hay tarea periódica |
| RF-AZ-18 Prioridad explicable | ✅ | 40U+25Z+20A+10D+5R, factores guardados, recalculada en cada cambio |
| RF-AZ-19 Consola | 🟡 | Bandeja, filtros, detalle, línea de tiempo, antenas, riesgos y resumen IA. Falta mapa y dashboard de Looker (P3) |
| RF-AZ-20 Acciones del operador | ✅ | Confirmar, descartar, cambiar estado **o prioridad** |
| RF-AZ-21 Solo operadores | ✅ | Token `x-operator-token` (el plan sugiere la auth de Static Web Apps) |
| RF-AZ-22 Tiempo real | ❌ ➕ | Sondeo cada 15 s |
| RF-AZ-23 Tres etapas | ✅ | |
| RF-AZ-24 Correo + SMS con degradación | 🟡 | Correo real con ACS si se crea; **SMS simulado** (la cuenta de estudiante no permite números) |
| RF-AZ-25 Uno por usuario y etapa | ✅ | Idempotente; tasa limitada por la concurrencia de Service Bus; **estado de entrega en el historial del reporte** |
| RF-AZ-26 Texto por IA | ✅ ➕ | |
| RF-AZ-27 Picos | ✅ | `202` + procesamiento asíncrono por change feed + reintentos ante `429` de Cosmos |
| RF-AZ-28 Respaldo continuo | 🟡 | `deploy.ps1` crea Cosmos con respaldo continuo de 7 días (si la cuenta lo permite); no verificado |
| RF-AZ-29 Modo degradado | ✅ | |
| RF-AZ-30 IA por reporte | ✅ | `classify-report` tras guardar (change feed), timeout 4 s, reglas de respaldo, fuente `gemini`/`reglas` |
| RF-AZ-31 Resumen + causa probable | ✅ | `summarize-incident` (8 s): resumen, causa probable, acción recomendada |
| RF-AZ-32 Exportar reportes e incidencias | ✅ | `incident.upsert` y `report.upsert` con el sobre común |
| RF-AZ-33 Leer `GET /config` | ✅ | Con caché (`CONFIG_CACHE_S`) y valores por defecto |

**Total:** 28 ✅ · 4 🟡 · 1 ❌ (extra).

## 3. Qué recibe Azure (APIs que Azure expone, prefijo `/api`)

| Quién llama | Ruta | Autenticación | Contenido |
|---|---|---|---|
| Usuario | `POST /api/reports` | Pública (esquema, tope 8 KB, límite de tasa) | Reporte |
| Usuario | `GET /api/reports/{id}?verify=1234` | Código + últimos 4 dígitos del celular | Estado e historial, sin datos personales |
| Portal | `GET /api/zones`, `GET /api/catalog` | Pública | Catálogo de zonas y categorías |
| **AWS** | `POST /api/events/infra` | **`X-API-Key`** = `AZURE_INBOUND_API_KEY` | Sobre común `antenna.event` |
| Consola | `GET /api/incidents?zone_id&incident_type&priority_level&status&include_closed=1` | `x-operator-token` | Bandeja por prioridad |
| Consola | `GET/PATCH /api/incidents/{id}` | `x-operator-token` | Detalle; acciones `CONFIRMAR`, `DESCARTAR`, `CAMBIAR_ESTADO`, `CAMBIAR_PRIORIDAD` |
| Consola | `PATCH /api/reports/{id}` | `x-operator-token` | Cambiar estado de un reporte |
| Consola | `GET /api/antennas`, `GET /api/predictions` | `x-operator-token` | AWS en vivo o proyección local |
| Consola | `GET /api/infra/events`, `GET /api/settings`, `POST /api/ops/redrive` | `x-operator-token` | Eventos, configuración efectiva, reenvío de DLQ |

**URL base para AWS:** `https://<app>.azurewebsites.net/api` (P1 la guarda en SSM `/pulsored/endpoints/azure`; su outbox agrega `/events/infra`).

### 3.1 Reporte (portal → Azure)
```json
POST /api/reports
{
  "idempotency_key": "uuid-del-cliente",
  "user_document": "12345678", "phone": "+51999999999", "email": "usuario@correo.com",
  "zone_id": "LIM-SURCO",
  "location": {"lat": -12.145, "lng": -76.993},
  "occurred_at": "2026-11-15T14:30:00Z",
  "category": "SIN_INTERNET",
  "description": "No tengo internet desde las 2 pm"
}
```
`zone_id` o `location` (al menos uno). Respuesta: `202 {"report_id":"REP-LIM-SURCO-...","status":"REGISTRADA","zone_id":"LIM-SURCO"}` · `200` con `duplicate:true` · `400` · `429`.

### 3.2 Evento de AWS → Azure (`type: antenna.event`)
Sobre común (`contracts/events/envelope.schema.json`) + payload (`contracts/events/antenna-event.schema.json`):
```json
POST /api/events/infra          (header: X-API-Key)
{
  "event_id": "uuid determinista (uuid5)", "type": "antenna.event", "schema_version": "1.0",
  "source": "aws", "occurred_at": "2026-11-20T15:04:00Z", "correlation_id": "c1f0a9b2-...",
  "payload": {
    "antenna_id": "ANT-LIM-004", "zone_id": "LIM-SURCO", "episode_id": "EP-ANT-LIM-004-1763650860",
    "event_type": "FALLA", "severity": "ALTA", "previous_state": "NORMAL", "new_state": "CAIDA",
    "gb_per_min": 41.2, "first_anomaly_at": "2026-11-20T15:01:00Z", "detected_at": "2026-11-20T15:03:00Z"
  }
}
```
`prediction` solo en PREVENTIVA · `recurrence` solo en ESTRUCTURAL · `elapsed_minutes` solo en PROLONGADA.
Respuestas: `202 {"status":"accepted"}` · `200 {"status":"duplicate"}` · `400` inválido (**AWS no reintenta**) · `401` clave inválida · `5xx` (AWS reintenta con su outbox y DLQ).

## 4. Qué envía Azure

| Destino | Llamada (variables de `.env`) | Contenido | Si falla |
|---|---|---|---|
| **GCP** | `POST {GCP_API_BASE}{GCP_CLASSIFY_PATH}` (`/ai/classify-report`), timeout 4 s | `report_id`, texto sin datos personales, categoría declarada, tipo de zona | `503`/timeout → reglas (`source=reglas`) |
| **GCP** | `POST {GCP_API_BASE}{GCP_SUMMARIZE_PATH}` (`/ai/summarize-incident`), timeout 8 s | Zona, usuarios, evidencia de antenas, hasta 10 textos | Resumen por reglas |
| **GCP** | `POST {GCP_API_BASE}{GCP_INGEST_PATH}` (`/ingest/incidents`) | Sobre `incident.upsert` / `report.upsert` + `Idempotency-Key` | Reintento de Service Bus → DLQ (también 401/403 de clave); otros 4xx se registran y no se reintentan |
| **AWS** | `GET {AWS_API_BASE}{AWS_CONFIG_PATH}` (`/config`) | Configuración global | Caché y valores por defecto |
| **AWS** | `GET {AWS_API_BASE}/antennas`, `/predictions` | Para la consola | Proyección local |
| Usuarios | ACS Email (real) y SMS (simulado) | 3 etapas | Reintentos y DLQ |

Todas las llamadas a AWS y GCP llevan `X-API-Key` (`AWS_API_KEY`, `GCP_API_KEY`). El texto que va a la IA pasa por un filtro que oculta números y correos escritos por el usuario.

## 5. Arquitectura en Azure

Región `westus2`.

| Componente | Uso |
|---|---|
| Functions (Python 3.12, consumo, modelo v2) | API HTTP, clasificación + correlación, enlazador de infraestructura, notificador, exportador |
| Durable Functions | Orquestador `incident_lifecycle` |
| Cosmos DB (free tier o serverless, respaldo continuo 7 días) | Reportes, incidencias, historial, eventos, notificaciones |
| Service Bus Basic | Colas `notifications` y `analytics-export`, con DLQ |
| Communication Services Email | Correo real |
| Key Vault | Secretos (Cosmos, Service Bus, token de operador, claves de AWS/GCP, `AZURE_INBOUND_API_KEY`) |
| Static Web Apps (Free) | Portal y consola |
| Application Insights | Logs y trazas |

| Función | Disparador | Qué hace |
|---|---|---|
| `intake_report` | HTTP | Valida, límite de tasa, guarda, `202` |
| `correlator_fn` | Change feed `reports` | Clasifica con IA (una vez), exporta el reporte, cuenta usuarios distintos, abre o enlaza la incidencia |
| `ingest_infra_event` | HTTP | Valida el sobre común y lo guarda (`create_item`) |
| `infra_linker` | Change feed `infra_events` | Evidencia, PREVENTIVA/ESTRUCTURAL, prioridad, señal a la orquestación, exportación |
| `incident_lifecycle` | Durable | EN_REVISION → EN_ATENCION → SOLUCIONADA → CERRADA, con espera al operador, escalamiento y reapertura |
| `notifier` | Service Bus | Un mensaje por usuario y etapa; estado en el historial |
| `exporter` | Service Bus | `POST` a GCP |

**Diferencia con `docs/global/PLAN.md`:** el plan usa Storage Queue + `report-processor` + `sweeper` y descarta Durable Functions. Se implementó Durable Functions + Service Bus; se mantiene salvo que el equipo decida lo contrario.

## 6. Reglas de dominio

- **Incidencia colectiva:** ≥ `USERS_THRESHOLD` (10) usuarios distintos en la misma zona en `WINDOW_HOURS` (24) h. ID `INC-<zona>-<epoch>`.
- **Estados:** `REGISTRADA → EN_REVISION → EN_ATENCION → SOLUCIONADA → CERRADA` (+ reapertura). Etiquetas: Registrada, En revisión, **Siendo atendida**, Solucionada, Cerrada.
- **Tipos:** `COLECTIVA` (usuarios), `PREVENTIVA` y `ESTRUCTURAL` (eventos de AWS; ID `INC-<zona>-<TIPO>-<antena>-<epoch>`, no notifican a usuarios).
- **Prioridad:** `score = 40·U + 25·Z + 20·A + 10·D + 5·R`. **A:** FALLA/PROLONGADA según `new_state` (INHABILITADA 1.0, CAIDA 0.7, SATURADA 0.5), SATURACION 0.5, DEGRADACION 0.3 (**propuesta, confirmar con el equipo**), PREVENTIVA 0. **R:** episodios de falla en 30 días (o `recurrence.episodes_30d` de un ESTRUCTURAL) / 5. Niveles: CRÍTICA ≥ 75 · ALTA ≥ 50 · MEDIA ≥ 25 · BAJA. El operador puede fijarla a mano.
- **Notificaciones:** clave de idempotencia `incidente|usuario|etapa|ciclo`; la cola no lleva datos de contacto.

## 7. Modelo de datos (Cosmos DB, base `pulsored`)

| Contenedor | Partición | `id` | Notas |
|---|---|---|---|
| `reports` | `/zone_id` | `REP-…` | PII **solo aquí**; `user_key`, `category`, `corr_group`, `ai{category, urgency, critical_site, source}` |
| `incidents` | `/zone_id` | `INC-…` | `incident_type`, `detected_by`, prioridad y factores, tiempos, `ai{summary, probable_cause, recommended_action}` |
| `incident_users` | `/incident_id` | `user_key` | Conteo exacto |
| `history` | `/entity_id` | `ts\|seq` | Solo se agrega |
| `infra_events` | `/zone_id` | `event_id` | Payload del contrato + sobre; TTL 30 días |
| `notifications` | `/incident_id` | `incidente\|usuario\|etapa\|ciclo` | Estado de entrega |
| `zones` | `/id` | `zone_id` | Catálogo |
| `leases` | `/id` | — | Change feed |

## 8. Picos, fallos y recuperación

| Situación | Manejo |
|---|---|
| Pico de reportes | `202` inmediato; change feed asíncrono; reintentos ante `429` de Cosmos; el portal reintenta con la misma `idempotency_key` |
| AWS reenvía un evento | `200 duplicate` |
| Azure caído | AWS acumula en su outbox y reenvía |
| GCP caído | Clasificación y resumen por reglas; exportación a DLQ y `POST /api/ops/redrive` |
| AWS apagado (Lab) | Se sigue registrando, detectando y notificando; `/config` usa caché o valores por defecto |

## 9. Lo que queda por hacer

1. **Desplegar** (`.\infra\deploy.ps1` + `.\infra\smoke_test.ps1`) y verificar free tier, respaldo continuo, ACS y los triggers reales.
2. Completar en `.env` las URLs y claves de AWS y GCP cuando existan (§10) y ejecutar `.\infra\deploy.ps1 -SettingsOnly`.
3. Confirmar con el equipo el peso de DEGRADACION y si P1 agrega `USERS_THRESHOLD`/`WINDOW_HOURS`/`CORRELATION_MODE` a su `/config`.
4. Extras: mapa en la consola, reconciliación periódica, SignalR.

## 10. Variables globales (`.env`)

`.env.example` (versionado) es la plantilla; `.env` (no versionado) tiene los valores reales. En local el backend lo lee al arrancar; en Azure lo sube `deploy.ps1` (las claves a Key Vault).

| Variable | Qué es | Quién la da |
|---|---|---|
| `AWS_API_BASE` | URL de API Gateway (`https://<id>.execute-api.us-east-1.amazonaws.com`) | P1 |
| `AWS_API_KEY` | Clave `azure` de `pulsored/secrets.inbound` | P1 |
| `AWS_CONFIG_PATH`, `AWS_ANTENNAS_PATH`, `AWS_PREDICTIONS_PATH` | Rutas (`/config`, `/antennas`, `/predictions`) | Contrato |
| `AZURE_INBOUND_API_KEY` | Clave con la que AWS llama a `/api/events/infra` | Yo (la genera `deploy.ps1`) → P1 la guarda como `outbound.azure_api_key` |
| `GCP_API_BASE` | URL del API Gateway: `https://telecorp-gateway-1gcgm860.uc.gateway.dev/v1` | P3 |
| `GCP_API_KEY` | Clave que GCP asigna a Azure | P3 |
| `GCP_INGEST_PATH`, `GCP_CLASSIFY_PATH`, `GCP_SUMMARIZE_PATH` | Rutas (`/ingest/incidents`, `/ai/classify-report`, `/ai/summarize-incident`) | Contrato |
| `USERS_THRESHOLD`, `WINDOW_HOURS`, `CORRELATION_MODE`, `RATE_LIMIT_PER_HOUR`, tiempos | Reglas (por defecto; AWS `/config` manda si trae la clave) | Equipo |

**Cambiar de simulado a real:** completar `AWS_API_BASE`/`AWS_API_KEY` o `GCP_API_BASE`/`GCP_API_KEY` en `.env` → `.\infra\deploy.ps1 -SettingsOnly`. La consola muestra en la cabecera si cada integración está conectada o simulada.

## 11. Qué necesito de los demás y qué doy

| De quién | Qué |
|---|---|
| **P1 (AWS)** | `AWS_API_BASE` y la clave `azure` (`AWS_API_KEY`) |
| **P3 (GCP)** | ~~`GCP_API_BASE`~~ y ~~`GCP_API_KEY`~~ (recibidas y conectadas) · activar Gemini real en Cloud Run |
| **Todos** | Mantener `contracts/` sincronizado (si cambia, copiar `zones.json`, `enums.json` y `events/*.json` a `backend/pulsored/data/` y `frontend/`) |

**Yo les doy:** URL base `https://<app>.azurewebsites.net/api` y `AZURE_INBOUND_API_KEY` (de `infra\outputs.local.json`, por canal privado).

## 12. Pruebas y ejecución local

```powershell
cd backend
.\.venv\Scripts\python -m pytest -q                 # 56 pruebas (contratos, reglas, ciclo de vida, HTTP)
.\.venv\Scripts\python scripts\simulate_local.py    # demo de punta a punta en memoria
.\.venv\Scripts\python scripts\dev_server.py        # portal + consola + API en http://localhost:8080 (sin Azure)
.\.venv\Scripts\python scripts\aws_simulator.py --url http://localhost:8080 --key dev-aws-key --type FALLA --state CAIDA
```
Las pruebas cubren, entre otras: ventana de 24 h, 9/10 usuarios, dos zonas, modo ZONA vs ZONA_Y_CATEGORIA, coordenadas → zona, límite de tasa, verificación de la consulta, idempotencia de reportes y eventos, los 7 tipos de evento, validación del sobre común, PREVENTIVA/ESTRUCTURAL, proyección de antenas, prioridad, PII fuera de GCP/colas/IA, contrato de IA con GCP simulado, notificador idempotente con estado en el historial, exportación con sobre común y `event_id` determinista, reapertura y escalamiento.
