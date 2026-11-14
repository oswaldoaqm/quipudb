# Plan de commits manuales — Issue #277

Se proponen **29 commits significativos**, organizados por responsabilidades y
pruebas reales. Los comandos de esta tabla son exclusivamente para ejecución
manual del usuario; no se ejecutó staging, commit ni publicación.

Ejecutar desde la raíz `/Users/mauricioteran/Documents/proyecto_bd/quipudb`, en la
rama actual. Los mensajes se cotejaron con `.github/scripts/check-commits.sh`:
tipos y alcances permitidos, inicio en minúscula, máximo 72 caracteres y sin punto
final. No se utiliza el alcance `distributed`, que no está permitido.

Cada fila selecciona archivos completos: **no requiere `git add -p`**. En archivos
existentes se registra únicamente el diff de esta entrega (auditoría, fixtures,
CI o enlaces/estado documental). Si se introducen otros cambios antes de ejecutar
estos comandos, revisar el diff y separar los ajenos; no usar `git add .`.

| Orden | Objetivo específico | Archivos exactos | Comando git add | Comando git commit | Dependencias |
|---|---|---|---|---|---|
| 1 | Definir errores v1 y traducción de causas SQL | `engine/distributed/node_errors.py` | `git add engine/distributed/node_errors.py` | `git commit -m "feat(api): definir errores del servicio de nodo"` | Base #276 |
| 2 | Validar cuerpos, versiones e identidad local | `engine/distributed/node_requests.py` | `git add engine/distributed/node_requests.py` | `git commit -m "feat(api): validar peticiones internas de fragmentos"` | 1 |
| 3 | Admitir SQL local y comprobar pertenencia con el binder | `engine/distributed/admission.py` | `git add engine/distributed/admission.py` | `git commit -m "feat(parser): validar operaciones sql sobre fragmentos"` | 1; #276 |
| 4 | Auditar el mismo handle sin abrir otra Database | `engine/distributed/storage.py` | `git add engine/distributed/storage.py` | `git commit -m "refactor(storage): reutilizar auditoria del handle activo"` | Base #276 |
| 5 | Excluir servidores sobre un mismo directorio | `engine/distributed/node_lease.py` | `git add engine/distributed/node_lease.py` | `git commit -m "feat(storage): bloquear directorios de nodos activos"` | Ninguna nueva |
| 6 | Gestionar ciclo de vida y ejecución local serializada | `engine/distributed/fragments.py` | `git add engine/distributed/fragments.py` | `git commit -m "feat(api): implementar servicio local de fragmentos"` | 1–5 |
| 7 | Exponer las cuatro rutas HTTP y sobres v1 | `engine/distributed/node_api.py` | `git add engine/distributed/node_api.py` | `git commit -m "feat(api): exponer endpoints internos del nodo"` | 1–2, 6 |
| 8 | Arrancar un nodo con configuración explícita | `engine/distributed/node_server.py` | `git add engine/distributed/node_server.py` | `git commit -m "feat(api): agregar comando de arranque de nodos"` | 6–7; Uvicorn de requirements |
| 9 | Preparar fixtures reales y peticiones comunes de prueba | `engine/distributed/conftest.py`, `engine/distributed/node_test_support.py` | `git add engine/distributed/conftest.py engine/distributed/node_test_support.py` | `git commit -m "test(api): preparar fixtures de nodos reales"` | 4, 6 |
| 10 | Comprobar códigos HTTP y contexto de errores | `engine/distributed/test_node_errors.py` | `git add engine/distributed/test_node_errors.py` | `git commit -m "test(api): verificar sobres y codigos de errores"` | 1, 9 |
| 11 | Probar JSON, versiones y destino antes de ejecutar | `engine/distributed/test_node_requests.py` | `git add engine/distributed/test_node_requests.py` | `git commit -m "test(api): cubrir versiones e identidad de peticiones"` | 2, 9 |
| 12 | Probar admisión de INSERT y claves INT32 | `engine/distributed/test_admission_insert.py` | `git add engine/distributed/test_admission_insert.py` | `git commit -m "test(parser): validar inserciones y pertenencia de claves"` | 3, 9 |
| 13 | Probar las restricciones del SELECT interno | `engine/distributed/test_admission_scan.py` | `git add engine/distributed/test_admission_scan.py` | `git commit -m "test(parser): delimitar consultas locales de fragmentos"` | 3, 9 |
| 14 | Probar DELETE por PK y calificación de columnas | `engine/distributed/test_admission_delete.py` | `git add engine/distributed/test_admission_delete.py` | `git commit -m "test(parser): validar eliminaciones por clave primaria"` | 3, 9 |
| 15 | Comprobar adquisición, exclusión y liberación del lease | `engine/distributed/test_node_lease.py` | `git add engine/distributed/test_node_lease.py` | `git commit -m "test(storage): verificar exclusividad del directorio"` | 5 |
| 16 | Verificar INSERT real, duplicados y concurrencia | `engine/distributed/test_node_insert.py` | `git add engine/distributed/test_node_insert.py` | `git commit -m "test(api): comprobar inserciones reales y duplicados"` | 6, 9 |
| 17 | Verificar filtros y metadatos de lecturas vacías | `engine/distributed/test_node_scan.py` | `git add engine/distributed/test_node_scan.py` | `git commit -m "test(api): comprobar busquedas locales con filtros"` | 6, 9 |
| 18 | Verificar DELETE real y conservación de otros nodos | `engine/distributed/test_node_delete.py` | `git add engine/distributed/test_node_delete.py` | `git commit -m "test(api): comprobar eliminaciones e aislamiento"` | 6, 9 |
| 19 | Inspeccionar B+ y hash tras mutaciones y duplicados | `engine/distributed/test_node_indexes.py` | `git add engine/distributed/test_node_indexes.py` | `git commit -m "test(index): verificar indices tras mutar fragmentos"` | 6, 9 |
| 20 | Verificar arranque, auditoría, cierre y reapertura | `engine/distributed/test_node_lifecycle.py` | `git add engine/distributed/test_node_lifecycle.py` | `git commit -m "test(storage): comprobar ciclo de vida de los nodos"` | 4–7, 9 |
| 21 | Verificar HTTP, JSON, estados y rechazos sin mutación | `engine/distributed/test_node_api.py` | `git add engine/distributed/test_node_api.py` | `git commit -m "test(api): verificar endpoints y rechazos del protocolo"` | 7, 9 |
| 22 | Inducir fallo tras una escritura real sin prometer rollback | `engine/distributed/test_node_failures.py` | `git add engine/distributed/test_node_failures.py` | `git commit -m "test(api): comprobar resultados inciertos de escritura"` | 6–7, 9 |
| 23 | Comparar single con el motor: texto, espacial, vectores y audio | `engine/distributed/test_node_single.py` | `git add engine/distributed/test_node_single.py` | `git commit -m "test(api): preservar busquedas multimodales locales"` | 6–7 |
| 24 | Ejecutar dos servidores TCP reales y reiniciar sus procesos | `engine/distributed/node_process_support.py`, `engine/distributed/test_node_processes.py` | `git add engine/distributed/node_process_support.py engine/distributed/test_node_processes.py` | `git commit -m "test(api): verificar nodos http independientes y persistentes"` | 4, 7–9; permiso de puertos |
| 25 | Publicar configuración local exacta de dos nodos | `docs/ejemplos/catalogo-nodos-locales-v1.json` | `git add docs/ejemplos/catalogo-nodos-locales-v1.json` | `git commit -m "docs(catalog): agregar configuracion local de dos nodos"` | Base #276 |
| 26 | Publicar cuerpos JSON reutilizables con curl | `docs/ejemplos/nodo-insert-v1.json`, `docs/ejemplos/nodo-scan-v1.json`, `docs/ejemplos/nodo-delete-v1.json` | `git add docs/ejemplos/nodo-insert-v1.json docs/ejemplos/nodo-scan-v1.json docs/ejemplos/nodo-delete-v1.json` | `git commit -m "docs(api): agregar ejemplos de operaciones internas"` | 25; protocolo #275 |
| 27 | Ejecutar los ejemplos documentados contra el motor real | `engine/distributed/test_node_examples.py` | `git add engine/distributed/test_node_examples.py` | `git commit -m "test(api): ejecutar los ejemplos json de los nodos"` | 7, 9, 25–26 |
| 28 | Documentar operaciones locales por fragmento | `docs/operaciones-fragmentos.md` | `git add docs/operaciones-fragmentos.md` | `git commit -m "docs(storage): documentar operaciones locales por fragmento"` | 1–27 |
| 29 | Documentar servicio y evidencia final del issue 277 | `docs/arquitectura-distribuida.md`, `docs/contrato-distribuido-v1.md`, `docs/revision-issue-277.md`, `docs/commits-issue-277.md` | `git add docs/arquitectura-distribuida.md docs/contrato-distribuido-v1.md docs/revision-issue-277.md docs/commits-issue-277.md` | `git commit -m "docs(docs): documentar servicio y evidencia del issue 277"` | 1–28 |

## Por qué esta división

- Errores, petición, admisión, auditoría, lease, servicio, HTTP y CLI son fronteras
  distintas. Se incorporan en ese orden para que ningún módulo nuevo importe
  otro que todavía no esté registrado.
- Las pruebas se separan por funcionalidad: validación pura, cada operación real,
  índices, ciclo de vida, HTTP, fallos, modalidades y procesos. Cada archivo
  verifica una propiedad distinta; no son divisiones por cantidad de líneas.
- El harness multiproceso y su prueba se agrupan: el harness sin consumidores no
  sería una entrega independiente útil. Igual criterio para fixtures y helpers.
- No se fragmenta `admission.py` por ramas de un mismo condicional ni
  `fragments.py` por métodos que dependen del mismo ciclo de vida: sería introducir
  estados intermedios incompletos para aumentar el número de commits.
- Se separan configuración de despliegue y cuerpos de operaciones porque son
  artefactos distintos; sus pruebas se incorporan después de ambos.
- Los tres documentos finales y sus referencias se agrupan para no dejar enlaces
  rotos entre commits. Incluyen resultados reales, instrucciones para Oswaldo y
  este plan; no se crean commits adicionales de correcciones de estilo.

## Comprobaciones manuales

Antes de cada commit, revisar `git diff --cached` y el conjunto seleccionado.
No incluir `.Rhistory`, `.venv`, build, caches, logs o datos temporales de pruebas.
Después de registrar los commits, el usuario puede ejecutar el validador:

```bash
bash .github/scripts/check-commits.sh fb05e20 HEAD
```

Ese rango toma como base el HEAD recibido con #276, sin requerir cambios de rama
ni un rebase. No se ha ejecutado contra commits #277 porque todavía no existen.
Las verificaciones de mensajes de esta entrega validaron los textos propuestos,
no un historial ficticio.

[Guía del servicio](operaciones-fragmentos.md) · [Resultados y criterios](revision-issue-277.md).
