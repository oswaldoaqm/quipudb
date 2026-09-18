# Interfaz de usuario

[Volver al informe de la Parte 1](parte_1.md).

**Borrador técnico basado en código. Requiere aporte y revisión de Danna Gala**,
responsable declarada de 2.1.5. No acredita autoría ni aprobación del texto.

## Propósito y componentes implementados

La interfaz usa React y TypeScript con Vite. [App.tsx](../../frontend/src/App.tsx)
coordina el estado de la consulta, resultados y selección de tabla. Los paneles
son componentes separados; el editor usa Monaco y la tabla de resultados usa
virtualización para limitar las filas montadas en el DOM.

| Panel | Función implementada | Límite de interpretación |
|---|---|---|
| [Archivos](../../frontend/src/panels/FilesPanel.tsx) | Lista tablas y muestra esquema, organización, registros e índices | No constituye un cargador de archivos ni prueba de tablas nativas reales cuando usa mocks |
| [Consultas](../../frontend/src/panels/QueryPanel.tsx) | Editor SQL, ejecución y presentación de errores con posición | El editor no amplía la gramática aceptada por el engine |
| [Resultados](../../frontend/src/panels/ResultsPanel.tsx) | Filas, columnas, tipos y cantidad de resultados; render virtualizado | Virtualización del DOM no equivale a paginación en servidor ni evita recibir todas las filas |
| [Plan](../../frontend/src/panels/PlanPanel.tsx) | Árbol/pasos y métricas de tiempo y páginas | Un tipo de nodo representable no demuestra soporte SQL de esa operación |

La presentación distingue estadísticas de pasos y totales. Los formatos visuales
para valores nulos o nodos como JOIN no acreditan soporte SQL de `NULL` o `JOIN`.
La descripción de este capítulo procede del código; no informa una nueva prueba
visual o de integración realizada durante la redacción.

## Cliente, mocks y contrato HTTP

[client.ts](../../frontend/src/api/client.ts) selecciona la implementación de
`MotorClient`. La [configuración de ejemplo](../../frontend/.env.example) activa
`VITE_USE_MOCK=true`. En ese modo las respuestas se obtienen del simulador local,
sin ejecutar QuipuDB nativo.

El cliente HTTP prevé `POST /query` con cuerpo `{sql}` y `GET /tables`; sus
[tipos](../../frontend/src/api/types.ts) definen el contrato esperado. Cambiar
la configuración a modo HTTP solo selecciona otro cliente: no inicia un backend.
[engine/api/__init__.py](../../engine/api/__init__.py) no implementa todavía
rutas ni aplicación FastAPI. Por tanto, no se afirma integración end-to-end
frontend → HTTP → QueryProcessor → core en el estado documentado.

Para completar esa integración futura haría falta implementar y verificar el
adaptador HTTP, incluyendo traducción de resultados, errores, metadatos y planes.
Se documenta como trabajo pendiente, no se modifica ni implementa en este issue.

## Dominio y mediciones simuladas

Los [datos mock](../../frontend/src/api/mock/datos.ts) incluyen tablas académicas
para mostrar los paneles. El [simulador de consultas](../../frontend/src/api/mock/consulta.ts)
calcula respuestas y estadísticas ilustrativas con su propia lógica. No comparte
necesariamente todas las restricciones del parser ni del catálogo nativo.

En particular, esos datos no son el dataset experimental `codigo INT`,
`nombre VARCHAR(16)`, `promedio DOUBLE`; incluyen otros esquemas y una combinación
de tabla secuencial con índice secundario que el core no admite. La distinción se
detalla en [Dominio de datos](dominio_datos.md).

Los tiempos y páginas del mock no deben copiarse como resultados de rendimiento.
La evidencia oficial está únicamente en el
[capítulo experimental](comparacion_experimental.md), generado con bindings reales.
La interfaz no se utilizó para producir esas corridas.

## Limitaciones y revisión pendiente

La interfaz permite desarrollar y revisar la interacción por separado del motor,
pero el funcionamiento con mocks no valida persistencia, transacciones ni
concurrencia nativas. Los scripts del [package.json](../../frontend/package.json)
incluyen desarrollo, build y comprobación de tipos; su existencia no demuestra
que se hayan ejecutado ni sustituye pruebas de integración HTTP.

La responsable debe aportar/revisar la explicación de su diseño, comportamiento
visual y evidencia de validación. No se incorporan capturas inventadas ni se
atribuye una revisión visual a otra persona.
