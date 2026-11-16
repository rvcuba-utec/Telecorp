# PulsoRed — Información del despliegue en Azure

> Estado al 2026-11-15: **desplegado y funcionando** en la suscripción personal (prueba gratuita, límite de gasto activado), región `westus2`.
> Este archivo **no contiene secretos**. Las claves están en `infra\outputs.local.json` (no se versiona) y en Key Vault.

## Links

| Qué | URL |
|---|---|
| **Portal de usuario** | https://orange-wave-07e25541e.5.azurestaticapps.net |
| **Consola de operación** | https://orange-wave-07e25541e.5.azurestaticapps.net/consola |
| **API (salud)** | https://pulsored-func-pr8685.azurewebsites.net/api/health |
| **URL base de la API** | https://pulsored-func-pr8685.azurewebsites.net/api |
| **Eventos de AWS** | `POST https://pulsored-func-pr8685.azurewebsites.net/api/events/infra` (header `X-API-Key`) |

La consola pide un **token de operador**: valor `operatorToken` de `infra\outputs.local.json`.

## Recursos creados (grupo `rg-pulsored`)

| Recurso | Nombre | Notas |
|---|---|---|
| Cosmos DB | `pulsored-cosmos-pr8685` | Free tier · respaldo continuo (7 días) · base `pulsored` |
| Function App (Python 3.12, consumo) | `pulsored-func-pr8685` | 28 funciones: HTTP, change feed, Service Bus y Durable |
| Service Bus (Basic) | `pulsored-sb-pr8685` | Colas `notifications` y `analytics-export` con DLQ |
| Key Vault | `pulsored-kv-pr8685` | Secretos (Cosmos, Service Bus, token de operador, claves de AWS/GCP) |
| Communication Services + Email | `pulsored-acs-pr8685`, `pulsored-email-pr8685` | Correo **real** desde `DoNotReply@6801cbcc-0b1e-46e0-ade4-52d84fda720b.azurecomm.net` |
| Static Web Apps (Free) | `pulsored-web-pr8685` | Portal y consola |
| Storage, Log Analytics, Application Insights | `pulsoredstpr8685`, `pulsored-law-pr8685`, `pulsored-ai-pr8685` | Runtime de Functions y monitoreo |

## Verificado en la nube (prueba de humo + prueba manual)

- Salud y catálogo de zonas/categorías del contrato.
- Seguridad: endpoints de operador sin token → `401`; eventos de AWS sin `X-API-Key` → `401`.
- Reportes idempotentes (`202` y luego `200` con el mismo id); consulta con dato de verificación (últimos 4 dígitos del celular).
- Eventos de AWS con el contrato real (sobre común `antenna.event`): nuevo `202`, repetido `200 duplicate`, tipo inválido `400`; PREVENTIVA crea su incidencia.
- 10 usuarios en una zona → incidencia colectiva → orquestación Durable corriendo → IA (reglas, GCP aún sin conectar) → "En revisión" → 10 notificaciones "Evaluando" enviadas.
- Frontend publicado (todas las páginas `200`) y CORS correcto para portal y consola.

## Integraciones

| Integración | Estado | Para activarla |
|---|---|---|
| AWS → Azure (eventos de antenas) | Listo para recibir | Entregar a P1 (por privado) la URL base y `azureInboundApiKey` de `infra\outputs.local.json` |
| Azure → AWS (`/config`, `/antennas`, `/predictions`) | Simulado | Poner `AWS_API_BASE` y `AWS_API_KEY` en `.env` |
| Azure → GCP (`/ai/classify-report`, `/ai/summarize-incident`, `/ingest/incidents`) | **Real** (API Gateway + API key en Key Vault). Verificado: clasificación `fuente=gemini`, resumen `ai_source=gemini`, exportación `sent`, DLQ vacía | — |
| Correo (ACS) | **Real** | — |
| SMS | Simulado | No disponible en la cuenta |

Después de editar `.env`:
```powershell
.\infra\deploy.ps1 -SettingsOnly
```

## Para P1 (AWS)
- SSM `/pulsored/endpoints/azure` = `https://pulsored-func-pr8685.azurewebsites.net/api`
- Secreto `outbound.azure_api_key` = valor `azureInboundApiKey` de `infra\outputs.local.json` (por canal privado)
- Su outbox llama a `POST {base}/events/infra` con el header `X-API-Key`.

## Para P3 (GCP)
- Azure llama a `POST {GCP_API_BASE}/ai/classify-report`, `/ai/summarize-incident` y `/ingest/incidents` con el header `x-api-key`. Formato exacto en el `README.md` (sección "Clasificación con IA") y en `docs/global/GCP.md` §4.2.
- URL base configurada: `https://telecorp-gateway-1gcgm860.uc.gateway.dev/v1` (rutas `/ingest/incidents`, `/ai/classify-report`, `/ai/summarize-incident`). `/ingest/classify-report` no existe en el Gateway (404) y Azure no la usa.
- API key recibida y guardada en Key Vault (`gcp-api-key`); nunca en el repo.
- Verificado el 2026-11-15 en la nube: un reporte declarado `OTRO` fue reclasificado por GCP como `SIN_SENAL`; la incidencia `INC-LIM-MIRAFLORES-1794789711` recibió resumen, causa y acción de GCP; las exportaciones a `/ingest/incidents` responden `sent`.
- Aviso para P3: GCP responde `model_version = gemini-2.5-flash-hybrid`, que es su respaldo por reglas. Gemini real aún no está activo de su lado (`GCP_PROJECT_ID`/`GEMINI_API_KEY` en Cloud Run).
- Si se cambia la clave: actualizar `.env`, `deploy.ps1 -SettingsOnly` y luego `az functionapp stop` + `start` (un simple restart puede dejar una instancia con la clave anterior).

## Operación

```powershell
.\infra\smoke_test.ps1              # prueba de humo contra el despliegue
.\infra\deploy.ps1                  # volver a desplegar todo (idempotente)
.\infra\deploy.ps1 -SettingsOnly    # solo actualizar variables del .env
.\infra\teardown.ps1 -Yes           # borrar todo (irreversible)
```

- **Logs y errores:** Application Insights `pulsored-ai-pr8685` (portal de Azure → Application Insights → Failures / Logs).
- **Costo:** centavos del crédito de prueba; revisar en *Cost Management*. Con el límite de gasto activado no se cobra a la tarjeta.

## Notas
- La incidencia de prueba `INC-LIM-VES-1794781969` quedó sin orquestación porque la procesó la versión anterior durante la publicación del arreglo; es un dato de prueba. Las incidencias nuevas funcionan.
- Arreglos hechos durante el despliegue: app settings por archivo JSON, conteos escalares de Cosmos, recuperación de incidencias huérfanas y reintentos de los triggers de Cosmos.
