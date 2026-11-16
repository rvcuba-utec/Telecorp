#!/usr/bin/env bash
# Instala las herramientas dentro del proyecto. No escribe en el ~/ real ni modifica el sistema.
set -euo pipefail
cd "$(dirname "$0")"
UV_REAL="$(command -v uv || true)"
source scripts/entorno.sh
if [ -n "$UV_REAL" ]; then
  "$UV_REAL" venv --quiet --allow-existing --managed-python --python 3.12 .venv
  "$UV_REAL" pip install --quiet --python .venv/bin/python -r requirements-dev.txt
else
  python3 -m venv .venv
  .venv/bin/pip install --quiet --no-cache-dir -r requirements-dev.txt
fi
[ -f config.env ] || cp config.example.env config.env
cat > "$HOME/.aws/config" <<CFG
[default]
region = us-east-1
output = json
CFG
cat <<MSG
Listo. Falta:
  Pegar las credenciales de AWS Academy (AWS Details > AWS CLI) en:
     $AWS_SHARED_CREDENTIALS_FILE
  (bloque [default] con aws_access_key_id, aws_secret_access_key y aws_session_token)
Luego:  ./deploy.sh
Para eliminar todo lo instalado: ./clean.sh
MSG
