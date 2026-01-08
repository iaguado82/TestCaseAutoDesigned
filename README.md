A-Intelligence Workflow

Automatización avanzada de Test Cases con IA (Telefónica I+D)

Este proyecto implementa un flujo automatizado, determinista, seguro y auditable para el diseño de Test Cases a partir de User Stories en entornos Telefónica (TID), integrando Jira, Confluence y modelos LLM corporativos (GitHub Copilot / GitHub Models).

El sistema transforma requisitos funcionales dispersos (User Stories, dependencias, épicas y documentación técnica) en Test Cases manuales estructurados, con:

clasificación System / End-to-End,

evaluación de candidaturas de automatización,

generación de código de automatización cuando aplica,

vinculación jerárquica completa en Jira,

sin intervención humana en el diseño de las pruebas, y con control explícito de contexto, tokens y estabilidad.

1. Arquitectura del Sistema

El sistema sigue una arquitectura modular y orientada a responsabilidades, diseñada para operar en entornos corporativos con restricciones de seguridad, trazabilidad y control de costes.

El flujo es determinista: a igualdad de entrada y fuentes, el resultado es reproducible.

Flujo conceptual

Una User Story se selecciona como punto de entrada.

El sistema resuelve la fuente real de requisitos (truth sources).

Se expande contexto técnico de apoyo de forma controlada.

Se genera inventario técnico y escenarios bajo contrato estricto.

Se valida cobertura completa antes de crear cualquier Test Case.

Se publican los Test Cases en Jira con enlaces jerárquicos correctos.

2. Modelo Conceptual Clave
2.1 Separación estricta de responsabilidades

El sistema distingue explícitamente entre qué información define requisitos y qué información solo aporta contexto, evitando mezclas implícitas que degraden la calidad de las pruebas.

A) Fuente de Verdad (Truth Sources)

Información prioritaria y obligatoria para el diseño de pruebas:

La User Story ejecutada, procesada siempre al 100%.

Issues enlazados mediante relaciones de dependencia funcional (configurables por entorno), cuando el requisito real reside fuera de la US original.

Estas fuentes:

Constituyen la base semántica del inventario técnico.

Nunca se omiten.

Solo se recortan de forma explícita y señalizada en casos extremos de limitación de contexto.

B) Contexto Ampliado (Supporting Context)

Información de apoyo para mejorar precisión y cobertura:

Issues Jira mencionados en las descripciones de las fuentes de verdad.

Documentación Confluence enlazada explícitamente.

Documentación técnica asociada a la épica o anchor.

Jerarquía de épicas (Epic → Parent Epic).

Este contexto:

No define requisitos funcionales.

Está limitado por presupuesto.

Se degrada de forma controlada para garantizar estabilidad.

3. Resolución de Jerarquía y Enlaces
3.1 Cadena funcional soportada

El sistema soporta múltiples topologías de proyecto:

Proyectos donde la US contiene toda la definición funcional.

Proyectos donde la US actúa como contenedor y la definición reside en otra US dependiente.

Proyectos con distintos nombres de relaciones Jira (configurables por entorno).

3.2 Reglas de enlace de Test Cases
Test Cases System

Se vinculan únicamente a la User Story ejecutada.

Test Cases End-to-End (E2E)

Se vinculan a la épica anchor (por ejemplo JEFE-XXX).

Y adicionalmente a la User Story.

Esto garantiza:

Visión E2E a nivel programa o iniciativa.

Visión funcional a nivel de User Story.

4. Flujo de Generación de Pruebas
Paso 1 – Recolección de Verdad

Se procesa íntegramente la descripción de la User Story.

Se detectan automáticamente issues enlazados por dependencia.

Estos issues se incorporan como fuentes de verdad adicionales.

Paso 2 – Expansión de Contexto

Se extraen referencias a otros issues Jira.

Se procesan enlaces Confluence.

Se controla explícitamente:

profundidad,

deduplicación,

tamaño máximo por bloque.

Paso 3 – Generación bajo Contrato

El modelo LLM opera bajo un contrato estricto, que exige:

Inventario técnico numerado de 1 a N.

Inventario atómico (una única verificación observable por punto).

Correspondencia exacta 1:1 entre inventario y escenarios.

Clasificación System / E2E basada en heurística UI/UX.

Campos obligatorios por escenario:

automation_candidate

automation_type

automation_code (cuando aplica)

Paso 4 – Validación y Completado Iterativo

Tras la generación inicial:

Se detectan los inventory_id no cubiertos.

Se lanzan iteraciones de completado dirigidas, solicitando solo los IDs faltantes.

Cada iteración:

No vuelve a generar inventario.

Usa un contexto compacto y controlado.

Devuelve exclusivamente los escenarios solicitados.

El proceso se repite hasta:

Alcanzar cobertura completa, o

Superar el número máximo de intentos permitidos.

⚠️ Si no se alcanza cobertura total, no se crea ningún Test Case en Jira.

Calidad de los autocompletados

No se degrada la calidad porque:

El qué probar ya está definido por el inventario.

El modelo solo materializa escenarios ya especificados.

No se le delega descubrimiento de requisitos.

Paso 5 – Post-procesado previo a Jira

Antes de publicar:

Normalización de Jira Wiki Markup.

Inserción automática de:

bloque informativo de automatización,

KPIs cuando aplica.

Conversión opcional a plantillas corporativas mediante post-proceso local.

Todo ello sin consumo adicional de tokens LLM.

5. Control de Tokens y Estabilidad

El sistema incorpora defensas explícitas frente a errores habituales en LLMs:

413 Request Too Large

429 Rate Limit Reached

Gestión preventiva

Presupuestos configurables por bloque:

Truth

Contexto ampliado

Confluence

Estimación conservadora de tokens basada en tamaño de texto (chars → tokens).

Gestión reactiva

El propio error del modelo se utiliza como señal de ajuste.

Orden de degradación:

Documentación Confluence.

Contexto ampliado.

Recorte controlado de la truth (hard-clip).

Hard-clip

Nunca elimina la fuente de verdad.

Mantiene cabecera y cola del texto.

Señaliza explícitamente el recorte.

6. Seguridad y Privacidad
6.1 Soberanía de Datos

Los prompts no se usan para entrenar modelos globales.

Datos procesados solo en memoria.

Operación bajo contratos Enterprise aprobados.

6.2 Gestión de Credenciales

Uso exclusivo de variables de entorno.

Tokens con principio de mínimo privilegio.

Ningún secreto versionado en el código.

Todas las acciones quedan trazadas en Jira.

6.3 Seguridad en tránsito y ejecución

HTTPS (TLS ≥ 1.2).

Sin persistencia local de datos sensibles.

Compatible con ejecución local controlada y runners efímeros.

7. Configuración y Uso
7.1 Variables de entorno requeridas
Jira
JIRA_URL=https://jira.tid.es
JIRA_USERNAME=<usuario_jira>
JIRA_PERSONAL_TOKEN=<token_personal_jira>

Confluence
CONFLUENCE_URL=https://confluence.tid.es
CONFLUENCE_PERSONAL_TOKEN=<token_personal_confluence>

Proyecto destino de Test Cases
TARGET_PROJECT=MULTISTC


TARGET_PROJECT se resuelve por orden:

Parámetro CLI

Variable de entorno

Fallback en config.py

Modelos LLM corporativos
GITHUB_TOKEN=<token_github_models>
GITHUB_MODEL=<modelo_llm>

7.2 Ejecución

Punto de entrada: run.py

Ejecución básica
python run.py --issue MULTISTC-31379

Ejecución indicando proyecto destino
python run.py --issue MULTISTC-31379 --target-project MULTISTC

7.3 Resolución del Issue de entrada

Si no se proporciona --issue, se usa:

MANUAL_ISSUE_KEY=MULTISTC-31379


Si no se resuelve ningún issue válido, la ejecución aborta inmediatamente.

7.4 Códigos de salida
Código	Significado
0	Ejecución correcta
10	Rate limit diario
11	Reintentos agotados
12	Request too large
20	Cobertura incompleta (no se crean TCs)
30	Error Jira / IO
99	Error no controlado
8. Conformidad Técnica (MCP / Entorno Corporativo)

Uso exclusivo de variables de entorno estándar.

Autenticación mediante Bearer Tokens.

Sin dependencias no auditables.

Compatible con políticas MCP (Model Context Protocol).

Código estructurado, trazable y revisable para QA Senior y auditorías internas.

## Diagramas de Arquitectura

- [Diagrama de dependencias entre módulos (Mermaid)](docs/flow.mmd)
- [Diagrama de secuencia – orden temporal de ejecución (Mermaid)](docs/sequence.mmd)
