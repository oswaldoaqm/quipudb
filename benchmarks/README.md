# Datasets y banco de pruebas reproducible

El issue #5 prepara los mismos registros para las pruebas de storage, indices
y la comparacion experimental de QuipuDB. El generador requiere Python 3.11+
y solo usa la biblioteca estandar; no necesita compilar el core ni los bindings.

## Generar los CSV

Desde la raiz del repositorio:

```bash
python benchmarks/scripts/generar_datasets.py
```

Genera `alumnos_1000.csv`, `alumnos_10000.csv` y `alumnos_100000.csv` dentro de
`benchmarks/datasets/`, con exactamente 1 000, 10 000 y 100 000 registros,
respectivamente, mas una cabecera. La ruta predeterminada se calcula desde el
script, por lo que no depende del directorio de trabajo.

Para elegir una carpeta de salida o generar solo un tamano:

```bash
python benchmarks/scripts/generar_datasets.py --salida /tmp/quipudb-datasets
python benchmarks/scripts/generar_datasets.py --tamanos 1000
```

Se pueden combinar ambas opciones. Solo se admiten los tres tamanos indicados.
El script crea la carpeta si hace falta y reemplaza los CSV solicitados si ya
existen. Los datos sinteticos son regenerables: `.gitignore` ya excluye
`benchmarks/datasets/`, salvo su `.gitkeep`. Los CSV no se commitean.

## Esquema comun

La cabecera es `codigo,nombre,promedio`, en ese orden.

| Columna | Tipo en QuipuDB | Contenido |
|---|---|---|
| `codigo` | `INT`, clave primaria | Enteros unicos de 1 a N |
| `nombre` | `VARCHAR(16)` | `alumno` seguido del codigo; como maximo 12 caracteres ASCII |
| `promedio` | `DOUBLE` | Entre 0.00 y 20.00 inclusive, escrito con dos decimales |

Formato: CSV separado por comas, UTF-8 sin BOM, saltos de linea LF y punto
decimal. No se escribe una columna extra de indice. Al cargarlo en el motor,
convertir `codigo` a `int` y `promedio` a `float`; `nombre` se conserva como texto.
El esquema usa `key_column=0` y ocupa 28 bytes por registro serializado antes
de cabeceras y otros datos de almacenamiento.

Heap, Secuencial y B+ agrupado reciben los registros completos. Para B+ no
agrupado y Extendible Hashing, se carga un Heap y se crea el indice sobre
`codigo`. El CSV no contiene RIDs: dependen del archivo de datos que se cree.

## Reproducibilidad

La semilla fija es **20260906**. Cada llamada a `generar_dataset` crea su propia
instancia de `random.Random`: generar 100k primero no afecta a 1k ni a 10k.
Se crean los registros por codigo, se eligen uniformemente las centesimas del
promedio entre 0 y 2000, y despues se mezclan las filas con `shuffle`.
Todos deben conservar ese mismo orden de insercion para el escenario base.

Los codigos compartidos conservan sus valores entre tamanos, pero las filas
de 1k no son necesariamente las primeras 1 000 del CSV de 100k.
Python 3.11 es la referencia del CI. La reproducibilidad requiere mantener el
algoritmo y su entorno: las funciones de `random` pueden cambiar entre versiones
de Python. Las pruebas comparan los bytes y fijan esta referencia SHA-256 de 1k:

```text
851cea735a096721d5970ec14bf29d41e87b3fe4467a0eb4a05e3a5e6fd04ad7
```

## Comprobar el cambio

Con pytest y Ruff instalados en el entorno de desarrollo:

```bash
python -B -m pytest benchmarks/test_generar_datasets.py -q -p no:cacheprovider
ruff check --no-cache benchmarks/scripts/generar_datasets.py benchmarks/test_generar_datasets.py
```

Las pruebas usan carpetas temporales y comprueban los tres tamanos, esquema,
unicidad, limites, orden mezclado, igualdad byte por byte, generacion individual
y orden independiente de las llamadas. No necesitan CSV previamente generados.
Las comparaciones completas y las graficas corresponden a los issues #39, #40 y #41.

## Banco de pruebas (issue #38)

El banco comparte medicion, aislamiento y exportacion entre experimentos. El caso
predeterminado conserva **solo insercion sobre un Heap vacio** del issue #38.
El issue #39 agrega `--suite archivos` con seis casos de comparacion (ver abajo).
No implementa estructuras. Requiere los bindings reales
`quipudb_native` compilados para el mismo Python que ejecuta el script; consulta
la seccion de bindings del README principal. Sin ellos la CLI falla explicitamente:
nunca genera resultados simulados. La infraestructura usa la biblioteca estandar.

Desde la raiz del repositorio, con los datasets de #5 ya generados:

```bash
PYTHONPATH=build-py/bindings python -B benchmarks/scripts/ejecutar_benchmarks.py

# Comprobacion pequena; el resto de los parametros mantiene sus valores predeterminados.
PYTHONPATH=build-py/bindings python -B benchmarks/scripts/ejecutar_benchmarks.py --tamanos 1000

# Se pueden configurar entrada, salida, base temporal y cantidad de corridas.
PYTHONPATH=build-py/bindings python -B benchmarks/scripts/ejecutar_benchmarks.py \
  --datasets benchmarks/datasets --salida benchmarks/results --temporales /tmp \
  --tamanos 1000 10000 100000 --calentamientos 1 --repeticiones 5
```

Las rutas predeterminadas de entrada/salida se calculan desde el script, no desde
el directorio de trabajo. La carpeta indicada en `--temporales` debe existir;
elige una ubicada en el mismo dispositivo para todas las mediciones comparables.
El banco crea y elimina solamente sus propios subdirectorios temporales.

`--page-size BYTES` es opcional: si se omite, no se pasa ese argumento al binding.
El valor realmente utilizado se obtiene de `Database.table_info().page_size` y se
registra en el entorno, junto con el origen (binding o seleccion explicita).
No se duplica una constante de tamano de pagina en el banco.

### Metodo de medicion

1. Leer y validar cada CSV completo, convertir a `int`/`str`/`float` y calcular su
   SHA-256 antes de medir. No regenerar datos ni cambiar el orden de las filas.
2. Por caso y tamano, ejecutar un calentamiento y cinco repeticiones medidas por
   defecto. Ambos son configurables: calentamientos >= 0, repeticiones >= 1.
3. En **cada corrida**, incluso calentamientos, crear un catalogo y archivos nuevos
   dentro de un `TemporaryDirectory`. Preparar y hacer flush fuera del cronometro.
4. Medir espacio inicial y reiniciar contadores. Usar `time.perf_counter_ns()`
   alrededor de la operacion; la insercion incluye el bucle Python/binding y un
   `db.flush()` final, pero no la creacion de la tabla vacia.
5. Inmediatamente despues de detener el reloj, copiar `pages_read` y
   `pages_written`. Luego medir espacio final y validar todos los registros.
   La validacion puede leer disco, pero no contamina ni tiempo ni contadores.
6. Cerrar tablas/indices antes de eliminar temporales, incluso ante errores.
   Descartar calentamientos y conservar todas las repeticiones medidas.
7. Exportar las muestras y su mediana, minimo y maximo de tiempo. No eliminar
   outliers. Si falla una corrida o su validacion, abortar sin exportar una serie
   experimental incompleta como si fuera valida.

Los tiempos son nanosegundos enteros; la unidad no implica precision real de 1 ns.
Para graficas, convertir a ms dividiendo entre 1 000 000. `n_operaciones` permite
normalizar el tiempo de un lote: en insercion equivale a N inserciones. La mediana
principal corresponde al tiempo del lote completo, no a consultas individuales.

### Resultados CSV

Cada ejecucion crea un identificador UTC con UUID y tres archivos UTF-8 con LF.
Se abren en modo exclusivo: no sobrescriben ejecuciones anteriores. Los CSV
directamente dentro de `benchmarks/results/` ya estan ignorados por Git.

`<id>_mediciones.csv`, una fila por repeticion medida:

```csv
ejecucion,caso,tecnica,operacion,n_registros,n_operaciones,repeticion,tiempo_ns,paginas_leidas,paginas_escritas,datos_bytes,indices_bytes,espacio_antes_bytes,espacio_despues_bytes,estado
```

`<id>_resumen.csv`, agrupado por ejecucion, caso, tecnica, operacion, N y numero de
operaciones; nunca mezcla configuraciones entre corridas:

```csv
ejecucion,caso,tecnica,operacion,n_registros,n_operaciones,repeticiones,mediana_tiempo_ns,mediana_paginas_leidas,mediana_paginas_escritas,mediana_datos_bytes,mediana_indices_bytes,mediana_espacio_antes_bytes,mediana_espacio_despues_bytes,min_tiempo_ns,max_tiempo_ns
```

`<id>_entorno.csv`, formato largo para metadatos generales y por caso/tamano:

```csv
ejecucion,clave,valor
```

`datos_bytes` e `indices_bytes` son tamanos finales de los archivos del motor,
obtenidos del sistema de archivos. El total antes/despues suma datos e indices;
excluye catalogo, CSV de entrada, resultados y RAM. Estas suites no crean indices.
No mide bloques fisicos asignados ni el pico transitorio de espacio.
`paginas_leidas` cuenta accesos instrumentados por el core, no lecturas fisicas
del SSD. `estado` es `ok` en las filas exportadas; los errores abortan la ejecucion,
no se convierten en ceros ni entran a la mediana.

### Entorno y mediciones oficiales

Se registran automaticamente sistema, arquitectura, informacion disponible de CPU,
Python y su compilador (no el del core), reloj/resolucion, commit/estado de Git,
semilla del generador, hashes/rutas de datasets, calentamientos, repeticiones,
ruta temporal, archivo del modulo nativo y configuracion real del caso.

RAM, disco y compilacion del core quedan como `no_documentado` si no se declaran.
Se puede repetir `--entorno CLAVE=VALOR`; se guarda como `declarado.CLAVE` para no
confundir informacion proporcionada por una persona con deteccion automatica:

```bash
PYTHONPATH=build-py/bindings python -B benchmarks/scripts/ejecutar_benchmarks.py \
  --tamanos 1000 --entorno compilacion_core=Release \
  --entorno 'compilador_core=VERSION_REAL' --entorno 'cpu=MODELO_REAL' \
  --entorno 'ram=CANTIDAD_REAL' --entorno 'disco=DISPOSITIVO_REAL'
```

Sustituir los marcadores por datos verificados; el script no comprueba declaraciones
manuales. Antes de considerar una corrida oficial, completar esos datos, comprobar
que el modulo corresponde al commit registrado y que fue compilado en Release.
Conservar los tres CSV juntos. Una ejecucion desde un arbol con cambios se indica
en `git_cambios`; el commit por si solo no identifica esos cambios locales.

La cache del sistema operativo no se controla. Archivos nuevos y calentamiento no
garantizan cache fria ni uniformemente caliente. `flush` no equivale a `fsync`;
no se promete persistencia fisica inmediata. Usar el mismo equipo, version de
Python, build, dispositivo y parametros, sin benchmarks concurrentes. Reproducible
significa entradas y procedimiento repetibles, no tiempos identicos.

### Extension y pruebas

`banco_pruebas.py` define `Dataset`, `Caso` y `Operacion`. Cada `Caso.preparar`
es un administrador de contexto: recibe directorio y dataset, prepara recursos,
entrega callbacks de operacion/contadores/espacio/validacion y cierra en `finally`.
Su configuracion y numero de operaciones deben mantenerse entre repeticiones.
`casos_archivos.py` contiene los adaptadores de Heap y Secuencial; la CLI conserva
`caso_insercion_heap` como entrada compatible con el issue #38.

#39 reutiliza el banco sin cambiar su cronometro ni exportador. Para #40 e indices
secundarios, el adaptador debera sumar contadores de indice y tabla cuando recupere
registros completos, y separar archivos de datos e indices. No se resuelve en #38
la seleccion de consultas, reorganizaciones, mantenimiento de indices ni rangos.

```bash
python -B -m pytest benchmarks/test_generar_datasets.py benchmarks/test_banco_pruebas.py benchmarks/test_casos_archivos.py -q -rs -p no:cacheprovider
ruff check engine benchmarks
ruff format --check benchmarks/scripts/casos_archivos.py benchmarks/scripts/ejecutar_benchmarks.py benchmarks/test_casos_archivos.py

# Integracion real pequena, cuando los bindings estan disponibles:
PYTHONPATH=build-py/bindings python -B -m pytest benchmarks/test_banco_pruebas.py -k integracion_heap_real -q -rs -p no:cacheprovider
```

Las pruebas unitarias usan operaciones y relojes controlados exclusivamente en
directorios temporales de pytest; esos resultados no son benchmarks reales. La
integracion usa Heap y Secuencial reales con 1k y se marca skip con una razon si
falta el modulo. Las pruebas de seleccion/porcentaje tambien comprueban 10k y 100k
sin construir tablas nativas grandes: la corrida completa es un experimento.
El CI de Python descubre estas pruebas; el job actual de bindings selecciona otros
archivos, por lo que esta integracion debe ejecutarse explicitamente al validar
un entorno con el core compilado. No se modifico el workflow en este issue.

## Comparacion de archivos (issue #39)

`--suite archivos` agrega los seis casos siguientes. Sin esa opcion, o con
`--suite heap`, sigue ejecutandose solamente el caso Heap minimo de #38.

| `caso` | `tecnica` | `operacion` | `n_operaciones` |
|---|---|---|---|
| `heap_insercion` | `heap` | `insercion` | N |
| `sequential_insercion` | `sequential` | `insercion` | N |
| `heap_busqueda_pk` | `heap` | `busqueda_pk` | consultas |
| `sequential_busqueda_pk` | `sequential` | `busqueda_pk` | consultas |
| `sequential_reorganizacion_explicita` | `sequential` | `reorganizacion_explicita` | 1 |
| `sequential_eliminacion_con_reorganizacion` | `sequential` | `eliminacion_con_reorganizacion` | 1 |

### Protocolo

- **Insercion:** misma tupla de registros en el orden mezclado del CSV, mismo
  esquema y configuracion de pagina. Medir N llamadas a `insert()` y el `db.flush()`
  final. No ordenar previamente el CSV para favorecer al Secuencial.
- **Busqueda PK:** cargar y hacer flush fuera del cronometro. Medir un lote de
  1000 busquedas exitosas por defecto (`--consultas`, entre 1 y el menor N).
  Usar `Random(20260906).sample(range(1, N + 1), consultas)`, sin reemplazo,
  con un generador local nuevo por tamano y caso. Ambas tecnicas reciben las
  mismas claves en el mismo orden, no las primeras filas fisicas del CSV.
  La materializacion de las respuestas del binding entra al tiempo; las
  comprobaciones de contenido quedan fuera. No se hace flush durante busquedas.
- **Reorganizacion explicita:** cargar N, eliminar exactamente `3*N//10` claves
  distintas y hacer flush durante la preparacion. Verificar vivos y desperdicio
  de 0.30: con la implementacion actual la reorganizacion automatica ocurre
  cuando el desperdicio es **mayor**, no igual, al 30%. Medir una unica llamada
  a `q.reorganize(tabla)` y el flush final. Es la medida principal del costo de
  reorganizar, aunque se prepara al 30% para evitar una reorganizacion previa.
- **Eliminacion con reorganizacion:** misma preparacion; medir solo la siguiente
  eliminacion y flush, sin llamar a `q.reorganize()`. Incluye busqueda, eliminacion,
  metadatos, reorganizacion automatica y flush; no es reorganizacion aislada.
  Las claves de eliminacion provienen de otro generador local con la misma
  semilla, muestreando `3*N//10 + 1` codigos. Ambos escenarios comparten las
  primeras D eliminaciones y difieren en un sobreviviente final.
- Tras reorganizar, comprobar desperdicio cero y todos los registros completos
  sobrevivientes (700/699, 7000/6999 o 70000/69999). Estas validaciones ocurren
  despues de copiar los contadores. El banco crea una tabla independiente por
  calentamiento y repeticion, y cierra/elimina sus temporales incluso ante errores.

La carga, seleccion de claves, eliminaciones preparatorias, validacion y exportacion
no se cronometran. La creacion del archivo interno `.reorg` por el core **si** es
parte del trabajo de reorganizacion medido, a diferencia del directorio temporal
externo del banco. No existe un caso artificial de reorganizacion Heap.

### Validacion pequena y corrida oficial

Con el Python compatible con el modulo nativo, ejecutar primero:

```bash
PYTHONPATH=build-py/bindings python -B benchmarks/scripts/ejecutar_benchmarks.py \
  --suite archivos --tamanos 1000 --consultas 1000 \
  --calentamientos 1 --repeticiones 2 --entorno proposito=validacion_pequena_no_oficial
PYTHONPATH=build-py/bindings python -B -m pytest benchmarks/test_casos_archivos.py -q -rs -p no:cacheprovider
```

La corrida oficial de #39 ya fue completada y documentada. Comando de referencia
del protocolo (no es necesario ejecutarlo para generar graficas):

```bash
PYTHONPATH=build-py/bindings python -B benchmarks/scripts/ejecutar_benchmarks.py \
  --suite archivos --tamanos 1000 10000 100000 --consultas 1000 \
  --calentamientos 1 --repeticiones 5 --entorno proposito=experimento_oficial
```

Antes de esa corrida, agregar las declaraciones verificadas de compilacion,
maquina y disco descritas arriba; fijar el mismo dispositivo temporal para todas
las tecnicas. Ese comando esta documentado, no se ejecuta automaticamente.

### Interpretacion y limitaciones

Se conservan los tres CSV y sus cabeceras de #38 sin cambios. `n_registros` es N
inicial, tambien en reorganizaciones. `n_operaciones` cuenta llamadas de la fase
medida, no las de preparacion. Los hashes de las secuencias, semilla, numero de
borrados, vivos iniciales y finales esperados, desperdicio inicial observado,
pagina real y alcance del tiempo quedan en `<id>_entorno.csv`. Los valores finales
esperados se validan, no se presentan como contadores adicionales observados.

`datos_bytes` y espacio antes/despues provienen de `stat().st_size` del archivo
real indicado por `table_info().file`, tras flush. Para comparar espacio Heap vs
Secuencial, usar las filas de insercion con N vivos; no comparar estas filas con
una tabla reorganizada que tiene menos registros. No se mide el pico de espacio
durante la coexistencia temporal del archivo original y `.reorg`.

`mediana_tiempo_ns / n_operaciones` en busquedas es el tiempo medio por consulta
del lote representativo, **no la mediana de latencias individuales**. Se guardan
las muestras, mediana, minimo y maximo, sin descartar outliers. Para graficas de
#41 se podran usar tiempo de insercion, tiempo total/normalizado de busquedas,
espacio final, paginas y los dos tiempos de reorganizacion claramente separados.

Los bindings no exponen el contador de reorganizaciones ni el ajuste del umbral;
el estado al 30% y a 0% permite comprobar el contrato actual, no contar llamadas
internas de C++. Si cambia ese contrato, los adaptadores fallan explicitamente.
Las paginas provienen de `tabla.stats()` y reflejan la instrumentacion del core,
no todas las E/S fisicas ni necesariamente todos sus accesos internos (por ejemplo,
la correccion de la ultima pagina en reorganizacion no contabiliza todo su trabajo).
No se altera el core para corregir o completar contadores en este issue.

El bucle Python/binding entra en ambos tiempos. La cache del SO, orden fijo de
casos, calentamiento, procesos concurrentes, frecuencia de CPU y disco pueden
sesgar resultados. No se promete cache fria, fsync ni resultados identicos entre
maquinas/versiones. Registrar version de Python y hashes permite identificar las
entradas: no se supone estabilidad universal de `random.sample` entre versiones.

El informe [comparacion_heap_secuencial.md](comparacion_heap_secuencial.md) conserva
el protocolo y los resultados oficiales: 90 mediciones y 18 resumenes. Las graficas
se generan por separado en #41, sin ejecutar esta suite nuevamente.

## Comparacion de indices (issue #40)

`--suite indices` ejecuta 21 casos por tamano usando los mismos CSV de #5 y el
banco de #38 sin modificaciones. Las suites `heap` (predeterminada) y `archivos`
conservan su comportamiento. No se modifican core, bindings ni datasets.

`tecnica` usa `bplus_clustered`, `bplus_unclustered` o `extendible_hash`;
`caso` es `<tecnica>_<operacion>`:

| Operacion | Tecnicas | n_operaciones |
|---|---|---|
| `carga_indexada` | Las tres | N inserciones logicas |
| `construccion_indice` | Los dos secundarios | 1 create_index |
| `busqueda_igualdad` | Las tres | consultas (1000 por defecto) |
| `busqueda_rango` | Los dos B+ | consultas-rango (100 por defecto) |
| `recorrido_ordenado` | Los dos B+ | 1 recorrido completo de N filas |
| `insercion_incremental` | Las tres | D=N//10 altas |
| `eliminacion_incremental` | Las tres | D=N//10 bajas |
| `mantenimiento_mixto` | Las tres | 2*D*3 operaciones logicas |

### Preparacion y operaciones medidas

Todas las repeticiones y calentamientos usan catalogos/archivos independientes.
Esquema, lectura del CSV, conversion, seleccion de consultas, oraculos esperados,
preparacion, reset de contadores y validacion quedan fuera del cronometro.
Los bindings se importan solo al ejecutar; no son necesarios para los tests unitarios.

- **Carga indexada:** crear tabla vacia y, para secundarios, indice vacio fuera
  del cronometro. Agrupado inserta en la tabla-arbol; secundarios insertan en Heap
  y luego en el indice con el RID recibido. Medir todas las altas en orden CSV y
  un flush final. Una insercion logica puede requerir dos llamadas nativas.
- **Construccion:** cargar Heap sin indice durante preparacion. Medir un unico
  `db.create_index()` completo y flush: incluye catalogo, archivo y recorrido de
  los datos. Reset del Heap antes; el indice es nuevo y sus contadores no se
  reinician al terminar build. No existe un caso equivalente de indice secundario
  sobre B+ agrupado, ni se resta el tiempo de otra corrida para aislar su costo.
- **Igualdad:** cargar fuera del intervalo con la politica de carga indexada.
  Reutilizar `seleccionar_claves()` de #39, semilla 20260906, sin reemplazo sobre
  1..N y generador independiente por tamano. Mismas claves para las tres tecnicas.
  Agrupado hace search; secundarios hacen index.search y heap.read por cada RID.
  Materializar todas las respuestas dentro, validar fuera. No flush de consultas.
- **Rangos:** 100 intervalos inclusivos por defecto, de ancho N//100 (1% para los
  tres tamanos). Inicios distintos muestreados localmente con la misma semilla;
  los intervalos pueden solaparse. Mismos rangos para ambos B+. Resolver los RIDs
  del no agrupado dentro del intervalo. Guardar y validar respuestas completas.
- **Orden:** un recorrido completo ascendente por codigo. Agrupado usa scan;
  no agrupado usa scan del indice y lee los RIDs en ese orden. La validacion NO
  ordena el resultado para ocultar un recorrido incorrecto. La carga no se mide.
- **Altas incrementales:** sobre N iniciales, insertar D=N//10 registros derivados
  en memoria antes de medir. Se muestrean D codigos c y se forman registros
  `(N+c, "alumno" + str(N+c), promedio_original_de_c)`. Son claves nuevas,
  mezcladas pero mayores que las iniciales; no se alteran los CSV comunes. Medir
  insercion completa y flush final; validar N+D registros y sus entradas.
- **Bajas incrementales:** sobre N iniciales, retirar D claves de la misma muestra.
  En secundarios se elimina del indice y del Heap. Medir lote y flush; validar
  N-D sobrevivientes, retornos de remove y ausencia de entradas/RIDs incorrectos.
- **Mixto:** sobre N iniciales, repetir tres veces: borrar D claves y reinsertar
  sus registros en el orden muestreado. Misma muestra en los tres ciclos y entre
  tecnicas. Siempre usar el RID NUEVO al reinsertar en el indice. Un solo flush
  al final del lote medido. Validar N filas originales; el tiempo no se atribuye
  exclusivamente a insertar o borrar.

Los deletes/insert del adaptador coordinan operaciones, no implementan estructuras
ni transacciones: si falla una llamada, el banco aborta y limpia el estado temporal,
sin exportar resultados incompletos. No intenta recuperarse y continuar midiendo.

Hash no soporta rango ni orden nativos: se registran `rango_nativo=False` y
`orden_nativo=False` en el entorno. No hay casos emulados, filas ficticias ni
tiempos cero; tampoco se introduce un sort/scan alternativo bajo el nombre Hash.

### Metricas y configuracion

No cambian las cabeceras de los tres CSV. El adaptador copia las paginas del
agrupado, o suma paginas de Heap e indice en los secundarios. El banco captura
esas copias inmediatamente despues del reloj, antes de metadata/espacio/validacion.

Los bytes se obtienen con stat de las rutas de table_info.file y de su metadata
indexes, despues de flush. Para agrupado, datos_bytes incluye el archivo integrado
y indices_bytes es cero archivos secundarios; no significa costo de indexacion
cero. Para secundarios, datos_bytes es Heap e indices_bytes es el indice real.
Antes de construir el indice su espacio es cero; despues se mide el archivo creado.
Catalogo, CSV, RAM y pico temporal no se contabilizan como espacio de tabla/indice.

La configuracion por caso en entorno conserva pagina real y origen, modulo nativo,
politica de carga, semilla, hashes de consultas/altas/bajas, selectividad, D, ciclos,
vivos iniciales/finales esperados y politica de flush. N en n_registros es el tamano
del dataset inicial; no el numero de filas despues de un lote de modificaciones.
Los vivos finales esperados se validan, no se presentan como nuevos contadores.

El costo incluye Python/bindings y resolucion de RIDs; las funciones C++ lookup,
lookup_range y estadisticas internas no estan expuestas. No se supone orden del
arbol, altura, capacidad de bucket ni numero de splits/merges. Solo se configura
page-size si se solicita, y se registra el valor real de table_info.page_size.
Las paginas instrumentadas pueden omitir E/S de metadata; no representan lecturas
fisicas de SSD. Se mantienen las limitaciones de cache, fsync y entorno de #38/#39.

### Validacion y ejecucion

Con el Python compatible con los bindings Release:

```bash
# Solo validacion pequena, no corrida oficial:
PYTHONPATH=build-py/bindings python -B benchmarks/scripts/ejecutar_benchmarks.py \
  --suite indices --tamanos 1000 --consultas 1000 --consultas-rango 100 \
  --calentamientos 1 --repeticiones 2 --entorno proposito=validacion_indices_1k_no_oficial

PYTHONPATH=build-py/bindings python -B -m pytest benchmarks/test_generar_datasets.py \
  benchmarks/test_banco_pruebas.py benchmarks/test_casos_archivos.py \
  benchmarks/test_casos_indices.py -q -rs -p no:cacheprovider
ruff check engine benchmarks
ruff format --check benchmarks/scripts/casos_indices.py benchmarks/scripts/ejecutar_benchmarks.py benchmarks/test_casos_indices.py
git diff --check
```

consultas debe estar entre 1 y el menor N. consultas-rango entre 1 y el menor
`N - N//100 + 1`. Los tests nativos usan solamente 1k y se saltan con motivo claro
si no existe el binding compatible; los unitarios usan dobles solo en temporales.

La corrida oficial ya fue completada con `--tamanos 1000 10000 100000
--calentamientos 1 --repeticiones 5`: 315 mediciones y 63 resumenes. No se debe
repetir para generar graficas. Conservar sus tres CSV ignorados por Git separados
de los de validacion.

El informe [comparacion_indices.md](comparacion_indices.md) describe capacidades,
sesgos, resultados oficiales y conclusiones basadas en esas mediciones.

## Graficas e informe (issue #41)

`scripts/generar_graficas.py` convierte exclusivamente las dos corridas oficiales
en 12 figuras de tiempo y tres de espacio, cada una en PNG 300 dpi y SVG, mas las
tablas numericas del [informe integrado](../docs/informe/comparacion_experimental.md).
No importa bindings, ejecuta benchmarks ni lee/regenera datasets. Mantiene #39 y
#40 separados y conserva las cinco muestras, incluidos los valores altos.

Fuentes necesarias: mediciones, resumen y entorno de cada uno de estos IDs:

- #39: `20260918T025045Z_530093b2675c4ff8a483020858915b56`.
- #40: `20260918T035512Z_4e75ba53c6a6497798a0d95a278f9dfe`.

Los seis CSV siguen ignorados por Git: otro integrante debe obtenerlos del archivo
de resultados oficiales y copiarlos a `benchmarks/results/` o indicar --entrada.
No se selecciona la corrida mas reciente ni se extraen numeros del Markdown.
El [manifiesto](../docs/informe/fuentes_resultados.json) fija nombres y SHA-256.
Si falta una fuente o cambia su hash, el generador falla antes de exportar.

Desde la raiz, con Matplotlib (ya declarado en requirements.txt):

```bash
MPLCONFIGDIR=/tmp/quipudb-matplotlib python -B benchmarks/scripts/generar_graficas.py
# Opcional: otras carpetas, manteniendo exactamente los seis archivos oficiales.
MPLCONFIGDIR=/tmp/quipudb-matplotlib python -B benchmarks/scripts/generar_graficas.py \
  --entrada /ruta/a/csv-oficiales --salida /ruta/a/informe
```

Salida predeterminada: `docs/informe/graficas/` (30 archivos) y el bloque de tablas
de `docs/informe/comparacion_experimental.md`. El texto fuera de los marcadores
BEGIN/END RESULTADOS actua como plantilla y se conserva. Una carpeta --salida
alternativa recibe figuras y una copia del informe; los enlaces relativos a otros
documentos suponen el destino canonico docs/informe. El manifiesto canonico se
consulta en el repositorio y no se copia a la carpeta alternativa.

La exportacion reemplaza solo sus figuras e informe derivados; nunca las fuentes.
No cambiar los hashes para aceptar otra corrida. PNG/SVG e informe se versionan,
pero los CSV siguen fuera de Git. svg.hashsalt fijo y ausencia de fecha SVG facilitan
la reproduccion con la misma version de Matplotlib/fuentes; no se promete identidad
binaria entre versiones. La version del renderizador queda en el informe.

Medianas y minimo-maximo de cinco repeticiones, con todas las muestras visibles
(pueden superponerse); no son intervalos de confianza. Ejes temporales logaritmicos.
No hay filas cero para capacidades ausentes. Espacio total considera datos e indice;
en agrupado el arbol esta integrado en datos. Consultar pies de figura y limites.

Pruebas del generador, sin medir estructuras:

```bash
MPLCONFIGDIR=/tmp/quipudb-matplotlib python -B -m pytest benchmarks/test_generar_graficas.py \
  -q -rs -p no:cacheprovider
ruff check engine benchmarks
ruff format --check benchmarks/scripts/generar_graficas.py benchmarks/test_generar_graficas.py
git diff --check
```

Las pruebas usan fuentes sinteticas explicitamente de prueba, solo en temporales.
No necesitan los CSV ignorados ni bindings. La prueba de renderizado se salta con
razon explicita si falta Matplotlib (el CI actual instala pytest y Ruff); antes de
entregar las figuras, ejecutarla con Matplotlib y revisar visualmente las 15.

## Dataset espacial (issue #130)

La comparacion del 2.2.4 (secuencial vs R-Tree vs GIST) mide las tres tecnicas
sobre los mismos puntos. Como con los datasets de #5, el generador solo usa la
biblioteca estandar y los CSV quedan fuera de Git (`benchmarks/datasets/*`).

```bash
python benchmarks/scripts/generar_puntos.py
python benchmarks/scripts/generar_puntos.py --tamanos 1000 --salida /tmp/quipudb-puntos
```

Genera `puntos_1000.csv`, `puntos_10000.csv` y `puntos_100000.csv` con la
cabecera `id,ciudad,latitud,longitud`:

| Columna | Tipo en QuipuDB | Contenido |
|---|---|---|
| `id` | `INT`, clave | Enteros 1..N, en orden |
| `ciudad` | `VARCHAR(16)` | Ciudad alrededor de la cual se genero el punto |
| `latitud`, `longitud` | `POINT` | Grados WGS84, seis decimales fijos (~11 cm) |

### Distribucion

Los puntos **no** son uniformes sobre el mundo. Con puntos repartidos al azar por
el planeta los MBR casi no se solapan y el R-Tree sale favorecido de forma
artificial; datos concentrados en ciudades son el caso real y el mas exigente.

- Se eligen las 15 ciudades mas pobladas del Peru, cada una con probabilidad
  proporcional a su poblacion urbana (censo 2017, aproximada). Lima concentra
  ~61 % de los puntos.
- Alrededor de la ciudad, un desplazamiento normal en **kilometros** norte y este,
  con desviacion `3.8 km * sqrt(poblacion / 1 millon)`: ~12 km en Lima, <2 km en
  una ciudad de 200 mil. El 10 % de los puntos cae en la periferia, con una
  desviacion cinco veces mayor.
- Los kilometros se convierten a grados con 111,195 km por grado de latitud y
  ese valor por el coseno de la latitud para la longitud, de modo que las nubes
  no salen estiradas este-oeste.

No se modela la costa: algunos puntos de ciudades costeras caen en el mar. No
afecta a las mediciones, que dependen de la densidad y no del uso del suelo.

Como referencia, con 100k puntos y centros tomados del propio dataset, un radio
de 1 km devuelve entre 1 y ~200 puntos, uno de 5 km hasta ~4 600 y uno de 10 km
hasta ~16 000 (16 % del total): ahi es donde la poda del R-Tree deja de valer la
pena, que es lo que busca #131.

### Reproducibilidad

Misma semilla que #5, **20260906**, con un `random.Random` nuevo por tamano. Cada
punto consume siempre la misma cantidad de numeros aleatorios, asi que los
datasets estan **anidados**: 1k son las primeras 1 000 filas de 10k, y 10k las
primeras de 100k. Al crecer el dataset solo se agregan puntos. El SHA-256 de
referencia de 1k, comprobado en Python 3.11 y 3.14, es:

```text
a71f78dc15cd138a90c7be4d8cb6a925bbb2afacfc22e613a80b743061e6c82e
```

### Carga en QuipuDB

`scripts/cargar_puntos.py` valida el CSV completo (ids 1..N en orden, coordenadas
finitas y en rango, ciudad compatible con `VARCHAR(16)`), crea la tabla Heap
`puntos(id INT, ciudad VARCHAR(16), ubicacion POINT)`, inserta en el orden del CSV
y construye el R-Tree `puntos_ubicacion` con `create_index`. El R-Tree necesita
un Heap porque sus RID son estables. Requiere los bindings:

```bash
PYTHONPATH=build-py/bindings python -B benchmarks/scripts/cargar_puntos.py \
  benchmarks/datasets/puntos_100000.csv --catalogo /tmp/quipudb-puntos-100k
# --sin-indice deja solo el Heap, que es la base de la busqueda secuencial.
```

La carpeta de `--catalogo` debe ser nueva o estar vacia. Imprime filas, entradas
del indice, tiempo total y el tamano de cada archivo; no es una medicion
oficial, que corresponde a #131. `leer_puntos` y `cargar_quipudb` son las que
reutilizan los benchmarks para que todas las tecnicas reciban las mismas filas.

```bash
python -B -m pytest benchmarks/test_generar_puntos.py -q -rs -p no:cacheprovider
PYTHONPATH=build-py/bindings python -B -m pytest benchmarks/test_generar_puntos.py \
  -k integracion -q -rs -p no:cacheprovider
ruff check engine benchmarks
```

Las pruebas comprueban formato, bytes identicos entre corridas, el hash de 1k, el
anidamiento, que cada punto queda cerca de su ciudad y dentro del Peru, que la
cantidad por ciudad sigue a la poblacion, que las nubes no estan estiradas, y la
validacion del CSV. La integracion carga 1k y compara un radio de 10 km del
R-Tree contra un recorrido completo con una Haversine propia; se salta con motivo
si faltan los bindings.

## R-Tree vs busqueda secuencial (issue #131)

`--suite espacial` mide el R-Tree contra recorrer la tabla entera, sobre los CSV
de puntos de #130 y con el mismo banco de #38 (calentamiento, repeticiones,
aislamiento y los tres CSV de resultados sin cambios de cabecera).

```bash
python benchmarks/scripts/generar_puntos.py
PYTHONPATH=build-py/bindings python -B benchmarks/scripts/ejecutar_benchmarks.py \
  --suite espacial --tamanos 1000 10000 100000 --calentamientos 1 --repeticiones 5 \
  --entorno proposito=experimento_oficial --entorno compilacion_core=Release
```

`--consultas` (100 por defecto, maximo 1 000) fija el tamano del lote y
`--sin-cruce` omite los radios grandes. La suite usa el page-size del binding.

### Las dos tecnicas

- **secuencial**: `quipudb_native.scan_radius` / `scan_k_nearest`. Recorren el
  Heap con su cursor y miden la distancia a cada punto. Estan en C++ a proposito:
  si el recorrido fuera Python y el R-Tree C++, se mediria el interprete y no la
  poda. El k-NN guarda los k mejores en un max-heap, O(k) de memoria.
- **rtree**: `RTreeIndex.search_radius` y `RTreeIndex.k_nearest` sobre el R-Tree
  `puntos_ubicacion`, montado sobre el mismo Heap. El k-NN es el best-first de
  #121, que se detiene sin leer los subarboles mas lejanos que el k-esimo.

Las dos devuelven **RID**, sin leer los registros: entregan lo mismo y ninguna
paga una lectura del Heap que la otra no paga. El tiempo incluye el cruce
Python/binding de cada consulta y la materializacion de la lista de RIDs.

### Casos

`caso` es `<tecnica>_<operacion>_<metrica>`; `n_operaciones` es el numero de
consultas del lote, asi que `mediana_tiempo_ns / n_operaciones` es el tiempo
medio por consulta (promedio de 100 consultas, como pide el enunciado).

| Operacion | Tecnicas | Metricas |
|---|---|---|
| `construccion_indice` | rtree | — |
| `radio_1km`, `radio_5km`, `radio_10km` | las dos | haversine, euclidiana |
| `knn_10`, `knn_50`, `knn_100` | las dos | haversine, euclidiana |
| `radio_25km` ... `radio_2000km` (cruce) | las dos | haversine |

37 casos por tamano. El secuencial no tiene construccion: no se genera una fila
con tiempo cero. La construccion mide `create_index` (catalogo, archivo,
recorrido del Heap e insercion de cada punto) mas un flush, sobre un Heap
cargado fuera del reloj.

### Consultas

- **Centros:** `Random(20260906).sample(range(1, 1001), 100)` elige ids de los
  primeros 1 000 puntos y se usan sus coordenadas. Como los datasets estan
  anidados, son **los mismos centros en 1k, 10k y 100k**: entre tamanos solo
  cambia la densidad alrededor. Caen donde hay datos, no en el mar.
- **Radios:** en metros con Haversine. Con la euclidiana el radio va en grados y
  se usa el equivalente de los mismos km (`km * 1000 / 111 195`). Ese circulo
  en grados mide lo mismo de norte a sur, pero de este a oeste solo
  `km * cos(latitud)`: en Lima un 2 % menos y en Tacna un 5 %. Por eso la
  euclidiana devuelve algo menos de puntos, y la comparacion entre metricas mide
  sobre todo el costo de la formula, no el de regiones distintas.
- **Radios de cruce:** 25, 50, 100, 250, 500 y 2 000 km, solo con Haversine.
  Buscan el radio donde la poda ya no descarta nada y el indice solo agrega
  trabajo; 2 000 km cubre todo el Peru.

### Validacion

Fuera del reloj. La primera vez que aparece una consulta (por dataset, operacion,
metrica y centros), su respuesta se calcula con la busqueda secuencial y se
guarda. Cada repeticion de **las dos tecnicas** se compara contra esa referencia:

- radio: la cantidad y el SHA-256 del conjunto ordenado de RIDs (los RID
  coinciden porque cada repeticion carga el mismo CSV en el mismo orden en un
  Heap nuevo). Se guarda el hash y no la lista: con 2 000 km la referencia de
  100k serian 10 millones de RIDs, mas de 1 GB, y el recolector de basura
  agregaria ruido a los tiempos;
- k-NN: la lista de distancias, recalculadas en Python, que debe estar ordenada
  y tener k elementos. Se comparan distancias y no RIDs porque con empates cual
  entra es indistinto.

Las pruebas de C++ comprueban ademas la busqueda secuencial contra un oraculo
independiente, asi que la referencia no es circular. Una diferencia aborta la
corrida sin exportar.

### Metricas

- `paginas_leidas`: suma de los contadores del Heap y del R-Tree. Es la metrica
  que no depende de la maquina. El secuencial lee siempre todas las paginas del
  Heap; el R-Tree, solo los nodos cuyo MBR intersecta la region (o que el k-NN
  no pudo descartar).
- `datos_bytes` / `indices_bytes`: tamano del Heap y del archivo `.rtree`.
- En el entorno, por caso: metrica, radio en km y en unidades, `resultados_totales`
  (puntos devueltos en las 100 consultas, identico entre tecnicas) y
  `fraccion_media_devuelta`, que es la selectividad.

**Memoria.** Las dos estructuras viven en disco y se leen pagina a pagina: el
banco no mide RAM. Por consulta, el secuencial necesita una pagina y O(k) para el
k-NN; el R-Tree, el camino de la recursion para el radio y la cola de prioridad
del best-first para el k-NN (O(k + frontera)). El costo de memoria permanente es
el espacio en disco del indice.

### Pruebas

```bash
python -B -m pytest benchmarks/test_casos_espaciales.py -q -rs -p no:cacheprovider
PYTHONPATH=build-py/bindings python -B -m pytest benchmarks/test_casos_espaciales.py \
  engine/test_bindings.py -q -rs -p no:cacheprovider
ruff check engine benchmarks
```

Las unitarias comprueban centros identicos entre tamanos, conversion de radios,
el oraculo de distancias, la lista de casos y la CLI. La integracion corre una
suite reducida sobre 1k con bindings reales y comprueba que el secuencial lee
las mismas paginas con cualquier radio, que el R-Tree poda con radio chico y no
con 2 000 km, y que las dos tecnicas devuelven la misma cantidad de puntos.
Los resultados oficiales y su analisis estan en
[comparacion_rtree_secuencial.md](comparacion_rtree_secuencial.md).

## GiST de PostGIS (issue #132)

`scripts/bench_postgis.py` corre las mismas consultas de la suite espacial sobre
PostgreSQL con PostGIS: mismos CSV de #130, mismos 100 centros, radios, k y
metricas. Escribe los tres CSV del banco con `tecnica=postgis_gist`, asi que se
grafican junto a los de #131. No necesita los bindings; si `psycopg` (ya en
`requirements.txt`) y un servidor.

### Levantar PostGIS

Los indices GiST espaciales necesitan PostGIS, una extension aparte. La forma
mas simple es su imagen oficial de Docker; se usaron PostgreSQL 17.5 y PostGIS
3.5.2:

```bash
docker run -d --name quipudb-postgis -e POSTGRES_PASSWORD=quipudb   -p 55432:5432 postgis/postgis:17-3.5 -c jit=off
```

El script se conecta por defecto a `localhost:55432` con usuario `postgres` y
clave `quipudb` (`--dsn` para otro servidor), reintenta mientras el contenedor
arranca y crea la extension si falta. `jit=off` evita que la compilacion JIT
entre en consultas de milisegundos.

### Medir

```bash
python benchmarks/scripts/generar_puntos.py
python -B benchmarks/scripts/bench_postgis.py --tamanos 1000 10000 100000   --calentamientos 1 --repeticiones 5   --referencia benchmarks/results/<id de #131>_entorno.csv   --entorno proposito=experimento_oficial
```

`--referencia` compara la cantidad de puntos devueltos en cada radio con la
corrida de QuipuDB y aborta si no coincide. En la oficial coincidieron los 54.

Por tamano y metrica se crea una tabla (`puntos_geog` con `geography` para
Haversine, `puntos_geom` con `geometry` para la euclidiana), se copia el CSV en
su orden y se hace `VACUUM ANALYZE`. Despues:

- **Construccion:** `DROP INDEX` y `CREATE INDEX ... USING gist`, cronometrado
  desde el cliente, en cada repeticion. PostgreSQL no da buffers de un `CREATE
  INDEX`, asi que sus paginas quedan en 0 y el entorno lo marca como
  `paginas_construccion=no_instrumentadas`.
- **Consultas:** `ST_DWithin(ubicacion, centro, radio, false)` (esfera, la misma
  de la Haversine del core) o `ST_DWithin` en grados con `geometry`, y
  `ORDER BY ubicacion <-> centro LIMIT k` para el k-NN. Cada una con
  `EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON)`:
  `tiempo_ns` es la suma de los `Execution Time` del lote y `paginas_leidas` los
  bloques compartidos tocados (hit + read, de 8 KiB). Se fuerza el indice con
  `enable_seqscan = off` y se verifica que el plan lo use; el plan que habria
  elegido PostgreSQL queda como `plan_natural` en el entorno.
- **Tiempo del cliente:** despues, el mismo lote sin `EXPLAIN`, cronometrado
  desde Python. Su mediana queda como `tiempo_cliente_ns_mediana`: incluye la
  red hasta el contenedor, que en Docker para Windows cuesta mas que la consulta.

Las consultas se repiten sobre la misma tabla, sin recargar: los datos quedan en
`shared_buffers`, igual que los archivos de QuipuDB quedan en la cache del
sistema. Docker corre en una VM y no se puede fijar a un nucleo como el proceso
de #131; la dispersion es mayor y el informe lo indica.

### Graficas e informe del 2.2.4

`scripts/generar_graficas_espacial.py` lee solo las dos corridas oficiales
fijadas en `docs/informe/fuentes_espacial.json` (nombres y SHA-256 de los seis
CSV, que siguen fuera de Git) y genera siete figuras PNG + SVG en
`docs/informe/graficas/espacial/` y las tablas del
[informe integrado](../docs/informe/comparacion_espacial.md), entre sus marcadores
BEGIN/END RESULTADOS. Si falta una fuente o cambio su hash, falla antes de
escribir nada.

```bash
MPLCONFIGDIR=/tmp/quipudb-matplotlib python -B benchmarks/scripts/generar_graficas_espacial.py
MPLCONFIGDIR=/tmp/quipudb-matplotlib python -B -m pytest   benchmarks/test_postgis_y_graficas_espacial.py -q -rs -p no:cacheprovider
```

Las pruebas no necesitan PostgreSQL ni los CSV oficiales: comprueban el SQL
generado, que PostGIS cubra exactamente los casos de la suite espacial, la
lectura de referencias, y el generador sobre fuentes sinteticas (tablas, hash
alterado y conservacion del texto fuera de los marcadores). La que verifica los
hashes oficiales se salta con motivo si los CSV no estan.
