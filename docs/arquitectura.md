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
| Operaciones de tabla e indice (`insert`, `remove`, `search`, `range_search`, `scan`) — [`core/include/quipudb/catalog/table.hpp`](../core/include/quipudb/catalog/table.hpp) | core (2.1.1, 2.1.2) | parser (2.1.3), planner, benchmarks (2.1.6) |
| Estructura del plan de ejecucion | planner + core | frontend (2.1.5) |
| Manejo de bloqueos alrededor de las operaciones de tabla | transactions (2.1.4) | core |
| Contrato HTTP de la API | api | frontend |

## Por que el plan de ejecucion es un contrato y no un detalle

La seccion 2.1.5 exige un Panel de Plan de Ejecucion que muestre que indices se
usaron y en que orden se ejecutaron las operaciones. Eso obliga a que cada
operacion del core reporte que estructura empleo. Se disena desde el inicio,
no al final.

## Contrato del core

Fijado en el issue #3. Vive en tres cabeceras y una prueba:

| Archivo | Que define |
|---|---|
| `core/include/quipudb/error.hpp` | La jerarquia de excepciones del core |
| `core/include/quipudb/catalog/types.hpp` | `PageId`, `RID`, `DataType`, `Value`, `Key`, `Record`, `Column`, `Schema`, `compare` |
| `core/include/quipudb/catalog/table.hpp` | Las interfaces `TableFile` e `Index`, `OpStats` y los nombres canonicos `kind::` |
| `core/tests/contract_test.cpp` | Dos implementaciones en memoria que fijan la semantica esperada |

### Dos interfaces, no una

```
TableFile                              Index
  heap file                              B+ no agrupado
  archivo secuencial paginado            extendible hashing
  B+ agrupado
  guarda registros completos             guarda pares (clave, RID)
  search -> vector<Record>               search -> vector<RID>
```

Un indice agrupado **es** la tabla: los registros viven en sus hojas. Un
indice no agrupado apunta a una tabla que vive en otro archivo. Si ambos
compartieran la misma firma, `search` devolveria unas veces registros y otras
punteros, y quien consume el contrato tendria que preguntar cual de los dos
recibio. Por eso el B+ agrupado implementa `TableFile` y el no agrupado
implementa `Index`; comparten el codigo de nodos por dentro, no la interfaz
por fuera.

### Firmas

```cpp
class TableFile {                                  // una tabla fisica
  const Schema&        schema() const;
  std::string_view     kind() const;               // "heap" | "sequential" | "bplus_clustered"
  RID                  insert(const Record&);      // DuplicateKey si la PK existe
  std::size_t          remove(const Key&);         // 0 o 1
  std::vector<Record>  search(const Key&);         // vacio si no hay
  std::vector<Record>  range_search(const Key& lo, const Key& hi);  // [lo, hi]
  std::size_t          update(const Key&, const Record&);  // misma clave, mismo RID
  std::vector<Record>  scan();                     // materializa: ver cursor()
  std::unique_ptr<RecordCursor> cursor();          // recorrido incremental
  std::optional<Record> read(RID);                 // para resolver RIDs de un Index
  std::size_t          size() const;
  const OpStats&       stats() const;  void reset_stats();
};

class Index {                                      // indice secundario
  std::string_view     kind() const;               // "bplus_unclustered" | "extendible_hash"
  DataType             key_type() const;
  bool                 supports_range() const;     // false en hash
  void                 insert(const Key&, RID);    // admite claves repetidas
  std::size_t          remove(const Key&);         // todas las entradas
  bool                 remove(const Key&, RID);    // solo ese par
  std::vector<RID>     search(const Key&);
  std::vector<RID>     range_search(const Key& lo, const Key& hi);  // Unsupported en hash
  std::vector<std::pair<Key, RID>> scan();         // ordenado en B+
  std::size_t          size() const;
  const OpStats&       stats() const;  void reset_stats();
};
```

### Decisiones

| Pregunta del issue | Decision | Por que |
|---|---|---|
| Busqueda devuelve registro o puntero | `TableFile` devuelve registros completos; `Index` devuelve `RID` | Es la diferencia real entre agrupado y no agrupado; un indice secundario que devolviera registros tendria que conocer el archivo de la tabla |
| Errores: excepcion o codigo de retorno | Excepciones, todas heredan de `quipudb::Error` | pybind11 las traduce a Python solas; un codigo de retorno se ignora. "No encontrado" **no** es un error: vector vacio, `nullopt` o `0` |
| `remove` vs `delete` | `remove` | `delete` es palabra reservada de C++ |
| Rango | Inclusivo en ambos extremos | Es lo que `BETWEEN` de SQL espera y evita el `hi + 1` con strings y doubles |
| Clave primaria | Unica; el duplicado lanza `DuplicateKey` | Sin esto el archivo secuencial y el B+ agrupado no tienen orden total |
| Claves repetidas en indice secundario | Permitidas; `remove(key, rid)` quita una sola | Un indice sobre `edad` tiene muchas filas por valor |
| Registros | Longitud fija; `VARCHAR(n)` se rellena | Slots calculables sin directorio de offsets; simplifica heap, secuencial y hojas del B+ |
| Tiempo en las estadisticas | No; solo paginas y registros | El tiempo lo mide el planner, que sabe donde empieza y termina la consulta completa |
| Nombres de estructura | Constantes en `kind::` | Planner, frontend y benchmarks usan exactamente las mismas cadenas |

### Recorrido incremental, actualizacion y apertura (#56)

`scan()` devuelve el vector completo y eso no escala: 100 000 registros ocupan
4,3 MB en disco y 17,7 MB de memoria al materializarlos. `cursor()` recorre lo
mismo con memoria acotada a una pagina (heap file) o a un grupo (secuencial):
0 MB medibles de RSS adicional. Es lo que permite que el external sorting del
#20 sea externo de verdad, y lo que usa `SequentialFile::reorganize`.

`update(clave, registro)` reescribe el registro en su slot sin cambiar el RID,
asi que los indices secundarios que lo apuntan siguen valiendo. La clave nueva
tiene que ser la misma: cambiarla es `remove` mas `insert`, que mueve el
registro de sitio.

`Database` (`catalog/database.hpp`) junta el catalogo con los archivos
abiertos: `db.table("alumnos")` devuelve el `TableFile` que corresponda a la
organizacion registrada, y siempre el mismo objeto. Eso ultimo importa: dos
handles sobre el mismo archivo tienen cada uno su estado en memoria, se pisan
al escribir y dejan contadores que no cuadran. El catalogo guarda ademas el
`page_size` de cada tabla, sin el cual una tabla creada con otro tamano no se
podia reabrir.

### Lo que esto NO decide todavia

- La forma del plan de ejecucion: resuelta en
  [ADR 0002](adr/0002-plan-de-ejecucion.md) e implementada en
  `engine/planner/plan.py`. `OpStats` y `kind()` son sus insumos.
- El formato de una pagina esta en `core/include/quipudb/storage/page.hpp`
  (#6): cabecera de 8 bytes (`next`, `record_count`, `free_space`) y body
  libre para cada estructura; el archivo lo administra `DiskManager`, con la
  pagina 0 como cabecera y area meta.
- El formato de un registro esta en `core/include/quipudb/catalog/record_codec.hpp`
  (#7): columnas concatenadas en el orden del esquema, cada una con su tamano
  fijo (INT 4, DOUBLE 8, VARCHAR n rellenado con `\0`, BOOL 1, DATE 4), sin
  cabecera ni separadores. El catalogo (`catalog.hpp`) guarda los esquemas,
  la organizacion de cada tabla y sus indices en un archivo de texto que se
  reescribe atomicamente en cada cambio.
- Como se abre un `TableFile` desde el catalogo: `Database` (#56).

## Organizaciones implementadas

| Estructura | Archivo | Estado |
|---|---|---|
| Heap File | `core/include/quipudb/storage/heap_file.hpp` | completo: insercion y escaneo (#8), free list y reutilizacion (#9) |
| Archivo Secuencial Paginado | `core/include/quipudb/storage/sequential_file.hpp` | completo: #10, #11, #12 y #13 |

El heap file guarda slots de tamano fijo (`[1 byte de estado][registro]`) en el
body de cada pagina, asi que el slot `i` esta siempre en `i * slot_size` y un
RID se resuelve sin recorrer nada. Medido sobre 1k / 10k / 100k registros: la
insercion es constante por registro y la busqueda crece lineal, que es
exactamente el perfil que el 2.1.6 compara contra el archivo secuencial.

**Reutilizacion de espacio: FREE_LIST.** Las paginas con al menos un slot
libre se encadenan por el campo `next` de la cabecera de pagina; la cabeza
vive en el area meta y persiste entre sesiones. Insertar toma la cabeza y,
si la pagina se llena, la desenlaza; eliminar libera el slot y, si la pagina
estaba llena, la mete a la lista. Las dos operaciones son O(1).

Se descarto **MOVE_THE_LAST** (traer el ultimo registro al hueco para dejar el
archivo compacto) porque mueve un registro de sitio y en este motor un RID es
una direccion estable: los indices no agrupados (#16) guardan RIDs, y cada
eliminacion obligaria a corregir el indice del registro movido. FREE_LIST
paga con paginas que quedan con huecos hasta reutilizarse; MOVE_THE_LAST
pagaria con correccion de indices en cada borrado.

### Archivo secuencial: area principal y overflow

Las paginas principales forman una lista enlazada en orden de clave (campo
`next`), con los registros ordenados dentro de cada una; cada pagina principal
tiene ademas una pagina de overflow, cuya cabeza vive en los primeros 4 bytes
del body.

Un registro va al area principal si su pagina tiene sitio, o si su clave es
mayor que todas y toca abrir pagina al final (por eso una carga ordenada no
genera overflow). Si la pagina esta llena y la clave cae en medio, va al
overflow. Cuando la pagina de overflow tambien se llena, el grupo se parte:
sus registros se juntan, se ordenan y se reparten en paginas principales a
media carga, empalmadas en la cadena.

Ese limite de una pagina de overflow por grupo no es decorativo. Sin el, el
area principal deja de crecer en cuanto se llena la primera pagina y todo se
apila en overflow: medido con 100 000 inserciones aleatorias, 3 paginas
principales contra 714 de overflow y 93 s de carga, frente a 998 contra 40 y
0,5 s con la particion activada.

| 100 000 registros | insercion | paginas principales | overflow |
|---|---|---|---|
| en orden ascendente | 0,0029 ms/registro | 715 | 0 |
| en orden aleatorio | 0,0051 ms/registro | 998 | 40 |

**Eliminacion lazy.** `remove` marca el slot como borrado y no mueve nada: el
archivo no cambia de tamano y los registros que le siguen conservan su
posicion. El espacio desperdiciado es el que ocupan esos marcados, y
`wasted_ratio()` es marcados / (vivos + marcados). Los slots libres al final
de una pagina no cuentan: son sitio util para las proximas inserciones, y
contarlos haria que un archivo recien partido (paginas a media carga)
pareciera desperdiciar la mitad. Sobre esa razon dispara la reorganizacion del
#12.

El desperdicio solo baja solo al partir un grupo, donde los marcados se quedan
fuera de las paginas nuevas. Al abrir el archivo se recuentan vivos y marcados
recorriendo las cadenas y se comparan con lo que declara el area meta.

**Reorganizacion.** Cuando la razon de desperdicio supera el umbral (30% por
defecto, configurable por archivo), `remove` dispara `reorganize()`: junta los
registros vivos en orden, los reparte en paginas consecutivas desde la primera
sin huecos ni overflow, y devuelve al sistema las paginas que sobran
(`DiskManager::truncate`). Al terminar, el desperdicio es cero y el archivo
vuelve a ser puramente secuencial.

Las paginas quedan llenas al 80% y no al tope: empacar del todo mandaria la
siguiente insercion de cada rango derecho al overflow. La reorganizacion
materializa los registros vivos en memoria, lo que es aceptable para los
100 000 del 2.1.6 y evita pisar paginas todavia no leidas al reescribir sobre
el mismo archivo; una version que no cargue todo tendria que apoyarse en el
external sorting del #20.

Tiempo de reorganizacion, borrando el 30% de los registros (una de las
metricas que pide comparar el 2.1.6):

| registros vivos | tiempo | paginas antes | paginas despues |
|---|---|---|---|
| 700 | 0,2 ms | 8 | 7 |
| 7 000 | 2,1 ms | 72 | 63 |
| 70 000 | 20,8 ms | 715 | 625 |

**Busqueda.** Dos niveles de busqueda binaria: primero sobre `first_keys_` (la
primera clave de cada pagina principal, que vive en memoria) para dar con el
grupo, despues dentro de la pagina para dar con la posicion. Solo entonces se
recorre el overflow de ese grupo, que es como mucho una pagina. El resultado
es que una busqueda puntual cuesta lo mismo con 1 000 registros que con
100 000.

Busqueda puntual, promedio de 100 consultas con claves al azar sobre datos
insertados en orden aleatorio:

| registros | secuencial binaria | secuencial lineal | heap (lineal) |
|---|---|---|---|
| 1 000 | 0,0034 ms · 1,3 paginas | 0,21 ms · 8 paginas | 0,014 ms · 3,7 paginas |
| 10 000 | 0,0022 ms · 1,1 paginas | 2,62 ms · 111 paginas | 0,160 ms · 37 paginas |
| 100 000 | 0,0025 ms · 1,0 paginas | 24,1 ms · 1 019 paginas | 1,995 ms · 345 paginas |

Esa ultima fila es el resumen de la comparacion que pide el 2.1.6: el heap
lee media tabla en promedio, el secuencial lee una pagina.
