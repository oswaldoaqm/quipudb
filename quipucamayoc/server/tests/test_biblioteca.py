"""La biblioteca: listar, filtrar y agregar un PDF (#250)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.llm_falso import LLMFalso
from tests.pdfs import pdf


def _ops(paso: dict) -> list[str]:
    return [paso["op"]] + [op for hijo in paso["children"] for op in _ops(hijo)]


def _cliente(corpus, tmp_path: Path, llm: LLMFalso | None = None) -> TestClient:
    motor, tablas = corpus
    app = create_app(
        Settings(gemini_api_key="clave"),
        motor=motor,
        llm=llm or LLMFalso(),
        tablas=tablas,
        carpeta_subidos=tmp_path / "subidos",
    )
    return TestClient(app)


def _subir(cliente: TestClient, contenido: bytes, **campos):
    datos = {"titulo": "Learned Bloom filters", "autores": "Ada Lovelace, Alan Turing", "anio": "2024"}
    datos.update(campos)
    return cliente.post(
        "/papers", data=datos, files={"archivo": ("paper.pdf", contenido, "application/pdf")}
    )


_PDF_NUEVO = pdf(
    [
        [
            "Learned Bloom filters replace hash functions with a classifier.",
            "A backup filter keeps the false negative rate at zero.",
            "The classifier is trained on keys and non keys of the set.",
        ]
    ]
)


def test_lista_los_papers_con_sus_chunks_y_el_sql(corpus, tmp_path: Path) -> None:
    cuerpo = _cliente(corpus, tmp_path).get("/papers").json()

    assert sorted(p["arxiv_id"] for p in cuerpo["papers"]) == [
        "2401.00001v1",
        "2401.00002v1",
        "2401.00003v1",
    ]
    assert all(p["chunks"] >= 1 for p in cuerpo["papers"])
    propositos = [s["proposito"] for s in cuerpo["sql"]]
    assert propositos == ["todos los papers", "chunks por paper (GROUP BY)"]
    assert "GROUP BY paper_id" in cuerpo["sql"][1]["sql"]


def test_el_rango_de_anios_va_al_b_mas_y_el_autor_se_filtra_aparte(corpus, tmp_path: Path) -> None:
    cliente = _cliente(corpus, tmp_path)
    assert _subir(cliente, _PDF_NUEVO).status_code == 201

    por_anio = cliente.get("/papers", params={"anio_desde": 2023, "anio_hasta": 2025}).json()
    por_autor = cliente.get("/papers", params={"autor": "turing"}).json()
    nadie = cliente.get("/papers", params={"autor": "Grace Hopper"}).json()

    assert [p["titulo"] for p in por_anio["papers"]] == ["Learned Bloom filters"]
    assert "WHERE anio BETWEEN 2023 AND 2025" in por_anio["sql"][0]["sql"]
    assert "index_range" in _ops(por_anio["sql"][0]["plan"]["root"])
    assert [p["autores"] for p in por_autor["papers"]] == ["Ada Lovelace, Alan Turing"]
    assert nadie["papers"] == []


def test_un_pdf_subido_queda_listo_para_preguntar(corpus, tmp_path: Path) -> None:
    llm = LLMFalso(reescritura="learned bloom filter backup", respuesta="Usan un filtro de respaldo [1].")
    cliente = _cliente(corpus, tmp_path, llm)

    subido = _subir(cliente, _PDF_NUEVO)

    assert subido.status_code == 201
    cuerpo = subido.json()
    assert cuerpo["arxiv_id"].startswith("local-")
    assert cuerpo["chunks"] == 1
    assert cuerpo["tiempos"]["indice_s"] > 0
    assert (tmp_path / "subidos" / f"{cuerpo['arxiv_id']}.pdf").is_file()

    respuesta = cliente.post("/ask", json={"pregunta": "¿Cómo evitan falsos negativos?"}).json()
    primera = respuesta["fuentes"][0]["paper"]
    assert (primera["arxiv_id"], primera["url"]) == (cuerpo["arxiv_id"], "")


def test_un_archivo_que_no_es_pdf_no_deja_nada(corpus, tmp_path: Path) -> None:
    cliente = _cliente(corpus, tmp_path)

    respuesta = _subir(cliente, b"hola, no soy un pdf")

    assert respuesta.status_code == 400
    assert respuesta.json() == {"error": "el archivo no es un PDF"}
    assert len(cliente.get("/papers").json()["papers"]) == 3


def test_un_pdf_roto_no_deja_filas_a_medias(corpus, tmp_path: Path) -> None:
    cliente = _cliente(corpus, tmp_path)

    respuesta = _subir(cliente, b"%PDF-1.4\nesto no tiene objetos ni xref")

    assert respuesta.status_code == 400
    assert "PDF" in respuesta.json()["error"]
    assert len(cliente.get("/papers").json()["papers"]) == 3


def test_faltan_campos_es_422(corpus, tmp_path: Path) -> None:
    cliente = _cliente(corpus, tmp_path)

    assert _subir(cliente, _PDF_NUEVO, anio="hace mucho").status_code == 422
    assert _subir(cliente, _PDF_NUEVO, titulo="").status_code == 422
