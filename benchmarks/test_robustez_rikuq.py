"""Bateria de robustez de Rikuq: que mide cada degradacion y como se cuenta.

Las imagenes se fabrican y no se cargan modelos: lo que se comprueba es el
**metodo** -- que cada degradacion haga lo que dice su nombre, que girar no
pierda esquinas y, sobre todo, que los porcentajes salgan sobre el total de
identidades y no sobre las que se llegaron a detectar.
"""

import numpy as np
import pytest

from benchmarks.scripts.robustez_rikuq import (
    SEMILLA,
    banda,
    brillo,
    casos,
    contraluz,
    escala,
    girar_lienzo,
    gris,
    jpeg,
    parche,
    tabla_markdown,
    tinte,
)


def _foto(alto: int = 60, ancho: int = 40) -> np.ndarray:
    """Un degradado con algo de estructura, no un rectangulo plano."""

    base = np.linspace(20, 230, alto * ancho, dtype=np.float32).reshape(alto, ancho)
    return np.repeat(base[:, :, None], 3, axis=2).astype(np.uint8)


def _fila(identidades: int, detectadas: int, reconocidas: int) -> dict[str, str]:
    return {
        "grupo": "giro",
        "caso": "90 grados",
        "identidades": str(identidades),
        "detectadas": str(detectadas),
        "reconocidas": str(reconocidas),
        "distancia_media": "0.4",
        "distancia_p90": "0.8",
    }


# --- como se cuenta -----------------------------------------------------------


def test_el_porcentaje_sale_sobre_las_identidades_y_no_sobre_las_detectadas() -> None:
    """Es la diferencia entre medir y maquillar.

    Un caso que solo detecta 10 de 40 caras pero acierta en 9 de esas 10 no
    reconoce al 90%: reconoce al 22%. Las 30 que ni se detectaron tambien son
    rostros perdidos, y dividir entre las detectadas las esconderia.
    """

    tabla = tabla_markdown([_fila(identidades=40, detectadas=10, reconocidas=9)])

    assert "| 22% |" in tabla
    assert "| 90% |" not in tabla


def test_la_deteccion_se_reporta_aparte_del_reconocimiento() -> None:
    """No detectar y detectar mal no cuestan lo mismo, y se ven por separado."""

    tabla = tabla_markdown([_fila(identidades=40, detectadas=28, reconocidas=14)])

    assert "| 70% |" in tabla  # detecto 28 de 40
    assert "| 35% |" in tabla  # reconocio 14 de 40


def test_la_tabla_trae_una_fila_por_caso_mas_la_cabecera() -> None:
    filas = [_fila(40, 40, 40), _fila(40, 20, 10)]

    assert len(tabla_markdown(filas).splitlines()) == len(filas) + 2


# --- que hace cada degradacion ------------------------------------------------


@pytest.mark.parametrize("grados", [45, 90, 135, 180, 225, 270, 315])
def test_girar_no_recorta_ninguna_esquina(grados) -> None:
    """Al girar en diagonal las esquinas se salen del encuadre original.

    Si se girara sobre el mismo lienzo se perderia parte de la imagen, y ahi
    puede estar la cara. Se comprueba contando pixeles no negros: el lienzo
    crece, pero el contenido tiene que seguir entero.
    """

    foto = _foto()
    antes = int((foto[:, :, 0] > 0).sum())

    girada = girar_lienzo(foto, grados)
    despues = int((girada[:, :, 0] > 0).sum())

    assert girada.shape[0] == girada.shape[1], "el lienzo tiene que ser cuadrado"
    assert despues >= antes * 0.98


def test_girar_90_grados_intercambia_los_lados() -> None:
    girada = girar_lienzo(_foto(alto=60, ancho=40), 90)

    assert girada.shape[0] >= 60 and girada.shape[1] >= 60


def test_la_banda_tapa_la_franja_que_se_le_pide() -> None:
    """Las gafas de sol y la mascarilla son esto: una franja a cierta altura."""

    tapada = banda(_foto(alto=100, ancho=40), 0.30, 0.48)

    assert tapada[0:29].any(), "arriba de la franja no se toca"
    assert not tapada[30:48].any(), "la franja queda en negro"
    assert tapada[49:].any(), "debajo de la franja no se toca"


def test_el_parche_tapa_area_pero_deja_el_resto() -> None:
    tapada = parche(_foto(alto=80, ancho=80), 0.25)

    negros = int((tapada[:, :, 0] == 0).sum())
    assert negros >= 20 * 20 * 0.9
    assert tapada.any(), "un parche del 25% no puede dejar la imagen en negro"


def test_el_brillo_satura_sin_desbordar() -> None:
    """Multiplicar sin recortar daria la vuelta al byte y oscureceria la cara."""

    claro = brillo(_foto(), 4.0)

    assert claro.max() == 255
    assert claro.dtype == np.uint8


def test_el_contraluz_deja_un_lado_mas_oscuro_que_el_otro() -> None:
    """Imita una ventana lateral, que es lo que pasa en un salon de verdad."""

    plana = np.full((40, 40, 3), 200, dtype=np.uint8)
    iluminada = contraluz(plana)

    izquierda = iluminada[:, :10].mean()
    derecha = iluminada[:, -10:].mean()
    assert izquierda < derecha


def test_el_blanco_y_negro_deja_los_tres_canales_iguales() -> None:
    convertida = gris(_foto())

    assert np.array_equal(convertida[:, :, 0], convertida[:, :, 1])
    assert np.array_equal(convertida[:, :, 1], convertida[:, :, 2])


def test_el_tinte_mueve_un_canal_y_no_los_otros() -> None:
    plana = np.full((20, 20, 3), 100, dtype=np.uint8)
    tenida = tinte(plana, 2, 1.5)

    assert tenida[:, :, 2].mean() > tenida[:, :, 0].mean()
    assert tenida[:, :, 0].mean() == pytest.approx(100)


def test_bajar_la_resolucion_conserva_el_tamano_pero_pierde_detalle() -> None:
    """La cara lejos tiene menos pixeles reales, no una imagen mas chica."""

    foto = _foto()
    reducida = escala(foto, 0.2)

    assert reducida.shape == foto.shape
    assert len(np.unique(reducida)) < len(np.unique(foto))


def test_comprimir_mas_aleja_mas_del_original() -> None:
    foto = _foto()
    suave = jpeg(foto, 90).astype(np.int16)
    bruta = jpeg(foto, 4).astype(np.int16)
    original = foto.astype(np.int16)

    assert np.abs(bruta - original).mean() > np.abs(suave - original).mean()


# --- que casos entran ---------------------------------------------------------


def test_cada_caso_tiene_nombre_propio_dentro_de_su_grupo() -> None:
    """Dos casos con el mismo nombre se pisarian en la tabla del informe."""

    nombres = [(grupo, nombre) for grupo, nombre, _ in casos()]

    assert len(nombres) == len(set(nombres))


def test_la_bateria_cubre_las_siete_familias_de_degradacion() -> None:
    """Cada familia es una forma distinta de que una foto salga mal de verdad."""

    assert {grupo for grupo, _, _ in casos()} == {
        "giro",
        "iluminacion",
        "nitidez",
        "compresion",
        "resolucion",
        "oclusion",
        "color",
    }


def test_el_giro_llega_mas_alla_de_donde_el_pipeline_se_rompe() -> None:
    """La bateria vieja llegaba a 30 grados y el fallo empieza sobre los 55.

    Si los casos no pasaran de ahi, la tabla diria que todo va bien.
    """

    angulos = [nombre for grupo, nombre, _ in casos() if grupo == "giro"]

    assert "60 grados" in angulos
    assert "90 grados" in angulos
    assert "180 grados" in angulos


def test_toda_degradacion_devuelve_una_imagen_usable() -> None:
    foto = _foto()

    for grupo, nombre, degradar in casos():
        salida = degradar(foto)
        assert salida.ndim == 3, f"{grupo}/{nombre} no devolvio una imagen en color"
        assert salida.dtype == np.uint8, f"{grupo}/{nombre} cambio el tipo de pixel"
        assert salida.size > 0, f"{grupo}/{nombre} devolvio una imagen vacia"


def test_la_semilla_esta_fijada_para_poder_comparar_corridas() -> None:
    """Con semilla libre, dos corridas eligen identidades distintas y la
    diferencia entre ellas no significaria nada."""

    assert isinstance(SEMILLA, int)
