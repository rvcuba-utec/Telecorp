#!/usr/bin/env bash
# Verificación de extremo a extremo contra AWS (con el receptor simulado). Uso: ./demo.sh [e2e|resilience|status]
source "$(dirname "$0")/scripts/lib.sh"
comprobar_entorno >/dev/null
python tools/e2e.py "${1:-e2e}" "${@:2}"
