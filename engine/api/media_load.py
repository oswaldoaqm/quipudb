"""Trabajos acotados de ingesta multimedia, fuera del hilo HTTP y del lock SQL.

Solo un trabajo activo por proceso, sin cola ilimitada. No se reconstruyen
codebooks durante la carga: hay que reutilizar el vocabulario del dataset.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from time import monotonic
from typing import Any
from uuid import uuid4

from engine.api.schemas import MediaJobResponse, MediaLoadRequest
from engine.executor.bulk_load import INT32_MAX
from engine.executor.media_load import MediaColumns, MediaLoadError, MediaLoadReport, MediaRow

MAX_RETAINED_JOBS = 32


class MediaLoadConflict(MediaLoadError):
    """Ya hay una carga activa o el servidor esta cerrandose."""


class _Cancelled(Exception):
    pass


def media_path(root: Path, value: str, *, directory: bool) -> Path:
    """Rechaza escapes y symlinks, tambien los que apuntan dentro de la raiz."""

    supplied = Path(value)
    candidate = Path(os.path.abspath(supplied if supplied.is_absolute() else root / supplied))
    if not candidate.is_relative_to(root):
        raise MediaLoadError("la ruta debe estar dentro de QUIPUDB_MEDIA_ROOT")
    current = root
    for part in candidate.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise MediaLoadError("no se admiten enlaces simbolicos en las rutas multimedia")
    try:
        resolved = candidate.resolve(strict=True)
        valid = resolved.is_dir() if directory else resolved.is_file()
    except OSError as error:
        raise MediaLoadError(f"no se puede abrir la ruta: {error}") from None
    if not valid or not resolved.is_relative_to(root):
        raise MediaLoadError("se esperaba una carpeta" if directory else "se esperaba un archivo")
    return resolved


def iter_media_files(folder: Path, recursive: bool) -> Iterator[Path]:
    """No construye una lista del dataset ni sigue directorios enlazados."""

    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.is_symlink():
                yield Path(entry.path)  # Se reporta como fallo, nunca se abre.
            elif entry.is_dir(follow_symlinks=False):
                if recursive:
                    yield from iter_media_files(Path(entry.path), recursive)
            elif entry.is_file(follow_symlinks=False):
                yield Path(entry.path)


@dataclass(slots=True)
class _Job:
    job_id: str
    table: str
    report: MediaLoadReport
    started: float
    next_id: int
    status: str = "pending"
    discovered: int = 0
    finished: float | None = None
    codebook_id: str | None = None
    dimension: int | None = None
    error: str | None = None


class MediaJobs:
    """Extrae de uno en uno y serializa solo cada lote de escritura con SQL."""

    def __init__(self, motor: Any, root: Path) -> None:
        self._motor = motor
        self.root = root.resolve()
        self._lock = Lock()
        self._stop = Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="quipudb-media")
        self._jobs: dict[str, _Job] = {}
        self._active: str | None = None

    def start(self, table: str, request: MediaLoadRequest) -> MediaJobResponse:
        folder = media_path(self.root, request.folder, directory=True)
        vocabulary = media_path(self.root, request.vocabulary, directory=False)
        if vocabulary.stat().st_size > 64 * 1024**2:
            raise MediaLoadError("el archivo de vocabulario excede 64 MiB")
        columns = MediaColumns(**request.columns.model_dump())
        with self._motor.en_uso() as (processor, _, _native):
            processor.load_media_batch(table, (), columns=columns)
        with self._lock:
            if self._stop.is_set() or self._active is not None:
                raise MediaLoadConflict("ya hay una carga multimedia activa o el servidor se cierra")
            while len(self._jobs) >= MAX_RETAINED_JOBS:
                self._jobs.pop(next(iter(self._jobs)))
            identifier = uuid4().hex
            job = _Job(identifier, table, MediaLoadReport(table), monotonic(), request.start_id)
            self._jobs[identifier] = job
            self._active = identifier
            # Copia independiente: el solicitante no puede alterar un trabajo activo.
            options = request.model_copy(deep=True)
            self._executor.submit(self._run, job, folder, vocabulary, columns, options)
        return self.get(table, identifier)

    def get(self, table: str, identifier: str) -> MediaJobResponse:
        with self._lock:
            job = self._jobs.get(identifier)
            if job is None or job.table != table:
                raise KeyError(identifier)
            report = job.report
            return MediaJobResponse(
                job_id=identifier,
                table=table,
                status=job.status,
                discovered=job.discovered,
                processed=report.inserted + report.failed,
                inserted=report.inserted,
                failed=report.failed,
                next_id=job.next_id,
                errors=[{"path": e.path, "error": e.error} for e in report.errors],
                errors_truncated=report.errors_truncated,
                elapsed_ms=((job.finished or monotonic()) - job.started) * 1000,
                codebook_id=job.codebook_id,
                dimension=job.dimension,
                error=job.error,
            )

    def close(self) -> None:
        with self._lock:
            self._stop.set()
        self._executor.shutdown(wait=True)

    def _run(
        self,
        job: _Job,
        folder: Path,
        vocabulary_path: Path,
        columns: MediaColumns,
        options: MediaLoadRequest,
    ) -> None:
        terminal_status = "completed"
        fatal_error = None
        try:
            self._check_stop()
            # No arrastra numpy/OpenCV/soundfile al importar la API o usar CSV.
            from engine.multimedia.features import (
                EXTENSIONES_DE_IMAGEN,
                Vocabulary,
                extract_features,
            )

            with self._lock:
                job.status = "running"
            vocabulary = Vocabulary.load(vocabulary_path)
            with self._motor.en_uso() as (processor, _, _native):
                processor.load_media_batch(job.table, (), columns=columns, dimension=vocabulary.k)
            with self._lock:
                job.codebook_id = vocabulary.identifier
                job.dimension = vocabulary.k
            batch: list[MediaRow] = []
            for path in iter_media_files(folder, options.recursive):
                self._check_stop()
                with self._lock:
                    identifier = job.next_id
                    if identifier > INT32_MAX:
                        raise MediaLoadError("se agotaron los identificadores INT de 32 bits")
                    job.next_id += 1
                    job.discovered += 1
                try:
                    path = media_path(self.root, str(path), directory=False)
                    if path.stat().st_size > options.max_file_bytes:
                        raise MediaLoadError("el archivo excede max_file_bytes")
                    extraction_options = (
                        {"max_keypoints": options.max_keypoints}
                        if path.suffix.lower() in EXTENSIONES_DE_IMAGEN
                        else {"max_seconds": options.max_audio_seconds}
                    )
                    vector = extract_features(path, vocabulary, **extraction_options)
                    batch.append(
                        MediaRow(identifier, path.name, str(path), tuple(float(v) for v in vector))
                    )
                except Exception as error:
                    with self._lock:
                        job.report.fail(str(path), str(error) or type(error).__name__)
                if len(batch) >= options.batch_size:
                    self._write(job, batch, columns, vocabulary.k)
                    batch.clear()
            if batch:
                self._write(job, batch, columns, vocabulary.k)
        except _Cancelled:
            terminal_status = "cancelled"
        except Exception as error:
            terminal_status = "failed"
            fatal_error = (str(error) or type(error).__name__)[:1024]
        finally:
            with self._lock:
                job.status = terminal_status
                job.error = fatal_error
                job.finished = monotonic()
                self._active = None

    def _check_stop(self) -> None:
        if self._stop.is_set():
            raise _Cancelled

    def _write(
        self, job: _Job, batch: list[MediaRow], columns: MediaColumns, dimension: int
    ) -> None:
        self._check_stop()
        with self._motor.en_uso() as (processor, _, _native):
            report = processor.load_media_batch(
                job.table, batch, columns=columns, dimension=dimension
            )
        with self._lock:
            job.report.merge(report)
