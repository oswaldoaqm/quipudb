# Datasets comunes de prueba

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
Las mediciones de rendimiento y las graficas corresponden a los siguientes issues.
