# Procesamiento de consultas SQL

[Volver al informe de la Parte 1](parte_1.md).

**Borrador técnico basado en código y pruebas. Requiere aporte y revisión de
Sebastian Cangalaya Martinez**, responsable declarado de 2.1.3.
No acredita autoría ni aprobación del responsable.

## Propósito y recorrido de una consulta

[QueryProcessor](../../engine/executor/processor.py) ofrece la entrada Python
del motor SQL. Cada llamada recibe una sentencia, no un script SQL completo.

1. El [lexer](../../engine/parser/lexer.py) convierte el texto en tokens y
   conserva posiciones para explicar errores.
2. El [parser descendente recursivo](../../engine/parser/parser.py) construye
   un AST inmutable. Acepta un punto y coma final opcional.
3. La [validación semántica](../../engine/parser/semantic.py) resuelve tablas,
   columnas, tipos, literales y restricciones del subconjunto antes de ejecutar.
4. El [planificador](../../engine/planner/optimizer.py) elige accesos y operaciones
   por reglas usando metadatos del catálogo.
5. El ejecutor adquiere los locks correspondientes, invoca bindings y convierte
   la salida a [QueryResult](../../engine/executor/result.py). Las mutaciones
   mantienen tabla e índices mediante [primitivas DML](../../engine/executor/dml.py).

No se ejecuta SQL mediante una base externa. Los errores distinguen sintaxis,
semántica, capacidades no soportadas y fallos de ejecución. No todos los errores
ocurren dentro del mismo ámbito transaccional: analizar o validar una sentencia
no equivale a haber iniciado una modificación protegida por undo.

## Subconjunto realmente soportado

| Sentencia/capacidad | Alcance actual |
|---|---|
| `CREATE TABLE` | Columnas `INT`, `DOUBLE`, `VARCHAR(n)`, `BOOL`, `DATE`; una clave primaria; almacenamiento HEAP por defecto o SEQUENTIAL |
| `INSERT INTO ... VALUES (...)` | Una tupla posicional por sentencia |
| `SELECT` | `*` o proyección de columnas; filtro simple opcional |
| `WHERE` | Una comparación `=`, `<`, `<=`, `>`, `>=` o `BETWEEN` inclusivo |
| `ORDER BY` | Una columna, ASC o DESC |
| `GROUP BY` | Una columna; agregados `COUNT(*)`, `SUM`, `MIN`, `MAX`, `AVG`, sujetos a validación semántica |
| `DELETE FROM ... WHERE ...` | Requiere filtro; mantiene índices secundarios |
| `BEGIN TRANSACTION`, `END TRANSACTION` | Inicio y confirmación de transacción explícita |

No se soportan SQL `UPDATE`, `JOIN`, `CREATE INDEX`, `DROP`, `ALTER`, `COMMIT`,
`ROLLBACK`, `NULL`, subconsultas, alias, `HAVING`, `LIMIT` ni condiciones booleanas
generales con `AND`/`OR`/`NOT`. El `AND` de `BETWEEN` es parte de esa sintaxis,
no soporte de conjunciones arbitrarias. Tampoco hay listas generales de columnas
de agrupación u ordenamiento.

Las tablas B+ agrupadas y los índices secundarios se pueden crear mediante la
API nativa, no mediante estas sentencias SQL de creación. El planificador sí
puede utilizar estructuras existentes en el catálogo cuando su acceso es válido.
La disponibilidad de JOIN en C++/bindings no modifica la gramática.

## Selección de accesos y operadores

Sin filtro se realiza un recorrido. Para condiciones sobre clave primaria,
el planificador puede seleccionar `TableSearch` o `TableRange` antes de buscar
un índice secundario. El nombre del nodo describe la operación de la interfaz,
no su complejidad: la búsqueda pública PK de Heap sigue siendo lineal.

Cuando corresponde un acceso secundario, igualdad prefiere Hash extensible y
después B+ entre los índices elegibles; los desempates son deterministas.
Los rangos requieren un índice que los soporte: Hash no es candidato. Sin vía
aplicable se recorre y filtra. Para límites estrictos `<` o `>`, una consulta
nativa inclusiva puede complementarse con un filtro residual.

`ORDER BY` utiliza el ordenamiento externo; no se asume una optimización general
que elimine ese paso por existir un índice ordenado. `GROUP BY` usa agrupación
externa, con estrategia AUTO y su alternativa de ordenamiento cuando corresponda.
Las restricciones de proyección, tipos y agregados se verifican semánticamente.

El planificador es por reglas. La selección interna entre algoritmos de JOIN
nativo es otra decisión, no evidencia de un optimizador SQL de joins o basado
en costos globales.

## Modificaciones y coherencia con índices

`INSERT` obtiene los índices de la tabla, inserta el registro y añade las
entradas secundarias. `DELETE` determina candidatos con sus RIDs y registros
antes de modificarlos; esto evita invalidar un recorrido mientras se borra.
Cuando se necesitan RIDs para mantenimiento de índices, no basta una ruta que
solo entregue valores del registro.

Las primitivas DML incluyen acciones compensatorias ante fallos, y las
transacciones registran cómo deshacer operaciones exitosas. Esa compensación
no es un log durable ni garantiza recuperación ante cualquier fallo; su alcance
se explica en [Transacciones y concurrencia](transacciones_concurrencia.md).
No debe suponerse que insertar directamente en `TableFile` actualiza por sí solo
todos los índices del catálogo.

## Resultados, plan y límites de medición

El resultado SQL materializa filas en Python. Los nodos de plan de `SELECT`
registran pasos, tiempos y contadores instrumentados; DDL/DML no deben describirse
como si siempre produjeran ese mismo árbol (`plan` puede ser nulo).
Los tiempos de ejecución SQL incluyen trabajo de su capa Python y no son los
tiempos de los experimentos directos sobre bindings de #39/#40.

Páginas leídas/escritas no equivalen a accesos físicos al dispositivo. Los
contratos de plan tampoco prueban que el servidor HTTP o todos los nodos que
la interfaz puede representar estén implementados en el parser.

## Evidencia y revisión pendiente

- Sintaxis y errores: [lexer](../../engine/parser/test_lexer.py),
  [parser](../../engine/parser/test_parser.py) y
  [validación de SELECT](../../engine/parser/test_semantic_select.py).
- Accesos y planes: [optimizer](../../engine/planner/test_optimizer.py) y
  [plan](../../engine/planner/test_plan.py).
- Operadores: [SELECT](../../engine/executor/test_select.py),
  [DELETE](../../engine/executor/test_delete.py) y
  [orden/agrupación](../../engine/executor/test_order_group.py).
- Integración Python: [QueryProcessor](../../engine/test_query_processor.py).
- Contexto de diseño: [ADR de SQL](../adr/0003-procesamiento-consultas-sql.md)
  y [ADR de plan](../adr/0002-plan-de-ejecucion.md).

El responsable debe revisar este alcance, aportar sus decisiones de diseño y
confirmar las explicaciones de su sección antes de presentarla como contribución
individual terminada.
