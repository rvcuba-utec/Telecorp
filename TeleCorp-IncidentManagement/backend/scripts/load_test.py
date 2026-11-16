"""Prueba de carga: 2 000 reportes en ~60 s sobre las zonas del catálogo, con claves idempotentes y reintentos con backoff.
  python scripts/load_test.py --url https://<func>.azurewebsites.net --total 2000 --seconds 60
Criterios (RNF-10): 0 reportes perdidos, p95 POST < 1 s. Cada usuario es distinto (no choca con el límite de tasa)."""
import argparse
import json
import os
import random
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://localhost:7071")
ap.add_argument("--total", type=int, default=2000)
ap.add_argument("--seconds", type=int, default=60)
ap.add_argument("--workers", type=int, default=40)
a = ap.parse_args()
run = int(time.time())
_cat = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pulsored", "data", "zones.json")
with open(_cat, encoding="utf-8") as f:
    zones = [z["zone_id"] for z in json.load(f)["zones"]]
sess = requests.Session()


def send(i):
    body = {"idempotency_key": f"load-{run}-{i:06d}", "user_document": f"L{run % 10**5}{i:06d}"[:15],
            "phone": f"+51988{i:06d}", "zone_id": zones[i % len(zones)],
            "occurred_at": datetime.now(timezone.utc).isoformat(), "category": "SIN_INTERNET",
            "description": "prueba de carga"}
    dt = 0.0
    for attempt in range(5):
        t0 = time.perf_counter()
        try:
            r = sess.post(f"{a.url}/api/reports", json=body, timeout=15)
            dt = time.perf_counter() - t0
            if r.status_code in (200, 202):
                return ("ok", dt)
            if r.status_code < 500 and r.status_code != 429:
                return ("rechazado", dt)
        except requests.RequestException:
            dt = time.perf_counter() - t0
        time.sleep(min(8, 0.5 * 2 ** attempt) * random.uniform(0.5, 1.5))   # backoff + jitter
    return ("perdido", dt)


start = time.time()
with ThreadPoolExecutor(a.workers) as ex:
    res = list(ex.map(send, range(a.total)))
elapsed = time.time() - start
lat = sorted(d for s, d in res if s == "ok")
cnt = {k: sum(1 for s, _ in res if s == k) for k in ("ok", "rechazado", "perdido")}
print(f"enviados={a.total} en {elapsed:.1f}s | {cnt}")
if lat:
    print(f"p50={statistics.median(lat):.3f}s p95={lat[int(len(lat) * .95) - 1]:.3f}s max={lat[-1]:.3f}s")
print("OK" if cnt["perdido"] == 0 and lat and lat[int(len(lat) * .95) - 1] < 1.0 else "REVISAR")
