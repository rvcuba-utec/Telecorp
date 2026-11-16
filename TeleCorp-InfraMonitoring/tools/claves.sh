#!/usr/bin/env bash
# Muestra las claves inbound (las que se entregan a Azure/GCP/antenas) guardadas localmente.
# Uso: bash tools/claves.sh            -> todas, con la URL de la API
#      bash tools/claves.sh azure      -> solo la clave de ese llamante (telemetry|gcp|azure|operator)
cd "$(dirname "$0")/.."
f=.local-env/claves.json
[ -f "$f" ] || { echo "No hay claves locales. Ejecuta ./deploy.sh (o: source scripts/entorno.sh && python tools/bootstrap_secrets.py)." >&2; exit 1; }
if [ -n "${1:-}" ]; then
  python3 -c 'import json,sys; v=json.load(open(sys.argv[1]))["inbound"].get(sys.argv[2]); print(v) if v else sys.exit("llamante desconocido: " + sys.argv[2])' "$f" "$1"
else
  cat "$f"
fi
