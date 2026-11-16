#!/usr/bin/env bash
# AWS Academy apaga los disparadores (SQS y Streams) al cerrar el laboratorio y CloudFormation no los reactiva.
# Úsalo siempre tras reiniciar el laboratorio.
source "$(dirname "$0")/scripts/lib.sh"
comprobar_entorno >/dev/null
python tools/e2e.py warmup
