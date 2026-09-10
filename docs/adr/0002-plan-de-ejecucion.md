# ADR 0002 - Estructura del plan de ejecucion

- **Fecha:** 2026-09-06
- **Estado:** Aceptada
- **Issue:** #4

## Contexto

La seccion 2.1.5 exige un Panel de Plan de Ejecucion que muestre que indices se
usaron, en que orden se ejecutaron las operaciones y cuantas paginas se
leyeron. La seccion 2.1.6 mide esas mismas cantidades para comparar
estructuras. Si cada modulo inventa su propia forma de reportar esto, el panel
se improvisa la ultima semana y los benchmarks no son comparables.

El core ya expone los insumos (ADR 0001, issue #3): cada `TableFile` e `Index`
declara su `kind()` y acumula `OpStats` (paginas leidas y escritas, registros
examinados y devueltos). Falta acordar como se agregan por consulta.

## Decision

El plan es un **arbol de pasos**. La API lo devuelve junto con los resultados
de cada consulta, como JSON, con exactamente esta forma:

```
Plan
  query      : str      la consulta tal como la escribio el usuario
  time_ms    : float    tiempo total, de parsear a devolver
  totals     : Stats    suma de stats de todo el arbol (comodidad para el panel)
  root       : Step

Step
  op         : str      que se hizo            (tabla de abajo)
  structure  : str      con que estructura     (tabla de abajo)
  table      : str|null tabla sobre la que opera
  column     : str|null columna de busqueda, de orden o de agrupacion
  detail     : str      texto libre y legible: el predicado, la clave, cuantos RIDs
  stats      : Stats    contadores propios del paso, sin incluir hijos
  time_ms    : float    tiempo propio del paso, sin incluir hijos
  children   : Step[]   pasos que se ejecutan antes y le entregan su salida

Stats                   espejo exacto de OpStats del core
  pages_read, pages_written, records_examined, records_returned : int
```

Un paso sin hijos lee directo del disco. Los hijos se ejecutan **antes** que el
padre (recorrido postorden), y ese es el "orden de operaciones" que el panel
dibuja.

### Valores de `op`

| `op` | Que hizo | Hijos |
|---|---|---|
| `scan` | Recorrido completo de la tabla | ninguno |
| `search` | Busqueda puntual por clave primaria en la tabla | ninguno |
| `range_search` | Busqueda por rango de clave primaria en la tabla | ninguno |
| `index_search` | Busqueda puntual en un indice secundario; entrega RIDs | ninguno |
| `index_range` | Busqueda por rango en un indice secundario; entrega RIDs | ninguno |
| `fetch` | Lee de la tabla los registros de los RIDs que entrega el hijo | 1 |
| `filter` | Evalua en memoria un predicado que ninguna estructura resolvio | 1 |
| `project` | Se queda con algunas columnas | 1 |
| `sort` | ORDER BY con external sorting | 1 |
| `group` | GROUP BY con external hashing | 1 |
| `join` | JOIN con hashing externo o indice | 2 |
| `limit` | Corta la salida | 1 |
| `insert` / `remove` | Escritura en la tabla y en sus indices | ninguno |

### Valores de `structure`

Los cinco primeros son, letra por letra, las constantes `kind::` de
`core/include/quipudb/catalog/table.hpp`. Los tres ultimos solo existen en la
capa Python.

| `structure` | Modulo |
|---|---|
| `heap` | storage |
| `sequential` | storage |
| `bplus_clustered` | index (implementa `TableFile`) |
| `bplus_unclustered` | index (implementa `Index`) |
| `extendible_hash` | index (implementa `Index`) |
| `external_sort` | external |
| `external_hash` | external |
| `memory` | el paso no toco disco: `filter`, `project`, `limit` |

### Reglas que el planner respeta al armar el plan

1. Una busqueda que resuelve un indice secundario siempre son **dos pasos**:
   `index_search` / `index_range` (hijo, estructura del indice) y `fetch`
   (padre, estructura de la tabla). Asi el panel muestra por separado las
   paginas del indice y las de la tabla, que es justo lo que 2.1.6 compara.
2. `extendible_hash` nunca aparece bajo `index_range`: el planner consulta
   `supports_range()` y, si es falso, cae a `scan` + `filter`.
3. `stats` y `time_ms` son propios del paso. Los totales se calculan sumando el
   subarbol; el panel no tiene que restar nada.
4. `detail` es para personas. Nadie lo parsea; si un dato hace falta como
   campo, se agrega un campo.
5. El paso `join` lleva en `structure` **la estructura que resolvio la
   busqueda**, no el nombre del algoritmo: `external_hash` cuando se
   particiono, y el `kind::` del indice o de la tabla que se sondeo cuando fue
   un index nested loop (`bplus_unclustered`, `extendible_hash`,
   `bplus_clustered`, `sequential`). No se agrego un valor `index_nested_loop`
   porque la columna `structure` responde "con que", y "cual de los dos
   algoritmos" ya se lee del par (`op`, `structure`) y se explica en `detail`.
   Es lo mismo que hace `index_search`, que tampoco dice "busqueda binaria".
6. En un index nested loop el hijo derecho del `join` **representa el conjunto
   de sondas, no un recorrido**: sus `stats` son la suma de todas las sondas
   -- las paginas del indice mas las de la tabla, juntas -- y no hay una
   pasada por el lado interno que dibujar. Sigue habiendo dos hijos, para que
   el panel no tenga que distinguir casos; lo que cambia es como se lee el de
   la derecha, y `detail` lo dice ("2 314 sondas por codigo").
   La regla 1 no aplica aqui: no se parte en `index_search` + `fetch` porque
   las dos mitades se pagan por fila externa y separarlas sugeriria dos
   pasadas que no existen.

## Ejemplos

Los tres que el panel (#37) debe soportar. Salen tal cual de
`engine/planner/test_plan.py`, asi que si cambian ahi, cambian aqui.

### Busqueda puntual, B+ agrupado

`SELECT * FROM alumnos WHERE codigo = 42`

```json
{
  "query": "SELECT * FROM alumnos WHERE codigo = 42",
  "time_ms": 0.41,
  "totals": {"pages_read": 3, "pages_written": 0, "records_examined": 1, "records_returned": 1},
  "root": {
    "op": "search",
    "structure": "bplus_clustered",
    "table": "alumnos",
    "column": "codigo",
    "detail": "codigo = 42",
    "stats": {"pages_read": 3, "pages_written": 0, "records_examined": 1, "records_returned": 1},
    "time_ms": 0.3,
    "children": []
  }
}
```

### Busqueda por rango, indice secundario sobre un heap

`SELECT * FROM alumnos WHERE promedio BETWEEN 15 AND 17`

```json
{
  "query": "SELECT * FROM alumnos WHERE promedio BETWEEN 15 AND 17",
  "time_ms": 2.1,
  "totals": {"pages_read": 13, "pages_written": 0, "records_examined": 24, "records_returned": 24},
  "root": {
    "op": "fetch",
    "structure": "heap",
    "table": "alumnos",
    "column": null,
    "detail": "lee 12 registros por RID",
    "stats": {"pages_read": 9, "pages_written": 0, "records_examined": 12, "records_returned": 12},
    "time_ms": 1.2,
    "children": [
      {
        "op": "index_range",
        "structure": "bplus_unclustered",
        "table": "alumnos",
        "column": "promedio",
        "detail": "promedio en [15, 17]",
        "stats": {"pages_read": 4, "pages_written": 0, "records_examined": 12, "records_returned": 12},
        "time_ms": 0.6,
        "children": []
      }
    ]
  }
}
```

### ORDER BY sin indice, external sorting sobre un scan

`SELECT * FROM alumnos ORDER BY nombre`

```json
{
  "query": "SELECT * FROM alumnos ORDER BY nombre",
  "time_ms": 48.0,
  "totals": {"pages_read": 120, "pages_written": 80, "records_examined": 20000, "records_returned": 20000},
  "root": {
    "op": "sort",
    "structure": "external_sort",
    "table": "alumnos",
    "column": "nombre",
    "detail": "k-way merge, 4 runs",
    "stats": {"pages_read": 80, "pages_written": 80, "records_examined": 10000, "records_returned": 10000},
    "time_ms": 31.0,
    "children": [
      {
        "op": "scan",
        "structure": "heap",
        "table": "alumnos",
        "column": null,
        "detail": "",
        "stats": {"pages_read": 40, "pages_written": 0, "records_examined": 10000, "records_returned": 10000},
        "time_ms": 14.0,
        "children": []
      }
    ]
  }
}
```

## Consecuencias

- El frontend dibuja un arbol generico: no necesita saber que estructuras
  existen, solo leer `op`, `structure` y `stats`. Agregar el R-Tree en la
  Parte 2 es agregar un valor a `structure`, no cambiar el panel.
- Los benchmarks (2.1.6) leen `totals` y `stats` de cada paso; las paginas
  leidas por el indice y por la tabla salen separadas sin instrumentacion
  extra.
- El tiempo se mide en el planner, no en el core (ADR 0001, issue #3), asi que
  `time_ms` incluye el costo del binding pybind11. Es el tiempo que el usuario
  percibe, que es lo que el panel muestra; para comparar estructuras entre si
  la metrica justa son las paginas.
- La implementacion de referencia es `engine/planner/plan.py`
  (`Plan`, `Step`, `Stats`, `Op`, `Structure`), con `to_dict` / `from_dict`
  probados ida y vuelta.
