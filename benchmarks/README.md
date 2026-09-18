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

El banco comparte medicion, aislamiento y exportacion entre experimentos. Por ahora
incluye **solo insercion sobre un Heap vacio**, para validar el recorrido completo.
No compara tecnicas ni implementa estructuras. Requiere los bindings reales
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
normalizar el tiempo de un lote: en este caso equivale a N inserciones. La mediana
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
excluye catalogo, CSV de entrada, resultados y RAM. En este caso no hay indices.
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
`ejecutar_benchmarks.py` contiene el unico adaptador actual, el de Heap.

#39 y #40 podran agregar casos sin cambiar el cronometro o exportador. Para indices
secundarios, el adaptador debera sumar contadores de indice y tabla cuando recupere
registros completos, y separar archivos de datos e indices. No se resuelve en #38
la seleccion de consultas, reorganizaciones, mantenimiento de indices ni rangos.

```bash
python -B -m pytest benchmarks/test_generar_datasets.py benchmarks/test_banco_pruebas.py -q -rs -p no:cacheprovider
ruff check --no-cache benchmarks/scripts/banco_pruebas.py benchmarks/scripts/ejecutar_benchmarks.py benchmarks/test_banco_pruebas.py

# Integracion real pequena, cuando los bindings estan disponibles:
PYTHONPATH=build-py/bindings python -B -m pytest benchmarks/test_banco_pruebas.py -k integracion_heap_real -q -rs -p no:cacheprovider
```

Las pruebas unitarias usan operaciones y relojes controlados exclusivamente en
directorios temporales de pytest; esos resultados no son benchmarks reales. La
integracion usa Heap real con 1k y se marca skip con una razon si falta el modulo.
El CI de Python descubre estas pruebas; el job actual de bindings selecciona otros
archivos, por lo que esta integracion debe ejecutarse explicitamente al validar
un entorno con el core compilado. No se modifico el workflow en este issue.
