# ADR 0005 - Lock manager con locks de tabla y timeout

- **Fecha:** 2026-09-16
- **Estado:** Aceptada
- **Issue:** #30

## Contexto

La seccion 2.1.4 pide que multiples transacciones puedan acceder a la BD de
forma simultanea sin corromperla: locks compartidos para lectura, exclusivos
para escritura, y una estrategia documentada de interbloqueo. `database.hpp`
(ADR 0001) ya dejaba dicho que el core no es seguro para hilos a proposito, y
que sincronizar el acceso es trabajo de esta capa (`engine/transactions/`).

El modelo de referencia es el que da `material/05 Recuperación ante
Fallos.pdf` ("Control de Concurrencia y Database Recovery", semana 05,
diapositivas 39-62): un "Concurrency Manager" que mantiene el estado de cada
elemento (`libre` / `compartido` con contador de lectores / `exclusivo`) y
hace esperar a quien no puede tomar el lock hasta que se libera. El mismo
material muestra, con el protocolo de actualizacion (PU, diapositivas 57-58 y
62), como dos transacciones que retienen lecturas compartidas y compiten por
actualizar el mismo elemento quedan esperandose mutuamente — el interbloqueo
que este ADR tiene que resolver.

## Decision

### Granularidad: por tabla

El material (diapositiva 41) deja explicito el trade-off de granularidad:

```
Base de datos > Tabla > Registro > Campo
grano mas fino → mas concurrencia, mas interbloqueo, mas costo de manejo
```

QuipuDB bloquea por **tabla**, no por registro, por tres razones:

1. En archivo secuencial y B+ agrupado un `INSERT` puede reorganizar o hacer
   split y mover registros de lugar; un lock "por registro" tendria que ser
   por clave primaria y no por RID, sumando una capa de traduccion mas.
2. `SELECT`/`DELETE` con rango o `scan` tocan un numero no acotado de filas.
   Un lock por registro tendria que escalar a "toda la tabla" en esos casos
   de todas formas — que es exactamente el lock de tabla.
3. El criterio de aceptacion del issue ("escrituras concurrentes al mismo
   registro se serializan correctamente") lo cumple un lock de tabla por
   construccion: es un superconjunto correcto del caso pedido, sin la
   complejidad adicional de locks de intencion para mezclar granularidades.

### Modos: compartido y exclusivo

`SELECT` pide `SHARED`; `INSERT`/`DELETE` piden `EXCLUSIVE`. Implementado
desde cero en `engine/transactions/locks.py` (`TableLock`, sobre
`threading.Condition`) siguiendo el mismo modelo de estado de las
diapositivas, pero indexado por `owner` en vez de anonimo: hace falta para
que una misma transaccion pueda repetir un lock que ya tiene sin bloquearse a
si misma, y para soportar upgrade `SHARED`→`EXCLUSIVE` cuando es la unica
lectora.

`CREATE TABLE` queda fuera de este mecanismo: ya esta prohibido dentro de una
transaccion (ADR 0004), y crear tablas con el mismo nombre en paralelo es
DDL/catalogo, no acceso a datos — fuera del alcance de este issue.

### Ciclo de vida: 2PL estricto dentro de una transaccion, por sentencia fuera de ella

Con `BEGIN...END TRANSACTION` explicito, el primer acceso a una tabla
adquiere el lock y lo retiene hasta `END TRANSACTION` o rollback (el mismo
patron que la diapositiva 51: varios `SREAD` retenidos hasta el `COMMIT`).
Sin transaccion explicita (autocommit), cada sentencia adquiere y libera su
lock alrededor de si misma. `Transaction` (ADR 0004) lleva la cuenta de que
locks tiene y los libera en `commit()`/`rollback()`; no hizo falta una
abstraccion nueva para esto.

### Interbloqueo: timeout

De las tres estrategias que menciona el issue (prevencion, deteccion,
timeout), se eligio **timeout**: cada `acquire` tiene un plazo (`5s` por
defecto, configurable); si no se cumple, lanza `LockTimeoutError`, subclase
de `TransactionError` (ADR 0004). Como `QueryProcessor` ya sabe abortar una
transaccion activa ante cualquier excepcion de una mutacion
(`_fail_in_transaction`), un timeout de lock entra por el mismo camino:
deshace lo aplicado y libera los demas locks que esa transaccion tenia,
rompiendo el interbloqueo sin necesitar un hilo de deteccion de ciclos ni un
grafo de espera. Cubre en particular el interbloqueo de upgrade que muestra
el material: si dos transacciones retienen `SHARED` y ambas piden
`EXCLUSIVE` al mismo tiempo, una de las dos agota el timeout, se deshace, y
libera su `SHARED` para que la otra pueda completar el upgrade.

### Un `LockManager` compartido explicitamente

`QueryProcessor` recibe un `lock_manager: LockManager | None` opcional; si no
se pasa, crea uno privado (asi el uso y las pruebas de #29 no cambian: cada
processor sin compartir manager hace locking en no-op). Para concurrencia
real — la demo de #32 y las pruebas de este issue — se construye un
`LockManager()` una vez y se pasa el mismo objeto a cada `QueryProcessor`
(uno por hilo/conexion) que comparte la misma `Database`.

## Consecuencias

- Dos transacciones que tocan tablas distintas nunca se bloquean entre si; el
  costo de este diseño se paga solo cuando de verdad compiten por la misma
  tabla, que es el caso que el issue pide demostrar.
- Una transaccion que toca dos tablas en orden distinto que otra puede
  interbloquearse (A→B contra B→A); el timeout lo resuelve automaticamente
  en vez de colgar el proceso, a costa de que una de las dos transacciones
  se aborte y tenga que reintentarse (responsabilidad de quien llama, no de
  esta capa).
- La demo de hilos de #32 se construye directo sobre este `LockManager`: un
  `Database` y un `LockManager` compartidos, un `QueryProcessor` por hilo.
