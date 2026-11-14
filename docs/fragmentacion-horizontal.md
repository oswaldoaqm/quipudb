# Fragmentación horizontal implementada — Issue #276

La implementación reutiliza el motor local Python/C++ y el formato del [contrato v1](contrato-distribuido-v1.md), conforme a la [arquitectura distribuida](arquitectura-distribuida.md). Implementa metadatos, pertenencia, selección de
rangos y preparación/auditoría física fuera de línea. No introduce coordinador, HTTP,
enrutamiento SQL ni servicios de mutación.

## Archivos e interfaces

| Archivo | Responsabilidad |
|---|---|
| `engine/distributed/__init__.py` | Paquete sin inicialización del motor. |
| [`contracts.py`](../engine/distributed/contracts.py) | Modelos estrictos e inmutables de nodos, columnas, índices, tablas y fragmentos. |
| [`catalog.py`](../engine/distributed/catalog.py) | `DistributedCatalog`, `CatalogError`, `load_catalog`. |
| [`partitioning.py`](../engine/distributed/partitioning.py) | `owner_for_key`, `fragments_for_comparison`. |
| [`protocol.py`](../engine/distributed/protocol.py) | Sobres de petición, salud, catálogo, ejecución y error; solo modelos, sin endpoints. Reutiliza `QueryResponse` y `TableInfo`. |
| [`storage.py`](../engine/distributed/storage.py) | Preparación y auditoría fuera de línea de directorios de nodos range. |
| [`scripts/demo_fragmentacion.py`](../scripts/demo_fragmentacion.py) | Procesos independientes que insertan con `QueryProcessor`, buscan y reabren archivos reales. |
| [`ejemplos/catalogo-distribuido-v1.json`](ejemplos/catalogo-distribuido-v1.json) | Configuración reproducible, con Heap, B+ secundario y hash extensible. |
| `engine/distributed/conftest.py`, `test_*.py` | Pruebas junto al código, como en el resto de `engine`. |
| Pruebas locales | La suite del #276 se ejecutó con bindings Python/C++ recompilados. No se modificó el workflow de GitHub Actions. |

No se modifican C++, bindings, parser, executor, API local, frontend ni módulos asignados a
Oswaldo. `storage.py` es una adaptación adicional acotada a #276: demuestra que la asignación
puede materializarse y persistir en motores reales; no es el futuro `fragments.py` ni una
puerta para ejecutar operaciones remotas.

## Configuración y validación

El JSON de ejemplo usa `protocol_version: 1`, `catalog_version: 1`, los nodos `n1` y `n2` y la
tabla `items(id INT, nombre VARCHAR(80))`. Declara dos fragmentos:

| Fragmento | Nodo | Intervalo |
|---|---|---|
| `items_f0` | `n1` | `[null, 10000)` |
| `items_f1` | `n2` | `[10000, null)` |

`null` indica infinito, no una clave nula. Cada límite finito debe ser INT32. Se exige
cobertura completa, sin huecos, superposiciones ni fragmentos vacíos en el dominio
[-2147483648, 2147483647]. Una tabla range tiene dos o más fragmentos, máximo uno por nodo y
PK INT sobre Heap. Los fragmentos se pueden declarar en cualquier orden; `ordered_fragments`
los ordena por límite inferior.

Los modelos validan nombres, referencias, organización, tipos de columnas, tamaños, PK,
índices compatibles y campos requeridos. Los límites de registro de Heap y secuencial se
comprueban reutilizando `bind_create_table`, sin reconstruir SQL ni cargar C++. Los límites
físicos específicos del B+ agrupado siguen siendo responsabilidad del núcleo al abrir/preparar
sus tablas de modo single. El preparador de este issue no crea tablas single.

El catálogo acepta tablas single para conservar sus metadatos, incluida PK de otro tipo y las
organizaciones existentes. FULLTEXT solo se admite en single; las columnas POINT y VECTOR
exigen índices R-Tree y vectoriales respectivamente cuando se indexan. Solo B+ secundario
declara `supports_range: true`.

`load_catalog` rechaza JSON con campos repetidos, NaN/Infinity, campos adicionales, tipos
coercibles pero incorrectos (`true`, `1.0` o `"1"` en lugar de INT) y versiones de protocolo
distintas de 1. No modifica el archivo leído. `catalog_version` puede ser cualquier entero
positivo: debe incrementarse cuando cambien metadatos; `expected_catalog_version` permite
exigir el valor de un despliegue. No se confunde esa versión con el número de protocolo.

Para cambiar la frontera, cambiar `upper` de `items_f0` y `lower` de `items_f1` al mismo valor
e incrementar `catalog_version`. Preparar nuevos datos fuera de servicio; esta implementación
no migra ni redistribuye archivos existentes. Las URLs son metadatos: la demostración no abre
puertos ni necesita DNS para n1/n2.

## Uso de las funciones

Desde la raíz del repositorio, con las dependencias Python instaladas:

```python
from engine.distributed.catalog import load_catalog
from engine.distributed.partitioning import owner_for_key, fragments_for_comparison

catalog = load_catalog(
    "docs/ejemplos/catalogo-distribuido-v1.json", expected_catalog_version=1,
)
table = catalog.table("items")
fragment = owner_for_key(table, 10000)
assert fragment.fragment_id == "items_f1"
assert catalog.node(fragment.node_id).base_url == "http://n2:8000"
assert [f.fragment_id for f in catalog.fragments_for_node("n1")] == ["items_f0"]
assert [f.fragment_id for f in fragments_for_comparison(table, "<", 10000)] == ["items_f0"]
```

Estas funciones son puras, no importan `quipudb_native` ni escriben datos. Su entrada es un
`DistributedTable` validado, no un nombre de tabla suelto. `owner_for_key` devuelve el
fragmento completo con `node_id`. `fragments_for_comparison` admite `=`, `<`, `<=`, `>` y `>=`
sobre PK; devuelve una tupla ordenada, que puede estar vacía en `< INT32_MIN` o `> INT32_MAX`.
No genera claves fuera del dominio para comparar extremos. No interpreta WHERE ni decide qué
hacer con filtros de otras columnas: eso corresponde a #279.

Sobre single, ambas funciones de rangos lanzan `ValueError`; su único propietario se obtiene
directamente de `table.fragments[0]`. Las búsquedas de metadatos desconocidos lanzan
`KeyError`; un catálogo de archivo inválido lanza `CatalogError`, y la construcción directa de
modelos inválidos lanza `pydantic.ValidationError`. Para nuevas configuraciones se debe
validar de nuevo; no usar `model_construct` ni `model_copy(update=...)` para eludir
validaciones.

## Almacenamiento físico

`prepare_range_node(catalog, node_id, data_dir) -> Path` crea un directorio nuevo,
`catalogo.txt`, tablas Heap e índices declarados B+, hash o R-Tree. Usa
`Database.create_table`, `to_native_schema` y `Database.create_index`. Cierra los handles y
hace `flush()` antes de retornar. No elimina datos ante un fallo: deja los restos para
inspección y exige otra ruta nueva al repetir.

`verify_range_node(catalog, node_id, data_dir) -> dict[str, int]` exige archivos existentes,
reabre el catálogo, compara las tablas, columnas e índices con los metadatos y recorre todas
las PK comprobando su pertenencia. Devuelve conteos por fragmento. Rechaza tablas inesperadas,
diferencias de esquema/índices y filas fuera del fragmento, sin repararlas. Comprueba que los
archivos declarados estén en el directorio propio y no sean enlaces simbólicos hacia otro
nodo.

Ambas utilidades requieren ejecución fuera de línea: nadie más debe tener el directorio
abierto. No constituyen un lock entre procesos. El preparador rechaza un directorio existente,
incluso vacío, para no adoptar archivos compartidos. La auditoría no crea un catálogo si
falta. El control de acceso y la validación de cada escritura serán responsabilidad de #277:
escribir directamente en el motor local todavía permite saltarse la pertenencia, y la
auditoría lo detecta.

El preparador automático se limita a nodos con tablas range y rechaza IVF/HNSW antes de crear
archivos. El catálogo v1 no contiene centroides ni métricas para crear esos índices; no se
inventan parámetros. Sus metadatos sí se representan y validan. Las búsquedas especializadas y
las tablas single siguen usando la preparación local existente, como establece #275.

## Reproducir dos nodos reales

Usar el mismo Python con el que se compilaron los bindings. Ejemplo con `.venv`:

```bash
.venv/bin/python -m pip install -r requirements.txt
cmake -S . -B build-py -DQUIPUDB_BUILD_PYTHON=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build-py --parallel
PYTHONPATH=build-py/bindings .venv/bin/python -c 'import quipudb_native; print(quipudb_native.__file__)'
PYTHONPATH=build-py/bindings .venv/bin/python -m scripts.demo_fragmentacion --root /tmp/quipudb-276-demo
```

Si CMake está instalado dentro del entorno, usar `.venv/bin/cmake`. No ejecutar la demo sobre
datos existentes: elegir una raíz nueva. La demo inicia un proceso por nodo en paralelo,
prepara y carga cada uno, espera su terminación y después lanza procesos nuevos para verificar
persistencia. En cada nodo las inserciones pasan por `QueryProcessor`, conservando índices y
validaciones locales.

Con la frontera de ejemplo se espera:

| Nodo | Claves persistidas |
|---|---|
| n1 | -2147483648, 9999 |
| n2 | 10000, 10001, 2147483647 |

Cada directorio contiene su propio `catalogo.txt`, `items.heap`,
`items.por_id.bplus_unclustered` e `items.por_nombre.extendible_hash`. La demo busca todas las
claves en ambos motores mediante el índice de nombre y comprueba que las ajenas no aparezcan.
Emite JSON por nodo/fase con PID, ruta, archivos, claves y conteos; no son respuestas HTTP ni
otro formato de catálogo.

Para verificar de nuevo, sin insertar ni preparar:

```bash
PYTHONPATH=build-py/bindings .venv/bin/python -m scripts.demo_fragmentacion --root /tmp/quipudb-276-demo --verify-only
```

También acepta `--catalog /ruta/cluster.json`. La demo requiere el esquema de ejemplo
`items(id INT, nombre VARCHAR(80))`; permite cambiar fronteras y cantidad de nodos. Las
utilidades de `storage.py` no están limitadas a ese esquema. El script es una demostración
local reproducible, no un servidor distribuido ni un coordinador de producción.

## Pruebas locales

```bash
# Modelos, catálogo y rangos; sin bindings, test_storage se reporta como omitido.
.venv/bin/python -m pytest engine/distributed -q -rs -p no:cacheprovider

# Incluye archivos reales, procesos separados, índices y reapertura.
PYTHONPATH=build-py/bindings .venv/bin/python -m pytest engine/distributed -q -rs -p no:cacheprovider

# Regresión Python y núcleo existente.
PYTHONPATH=build-py/bindings .venv/bin/python -m pytest engine benchmarks -q -rs -p no:cacheprovider
ctest --test-dir build-py --output-on-failure --no-tests=error

# Calidad de los archivos de implementación nuevos.
.venv/bin/ruff check engine/distributed scripts/demo_fragmentacion.py
git diff --check
```

Las pruebas nativas solo se omiten cuando no se puede importar el binding. El workflow de
GitHub Actions no se modificó para esta entrega: las pruebas de `engine/distributed` se
ejecutaron localmente con los bindings recompilados y su integración automática en CI queda
pendiente.

`test_protocol.py` valida directamente los ocho ejemplos JSON normativos del #275.
`test_partitioning.py` usa un oráculo de claves para cada comparación y fronteras cercanas a
ambos extremos INT32. `test_storage.py` ejecuta la demo con 10000 y -5, comprueba archivos
distintos, cuatro procesos, relectura y una tercera apertura. También prueba auditoría de
filas mal ubicadas y metadatos alterados.

## Dependencias para #277

- Cargar una configuración con `load_catalog` y exigir la versión de despliegue.
- Usar `DistributedCatalog.table/node/fragments_for_node` y los objetos inmutables.
- Usar `owner_for_key` antes de mutaciones y confirmar el nodo/fragmento recibido.
- Usar los modelos de `protocol.py`, sin definir otro sobre JSON.
- `table.bound_schema` sirve al binder/adaptador existentes. Reutilizar `QueryProcessor`/DML para mantener índices; no insertar directamente en Heap.
- La auditoría de almacenamiento puede ejecutarse antes de abrir el servicio; no llamarla abriendo un segundo `Database` sobre un directorio ya en uso.

Los modelos de protocolo solo validan estructura. #277 aún debe validar SQL, identidad,
pertenencia, correspondencia entre versiones y petición, errores de ejecución, locks y cierre
del servicio. #278/#279 implementarán transporte, coordinación y combinación; este issue no
ofrece esos comportamientos.

## Límites

No hay replicación, migración, redistribución, transacciones entre nodos, reintentos ni
recuperación ante caída. La demostración prueba persistencia tras cierre normal, no
durabilidad ante corte de energía. Las búsquedas de texto, multimedia, vectoriales y
espaciales distribuidas no se habilitan: conserva el alcance #275. La auditoría recorre los
datos completos y no está diseñada para cada petición.

El plan de commits y la matriz de evidencia de esta entrega están en [Revisión del #276](revision-issue-276.md).