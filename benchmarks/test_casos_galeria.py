"""Datos artificiales solo para pruebas; nunca se publican como resultados."""

import copy
import json
from pathlib import Path

import pytest

from benchmarks.scripts.casos_galeria import clasificar, preparar, seleccionar, validar_resultados


def vecinos():
    return [
        {"id": i, "rid": [1, i], "nombre": f"{i:06d}.png", "distancia": i / 10} for i in range(1, 6)
    ]


def test_seleccion_reproducible_y_estratos():
    tablas = {t: {} for t in ("exacto", "ivf", "hnsw")}
    for q in range(10, 30):
        for t, p in (("exacto", 0), ("ivf", 16), ("hnsw", 80)):
            top = [[1, i] for i in range(5)]
            if t == "ivf" and q in (10, 11):
                top[-1] = [2, 99]
            tablas[t][(p, 0, q)] = {"top10": json.dumps(top)}
    a = seleccionar(tablas)
    invertidas = {t: dict(reversed(list(rows.items()))) for t, rows in tablas.items()}
    assert a == seleccionar(invertidas)
    assert set(a[:2]) == {10, 11}
    assert len(set(a)) == 4
    assert a != seleccionar(tablas, 100)


@pytest.mark.parametrize(
    "error", ["duplicado", "rid", "faltante", "self", "orden", "nan", "negativo"]
)
def test_validacion_resultados(error):
    rows = vecinos()
    if error == "duplicado":
        rows[-1]["id"] = rows[0]["id"]
    elif error == "rid":
        rows[-1]["rid"] = rows[0]["rid"]
    elif error == "faltante":
        rows.pop()
    elif error == "self":
        rows[-1]["id"] = 99
    elif error == "orden":
        rows.reverse()
    else:
        rows[-1]["distancia"] = float("nan") if error == "nan" else -1
    with pytest.raises(ValueError):
        validar_resultados(rows, 99)


def test_exito_error_y_empates():
    ref = vecinos()
    validar_resultados(ref, 99)
    assert clasificar(ref, ref)["clase"] == "exito"
    otro = copy.deepcopy(ref)
    otro[-1].update(id=6, rid=[1, 6])
    assert clasificar(otro, ref)["recall5"] == 0.8
    assert clasificar(otro, ref)["recall5_empates"] == 1
    otro[-1]["distancia"] = 0.6
    assert clasificar(otro, ref)["clase"] == "error"
    assert clasificar(otro, ref)["faltantes_exactos"] == [5]


def test_recuperacion_nativa_contra_csv_y_archivos():
    pytest.importorskip("quipudb_native")
    raiz = Path(__file__).resolve().parents[1]
    dataset = raiz / "benchmarks/datasets/multimedia/imagenes_1000"
    corrida = raiz / "benchmarks/datasets/metricas_192/imagenes_1000/COSINE"
    if not dataset.exists() or not corrida.exists():
        pytest.skip("fuentes locales de #190/#192 no disponibles")
    datos = preparar(dataset, corrida)
    assert [c["consulta"] for c in datos["casos"]] == [192, 603, 316, 392]
    assert [c["evaluacion"]["ivf"]["recall5"] for c in datos["casos"]] == [0.6, 0.8, 1, 1]
    assert all(c["evaluacion"]["hnsw"]["recall5"] == 1 for c in datos["casos"])
    assert all(c["control_ivf_completo"]["coincide_top10_exacto"] for c in datos["casos"])
