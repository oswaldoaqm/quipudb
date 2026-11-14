"""El dataset de rostros: descarga, manifiesto y verificacion (#301), sin red."""

import hashlib
from pathlib import Path

from app.dataset.lfw import Rostro, sha256


def _imagen(carpeta: Path, nombre: str, contenido: bytes = b"\xff\xd8\xff falsa") -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    archivo = carpeta / nombre
    archivo.write_bytes(contenido)
    return archivo


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
