# QuipuDB

Motor de base de datos multimodal escrito desde cero: almacenamiento paginado,
indices B+ y hash, R-Tree para datos espaciales, busqueda de texto y busqueda
vectorial sobre imagenes y audio.

El nombre viene del **quipu**, el sistema andino de cuerdas anudadas que los
incas usaban para registrar y recuperar informacion: un motor de almacenamiento
e indexacion anterior en siglos al disco duro.

> Proyecto Integrador del curso **Base de Datos 2**, Universidad de Ingenieria y
> Tecnologia (UTEC), ciclo 2026-2. En desarrollo activo.

## Arquitectura

El motor esta partido en dos capas con un contrato explicito entre ellas: un
core en C++ que es lo unico que toca disco, y una capa Python que traduce SQL a
operaciones del core y la expone por HTTP.

```
 frontend/  ->  engine/api  ->  engine/{parser, planner, transactions}
                                        |  pybind11
                                        v
                                     core/  (C++20)
                          storage · index · external · catalog
```

El detalle esta en [`docs/arquitectura.md`](docs/arquitectura.md), y las
decisiones de diseno fechadas en [`docs/adr/`](docs/adr/).

## Estructura del repositorio

| Ruta | Contenido |
|---|---|
| `core/` | Motor en C++20: paginas, heap file, archivo secuencial, indices, external algorithms |
| `bindings/` | Capa pybind11 que expone el core a Python |
| `engine/` | Parser SQL, planner, transacciones y API REST |
| `frontend/` | Interfaz grafica: archivos, consultas, resultados y plan de ejecucion |
| `benchmarks/` | Comparacion experimental contra PostgreSQL |
| `docs/` | Arquitectura, ADRs e informe |

## Alcance del proyecto

| Parte | Contenido | Estado |
|---|---|---|
| 1 | Base de datos relacional: storage, indices, SQL, transacciones, frontend, benchmarks | En curso |
| 2 | Base de datos espacial: R-Tree, k-NN, consultas por rango y poligono | Pendiente |
| 3 | Busqueda de texto: SPIMI, TF-IDF + coseno, BM25 | Pendiente |
| 4 | Busqueda vectorial multimedia: SIFT/MFCC, IVF, HNSW | Pendiente |
| 5 | Aplicacion de IA sobre la API del motor | Pendiente |

## Compilar y ejecutar

Requisitos: CMake 3.20+, un compilador con C++20 y Python 3.11+.

```bash
# Core en C++
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel
ctest --test-dir build --output-on-failure

# Capa Python
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Si vas a trabajar sobre la capa Python **no necesitas compilar los bindings**;
solo se construyen con `-DQUIPUDB_BUILD_PYTHON=ON`. Las pruebas que dependen de
ellos se saltan solas si el módulo no está: `pytest` sigue pasando.

### Bindings de Python (opcional)

Dan acceso al core desde Python: `Database`, `TableFile`, `Index` y los tipos del
esquema. Los necesitan el parser (2.1.3), las transacciones (2.1.4) y los
benchmarks (2.1.6); el frontend consume la API, no el core.

```bash
cmake -S . -B build-py -DCMAKE_BUILD_TYPE=Release -DQUIPUDB_BUILD_PYTHON=ON
cmake --build build-py --parallel

# Que Python encuentre el modulo
export PYTHONPATH=$PWD/build-py/bindings        # Windows: set PYTHONPATH=%CD%\build-py\bindings
python -m pytest engine/test_bindings.py -q
```

pybind11 se descarga solo con FetchContent: no hay que instalarlo aparte.

```python
import quipudb_native as q

db = q.Database("datos/catalogo.txt")
esquema = q.Schema("alumnos", [
    q.Column("codigo", q.DataType.INT),
    q.Column("nombre", q.DataType.VARCHAR, 32),
    q.Column("promedio", q.DataType.DOUBLE),
], key_column=0)

t = db.create_table(esquema, q.kind.HEAP)
t.insert([1, "ana", 15])            # el 15 se promueve a 15.0 segun el esquema

db.create_index("alumnos", "por_promedio", "promedio", q.kind.EXTENDIBLE_HASH)
ix = db.index("alumnos", "por_promedio")
for rid in ix.search(15.0):
    print(t.read(rid))

print(t.stats().as_dict())          # lo que consume el plan de ejecucion
db.flush()
```

### Procesar SQL

Con los bindings compilados, `QueryProcessor` ejecuta `CREATE TABLE`, `DROP TABLE`,
`INSERT INTO`, `SELECT` y `DELETE FROM`. El resultado de una consulta incluye
las filas y el plan físico realmente recorrido:

```python
import quipudb_native as q

from engine.executor import QueryProcessor

db = q.Database("catalogo.txt")
processor = QueryProcessor(db)
processor.execute(
    "CREATE TABLE cursos (id INT PRIMARY KEY, nombre VARCHAR(40), nota DOUBLE) USING HEAP"
)
processor.execute("INSERT INTO cursos VALUES (1, 'Bases de Datos 2', 18)")

result = processor.execute("SELECT nombre, nota FROM cursos WHERE nota >= 14")
print(result.columns)          # ('nombre', 'nota')
print(result.rows)             # (('Bases de Datos 2', 18.0),)
print(result.plan.to_dict())   # scan/filter/project y sus estadísticas

ordered = processor.execute("SELECT nombre, nota FROM cursos ORDER BY nota DESC")
grouped = processor.execute(
    "SELECT nombre, COUNT(*), AVG(nota) FROM cursos GROUP BY nombre ORDER BY nombre"
)
print(ordered.rows)            # filas ordenadas mediante ExternalSort
print(grouped.columns)         # ('nombre', 'COUNT_all', 'AVG_nota')

deleted = processor.execute("DELETE FROM cursos WHERE nota < 11")
print(deleted.affected_rows)   # 0
print(deleted.plan)            # None: el plan DML aún requiere acuerdo en ADR 0002

processor.execute("DROP TABLE cursos")
```

`WHERE` admite `=`, `<`, `<=`, `>`, `>=` y `BETWEEN` inclusivo. El planner usa
la clave primaria o un índice secundario aplicable; si no existe uno, registra
el `scan` y el filtro en memoria. `DELETE` materializa todos sus candidatos antes
de escribir y mantiene cada índice secundario. `ORDER BY` admite `ASC` y `DESC`
y usa external sorting; `GROUP BY` admite `COUNT(*)`, `SUM`, `MIN`, `MAX` y
`AVG`, y usa external hashing con fallback seguro a sort. El plan explica la
dirección, los runs, las pasadas, la estrategia y las particiones realmente
utilizadas.

Las sentencias pueden ocupar varias líneas y contener comentarios de línea
`-- comentario` o de bloque `/* comentario */`. Los marcadores escritos dentro
de un string se conservan como texto.

Para pruebas reproducibles puede limitarse la memoria de estos algoritmos sin
cambiar los valores por defecto:

```python
processor = QueryProcessor(
    db,
    external_buffers=3,
    external_page_size=512,
    temp_dir="temporales",
)
```

Detalles que conviene saber:

- **`Database` es la puerta de entrada.** Devuelve siempre el mismo objeto para
  una tabla o índice dado; dos handles sobre el mismo archivo se pisan.
- **Los enteros se promueven según el esquema.** Un `15` en una columna DOUBLE
  entra como `15.0`, y en una DATE como `Date(15)`.
- **`bool` es `bool`.** No llega como entero, pese a que en Python `bool` derive
  de `int`.
- **Cada error del core tiene su excepción**: `IoError`, `SchemaError`,
  `InvalidRecord`, `DuplicateKey`, `Unsupported`, todas derivadas de
  `QuipuDBError`.
- **`cursor()` no se expone**: deja de valer si la tabla se modifica, y desde
  Python eso sería un uso-después-de-liberar. `scan_with_rids()` devuelve copias
  materializadas para `DELETE`; `source_of()` lo envuelve sin entregarlo a los
  algoritmos externos.

## Interfaz

Los cuatro paneles de la seccion 2.1.5 -- archivos, consultas, resultados y
plan de ejecucion -- en una sola pantalla. Requiere Node 20+.

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173
```

Arranca con **datos falsos**: no necesita el core compilado ni la API
levantada, y lanza sola una consulta de prueba al abrir. Los planes que dibuja
son los tres ejemplos del [ADR 0002](docs/adr/0002-plan-de-ejecucion.md), asi
que el panel ya sabe representar las formas que el planner produce.

Para hablar con el motor real, copia `frontend/.env.example` a `frontend/.env`
y pon `VITE_USE_MOCK=false`. Todo el trafico pasa por `frontend/src/api/client.ts`;
ningun componente cambia al hacerlo.

| Script | Que hace |
|---|---|
| `npm run dev` | Servidor de desarrollo con recarga en caliente |
| `npm run build` | Verifica tipos y compila a `frontend/dist/` |
| `npm run typecheck` | Solo la verificacion de tipos |

## Equipo

| Integrante | Responsabilidad en la Parte 1 |
|---|---|
| Oswaldo Alejandro Quispe Monzon | Gestion de archivos e indexacion (2.1.1, 2.1.2) |
| Sebastian Cangalaya Martinez | Procesamiento de consultas SQL (2.1.3) |
| Juan David Velo Poma | Transacciones y concurrencia (2.1.4) |
| Danna Gala | Interfaz de usuario (2.1.5) |
| Mauricio Teran | Comparacion experimental (2.1.6) |

## Contribuir

Las convenciones de commits, ramas y pull requests estan en
[`CONTRIBUTING.md`](CONTRIBUTING.md). Leelo antes del primer commit: el
historial del repositorio es parte de la evaluacion del curso.

## Licencia

MIT. Ver [`LICENSE`](LICENSE).
