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
  std::vector<Record>  scan();
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
- Como se crea o abre un `TableFile` desde un path: lo define el catalogo (#7).

## Organizaciones implementadas

| Estructura | Archivo | Estado |
|---|---|---|
| Heap File | `core/include/quipudb/storage/heap_file.hpp` | insercion y escaneo (#8); free list en #9 |

El heap file guarda slots de tamano fijo (`[1 byte de estado][registro]`) en el
body de cada pagina, asi que el slot `i` esta siempre en `i * slot_size` y un
RID se resuelve sin recorrer nada. Medido sobre 1k / 10k / 100k registros: la
insercion es constante por registro y la busqueda crece lineal, que es
exactamente el perfil que el 2.1.6 compara contra el archivo secuencial.
