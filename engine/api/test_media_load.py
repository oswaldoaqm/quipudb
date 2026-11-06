"""REST y concurrencia de la carga multimedia, sin necesitar el core compilado."""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from threading import Event

import numpy as np
import pytest
from fastapi.testclient import TestClient

from engine.api.main import create_app
from engine.api.media_load import MediaJobs, media_path
from engine.api.schemas import MediaLoadRequest
from engine.executor import QueryProcessor
from engine.executor.media_load import MediaLoadError, MediaLoadReport
from engine.executor.test_processor import _FakeDatabase, _NativeModule
from engine.multimedia import features
from engine.multimedia.features import Vocabulary
from engine.multimedia.kmeans import Codebook, huella_de


def save_vocabulary(path, dimension=2):
    centers = np.zeros((dimension, 128), dtype=np.float32)
    centers[-1] = 1
    vocabulary = Vocabulary(Codebook(centers, 0, huella_de(centers)), np.ones(dimension), 2)
    vocabulary.save(path)
    return vocabulary


@pytest.fixture()
def environment(tmp_path, monkeypatch):
    folder = tmp_path / "objetos"
    folder.mkdir()
    save_vocabulary(tmp_path / "vocabulario.npz")
    database = _FakeDatabase()
    processor = QueryProcessor(database, native_module=_NativeModule)
    processor.execute(
        "CREATE TABLE objetos (id INT PRIMARY KEY, nombre VARCHAR(64), "
        "ruta VARCHAR(1024), rasgos VECTOR(2))"
    )
    monkeypatch.setattr(features, "extract_features", lambda *a, **kw: np.array([0.6, 0.8]))
    app = create_app(processor=processor, database=database, native=_NativeModule, media_root=tmp_path)
    with TestClient(app) as client:
        yield client, processor, folder


def start(client, **options):
    return client.post(
        "/tables/objetos/load-media",
        json={"folder": "objetos", "vocabulary": "vocabulario.npz", **options},
    )


def wait(client, job_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/tables/objetos/load-media/{job_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"completed", "failed", "cancelled"}:
            return body
        time.sleep(0.005)
    pytest.fail("el trabajo no termino")


def test_carga_carpeta_por_http_y_expone_progreso(environment):
    client, processor, folder = environment
    for name in ("ana.png", "luis.PNG", "foto'con_comilla.jpg"):
        (folder / name).write_bytes(b"imagen")
    response = start(client, batch_size=2, start_id=10)
    assert response.status_code == 202
    report = wait(client, response.json()["job_id"])
    assert report["status"] == "completed"
    assert (report["discovered"], report["processed"], report["inserted"]) == (3, 3, 3)
    assert report["failed"] == 0
    assert report["next_id"] == 13
    assert report["dimension"] == 2
    assert report["codebook_id"]
    assert report["elapsed_ms"] >= 0
    records = processor._database.tables["objetos"].records
    assert {r[0] for r in records} == {10, 11, 12}
    assert all(Path(r[2]).parent == folder for r in records)


def test_un_archivo_defectuoso_no_detiene_los_buenos(environment, monkeypatch):
    client, _, folder = environment
    for name in ("bien.png", "roto.png", "otro.png"):
        (folder / name).write_bytes(b"imagen")

    def extract(path, *_args, **_kwargs):
        if path.name == "roto.png":
            raise ValueError("imagen defectuosa")
        return np.zeros(2)

    monkeypatch.setattr(features, "extract_features", extract)
    report = wait(client, start(client).json()["job_id"])
    assert (report["inserted"], report["failed"]) == (2, 1)
    assert Path(report["errors"][0]["path"]).name == "roto.png"
    assert report["errors"][0]["error"] == "imagen defectuosa"


def test_extractores_no_bloquean_sql_progreso_ni_otras_peticiones(environment, monkeypatch):
    client, _, folder = environment
    (folder / "lenta.png").write_bytes(b"imagen")
    entered, release = Event(), Event()

    def slow(*_args, **_kwargs):
        entered.set()
        assert release.wait(5)
        return np.ones(2)

    monkeypatch.setattr(features, "extract_features", slow)
    try:
        response = start(client)
        assert response.status_code == 202
        assert entered.wait(2)
        identifier = response.json()["job_id"]
        assert client.get(f"/tables/objetos/load-media/{identifier}").json()["status"] == "running"
        query = client.post("/query", json={"sql": "SELECT * FROM objetos"})
        assert query.status_code == 200
        assert query.json()["rows"] == []
        assert start(client).status_code == 409
    finally:
        release.set()
    assert wait(client, identifier)["inserted"] == 1


def _enlazar_o_saltar(enlace, destino, *, carpeta=False):
    """Crea un enlace simbolico, o salta la prueba si el sistema no deja.

    Windows exige privilegio de administrador o el modo desarrollador para
    crearlos, asi que esta comprobacion fallaba por el entorno en las
    maquinas del equipo. El CI corre en Linux y la sigue ejecutando.
    """

    try:
        enlace.symlink_to(destino, target_is_directory=carpeta)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"el sistema no deja crear enlaces simbolicos: {error}")


def test_las_rutas_fuera_de_la_raiz_se_rechazan(environment):
    # No necesita enlaces: corre en cualquier sistema.
    client, _, _ = environment
    assert start(client, folder="../").status_code == 400
    assert start(client, vocabulary="../fuera.npz").status_code == 400


def test_los_enlaces_simbolicos_se_rechazan(environment, tmp_path):
    client, _, folder = environment
    _enlazar_o_saltar(tmp_path / "enlace", folder, carpeta=True)
    assert start(client, folder="enlace").status_code == 400
    _enlazar_o_saltar(folder / "enlazada.png", tmp_path / "vocabulario.npz")
    report = wait(client, start(client).json()["job_id"])
    assert report["failed"] == 1
    assert "simbolicos" in report["errors"][0]["error"]


def test_no_confirma_ni_aborta_una_transaccion_abierta_entre_lotes(environment, monkeypatch):
    client, processor, folder = environment
    (folder / "foto.png").write_bytes(b"imagen")
    entered, release = Event(), Event()

    def slow(*_args, **_kwargs):
        entered.set()
        assert release.wait(5)
        return np.ones(2)

    monkeypatch.setattr(features, "extract_features", slow)
    response = start(client)
    try:
        assert entered.wait(2)
        assert client.post("/query", json={"sql": "BEGIN TRANSACTION"}).status_code == 200
    finally:
        release.set()
    report = wait(client, response.json()["job_id"])
    assert report["status"] == "failed"
    assert "transaccion activa" in report["error"]
    assert client.post("/query", json={"sql": "END TRANSACTION"}).status_code == 200
    assert not processor._database.tables["objetos"].records


def test_dimension_y_vocabulario_invalidos_fallan_sin_insertar(environment, tmp_path):
    client, processor, folder = environment
    (folder / "foto.png").write_bytes(b"imagen")
    save_vocabulary(tmp_path / "otro.npz", dimension=3)
    report = wait(client, start(client, vocabulary="otro.npz").json()["job_id"])
    assert report["status"] == "failed"
    assert "VECTOR(2)" in report["error"]
    (tmp_path / "roto.npz").write_bytes(b"no soy un vocabulario")
    report = wait(client, start(client, vocabulary="roto.npz").json()["job_id"])
    assert report["status"] == "failed"
    assert report["inserted"] == 0
    assert not processor._database.tables["objetos"].records


def test_carpeta_vacia_y_recorrido_recursivo(environment):
    client, _, folder = environment
    report = wait(client, start(client).json()["job_id"])
    assert report["status"] == "completed"
    assert report["processed"] == 0
    nested = folder / "subcarpeta"
    nested.mkdir()
    (nested / "foto.png").write_bytes(b"imagen")
    assert wait(client, start(client).json()["job_id"])["inserted"] == 0
    assert wait(client, start(client, recursive=True).json()["job_id"])["inserted"] == 1


def test_limite_de_archivo_y_de_errores_no_acumulan_respuesta(environment):
    client, _, folder = environment
    for i in range(120):
        (folder / f"{i}.png").write_bytes(b"demasiado grande")
    report = wait(client, start(client, max_file_bytes=1).json()["job_id"])
    assert report["failed"] == 120
    assert len(report["errors"]) == 100
    assert report["errors_truncated"]


def test_transaccion_tabla_y_trabajo_inexistentes(environment):
    client, processor, _ = environment
    processor.execute("BEGIN TRANSACTION")
    assert start(client).status_code == 400
    processor.execute("END TRANSACTION")
    body = {"folder": "objetos", "vocabulary": "vocabulario.npz"}
    assert client.post("/tables/fantasma/load-media", json=body).status_code == 404
    assert client.get("/tables/objetos/load-media/desconocido").status_code == 404
    identifier = start(client).json()["job_id"]
    assert client.get(f"/tables/otra/load-media/{identifier}").status_code == 404


@pytest.mark.parametrize("options", [{"batch_size": 0}, {"batch_size": 257}, {"start_id": True}])
def test_opciones_invalidas_conservan_el_formato_de_error(environment, options):
    client, _, _ = environment
    response = start(client, **options)
    assert response.status_code == 422
    assert response.json()["error"].startswith("peticion invalida:")


def test_no_materializa_el_dataset_y_los_lotes_son_acotados(tmp_path, monkeypatch):
    import engine.api.media_load as media

    folder = tmp_path / "objetos"
    folder.mkdir()
    picture = folder / "foto.png"
    picture.write_bytes(b"imagen")
    save_vocabulary(tmp_path / "vocabulario.npz")
    extracted = 0
    sizes = []

    def paths(*_args):
        for i in range(3000):
            assert i == extracted, "el recorrido adelanto archivos sin extraerlos"
            yield picture

    def extract(*_args, **_kwargs):
        nonlocal extracted
        extracted += 1
        return np.ones(2)

    class Sink:
        def load_media_batch(self, table, rows, **_kwargs):
            sizes.append(len(rows))
            return MediaLoadReport(table, inserted=len(rows))

        @contextmanager
        def en_uso(self):
            yield self, None, None

    monkeypatch.setattr(media, "iter_media_files", paths)
    monkeypatch.setattr(features, "extract_features", extract)
    manager = MediaJobs(Sink(), tmp_path)
    try:
        options = MediaLoadRequest(folder="objetos", vocabulary="vocabulario.npz", batch_size=7)
        started = manager.start("objetos", options)
        # 30 s y no 10: el lote de 3 000 objetos tarda 8 o 9 segundos en las
        # maquinas del equipo, asi que con 10 la prueba fallaba cada vez que
        # algo mas cargaba el equipo. El plazo sigue detectando un cuelgue,
        # que es lo que comprueba; no afloja lo que mide.
        deadline = time.monotonic() + 30
        while (report := manager.get("objetos", started.job_id)).status in {"pending", "running"}:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        assert report.inserted == 3000
        assert max(sizes) <= 7
    finally:
        manager.close()


def test_ruta_debe_ser_del_tipo_esperado(tmp_path):
    with pytest.raises(MediaLoadError):
        media_path(tmp_path, "no_existe", directory=True)
    with pytest.raises(MediaLoadError):
        media_path(tmp_path, ".", directory=False)
