"""Funciones HTTP (prefijo /api): portal de usuario, consola de operación y recepción de eventos de AWS."""
import logging

import azure.durable_functions as df
import azure.functions as func

from pulsored import config, domain, httpx
from pulsored.queueing import get_queue
from pulsored.services import aws, export, incidents, infra, intake, reports
from pulsored.store import get_store

bp = df.Blueprint()
log = logging.getLogger("pulsored.http")


def _resp(result):
    status, body = result[0], result[1]
    headers = {"Cache-Control": "no-store"}
    if status == 429:
        headers["Retry-After"] = str((body or {}).get("retry_after_s", 60))
    return func.HttpResponse(httpx.dumps(body), status_code=status, mimetype="application/json", headers=headers)


def _body(req):
    try:
        return req.get_json()
    except ValueError:
        return None


def _op(req):
    return httpx.operator_ok(req.headers)


# ---------- públicas ----------
@bp.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health(req: func.HttpRequest) -> func.HttpResponse:
    return _resp((200, {"ok": True, "service": "pulsored-azure", "zones_version": domain.ZONES_VERSION}))


@bp.route(route="zones", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def zones(req: func.HttpRequest) -> func.HttpResponse:
    return _resp((200, {"zones_version": domain.ZONES_VERSION, "items": get_store().list_zones()}))


@bp.route(route="catalog", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def catalog(req: func.HttpRequest) -> func.HttpResponse:
    """Categorías y etiquetas de estado (contracts/enums.json) para el portal."""
    return _resp((200, {"category": domain.CATEGORIES, "status_labels": domain.STATUS_LABELS,
                        "incident_type": domain.INCIDENT_TYPES}))


# ---------- portal de usuario ----------
@bp.route(route="reports", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def intake_report(req: func.HttpRequest) -> func.HttpResponse:
    if len(req.get_body() or b"") > 8192:
        return _resp((413, {"error": "TOO_LARGE", "message": "Solicitud demasiado grande"}))
    return _resp(intake.register_report(get_store(), _body(req)))


@bp.route(route="reports/{report_id}", methods=["GET", "PATCH"], auth_level=func.AuthLevel.ANONYMOUS)
def reports_api(req: func.HttpRequest) -> func.HttpResponse:
    rid = req.route_params["report_id"]
    if req.method == "GET":
        return _resp(reports.get_public_report(get_store(), rid, req.params.get("verify"), operator=_op(req)))
    if not _op(req):
        return _resp(httpx.unauthorized())
    b = _body(req) or {}
    code, out = reports.change_report_status(get_store(), rid, b.get("to"), b.get("note", ""))
    if code == 200:
        export.enqueue_report(get_queue(), out.pop("report"))
    return _resp((code, out))


# ---------- consola de operación (x-operator-token) ----------
@bp.route(route="incidents", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def incidents_list(req: func.HttpRequest) -> func.HttpResponse:
    if not _op(req):
        return _resp(httpx.unauthorized())
    p = req.params
    return _resp(incidents.list_incidents(get_store(), p.get("zone_id"), p.get("include_closed") == "1",
                                          incident_type=p.get("incident_type"), priority_level=p.get("priority_level"),
                                          status=p.get("status")))


@bp.route(route="incidents/{incident_id}", methods=["GET", "PATCH"], auth_level=func.AuthLevel.ANONYMOUS)
@bp.durable_client_input(client_name="client")
async def incidents_detail(req: func.HttpRequest, client) -> func.HttpResponse:
    if not _op(req):
        return _resp(httpx.unauthorized())
    iid, store = req.route_params["incident_id"], get_store()
    if req.method == "GET":
        return _resp(incidents.get_incident(store, iid))
    status, body, signal = incidents.apply_action(store, iid, _body(req) or {}, queue=get_queue())
    if signal:
        try:
            await client.raise_event(iid, "operator_decision", signal)
        except Exception as e:  # noqa: BLE001 - la orquestación ya terminó o no existe
            log.warning("no se pudo señalar %s (%s); aplico el cambio directo", iid, e)
            if signal == "DESCARTAR":
                incidents.force_status(store, iid, domain.zone_of_incident_id(iid), "CERRADA", "OPERADOR",
                                       "Descartado", reason="FALSO_POSITIVO")
    return _resp((status, body))


@bp.route(route="infra/events", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def infra_events_list(req: func.HttpRequest) -> func.HttpResponse:
    if not _op(req):
        return _resp(httpx.unauthorized())
    return _resp((200, {"items": get_store().list_infra_events(req.params.get("zone_id"))}))


@bp.route(route="antennas", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def antennas(req: func.HttpRequest) -> func.HttpResponse:
    """Estado de antenas: AWS GET /antennas si está configurado; si no, proyección local."""
    if not _op(req):
        return _resp(httpx.unauthorized())
    return _resp((200, aws.antennas(get_store(), req.params.get("zone_id"))))


@bp.route(route="predictions", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def predictions(req: func.HttpRequest) -> func.HttpResponse:
    """Riesgos predictivos vigentes: AWS GET /predictions o, si no, eventos PREVENTIVA recibidos."""
    if not _op(req):
        return _resp(httpx.unauthorized())
    return _resp((200, aws.predictions(get_store(), req.params.get("zone_id"))))


@bp.route(route="settings", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def settings(req: func.HttpRequest) -> func.HttpResponse:
    """Configuración efectiva (AWS /config + valores por defecto), sin secretos."""
    if not _op(req):
        return _resp(httpx.unauthorized())
    return _resp((200, config.effective()))


@bp.route(route="ops/redrive", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def redrive_dlq(req: func.HttpRequest) -> func.HttpResponse:
    if not _op(req):
        return _resp(httpx.unauthorized())
    queue = (_body(req) or {}).get("queue")
    if queue not in ("notifications", "analytics-export"):
        return _resp((400, {"error": "BAD_REQUEST", "message": "queue inválida"}))
    return _resp((200, {"moved": get_queue().redrive(queue)}))


# ---------- AWS -> Azure (INT-01): sobre común antenna.event con X-API-Key ----------
@bp.route(route="events/infra", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def ingest_infra_event(req: func.HttpRequest) -> func.HttpResponse:
    if not httpx.inbound_key_ok(req.headers):
        return _resp((401, {"error": "UNAUTHORIZED", "message": "X-API-Key inválida"}))
    if len(req.get_body() or b"") > 32768:
        return _resp((413, {"error": "TOO_LARGE", "message": "Evento demasiado grande"}))
    return _resp(infra.ingest_event(get_store(), _body(req)))
