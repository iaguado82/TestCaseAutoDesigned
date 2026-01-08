A-Intelligence Workflow

Automatización avanzada de Test Cases con IA (Telefónica I+D)

Este proyecto implementa un flujo automatizado, seguro y auditable para el diseño de Test Cases a partir de User Stories en entornos Telefónica (TID), integrando Jira, Confluence y modelos LLM corporativos (GitHub Copilot / GitHub Models).

El sistema transforma requisitos funcionales dispersos (User Stories, dependencias, épicas y documentación técnica) en Test Cases manuales estructurados, con clasificación de automatización, KPIs de rendimiento y vinculación jerárquica completa, sin intervención humana en el diseño.

Arquitectura del Sistema

El sistema sigue una arquitectura orientada a eventos y determinista, con control explícito del contexto y del presupuesto de tokens, diseñada para operar en entornos corporativos con restricciones de seguridad y trazabilidad.

Flujo conceptual:

Una User Story se selecciona como punto de entrada.

El sistema resuelve la fuente real de requisitos (truth sources).

Se expande contexto técnico de apoyo de forma controlada.

Se genera inventario técnico y escenarios bajo contrato estricto.

Se valida cobertura completa antes de crear cualquier Test Case.

Se publican los Test Cases en Jira con enlaces jerárquicos correctos.

Modelo Conceptual Clave
2.1 Separación estricta de responsabilidades

El sistema distingue explícitamente entre dos tipos de información:

A) Fuente de Verdad (Truth Sources)

Información prioritaria y obligatoria para el diseño de pruebas:

La User Story ejecutada (descripción completa, siempre procesada al 100%).

Issues enlazados mediante el tipo de relación “is a dependency for” (configurable), cuando el requisito funcional real reside fuera de la US original.

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

Se degrada de forma controlada para garantizar estabilidad del sistema.

Resolución de Jerarquía y Enlaces
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

Esto garantiza trazabilidad completa:

Visión E2E a nivel programa o iniciativa.

Visión funcional a nivel de User Story.

Flujo de Generación de Pruebas
Paso 1 – Recolección de Verdad

Se procesa íntegramente la descripción de la User Story.

Se detectan automáticamente issues enlazados por dependencia.

Estos issues se incorporan como fuentes de verdad adicionales.

Paso 2 – Expansión de Contexto

Se extraen referencias a otros issues Jira.

Se procesan enlaces Confluence.

Se controla:

profundidad,

deduplicación,

tamaño máximo por bloque.

Paso 3 – Generación bajo Contrato

El modelo LLM opera bajo un contrato estricto que exige:

Inventario técnico numerado de 1 a N.

Inventario atómico (una única verificación observable por punto).

Correspondencia exacta 1:1 entre inventario y escenarios.

Clasificación System / E2E basada en heurística UI/UX.

Inclusión obligatoria de los campos:

automation_candidate

automation_type

automation_code (solo si procede)

Paso 4 – Validación y Completado Iterativo

Tras la generación inicial, el sistema valida la cobertura real:

Se detectan los inventory_id no cubiertos.

Se lanzan iteraciones de completado dirigidas, solicitando únicamente los IDs faltantes.

Cada iteración:

No vuelve a generar inventario.

Usa un contexto compacto y controlado.

Devuelve exclusivamente los escenarios solicitados.

Este proceso se repite hasta:

Alcanzar cobertura completa, o

Superar el número máximo de intentos permitidos.

⚠️ Si no se alcanza cobertura total, no se crea ningún Test Case en Jira, evitando publicaciones parciales o inconsistentes.

Calidad de los autocompletados

El uso de contexto resumido en esta fase no degrada la calidad de los tests, ya que:

El “qué probar” ya está definido por el inventario.

El modelo solo materializa escenarios previamente especificados.

No se le delega descubrimiento de requisitos en esta fase.

Paso 5 – Post-procesado previo a Jira

Antes de publicar:

Normalización de Jira Wiki Markup.

Inserción automática de:

KPIs de rendimiento cuando aplica.

Bloque informativo de automatización.

Conversión opcional a tablas corporativas mediante post-proceso local.

Sin consumo adicional de tokens LLM.

Control de Tokens y Estabilidad

El sistema incorpora defensas explícitas contra errores habituales en LLMs como:

413 Request Too Large

429 Rate Limit Reached

Gestión preventiva

Presupuestos configurables por bloque:

Truth

Contexto ampliado

Confluence

Estimación conservadora de tokens basada en tamaño de texto (chars → tokens).

Gestión reactiva

Dado que los límites reales del modelo no son predecibles con exactitud:

El propio error del modelo se utiliza como señal final de ajuste.

El sistema reacciona degradando el contexto de forma jerárquica.

Orden de degradación

Documentación Confluence.

Contexto ampliado.

Recorte controlado de la truth (hard-clip).

Hard-clip (última defensa)

Nunca elimina la fuente de verdad.

Recorta manteniendo cabecera y cola del texto.

Señaliza explícitamente el recorte en el contenido enviado al modelo.

Este enfoque prioriza siempre la coherencia funcional frente a la exhaustividad contextual.

Seguridad y Privacidad
5.1 Soberanía de Datos

Los prompts no se utilizan para entrenar modelos globales.

Los datos se procesan únicamente en memoria.

Operación bajo contratos Enterprise con proveedores aprobados.

5.2 Gestión de Credenciales

Uso exclusivo de variables de entorno.

Tokens con principio de mínimo privilegio.

Ningún secreto se versiona en el código.

Todas las acciones quedan trazadas en Jira bajo el usuario asociado al token.

5.3 Seguridad en tránsito y ejecución

Comunicaciones cifradas mediante HTTPS (TLS 1.2 o superior).

Sin persistencia local de datos sensibles.

Compatible con ejecución local controlada y runners efímeros corporativos.

Configuración y Uso

(sin cambios respecto al README original)

Conformidad Técnica (MCP / Entorno Corporativo)

Uso exclusivo de variables de entorno estándar.

Autenticación mediante Bearer Tokens.

Sin dependencias no auditables.

Compatible con políticas MCP (Model Context Protocol).

Código estructurado, trazable y revisable para QA Senior y auditorías internas.