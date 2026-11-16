# TeleCorp-InfraMonitoring (módulo AWS)

Módulo de **infraestructura y automatización** de PulsoRed (Red TeleCorp): recibe la telemetría de las antenas, detecta fallas, orquesta con Step Functions y envía eventos a Azure y GCP sin perderlos.

100 % serverless (API Gateway, Lambda, DynamoDB, SQS, Step Functions, EventBridge, SNS, Secrets Manager, SSM). Sin VPC, NAT ni EC2. Todo corre con el rol `LabRole` de AWS Academy; no se crean roles IAM.

## Requisitos

`python3`, `uv` (o `python3` con `venv`), `zip` y `curl`. Una sesión activa de AWS Academy.

## Despliegue

```bash
./setup.sh      # instala AWS CLI, SAM, moto y pytest DENTRO del proyecto
# Pega las credenciales de AWS Academy (AWS Details > AWS CLI) en .local-env/home/.aws/credentials
./deploy.sh     # valida, despliega con SAM, crea el secreto, carga el catálogo y comprueba
```

**Todo queda dentro del proyecto:** AWS CLI, SAM, Python 3.12, cachés y credenciales están en `.local-env/` y `.venv/`, fuera de Git. No se escribe en `~/`. Para quitarlo: `./clean.sh`. Si caducan las credenciales, pega las nuevas en el mismo archivo y repite el comando. `config.env` (fuera de Git) fija el stack, la región, el correo de operadores y si se despliega el receptor simulado.

| Comando | Qué hace |
|---|---|
| `./deploy.sh` | Despliega o actualiza todo y lo comprueba |
| `./check.sh` | Comprobaciones de solo lectura del sistema desplegado |
| `./demo.sh` | Verificación completa de extremo a extremo (~4 min): episodio, idempotencia, PREVENTIVA y ESTRUCTURAL |
| `./demo.sh resilience` | Receptor caído → reintentos → DLQ → `event-redelivery` (~20 min). Requiere el simulador apagado |
| `./demo.sh status` | Colas, simulador y lo recibido por el receptor simulado |
| `./warmup.sh` | Reactiva los disparadores SQS y Streams (Academy los apaga al cerrar el laboratorio). Úsalo tras reiniciar el laboratorio |
| `./test.sh` | Pruebas locales (unitarias, contrato e integración con moto), sin AWS |
| `tools/claves.sh` | Muestra las claves `inbound` para entregarlas a Azure, GCP y las antenas |
| `./destroy.sh` | Elimina el stack, pidiendo confirmación |
| `./clean.sh` | Borra herramientas y credenciales locales; no toca AWS |

## Claves y endpoints

`./deploy.sh` genera el secreto `pulsored/secrets` (Secrets Manager) con 4 claves **inbound** (`telemetry`, `gcp`, `azure`, `operator`: las que otros usan para llamar a AWS) y las guarda en `.local-env/claves.json`. Las claves **outbound** (las que AWS usa con Azure y GCP) se las pides a ellos:

```bash
source scripts/entorno.sh
python tools/bootstrap_secrets.py --set azure=CLAVE_AZURE gcp=CLAVE_GCP
python tools/seed_catalog.py --azure-url https://<function-app> --gcp-url https://<cloud-run>
```

Mientras no existan, los outbox apuntan a un **receptor simulado** (`mock-receiver`, desplegado con `DEPLOY_MOCK=true`) que valida la clave, es idempotente por `event_id` y puede simular una caída (`POST /mock/mode`). Al pasar a las URL reales, ponlo en `false` y redespliega.

## Estructura

```
template.yaml            AWS SAM (20 Lambdas, 4 máquinas, 8 tablas, 6 colas, alarmas, tablero)
src/common/python/common capa común: clasificación, máquina de confirmación, ingesta, emisor, config, secretos
src/functions/<nombre>/  una carpeta por Lambda
statemachines/*.asl.json las 4 máquinas de Step Functions
tests/{unit,contract,integration}  pytest + moto + jsonschema contra contracts/
tools/                   seed_catalog.py, bootstrap_secrets.py, e2e.py
contracts/               contratos compartidos con Azure y GCP (openapi, esquemas, zonas, enums)
```

Contratos compartidos con Azure y GCP: `contracts/`. Regenerar zonas y enums: `python3 tools/generate_catalog.py`.

## Restricciones del Learner Lab

Marcadas **[VERIFICAR]** en el diseño; `./deploy.sh` y `./demo.sh` las comprueban en la cuenta real: `LabRole` asumible por Lambda, Step Functions y API Gateway; Streams → Lambda; `StartMessageMoveTask` (si no se permite, `event-redelivery` usa la Lambda `dlq-redrive`); ventana de agrupación de 300 s en SQS. Las reglas de EventBridge apuntan a Lambdas (no necesitan asumir ningún rol).

Guardarraíles de costo: el simulador está **apagado** por defecto (`POST /simulator/stop` lo detiene); no hay pruebas de carga contra la API (superar la concurrencia de Lambdas puede bloquear la cuenta de Academy).
