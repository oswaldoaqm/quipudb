# Plan: Procesamiento de consultas SQL 2.1.3

**Generado**: 2026-09-12
**Última actualización**: 2026-09-13
**Complejidad**: Alta
**Issues asignados**: #24, #25, #26, #27 y #28
**Estado**: Issues #24, #25 y #26 integrados

## Registro de continuidad

Este archivo es la fuente de verdad para continuar el trabajo en otra sesión.

Decisiones confirmadas por el responsable de 2.1.3:

- Implementar todo el Query Processor mostrado en el flujo: parser, optimizador, ejecutor y plan de
  ejecución. La parte física de storage/index ya terminada se consume mediante bindings.
- Cubrir los cinco issues asignados, del #24 al #28.
- Ampliar bindings o core solo cuando sea imprescindible, conservando compatibilidad y agregando
  pruebas antes de integrar.
- Implementar únicamente el SQL requerido por esos issues.
- No implementar `UPDATE`.
- Usar lexer propio y parser descendente recursivo, sin dependencia externa de parsing.
- Mantener `Proyecto_Integrador_BD2.docx` fuera de Git.
- Trabajar por issue y por rama; nunca directamente sobre `main`.

Estado local al redactar este plan:

- `main` coincide con `origin/main` en `1421693`.
- Python: 10 pruebas pasan, un módulo se omite porque los bindings no están compilados.
- Ruff: sin errores.
- El core compila con tests desactivados.
- CTest no pudo configurarse localmente porque CMake no tuvo red para descargar GoogleTest.
- Los únicos archivos nuevos son este plan y el DOCX del enunciado; ninguno está committed.

Avance del issue #24 (2026-09-13):

- Rama creada: `feat/parser-tokenizer-gramatica-base`.
- ADR 0003 y contrato sintactico de arquitectura documentados.
- Posiciones, familia de errores, AST inmutable, tokenizer y parser de #24 implementados.
- API publica disponible mediante `from engine.parser import parse_sql, tokenize`.
- El parser cubre la sintaxis necesaria para #25–#28, pero no ejecuta ni consulta el catalogo.
- Validacion local: 148 pruebas de parser; suite completa con 158 aprobadas y 1 omitida por no tener
  `quipudb_native`; Ruff y compilacion del core sin tests aprobados.
- El DOCX continua fuera de Git.
- Commits funcionales locales: `5152e33`, `d023605`, `b809171`; la regla de exclusion del DOCX esta
  en `5215b8e`.
- Rama publicada en `origin/feat/parser-tokenizer-gramatica-base`; HEAD local y remoto verificados
  en `c503a58` antes de registrar esta actualizacion.

Avance del issue #25 (2026-09-13):

- El PR #75 se integro en `main` mediante el merge commit `d1e4ae3`; desde esa revision se creo
  `feat/parser-create-table-insert`.
- Se implementaron el IR semantico, la validacion de esquemas y literales, `QueryResult`,
  `QueryProcessor` y el adaptador diferido a `quipudb_native`.
- `CREATE TABLE` crea heap o secuencial y rechaza archivos fisicos preexistentes sin adoptarlos ni
  borrarlos. Si falla la creacion, el catalogo vuelve al estado anterior.
- `INSERT INTO` valida los cinco tipos, mantiene todos los indices secundarios registrados y aplica
  rollback de mejor esfuerzo ante un fallo intermedio.
- Los bindings exponen snapshots de solo lectura de `TableInfo` e `IndexInfo`; las copias sobreviven
  a reaperturas y no permiten modificar indirectamente el catalogo.
- Gate local aprobado: 299 pruebas C++, 287 pruebas Python con bindings reales, Ruff normal e
  import sorting, `git diff --check` y validador del historial.
- Commits funcionales previos a este registro: `0815e15`, `e2b2fc2`, `ac95f51`, `3f0ce53`,
  `aa8d19c`, `0c39088` y `4b913a0`.
- Rama publicada en `origin/feat/parser-create-table-insert`; PR #76 integrado en `main` con
  `Closes #25`. El DOCX continua fuera de Git.

Avance del issue #26 (2026-09-13):

- El PR #76 se integro en `main` mediante el merge commit `f681607`; desde esa revision se creo
  `feat/parser-select-where-igualdad`.
- Se implementaron el enlace semantico de proyecciones y predicados, un IR fisico inmutable, el
  adaptador de metadata y la seleccion determinista de PK, hash, B+ secundario o scan.
- El ejecutor cubre `SELECT *`, proyeccion, `=`, `<`, `<=`, `>`, `>=` y `BETWEEN`; los rangos
  estrictos agregan un filtro residual sobre la ruta inclusiva del core.
- Los planes separan busqueda de indice, `fetch`, scan, filtro y proyeccion, con tiempos y
  `OpStats` propios. Cada operacion reinicia sus contadores para no contaminar la siguiente.
- Las filas convierten los cinco tipos a escalares Python, incluido `quipudb_native.Date` a
  `datetime.date`; un RID secundario colgado se denuncia en vez de omitir una fila.
- Gate local aprobado: 299 pruebas C++, 390 pruebas Python con bindings reales, 319 pruebas sin
  bindings (2 omitidas como se espera), Ruff 0.16.7, `git diff --check` y validador del historial.
- Commits funcionales previos a este registro: `1988636`, `2c22f15`, `81c5486`, `64dd44a`,
  `675137e`, `1590b5c` y `9b1ac29`.
- Rama publicada en `origin/feat/parser-select-where-igualdad`; PR #77 integrado en `main` con
  `Closes #26`. La correccion de referencia #2 -> #4 quedo registrada en el issue. La documentacion
  final se traslado a `docs/docs-select-issue-26` porque el PR se integro mientras se publicaba ese
  ultimo commit. El DOCX continua fuera de Git.

Siguiente acción:

1. Integrar la documentacion final de #26 desde `docs/docs-select-issue-26`.
2. Continuar con #27 desde `main` actualizado con el issue #26.

## Resultado esperado

La entrega completa permitirá ejecutar este flujo:

```text
SQL
  -> tokenizer y parser
  -> AST
  -> análisis semántico
  -> optimizador y plan físico
  -> ejecutor
  -> quipudb_native
  -> QueryResult con filas, filas afectadas y Plan
```

El parser será puro: `parse_sql(sql)` no abrirá archivos ni importará `quipudb_native`. El acceso al
catálogo, la selección de índices y la ejecución vivirán fuera del parser sintáctico. Así las pruebas
del issue #24 podrán ejecutarse aun cuando el módulo C++ no esté compilado.

El contrato público propuesto será:

```python
statement = parse_sql(sql)
result = QueryProcessor(database).execute(sql)

result.columns
result.rows
result.affected_rows
result.plan
```

`Plan` seguirá usando las clases existentes de `engine/planner/plan.py` y el JSON del ADR 0002.

## Subconjunto SQL exacto

### Incluido

```sql
CREATE TABLE alumnos (
    codigo INT PRIMARY KEY,
    nombre VARCHAR(40),
    promedio DOUBLE,
    activo BOOL,
    ingreso DATE
) USING HEAP;

INSERT INTO alumnos VALUES (1, 'Ana', 15.5, TRUE, DATE '2026-09-12');

SELECT * FROM alumnos;
SELECT codigo, nombre FROM alumnos;
SELECT * FROM alumnos WHERE codigo = 1;
SELECT * FROM alumnos WHERE promedio BETWEEN 14 AND 18;
SELECT * FROM alumnos WHERE promedio < 18;

DELETE FROM alumnos WHERE codigo = 1;

SELECT * FROM alumnos ORDER BY promedio ASC;
SELECT * FROM alumnos ORDER BY promedio DESC;
SELECT activo, COUNT(*) FROM alumnos GROUP BY activo;
SELECT activo, AVG(promedio) FROM alumnos GROUP BY activo;
```

Reglas mínimas:

- `CREATE TABLE` admite los tipos `INT`, `DOUBLE`, `VARCHAR(n)`, `BOOL` y `DATE`.
- Se exigirá exactamente una columna `PRIMARY KEY`, porque `Schema` siempre necesita `key_column`.
- `USING HEAP` y `USING SEQUENTIAL` eligen la organización. Se propondrá `HEAP` como valor por
  defecto si la cláusula se omite; esta sintaxis se fijará en el ADR antes de codificar.
- `INSERT` es posicional y debe proporcionar un valor por columna.
- `SELECT` admite `*` o una lista simple de columnas.
- `WHERE` admite una condición simple: `=`, `<`, `<=`, `>`, `>=` o `BETWEEN` inclusivo.
- `DELETE` exige `WHERE`; no se habilitará borrado total implícito.
- `ORDER BY` admite una sola columna y `ASC` o `DESC`.
- `GROUP BY` admite una sola columna y `COUNT(*)`, `SUM`, `MIN`, `MAX` y `AVG`.
- Keywords sin sensibilidad a mayúsculas; identificadores preservados y resueltos exactamente como
  los almacena el catálogo.
- Una sola sentencia por llamada, con punto y coma final opcional.
- Strings usan el escape SQL `''`; las fechas usan `DATE 'YYYY-MM-DD'` y se convierten al tipo nativo.

### Excluido

- `UPDATE`.
- `JOIN`, aunque el core ya lo implemente.
- `CREATE INDEX`, `DROP`, `ALTER` y demás DDL.
- `NULL` y lógica de tres valores.
- `AND`, `OR`, `NOT`, subconsultas, expresiones aritméticas y funciones escalares.
- `HAVING`, múltiples columnas de orden o agrupación y aliases.
- Transacciones y concurrencia, que corresponden a 2.1.4.
- SQL espacial, búsqueda de texto y similitud multimedia de las partes posteriores.

Si un criterio escrito de #24–#28 contradice esta lista, prevalece el issue. Cualquier ampliación
adicional requiere actualizar primero este plan y el ADR.

## Issues verificados y orden de entrega

Los cinco issues están abiertos, asignados a `Sebastian1byte` y pertenecen al milestone de Parte 1.
Sus dependencias de core (#3, #7, #9, #15, #20 y #21) están cerradas.

| Issue | Resultado exigido | Rama sugerida en GitHub |
|---|---|---|
| #24 Tokenizer y gramática base de SQL | Tokens, AST, parser, posiciones y pruebas válidas/inválidas | `feat/parser-tokenizer-gramatica-base` |
| #25 CREATE TABLE e INSERT INTO | Crear esquema, elegir heap/secuencial, insertar y leer | `feat/parser-create-table-insert` |
| #26 SELECT con WHERE | Proyección, igualdad/rango, selección de índice o scan y plan | `feat/parser-select-where-igualdad` |
| #27 DELETE FROM con WHERE | Borrar y mantener todos los índices consistentes | `feat/parser-delete-from-where` |
| #28 ORDER BY y GROUP BY | External sort ASC/DESC, external group y explicación en el plan | `feat/parser-order-by-group` |

El issue #26 menciona el plan como `#2`, pero #2 es un PR de CI. El contrato real del plan es el issue
#4 y el ADR 0002. Antes de implementar #26 se dejará constancia de la corrección en el issue, sin
cambiar el contrato unilateralmente. El issue #4 está cerrado, aunque conserva pendiente la
confirmación explícita de la responsable del frontend; por eso cualquier cambio al JSON requiere
coordinación previa.

## Arquitectura propuesta

| Componente | Responsabilidad | Rutas previstas |
|---|---|---|
| Lexer | Tokens y posiciones | `engine/parser/tokens.py`, `engine/parser/lexer.py` |
| AST | Sintaxis sin resolver | `engine/parser/ast.py`, `engine/parser/span.py` |
| Parser | Texto a AST | `engine/parser/parser.py` |
| Semántica | Nombres, tipos y coerción | `engine/parser/semantic.py`, `engine/parser/bound_ast.py` |
| Catálogo abstracto | Evitar dependencia nativa en tests puros | `engine/parser/catalog.py` |
| Optimizador | Elegir PK, B+, hash o scan; ordenar operadores | `engine/planner/optimizer.py` |
| Ejecutor | Ejecutar operadores y medir stats | `engine/executor/` |
| Fachada | `QueryProcessor.execute` y `QueryResult` | `engine/executor/processor.py`, `engine/executor/result.py` |
| Adaptador nativo | Traducir metadata y errores del binding | `engine/planner/native_catalog.py` |

No se usará `Step` como AST. `Step` representa una operación física ya medida; el AST representa la
intención SQL y el IR enlazado representa columnas y tipos resueltos.

## Sprint 0: Contratos y seguridad de integración

**Objetivo**: cerrar las decisiones que afectan a varios módulos antes de escribir lógica.

**Incremento demostrable**:

- ADR con gramática, AST, semántica de CREATE y forma de `QueryResult`.
- Convención para planes de CREATE/INSERT/DELETE acordada con quien consume el JSON.
- Documento del issue #26 corregido o comentado para apuntar a #4.

### Tarea 0.1: Escribir el ADR del Query Processor

- **Ubicación**: `docs/adr/0003-procesamiento-consultas-sql.md`, `docs/arquitectura.md`.
- **Descripción**: fijar EBNF, tipos, `PRIMARY KEY`, `USING`, fechas, casing, errores, AST, IR y
  `QueryResult`.
- **Dependencias**: ninguna.
- **Criterios de aceptación**:
  - Toda consulta incluida en este plan tiene una única interpretación.
  - Toda construcción excluida tiene un error explícito.
  - Se documenta que no hay `UPDATE`, `NULL`, JOIN ni múltiples sentencias.
- **Validación**: revisión cruzada contra los cuerpos de #24–#28 y el DOCX.

### Tarea 0.2: Acordar el plan para DDL y DML

- **Ubicación**: `docs/adr/0002-plan-de-ejecucion.md`, `engine/planner/plan.py` si se aprueba una
  extensión.
- **Descripción**: decidir si `CREATE` se agrega a `Op` y cómo un único paso INSERT/DELETE reporta
  estadísticas de tabla y varios índices sin contarlas dos veces.
- **Dependencias**: Tarea 0.1.
- **Criterios de aceptación**:
  - Frontend puede dibujar el resultado sin lógica especial no documentada.
  - Los totales coinciden con la suma de operaciones físicas.
  - `UPDATE` puede permanecer en el enum heredado, pero no se parsea ni ejecuta.
- **Validación**: round-trip JSON y ejemplo de INSERT/DELETE con dos índices.

### Tarea 0.3: Definir la política Git por issue

- **Ubicación**: GitHub y Git local.
- **Descripción**: usar una rama y un PR por issue, en el orden #24 → #25 → #26 → #27 → #28.
- **Dependencias**: ninguna.
- **Criterios de aceptación**:
  - Cada PR usa `Closes #N` y parte de `main` con el PR anterior integrado.
  - El DOCX nunca aparece en staging.
  - No se usa squash, conforme a `CONTRIBUTING.md`.
- **Validación**: `git status`, `git diff --cached --name-only` y script de commits.

## Sprint 1: Issue #24 Tokenizer y gramática base

**Objetivo**: obtener un AST completo del subconjunto SQL con errores precisos, sin ejecutar nada.

**Demo**:

```python
from engine.parser import parse_sql

statement = parse_sql("SELECT * FROM alumnos WHERE codigo = 7;")
```

El comando funciona sin `quipudb_native` y el AST coincide con el esperado.

### Tarea 1.1: Crear posiciones y errores SQL

- **Ubicación**: `engine/parser/span.py`, `engine/parser/errors.py`, tests colocados en
  `engine/parser/test_errors.py`.
- **Descripción**: implementar `SQLError`, `SQLLexError`, `SQLParseError`, `SQLSemanticError` y
  `SQLUnsupportedError` con offset, línea, columna y fragmento.
- **Dependencias**: Tarea 0.1.
- **Aceptación**: un carácter inválido, token inesperado y EOF incompleto señalan su posición.
- **Validación**: pruebas parametrizadas de una y varias líneas.

### Tarea 1.2: Modelar el AST inmutable

- **Ubicación**: `engine/parser/ast.py`, `engine/parser/test_ast.py`.
- **Descripción**: dataclasses congeladas para CREATE, INSERT, SELECT, DELETE, tipos SQL,
  proyecciones, comparaciones, BETWEEN, ORDER BY, GROUP BY, agregados y literales.
- **Dependencias**: Tarea 1.1.
- **Aceptación**: igualdad estructural, spans en todos los nodos y cero imports nativos.
- **Validación**: tests de construcción, igualdad e inmutabilidad.

### Tarea 1.3: Implementar el lexer manual

- **Ubicación**: `engine/parser/tokens.py`, `engine/parser/lexer.py`,
  `engine/parser/test_lexer.py`.
- **Descripción**: reconocer identificadores, keywords, enteros, doubles, strings, operadores,
  puntuación y EOF.
- **Dependencias**: Tarea 1.1.
- **Aceptación**:
  - Keywords aceptan cualquier casing.
  - Strings con `''` y operadores `<=`/`>=` se tokenizan correctamente.
  - Los lexemas y spans originales se conservan.
- **Validación**: válidos, inválidos, espacios, saltos de línea y caracteres no permitidos.

### Tarea 1.4: Implementar el parser descendente recursivo

- **Ubicación**: `engine/parser/parser.py`, `engine/parser/__init__.py`,
  `engine/parser/test_parser.py`.
- **Descripción**: parsear toda la sintaxis incluida y consumir exactamente una sentencia.
- **Dependencias**: Tareas 1.2 y 1.3.
- **Aceptación**:
  - Los ejemplos de #24–#28 producen el AST esperado.
  - Punto y coma final es opcional; trailing input o segunda sentencia falla.
  - Construcciones excluidas generan error claro.
- **Validación**: matriz de consultas válidas/inválidas y `ruff check engine/parser`.

### Gate del issue #24

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest engine/parser -q -p no:cacheprovider
ruff check engine/parser --no-cache
git diff --check
```

## Sprint 2: Issue #25 CREATE TABLE e INSERT INTO

**Objetivo**: ejecutar DDL mínimo e inserción tipada de extremo a extremo.

**Demo**: crear una tabla heap y otra secuencial desde SQL, insertar los cinco tipos, cerrar/reabrir la
base y leer los registros.

### Tarea 2.1: Implementar el binder de esquemas y literales

- **Ubicación**: `engine/parser/catalog.py`, `engine/parser/bound_ast.py`,
  `engine/parser/semantic.py`, pruebas semánticas.
- **Descripción**: resolver tipos, construir `Schema`, calcular `key_column` y convertir literales sin
  importar bindings durante el parseo.
- **Dependencias**: #24.
- **Aceptación**:
  - Valida int32, `VARCHAR(n)`, NUL embebido, booleanos y fechas.
  - INSERT exige la misma aridad del esquema.
  - Errores semánticos aparecen antes de crear o escribir archivos.
- **Validación**: fakes de catálogo y casos límite equivalentes a `engine/test_bindings.py`.

### Tarea 2.2: Exponer metadata mínima del catálogo

- **Ubicación**: `bindings/module.cpp`, `engine/test_bindings.py`.
- **Descripción**: exponer copias inmutables de `TableInfo` e `IndexInfo`, incluidos storage,
  `page_size`, esquema, nombre de índice, columna y kind.
- **Dependencias**: acuerdo en #25, porque cambia una interfaz compartida.
- **Aceptación**:
  - Python enumera cero, uno o varios índices después de reabrir la base.
  - Las referencias no quedan colgadas al mutar el catálogo.
  - La API anterior conserva comportamiento y firmas.
- **Validación**: pruebas C++ existentes más tests de binding para crear/reabrir/eliminar metadata.

### Tarea 2.3: Crear QueryResult y QueryProcessor

- **Ubicación**: `engine/executor/result.py`, `engine/executor/processor.py`,
  `engine/executor/__init__.py`.
- **Descripción**: crear la fachada que parsea, enlaza y despacha sentencias; implementar CREATE.
- **Dependencias**: Tareas 0.2, 2.1 y 2.2.
- **Aceptación**:
  - `USING HEAP` y `USING SEQUENTIAL` invocan el kind correcto.
  - Tabla duplicada conserva `SchemaError` como causa.
  - El resultado y plan siguen el contrato acordado.
- **Validación**: crear, cerrar, reabrir y comprobar esquema/storage.

### Tarea 2.4: Ejecutar INSERT manteniendo índices existentes

- **Ubicación**: `engine/executor/dml.py`, tests de executor y bindings.
- **Descripción**: validar todo el registro, insertar en la tabla, agregar `(valor, RID)` en cada
  índice y revertir best-effort si una actualización intermedia falla.
- **Dependencias**: Tareas 2.2 y 2.3.
- **Aceptación**:
  - Insertar agrega exactamente una entrada a cada índice registrado.
  - Un PK duplicado no modifica índices.
  - Un error no se reporta como éxito y no deja entradas nuevas conocidas.
- **Validación**: cero/dos índices, clave secundaria repetida, error inyectado y reapertura.

### Gate del issue #25

- Crear heap y secuencial desde SQL.
- Insertar y leer los cinco tipos.
- Ejecutar pruebas puras, pruebas con binding, CTest y Ruff.

## Sprint 3: Issue #26 SELECT con WHERE

**Objetivo**: ejecutar proyección, igualdad y rango eligiendo la mejor ruta disponible y explicándola
en el plan.

**Demo**: la misma consulta devuelve las mismas filas sobre heap, secuencial, B+ agrupado, B+
secundario y hash; el plan muestra rutas distintas.

### Tarea 3.1: Resolver columnas y condiciones

- **Ubicación**: `engine/parser/semantic.py`, `engine/parser/test_semantic_select.py`.
- **Descripción**: validar tabla, proyección, columna del predicado y coerción del literal al tipo de
  la columna.
- **Dependencias**: #25.
- **Aceptación**: tabla/columna inexistente, tipos incompatibles y proyección inválida fallan con span.
- **Validación**: catálogo falso y cada tipo del core.

### Tarea 3.2: Implementar selección de ruta

- **Ubicación**: `engine/planner/optimizer.py`, `engine/planner/native_catalog.py`,
  `engine/planner/test_optimizer.py`.
- **Descripción**: elegir entre PK, índice secundario o scan según columna, operador y
  `supports_range()`.
- **Dependencias**: Tareas 2.2 y 3.1.
- **Aceptación**:
  - PK usa `search` o `range_search`.
  - B+ secundario usa `index_search/index_range` y `fetch`.
  - Hash solo se usa con igualdad; rango cae a scan + filter.
  - Ausencia de índice cae a scan + filter.
- **Validación**: árboles esperados y prueba explícita de que hash no recibe rango.

### Tarea 3.3: Ejecutar scan, búsqueda, fetch, filtro y proyección

- **Ubicación**: `engine/executor/operators.py`, `engine/executor/test_select.py`.
- **Descripción**: ejecutar la ruta física, convertir RIDs en registros y formar columnas/filas.
- **Dependencias**: Tarea 3.2.
- **Aceptación**:
  - Rango es inclusivo donde corresponde.
  - Resultado vacío no es error.
  - Todas las rutas físicas producen resultados equivalentes.
- **Validación**: pruebas diferenciales contra una evaluación Python simple.

### Tarea 3.4: Medir y construir el Plan

- **Ubicación**: `engine/executor/instrumentation.py`, tests del plan y SELECT.
- **Descripción**: resetear/capturar deltas de `OpStats`, medir tiempo propio y llenar `Step` sin
  contaminar consultas posteriores.
- **Dependencias**: Tarea 3.3.
- **Aceptación**:
  - Índice secundario produce hijo index + padre fetch, como ordena ADR 0002.
  - `totals` coincide con la suma del árbol.
  - Dos consultas sucesivas no mezclan estadísticas.
- **Validación**: JSON exacto para igualdad, BETWEEN y fallback scan.

### Gate del issue #26

- Corregir en el issue la referencia #2 → #4.
- Ejecutar SELECT `*`, proyección, igualdad, BETWEEN, `<` y `>`.
- Verificar índice aplicable, hash no aplicable y scan.
- Verificar estructura y contadores del plan.

## Sprint 4: Issue #27 DELETE FROM con WHERE

**Objetivo**: borrar por las mismas condiciones de #26 sin dejar RIDs secundarios colgados.

**Demo**: una fila con valores secundarios repetidos desaparece de la tabla y de todos sus índices;
las filas vecinas permanecen accesibles después de reabrir la base.

### Tarea 4.1: Exponer una lectura segura con RID

- **Ubicación**: preferentemente `bindings/module.cpp` y `engine/test_bindings.py`; tocar el contrato
  C++ solo si la solución de binding no puede ser correcta o eficiente.
- **Descripción**: materializar `(RID, record)` sin exponer el cursor invalidable. La opción inicial es
  un helper de binding que consume el cursor dentro de C++ y devuelve copias.
- **Dependencias**: #26 y aprobación en #27.
- **Aceptación**:
  - El usuario Python nunca conserva un cursor después de una mutación.
  - Cada registro vivo tiene el RID que `TableFile.read` resuelve.
  - La API anterior no cambia.
- **Validación**: heap con huecos reutilizados, secuencial y B+ agrupado.

### Tarea 4.2: Planificar candidatos de DELETE

- **Ubicación**: `engine/planner/optimizer.py`, pruebas de optimizer.
- **Descripción**: reutilizar las rutas de #26 y capturar todos los candidatos antes de mutar la tabla.
- **Dependencias**: Tarea 4.1.
- **Aceptación**: igualdad/rango por índice y scan devuelven el mismo conjunto de `(RID, record)`.
- **Validación**: condiciones sin coincidencias, una coincidencia y varias.

### Tarea 4.3: Borrar tabla e índices con rollback best-effort

- **Ubicación**: `engine/executor/dml.py`, `engine/executor/test_delete.py`.
- **Descripción**: quitar exactamente `(secondary_key, RID)` de cada índice con `remove_one`, borrar
  la PK y restaurar cambios conocidos si una operación intermedia falla.
- **Dependencias**: Tareas 2.2 y 4.2.
- **Aceptación**:
  - Nunca se usa `Index.remove(key)` para una sola fila con clave repetida.
  - `affected_rows` es exacto.
  - La búsqueda posterior y la reapertura no encuentran RIDs colgados.
- **Validación**: dos índices, claves repetidas, rango, cero coincidencias y fallo inyectado.

### Gate del issue #27

- DELETE por igualdad y rango.
- Comprobación en tabla, cada índice y después de reabrir.
- Plan DML conforme al acuerdo del Sprint 0.

## Sprint 5: Issue #28 ORDER BY y GROUP BY

**Objetivo**: conectar SQL con external sort y external group, incluidos ASC/DESC y el razonamiento del
plan.

**Demo**: buffers bajos fuerzan archivos temporales; ORDER BY produce ambos sentidos y GROUP BY
calcula todas las agregaciones con estadísticas visibles.

### Tarea 5.1: Añadir dirección a ExternalSort sin romper compatibilidad

- **Ubicación**: `core/include/quipudb/external/external_sort.hpp`,
  `core/src/external/external_sort.cpp`, pruebas C++ y `bindings/module.cpp`.
- **Descripción**: soportar ascendente y descendente en todas las fases del k-way merge, manteniendo
  ascendente como valor por defecto para que llamadas existentes no cambien.
- **Dependencias**: acuerdo explícito en #28, porque modifica 2.1.2 y el binding.
- **Aceptación**:
  - ASC conserva exactamente el comportamiento y costo anterior.
  - DESC funciona con cero, uno y varios runs.
  - Empates conservan estabilidad en ambos sentidos.
- **Validación**: tests C++ de memoria y disco, bindings y regresión completa de external sort.

### Tarea 5.2: Permitir pipelines Python sin lista intermedia

- **Ubicación**: `bindings/module.cpp`, `engine/test_bindings.py`.
- **Descripción**: adaptar un iterable/generador Python a `RecordSource` con vida útil segura, para
  pasar el resultado de WHERE a sort/group sin materializar la entrada completa.
- **Dependencias**: aprobación en #28.
- **Aceptación**:
  - Fuente de una sola pasada, mantiene vivo el iterable y propaga excepciones.
  - Una excepción limpia temporales.
  - No convierte todo el generador en una lista.
- **Validación**: generador grande, vacío, consumo único y excepción intencional.

### Tarea 5.3: Optimizar y ejecutar ORDER BY

- **Ubicación**: optimizer y operadores del ejecutor, pruebas de order.
- **Descripción**: filtrar, ordenar con `ExternalSort` y proyectar en el orden correcto; construir el
  paso `sort` con runs, pasadas, dirección y motivo.
- **Dependencias**: Tareas 5.1 y 5.2.
- **Aceptación**:
  - ASC y DESC son correctos con más datos de los que caben en buffers.
  - Heap no se considera ordenado accidentalmente.
  - El plan usa `external_sort` y explica runs/pasadas/dirección.
- **Validación**: comparación con `sorted(..., reverse=...)`, claves repetidas y buffers bajos.

### Tarea 5.4: Optimizar y ejecutar GROUP BY

- **Ubicación**: optimizer y operadores del ejecutor, pruebas de group.
- **Descripción**: traducir agregados a `AggregateSpec`, ejecutar `ExternalGroupBy` y usar su esquema
  de salida.
- **Dependencias**: Tarea 5.2.
- **Aceptación**:
  - COUNT/SUM/MIN/MAX/AVG coinciden con una referencia Python.
  - SUM/AVG rechazan columnas no numéricas antes de ejecutar.
  - El plan registra la estrategia realmente usada y si hubo fallback.
- **Validación**: vacío, claves repetidas, todas las funciones, HASH/AUTO y buffers bajos.

### Gate del issue #28

- ORDER BY ASC y DESC mediante external sorting.
- GROUP BY con todas las agregaciones soportadas.
- WHERE combinado con ORDER/GROUP sin lista de entrada completa.
- Plan con algoritmo, dirección, estrategia, E/S y explicación.

## Sprint 6: Integración, CI y documentación final

**Objetivo**: verificar los cinco issues juntos y dejar una entrega reproducible.

### Tarea 6.1: Crear suite de extremo a extremo

- **Ubicación**: `engine/integration/test_query_processor.py`.
- **Descripción**: ejecutar el recorrido CREATE → INSERT → SELECT → DELETE y consultas ORDER/GROUP
  sobre bases temporales.
- **Dependencias**: #24–#28.
- **Aceptación**:
  - Cubre literalmente cada criterio de aceptación de los cinco issues.
  - Reabre la base para verificar persistencia.
  - Compara rutas físicas equivalentes y sus planes.
- **Validación**: suite con bindings compilados y sin skips inesperados.

### Tarea 6.2: Ajustar CI para pruebas de integración

- **Ubicación**: `.github/workflows/ci.yml`.
- **Descripción**: mantener lexer/parser en el job Python y ejecutar la integración en el job que
  garantiza la existencia de `quipudb_native`.
- **Dependencias**: Tarea 6.1.
- **Aceptación**:
  - Las pruebas nativas nuevas no pueden omitirse silenciosamente.
  - No se agrega una dependencia de parser.
- **Validación**: reproducir localmente los comandos exactos de ambos jobs.

### Tarea 6.3: Documentar uso y límites

- **Ubicación**: `README.md`, `docs/arquitectura.md`, ADR 0003.
- **Descripción**: documentar instalación, compilación, API, ejemplos SQL, plan y exclusiones.
- **Dependencias**: suite estable.
- **Aceptación**: un integrante nuevo puede copiar el ejemplo y obtener filas más plan.
- **Validación**: ejecutar literalmente los ejemplos documentados.

## Estrategia Git y commits

Se usará una rama y PR por issue, con la rama sugerida en el propio issue. Cada commit incluirá lógica
y pruebas relacionadas; no se dejarán commits deliberadamente rojos.

Mensajes previstos, ajustables según el diff real:

### Issue #24

- `docs: definir el subconjunto sql del procesador`
- `feat(parser): modelar tokens y arbol de sintaxis`
- `feat(parser): implementar el analizador sql`
- `test(parser): cubrir consultas validas y errores`

### Issue #25

- `feat(api): exponer metadatos del catalogo a python`
- `feat(parser): crear tablas desde sql`
- `feat(parser): insertar registros desde sql`
- `test(parser): cubrir create table e insert`

### Issue #26

- `feat(planner): elegir rutas para igualdad y rango`
- `feat(parser): ejecutar select con where`
- `test(parser): verificar rutas y planes de select`

### Issue #27

- `feat(api): exponer registros con rid de forma segura`
- `feat(parser): borrar filas y mantener sus indices`
- `test(parser): verificar delete en todas las estructuras`

### Issue #28

- `feat(external): ordenar flujos en ambos sentidos`
- `feat(api): adaptar iterables python como fuentes`
- `feat(planner): planificar order by y group by`
- `feat(parser): ejecutar ordenamiento y agrupacion`
- `test(parser): cubrir external sort y group by`
- `chore(ci): ejecutar la integracion con bindings`
- `docs: documentar consultas y planes soportados`

Antes de cada push:

```bash
ruff check engine benchmarks --no-cache
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider

cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel
ctest --test-dir build --output-on-failure --no-tests=error

cmake -S . -B build-py -DCMAKE_BUILD_TYPE=Release \
  -DQUIPUDB_BUILD_PYTHON=ON
cmake --build build-py --parallel
PYTHONPATH="$PWD/build-py/bindings" \
  python -m pytest engine/test_bindings.py engine/integration -q -rs

git diff --check
bash .github/scripts/check-commits.sh origin/main HEAD
git status --short --branch
```

El hook está configurado con `core.hooksPath=.githooks`, pero el archivo `commit-msg` tiene modo
`100644` y Git puede omitirlo en Linux. Por eso el script de CI se ejecutará manualmente. No se usará
`git add .`; cada staging nombrará rutas explícitas para no incluir el DOCX.

## Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Metadata de índices no visible en Python | #26 no puede optimizar y DML no mantiene índices | Completar metadata en #25 con copias inmutables |
| DELETE no conoce el RID | Índices secundarios quedan corruptos | Helper seguro `(RID, record)` y `remove_one` |
| Fallo entre tabla e índices | Escritura parcial | Validar antes, orden definido y rollback best-effort; atomicidad completa queda para 2.1.4 |
| DESC no existe en ExternalSort | #28 incumplido o materialización completa | Extender comparator/merge con dirección y default compatible |
| WHERE antes de sort/group materializa | Se pierde la propiedad externa | Adaptador de iterable a `RecordSource` |
| Stats acumulativas | Planes y benchmarks incorrectos | Reset/deltas por operación y tests consecutivos |
| Flujo lazy pierde su owner | Use-after-free o stats incompletas | `keep_alive`, consumo único y resultado materializado al final |
| Cambio del JSON del plan | Frontend incompatible | Acordarlo antes con #4 y mantener round-trip/tests |
| CREATE no define PK | Schema nativo inválido | Exigir exactamente un `PRIMARY KEY` |
| DATE no serializable | Respuesta futura falla | Conversión ISO definida en `QueryResult` |
| CMake sin red | No se ejecuta el gate C++ local | Usar caché o CI antes de declarar listo el PR |
| DOCX entra por accidente | Historial contiene el enunciado | Staging explícito y revisión de `git diff --cached --name-only` |

## Definición de terminado

La sección 2.1.3 se considerará terminada solo cuando:

- #24–#28 tengan todos sus criterios cubiertos por pruebas.
- CREATE/INSERT/SELECT/DELETE/ORDER/GROUP funcionen desde SQL sobre el core real.
- El optimizador elija índice o scan de acuerdo con el contrato.
- ASC y DESC usen external sorting y GROUP BY use el algoritmo externo configurado.
- INSERT y DELETE mantengan todos los índices secundarios.
- Cada consulta devuelva un plan válido, medido y consumible por frontend.
- Ruff, pytest puro, pytest con bindings, CTest y validación de commits pasen.
- Cada issue tenga su rama publicada y un PR listo para revisión, sin el DOCX.
- README y arquitectura describan exactamente lo implementado y sus límites.

## Reversión

- Cada issue vive en una rama/PR separada y se integra en orden de dependencia.
- Los cambios de bindings conservan la API previa y tienen regresión propia.
- La dirección de sort usa ASC por defecto, por lo que código anterior mantiene comportamiento.
- Un cambio problemático se revierte con un commit de reversión en la rama; no se reescribe `main` ni
  se usa `git reset --hard` sobre trabajo compartido.
