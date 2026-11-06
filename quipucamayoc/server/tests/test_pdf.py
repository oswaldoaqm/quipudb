"""Extraccion y limpieza del texto de un PDF (#241)."""

from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from app.corpus.pdf import PdfError, extraer, limpiar
from tests.pdfs import escribir

_CABECERA = "IEEE TRANSACTIONS ON KNOWLEDGE AND DATA ENGINEERING, VOL. {n}"


_TEMAS = ["graph indexes", "product quantization", "inverted files", "learned indexes"]
_CUERPO = [
    "The {tema} section measures latency and memory.",
    "Every experiment on {tema} runs ten times.",
    "Hierarchical navigable small world graphs out-",
    "perform inverted file indexes on recall.",
    "We report the median of the {tema} runs.",
    "Results on {tema} appear in the next table.",
]


def _paper(paginas: int = 4) -> list[list[str]]:
    # Como un paper de verdad: la cabecera y el numero se repiten en cada
    # pagina, el cuerpo no.
    contenido = []
    for n in range(1, paginas + 1):
        tema = _TEMAS[(n - 1) % len(_TEMAS)]
        contenido.append(
            [
                _CABECERA.format(n=n),
                f"Section {n} studies {tema} for approximate nearest neighbor search.",
                *(linea.format(tema=tema) for linea in _CUERPO),
                str(n),
            ]
        )
    return contenido


def test_extrae_el_texto_pagina_por_pagina(tmp_path: Path) -> None:
    documento = extraer(escribir(tmp_path / "paper.pdf", _paper()))

    assert len(documento.paginas) == 4
    assert documento.paginas[1].startswith("Section 2 studies product quantization")
    assert documento.palabras > 0


def test_une_las_palabras_cortadas_con_guion_al_final_de_linea(tmp_path: Path) -> None:
    documento = extraer(escribir(tmp_path / "paper.pdf", _paper()))

    assert "graphs outperform inverted" in documento.paginas[0]
    assert "out-" not in documento.paginas[0]


def test_quita_cabeceras_repetidas_y_numeros_de_pagina(tmp_path: Path) -> None:
    documento = extraer(escribir(tmp_path / "paper.pdf", _paper()))

    texto = " ".join(documento.paginas)
    assert "IEEE TRANSACTIONS" not in texto
    assert not documento.paginas[2].endswith(" 3")


def test_un_guion_de_verdad_se_conserva() -> None:
    # Solo se une cuando la linea siguiente sigue en minuscula.
    documento = limpiar(["k-NN graphs and LSH-\nBased methods"])

    assert documento.paginas == ("k-NN graphs and LSH-\nBased methods".replace("\n", " "),)


def test_normaliza_ligaduras_y_quita_caracteres_de_control() -> None:
    documento = limpiar(["eﬃcient ﬁltering\x00 of vectors\x07"])

    assert documento.paginas == ("efficient filtering of vectors",)


def test_los_nombres_de_glifo_de_ligaduras_vuelven_a_ser_letras() -> None:
    documento = limpiar(["elements are shi/f_ted to the right; a/f_ter the /f_f_irst Microso/f_t run"])

    assert documento.paginas == ("elements are shifted to the right; after the ffirst Microsoft run",)


def test_una_ruta_con_guion_bajo_no_es_una_ligadura() -> None:
    assert limpiar(["see /usr_bin/tool and a_b"]).paginas == ("see /usr_bin/tool and a_b",)


def test_quita_el_sello_lateral_de_arxiv() -> None:
    paginas = ["arXiv:1603.09320v4 [cs.DS] 14 Aug 2018\nWe present a new approach."]

    assert limpiar(paginas).paginas == ("We present a new approach.",)


def test_corta_desde_las_referencias_y_conserva_lo_anterior() -> None:
    paginas = [
        "Introduction. References to prior work are in Section 5.",
        "Body text about HNSW.",
        "Conclusion text.\nReferences\n[1] Malkov, Y. Efficient search.",
        "[2] Johnson, J. Billion-scale similarity search.",
    ]

    con = limpiar(paginas).paginas
    sin = limpiar(paginas, quitar_referencias=False).paginas

    assert con == (paginas[0], paginas[1], "Conclusion text.", "")
    assert "Malkov" in sin[2]


def test_las_referencias_numeradas_tambien_se_reconocen() -> None:
    paginas = ["Intro.", "Body.", "Fin.\n7 REFERENCES\n[1] A.", "[2] B."]

    assert limpiar(paginas).paginas[2:] == ("Fin.", "")


def test_un_archivo_que_no_es_pdf_da_un_error_claro(tmp_path: Path) -> None:
    ruta = tmp_path / "notas.pdf"
    ruta.write_text("esto no es un pdf", encoding="utf-8")

    with pytest.raises(PdfError, match="notas.pdf: no es un PDF legible"):
        extraer(ruta)


def test_un_pdf_sin_texto_da_un_error_claro(tmp_path: Path) -> None:
    with pytest.raises(PdfError, match="no tiene texto"):
        extraer(escribir(tmp_path / "escaneo.pdf", [[], []]))


def test_un_pdf_protegido_con_clave_da_un_error_claro(tmp_path: Path) -> None:
    original = escribir(tmp_path / "abierto.pdf", _paper(1))
    escritor = PdfWriter(clone_from=PdfReader(original))
    escritor.encrypt(user_password="secreto", algorithm="RC4-128")
    protegido = tmp_path / "protegido.pdf"
    with protegido.open("wb") as salida:
        escritor.write(salida)

    with pytest.raises(PdfError, match="protegido con clave"):
        extraer(protegido)


def test_un_pdf_con_solo_permisos_se_lee(tmp_path: Path) -> None:
    # Clave de usuario vacia: cualquiera lo abre, solo restringe imprimir o copiar.
    original = escribir(tmp_path / "abierto.pdf", _paper(1))
    escritor = PdfWriter(clone_from=PdfReader(original))
    escritor.encrypt(user_password="", owner_password="duenio", algorithm="RC4-128")
    ruta = tmp_path / "permisos.pdf"
    with ruta.open("wb") as salida:
        escritor.write(salida)

    assert "Section 1 studies graph indexes" in extraer(ruta).paginas[0]
