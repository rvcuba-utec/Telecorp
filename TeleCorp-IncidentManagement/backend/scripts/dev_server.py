"""Servidor local de desarrollo SIN Azure: sirve el frontend y la API (/api/*) con datos en memoria.
  python scripts/dev_server.py            -> http://localhost:8080  (portal)  y  /consola
El change feed se simula en línea: al crear un reporte o recibir un evento se corre el correlador/enlazador.
La orquestación Durable no corre aquí (usa scripts/simulate_local.py para verla)."""
import asyncio
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for k in ("COSMOS", "SB"):
    os.environ.pop(k, None)

import azure.functions as func  # noqa: E402

from blueprints import http_api as h  # noqa: E402
from pulsored import config, domain  # noqa: E402
from pulsored.queueing import get_queue  # noqa: E402
from pulsored.services import correlator, infra  # noqa: E402
from pulsored.store import get_store  # noqa: E402

FRONT = os.path.join(os.path.dirname(ROOT), "frontend")
PORT = int(os.environ.get("PORT", "8080"))


class _NoDurable:
    async def raise_event(self, *a, **k):
        raise RuntimeError("sin Durable en el servidor local")


def _fn(f):
    return f.build().get_user_function() if hasattr(f, "build") else f


ROUTES = [  # (método, partes de la ruta, función, nombre del parámetro)
    ("GET", ["health"], h.health, None), ("GET", ["zones"], h.zones, None), ("GET", ["catalog"], h.catalog, None),
    ("POST", ["reports"], h.intake_report, None), ("GET", ["reports", "*"], h.reports_api, "report_id"),
    ("PATCH", ["reports", "*"], h.reports_api, "report_id"), ("GET", ["incidents"], h.incidents_list, None),
    ("GET", ["incidents", "*"], h.incidents_detail, "incident_id"),
    ("PATCH", ["incidents", "*"], h.incidents_detail, "incident_id"),
    ("GET", ["infra", "events"], h.infra_events_list, None), ("GET", ["antennas"], h.antennas, None),
    ("GET", ["predictions"], h.predictions, None), ("GET", ["settings"], h.settings, None),
    ("POST", ["events", "infra"], h.ingest_infra_event, None), ("POST", ["ops", "redrive"], h.redrive_dlq, None),
]


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=FRONT, **k)

    def _api(self, method):
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/")[2:] if p]
        for m, pattern, fn, pname in ROUTES:
            if m == method and len(pattern) == len(parts) and all(a == "*" or a == b for a, b in zip(pattern, parts)):
                route = {pname: parts[-1]} if pname else {}
                n = int(self.headers.get("Content-Length") or 0)
                req = func.HttpRequest(method, u.path, headers=dict(self.headers), route_params=route,
                                       params={k: v[0] for k, v in parse_qs(u.query).items()},
                                       body=self.rfile.read(n) if n else b"")
                f = _fn(fn)
                res = asyncio.run(f(req, _NoDurable())) if asyncio.iscoroutinefunction(f) else f(req)
                self._after(method, parts, res)
                return self._send(res.status_code, res.get_body(), dict(res.headers))
        return self._send(404, json.dumps({"error": "NOT_FOUND"}).encode(), {})

    def _after(self, method, parts, res):
        """Simula el change feed de Cosmos."""
        if res.status_code != 202:
            return
        store, queue = get_store(), get_queue()
        out = json.loads(res.get_body())
        if parts == ["reports"]:
            zone = domain.zone_of_report_id(out["report_id"])
            correlator.process_report(store, store.get("reports", out["report_id"], zone), queue)
        elif parts == ["events", "infra"]:
            for e in store.list_infra_events(limit=1000):
                if e["id"] == out["event_id"]:
                    infra.link_event(store, e, queue)

    def _send(self, code, body, headers):
        self.send_response(code)
        headers.setdefault("Content-Type", "application/json")
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/"):
            return self._api("GET")
        if self.path in ("/config.json",):
            return self._send(200, json.dumps({"apiBase": ""}).encode(), {})
        if self.path.split("?")[0] == "/consola":
            self.path = "/consola.html"
        return super().do_GET()

    def do_POST(self):  # noqa: N802
        return self._api("POST")

    def do_PATCH(self):  # noqa: N802
        return self._api("PATCH")


if __name__ == "__main__":
    if not os.environ.get("AZURE_INBOUND_API_KEY"):
        os.environ["AZURE_INBOUND_API_KEY"] = "dev-aws-key"
    print(f"Portal:  http://localhost:{PORT}/      Consola: http://localhost:{PORT}/consola "
          f"(token: {config.env('OPERATOR_TOKEN') or '(define OPERATOR_TOKEN en .env)'})")
    print(f"Eventos AWS: POST http://localhost:{PORT}/api/events/infra  X-API-Key: {config.env('AZURE_INBOUND_API_KEY')}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
