"""Division en chunks (#242)."""

import itertools
import re

import pytest

from app.corpus.chunks import MAX_BYTES, MAXIMO, MINIMO, SOLAPAMIENTO, dividir


def _oraciones(cantidad: int, palabras_por_oracion: int = 12, prefijo: str = "w") -> str:
    """Oraciones numeradas: cada palabra es unica y se puede rastrear."""

    oraciones = []
    contador = 0
    for _ in range(cantidad):
        palabras = []
        for _ in range(palabras_por_oracion):
            palabras.append(f"{prefijo}{contador}")
            contador += 1
        oraciones.append("A" + " ".join(palabras) + ".")
    return " ".join(oraciones)


def test_un_documento_vacio_no_tiene_chunks() -> None:
    assert dividir(["", "   "]) == []


def test_un_paper_corto_es_un_solo_chunk_aunque_tenga_menos_del_minimo() -> None:
    [chunk] = dividir(["Only a short abstract about HNSW graphs."])

    assert chunk.orden == 0
    assert chunk.pagina == 1
    assert chunk.texto == "Only a short abstract about HNSW graphs."


def test_los_chunks_respetan_el_rango_salvo_el_ultimo() -> None:
    chunks = dividir([_oraciones(200)])  # 2 400 palabras

    assert len(chunks) > 5
    for chunk in chunks[:-1]:
        assert MINIMO <= chunk.palabras <= MAXIMO
    assert chunks[-1].palabras <= MAXIMO
    assert [c.orden for c in chunks] == list(range(len(chunks)))


def test_cortan_en_fin_de_oracion() -> None:
    for chunk in dividir([_oraciones(200)])[:-1]:
        assert chunk.texto.endswith(".")


def test_chunks_consecutivos_comparten_el_solapamiento_y_cubren_todo() -> None:
    texto = _oraciones(200)
    chunks = dividir([texto])

    for anterior, siguiente in itertools.pairwise(chunks):
        cola = anterior.texto.split()[-SOLAPAMIENTO:]
        assert siguiente.texto.split()[:SOLAPAMIENTO] == cola
    vistas = {palabra for chunk in chunks for palabra in chunk.texto.split()}
    assert vistas == set(texto.split())


def test_una_cola_corta_se_suma_al_ultimo_chunk() -> None:
    # 420 palabras: partir en 300 dejaria una cola de 170 (< 200).
    [chunk] = dividir([_oraciones(35)])

    assert chunk.palabras == 420


def test_sin_fines_de_oracion_corta_en_el_objetivo() -> None:
    texto = " ".join(f"token{i}" for i in range(1000))

    chunks = dividir([texto])

    assert chunks[0].palabras == 300


def test_et_al_no_corta_la_oracion() -> None:
    oracion = "Malkov et al. Showed that graphs scale."
    # Mismo texto repetido: si "al." contara como fin, habria cortes ahi.
    texto = " ".join([oracion] * 80)

    for chunk in dividir([texto])[:-1]:
        assert not chunk.texto.endswith("al.")


def test_cada_chunk_dice_en_que_pagina_empieza() -> None:
    paginas = [_oraciones(25, prefijo="p1x"), _oraciones(25, prefijo="p2x"), _oraciones(25, prefijo="p3x")]

    chunks = dividir(paginas)

    # Cada palabra lleva su pagina en el prefijo: p2x17 es de la pagina 2.
    for chunk in chunks:
        primera = chunk.texto.split()[0]
        assert chunk.pagina == int(re.search(r"p(\d)x", primera).group(1))
    assert [c.pagina for c in chunks] == sorted(c.pagina for c in chunks)
    assert chunks[0].pagina == 1
    assert chunks[-1].pagina >= 2


def test_nunca_pasa_del_tope_de_bytes_con_texto_multibyte() -> None:
    # "ñúñúñúñúñú." ocupa 21 bytes: 300 palabras asi son 6 600. El tope de
    # bytes gana aunque el chunk quede por debajo del minimo de palabras.
    texto = " ".join(["ñúñúñúñúñú."] * 2000)

    chunks = dividir([texto])

    for chunk in chunks:
        assert len(chunk.texto.encode("utf-8")) <= MAX_BYTES
        assert set(chunk.texto.split()) == {"ñúñúñúñúñú."}
    assert max(len(c.texto.encode("utf-8")) for c in chunks) > MAX_BYTES - 20


def test_una_palabra_gigante_se_recorta_sin_partir_un_caracter() -> None:
    gigante = "é" * 3000  # 6 000 bytes sin un solo espacio

    [chunk] = dividir([gigante])

    datos = chunk.texto.encode("utf-8")
    assert len(datos) <= MAX_BYTES
    assert chunk.texto == "é" * (MAX_BYTES // 2)


def test_conserva_tildes_y_simbolos() -> None:
    [chunk] = dividir(["La métrica ℓ₂ y el índice ∑ funcionan; ¿sí?"])

    assert chunk.texto == "La métrica ℓ₂ y el índice ∑ funcionan; ¿sí?"


def test_es_determinista() -> None:
    texto = _oraciones(150, palabras_por_oracion=17)

    assert dividir([texto]) == dividir([texto])


@pytest.mark.parametrize(
    "opciones",
    [{"minimo": 0}, {"minimo": 400}, {"solapamiento": 250}, {"objetivo": 600}],
)
def test_parametros_incoherentes_son_error(opciones) -> None:
    with pytest.raises(ValueError):
        dividir(["texto"], **opciones)
