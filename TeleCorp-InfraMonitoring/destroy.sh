#!/usr/bin/env bash
# Elimina el stack completo en AWS (tablas con sus datos incluidas). Pide confirmación.
source "$(dirname "$0")/scripts/lib.sh"
comprobar_entorno
echo "Se eliminará el stack '$STACK_NAME': APIs, Lambdas, colas, tablas con sus datos y máquinas de estado."
echo "El secreto 'pulsored/secrets' y los parámetros SSM /pulsored/* se conservan (usa tools/e2e.py purge para borrarlos)."
read -r -p "Escribe el nombre del stack para confirmar: " RESPUESTA
[ "$RESPUESTA" = "$STACK_NAME" ] || fallo "cancelado."
aws cloudformation delete-stack --stack-name "$STACK_NAME"
aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"
echo "Stack eliminado."
