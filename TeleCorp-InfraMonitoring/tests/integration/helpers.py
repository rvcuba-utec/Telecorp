import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

T0 = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=30)


def ts(i):
    return (T0 + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")


def sample(antenna="ANT-LIM-001", i=0, gb=95.0, **extra):
    return {"antenna_id": antenna, "ts": ts(i), "gb_per_min": gb, **extra}


class Receiver:
    """Receptor HTTP local que hace de Azure/GCP: registra lo recibido y responde el código configurado."""

    def __init__(self):
        self.status, self.received = 202, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.received.append({"path": self.path, "key": self.headers.get("X-API-Key"), "body": json.loads(body)})
                self.send_response(outer.status)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
