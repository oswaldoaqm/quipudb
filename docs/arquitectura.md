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

### Abrir indices desde el catalogo

El #56 resolvio para las tablas el problema de que cada consumidor escribiera su
propio `if (storage == "heap") new HeapFile(...)`. Los indices quedaron a medias:
el catalogo (#7) los registraba -- nombre, columna, tipo y archivo -- pero nadie
sabia convertir ese registro en un objeto usable, asi que planner, parser,
transacciones, benchmarks y bindings habrian tenido cada uno su copia del switch.

`Database::index(tabla, nombre)` cierra ese hueco. El switch de `IndexInfo::kind`
vive en `abrir_indice` y en ningun otro sitio: agregar un tipo de indice -- el
R-Tree de la Parte 2, por ejemplo -- es tocar esa funcion y nada mas.

**El indice se monta sobre el MISMO handle de tabla que devuelve `table()`.** No
es una comodidad: un indice guarda RID, y un RID solo significa algo respecto de
un estado concreto del archivo. Si el indice tuviera su propio handle, insertar
por `table()` dejaria al indice apuntando a un estado que su handle no conoce, y
`read(rid)` devolveria el registro equivocado o nada. Lo comprueba
`ElIndiceSeMontaSobreElMismoHandleQueDevuelveTable`.

De ahi salen tres reglas de ciclo de vida:

| operacion | que hace |
|---|---|
| `close(tabla)` | cierra primero los indices, despues la tabla |
| `drop_table` | borra tambien los archivos de sus indices: sus RID ya no apuntan a nada |
| `flush()` | vacia primero los indices, mientras su tabla sigue viva |
| destructor | suelta `indices_` explicitamente |

El destructor no confia en el orden de declaracion de los miembros. Ese orden ya
da el resultado correcto, pero es una propiedad fragil que un reordenamiento
inocente rompe en silencio y que ninguna prueba sin ASan detectaria.

`create_index` ademas **construye** el indice recorriendo la tabla, porque una
columna se puede indexar cuando la tabla ya tiene datos. Eso usa el cursor (#56)
y no `scan()`: en 100 000 registros la diferencia es materializarlos todos o
ninguno.

Lo que `Database` NO hace es mantener los indices al dia cuando la tabla cambia.
Quien inserta, borra o actualiza tiene que avisarle al indice; coordinar eso es
del planner (2.1.3). Aqui la responsabilidad termina en que haya un solo objeto
por archivo y en saber abrirlo.

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
| B+ Tree (maquinaria comun) | `core/include/quipudb/index/bplus_tree.hpp` | completo: nodos, split e insercion (#14), claves repetidas (#16), borrado con redistribucion y fusion (#17) |
| B+ agrupado | `core/include/quipudb/index/bplus_clustered_table.hpp` | tercera organizacion de tabla (#15) |
| B+ no agrupado | `core/include/quipudb/index/bplus_unclustered_index.hpp` | indice secundario sobre un heap file (#16) |

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

### B+ Tree

`BPlusTree` es la maquinaria que comparten el B+ agrupado (#15) y el no
agrupado (#16). No sabe que hay dentro de una entrada: guarda `payload_size`
bytes opacos por clave, y cada indice decide si eso es un registro completo o
un RID.

Cada nodo es una pagina. Las hojas guardan `[clave][payload]` y estan
encadenadas por el campo `next` de la cabecera, asi que recorrerlas en orden
no cuesta bajar por el arbol. Los internos guardan `[hijo izquierdo]` seguido
de pares `[clave][hijo]`: n claves y n+1 hijos.

La diferencia entre los dos splits es la que se suele equivocar: al partir una
**hoja** la clave separadora se **copia** hacia arriba y sigue viviendo abajo,
porque las hojas guardan todas las claves; al partir un **nodo interno** la
clave del medio se **mueve** y deja de estar abajo, porque los internos solo
guian la busqueda.

El orden (claves maximas por nodo) se calcula por omision como el maximo que
entra en la pagina, y se puede fijar a mano para forzar arboles altos con
pocas claves, que es lo que hacen las pruebas.

| claves | altura | orden | insercion | busqueda | paginas leidas | espacio |
|---|---|---|---|---|---|---|
| 1 000 | 2 | 510 | 0,0235 ms | 0,0018 ms | 2 | 20 KB |
| 10 000 | 2 | 510 | 0,0273 ms | 0,0021 ms | 2 | 136 KB |
| 100 000 | 2 | 510 | 0,0286 ms | 0,0037 ms | 2 | 1 036 KB |

Con paginas de 4 KB caben 510 claves por nodo, asi que 100 000 claves entran
en un arbol de altura 2: dos paginas leidas por busqueda, igual que con 1 000.
`check_invariants()` comprueba balanceo, orden, rangos de cada subarbol y que
la cadena de hojas recorra exactamente las mismas claves; las pruebas la
llaman despues de cada insercion en los arboles chicos.

### B+ agrupado: el indice ES la tabla

Agrupado quiere decir que los registros completos viven en las hojas del
arbol, ordenados por clave primaria. No hay archivo de datos aparte, asi que
`BPlusClusteredTable` implementa `TableFile` y es una **tercera organizacion
de tabla**, no un indice que se cuelga de otra:

| Organizacion | Como guarda |
|---|---|
| heap file | registros en orden de llegada |
| secuencial paginado | registros ordenados, con area de overflow |
| B+ agrupado | registros ordenados en las hojas de un arbol |

El payload de cada entrada del arbol es el registro serializado con el mismo
codec que las demas (#7). Como `BPlusTree` no sabe que guarda, el indice no
agrupado (#16) podra usar el mismo arbol con RIDs como payload.

Las tres organizaciones sobre los mismos datos, insertados en orden aleatorio
(promedio de 200 busquedas; el rango trae 1 000 registros):

**100 000 registros**

| | insercion | busqueda | paginas | rango de 1 000 | scan | espacio |
|---|---|---|---|---|---|---|
| heap file | 0,0036 ms | 1,289 ms | 381 | 3,44 ms | 13,4 ms | 2 864 KB |
| secuencial | 0,0062 ms | 0,0016 ms | 1,0 | 0,14 ms | 9,6 ms | 4 156 KB |
| B+ agrupado | 0,0173 ms | 0,0038 ms | 3,0 | 0,18 ms | 12,5 ms | 4 656 KB |

Lo que dice la tabla: el heap gana en insercion y pierde por dos ordenes de
magnitud en busqueda; el secuencial y el B+ agrupado buscan practicamente
igual de rapido, pero el B+ paga el triple en insercion a cambio de no tener
overflow que revisar ni reorganizaciones que disparar. El espacio del B+
incluye los nodos internos y las hojas a media carga tras los splits.

### B+ no agrupado: el indice apunta a la tabla

Las hojas guardan pares `(clave, RID)` y el registro se lee despues del
archivo de datos con `TableFile::read(rid)`. Por eso implementa `Index` y no
`TableFile`: se cuelga de una tabla en vez de serla.

Solo se puede montar sobre un **heap file**. Un RID guardado en el indice
tiene que seguir apuntando al mismo registro manana, y eso solo lo garantiza
el heap file: en el secuencial y en el B+ agrupado los registros se corren de
sitio al insertar. Lo comprueban tanto `Catalog::create_index` como la clase.

**Claves repetidas.** Es el caso normal: se indexa una columna que no es la
clave primaria, asi que muchos registros comparten valor. `BPlusTree` admite
repetidas desde este issue, y la diferencia esta en como se baja por el arbol:

- al **insertar** se va a la derecha en el empate con un separador, para que
  la entrada nueva quede despues de las iguales y la corrida conserve el orden
  de insercion;
- al **buscar** se va a la izquierda, a la primera hoja que podria contenerla,
  y desde ahi se sigue la cadena de hojas mientras la clave repita.

Y una regla que no es obvia: al partir un nodo, la rama nueva se coloca segun
**cual hijo se partio**, no comparando su clave separadora. Con separadores
repetidos, buscar por clave la insertaba antes de sus hermanas iguales y
dejaba los hijos desordenados; el arbol seguia pareciendo correcto en un
`scan` y fallaba la comprobacion de rangos por subarbol.

El costo de resolver los punteros es la desventaja del indice no agrupado
frente al agrupado, y es lo que compara el 2.1.6: encontrar los RID cuesta la
altura del arbol mas la cadena de hojas, pero traer los registros cuesta una
lectura de pagina por registro, porque estan repartidos por todo el heap.

### Borrado: redistribucion, fusion y free list

Borrar es el espejo de insertar. Si al quitar una entrada el nodo baja del
minimo, primero se intenta **prestar** una clave de un hermano al que le
sobre; si a ninguno le sobra, los dos hermanos se **fusionan** y el separador
que los dividia desaparece del padre, que puede quedar en falta a su vez, y
asi hacia arriba. Cuando la raiz se queda sin claves, su unico hijo pasa a ser
la raiz: es la unica forma en que un B+ **baja de altura**.

Los minimos no son un numero elegido a mano, salen de lo que deja un split
para que un nodo recien partido no nazca ya en falta:

| | minimo | fusion de dos nodos en falta |
|---|---|---|
| hoja | `ceil(orden/2)` | `2*min - 1 <= orden` |
| interno | `floor(orden/2)` | `2*min <= orden` |

Las dos desigualdades son lo que garantiza que una fusion siempre entre en una
pagina, y `merge_children` las comprueba en tiempo de ejecucion en vez de
confiar en el papel.

La rotacion en un nodo interno vuelve a separar los dos casos del split: el
separador del padre **baja** al nodo que estaba en falta y la clave del
hermano **sube** a ocupar su lugar. En una hoja no hay tal cosa: el separador
nuevo es una **copia** de la primera clave de la derecha.

La fusion siempre tira hacia la izquierda, asi la pagina que desaparece nunca
es la primera hoja del archivo y `first_leaf` no cambia nunca.

**Free list.** Las paginas que quedan libres al fusionar se encadenan por el
campo `next`, con la cabeza en el area meta, igual que el heap file (#9). Sin
eso, un ciclo de borrar e insertar haria crecer el archivo indefinidamente:
la prueba `ElArchivoNoCreceAlBorrarYVolverAInsertar` vacia y rellena un arbol
de 3 000 claves tres veces y exige que el numero de paginas no suba.

`check_invariants()` gano dos comprobaciones con este issue: la **ocupacion
minima** de todo nodo que no sea la raiz, y que cada pagina del archivo este
**o en el arbol o en la free list, nunca en las dos ni en ninguna**. La
segunda es la que atrapa una pagina liberada dos veces o una que se entrega
otra vez estando todavia colgada del arbol.

**Claves repetidas.** Con repetidas, una corrida de iguales puede abarcar
varias hojas y el descenso puede aterrizar en una hoja que no la contiene (el
separador es igual a la clave y las copias estan del otro lado). Por eso el
borrado es recursivo y no un recorrido de la cadena: baja por el primer hijo
cuyo rango puede contenerla y sigue al hermano de la derecha solo si el
separador que los divide es exactamente la clave buscada. Volver por la
recursion es ademas lo que deja al padre a mano, que es quien tiene a los
hermanos para rebalancear.

### Extendible Hashing: el directorio absorbe el crecimiento

`ExtendibleHash` (#18) es la tercera estructura de indexacion del 2.1.2 y la
primera que no mantiene un orden. Sigue la misma division que el B+: es la
**maquinaria**, guarda `payload_size` bytes opacos por clave y no sabe que
son; el adaptador que implementa `Index` -- con `search`, `remove` y
`supports_range() == false` -- es del #19, igual que `BPlusUnclusteredIndex`
(#16) se apoya en el arbol del #14.

```
directorio (2^global entradas)      bucket (cadena de paginas)
[PageId][PageId][PageId]...         [4 bytes profundidad local]
next -> siguiente pagina            [clave][payload]
record_count = entradas usadas      ...
                                    next -> pagina de overflow
```

Varias entradas del directorio pueden apuntar al **mismo** bucket: uno de
profundidad local L es compartido por las 2^(global-L) entradas que coinciden
en sus L bits bajos. El directorio se indexa con los bits **bajos** del hash, y
eso no es una preferencia: es lo que hace que duplicarlo conserve el reparto,
porque la entrada j y la j + 2^global quedan con el mismo bucket sin mover
nada.

| bucket lleno con | que pasa |
|---|---|
| local < global | se parte en dos (L+1); el directorio no cambia de tamano, solo repunta la mitad de las entradas que compartian el bucket |
| local == global | no hay bit que distinga las mitades: primero se **duplica el directorio** y despues se parte |
| claves inseparables | ningun bit las separa nunca: el bucket crece por **overflow** encadenado |

Duplicar el directorio no crea buckets. Ese es el punto de la estructura frente
al hashing estatico: el costo de crecer se paga sobre el directorio, que son
`PageId` de 4 bytes, y no sobre los datos.

**Cuando NO hay que partir.** Un indice secundario admite claves repetidas, y
dos claves iguales tienen el mismo hash: ningun bit las separa, partir deja un
bucket igual de lleno y otro vacio, y duplicar el directorio para intentarlo lo
hace crecer sin repartir. Antes de partir se comprueba si las entradas difieren
en algun bit **por encima** de los que ya comparten; si no, se encadena una
pagina de overflow, como el area de overflow del archivo secuencial (#10).

La pregunta se hace sobre todos los bits altos y no solo sobre el que toca
partir ahora. Preguntar solo por ese bit fue el primer intento y esta mal: con
buckets de 4 entradas, una de cada ocho veces las cuatro coinciden en ese bit
por casualidad, y ahi lo correcto es partir igual -- el hermano nace vacio -- y
volver a intentarlo un bit mas arriba. La version estricta llenaba de overflow
una estructura con claves perfectamente separables. Lo cubre
`ClavesAlAzarNoGeneranOverflow`, que usa claves al azar a proposito: con
patrones regulares ese bit separa casi siempre por construccion y el defecto no
se ve.

**El hash: FNV-1a a secas.** La clave se serializa con el mismo codec que los
registros (#7) y sobre esos bytes va FNV-1a de 64 bits, sin finalizador de
mezcla. La objecion clasica a FNV es que termina en una multiplicacion y los
bits bajos de un producto dependen solo de los bits bajos de sus factores. Es
cierto, pero para claves de tamano **fijo** eso no las agrupa: multiplicar por
un impar es una biyeccion modulo 2^k, asi que el byte que varia sigue dando
bits bajos distintos. Se midio la carga maxima de un bucket con 2 000 claves
sobre 512 buckets:

| patron de clave | hash = la clave | FNV-1a | FNV-1a + splitmix64 |
|---|---|---|---|
| int 1..n | 5 | **5** | 10 |
| int i*4 | 16 | **8** | 10 |
| int i*512 | 2 000 | **5** | 11 |
| varchar(12) con prefijo comun | - | **9** | 10 |
| double i*1,5 | - | **9** | 12 |

El finalizador reparte como el azar (Poisson); FNV a secas reparte **mejor** que
el azar en los patrones que de verdad aparecen en una clave de tabla:
correlativos, con paso fijo, texto con prefijo comun. Empeoraba la carga maxima
en todos los casos medidos, asi que no esta. La primera version del codigo si lo
llevaba, con el argumento de que "sin el, insertar 1..n manda todo al mismo
bucket": era falso y la medicion lo desmintio.

La columna "hash = la clave" es la respuesta a por que hace falta un hash. Con
un paso que es potencia de dos, usar el valor tal cual manda las 2 000 claves al
mismo bucket, y el sintoma no es que falten buckets sino que **el directorio se
dispara**: el split sigue bajando hasta el bit donde las claves difieren, y para
llegar ahi duplica el directorio una vez por bit. Por eso
`ElHashRepartelasClavesConUnPasoQueEsPotenciaDeDos` acota
`directory_size() <= 4 * bucket_count()` ademas de contar buckets: contar
buckets solo no lo ve.

**Claves que `compare` considera iguales tienen que hashear igual.** Los bytes
crudos no lo garantizan: `0.0` y `-0.0` solo difieren en el bit de signo y
`compare` los declara iguales, asi que `hash_of` los normaliza antes de mezclar
(y hace lo mismo con NaN, que `Schema::validate` ya no deja entrar en un
registro pero que puede llegar como clave suelta). Sin eso, una busqueda por
`0.0` no encontraria al `-0.0` que esta en otro bucket.

**Recorrido.** `entries()` va por el directorio y visita cada bucket una sola
vez sin recordar cuales ya vio: un bucket de profundidad local L aparece en
2^(global-L) entradas y solo la de indice menor que 2^L tiene los bits altos en
cero. Esa es su entrada canonica; desde las demas se salta. Memoria acotada a
una pagina, a cambio de leer una por entrada de directorio en vez de una por
bucket.

**Versionado.** El area meta guarda `version = 1`, la profundidad global, la
cabeza del directorio y la de la free list. Reabrir con otra version es un
`IoError`; con otra capacidad de bucket, otro payload o otro tipo de clave, un
`SchemaError`. Si cambia el layout de las paginas, sube la version.

**Lo que este issue no hace.** Buscar, borrar y fusionar buckets hermanos son
del #19, junto con el adaptador `Index` y la documentacion de por que esta
estructura no sirve para busquedas por rango -- material directo del 2.1.6.

### Extendible Hashing: busqueda, borrado y merge (#19)

`ExtendibleHashIndex` implementa `Index` sobre la maquinaria del #18 y es lo que
cierra la vineta "Indice Hash Dinamico" del enunciado. Misma relacion que hay
entre `BPlusTree` (#14) y `BPlusUnclusteredIndex` (#16), y las mismas dos
restricciones: solo se monta sobre un heap file, porque un RID guardado tiene
que seguir valiendo manana, y admite claves repetidas.

**Buscar** cuesta un acceso al directorio -- que vive en memoria -- mas uno al
bucket, sin importar cuantas claves haya guardadas. Lo comprueba
`LaBusquedaLeeUnaSolaPaginaCuandoNoHayOverflow`, que exige `pages_read == 1`
exactamente. Es la ventaja frente al B+, que paga la altura del arbol.

**Borrar** quita la entrada y tapa su hueco con la ultima de esa misma pagina:
dentro de un bucket no hay orden que conservar, asi que compactar cuesta una
copia y no un corrimiento. Despues se intenta fusionar con el hermano.

#### Por que el hash no sirve para busquedas por rango

Es material directo del analisis comparativo (2.1.6), y la respuesta esta en el
hash mismo, no en la implementacion: **el trabajo de un hash es esparcir**. Que
claves parecidas caigan lejos es lo que hace que los buckets se llenen parejo, y
es exactamente lo que impide recorrer un intervalo. Las claves 100 y 101 caen en
buckets sin relacion, y entre ellas puede estar cualquier otra clave del
archivo. No existe "el bucket siguiente".

Responder `WHERE edad BETWEEN 20 AND 30` con este indice obliga a una de dos
cosas, y las dos son peores que no tenerlo:

| salida | costo |
|---|---|
| recorrer todos los buckets y filtrar | un scan completo, con peor localidad que el del heap porque los buckets estan repartidos por el archivo |
| generar cada clave del intervalo y buscarla | una busqueda por valor posible aunque no exista ninguno; solo aplicable a tipos discretos y acotados |

El B+ no tiene el problema porque sus hojas estan encadenadas **en orden de
clave**: se baja una vez a un extremo del intervalo y se sigue la cadena. Esa es
la diferencia estructural que el 2.1.6 tiene que medir.

Por eso `supports_range()` devuelve false y `range_search` lanza `Unsupported`
en vez de devolver un resultado caro en silencio. El planner consulta
`supports_range()` antes de elegir; si lo ignora, recibe la excepcion. Devolver
el resultado igual seria peor: una consulta que parece funcionar y que en
100 000 registros tarda lo que un scan.

#### La condicion de merge se midio, no se razono

Dos hermanos fusionan si estan en la misma profundidad local, ninguno tiene
overflow, y sus entradas **caben juntas en una pagina**. Esa ultima condicion
salio de medir, y la medicion desmintio dos intentos previos. El script esta en
`docs/medir-condicion-merge.py`.

Merges disparados en el regimen que el 2.1.6 manda medir -- "rendimiento con
inserciones/eliminaciones frecuentes", 20 000 operaciones mitad altas mitad
bajas:

| condicion | capacidad 4 | capacidad 32 | capacidad 408 (paginas de 4 KB) |
|---|---|---|---|
| uno queda vacio | 182 | 0 | **0** |
| suma <= capacidad/2 | 162 | 0 | **0** |
| suma <= capacidad | 1 177 | 104 | **104** |

Las dos primeras **no fusionan nunca con capacidades reales**. En un regimen
equilibrado los buckets no llegan a vaciarse, ni a bajar de la mitad a la vez
que su hermano: implementarlas es tener merge en el codigo y no tenerlo en la
practica. La primera es, ademas, la que el issue #19 pedia literalmente.

Y al vaciar la estructura entera, que es donde se ve cuanto espacio recupera
cada una (capacidad 4, 6 000 claves insertadas y borradas):

| condicion | buckets que quedan |
|---|---|
| uno queda vacio | 359 |
| suma <= capacidad/2 | 134 |
| suma <= capacidad | **1** |

**La oscilacion resulto ser un miedo infundado.** El argumento para exigir un
umbral mas estricto era que un bucket recien partido ya cumpliria la condicion
de fusion y se pondria a partir y fusionar en bucle. Medido: **cero
oscilaciones en los 35 escenarios**. La razon es que un split de hash no reparte
por la mitad como el B+, sino por un bit del hash, y para volver a partir hay
que llenar un bucket ENTERO, no llegar a su mitad. La histeresis ya esta en la
estructura, asi que aqui no hay ningun umbral elegido a mano -- al reves que en
el B+, donde los minimos si hay que derivarlos del split.

El overflow se excluye solo: una cadena de overflow tiene mas entradas que la
capacidad, asi que jamas cumple "caben juntas en una pagina". Un bucket de
claves repetidas no se fusiona hasta que se borran esas claves.

#### Reducir el directorio sin tocar el formato

Se quita un bit cuando ningun bucket queda en la profundidad global. La primera
idea fue llevar un contador de buckets al tope en el area meta, lo que habria
obligado a subir la version del formato a 2 y a que los archivos del #18 dejaran
de abrirse. La medicion lo descarto: **reducir el directorio ocurre entre 0 y 4
veces en corridas de 20 000 operaciones**. Un recorrido O(directorio) que pasa
cuatro veces en 20 000 operaciones es ruido, no un costo. Asi que se comprueba
recorriendo, el formato **sigue en la version 1** y los archivos del #18 se
siguen abriendo.

`ElArchivoNoCreceAlBorrarYVolverAInsertar` cierra el ciclo: vacia y rellena un
indice de 600 claves tres veces y exige que el archivo no crezca, porque las
paginas que libera un merge van a la free list y se reusan.

## External algorithms

### External Sorting: k-way merge para ORDER BY (#20)

Ordena mas registros de los que caben en memoria, que es la primera mitad de la
viñeta de External Algorithms del enunciado. El ORDER BY de la 2.1.3 (#28) se
apoya en esto, y el plan de ejecucion (ADR 0002) ya tenia reservado el paso
`op: "sort"` con `structure: "external_sort"`.

**Fase 1.** Se lee de a B paginas -- todo lo que la memoria permite --, se
ordena ese trozo en memoria y se escribe como un run ya ordenado. La entrada
queda partida en N/B runs.

**Fase 2.** Se abren k = B-1 runs, se reserva la pagina que sobra para la
salida, y se saca el menor de los k frentes con un heap. Cada pasada reduce los
runs por un factor k.

Por que k = B-1 y no B: una pagina tiene que quedar libre para acumular la
salida. Escribir de a un registro convertiria el merge en una escritura de
pagina por registro.

| | costo en paginas |
|---|---|
| fase 1 | 2N |
| cada pasada | 2N |
| pasadas | ceil(log_{B-1}(N/B)) |
| **total** | **2N (1 + ceil(log_{B-1}(N/B)))** |

De ahi que **B se cuente en paginas y no en megabytes**: la formula solo tiene
sentido en paginas, y es la que el informe tiene que explicar. `predicted_pages`
la calcula y `stats()` da lo medido, para que el 2.1.6 contraste una contra otra
en vez de suponer que coinciden. `LasPaginasMedidasSeParecenALaFormula` ya lo
comprueba con holgura de 2x.

**Ordena un flujo, no una tabla.** Recibe un `RecordSource` y devuelve otro. El
GROUP BY (#21) y el JOIN (#22) van a necesitar ordenar cosas que no son tablas
-- la salida de un filtro, el resultado de un join -- y atarlo a `TableFile`
obligaria a rehacerlo dos veces.

`RecordCursor` (#56) no servia como esa entrada: exige `rid()`, y un registro
que sale de un sort no tiene direccion fisica que ofrecer. `RecordSource` es la
parte de `RecordCursor` que no depende de vivir en una tabla, y `source_of()`
adapta cualquier `TableFile`.

**Devuelve un flujo, no un vector.** El ADR 0002 dibuja `sort` como un paso con
un hijo que le entrega su salida: un pipeline. Devolver un `vector<Record>` con
100 000 registros dentro seria haber ordenado en disco para materializarlo todo
al final, que es justo lo que el issue quiere evitar. La memoria queda acotada a
B paginas de principio a fin.

**Si todo cabe en memoria no se toca el disco.** Es el caso comun en tablas
chicas, y `passes() == 0` es lo que lo distingue en el plan de ejecucion.

**Los temporales se borran solos.** Se sigue el patron de la reorganizacion del
secuencial (#12) -- escribir aparte, no tocar lo que todavia se lee -- pero aqui
hay varios temporales vivos a la vez, asi que borrarlos al terminar no basta: si
una excepcion interrumpe a media pasada, quedarian regados. Cada temporal se
borra en su destructor, y `NoDejaArchivosTemporalesNiSiquieraSiFalla` lo
comprueba lanzando a proposito desde la fuente.

Ademas, los runs de una pasada se sueltan en cuanto la siguiente los consumio,
asi que el espacio en disco queda acotado a dos pasadas y no a todas.

**El orden es estable.** El desempate del heap va por numero de run, asi que dos
registros con la misma clave conservan su orden relativo entre pasadas. Eso es
lo que hace que las claves repetidas salgan juntas, que es justo lo que el GROUP
BY del #21 necesita para agrupar sin otra pasada.

### GROUP BY externo: hash con re-particionado, y sort (#21)

Segunda mitad de la viñeta de External Algorithms. El enunciado permite
resolverlo "con External Hashing **o el uso estrategico de indices**"; estan
implementados **los dos caminos**, porque el 2.1.6 pide "analisis comparativo
entre las tecnicas implementadas" y con uno solo esa comparacion se afirma en
vez de medirse.

| camino | como | cuando gana |
|---|---|---|
| hash | particiona por hash de la clave; agrega particion por particion | muchos grupos chicos; no necesita orden |
| sort | ordena con el #20 y corta cuando la clave cambia | la salida sale ORDENADA, asi que GROUP BY + ORDER BY por la misma columna paga un solo ordenamiento |

`LosDosCaminosDanExactamenteLoMismo` comprueba que coinciden hasta el ultimo
decimal: si no, comparar sus tiempos no significaria nada.

#### Lo que tiene que caber en memoria son los GRUPOS, no las filas

Fue el error de la primera version y vale la pena dejarlo escrito. El
particionado rechazaba una cubeta por su numero de FILAS, asi que 4 000 filas
repartidas en 5 grupos se declaraban "no caben" -- cuando son cinco
acumuladores de unas decenas de bytes. Y el mensaje culpaba a "las claves son
todas iguales", que era exactamente el caso contrario.

Un acumulador es la clave mas un punado de contadores. Lo que decide si una
particion cabe es cuantas claves DISTINTAS trae, y por eso se agrega al vuelo
contando grupos en vez de mirar el tamaño de la cubeta.

#### Cuando el re-particionado no converge

Si una cubeta sale de una vuelta con exactamente el mismo tamaño con el que
entro, esa semilla no separo nada y las siguientes tampoco lo haran. Ahi:

- con `Strategy::kHash` se lanza `Unsupported`. Existe para medir el hash puro
  en los benchmarks, donde caer a sort falsearia la medicion.
- con `Strategy::kAuto` se cae a **sort**, que no depende de que el hash separe
  nada. Es el mismo problema que las claves repetidas del hash extensible (#18),
  pero aqui hay una salida mejor que encadenar overflow.

El limite no es un contador fijo de vueltas: cuantas hagan falta depende de
cuantos grupos haya y de cuantas particiones se abran. Con 15 particiones,
8 000 grupos convergen; con 2, no -- y eso es correcto, no un fallo.

**Al rendirse hay que rescatar TODAS las filas, tambien las de las cubetas ya
agregadas.** Es el segundo bug que aparecio: esas cubetas se liberaban en cuanto
sus grupos estaban cerrados, asi que el fallback a sort perdia sus filas -- 2 966
de 3 000 en la prueba que lo destapo. Ahora se sostienen hasta saber que nadie
se rindio.

Convertir los grupos ya cerrados de vuelta en filas no sirve: agregar es
asociativo para SUM, MIN y MAX, pero COUNT contaria 1 en vez de N y AVG
promediaria promedios.

#### El AVG no es SUM/COUNT

Sumar 100 000 doubles de magnitudes distintas acumula error de redondeo, y el
2.1.6 compara estos resultados **contra PostgreSQL**, que suma compensado. Un
AVG que devuelve 15,699999999999998 donde PostgreSQL dice 15,7 hace quedar mal a
la comparacion por una razon que no es del algoritmo.

SUM y AVG usan **suma de Neumaier**: cuesta una resta y una suma mas por
elemento. `LaSumaEsCompensadaYNoAcumulaErrorDeRedondeo` suma 1e16 con 10 000
unos -- donde la suma ingenua se traga los unos enteros -- y exige que el
resultado sea exacto **y distinto** del ingenuo, para que la prueba no pase por
casualidad.

#### El esquema de salida no es el de la entrada

Un GROUP BY produce filas con otra forma: `(clave, agregado, agregado, ...)`.
`output_schema()` lo arma, para que el planner (2.1.3) no tenga que inventarlo.
Un SUM de INT sube a DOUBLE porque un int32 puede desbordar, y eso obligaria a
elegir entre truncar y lanzar a media agregacion.
