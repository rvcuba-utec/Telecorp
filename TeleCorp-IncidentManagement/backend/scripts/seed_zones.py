"""Carga el catálogo de zonas (contracts/zones.json) en Cosmos. Uso:
  $env:COSMOS = "<connection string>"; python scripts/seed_zones.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pulsored import domain  # noqa: E402
from pulsored.store import CosmosStore  # noqa: E402

s = CosmosStore()
for z in domain.ZONES.values():
    s.upsert("zones", {**z, "id": z["zone_id"], "zones_version": domain.ZONES_VERSION})
print(f"{len(domain.ZONES)} zonas cargadas (versión {domain.ZONES_VERSION})")
