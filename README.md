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
solo se construyen con `-DQUIPUDB_BUILD_PYTHON=ON`.

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
