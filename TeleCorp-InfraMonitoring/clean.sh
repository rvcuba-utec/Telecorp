#!/usr/bin/env bash
# Borra del proyecto las herramientas, credenciales locales y builds. No toca nada en AWS.
set -euo pipefail
cd "$(dirname "$0")"
echo "Se borrarán: .local-env/ (incluye credenciales y claves) .venv/ .aws-sam/ y cachés de pytest"
read -r -p "Escribe 'borrar' para confirmar: " RESPUESTA
[ "$RESPUESTA" = borrar ] || { echo "Cancelado."; exit 1; }
rm -rf .local-env .venv .aws-sam .pytest_cache
find . -type d -name __pycache__ -not -path './.git/*' -prune -exec rm -rf {} +
echo "Listo. config.env se conserva; ./setup.sh vuelve a instalar todo. Los recursos en AWS no cambian."
