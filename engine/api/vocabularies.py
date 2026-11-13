"""Vocabularios multimedia desde la API: entrenarlos y asociarlos a tablas (#287).

Un vocabulario es el codebook de K palabras visuales o de audio y el IDF del
dataset del que salió (``engine/multimedia/features.py``). Hasta ahora solo se
construía desde Python y la API leía las asociaciones tabla -> vocabulario una
sola vez al arrancar, de ``QUIPUDB_MEDIA_VOCABULARIES``.

Aquí hay dos piezas:

- ``VocabularyRegistry``: las asociaciones que se hacen en caliente. Se guardan
  en ``vocabularios.json`` dentro de la raíz multimedia, así sobreviven al
  reinicio, y se aplican al procesador en el momento.
- ``VocabularyJobs``: el entrenamiento como trabajo en segundo plano, con un
  solo trabajo activo por proceso, igual que la carga multimedia.

Los vocabularios nuevos se guardan en ``vocabularios/`` dentro de la raíz.
"""

from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from time import monotonic
from typing import Any
from uuid import uuid4

from engine.api.media_load import iter_media_files
from engine.api.schemas import (
    MediaFileErrorResponse,
    VocabularyInfo,
    VocabularyJobResponse,
    VocabularyTrainRequest,
)
from engine.executor.media_load import MediaLoadError, MediaLoadReport, media_path
from engine.executor.native import from_native_schema
from engine.parser.ast import SqlTypeName

MAX_RETAINED_JOBS = 32
REGISTRY_FILE = "vocabularios.json"
VOCABULARY_DIR = "vocabularios"
SIFT_DIMENSION = 128
_LOGGER = logging.getLogger(__name__)


class VocabularyConflict(MediaLoadError):
    """Ya hay un entrenamiento activo o el servidor se está cerrando."""


class _Cancelled(Exception):
    pass


def kind_of(dimension: int) -> str:
    """SIFT da descriptores de 128 valores; MFCC, de 13."""

    return "imagen" if dimension == SIFT_DIMENSION else "audio"


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


class VocabularyRegistry:
    """Asociaciones ``tabla`` o ``tabla.columna`` -> vocabulario, persistentes.

    Parte de las de ``QUIPUDB_MEDIA_VOCABULARIES``; las registradas por la API
    se guardan aparte y, si repiten una clave, ganan porque son más recientes.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._lock = Lock()
        self._entries: dict[str, str] = {}
        self._saved: dict[str, str] = {}
        path = self.root / REGISTRY_FILE
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in data.items()
            ):
                raise ValueError(f"{REGISTRY_FILE} debe ser un objeto JSON de rutas")
            self._saved = data
            self._entries.update(data)

    def add_initial(self, initial: dict[str, str]) -> dict[str, str]:
        """Suma las de ``QUIPUDB_MEDIA_VOCABULARIES`` y devuelve todas juntas."""

        with self._lock:
            self._entries = {**initial, **self._entries}
            return dict(self._entries)

    def saved(self) -> dict[str, str]:
        with self._lock:
            return dict(self._saved)

    def entries(self) -> dict[str, str]:
        with self._lock:
            return dict(self._entries)

    def register(self, key: str, vocabulary: str, processor: Any) -> None:
        """Guarda la asociación en disco y la aplica al procesador ya abierto."""

        with self._lock:
            saved = {**self._saved, key: vocabulary}
            self.root.mkdir(parents=True, exist_ok=True)
            temporary = self.root / f".{REGISTRY_FILE}.tmp"
            temporary.write_text(json.dumps(saved, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(temporary, self.root / REGISTRY_FILE)
            self._saved = saved
            self._entries[key] = vocabulary
        processor.register_vocabulary(key, vocabulary)


def vector_column(catalog: Any, native: Any, table: str, column: str | None) -> tuple[str, int]:
    """La columna VECTOR de ``table`` que usará el vocabulario y su dimensión.

    Sin ``column``, la tabla tiene que tener exactamente una, igual que
    ``SIMILAR_TO`` sin columna.
    """

    if not catalog.has_table(table):
        raise KeyError(table)
    columns = from_native_schema(catalog.table_info(table).schema, native).columns
    vectors = [c for c in columns if c.data_type is SqlTypeName.VECTOR]
    if column is not None:
        match = [c for c in columns if c.name == column]
        if not match:
            raise MediaLoadError(f"la tabla {table!r} no tiene la columna {column!r}")
        if match[0].data_type is not SqlTypeName.VECTOR:
            raise MediaLoadError(f"la columna {column!r} no es VECTOR")
        return column, int(match[0].length)
    if len(vectors) != 1:
        raise MediaLoadError(
            f"la tabla {table!r} tiene {len(vectors)} columnas VECTOR; indica cuál con column"
        )
    return vectors[0].name, int(vectors[0].length)


def registry_key(table: str, column: str | None) -> str:
    return f"{table}.{column}" if column else table


def unique_vocabulary_path(root: Path, name: str) -> Path:
    """``vocabularios/<name>.npz`` sin pisar uno existente: agrega -2, -3, ..."""

    directory = root / VOCABULARY_DIR
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / f"{name}.npz"
    number = 2
    while candidate.exists():
        candidate = directory / f"{name}-{number}.npz"
        number += 1
    return candidate


def default_name(folder: Path, k: int) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", folder.name).strip("-")[:48] or "vocabulario"
    return f"{base}-k{k}"


def list_vocabularies(root: Path, registry: VocabularyRegistry) -> list[VocabularyInfo]:
    """Los ``.npz`` de ``vocabularios/``, los de la raíz y los asociados a tablas."""

    from engine.multimedia.features import Vocabulary, VocabularyError

    root = root.resolve()
    users: dict[str, list[str]] = {}
    for key, value in registry.entries().items():
        users.setdefault(Path(value).as_posix(), []).append(key)
    candidates = set(users)
    for directory in (root, root / VOCABULARY_DIR):
        if directory.is_dir():
            candidates.update(_relative(root, p) for p in directory.glob("*.npz"))
    result = []
    for relative in sorted(candidates):
        try:
            path = media_path(root, relative, directory=False)
            vocabulary = Vocabulary.load(path)
        except (MediaLoadError, VocabularyError):
            continue  # una asociación a un archivo borrado no rompe el listado
        result.append(
            VocabularyInfo(
                path=relative,
                k=vocabulary.k,
                dimension=vocabulary.dim,
                kind=kind_of(vocabulary.dim),
                documents=vocabulary.documents,
                codebook_id=vocabulary.identifier,
                registered_for=sorted(users.get(relative, [])),
            )
        )
    return result


@dataclass(slots=True)
class _Job:
    job_id: str
    k: int
    started: float
    report: MediaLoadReport = field(default_factory=lambda: MediaLoadReport(""))
    status: str = "pending"
    kind: str | None = None
    discovered: int = 0
    skipped: int = 0
    selected: int = 0
    described: int = 0
    finished: float | None = None
    vocabulary: str | None = None
    codebook_id: str | None = None
    documents: int | None = None
    registered_for: str | None = None
    error: str | None = None


class VocabularyJobs:
    """Entrena de a un vocabulario por vez, fuera del hilo HTTP y del lock SQL.

    Solo toma el lock del motor al final, para asociar el vocabulario a la
    tabla. Describir los archivos y correr k-means no bloquea consultas.
    """

    def __init__(self, motor: Any, root: Path, registry: VocabularyRegistry) -> None:
        self._motor = motor
        self.root = root.resolve()
        self._registry = registry
        self._lock = Lock()
        self._stop = Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="quipudb-vocab")
        self._jobs: dict[str, _Job] = {}
        self._active: str | None = None

    def start(self, request: VocabularyTrainRequest) -> VocabularyJobResponse:
        folder = media_path(self.root, request.folder, directory=True)
        target = None
        if request.table is not None:
            # Antes de describir nada: entrenar diez minutos para una tabla
            # que no existe o de otra dimensión sería trabajo tirado.
            with self._motor.en_uso() as (_, catalog, native):
                column, dimension = vector_column(catalog, native, request.table, request.column)
            if dimension != request.k:
                raise MediaLoadError(
                    f"la columna {column!r} es VECTOR({dimension}) y se pidió k = {request.k}; "
                    "tienen que coincidir"
                )
            target = registry_key(request.table, request.column)
        elif request.column is not None:
            raise MediaLoadError("column solo tiene sentido junto con table")
        with self._lock:
            if self._stop.is_set() or self._active is not None:
                raise VocabularyConflict(
                    "ya hay un entrenamiento de vocabulario activo o el servidor se cierra"
                )
            while len(self._jobs) >= MAX_RETAINED_JOBS:
                self._jobs.pop(next(iter(self._jobs)))
            identifier = uuid4().hex
            job = _Job(identifier, request.k, monotonic())
            self._jobs[identifier] = job
            self._active = identifier
            options = request.model_copy(deep=True)
            self._executor.submit(self._run, job, folder, options, target)
        return self.get(identifier)

    def get(self, identifier: str) -> VocabularyJobResponse:
        with self._lock:
            job = self._jobs.get(identifier)
            if job is None:
                raise KeyError(identifier)
            return VocabularyJobResponse(
                job_id=identifier,
                status=job.status,
                kind=job.kind,
                discovered=job.discovered,
                skipped=job.skipped,
                selected=job.selected,
                described=job.described,
                failed=job.report.failed,
                errors=[
                    MediaFileErrorResponse(path=e.path, error=e.error) for e in job.report.errors
                ],
                errors_truncated=job.report.errors_truncated,
                elapsed_ms=((job.finished or monotonic()) - job.started) * 1000,
                k=job.k,
                vocabulary=job.vocabulary,
                codebook_id=job.codebook_id,
                documents=job.documents,
                registered_for=job.registered_for,
                error=job.error,
            )

    def close(self) -> None:
        with self._lock:
            self._stop.set()
        self._executor.shutdown(wait=True)

    def _check_stop(self) -> None:
        if self._stop.is_set():
            raise _Cancelled

    def _run(
        self, job: _Job, folder: Path, options: VocabularyTrainRequest, target: str | None
    ) -> None:
        terminal_status = "completed"
        fatal_error = None
        try:
            from engine.multimedia.features import (
                EXTENSIONES_DE_AUDIO,
                EXTENSIONES_DE_IMAGEN,
                build_vocabulary,
                describe,
            )

            with self._lock:
                job.status = "running"
            images, audios = [], []
            for path in iter_media_files(folder, options.recursive):
                self._check_stop()
                suffix = path.suffix.lower()
                with self._lock:
                    job.discovered += 1
                    if suffix in EXTENSIONES_DE_IMAGEN:
                        images.append(path)
                    elif suffix in EXTENSIONES_DE_AUDIO:
                        audios.append(path)
                    else:
                        job.skipped += 1
            if images and audios:
                raise MediaLoadError(
                    f"la carpeta mezcla {len(images)} imágenes y {len(audios)} audios; "
                    "un vocabulario es de una sola modalidad"
                )
            files = sorted(images or audios)
            if not files:
                raise MediaLoadError("la carpeta no tiene imágenes ni audios reconocidos")
            kind = "imagen" if images else "audio"
            # Con más archivos que max_files se toman espaciados, siempre los
            # mismos: el resultado no depende de la suerte ni del orden del disco.
            if len(files) > options.max_files:
                step = len(files) / options.max_files
                files = [files[int(i * step)] for i in range(options.max_files)]
            with self._lock:
                job.kind = kind
                job.selected = len(files)
            extraction = (
                {"max_keypoints": options.max_keypoints}
                if kind == "imagen"
                else {"max_seconds": options.max_audio_seconds}
            )
            descriptors = []
            for path in files:
                self._check_stop()
                try:
                    path = media_path(self.root, str(path), directory=False)
                    if path.stat().st_size > options.max_file_bytes:
                        raise MediaLoadError("el archivo excede max_file_bytes")
                    descriptors.append(describe(path, **extraction))
                    with self._lock:
                        job.described += 1
                except Exception as error:  # noqa: BLE001 - un archivo roto no frena el resto
                    with self._lock:
                        job.report.fail(
                            _relative(self.root, path)
                            if path.is_relative_to(self.root)
                            else str(path),
                            str(error) or type(error).__name__,
                        )
            self._check_stop()
            if not descriptors:
                raise MediaLoadError("no se pudo describir ningún archivo")
            vocabulary = build_vocabulary(
                descriptors,
                options.k,
                seed=options.seed,
                max_descriptors=max(options.max_descriptors, options.k),
            )
            self._check_stop()
            destination = unique_vocabulary_path(
                self.root, options.name or default_name(folder, options.k)
            )
            vocabulary.save(destination)
            relative = _relative(self.root, destination)
            with self._lock:
                job.vocabulary = relative
                job.codebook_id = vocabulary.identifier
                job.documents = vocabulary.documents
            if target is not None:
                with self._motor.en_uso() as (processor, _, _native):
                    self._registry.register(target, relative, processor)
                with self._lock:
                    job.registered_for = target
        except _Cancelled:
            terminal_status = "cancelled"
        except Exception as error:
            _LOGGER.exception("fallo del entrenamiento de vocabulario %s", job.job_id)
            terminal_status = "failed"
            fatal_error = (str(error) or type(error).__name__)[:1024]
        finally:
            with self._lock:
                job.status = terminal_status
                job.error = fatal_error
                job.finished = monotonic()
                self._active = None


__all__ = [
    "VocabularyConflict",
    "VocabularyJobs",
    "VocabularyRegistry",
    "list_vocabularies",
    "registry_key",
    "vector_column",
]
