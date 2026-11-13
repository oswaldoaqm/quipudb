"""Entrenar y asociar vocabularios por REST (#287), sin el core compilado.

Los archivos son reales: SIFT corre sobre imágenes derivadas de
``multimedia/datos/ejemplo.png`` y MFCC sobre tramos de ``ejemplo.wav``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engine.api.main import create_app
from engine.api.vocabularies import REGISTRY_FILE
from engine.executor import QueryProcessor
from engine.executor.test_processor import _FakeDatabase, _NativeModule
from engine.multimedia.features import Vocabulary

cv2 = pytest.importorskip("cv2", reason="se necesita opencv-python-headless")
sf = pytest.importorskip("soundfile", reason="se necesita soundfile")

DATA = Path(__file__).parents[1] / "multimedia" / "datos"


def images(folder: Path, count: int = 6) -> list[Path]:
    """Recortes, giros y espejos de la imagen de ejemplo: todos con descriptores."""

    folder.mkdir(parents=True, exist_ok=True)
    base = cv2.imread(str(DATA / "ejemplo.png"))
    h, w = base.shape[:2]
    paths = []
    for i in range(count):
        image = base[i * 4 : h - i * 3, i * 2 : w - i * 5]
        image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE) if i % 2 else cv2.flip(image, 1)
        path = folder / f"foto_{i}.png"
        cv2.imwrite(str(path), image)
        paths.append(path)
    return paths


def audios(folder: Path, count: int = 4) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    signal, rate = sf.read(DATA / "ejemplo.wav")
    step = len(signal) // (count + 1)
    paths = []
    for i in range(count):
        path = folder / f"tramo_{i}.wav"
        sf.write(path, signal[i * step : i * step + 2 * step], rate)
        paths.append(path)
    return paths


@pytest.fixture()
def api(tmp_path):
    def build():
        database = _FakeDatabase()
        processor = QueryProcessor(database, native_module=_NativeModule)
        processor.execute(
            "CREATE TABLE fotos (id INT PRIMARY KEY, nombre VARCHAR(64), "
            "ruta VARCHAR(1024), rasgos VECTOR(8))"
        )
        processor.execute("CREATE TABLE doble (id INT PRIMARY KEY, a VECTOR(8), b VECTOR(4))")
        app = create_app(
            processor=processor, database=database, native=_NativeModule, media_root=tmp_path
        )
        return TestClient(app), processor

    return build


def wait(client, job_id):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        response = client.get(f"/vocabularies/{job_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"completed", "failed", "cancelled"}:
            return body
        time.sleep(0.01)
    pytest.fail("el entrenamiento no terminó")


def train(client, **request):
    response = client.post("/vocabularies", json=request)
    assert response.status_code == 202, response.json()
    return wait(client, response.json()["job_id"])


def test_entrena_imagenes_y_las_asocia_a_la_tabla(api, tmp_path):
    images(tmp_path / "fotos")
    (tmp_path / "fotos" / "leeme.txt").write_text("no es multimedia", encoding="utf-8")
    (tmp_path / "fotos" / "rota.png").write_bytes(b"no es una imagen")
    client, processor = api()
    with client:
        job = train(client, folder="fotos", k=8, seed=3, table="fotos")

    assert job["status"] == "completed", job
    assert (job["kind"], job["discovered"], job["skipped"], job["selected"]) == (
        "imagen",
        8,
        1,
        7,
    )
    assert (job["described"], job["failed"]) == (6, 1)
    assert job["errors"][0]["path"] == "fotos/rota.png"
    assert job["vocabulary"] == "vocabularios/fotos-k8.npz"
    assert job["registered_for"] == "fotos"
    assert job["documents"] == 6

    vocabulary = Vocabulary.load(tmp_path / job["vocabulary"])
    assert (vocabulary.k, vocabulary.dim, vocabulary.identifier) == (8, 128, job["codebook_id"])
    # Queda en el procesador abierto y en disco.
    assert processor._vector_search.vocabularies["fotos"] == "vocabularios/fotos-k8.npz"
    saved = json.loads((tmp_path / REGISTRY_FILE).read_text(encoding="utf-8"))
    assert saved == {"fotos": "vocabularios/fotos-k8.npz"}


def test_la_asociacion_sobrevive_al_reinicio(api, tmp_path):
    images(tmp_path / "fotos")
    client, _ = api()
    with client:
        train(client, folder="fotos", k=8, table="fotos")
    client, processor = api()  # otra API sobre la misma raíz multimedia
    with client:
        listed = client.get("/vocabularies").json()
    assert processor._vector_search.vocabularies["fotos"] == "vocabularios/fotos-k8.npz"
    assert [(v["path"], v["kind"], v["registered_for"]) for v in listed] == [
        ("vocabularios/fotos-k8.npz", "imagen", ["fotos"])
    ]


def test_entrena_audio_sin_asociarlo_y_no_pisa_uno_existente(api, tmp_path):
    audios(tmp_path / "sonidos")
    client, processor = api()
    with client:
        first = train(client, folder="sonidos", k=4, name="canciones")
        second = train(client, folder="sonidos", k=4, name="canciones")
        listed = client.get("/vocabularies").json()
    assert first["status"] == second["status"] == "completed"
    assert (first["kind"], first["registered_for"]) == ("audio", None)
    assert first["vocabulary"] == "vocabularios/canciones.npz"
    assert second["vocabulary"] == "vocabularios/canciones-2.npz"
    assert {v["path"]: (v["dimension"], v["registered_for"]) for v in listed} == {
        "vocabularios/canciones.npz": (13, []),
        "vocabularios/canciones-2.npz": (13, []),
    }
    assert not (tmp_path / REGISTRY_FILE).exists()
    assert "canciones" not in processor._vector_search.vocabularies


def test_max_files_toma_archivos_espaciados(api, tmp_path):
    images(tmp_path / "fotos", count=6)
    client, _ = api()
    with client:
        job = train(client, folder="fotos", k=8, max_files=3)
    assert (job["discovered"], job["selected"], job["described"]) == (6, 3, 3)
    assert job["documents"] == 3


def test_mezclar_imagenes_y_audios_falla_sin_guardar_nada(api, tmp_path):
    images(tmp_path / "mezcla", count=2)
    audios(tmp_path / "mezcla", count=2)
    client, _ = api()
    with client:
        job = train(client, folder="mezcla", k=4)
    assert job["status"] == "failed"
    assert "mezcla 2 imágenes y 2 audios" in job["error"]
    assert not (tmp_path / "vocabularios").exists()


def test_k_mayor_que_los_descriptores_falla_con_su_motivo(api, tmp_path):
    audios(tmp_path / "corto", count=1)
    client, _ = api()
    with client:
        job = train(client, folder="corto", k=4000)
    assert job["status"] == "failed"
    assert job["error"]


@pytest.mark.parametrize(
    ("request_", "status", "fragment"),
    [
        ({"folder": "fotos", "k": 8, "table": "nada"}, 404, "no existe"),
        ({"folder": "fotos", "k": 16, "table": "fotos"}, 400, "VECTOR(8)"),
        ({"folder": "fotos", "k": 8, "table": "doble"}, 400, "2 columnas VECTOR"),
        ({"folder": "fotos", "k": 8, "table": "fotos", "column": "ruta"}, 400, "no es VECTOR"),
        ({"folder": "fotos", "k": 8, "column": "rasgos"}, 400, "junto con table"),
        ({"folder": "../fuera", "k": 8}, 400, "QUIPUDB_MEDIA_ROOT"),
        ({"folder": "no_existe", "k": 8}, 400, "no se puede abrir"),
        ({"folder": "fotos", "k": 8, "name": "../x"}, 422, "name"),
        ({"folder": "fotos", "k": 1}, 422, "k"),
    ],
)
def test_errores_antes_de_entrenar(api, tmp_path, request_, status, fragment):
    images(tmp_path / "fotos", count=1)
    client, _ = api()
    with client:
        response = client.post("/vocabularies", json=request_)
    assert response.status_code == status
    assert fragment in response.json()["error"]


def test_asociar_un_vocabulario_existente(api, tmp_path):
    images(tmp_path / "fotos")
    client, processor = api()
    with client:
        trained = train(client, folder="fotos", k=8)
        response = client.put(
            "/tables/fotos/vocabulary", json={"vocabulary": trained["vocabulary"]}
        )
        by_column = client.put(
            "/tables/doble/vocabulary", json={"vocabulary": trained["vocabulary"], "column": "a"}
        )
        wrong = client.put(
            "/tables/doble/vocabulary", json={"vocabulary": trained["vocabulary"], "column": "b"}
        )
        missing = client.put("/tables/nada/vocabulary", json={"vocabulary": trained["vocabulary"]})
        broken = client.put("/tables/fotos/vocabulary", json={"vocabulary": "fotos/foto_0.png"})
    assert response.status_code == 200
    assert response.json()["registered_for"] == ["fotos"]
    assert by_column.status_code == 200
    assert by_column.json()["registered_for"] == ["doble.a", "fotos"]
    assert processor._vector_search.vocabularies["doble.a"] == trained["vocabulary"]
    assert (wrong.status_code, missing.status_code, broken.status_code) == (400, 404, 400)
    assert "VECTOR(4)" in wrong.json()["error"]


def test_un_solo_entrenamiento_a_la_vez(api, tmp_path, monkeypatch):
    from engine.multimedia import features

    images(tmp_path / "fotos", count=2)
    original = features.describe
    release = __import__("threading").Event()

    def slow(path, **options):
        release.wait(10)
        return original(path, **options)

    monkeypatch.setattr(features, "describe", slow)
    client, _ = api()
    with client:
        first = client.post("/vocabularies", json={"folder": "fotos", "k": 8})
        second = client.post("/vocabularies", json={"folder": "fotos", "k": 8})
        query = client.post("/query", json={"sql": "SELECT * FROM fotos"})
        release.set()
        done = wait(client, first.json()["job_id"])
    assert second.status_code == 409
    assert query.status_code == 200  # entrenar no bloquea el SQL
    assert done["status"] == "completed"


def test_trabajo_inexistente(api):
    client, _ = api()
    with client:
        response = client.get("/vocabularies/no-existe")
    assert response.status_code == 404
