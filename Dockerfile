# El motor completo en Linux: core C++ + bindings + capa Python + API.
#
# Es el mismo entorno del job `bindings` del CI (Python 3.11, GCC, cmake con
# QUIPUDB_BUILD_PYTHON=ON). Sirve para levantar la API con todo lo que la
# Parte 4 necesita —numpy, OpenCV con SIFT y soundfile— en maquinas donde el
# Python que carga el modulo nativo no las tiene, como el de MSYS2 en Windows.
FROM python:3.11-slim

# git: FetchContent descarga pybind11 con GIT_REPOSITORY (bindings/CMakeLists.txt).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake git \
    && rm -rf /var/lib/apt/lists/*

# Solo lo que corre la API; los benchmarks (pandas, matplotlib, psycopg) no.
# httpx lo usa el coordinador distribuido para hablar con los nodos (#278).
# opencv-python-headless y no opencv-python: no hay pantalla ni libGL (#176).
RUN pip install --no-cache-dir \
    "fastapi>=0.115" "uvicorn[standard]>=0.32" "pydantic>=2.9" "python-multipart>=0.0.9" \
    "httpx>=0.27" \
    "numpy>=1.26" "opencv-python-headless>=4.4" "soundfile>=0.12"

WORKDIR /app

# Primero el C++: si solo cambia Python, la capa compilada se reutiliza.
COPY CMakeLists.txt ./
COPY core ./core
COPY bindings ./bindings
RUN cmake -S . -B build-py -DCMAKE_BUILD_TYPE=Release \
        -DQUIPUDB_BUILD_PYTHON=ON -DQUIPUDB_BUILD_TESTS=OFF \
    && cmake --build build-py --parallel \
    && python -c "import sys; sys.path.insert(0, 'build-py/bindings'); import quipudb_native"

COPY engine ./engine
COPY scripts ./scripts

ENV PYTHONPATH=/app/build-py/bindings:/app \
    QUIPUDB_CATALOG=/app/datos/catalogo.txt \
    QUIPUDB_MEDIA_ROOT=/app/datos/multimedia

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "engine.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
