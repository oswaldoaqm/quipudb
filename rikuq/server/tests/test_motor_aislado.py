"""Regresion: configurar integracion no reutiliza el catalogo del usuario."""

from pathlib import Path

from tests.motor_aislado import REPO, entorno_aislado


def test_el_catalogo_y_pythonpath_heredados_se_reemplazan_por_destinos_propios(tmp_path) -> None:
    heredado = {
        "QUIPUDB_CATALOG": "catalogo-que-no-debe-tocarse.txt",
        "QUIPUDB_MEDIA_ROOT": "fotos-del-usuario",
        "PYTHONPATH": "otra-aplicacion",
        "QUIPUDB_URL": "http://motor-del-usuario:8000",
        "QUIPUDB_API_URL": "http://otro-motor-del-usuario:8000",
        "QUIPUDB_DISTRIBUTED_CONFIG": "cluster-del-usuario.json",
    }
    entorno = entorno_aislado(tmp_path, heredado)
    assert Path(entorno["QUIPUDB_CATALOG"]).is_relative_to(tmp_path)
    assert Path(entorno["QUIPUDB_MEDIA_ROOT"]).is_relative_to(tmp_path)
    assert str(REPO / "build-py" / "bindings") in entorno["PYTHONPATH"]
    assert "otra-aplicacion" not in entorno["PYTHONPATH"]
    assert "QUIPUDB_URL" not in entorno
    assert "QUIPUDB_API_URL" not in entorno
    assert "QUIPUDB_DISTRIBUTED_CONFIG" not in entorno
    assert heredado["QUIPUDB_CATALOG"] == "catalogo-que-no-debe-tocarse.txt"
