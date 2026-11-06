"""Seleccion y descarga del corpus de arXiv (#240), sin red."""

from pathlib import Path

import httpx
import pytest

from app.corpus.arxiv import (
    API_URL,
    ArxivClient,
    ArxivError,
    descargar,
    escribir_lista,
    leer_lista,
    leer_metadatos,
    parse_feed,
    seleccionar,
)


def _entrada(arxiv_id: str, titulo: str, anio: int = 2016) -> str:
    return f"""
  <entry>
    <id>http://arxiv.org/abs/{arxiv_id}</id>
    <published>{anio}-03-30T11:51:39Z</published>
    <title>{titulo}
      con salto</title>
    <summary>  Un resumen
      en dos lineas. </summary>
    <author><name>Yu. A. Malkov</name></author>
    <author><name>D. A. Yashunin</name></author>
    <arxiv:primary_category term="cs.DS" scheme="http://arxiv.org/schemas/atom"/>
    <arxiv:license>http://creativecommons.org/licenses/by/4.0/</arxiv:license>
  </entry>"""


def _feed(*entradas: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
        + "".join(entradas)
        + "</feed>"
    )


class _Reloj:
    """Tiempo falso: dormir lo adelanta y queda registrado."""

    def __init__(self) -> None:
        self.ahora = 0.0
        self.siestas: list[float] = []

    def sleep(self, segundos: float) -> None:
        self.siestas.append(segundos)
        self.ahora += segundos

    def clock(self) -> float:
        return self.ahora


def _cliente(handler, reloj: _Reloj | None = None, **opciones) -> ArxivClient:
    reloj = reloj or _Reloj()
    return ArxivClient(
        transport=httpx.MockTransport(handler), sleep=reloj.sleep, clock=reloj.clock, **opciones
    )


def test_parse_feed_normaliza_espacios_y_lee_todos_los_campos() -> None:
    [paper] = parse_feed(_feed(_entrada("1603.09320v4", "Efficient HNSW")))

    assert paper.arxiv_id == "1603.09320v4"
    assert paper.id_base == "1603.09320"
    assert paper.titulo == "Efficient HNSW con salto"
    assert paper.autores == ("Yu. A. Malkov", "D. A. Yashunin")
    assert paper.anio == 2016
    assert paper.categoria == "cs.DS"
    assert paper.abstract == "Un resumen en dos lineas."
    assert paper.licencia == "http://creativecommons.org/licenses/by/4.0/"
    assert paper.url_pdf == "https://arxiv.org/pdf/1603.09320v4"


def test_los_ids_viejos_con_barra_tienen_un_nombre_de_archivo_valido() -> None:
    [paper] = parse_feed(_feed(_entrada("cs/0101001v1", "Viejo")))

    assert paper.id_base == "cs/0101001"
    assert paper.archivo == "cs_0101001v1.pdf"


def test_una_entrada_de_error_de_la_api_no_es_un_paper() -> None:
    error = "<entry><id>http://arxiv.org/api/errors#incorrect_id</id><title>Error</title></entry>"

    assert parse_feed(_feed(error)) == []


def test_el_cliente_espacia_las_peticiones() -> None:
    reloj = _Reloj()
    cliente = _cliente(lambda _: httpx.Response(200, text=_feed()), reloj, intervalo_s=3.0)

    cliente.get(API_URL)
    cliente.get(API_URL)
    cliente.get(API_URL)

    assert reloj.siestas == [3.0, 3.0]


def test_ante_429_espera_lo_que_pide_retry_after_y_reintenta() -> None:
    respuestas = iter(
        [
            httpx.Response(429, headers={"Retry-After": "20"}),
            httpx.Response(503),
            httpx.Response(200, text="ok"),
        ]
    )
    reloj = _Reloj()
    cliente = _cliente(lambda _: next(respuestas), reloj, intervalo_s=3.0)

    assert cliente.get(API_URL).text == "ok"
    # 20 s por el Retry-After, despues el doble; el intervalo ya quedo cubierto.
    assert reloj.siestas == [20.0, 40.0]


def test_los_cortes_de_red_tambien_se_reintentan() -> None:
    llamadas = []

    def handler(peticion: httpx.Request) -> httpx.Response:
        llamadas.append(peticion)
        if len(llamadas) == 1:
            raise httpx.ReadTimeout("lento", request=peticion)
        return httpx.Response(200, text="ok")

    assert _cliente(handler).get(API_URL).text == "ok"
    assert len(llamadas) == 2


def test_se_rinde_despues_de_los_reintentos() -> None:
    cliente = _cliente(lambda _: httpx.Response(503), reintentos=3)

    with pytest.raises(ArxivError, match="fallo 3 veces; la ultima: HTTP 503"):
        cliente.get(API_URL)


def test_un_404_no_se_reintenta() -> None:
    llamadas = []

    def handler(peticion: httpx.Request) -> httpx.Response:
        llamadas.append(peticion)
        return httpx.Response(404)

    with pytest.raises(ArxivError, match="404"):
        _cliente(handler).get(API_URL)
    assert len(llamadas) == 1


def test_seleccionar_pone_las_semillas_primero_y_no_repite_papers() -> None:
    def handler(peticion: httpx.Request) -> httpx.Response:
        params = peticion.url.params
        if "id_list" in params:
            assert params["id_list"] == "1603.09320"
            return httpx.Response(200, text=_feed(_entrada("1603.09320v4", "HNSW")))
        if params["search_query"] == "q1":
            return httpx.Response(
                200, text=_feed(_entrada("2401.00001v1", "A"), _entrada("1603.09320v3", "HNSW"))
            )
        return httpx.Response(200, text=_feed(_entrada("2401.00001v2", "A"), _entrada("2402.1v1", "B")))

    papers = seleccionar(_cliente(handler), ["q1", "q2"], 10, ["1603.09320"])

    assert [p.arxiv_id for p in papers] == ["1603.09320v4", "2401.00001v1", "2402.1v1"]


def test_la_lista_va_y_vuelve(tmp_path: Path) -> None:
    papers = parse_feed(_feed(_entrada("1603.09320v4", "HNSW"), _entrada("cs/0101001v1", "Viejo")))
    ruta = tmp_path / "corpus" / "papers.tsv"

    escribir_lista(papers, ruta)

    assert leer_lista(ruta) == ["1603.09320v4", "cs/0101001v1"]
    assert ruta.read_text(encoding="utf-8").startswith("# arxiv_id\tanio")


def _servidor_de_pdfs(pdfs_pedidos: list[str], rotos: set[str] = frozenset()):
    def handler(peticion: httpx.Request) -> httpx.Response:
        if peticion.url.path == "/api/query":
            ids = peticion.url.params["id_list"].split(",")
            # 9999.99999v1 no existe: arXiv no lo devuelve.
            return httpx.Response(
                200, text=_feed(*(_entrada(i, f"T {i}") for i in ids if not i.startswith("9999")))
            )
        arxiv_id = peticion.url.path.removeprefix("/pdf/")
        pdfs_pedidos.append(arxiv_id)
        if arxiv_id in rotos:
            return httpx.Response(200, content=b"<html>captcha</html>")
        return httpx.Response(200, content=b"%PDF-1.5\n" + b"x" * 2048)

    return handler


def test_descargar_trae_metadatos_y_pdfs_y_no_repite(tmp_path: Path) -> None:
    pedidos: list[str] = []
    cliente = _cliente(_servidor_de_pdfs(pedidos))
    ids = ["1603.09320v4", "2401.00001v1"]

    primera = descargar(cliente, ids, tmp_path)
    segunda = descargar(cliente, ids, tmp_path)

    assert (primera.nuevos, primera.existentes, primera.fallidos) == (2, 0, ())
    assert (segunda.nuevos, segunda.existentes) == (0, 2)
    assert pedidos == ids
    assert (tmp_path / "pdf" / "1603.09320v4.pdf").read_bytes().startswith(b"%PDF-")
    assert [p.arxiv_id for p in leer_metadatos(tmp_path)] == ids
    assert not list((tmp_path / "pdf").glob("*.part"))


def test_un_pdf_roto_o_un_id_inexistente_no_detienen_la_descarga(tmp_path: Path) -> None:
    pedidos: list[str] = []
    cliente = _cliente(_servidor_de_pdfs(pedidos, rotos={"2401.00001v1"}))

    resultado = descargar(cliente, ["2401.00001v1", "9999.99999v1", "1603.09320v4"], tmp_path)

    assert resultado.nuevos == 1
    assert dict(resultado.fallidos) == {
        "9999.99999v1": "arXiv no devolvio sus metadatos",
        "2401.00001v1": "la respuesta no es un PDF",
    }
    assert not (tmp_path / "pdf" / "2401.00001v1.pdf").exists()
