#!/usr/bin/env bash
# Comprueba el sistema desplegado, sin desplegar nada.
source "$(dirname "$0")/scripts/lib.sh"
comprobar_entorno >/dev/null
stack_existe || fallo "el stack $STACK_NAME no existe. Ejecuta ./deploy.sh."
python tools/e2e.py smoke
