# Funciones comunes de los comandos. Se carga con `source`.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/entorno.sh"
[ -f config.env ] || { echo "ERROR: falta config.env. Ejecuta ./setup.sh primero." >&2; exit 1; }
# shellcheck disable=SC1091
source config.env
STACK_NAME="${STACK_NAME:-pulsored-aws}"
REGION="${REGION:-us-east-1}"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION" STACK_NAME REGION

paso()  { printf '\n==> %s\n' "$*"; }
fallo() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

comprobar_entorno() {
  local c
  for c in aws sam python zip curl; do
    command -v "$c" >/dev/null || fallo "falta '$c'. Ejecuta ./setup.sh."
  done
  local salida
  if ! salida="$(aws sts get-caller-identity --output text --query '[Account,UserId,Arn]' 2>&1)"; then
    case "$salida" in
      *ExpiredToken*|*expired*) fallo "las credenciales de AWS Academy caducaron. Pega las nuevas en $AWS_SHARED_CREDENTIALS_FILE" ;;
      *) fallo "no hay credenciales válidas de AWS. Pégalas en $AWS_SHARED_CREDENTIALS_FILE ($salida)" ;;
    esac
  fi
  read -r CUENTA USUARIO ARN <<<"$salida"
  export CUENTA USUARIO ARN
  echo "Cuenta:  $CUENTA"
  echo "Usuario: $USUARIO"
  echo "ARN:     $ARN"
  echo "Región:  $REGION   Stack: $STACK_NAME"
  aws iam get-role --role-name LabRole >/dev/null 2>&1 \
    || echo "AVISO: no pude comprobar el rol LabRole (puede no tener permiso iam:GetRole); el despliegue lo confirmará."
}

stack_existe() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" >/dev/null 2>&1
}

salida_stack() {   # salida_stack <Clave>
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" --output text 2>/dev/null || true
}
