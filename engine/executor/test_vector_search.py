"""Limites y extraccion del adaptador vectorial sin necesidad del core."""

from types import SimpleNamespace

import pytest

from engine.executor.vector_search import VectorSearch
from engine.parser import parse_sql
from engine.parser.errors import SQLSemanticError
from engine.parser.semantic import bind_select
from engine.parser.test_similar import QUERY, SCHEMA


@pytest.mark.parametrize("parameter", [0, -1, True, 1.5, 65537])
@pytest.mark.parametrize("position", [0, 1, 2])
def test_parametros_del_indice_deben_ser_enteros_acotados(tmp_path, parameter, position):
    options = [1, 64, 16]
    options[position] = parameter
    with pytest.raises(ValueError):
        VectorSearch(tmp_path, {}, *options)


def test_ivf_muestrea_sin_materializar_el_dataset(tmp_path, monkeypatch):
    from engine.multimedia import kmeans

    sampled = []

    def codebook(values, k, **options):
        sampled.append(values)
        assert k == 16
        return SimpleNamespace(centroids=values[:k])

    monkeypatch.setattr(kmeans, "build_codebook", codebook)
    native = SimpleNamespace(source_of=lambda table: ([i, [float(i), 0.0]] for i in range(10000)))
    search = VectorSearch(tmp_path, {}, 1, 64, 16)
    first = search.centroids(None, 1, native)
    second = search.centroids(None, 1, native)
    assert len(sampled[0]) == 4096
    assert first == second
    assert max(sampled[0][:, 0]) > 9000  # No es solo el prefijo de la tabla.


def test_archivo_excesivo_se_rechaza_antes_de_decodificar(tmp_path):
    query = tmp_path / "consulta.jpg"
    with query.open("wb") as stream:
        stream.truncate(64 * 1024**2 + 1)
    (tmp_path / "vocab.npz").write_bytes(b"no se abrira")
    search = VectorSearch(tmp_path, {"fotos": "vocab.npz"}, 1, 64, 16)
    where = bind_select(parse_sql(QUERY), SCHEMA).where
    with pytest.raises(SQLSemanticError, match="64 MiB"):
        search.paths("fotos", where, QUERY)
