#!/usr/bin/env bash
# Pruebas locales (unitarias, contrato e integración con moto), sin AWS ni credenciales.
set -uo pipefail
cd "$(dirname "$0")"
source scripts/entorno.sh
python -m pytest "$@" && echo "Todas las pruebas pasaron."
