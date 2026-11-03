"""Puente multimedia/indices; importar el parser no requiere NumPy ni OpenCV."""

from __future__ import annotations

import math
import random
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from engine.executor.instrumentation import measure_native
from engine.executor.media_load import media_path
from engine.parser.ast import VectorMetric
from engine.parser.bound_ast import BoundSimilarCondition
from engine.parser.errors import SQLSemanticError
from engine.planner.explain import vector_search_step
from engine.planner.optimizer import PhysicalTableAccess
from engine.planner.plan import Op, Step


def native_vector_metric(metric: VectorMetric, native: Any) -> Any:
    name = "DOT_PRODUCT" if metric is VectorMetric.DOT else metric.name
    return getattr(native.VectorMetric, name)


def execute_vector_candidates(
    database: Any,
    native: Any,
    access: PhysicalTableAccess,
    condition: BoundSimilarCondition,
    scores: list[float] | None = None,
) -> tuple[list[Any], Step]:
    if condition.vector is None:
        raise ValueError("SIMILAR_TO necesita ExtractFeatures antes de buscar")
    table = database.table(access.table.name)
    metric = native_vector_metric(condition.metric, native)
    k = min(condition.k, int(table.size()))
    if access.index is None:
        owner = table

        def search():
            return native.scan_k_nearest_vector(
                table,
                condition.column.column.name,
                condition.vector,
                k,
                metric,
            )
    else:
        index = database.index(access.table.name, access.index.name)
        parameter = (
            condition.nprobe if access.index.structure.value == "ivf" else condition.ef_search
        )
        owner = index

        def search():
            return index.k_nearest(condition.vector, k, metric, parameter)

    def run():
        matches = search()
        if scores is not None:
            scores.extend(float(native.from_distance(match.distance, metric)) for match in matches)
        return matches

    nearest = measure_native(owner, run)
    step = vector_search_step(access, condition, stats=nearest.stats, time_ms=nearest.time_ms)

    def fetch() -> list[Any]:
        rows = []
        for match in nearest.value:
            row = table.read(match.rid)
            if row is None:
                raise RuntimeError("la busqueda vectorial devolvio un RID inexistente")
            rows.append(row)
        return rows

    fetched = measure_native(table, fetch)
    return fetched.value, Step(
        op=Op.FETCH,
        structure=access.table.structure,
        table=access.table.name,
        detail="lee por RID conservando el orden de similitud",
        children=[step],
        stats=fetched.stats,
        time_ms=fetched.time_ms,
    )


class VectorSearch:
    """Configuracion del servidor, nunca vocabularios entrenados por consulta."""

    def __init__(
        self,
        root: Path,
        vocabularies: Mapping[str, str | Path],
        nprobe: int,
        ef_search: int,
        nlist: int,
    ) -> None:
        for name, value in (("nprobe", nprobe), ("ef_search", ef_search), ("nlist", nlist)):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65536:
                raise ValueError(f"{name} debe ser un entero entre 1 y 65536")
        self.root = root.resolve()
        self.vocabularies = dict(vocabularies)
        self.nprobe = nprobe
        self.ef_search = ef_search
        self.nlist = nlist

    def paths(self, table: str, where: BoundSimilarCondition, source: str) -> tuple[Path, Path]:
        """Valida rutas tambien en EXPLAIN, sin ejecutar ExtractFeatures."""

        try:
            query = media_path(self.root, where.query, directory=False)
            vocabulary = self.vocabularies.get(
                f"{table}.{where.column.column.name}", self.vocabularies.get(table)
            )
            if vocabulary is None:
                raise ValueError(
                    f"falta configurar el vocabulario de {table!r} en media_vocabularies "
                    "o QUIPUDB_MEDIA_VOCABULARIES"
                )
            vocabulary_path = media_path(self.root, str(vocabulary), directory=False)
            if query.stat().st_size > 64 * 1024**2 or vocabulary_path.stat().st_size > 64 * 1024**2:
                raise ValueError("el archivo o vocabulario excede 64 MiB")
            return query, vocabulary_path
        except (OSError, ValueError) as error:
            raise SQLSemanticError(str(error), where.query_span, source) from None

    def extract(
        self, table: str, where: BoundSimilarCondition, source: str
    ) -> BoundSimilarCondition:
        query, vocabulary_path = self.paths(table, where, source)
        try:
            from engine.multimedia.features import (
                EXTENSIONES_DE_IMAGEN,
                Vocabulary,
                extract_features,
            )

            vocabulary = Vocabulary.load(vocabulary_path)
            if vocabulary.k != where.column.column.length:
                raise ValueError(
                    f"el vocabulario tiene {vocabulary.k} palabras y la columna es "
                    f"VECTOR({where.column.column.length})"
                )
            options = (
                {"max_keypoints": 500}
                if query.suffix.lower() in EXTENSIONES_DE_IMAGEN
                else {"max_seconds": 30.0}
            )
            vector = tuple(float(value) for value in extract_features(query, vocabulary, **options))
            if len(vector) != vocabulary.k or any(
                not math.isfinite(value) or abs(value) > 3.4028234663852886e38 for value in vector
            ):
                raise ValueError("ExtractFeatures devolvio un vector invalido")
        except (OSError, ValueError, ImportError) as error:
            raise SQLSemanticError(
                f"no se pudo extraer el archivo de consulta: {error}", where.query_span, source
            ) from None
        return replace(where, vector=vector, nprobe=self.nprobe, ef_search=self.ef_search)

    def centroids(self, table: Any, column: int, native: Any) -> list[list[float]]:
        """Muestra reservoir uniforme y acotada; no materializa la tabla entera."""

        import numpy as np

        from engine.multimedia.kmeans import build_codebook

        sample: list[Any] = []
        rng = random.Random(188)
        for position, row in enumerate(native.source_of(table)):
            if position < 4096:
                sample.append(row[column])
            elif (chosen := rng.randrange(position + 1)) < 4096:
                sample[chosen] = row[column]
        if not sample:
            raise ValueError(
                "IVF necesita filas para entrenar centroides; carga los vectores primero"
            )
        codebook = build_codebook(
            np.asarray(sample, dtype=np.float32),
            min(self.nlist, len(sample)),
            seed=188,
        )
        return codebook.centroids.tolist()
