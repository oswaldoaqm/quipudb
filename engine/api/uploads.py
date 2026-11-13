"""Archivos multimedia subidos desde el navegador (#288).

``load-media``, ``POST /vocabularies`` y ``SIMILAR_TO`` reciben rutas que tienen
que existir dentro de ``QUIPUDB_MEDIA_ROOT``. Cuando la API corre en Docker, o
en otra máquina, el servidor no ve el disco del usuario; por eso el frontend
sube los archivos y recibe la ruta del servidor donde quedaron.

Cada subida crea una carpeta nueva ``subidas/<nombre>-<id>``. Una carpeta grande
se puede mandar en varias peticiones indicando la carpeta que devolvió la
primera: solo se puede agregar a carpetas de ``subidas/``, nunca a otra parte
de la raíz, así una subida no mezcla archivos con un dataset ya cargado.

Los archivos se revisan uno por uno. Un nombre peligroso, una extensión que el
pipeline no lee, un archivo demasiado grande o un nombre repetido se rechazan
con su motivo y los demás se guardan, igual que la carga CSV por omisión.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import uuid4

from engine.executor.media_load import MediaLoadError, media_path

UPLOAD_DIR = "subidas"
MAX_FILES_PER_REQUEST = 500
"""Por petición; una carpeta más grande se sube en varias tandas."""

MAX_NAME_LENGTH = 1024
_CHUNK = 1024 * 1024
_FORBIDDEN = re.compile(r'[\x00-\x1f<>:"|?*]')


class UploadError(MediaLoadError):
    """La petición entera no es válida: carpeta de destino, límites o formato."""


@dataclass
class UploadReport:
    folder: str
    saved: list[tuple[str, str, int]] = field(default_factory=list)
    """``(nombre recibido, ruta relativa a la raíz, bytes)``."""

    rejected: list[tuple[str, str]] = field(default_factory=list)


def media_extensions() -> frozenset[str]:
    from engine.multimedia.features import EXTENSIONES_DE_AUDIO, EXTENSIONES_DE_IMAGEN

    return EXTENSIONES_DE_IMAGEN | EXTENSIONES_DE_AUDIO


def safe_relative_name(name: str) -> PurePosixPath:
    """El nombre recibido como ruta relativa segura, o ``ValueError`` con el motivo.

    Acepta subcarpetas (``vacaciones/playa.png``), que es lo que manda el
    navegador al subir una carpeta, pero no rutas absolutas, ``..``, ``.``,
    letras de unidad de Windows ni caracteres de control.
    """

    if not name or len(name) > MAX_NAME_LENGTH:
        raise ValueError("nombre vacío o demasiado largo")
    text = name.replace("\\", "/")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise ValueError("no se admiten rutas absolutas")
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("el nombre no puede tener '..', '.' ni partes vacías")
    if any(_FORBIDDEN.search(part) or part.endswith((" ", ".")) for part in parts):
        raise ValueError("el nombre tiene caracteres no válidos")
    return PurePosixPath(*parts)


def new_upload_folder(root: Path, name: str | None) -> Path:
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", name or "").strip("-")[:48] or "subida"
    folder = root / UPLOAD_DIR / f"{base}-{uuid4().hex[:8]}"
    folder.mkdir(parents=True)
    return folder


def existing_upload_folder(root: Path, value: str) -> Path:
    """Una carpeta ya creada por una subida anterior, y ninguna otra."""

    folder = media_path(root, value, directory=True)
    if folder.parent != (root / UPLOAD_DIR).resolve():
        raise UploadError(f"solo se puede agregar archivos a una carpeta de {UPLOAD_DIR}/")
    return folder


def save_files(
    root: Path,
    folder: Path,
    files: list[tuple[str, BinaryIO]],
    *,
    max_file_bytes: int,
) -> UploadReport:
    """Guarda cada archivo en ``folder`` sin cargar ninguno entero en memoria."""

    root = root.resolve()
    extensions = media_extensions()
    report = UploadReport(folder.relative_to(root).as_posix())
    for name, stream in files:
        try:
            relative = safe_relative_name(name)
            if relative.suffix.lower() not in extensions:
                raise ValueError(
                    f"la extensión {relative.suffix or '(ninguna)'!r} no es una imagen ni un audio"
                )
            destination = folder.joinpath(*relative.parts)
            if destination.exists():
                raise ValueError("ya hay un archivo con ese nombre en la carpeta")
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise ValueError(f"no se pudo crear la subcarpeta: {error}") from None
            written = _copy_limited(stream, destination, max_file_bytes)
        except ValueError as error:
            report.rejected.append((name, str(error)))
            continue
        report.saved.append((name, destination.relative_to(root).as_posix(), written))
    return report


def _copy_limited(stream: BinaryIO, destination: Path, limit: int) -> int:
    written = 0
    try:
        with destination.open("xb") as output:
            while chunk := stream.read(_CHUNK):
                written += len(chunk)
                if written > limit:
                    raise ValueError(f"el archivo excede {limit} bytes")
                output.write(chunk)
    except ValueError:
        destination.unlink(missing_ok=True)
        raise
    except OSError as error:
        destination.unlink(missing_ok=True)
        raise ValueError(f"no se pudo guardar: {error}") from None
    return written


def remove_if_empty(folder: Path) -> None:
    """Una subida nueva en la que no se guardó nada no deja una carpeta vacía."""

    if folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()


__all__ = [
    "MAX_FILES_PER_REQUEST",
    "UPLOAD_DIR",
    "UploadError",
    "UploadReport",
    "existing_upload_folder",
    "new_upload_folder",
    "remove_if_empty",
    "safe_relative_name",
    "save_files",
]
