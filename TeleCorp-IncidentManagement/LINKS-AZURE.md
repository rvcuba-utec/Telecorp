# Links de Azure (PulsoRed)

Todo lo que expone el módulo Azure y se puede consumir desde fuera.

## Páginas web

| Qué | URL |
|---|---|
| **Portal de usuario** (registrar y consultar reportes) | https://orange-wave-07e25541e.5.azurestaticapps.net |
| **Consola de operación** (incidencias, antenas, predicciones) | https://orange-wave-07e25541e.5.azurestaticapps.net/consola |

## API

**URL base:** `https://pulsored-func-pr8685.azurewebsites.net/api`

Respuestas en JSON. CORS habilitado para el portal y la consola.

### Públicos (sin clave)

| Método | URL | Para qué |
|---|---|---|
| GET | https://pulsored-func-pr8685.azurewebsites.net/api/health | Estado del servicio |
| GET | https://pulsored-func-pr8685.azurewebsites.net/api/zones | Catálogo de zonas (`contracts/zones.json`) |
| GET | https://pulsored-func-pr8685.azurewebsites.net/api/catalog | Categorías y etiquetas de estado |
| POST | https://pulsored-func-pr8685.azurewebsites.net/api/reports | Registrar un reporte (máx. 5 por hora por usuario) |
| GET | `https://pulsored-func-pr8685.azurewebsites.net/api/reports/{report_id}?verify={últimos 4 del celular}` | Consultar el estado de un reporte |

Cuerpo de `POST /reports`:

```json
{
  "idempotency_key": "clave-unica-del-cliente",
  "user_document": "12345678",
  "phone": "+51987654321",
  "email": "usuario@correo.com",
  "category": "SIN_INTERNET",
  "zone_id": "LIM-SURCO",
  "description": "No tengo internet desde la mañana",
  "occurred_at": "2026-11-15T20:00:00Z"
}
```

- `category`: `SIN_INTERNET` · `INTERNET_LENTO` · `SIN_SENAL` · `LLAMADAS` · `SMS` · `OTRO`
- En lugar de `zone_id` se puede mandar `"location": {"lat": -12.1, "lng": -77.0}`.
- Respuesta: `202` nuevo · `200` ya existía (mismo `idempotency_key`) · `400` inválido · `429` límite por hora (`Retry-After`).

### Para AWS (header `X-API-Key`)

| Método | URL | Para qué |
|---|---|---|
| POST | https://pulsored-func-pr8685.azurewebsites.net/api/events/infra | Eventos de antenas (sobre común `antenna.event`) |

Respuesta: `202` nuevo · `200 duplicate` · `400` inválido · `401` clave incorrecta.

### Consola de operación (header `x-operator-token`)

| Método | URL | Para qué |
|---|---|---|
| GET | https://pulsored-func-pr8685.azurewebsites.net/api/incidents | Incidencias abiertas, por prioridad. Filtros: `zone_id`, `incident_type`, `priority_level`, `status`, `include_closed=1` |
| GET | `https://pulsored-func-pr8685.azurewebsites.net/api/incidents/{incident_id}` | Detalle: reportes, antenas, IA, historial |
| PATCH | `https://pulsored-func-pr8685.azurewebsites.net/api/incidents/{incident_id}` | Acción: `{"action": "CONFIRMAR" \| "DESCARTAR" \| "CAMBIAR_ESTADO" \| "CAMBIAR_PRIORIDAD", ...}` |
| PATCH | `https://pulsored-func-pr8685.azurewebsites.net/api/reports/{report_id}` | Cambiar el estado de un reporte: `{"to": "...", "note": "..."}` |
| GET | https://pulsored-func-pr8685.azurewebsites.net/api/infra/events | Eventos de antenas recibidos de AWS |
| GET | https://pulsored-func-pr8685.azurewebsites.net/api/antennas | Estado de antenas (desde AWS) |
| GET | https://pulsored-func-pr8685.azurewebsites.net/api/predictions | Predicciones de falla (desde AWS) |
| GET | https://pulsored-func-pr8685.azurewebsites.net/api/settings | Configuración efectiva e integraciones conectadas |
| POST | https://pulsored-func-pr8685.azurewebsites.net/api/ops/redrive | Reenviar la DLQ: `{"queue": "analytics-export" \| "notifications"}` |

## Lo que Azure consume de otras nubes

| Nube | URL base | Rutas |
|---|---|---|
| AWS | `https://0w6e50qek3.execute-api.us-east-1.amazonaws.com` | `GET /config` · `GET /antennas` · `GET /predictions` |
| GCP | `https://telecorp-gateway-1gcgm860.uc.gateway.dev/v1` | `POST /ai/classify-report` · `POST /ai/summarize-incident` · `POST /ingest/incidents` |

Estado (2026-11-15): las tres nubes conectadas y verificadas en las dos direcciones.

## Estados que ve el usuario

`Registrada` → `En revisión` → `Siendo atendida` → `Solucionada` → `Cerrada`
