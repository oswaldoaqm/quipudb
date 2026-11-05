"""Fuentes sinteticas solo para probar validadores; no son mediciones del informe."""

import csv
import json
from pathlib import Path

import pytest

from benchmarks.scripts import datos_parte4 as datos
from benchmarks.scripts.comparar_metricas import comparar
from benchmarks.scripts.resumir_vectoriales import resumir


def escribir(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def corrida(p, metrica):
    escribir(p / "completo.json", {"estado": "ok"})
    escribir(
        p / "entrada.json",
        {
            "n": 1000,
            "modalidad": "audio",
            "nlist": 32,
            "semilla": 20260906,
            "ids": list(range(1, 101)),
            "vectores_sha256": "prueba",
        },
    )
    escribir(
        p / "entorno.json",
        {
            "metrica": metrica,
            "k": 10,
            "candidatos": 11,
            "consultas": 100,
            "repeticiones": 5,
            "calentamientos": 1,
            "m": 16,
            "ef_construction": 200,
        },
    )
    for t in datos.TECNICAS:
        rows = []
        for param in datos.parametros(1000, t):
            for rep in range(5):
                for i in range(1, 101):
                    rows.append(
                        {
                            "tecnica": t,
                            "modalidad": "audio",
                            "n": 1000,
                            "parametro": param,
                            "repeticion": rep,
                            "consulta_id": i,
                            "tiempo_ns": 1000000 * (param + 1) * (rep + 1),
                            "recall10": 1,
                            "recall_empates": 1,
                            "resultados": 10,
                            "top10": json.dumps([[1, k] for k in range(10)]),
                        }
                    )
        with (p / f"{t}_consultas.csv").open("w") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        escribir(p / f"{t}_memoria.json", {"rss_max_bytes": 1000})
        if t != "exacto":
            escribir(
                p / f"{t}_construccion.json",
                {
                    "entrenamiento_s": 0.1 if t == "ivf" else 0,
                    "indice_s": 0.2,
                    "total_s": 0.3 if t == "ivf" else 0.2,
                    "indice_bytes": 100,
                    "rss_max_bytes": 1000,
                },
            )


@pytest.fixture
def fuentes(tmp_path, monkeypatch):
    monkeypatch.setattr(datos, "MODALIDADES", ("audio",))
    monkeypatch.setattr(datos, "TAMANOS", (1000,))
    a, b = tmp_path / "191", tmp_path / "192"
    corrida(a / "audio_1000", "COSINE")
    rows = resumir(a)
    with (a / "resumen.csv").open("w") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for m in ("COSINE", "EUCLIDEAN"):
        corrida(b / "audio_1000" / m, m)
    comparar(b / "audio_1000")
    return a, b


def test_lectura_y_unidades(fuentes):
    a, b = fuentes
    r = datos.leer_191(a)
    fila = next(r for r in r if r["tecnica"] == "ivf" and r["parametro"] == 1)
    assert fila["consulta_ms"] == 6
    assert fila["minimo_ms"] == 2 and fila["maximo_ms"] == 10
    assert len(datos.leer_192(b)) == 13
    assert datos.seleccionar(r, "audio", 1000, "ivf")["parametro"] == 1


@pytest.mark.parametrize(
    "cambio", ["faltante", "incompleto", "nan", "negativo", "modalidad", "duplicado", "parametro"]
)
def test_rechaza_fuentes_invalidas(fuentes, cambio):
    a, _ = fuentes
    p = a / "audio_1000/ivf_consultas.csv"
    if cambio == "faltante":
        p.unlink()
    else:
        with p.open() as f:
            rows = list(csv.DictReader(f))
            campos = list(rows[0])
        if cambio == "incompleto":
            rows.pop()
        if cambio == "duplicado":
            rows.append(rows[0])
        if cambio in ("nan", "negativo"):
            rows[0]["tiempo_ns"] = "nan" if cambio == "nan" else "-1"
        if cambio == "modalidad":
            rows[0]["modalidad"] = "imagenes"
        if cambio == "parametro":
            rows[0]["parametro"] = "999"
        with p.open("w") as f:
            w = csv.DictWriter(f, fieldnames=campos)
            w.writeheader()
            w.writerows(rows)
    with pytest.raises((ValueError, FileNotFoundError)):
        datos.leer_191(a)


def test_no_acepta_resumen_alterado(fuentes):
    a, b = fuentes
    p = b / "audio_1000/resumen.csv"
    p.write_text(p.read_text().replace(",3.0,", ",99.0,", 1))
    with pytest.raises(ValueError, match="inconsistente"):
        datos.leer_192(b)
    p = a / "resumen.csv"
    p.write_text(p.read_text().replace(",3.0,", ",99.0,", 1))
    with pytest.raises(ValueError, match="inconsistente"):
        datos.leer_191(a)


def test_consultas_de_metricas_deben_coincidir(fuentes):
    _, b = fuentes
    p = b / "audio_1000/EUCLIDEAN/entrada.json"
    r = json.loads(p.read_text())
    r["ids"][0] = 999
    p.write_text(json.dumps(r))
    with pytest.raises(ValueError):
        datos.leer_192(b)


@pytest.mark.parametrize("campo", ["comunes", "coseno_ns"])
def test_comparacion_debe_corresponder_a_consultas(fuentes, campo):
    _, b = fuentes
    p = b / "audio_1000/comparacion.csv"
    rows = datos.csv_de(p)
    rows[0][campo] = str(float(rows[0][campo]) + 1)
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with pytest.raises(ValueError, match="inconsistente"):
        datos.leer_192(b)


def test_seleccion_exige_umbral_y_desempata_por_parametro():
    base = {"dataset": "audio_1000", "tecnica": "ivf", "consulta_ms": 1, "recall10": 0.98}
    with pytest.raises(ValueError, match="ninguna configuracion"):
        datos.seleccionar([{**base, "parametro": 1}], "audio", 1000, "ivf")
    filas = [{**base, "recall10": 0.99, "parametro": p} for p in (4, 2)]
    assert datos.seleccionar(filas, "audio", 1000, "ivf")["parametro"] == 2


def test_fuentes_reales_si_estan_disponibles():
    raiz = Path(__file__).resolve().parents[1]
    a = raiz / "benchmarks/datasets/ivf_hnsw_191"
    b = raiz / "benchmarks/datasets/metricas_192"
    if not a.exists() or not b.exists():
        pytest.skip("fuentes locales ignoradas no disponibles")
    assert len(datos.leer_191(a)) == 88
    assert len(datos.leer_192(b)) == 88
