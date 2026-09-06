# Auditoria de la seccion 2.1.1

- **Fecha:** 2026-09-06
- **Alcance:** todo lo entregado en los issues #3, #4, #6, #7, #8, #9, #10, #11, #12 y #13
- **Motivo:** el resto del proyecto se construye encima de estas clases. Antes
  de empezar 2.1.2 conviene saber sobre que estamos parados.

## Veredicto

Los cimientos aguantan. La separacion `TableFile` / `Index`, el formato de
pagina, el codec de registros de longitud fija y la free list del heap file
resistieron fuzzing diferencial contra un `std::map` de referencia (unas 350
semillas, 400-500 operaciones cada una, con reapertura): ni un fallo.

Pero hay **tres defectos criticos** que no salen en las 111 pruebas, y **tres
cambios de contrato** que conviene hacer ahora, mientras la interfaz la
consumen dos archivos y no quince.

Los tres criticos comparten una causa: las pruebas cubren el camino feliz y
los errores previstos, pero no los **datos hostiles** (un `'\0'` dentro de un
VARCHAR, un NaN como clave) ni las **combinaciones de estado poco probables**
(un grupo del archivo secuencial que queda entero borrado).

## Metodo

- Revision linea por linea de las 14 cabeceras e implementaciones de `core/`.
- Fuzzing diferencial de `HeapFile` y `SequentialFile` contra `std::map`.
- Programas de reproduccion para cada hallazgo, compilados con
  `-fsanitize=address,undefined`.
- Revision de la interfaz contra lo que exigen 2.1.2 a 2.1.6 y las Partes 2 a 4.

Nada de lo que sigue es una sospecha: todo esta reproducido ejecutando codigo.

---

## Criticos

### C1. Partir un grupo enteramente borrado desreferencia un puntero nulo

`core/src/storage/sequential_file.cpp:322`

```cpp
claves.push_back(registros[i * por_pagina].first);   // registros puede estar vacio
```

Si todos los registros de un grupo estan marcados, `registros` queda vacio,
`destino` vale 0, `paginas.resize(std::max(destino, 1))` fuerza una pagina y el
bucle indexa `registros[0]` sobre un vector vacio.

Reproducido: 20 paginas principales, se llena el overflow del grupo 0, se
borran sus registros (el desperdicio **global** queda en 0,095, asi que la
reorganizacion no se dispara) y se inserta una clave de ese rango.

```
main=20 overflow=1 vivos=1155
borre 110 del grupo 0, desperdicio global=0.095 (no dispara)
insertando en el rango del grupo vacio...
sequential_file.cpp:322:48: runtime error: member access within null pointer
```

El umbral de reorganizacion es global y el vaciado es local a un grupo, asi
que la condicion es normal en cualquier carga con borrados concentrados en un
rango de claves.

**Arreglo:** si `registros` esta vacio, no partir: sacar la pagina de la cadena
principal y del directorio. Y quitar el `max(..., 1)`.

### C2. Un VARCHAR con `'\0'` adentro pierde datos y deja el archivo inabrible

`core/src/catalog/record_codec.cpp:83` corta en el primer `'\0'`, pero
`Schema::validate` (`types.hpp:190`) solo comprueba la longitud, asi que acepta
el string. Consecuencias encadenadas, todas reproducidas:

1. `encode`/`decode` de `"ab\0cd"` devuelve `"ab"`: perdida silenciosa.
2. Dos registros distintos en memoria (`"ab"` y `"ab\0X"`) se vuelven la misma
   clave al releerlos: se viola la unicidad de la clave primaria sin que nada
   lance.
3. Al reabrir, `rebuild_keys` colapsa las dos claves en su `std::map` y el
   contador no cuadra:
   `'...heap' declara 2 registros vivos y en las paginas hay 1`.
   **El archivo queda permanentemente inabrible.**

**Arreglo:** rechazar en `Schema::validate` cualquier string con `'\0'`. No
cambia el formato en disco.

### C3. Un NaN como clave borra el registro equivocado

`compare` (`types.hpp:106`) solo usa `<`, asi que un NaN da 0 contra cualquier
valor: es "igual" a todo.

```
compare(NaN,1.0)=0  compare(1.0,NaN)=0
antes vivos=3  ->  remove(NaN) devolvio 1, vivos=2 -> quedan: 2 3
```

`remove(NaN)` **borro el registro de clave 1.0** y reporto exito. Ademas
`KeyLess` deja de ser un orden estricto, lo que es comportamiento indefinido
para el `std::map` de `HeapFile` y para los `std::sort` del secuencial.

**Arreglo:** rechazar NaN en `Schema::validate` para columnas `DOUBLE`, que es
lo que hacen los motores reales.

---

## Importantes

| # | Hallazgo | Donde |
|---|---|---|
| I1 | Una terminacion abrupta deja el archivo **inabrible sin ruta de reparacion**: los contadores del area meta y las paginas quedan desfasados por una operacion y la comprobacion es de igualdad estricta. Reproducido matando el proceso tras 400 inserciones. | `heap_file.cpp:107`, `sequential_file.cpp:140` |
| I2 | El area meta solo guarda `record_size`: se reabre con un esquema **distinto pero del mismo tamano** y devuelve basura sin avisar. Cambiar `key_column` tampoco se detecta, y en el secuencial eso invalida el orden fisico. | `heap_file.cpp:48`, `sequential_file.cpp:55` |
| I3 | `page_count_ + 1u` se evalua en 32 bits: con `page_count_` maximo da 0 y la defensa contra archivos truncados **no se aplica justo en el caso peor**. | `disk_manager.cpp:85` |
| I4 | Las cuatro mutaciones del catalogo modifican memoria y **despues** guardan. Si `save()` falla, memoria y disco divergen. | `catalog.cpp:126, 134, 181, 192` |
| I5 | Dos indices sobre la misma columna reciben **el mismo nombre de archivo**: se pisan en disco. El nombre no incluye el del indice, que es lo unico unico. | `catalog.cpp:180` |
| I6 | `physical_slots()` es una resta sin signo: con una pagina de `free_space` incoherente da `SIZE_MAX` y sale un `std::out_of_range`, no un `IoError`, rompiendo la promesa de `error.hpp` de que todo hereda de `quipudb::Error`. | `sequential_file.hpp:270` |

Menores, anotados sin desarrollar: `split_group` deja alguna pagina huerfana
(no corrompe, infla el espacio en disco); `key_column` fuera de rango lanza
fuera del contrato porque ni `HeapFile` ni `SequentialFile` llaman a
`Catalog::validate`; `record_count` de la cabecera de pagina se escribe pero
nunca se lee.

---

## Huecos para lo que viene

Ordenados por cuando duelen. Los tres primeros cambian firmas del contrato:
moverlas ahora cuesta poco, en un mes cuesta coordinar a cinco personas.

### 1. `create_index` no valida sobre que tipo de tabla se crea — duele en #16

Un `Index` guarda RIDs y los resuelve con `TableFile::read`. Los RIDs del
archivo secuencial **no son estables** (esta documentado). Pero
`Catalog::create_index` (`catalog.cpp:155`) nunca mira `info.storage`, asi que
acepta crear un B+ no agrupado sobre una tabla secuencial. El indice devolvera
registros equivocados **sin lanzar nada**, que es el peor modo de fallo posible
para un benchmark.

Tres lineas: `if (info.storage != kind::kHeap) throw SchemaError(...)`.

### 2. No hay forma de abrir una tabla desde el catalogo — duele ya

`docs/arquitectura.md` promete que "como se crea o abre un `TableFile` desde un
path lo define el catalogo (#7)". No lo define: `Catalog` devuelve un
`TableInfo` y un `path`, y cada consumidor (planner, parser, transacciones,
benchmarks, bindings) tiene que escribir su propio `if (storage == "heap")
new HeapFile(...)`. Son cinco copias del mismo switch.

Ademas **falta un dato para poder abrir**: `TableInfo` no guarda `page_size`, y
`DiskManager` lanza si el que se pide no coincide con el grabado. El 2.1.6
quiere variar el tamano de pagina; en cuanto creen una tabla con 8192 y la
reabran por el catalogo con el default 4096, no abre.

### 3. No se puede iterar registros de a poco — duele en #20

`TableFile` no tiene cursor: `scan`, `search` y `range_search` devuelven el
`std::vector<Record>` completo. Medido con 100 000 registros: el archivo ocupa
4,3 MB en disco y **14,5 MB en memoria** por cada `scan()` (un `Value` son 40
bytes). Un k-way merge de k runs necesitaria k x 14,5 MB antes de comparar la
primera clave: es exactamente lo contrario de un algoritmo externo.

Las tres salidas que existen hoy no sirven: iterar por RID relee la pagina una
vez por slot (no hay cache); leer las paginas con `DiskManager` no permite
decodificarlas (`slot_record` es privado); y el secuencial materializa igual.

Hace falta un `cursor()` en el contrato, o como minimo un
`scan(std::function<bool(const Record&)>)`. De paso arregla que
`reorganize()` tambien materialice todo.

### 4. El core no es seguro para hilos — duele en 2.1.4

No hay un solo `mutex`, `atomic` ni `thread` en `core/`. Cuatro puntos:

- **`scratch_` es una sola `Page` por archivo** y `slot_record()` devuelve un
  `span` que apunta dentro. Dos hilos **leyendo** — ni siquiera escribiendo —
  se pisan el buffer y uno decodifica bytes de la pagina del otro. Devuelve un
  registro bien formado y equivocado.
- El `fstream` de `DiskManager` hace `seek` y `read` en dos llamadas: dos hilos
  intercalados leen la pagina del otro.
- `keys_`, `live_`, `free_head_`, `main_pages_` y `first_keys_` se mutan sin
  proteccion.
- `stats_` se incrementa sin atomicidad.

Consecuencia para 2.1.4: **un `shared_mutex` con lectores en paralelo no
sirve** tal como esta el codigo. Hace falta un mutex por archivo que serialice
todo, o sacar `scratch_` de la clase. Y si los bindings no liberan el GIL, los
hilos de Python nunca corren en paralelo y la demostracion de race conditions
no demuestra nada; si lo liberan, aparecen los cuatro problemas de arriba.

### 5. Los bindings estan vacios y la premisa sobre excepciones es falsa

`bindings/module.cpp` expone solo `version()`. Y `error.hpp` dice que pybind11
traduce las excepciones solo: a medias. Como todas heredan de
`std::runtime_error`, pybind11 las convierte **todas** a `RuntimeError` y se
pierde la subclase; el parser no podra distinguir `DuplicateKey` (error del
usuario) de `IoError` (disco roto) salvo por el texto del mensaje. Hay que
registrarlas explicitamente.

Ademas el caster automatico de `std::variant` prueba las alternativas en orden
de declaracion: un `int` de Python para una columna DOUBLE se convertiria a
`int32_t` y `Schema::validate` lanzaria. O sea `INSERT INTO t VALUES (1)`
fallaria y `(1.0)` funcionaria. Hace falta un conversor guiado por el `Schema`.
`Date` no tiene caster ninguno.

Detalle de CI: `QUIPUDB_BUILD_PYTHON` esta en `OFF` y el workflow no lo activa,
asi que **los bindings nunca se compilan en CI**.

### 6. Falta UPDATE — duele en 2.1.4

No existe en el contrato ni en `plan.py`. Para 2.1.3 el enunciado no lo pide,
pero la demostracion canonica de una race condition es un read-modify-write
(dos hilos sobre el mismo saldo). Sin `update` eso son dos operaciones, con una
ventana donde la fila no existe, el RID cambia de sitio y en el secuencial
puede disparar una reorganizacion en medio de la transaccion.

Es barato ahora: con registros de longitud fija, un `update` que no toque la
clave es reescribir el mismo slot. Diez lineas por implementacion.

### 7. `OpStats` es por objeto; `Step.stats` es por paso

Los campos cuadran exactamente con `plan.py` (mismos cuatro nombres) y hay una
prueba que lo fija. El problema es el ciclo de vida: la unica forma de obtener
las estadisticas de **una** operacion es `reset_stats(); op(); stats();`, y eso
se rompe con los hilos de 2.1.4, donde el reset de una consulta borra los
contadores de otra. O las operaciones devuelven su `OpStats`, o el mutex por
archivo cubre las tres llamadas.

Ademas `external_sort` y `external_hash` ya estan en `plan.py` pero
`core/external/` esta vacio, y no hay una interfaz que obligue a esas clases a
exponer `stats()`.

### 8. `Index` y `Key` no expresan lo espacial ni el k-NN

Para 2.1.2 la interfaz alcanza y no la tocaria. Para las Partes 2 y 4 el
bloqueo no esta en `Index` sino en `Key`: es un escalar, y `range_search(lo,
hi)` no puede expresar una caja 2-D, un radio ni un k-NN. Tampoco hay
`DataType::Point` ni `Array`.

Cuando lleguen, conviene una interfaz aparte (`SpatialIndex`, `VectorIndex`)
que reuse `RID`, `OpStats` y `TableFile::read`, en vez de forzar `Index`.
Cuidado al ampliar `DataType`: `type_of` hace `static_cast<DataType>(v.index())`,
asi que el orden del enum y el del variant tienen que coincidir; agregar al
final es seguro, insertar en medio corrompe los archivos existentes en silencio.

### Apunte para el 2.1.6

No hay buffer pool: `OpStats::pages_read` cuenta lecturas fisicas sin cache,
mientras PostgreSQL responde desde `shared_buffers`. Comparar paginas leidas
contra PostgreSQL sin aclararlo es comparar cosas distintas. O se agrega una
cache minima, o se dice explicitamente en el informe y se comparan solo tiempos.

---

## Lo que si aguanto

Vale decirlo, porque tambien es resultado de la auditoria:

- La free list del heap file, incluidas las transiciones llena/con-espacio, la
  reutilizacion de huecos y la estabilidad de los RIDs: sin un fallo en el
  fuzzing.
- El orden del directorio del secuencial, la deteccion de duplicados entre area
  principal y overflow, y la equivalencia entre busqueda binaria y lineal.
- El formato de pagina y de archivo, con sus validaciones de magic, version,
  tamano de pagina y truncado.
- La separacion `TableFile` / `Index`, que es la decision de diseno que mas se
  va a usar y no hay que tocar.
- Paginas de 65 536 bytes funcionan, aunque no habia prueba (el margen con los
  `uint16` de la cabecera es de 7 bytes).

## Estado de las correcciones

| Hallazgo | Estado |
|---|---|
| C1 grupo enteramente borrado | corregido en #55 |
| C2 byte nulo en VARCHAR | corregido en #55 |
| C3 NaN como clave | corregido en #55 |
| I3 desbordamiento de 32 bits | corregido en #55 |
| I5 archivos de indice que colisionan | corregido en #55 |
| I6 `physical_slots()` sin signo | corregido en #55 |
| `create_index` sin validar el storage | corregido en #55 |
| I1 recuperacion tras terminacion abrupta | pendiente |
| I2 hash del esquema en el area meta | pendiente |
| I4 catalogo que diverge si `save()` falla | pendiente |
| Cursor, UPDATE y apertura desde el catalogo | #56 |
| Seguridad de hilos | pendiente, para 2.1.4 |
| Bindings | #23 |

Encontrado ademas al verificar las correcciones: `Catalog::create_index`
devolvia una referencia dentro del vector de indices de la tabla, que la
siguiente llamada reubica. Ahora devuelve por valor.

## Plan propuesto

**Antes de empezar 2.1.2:**

1. Corregir C1, C2, C3, I3, I5, I6 y la validacion de `create_index`. Son
   defectos acotados, con reproduccion, y cada uno lleva su prueba.
2. Agregar al contrato lo que cambia firmas: cursor de registros, `update`, y
   una fabrica de tablas desde el catalogo con `page_size`. Avisar en los
   issues #24 (parser) y #38 (benchmarks) antes de tocarlo.

**Durante 2.1.2, sin bloquear:**

3. Reconstruir contadores al abrir en vez de lanzar (I1) y guardar un hash del
   esquema en el area meta (I2).
4. Mutex por archivo, para que 2.1.4 tenga sobre que apoyarse.

**Cuando toque:**

5. Bindings con excepciones registradas y conversor guiado por esquema (#23), y
   activar `QUIPUDB_BUILD_PYTHON` en el CI.
6. Interfaces espaciales y vectoriales aparte, ya en la Parte 2.
