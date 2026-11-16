#!/usr/bin/env python3
"""Genera contracts/zones.json (catalogo unico de zonas y antenas) y contracts/enums.json.

Uso:  python3 tools/generate_catalog.py
Es determinista: ejecutarlo dos veces produce el mismo resultado.
Los nombres de sitios criticos son GENERICOS y simulados a proposito.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANTENNAS_PER_ZONE = 5

# (zone_id, nombre, tipo, lat, lng, sitios criticos simulados)
ZONES = [
    ("LIM-SURCO",       "Santiago de Surco",        "RESIDENCIAL", -12.1450, -76.9930, []),
    ("LIM-MIRAFLORES",  "Miraflores",               "COMERCIAL",   -12.1211, -77.0297, []),
    ("LIM-SANISIDRO",   "San Isidro",               "COMERCIAL",   -12.0977, -77.0365, []),
    ("LIM-CALLAO",      "Callao",                   "INDUSTRIAL",  -12.0566, -77.1181, ["Parque industrial (simulado)", "Terminal de carga (simulado)"]),
    ("LIM-ATE",         "Ate",                      "INDUSTRIAL",  -12.0260, -76.9180, ["Zona fabril (simulada)"]),
    ("LIM-SMP",         "San Martin de Porres",     "RESIDENCIAL", -12.0090, -77.0870, []),
    ("LIM-SANMIGUEL",   "San Miguel",               "EDUCATIVA",   -12.0770, -77.0910, ["Campus universitario (simulado)"]),
    ("LIM-CERCADO",     "Cercado de Lima",          "CRITICA",     -12.0464, -77.0428, ["Central de emergencias (simulada)", "Sede de gobierno (simulada)"]),
    ("LIM-JESUSMARIA",  "Jesus Maria",              "SALUD",       -12.0700, -77.0510, ["Hospital central (simulado)"]),
    ("LIM-SJL",         "San Juan de Lurigancho",   "RESIDENCIAL", -11.9770, -77.0080, []),
    ("LIM-LAMOLINA",    "La Molina",                "EDUCATIVA",   -12.0850, -76.9100, ["Colegio y universidad (simulados)"]),
    ("LIM-VES",         "Villa El Salvador",        "RESIDENCIAL", -12.2130, -76.9360, []),
]

# desplazamientos fijos (grados) para repartir las 5 antenas alrededor del centroide
OFFSETS = [(0.0, 0.0), (0.004, 0.003), (-0.004, 0.003), (0.003, -0.004), (-0.003, -0.004)]

zones, n = [], 0
for zid, name, ztype, lat, lng, sites in ZONES:
    antennas = []
    for dlat, dlng in OFFSETS[:ANTENNAS_PER_ZONE]:
        n += 1
        antennas.append({"antenna_id": f"ANT-LIM-{n:03d}",
                         "lat": round(lat + dlat, 5), "lng": round(lng + dlng, 5)})
    zones.append({"zone_id": zid, "name": name, "type": ztype,
                  "centroid": {"lat": lat, "lng": lng},
                  "critical_sites": sites, "antennas": antennas})

catalog = {"zones_version": "1.0.0",
           "note": "Catalogo unico compartido por AWS, Azure y GCP. Datos simulados.",
           "zones": zones}

enums = {
    "antenna_state": ["NORMAL", "CAIDA", "INHABILITADA", "SATURADA"],
    "severity": ["INFO", "BAJA", "MEDIA", "ALTA", "CRITICA"],
    "event_type": ["FALLA", "SATURACION", "DEGRADACION", "RECUPERACION", "PREVENTIVA", "ESTRUCTURAL", "PROLONGADA"],
    "incident_type": ["COLECTIVA", "INFRAESTRUCTURA", "PREVENTIVA", "ESTRUCTURAL"],
    "status": ["REGISTRADA", "EN_REVISION", "EN_ATENCION", "SOLUCIONADA", "CERRADA"],
    "status_labels": {"REGISTRADA": "Registrada", "EN_REVISION": "En revisión", "EN_ATENCION": "Siendo atendida",
                      "SOLUCIONADA": "Solucionada", "CERRADA": "Cerrada"},
    "category": ["SIN_INTERNET", "INTERNET_LENTO", "SIN_SENAL", "LLAMADAS", "SMS", "OTRO"],
    "zone_type": ["RESIDENCIAL", "COMERCIAL", "INDUSTRIAL", "EDUCATIVA", "SALUD", "CRITICA"],
    "detected_by": ["ANTENA", "USUARIOS", "AMBOS"],
    "envelope_types": ["telemetry.batch", "antenna.event", "prediction.batch",
                       "incident.upsert", "report.upsert"],
}

(ROOT / "contracts" / "zones.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
(ROOT / "contracts" / "enums.json").write_text(json.dumps(enums, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"zonas={len(zones)} antenas={n}")
