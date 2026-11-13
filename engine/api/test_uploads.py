"""Subida de archivos multimedia desde el navegador (#288), sin el core compilado."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import engine.api.main as api_main
from engine.api.main import create_app
from engine.api.uploads import safe_relative_name
from engine.executor import QueryProcessor
from engine.executor.test_processor import _FakeDatabase, _NativeModule

DATA = Path(__file__).parents[1] / "multimedia" / "datos"
PNG = (DATA / "ejemplo.png").read_bytes()


@pytest.fixture()
def client(tmp_path):
    database = _FakeDatabase()
    processor = QueryProcessor(database, native_module=_NativeModule)
    app = create_app(
        processor=processor, database=database, native=_NativeModule, media_root=tmp_path
    )
    with TestClient(app) as client:
        yield client


def upload(client, names, *, content=PNG, params=None, **form):
    files = [("files", (name, content, "application/octet-stream")) for name in names]
    return client.post("/media/uploads", files=files, data=form, params=params or {})


def test_sube_una_carpeta_con_subcarpetas_a_una_carpeta_nueva(client, tmp_path):
    response = upload(
        client, ["vacaciones/playa.png", "vacaciones/cerro.jpg", "uno.png"], name="mis fotos"
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["folder"].startswith("subidas/mis-fotos-")
    assert body["saved"] == 3
    assert body["rejected"] == []
    folder = tmp_path / body["folder"]
    assert sorted(f["path"] for f in body["files"]) == [
        f"{body['folder']}/uno.png",
        f"{body['folder']}/vacaciones/cerro.jpg",
        f"{body['folder']}/vacaciones/playa.png",
    ]
    assert (folder / "vacaciones" / "playa.png").read_bytes() == PNG
    assert body["files"][0]["bytes"] == len(PNG)


def test_rechaza_cada_archivo_peligroso_y_guarda_los_demas(client, tmp_path):
    response = upload(
        client,
        ["bien.png", "../fuera.png", "/etc/raiz.png", "C:/windows.png", "notas.txt", "sin_ext",
         "a/../b.png", "bien.png"],
    )  # fmt: skip
    body = response.json()
    assert response.status_code == 200
    assert [f["name"] for f in body["files"]] == ["bien.png"]
    assert [r["name"] for r in body["rejected"]] == [
        "../fuera.png", "/etc/raiz.png", "C:/windows.png", "notas.txt", "sin_ext", "a/../b.png",
        "bien.png",
    ]  # fmt: skip
    motivos = {r["name"]: r["error"] for r in body["rejected"]}
    assert motivos["bien.png"] == "ya hay un archivo con ese nombre en la carpeta"
    assert "'..'" in motivos["../fuera.png"]
    assert "absolutas" in motivos["/etc/raiz.png"]
    assert "absolutas" in motivos["C:/windows.png"]
    assert "'.txt'" in motivos["notas.txt"]
    assert not (tmp_path / "fuera.png").exists()
    assert not list(tmp_path.glob("**/raiz.png"))


def test_un_archivo_demasiado_grande_no_deja_restos(client, tmp_path):
    response = upload(
        client, ["grande.png", "chico.wav"], content=b"x" * 100, params={"max_file_bytes": 50}
    )
    body = response.json()
    assert body["saved"] == 0
    assert all("excede 50 bytes" in r["error"] for r in body["rejected"])
    # Sin nada guardado tampoco queda la carpeta nueva.
    assert not (tmp_path / body["folder"]).exists()


def test_una_carpeta_grande_se_sube_en_tandas(client, tmp_path):
    first = upload(client, ["a.png", "b.png"], name="lote").json()
    second = upload(client, ["c.png", "a.png"], folder=first["folder"]).json()
    assert second["folder"] == first["folder"]
    assert [f["name"] for f in second["files"]] == ["c.png"]
    assert second["rejected"] == [
        {"name": "a.png", "error": "ya hay un archivo con ese nombre en la carpeta"}
    ]
    assert sorted(p.name for p in (tmp_path / first["folder"]).iterdir()) == [
        "a.png", "b.png", "c.png",
    ]  # fmt: skip


@pytest.mark.parametrize("folder", ["fotos", "..", "subidas", "subidas/../fotos", "/tmp"])
def test_solo_se_agrega_a_carpetas_de_subidas(client, tmp_path, folder):
    (tmp_path / "fotos").mkdir()
    (tmp_path / "subidas").mkdir()
    response = upload(client, ["a.png"], folder=folder)
    assert response.status_code == 400
    assert set(response.json()) == {"error", "kind", "line", "column", "end_line", "end_column"}
    assert not (tmp_path / "fotos" / "a.png").exists()


def test_sin_archivos_o_con_demasiados(client, monkeypatch):
    empty = client.post("/media/uploads", data={"name": "x"})
    assert empty.status_code == 400
    assert "files" in empty.json()["error"]

    monkeypatch.setattr(api_main, "MAX_FILES_PER_REQUEST", 2)
    many = upload(client, ["a.png", "b.png", "c.png"])
    assert many.status_code == 400
    assert "hasta 2 archivos por petición" in many.json()["error"]


def test_un_archivo_suelto_devuelve_una_ruta_lista_para_similar_to(client, tmp_path):
    body = upload(client, ["consulta.png"]).json()
    (archivo,) = body["files"]
    assert (tmp_path / archivo["path"]).is_file()
    assert archivo["path"] == f"{body['folder']}/consulta.png"


def test_la_carpeta_subida_sirve_para_entrenar_un_vocabulario(client):
    pytest.importorskip("cv2", reason="se necesita opencv-python-headless")
    from engine.api.test_vocabularies import train

    body = upload(client, ["a.png", "b.png"]).json()
    job = train(client, folder=body["folder"], k=8)
    assert job["status"] == "completed", job
    assert job["described"] == 2


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("playa.png", ("playa.png",)),
        ("viaje\\dia1\\foto.jpg", ("viaje", "dia1", "foto.jpg")),
        ("con espacio.png", ("con espacio.png",)),
    ],
)
def test_nombres_validos(name, expected):
    assert safe_relative_name(name).parts == expected


@pytest.mark.parametrize(
    "name", ["", ".", "a//b.png", "x\x00.png", "a:b.png", "fin.png ", "punto.", "x" * 1025]
)
def test_nombres_invalidos(name):
    with pytest.raises(ValueError):
        safe_relative_name(name)
