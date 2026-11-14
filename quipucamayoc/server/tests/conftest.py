"""El motor real para las pruebas de punta a punta.

Dos formas, en este orden:

1. ``QUIPUCAMAYOC_TEST_MOTOR_URL=http://127.0.0.1:8000``: un motor ya
   levantado. Las tablas de la prueba llevan un prefijo unico y se borran al
   final, asi que no tocan el corpus.
2. Si el modulo nativo se puede importar (``PYTHONPATH=build-py/bindings``),
   el API del motor corre dentro del proceso con ``TestClient`` y un catalogo
   temporal. Es lo que hace el CI en el job de los bindings.

Sin ninguna de las dos, las pruebas se saltan: la aplicacion no necesita el
core compilado para el resto de sus pruebas.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import uuid
from pathlib import Path

import pytest

from app.corpus.arxiv import Paper
from app.corpus.carga import cargar
from app.corpus.esquema import Tablas
from app.motor import ClienteMotor, MotorError
from tests.pdfs import escribir

_RAIZ = Path(__file__).resolve().parents[3]


def _motor_en_proceso(catalogo: Path) -> ClienteMotor:
    if str(_RAIZ) not in sys.path:
        sys.path.insert(0, str(_RAIZ))
    from engine.api.main import create_app
    from fastapi.testclient import TestClient

    http = TestClient(create_app(catalog=catalogo))
    return ClienteMotor(str(http.base_url), http=http)


@pytest.fixture()
def motor_real(tmp_path: Path):
    """``(cliente, prefijo)``: el prefijo aisla las tablas de cada prueba."""

    url = os.environ.get("QUIPUCAMAYOC_TEST_MOTOR_URL")
    if url:
        cliente = ClienteMotor(url)
    elif importlib.util.find_spec("quipudb_native") is not None:
        cliente = _motor_en_proceso(tmp_path / "catalogo.txt")
    else:
        pytest.skip("sin QUIPUCAMAYOC_TEST_MOTOR_URL ni modulo nativo no hay motor real")

    prefijo = f"t{uuid.uuid4().hex[:8]}_"
    yield cliente, prefijo
    try:
        for tabla in sorted(cliente.table_names()):
            if tabla.startswith(prefijo):
                cliente.query(f"DROP TABLE {tabla}")
    except MotorError:
        pass
    cliente.close()


# Un corpus de tres papers de temas distintos, cargado con la carga de verdad.
_TEMAS = {
    "2401.00001v1": (
        "Graph indexes",
        [
            "Hierarchical navigable small world graphs answer nearest neighbor queries.",
            "Greedy routing descends the layers of the graph to reach the query.",
            "Graph indexes trade memory for high recall at low latency.",
        ],
    ),
    "2401.00002v1": (
        "Inverted files",
        [
            "Inverted file indexes cluster vectors around centroids with k-means.",
            "A query probes the nprobe closest lists to find candidates.",
            "Product quantization compresses the vectors stored in each list.",
        ],
    ),
    "2401.00003v1": (
        "Query optimization",
        [
            "Cost based optimizers choose join orders with cardinality estimates.",
            "Histograms summarize column distributions for selectivity.",
            "Dynamic programming enumerates plans for small join graphs.",
        ],
    ),
}


@pytest.fixture()
def corpus(motor_real, tmp_path: Path):
    motor, prefijo = motor_real
    tablas = Tablas(prefijo)
    papers = []
    for arxiv_id, (titulo, oraciones) in _TEMAS.items():
        paper = Paper(arxiv_id, titulo, ("Ada Lovelace",), 2020, "cs.DB", "Resumen.")
        papers.append(paper)
        # Dos paginas por paper, cada una un poco distinta: varios chunks.
        escribir(
            tmp_path / paper.archivo,
            [[f"{titulo} page one.", *oraciones], [f"{titulo} page two.", *reversed(oraciones)]],
        )
    reporte = cargar(motor, papers, tmp_path, tablas=tablas)
    assert reporte.fallidos == []
    return motor, tablas


@pytest.fixture()
def corpus_vectorial(corpus, tmp_path: Path):
    """El mismo corpus con sus embeddings (falsos) cargados y el HNSW creado."""

    from app.corpus.vectores import cargar as cargar_vectores
    from app.corpus.vectores import chunks_del_motor
    from tests.llm_falso import EmbedderFalso

    motor, tablas = corpus
    embedder = EmbedderFalso()
    chunks = chunks_del_motor(motor, tablas)
    vectores = dict(
        zip([c for c, _, _ in chunks], embedder.documentos([t for _, _, t in chunks]), strict=True)
    )
    reporte = cargar_vectores(motor, chunks, vectores, tablas=tablas)
    assert reporte.indice_creado and not reporte.sin_embedding
    return motor, tablas
