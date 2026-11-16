# Contexto del proyecto

Hackathon "Red TeleCorp" (3 nubes, equipo de 3). **Este repo es el módulo AWS** (el dueño del repo construye este módulo). Los otros: GCP = `TeleCorp-DataAnalytics`, Azure = repo aparte de otra persona.

## Dónde está todo
- **`docs/AWS.md` es el documento pilar**: alcance, reglas de dominio, API, modelo de datos, máquinas de estado, plan por días. Construye siguiéndolo.
- `contracts/` = contratos compartidos con Azure y GCP (`openapi-aws.yaml`, `events/*.json`, `zones.json`, `enums.json`). El código debe validar contra ellos. `zones.json` y `enums.json` se regeneran con `python3 tools/generate_catalog.py`.
- `docs/global/` = documentos del equipo completo (requerimientos, plan general, decisiones, módulo GCP). Si contradicen `docs/AWS.md`, manda `docs/AWS.md`.

## Reglas que no se deben romper
- Todo serverless, sin VPC/NAT/EC2. Costo mínimo (AWS Academy, rol `LabRole`, sin crear roles IAM).
- Umbrales de antena: 0 → INHABILITADA; <60 → CAIDA; 60–129 → NORMAL; >129 → SATURADA (se evalúa el 0 primero).
- Mensajes entre nubes: sobre común, entrega al menos una vez, receptores idempotentes (`event_id` determinista).
- Claves en Secrets Manager y configuración en SSM; nada sensible en el repo.
- Lo marcado **[VERIFICAR]** en `docs/AWS.md` no está comprobado en el Learner Lab real.
- **Borrar `docs/` antes de entregar** (el enunciado anula a quien entregue un informe).
