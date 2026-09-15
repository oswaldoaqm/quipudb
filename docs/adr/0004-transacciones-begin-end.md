# ADR 0004 - BEGIN y END TRANSACTION con bitacora de deshacer

- **Fecha:** 2026-09-15
- **Estado:** Aceptada
- **Issue:** #29

## Contexto

La seccion 2.1.4 pide agrupar varias operaciones para que se apliquen como una
sola unidad, con `BEGIN TRANSACTION` y `END TRANSACTION`. El core (ADR 0001)
no tiene log de escritura anticipada: cada `insert`/`remove` de `TableFile` e
`Index` queda en disco en cuanto se ejecuta (`core/include/quipudb/catalog/
table.hpp`). `database.hpp` ya deja escrito que el core no es seguro para
hilos a proposito, y que eso es responsabilidad del lock manager de 2.1.4
apoyado en `Database`; por simetria, agrupar operaciones tampoco es trabajo
del core, sino de la capa Python que ya lo consume (`engine/transactions/`,
segun `docs/arquitectura.md`).

El ADR 0003 dejaba `BEGIN`, `COMMIT` y `ROLLBACK` explicitamente fuera del
subconjunto SQL y el parser ya los rechazaba en `_UNSUPPORTED_WORDS`. Este ADR
cambia eso solo para `BEGIN TRANSACTION`/`END TRANSACTION`; `COMMIT` y
`ROLLBACK` como palabras SQL siguen sin soportarse.

## Decision

`BEGIN TRANSACTION` y `END TRANSACTION` se agregan como sentencias del mismo
subconjunto SQL del ADR 0003 (mismo `parse_sql`, mismo despacho en
`QueryProcessor.execute()`), sin pasar por `engine/parser/semantic.py` porque
no referencian tabla ni columnas.

`QueryProcessor` mantiene como mucho una transaccion activa a la vez (sin
anidamiento). Mientras esta activa, cada `INSERT`/`DELETE` que se ejecuta con
exito registra en una bitacora de deshacer (`engine.transactions.Transaction`)
la operacion inversa necesaria para revertirlo — reutilizando
`insert_with_indexes`/`delete_with_indexes` de `engine/executor/dml.py`, que ya
son atomicas por sentencia con rollback de mejor esfuerzo. `END TRANSACTION`
simplemente descarta la bitacora: como no hay WAL, los datos ya estan en disco
y "confirmar" no tiene nada mas que hacer. Un rollback (transaccion abandonada
via `QueryProcessor.rollback()`, o un error de dominio a mitad de la
transaccion) reproduce la bitacora en reversa y deja el estado igual al de
antes del `BEGIN`.

`CREATE TABLE` dentro de una transaccion activa es un error: es DDL, no hay
forma barata de deshacerlo sin borrar el archivo de la tabla, y no se
pretende DDL transaccional en este proyecto. `SELECT` se permite sin registrar
nada, porque no muta.

## Consecuencias

- "BEGIN/END agrupan operaciones" no implica un log de escritura anticipada ni
  recuperacion ante caida de proceso: si el proceso muere a mitad de una
  transaccion, lo que ya se aplico queda aplicado (igual que hoy sin
  transacciones). La bitacora solo cubre el caso donde el propio proceso
  decide deshacer — por un error de dominio o por un rollback explicito.
- Un error de dominio (por ejemplo `DuplicateKey`) a mitad de una transaccion
  aborta toda la transaccion automaticamente: el processor hace rollback de lo
  ya aplicado, limpia la transaccion activa y vuelve a lanzar el error
  original. Quien llama no queda con una transaccion a medio aplicar ni con
  una transaccion "colgada" que haya que cerrar a mano.
- El lock manager de #30 se construye encima de esto: una transaccion activa
  es el alcance natural para adquirir y liberar locks.
