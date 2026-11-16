# TeleCorp-IncidentManagement (módulo Azure)

Módulo de **cliente e incidencias** de PulsoRed (Red TeleCorp): recibe los reportes de los usuarios, detecta incidencias colectivas, las cruza con los eventos de antenas de AWS, las prioriza, notifica a los afectados y envía datos a GCP.

100 % serverless: Azure Functions (Python 3.12) + Durable Functions, Cosmos DB, Service Bus, Communication Services, Key Vault, Static Web Apps. Región `westus2`.

| Nube | Contexto | Repo |
|---|---|---|
| **AWS** | Infraestructura y automatización: telemetría, eventos, Step Functions, predicciones, configuración global | `TeleCorp-InfraMonitoring` |
| **Azure** (este repo) | Cliente e incidencias: portal, reportes, correlación, priorización, ciclo de vida, notificaciones | `TeleCorp-IncidentManagement` |
| **GCP** | Inteligencia y analítica: predicción, IA generativa (Gemini), histórico y dashboards | `TeleCorp-DataAnalytics` |

Documento pilar: [`docs/AZURE.md`](docs/AZURE.md). Contratos compartidos con AWS y GCP: [`contracts/`](contracts/). Documentos del equipo: [`docs/global/`](docs/global/).

## Qué hace Azure

1. **Recibe reportes** (`POST /api/reports`): validación, idempotencia, límite de tasa por usuario, zona declarada o calculada desde las coordenadas, `202`.
2. **Clasifica cada reporte con IA** (Gemini vía GCP, `/ai/classify-report`) después de guardarlo; si GCP no responde, usa reglas.
3. **Detecta incidencias colectivas**: ≥ 10 usuarios distintos en la misma zona dentro de 24 h (cualquier categoría; modo por categoría configurable).
4. **Recibe eventos de AWS** (`POST /api/events/infra`, sobre común `antenna.event`, 7 tipos) y los correlaciona: evidencia de antena, origen `ANTENA`/`USUARIOS`/`AMBOS`, tiempo de detección; las alertas PREVENTIVA y ESTRUCTURAL crean su propia incidencia.
5. **Prioriza** con un puntaje explicable `40U + 25Z + 20A + 10D + 5R` → CRÍTICA, ALTA, MEDIA, BAJA (el operador puede ajustarla).
6. **Orquesta el ciclo de vida** con Durable Functions: Registrada → En revisión → Siendo atendida → Solucionada → Cerrada, con reapertura, escalamiento al operador y cierre automático.
7. **Notifica** a quienes reportaron en 3 etapas (evaluando → incidencia identificada → restablecido), un mensaje por usuario y etapa, con el estado de entrega en el historial. Correo con Azure Communication Services; SMS simulado.
8. **Resume con IA** cada incidencia (`/ai/summarize-incident`): resumen, causa probable y acción recomendada.
9. **Exporta a GCP** reportes e incidencias (`incident.upsert`, `report.upsert`) sin datos personales.
10. **Lee la configuración global de AWS** (`GET /config`) con caché y valores por defecto.
11. **Portal de usuario** y **consola de operación** (bandeja, filtros, detalle, antenas, riesgos predictivos, acciones).

## APIs: qué recibe y qué envía Azure

### Recibe (prefijo `/api`)
| Quién llama | Ruta | Autenticación | Contenido |
|---|---|---|---|
| Usuario | `POST /api/reports` | Pública (esquema, tope de tamaño, límite de tasa) | Reporte |
| Usuario | `GET /api/reports/{id}?verify=1234` | Código + últimos 4 dígitos del celular | Estado e historial, sin datos personales |
| Portal | `GET /api/zones`, `GET /api/catalog` | Pública | Zonas y categorías del contrato |
| **AWS** | `POST /api/events/infra` | `X-API-Key` (`AZURE_INBOUND_API_KEY`) | Sobre común `antenna.event` |
| Consola | `GET /api/incidents`, `GET/PATCH /api/incidents/{id}`, `PATCH /api/reports/{id}` | `x-operator-token` | Gestión |
| Consola | `GET /api/antennas`, `GET /api/predictions`, `GET /api/infra/events`, `GET /api/settings`, `POST /api/ops/redrive` | `x-operator-token` | Operación |

### Consume (endpoints de otras nubes que Azure llama; URLs en `.env`)
| Nube | Endpoint que Azure consume | Header | Para qué | Si falla |
|---|---|---|---|---|
| **GCP** | `POST {GCP_API_BASE}/ai/classify-report` | `x-api-key` | **Obtener la clasificación con IA de cada reporte** (categoría, urgencia, sitio crítico) | Clasificación por reglas |
| **GCP** | `POST {GCP_API_BASE}/ai/summarize-incident` | `x-api-key` | Resumen, causa probable y acción recomendada de cada incidencia | Resumen por reglas |
| **GCP** | `POST {GCP_API_BASE}/ingest/incidents` | `x-api-key` + `Idempotency-Key` | Enviar `incident.upsert` y `report.upsert` (sin datos personales) | Reintento y DLQ |
| **AWS** | `GET {AWS_API_BASE}/config` | `X-API-Key` | Configuración global | Caché / valores por defecto |
| **AWS** | `GET {AWS_API_BASE}/antennas`, `/predictions` | `X-API-Key` | Estado de antenas y riesgos para la consola | Proyección local |
| Usuarios | ACS Email / SMS | — | Avisos en 3 etapas | Reintento y DLQ |

Entrega al menos una vez y receptores idempotentes. Detalle en [`docs/AZURE.md`](docs/AZURE.md) §3 y §4; contrato exacto con GCP en [`docs/global/GCP.md`](docs/global/GCP.md) §4.2.

### Clasificación con IA (GCP `/ai/classify-report`)
Azure **requiere este endpoint de GCP** para clasificar cada reporte (RF-AZ-30 / RF-GCP-23, decisión D1 del equipo).

- **Cuándo:** una vez por reporte, justo después de guardarlo (change feed de Cosmos), antes de contar usuarios para la incidencia. El usuario ya recibió su `202`: nunca espera a la IA.
- **Código:** `backend/pulsored/services/ai.py` (`classify_report`), llamado desde `services/correlator.py` (`classify`). Tiempo de espera: 4 s (`AI_CLASSIFY_TIMEOUT_S`).
- **Uso del resultado:** se guarda en el reporte; si hay sitio crítico sube la prioridad de la incidencia (+0.2 en el factor de zona); se muestra en la consola; se devuelve a GCP en `report.upsert` (`ai_category`, `ai_urgency`, `classification_source`).

```json
// Azure -> GCP   POST {GCP_API_BASE}/ai/classify-report   (header x-api-key)   — sin documento, celular ni correo
{ "report_id": "REP-LIM-SURCO-ABCDEFGH12345678", "text": "Desde ayer no tengo internet, soy del colegio San Juan",
  "declared_category": "SIN_INTERNET", "zone_type": "EDUCATIVA" }

// GCP -> Azure   200
{ "report_id": "REP-LIM-SURCO-ABCDEFGH12345678", "category": "SIN_INTERNET", "urgency": "ALTA",
  "critical_site": { "mentioned": true, "kind": "COLEGIO" },
  "confidence": 0.91, "model_version": "gemini-<modelo>", "source": "gemini" }

// GCP no disponible   503 {"error": "ai_unavailable"}  (o más de 4 s)  -> Azure clasifica por reglas, source = "reglas"
```

`/ai/summarize-incident` funciona igual (timeout 8 s): Azure envía zona, usuarios afectados, evidencia de antenas y hasta 10 descripciones; GCP responde `summary`, `probable_cause`, `recommended_action` y `model_version`.

## Variables globales (`.env`)

Las URLs y claves de los otros módulos están en un solo archivo:

```powershell
copy .env.example .env      # y completa los valores
```

| Variable | Qué es | Quién la da |
|---|---|---|
| `AWS_API_BASE`, `AWS_API_KEY` | URL de API Gateway y clave `azure` | P1 (AWS) |
| `GCP_API_BASE`, `GCP_API_KEY` | URL del API Gateway (`https://telecorp-gateway-1gcgm860.uc.gateway.dev/v1`, ya configurada) y la API key de Azure (solo en `.env` y Key Vault) | P3 (GCP) |
| `AZURE_INBOUND_API_KEY` | Clave con la que AWS llama a `/api/events/infra` | La genera `deploy.ps1`; se entrega a P1 |
| `*_PATH`, `*_TIMEOUT_S` | Rutas y tiempos de espera de cada API | Contrato |
| `USERS_THRESHOLD`, `WINDOW_HOURS`, `CORRELATION_MODE`, `RATE_LIMIT_PER_HOUR`, … | Reglas por defecto (AWS `/config` manda si trae la clave) | Equipo |

Si una URL base está vacía, esa integración funciona en **modo simulado** (reglas, mock o proyección local). Cuando AWS o GCP estén listos: completa `.env` y ejecuta `.\infra\deploy.ps1 -SettingsOnly`. `.env` **no se versiona**.

## Estado

Código **adaptado a los contratos compartidos y probado en local** (56 pruebas, simulación de punta a punta, servidor de desarrollo). **Nada desplegado todavía.** Requisitos RF-AZ-01…33: 28 cumplen, 4 parciales, 1 extra pendiente (detalle en [`docs/AZURE.md`](docs/AZURE.md) §2).

## Estructura

```
backend/     Azure Functions (Python 3.12, modelo v2) + Durable Functions
  pulsored/    lógica de negocio (sin dependencias de Azure; testeable)
    data/        copia de contracts/ (zonas, enums, esquemas de eventos)
  blueprints/  http_api.py (REST) y triggers.py (change feed, Service Bus, Durable)
  tests/       56 pruebas
  scripts/     dev_server.py · simulate_local.py · aws_simulator.py · load_test.py · seed_zones.py
frontend/    portal de usuario (index.html) y consola de operación (consola.html), sin build
infra/       deploy.ps1 · smoke_test.ps1 · teardown.ps1
contracts/   contratos compartidos con AWS y GCP (copia de TeleCorp-InfraMonitoring)
docs/        AZURE.md (pilar) · AZURE.drawio · global/ (requisitos, plan y GCP del equipo)
.env.example plantilla de variables globales
```

## Probar en local (sin Azure)
```powershell
cd backend
py -3.12 -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt pytest
.\.venv\Scripts\python -m pytest -q                # 56 pruebas
.\.venv\Scripts\python scripts\simulate_local.py   # 10 reportes -> incidencia -> orquestación -> avisos -> export
.\.venv\Scripts\python scripts\dev_server.py       # portal y consola en http://localhost:8080
```

## Desplegar en Azure
```powershell
az login --use-device-code
.\infra\deploy.ps1                 # crea todo, sube .env, publica backend y frontend (idempotente)
.\infra\smoke_test.ps1             # prueba de humo con el contrato real
.\infra\deploy.ps1 -SettingsOnly   # solo actualiza las variables del .env
.\infra\teardown.ps1 -Yes          # borra todo
```
`deploy.ps1` escribe `infra\outputs.local.json` (contiene secretos; **no se versiona**) con la URL base para AWS y `azureInboundApiKey`. Restricciones de la cuenta (Azure for Students): regiones `westus2`, `canadacentral`, `brazilsouth`, `chilecentral`, `westus`; Cosmos free tier con respaldo a serverless; SMS siempre simulado.

> `docs/` es interno y se borra antes de entregar (el enunciado anula a quien entregue un informe).
