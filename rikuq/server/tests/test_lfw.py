"""El dataset de rostros: descarga, manifiesto y verificacion (#301), sin red."""

import hashlib
from pathlib import Path

import pytest

from app.dataset.lfw import (
    DatasetError,
    Rostro,
    construir,
    escribir,
    leer,
    recolectar,
    sha256,
)


def _imagen(carpeta: Path, nombre: str, contenido: bytes = b"\xff\xd8\xff falsa") -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    archivo = carpeta / nombre
    archivo.write_bytes(contenido)
    return archivo


def _poblar(datos: Path, personas: dict[str, int], origen: str = "lfw") -> None:
    """Crea ``datos/<origen>/<persona>/<persona>_000N.jpg`` con N fotos."""

    for persona, cuantas in personas.items():
        for i in range(1, cuantas + 1):
            _imagen(datos / origen / persona, f"{persona}_{i:04d}.jpg", f"{persona}{i}".encode())


def test_sha256_es_el_del_archivo(tmp_path: Path) -> None:
    archivo = _imagen(tmp_path, "x.jpg", b"unos bytes")

    assert sha256(archivo) == hashlib.sha256(b"unos bytes").hexdigest()


def test_sha256_de_un_archivo_grande_no_lo_carga_entero(tmp_path: Path) -> None:
    """Se lee por trozos: LFW son 173 MB y el manifiesto los recorre todos."""

    contenido = b"x" * (3 * (1 << 20) + 7)
    archivo = _imagen(tmp_path, "grande.jpg", contenido)

    assert sha256(archivo) == hashlib.sha256(contenido).hexdigest()


def test_el_rostro_resuelve_su_ruta_contra_la_carpeta_de_datos(tmp_path: Path) -> None:
    rostro = Rostro("Ana_Lopez", "lfw/Ana_Lopez/Ana_Lopez_0001.jpg", "abc", "lfw")

    assert rostro.archivo(tmp_path) == tmp_path / "lfw" / "Ana_Lopez" / "Ana_Lopez_0001.jpg"


def test_recolectar_toma_el_nombre_de_la_carpeta_como_persona(tmp_path: Path) -> None:
    _poblar(tmp_path, {"Ana_Lopez": 2})

    rostros = list(recolectar(tmp_path / "lfw", "lfw"))

    assert [r.ruta for r in rostros] == [
        "lfw/Ana_Lopez/Ana_Lopez_0001.jpg",
        "lfw/Ana_Lopez/Ana_Lopez_0002.jpg",
    ]
    assert {r.persona for r in rostros} == {"Ana_Lopez"}
    assert all(r.origen == "lfw" for r in rostros)


def test_recolectar_ignora_lo_que_no_es_imagen(tmp_path: Path) -> None:
    _poblar(tmp_path, {"Ana_Lopez": 1})
    (tmp_path / "lfw" / "Ana_Lopez" / "notas.txt").write_text("no es una foto")
    (tmp_path / "lfw" / "suelta.jpg").write_bytes(b"fuera de una carpeta de persona")

    rostros = list(recolectar(tmp_path / "lfw", "lfw"))

    assert [r.ruta for r in rostros] == ["lfw/Ana_Lopez/Ana_Lopez_0001.jpg"]


def test_recolectar_calcula_el_sha_de_cada_imagen(tmp_path: Path) -> None:
    _poblar(tmp_path, {"Ana_Lopez": 2})

    rostros = list(recolectar(tmp_path / "lfw", "lfw"))

    assert [r.sha256 for r in rostros] == [sha256(r.archivo(tmp_path)) for r in rostros]
    assert rostros[0].sha256 != rostros[1].sha256


def test_recolectar_una_carpeta_que_no_existe_no_es_error(tmp_path: Path) -> None:
    """El equipo puede no haber puesto sus fotos todavia."""

    assert list(recolectar(tmp_path / "equipo", "equipo")) == []


def test_construir_descarta_las_identidades_con_pocas_fotos(tmp_path: Path) -> None:
    """Con una sola foto no queda ninguna para preguntar que no se haya cargado."""

    _poblar(tmp_path, {"Con_Muchas": 3, "Con_Una": 1, "Con_Dos": 2})

    rostros = construir(tmp_path, minimo=2)

    assert {r.persona for r in rostros} == {"Con_Muchas", "Con_Dos"}
    assert len(rostros) == 5


def test_construir_siempre_incluye_al_equipo(tmp_path: Path) -> None:
    """Son pocas fotos y son las que la demostracion tiene que reconocer."""

    _poblar(tmp_path, {"Con_Muchas": 10})
    _poblar(tmp_path, {"Danna": 1}, origen="equipo")

    rostros = construir(tmp_path, minimo=10)

    assert ("equipo", "Danna") in {(r.origen, r.persona) for r in rostros}


def test_construir_sin_imagenes_explica_que_hacer(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match="descargar"):
        construir(tmp_path, minimo=1)


def test_construir_rechaza_un_minimo_invalido(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match="al menos 1"):
        construir(tmp_path, minimo=0)


def test_escribir_y_leer_dan_lo_mismo(tmp_path: Path) -> None:
    _poblar(tmp_path, {"Ana_Lopez": 2})
    _poblar(tmp_path, {"Danna": 1}, origen="equipo")
    originales = construir(tmp_path, minimo=1)
    salida = tmp_path / "dataset" / "rostros.tsv"

    filas = escribir(originales, salida)

    assert filas == 3
    assert leer(salida) == originales


def test_el_manifiesto_es_un_tsv_con_cabecera(tmp_path: Path) -> None:
    _poblar(tmp_path, {"Ana_Lopez": 1})
    salida = tmp_path / "rostros.tsv"

    escribir(construir(tmp_path, minimo=1), salida)

    lineas = salida.read_text(encoding="utf-8").splitlines()
    assert lineas[0] == "persona\truta\tsha256\torigen"
    assert lineas[1].split("\t")[:2] == ["Ana_Lopez", "lfw/Ana_Lopez/Ana_Lopez_0001.jpg"]


def test_leer_un_manifiesto_que_no_existe_explica_que_hacer(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match="manifiesto"):
        leer(tmp_path / "no-esta.tsv")


def test_leer_rechaza_una_cabecera_distinta(tmp_path: Path) -> None:
    ruta = tmp_path / "rostros.tsv"
    ruta.write_text("persona\truta\n", encoding="utf-8")

    with pytest.raises(DatasetError, match="cabecera"):
        leer(ruta)


def test_leer_rechaza_una_fila_incompleta(tmp_path: Path) -> None:
    """Saltarla en silencio volveria inutil la garantia del manifiesto."""

    ruta = tmp_path / "rostros.tsv"
    ruta.write_text("persona\truta\tsha256\torigen\nAna\tlfw/Ana/a.jpg\n", encoding="utf-8")

    with pytest.raises(DatasetError, match=r":2 "):
        leer(ruta)
