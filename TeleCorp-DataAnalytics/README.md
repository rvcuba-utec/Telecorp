# Caso de Estudio: Red TeleCorp

## 1. Contexto y Problemática

**Red TeleCorp** es una empresa de telecomunicaciones que brinda servicios de telefonía móvil, Internet y conectividad a millones de usuarios distribuidos en diferentes ciudades y zonas geográficas. Debido a la gran cantidad de clientes, dispositivos, estaciones base y elementos que forman parte de su infraestructura, mantener la continuidad y calidad del servicio representa uno de los principales desafíos operativos de la organización.

La experiencia de un usuario no depende únicamente de que su dispositivo tenga señal. Una interrupción en una antena, una degradación del servicio, una saturación de la infraestructura o una falla localizada puede afectar llamadas, mensajes, navegación por Internet y otros servicios. En determinadas situaciones, el problema puede afectar solamente a algunos usuarios, mientras que en otras puede impactar a cientos o miles de personas ubicadas dentro de una misma zona.

---

### 1.1. Fuentes de Información y Parámetros de Simulación

Actualmente, la empresa recibe información proveniente de diferentes fuentes:
1. **Métricas generadas por las antenas.**
2. **Reportes realizados por los usuarios.**
3. **Registros generados por los sistemas internos.**

El volumen de información puede crecer considerablemente durante determinados periodos, por lo que resulta necesario contar con mecanismos capaces de procesar estos acontecimientos de manera oportuna.

Como parte del desafío, cada antena de la red genera periódicamente información relacionada con la cantidad de tráfico que está procesando. Para efectos de la hackathon, se establecen los siguientes umbrales operativos:

| Estado de la Antena | Rango de Tráfico (`GB / minuto`) |
| :--- | :--- |
| **Inhabilitada** | `0 GB/min` |
| **Caída** | `> 0 GB/min` y `< 60 GB/min` |
| **Normal** | `60 GB/min` a `129 GB/min` (inclusive) |
| **Saturada** | `> 129 GB/min` |

---

### 1.2. Gestión de Incidencias y Reportes de Usuarios

La información proveniente de las antenas no es la única fuente que debe ser considerada. **Red TeleCorp** desea mejorar el proceso mediante el cual los propios usuarios pueden reportar problemas relacionados con su servicio:

* **Portal de gestión de incidencias:** Debe permitir que una persona registre un inconveniente proporcionando:
  * Identificación del usuario.
  * Número asociado al servicio.
  * Ubicación aproximada.
  * Fecha y hora del inconveniente.
  * Tipo de problema.
  * Descripción de lo ocurrido.
* **Ciclo de vida y trazabilidad:** Los reportes deberán poder ser consultados y administrados posteriormente. Cada incidencia deberá mantener un **estado** y conservar un **historial** de su evolución:
  `Registrada` → `En revisión` → `Siendo atendida` → `Solucionada` → `Cerrada`

---

### 1.3. Dolores Clave a Resolver

1. **Detección de incidentes colectivos:**
   * Incidentes que inicialmente parecen aislados pueden corresponder a un problema generalizado (por ejemplo, múltiples reportes de falta de Internet en un mismo sector a lo largo de las horas).
   * **Regla de negocio:** Si se detecta que **10 o más usuarios diferentes** reportan un inconveniente relacionado con el servicio dentro de una **misma zona** en un **intervalo menor a 24 horas**, la organización deberá considerar que existe un **posible incidente colectivo** y realizar una verificación de la infraestructura correspondiente.
2. **Comunicación proactiva con los usuarios afectados:**
   * Cuando se determine una afectación zonal, los usuarios que reportaron el inconveniente deberán recibir comunicaciones por **correo electrónico y/o teléfono celular (SMS)** informando si el inconveniente está siendo evaluado, si se identificó una incidencia en la zona o si el servicio fue restablecido.
3. **Priorización inteligente:**
   * No todos los incidentes tienen la misma importancia según la cantidad de afectados (5 personas vs. miles de usuarios) y el tipo de zona afectada (**residencial** vs. **zona industrial, institución educativa, centro médico o servicios críticos**).
4. **Identificación de patrones y zonas críticas:**
   * Conocer qué zonas presentan mayor cantidad de incidentes, qué antenas fallan con mayor frecuencia y qué tipos de inconvenientes se reportan con mayor regularidad para apoyar la toma de decisiones.
5. **Detección de problemas estructurales (incidentes recurrentes):**
   * Una antena puede presentar varios problemas en días diferentes y *aparentemente recuperarse* tras cada evento. La acumulación de estos incidentes indica un posible **problema estructural** que requiere atención especializada.
6. **Reducción del tiempo de detección y correlación:**
   * Evitar que los problemas sean detectados primero por los usuarios antes que por la supervisión interna, estableciendo una correlación entre los reportes de usuarios y el comportamiento de la infraestructura.
7. **Resiliencia ante eventos masivos simultáneos:**
   * Soportar escenarios como cortes de energía, eventos climáticos o fallas masivas que generen miles de reportes en pocos minutos, sin pérdida de registros ni degradación considerable del servicio.
8. **Análisis histórico:**
   * Analizar la evolución de los incidentes, cuánto tiempo permanecieron activos, zonas afectadas, usuarios involucrados y frecuencia de fallas por elemento de infraestructura.
9. **Eficiencia de costos y arquitectura Serverless:**
   * Menor costo operativo posible, escalabilidad elástica según el volumen de usuarios/eventos y **sin depender de infraestructura permanentemente encendida**.

---

## 2. Requerimientos Funcionales — Módulo GCP

> **Responsabilidad del Módulo GCP:** Data Engineering, Analytics, Machine Learning Predictivo e IA Generativa (Gemini).

### A. Ingesta y Almacenamiento de Datos

| ID | Requerimiento | Descripción |
| :--- | :--- | :--- |
| **RF-GCP-01** | **Recepción de métricas de infraestructura** | El sistema deberá recibir de forma idempotente (mediante `POST /ingest/telemetry` y `POST /ingest/events`) las métricas históricas de tráfico y cambios de estado operativo de las antenas generadas por el módulo **AWS**. |
| **RF-GCP-02** | **Recepción de información de incidencias** | El sistema deberá recibir de forma idempotente (mediante `POST /ingest/incidents`) la información de los reportes e incidencias individuales y colectivas gestionadas por el módulo **Azure**, incluyendo datos agregados sobre usuarios afectados, zona, tipo de problema, prioridad, estado y tiempos de detección. |
| **RF-GCP-03** | **Validación de datos recibidos** | El sistema deberá validar la estructura, integridad y consistencia de los datos recibidos contra el catálogo único de zonas/antenas y marcas temporales, enviando los registros inválidos a una tabla/cola de rechazos. |
| **RF-GCP-04** | **Almacenamiento histórico optimizado** | El sistema deberá almacenar de manera persistente los registros históricos de métricas e incidencias en un modelo particionado por fecha y agrupado (*clustered*) por zona/antena, e incluir un histórico sintético inicial (~30 días) con patrones realistas para análisis y entrenamiento. |
| **RF-GCP-05** | **Integración de fuentes de información** | El sistema deberá integrar los datos provenientes de **AWS** y **Azure** en un modelo unificado, permitiendo relacionar las métricas de las antenas con las incidencias reportadas en sus respectivas zonas y periodos de tiempo. |

---

### B. Procesamiento y Analítica de Datos

| ID | Requerimiento | Descripción |
| :--- | :--- | :--- |
| **RF-GCP-06** | **Procesamiento de datos históricos** | El sistema deberá procesar, transformar y organizar los datos recibidos para facilitar su consulta, incluyendo el cálculo de duración activa de incidentes, usuarios afectados y tiempo de detección. |
| **RF-GCP-07** | **Análisis de zonas críticas** | El sistema deberá identificar las zonas geográficas con mayor concentración de incidencias y usuarios afectados, considerando el tipo de zona y sitios críticos. |
| **RF-GCP-08** | **Identificación de antenas problemáticas** | El sistema deberá identificar las antenas que presentan mayor frecuencia de fallas, degradaciones del servicio y saturaciones. |
| **RF-GCP-09** | **Identificación y análisis de problemas recurrentes (estructurales)** | El sistema deberá identificar los tipos de problemas más frecuentes y analizar/visualizar la recurrencia histórica de fallas por antena a lo largo del tiempo (en coordinación con la detección nocturna de eventos estructurales emitida por **AWS**). |
| **RF-GCP-10** | **Análisis de correlación** | El sistema deberá analizar la relación entre las métricas operativas de las antenas y las incidencias reportadas por los usuarios (caída de tráfico vs. aumento de reportes y desfase temporal entre ambos). |
| **RF-GCP-11** | **Visualización de información analítica** | El sistema deberá proporcionar dashboards (Looker Studio) que permitan consultar indicadores, gráficos y estadísticas sobre el comportamiento de la infraestructura y las incidencias. |
| **RF-GCP-12** | **Visualización geoespacial** | El sistema deberá representar geográficamente la ubicación de las antenas y la distribución de las incidencias, permitiendo identificar zonas críticas y concentraciones de problemas. |

---

### C. Inteligencia Artificial Predictiva y Generativa

| ID | Requerimiento | Descripción |
| :--- | :--- | :--- |
| **RF-GCP-13** | **Preparación de datos para Machine Learning** | El sistema deberá preparar una tabla/vista reproducible de características (*features*: tendencia, volatilidad, caídas previas, saturaciones e incidencias) a partir del histórico de infraestructura e incidencias. |
| **RF-GCP-14** | **Entrenamiento de modelos predictivos** | El sistema deberá entrenar modelos de aprendizaje automático (BigQuery ML) utilizando información histórica para identificar patrones asociados a posibles fallas o degradaciones de las antenas. |
| **RF-GCP-15** | **Predicción periódica de fallas** | El sistema deberá ejecutar periódicamente (cada ~15 min) la estimación de probabilidad de que una antena presente una falla o degradación dentro de un horizonte temporal determinado. |
| **RF-GCP-16** | **Registro de predicciones** | El sistema deberá almacenar cada predicción generada, incluyendo `antena_id`, `probabilidad` estimada de falla, `estado_previsto`, `horizonte` temporal, `model_version` y marca temporal. |
| **RF-GCP-17** | **Actualización de modelos** | El sistema deberá permitir actualizar y volver a entrenar los modelos predictivos utilizando nuevos datos históricos disponibles, registrando la versión por entrenamiento. |
| **RF-GCP-21** | **Clasificación de reportes con IA Generativa (Gemini)** | El sistema deberá exponer un servicio (`POST /ai/classify-report`) consumido por **Azure** que utilice **Gemini** sobre el texto del reporte (sin datos personales) para devolver un JSON validado con categoría normalizada, nivel de urgencia y mención de sitios críticos (hospital, colegio, industria). |
| **RF-GCP-22** | **Resumen operativo e hipótesis de causa con IA Generativa (Gemini)** | El sistema deberá exponer un servicio (`POST /ai/summarize-incident`) consumido por **Azure** que utilice **Gemini** para generar un resumen operativo para el operador, una hipótesis de causa raíz y una acción recomendada sobre una incidencia. |
| **RF-GCP-23** | **Control de calidad y privacidad en IA Generativa** | El sistema deberá validar el esquema JSON de salida de Gemini, aplicar tiempos de espera cortos (*timeouts*), registrar `model_version` y garantizar que ningún dato personal (documento/teléfono) sea procesado en los prompts. |

---

### D. Integración y Comunicación

| ID | Requerimiento | Descripción |
| :--- | :--- | :--- |
| **RF-GCP-18** | **Exposición de predicciones bajo demanda (Pull)** | El sistema deberá exponer el endpoint `GET /predictions` para que **AWS** (y la consola operativa) puedan consultar las predicciones vigentes por antena o zona, incluyendo probabilidad, estado previsto, horizonte y `model_version`. |
| **RF-GCP-19** | **Envío de predicciones preventivas hacia AWS (Push)** | El sistema deberá enviar periódicamente al módulo **AWS** (`POST /predictions` con reintentos) las predicciones cuya probabilidad sea `>= 0.50`, para que **AWS** evalúe la regla de alerta preventiva (`probabilidad >= 0.70` y `horizonte <= 24 h`) y dispare su flujo en Step Functions. |
| **RF-GCP-20** | **Consulta de resultados analíticos** | El sistema deberá permitir que los usuarios autorizados consulten los resultados de los análisis históricos y predictivos mediante su interfaz de visualización (Looker Studio). |
