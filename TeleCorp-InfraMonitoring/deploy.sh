#!/usr/bin/env bash
# Uso: ./deploy.sh [--no-seed]   Despliega (o actualiza) todo el módulo AWS y lo comprueba.
SEED=1
while [ $# -gt 0 ]; do case "$1" in --no-seed) SEED=0; shift ;; *) echo "opción desconocida: $1" >&2; exit 1 ;; esac; done
source "$(dirname "$0")/scripts/lib.sh"
comprobar_entorno

paso "Validando la plantilla"
cfn-lint template.yaml || fallo "la plantilla tiene errores."
find src -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true

BUCKET="pulsored-sam-${CUENTA}-${REGION}"
paso "Bucket de artefactos ($BUCKET)"
aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null || aws s3 mb "s3://$BUCKET" --region "$REGION"

paso "sam deploy ($STACK_NAME)"
OVERRIDES=("DeployMock=${DEPLOY_MOCK:-true}")
[ -n "${OPS_EMAIL:-}" ] && OVERRIDES+=("OpsEmail=$OPS_EMAIL")
sam deploy --template-file template.yaml --stack-name "$STACK_NAME" --s3-bucket "$BUCKET" --region "$REGION" \
  --capabilities CAPABILITY_IAM --no-confirm-changeset --no-fail-on-empty-changeset \
  --parameter-overrides "${OVERRIDES[@]}" \
  || fallo "falló sam deploy. Revisa los eventos del stack: aws cloudformation describe-stack-events --stack-name $STACK_NAME"

if [ "$SEED" = 1 ]; then
  paso "Secreto y claves (Secrets Manager)"
  python tools/bootstrap_secrets.py
  paso "Catálogo (12 zonas, 60 antenas), configuración (SSM) y endpoints"
  python tools/seed_catalog.py
fi
./check.sh
paso "Listo"
echo "API:    $(salida_stack ApiUrl)"
echo "Claves: .local-env/claves.json (fuera de Git; ./tools/claves.sh para verlas)"
echo "Demo:   ./demo.sh"
