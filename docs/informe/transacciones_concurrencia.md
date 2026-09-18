# Transacciones y concurrencia

[Volver al informe de la Parte 1](parte_1.md).

**Borrador técnico basado en código y pruebas. Requiere aporte y revisión de
Juan David Velo Poma**, responsable declarado de 2.1.4.
No acredita autoría ni aprobación del responsable.

## Propósito y alcance implementado

La capa Python agrupa modificaciones con `BEGIN TRANSACTION` y
`END TRANSACTION`, registra acciones inversas y coordina accesos por tabla.
Existe una transacción activa por `QueryProcessor`; no se admiten transacciones
anidadas ni DDL dentro de una transacción explícita. Las sentencias fuera de ella
usan el comportamiento autocommit del procesador.

La implementación combina [Transaction](../../engine/transactions/transaction.py),
[LockManager](../../engine/transactions/locks.py) y
[QueryProcessor](../../engine/executor/processor.py). Es una coordinación en
memoria, no un subsistema transaccional durable dentro del core C++.

## Algoritmo de deshacer

1. Cada inserción exitosa registra una acción inversa para eliminar el registro
   y sus entradas de índice.
2. Cada eliminación exitosa conserva los registros necesarios para reinsertarlos
   y restaurar índices. La reinserción puede recibir otro RID.
3. `END TRANSACTION` confirma descartando las acciones de deshacer y liberando
   locks; no realiza un protocolo de commit con WAL.
4. `rollback()` reproduce las acciones en orden inverso y libera los locks.

Las acciones inversas reutilizan [DML con índices](../../engine/executor/dml.py).
El rollback intenta continuar aun si una compensación falla: suprime excepciones
individuales durante el deshacer. Por ello no debe prometerse restauración completa
ante todos los errores de almacenamiento. Tampoco restituye necesariamente la
misma disposición física, tamaño de archivo o RID previo.

El procesador deshace ante fallos capturados en las rutas de modificación y
adquisición de locks. No todo error de sintaxis, validación o lectura equivale
a un aborto automático de transacción. `rollback()` está disponible en la API
Python; **la sentencia SQL `ROLLBACK` no pertenece al subconjunto actual**.

## Locks de tabla y control de concurrencia

Las lecturas usan locks compartidos y las modificaciones exclusivos. Varios
lectores pueden coexistir; el exclusivo requiere que no exista otro propietario
incompatible. El upgrade de compartido a exclusivo solo se concede cuando no
quedan otros lectores. Una variable de condición coordina la espera.

Una transacción explícita conserva sus locks hasta confirmar o deshacer. En
autocommit se liberan al terminar la sentencia. El timeout usa un reloj monotónico
y su valor predeterminado es 5 segundos; al expirar genera `LockTimeoutError`.
No hay un detector de ciclos de deadlock: un conflicto puede terminar por timeout.

Para coordinar varios procesadores deben compartir el mismo `LockManager`.
La protección es por tabla, en el mismo proceso y a través de estas rutas del
engine. No cubre procesos independientes ni accesos directos a los bindings
que omitan el coordinador. Tampoco implementa MVCC ni locks de fila.

## Demostración concurrente existente

[demo_concurrencia.py](../../engine/transactions/demo_concurrencia.py) muestra
una secuencia de lectura y modificación concurrente. El incremento se expresa
con lectura, borrado e inserción, no con SQL `UPDATE`. La variante protegida usa
transacciones y contempla reintentos tras timeout con una espera variable.

Esos reintentos pertenecen a la demostración, no constituyen una política universal
automática de `QueryProcessor`. La variante sin protección ilustra el riesgo de
intercalado; una ejecución particular no tiene por qué reproducir siempre la
misma carrera. Este informe no incorpora una nueva corrida de esa demostración
ni la presenta como parte de los benchmarks oficiales.

## Garantías que no deben afirmarse

- No existe WAL ni recuperación después de caída a partir de un log persistente.
- Las operaciones ya realizadas pueden haber escrito archivos antes del commit;
  la bitácora en memoria no sobrevive a la terminación del proceso.
- `flush` no demuestra por sí mismo un protocolo de durabilidad con `fsync`.
- La compensación best effort y los locks de tabla no justifican una afirmación
  general de ACID completo ni aislamiento para clientes que eviten el engine.
- La reinserción durante rollback restaura valores mediante operaciones normales;
  no revierte necesariamente estadísticas, organización física o espacio usado.

## Evidencia y revisión pendiente

Pruebas existentes: [undo/transacción](../../engine/transactions/test_transaction.py),
[locks](../../engine/transactions/test_locks.py),
[concurrencia](../../engine/test_concurrency.py) y
[demostración](../../engine/transactions/test_demo_concurrencia.py).
Decisiones relacionadas: [ADR BEGIN/END](../adr/0004-transacciones-begin-end.md)
y [ADR locks de tabla](../adr/0005-lock-manager-tabla.md).

El responsable debe aportar/revisar la explicación de las decisiones y garantías
antes de considerar cerrada su contribución individual al informe.
