# Aísla las herramientas: nada se escribe en el ~/ real. Todo queda en .local-env/ (fuera de Git).
_RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LOCAL_ENV="$_RAIZ/.local-env"
export HOME="$LOCAL_ENV/home"                 # ~/.aws y ~/.aws-sam quedan aquí
export npm_config_cache="$LOCAL_ENV/npm-cache"
export UV_CACHE_DIR="$LOCAL_ENV/uv-cache"
export UV_PYTHON_INSTALL_DIR="$LOCAL_ENV/uv-python"
export AWS_SHARED_CREDENTIALS_FILE="$HOME/.aws/credentials"
export AWS_CONFIG_FILE="$HOME/.aws/config"
export AWS_PAGER=""
export SAM_CLI_TELEMETRY=0
export PYTHONDONTWRITEBYTECODE=1
export PATH="$_RAIZ/.venv/bin:$PATH"
mkdir -p "$HOME/.aws"
chmod 700 "$LOCAL_ENV" "$HOME/.aws"
if [ ! -f "$AWS_SHARED_CREDENTIALS_FILE" ]; then
  : > "$AWS_SHARED_CREDENTIALS_FILE"
  chmod 600 "$AWS_SHARED_CREDENTIALS_FILE"
fi
