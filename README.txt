A Intelligence Workflow: Automatización de Test Cases con IA (TID)

Este proyecto implementa un flujo automatizado de diseño de pruebas para Telefónica (TID), integrando Jira, Confluence y GitHub Copilot para transformar User Stories en Test Cases técnicos y estructurados sin intervención manual.

1. Arquitectura del Sistema

El sistema opera mediante una arquitectura orientada a eventos, utilizando GitHub Actions como motor de ejecución seguro.

Diagrama de Flujo (Mermaid)

graph TD
    A[Jira TID: User Story Created] -->|Webhook| B[GitHub Actions Runner]
    B --> C{Context Gathering}
    C -->|API Jira| D[Identify Epic & Parent Epic]
    D -->|API Confluence| E[Extract Tech Specs Field 22398]
    E --> F[GitHub Copilot API GPT-4]
    F -->|Reasoning| G[Generate Test Scenarios]
    G --> H[Create TCs in MULTISTC]
    H -->|Link| A
    H -->|Labels| I[IA_manual / IA_automatico]


Detalle del Proceso

Disparador: Una regla de Jira Automation envía un repository_dispatch a GitHub.

Recolección de Contexto: El script navega jerárquicamente: US -> Épica -> Épica Superior (relación is child of). Extrae la documentación técnica de Confluence vinculada.

Generación: Se envía el contexto a la API de Copilot bajo contrato Enterprise.

Acción Final: Se crean dos versiones por cada escenario (Manual y Automática con Selenium) en el proyecto MULTISTC, vinculándolos a la US original.

2. Análisis de Seguridad y Privacidad

Para cumplir con las normativas de seguridad de Telefónica, el diseño se basa en tres pilares fundamentales:

A. Soberanía de Datos

No Entrenamiento: Al utilizar GitHub Copilot Enterprise, los prompts enviados no se utilizan para entrenar modelos globales.

Procesamiento Efímero: Los datos se procesan en memoria y se descartan tras generar la respuesta.

Contrato Corporativo: Operamos bajo el paraguas legal de Telefónica con Microsoft/GitHub.

B. Gestión de Secretos e Identidad

Mínimos Privilegios: Los tokens (PATs) están limitados exclusivamente a las acciones de lectura/escritura necesarias.

GitHub Secrets: Las credenciales nunca están en el código; se almacenan cifradas en el repositorio.

Trazabilidad: Todas las acciones en Jira quedan registradas bajo el usuario asociado al token (id02621).

C. Seguridad en Tránsito y Ejecución

Cifrado TLS: Comunicaciones vía HTTPS cifradas (TLS 1.2+).

Aislamiento: Los Runners de GitHub Actions son contenedores efímeros que se destruyen tras el uso.

3. Configuración y Despliegue

Requisitos Previos

Python 3.10+

Acceso a la API de Jira y Confluence TID.

GITHUB_TOKEN con permisos de Copilot.

Instalación Local

Clona el repositorio.

Crea un archivo .env basado en env_example.txt.

Instala dependencias:

pip install requests python-dotenv


Ejecuta una prueba manual:

export MANUAL_ISSUE_KEY="PROJ-123"
python generate_tests_copilot.py


Despliegue en GitHub Actions

Configura los siguientes Secrets en tu repositorio:

JIRA_PERSONAL_TOKEN

CONFLUENCE_PERSONAL_TOKEN

GITHUB_TOKEN (Suministrado automáticamente por GitHub o un PAT específico).

4. Conformidad Técnica (MCP Compliance)

El script respeta las restricciones del entorno MCP (Model Context Protocol):

Uso de variables de entorno estándar (JIRA_USERNAME, CONFLUENCE_URL).

Autenticación mediante Bearer Tokens.

Compatibilidad con contenedores Docker Atlassian de TID.

Documentación generada para el equipo de QA Senior - Telefónica I+D