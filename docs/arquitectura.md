# Arquitectura de QuipuDB

## Vista general

QuipuDB esta partido en dos capas con un contrato explicito entre ellas.

```
                 frontend/            (2.1.5)
                     |  HTTP
                 engine/api/          FastAPI
                     |
     +---------------+----------------+
     |               |                |
 engine/parser/  engine/planner/  engine/transactions/
   (2.1.3)        plan de ejec.       (2.1.4)
     |               |                |
     +---------------+----------------+
                     |  pybind11  (bindings/)
                     v
                  core/              C++20  (2.1.1 y 2.1.2)
        storage/  index/  external/  catalog/
```

## Capas

**core/ (C++20)** contiene todo lo que toca disco: paginas, registros, heap
file, archivo secuencial paginado, indices B+ agrupado y no agrupado,
extendible hashing y los external algorithms (sorting por k-way merge, hashing
para GROUP BY y JOIN). No sabe nada de SQL.

**engine/ (Python)** traduce SQL a operaciones del core. El parser produce un
arbol, el planner decide que estructura usar y en que orden, transactions
coordina el acceso concurrente, y api expone todo por HTTP.

**frontend/** consume unicamente la API. No habla con el core.

## Contratos entre modulos

Estos son los puntos donde el trabajo de una persona depende del de otra. Un
cambio aqui se avisa en el issue correspondiente antes de hacerlo.

| Contrato | Lo define | Lo consume |
|---|---|---|
| Operaciones de tabla e indice (`scan`, `search`, `range_search`, `insert`, `delete`) | core (2.1.1, 2.1.2) | parser (2.1.3), benchmarks (2.1.6) |
| Estructura del plan de ejecucion | planner + core | frontend (2.1.5) |
| Manejo de bloqueos alrededor de las operaciones de tabla | transactions (2.1.4) | core |
| Contrato HTTP de la API | api | frontend |

## Por que el plan de ejecucion es un contrato y no un detalle

La seccion 2.1.5 exige un Panel de Plan de Ejecucion que muestre que indices se
usaron y en que orden se ejecutaron las operaciones. Eso obliga a que cada
operacion del core reporte que estructura empleo. Se disena desde el inicio,
no al final.
