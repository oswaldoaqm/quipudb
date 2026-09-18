"""Comprueba el formato y la reproducibilidad de los datasets comunes (issue #5)."""

from __future__ import annotations

import csv
import hashlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.scripts.generar_datasets import generar_dataset

SCRIPT = Path(__file__).resolve().parent / "scripts" / "generar_datasets.py"
TAMANOS_ESPERADOS = (1_000, 10_000, 100_000)
SHA256_1K = "851cea735a096721d5970ec14bf29d41e87b3fe4467a0eb4a05e3a5e6fd04ad7"


def ejecutar_script(salida: Path, *tamanos: int) -> None:
    comando = [sys.executable, "-B", str(SCRIPT), "--salida", str(salida)]
    if tamanos:
        comando.extend(["--tamanos", *(str(tamano) for tamano in tamanos)])
    # Ejecutar fuera del repositorio tambien comprueba que el script es autonomo.
    subprocess.run(comando, cwd=salida.parent, check=True, capture_output=True, text=True)


@pytest.fixture(scope="module")
def conjuntos(tmp_path_factory):
    raiz = tmp_path_factory.mktemp("datasets")
    primera = raiz / "primera"
    segunda = raiz / "segunda"
    ejecutar_script(primera)
    ejecutar_script(segunda)
    return primera, segunda


def test_genera_exactamente_los_tres_archivos(conjuntos):
    esperados = {f"alumnos_{tamano}.csv" for tamano in TAMANOS_ESPERADOS}
    for carpeta in conjuntos:
        assert {ruta.name for ruta in carpeta.iterdir()} == esperados


@pytest.mark.parametrize("tamano", TAMANOS_ESPERADOS)
def test_contenido_y_formato(conjuntos, tamano):
    ruta = conjuntos[0] / f"alumnos_{tamano}.csv"
    contenido = ruta.read_bytes()
    assert contenido.startswith(b"codigo,nombre,promedio\n")
    assert contenido.endswith(b"\n")
    assert b"\r" not in contenido
    with ruta.open(encoding="utf-8", newline="") as archivo:
        lector = csv.reader(archivo)
        assert next(lector) == ["codigo", "nombre", "promedio"]
        filas = list(lector)

    assert len(filas) == tamano
    codigos = []
    for fila in filas:
        assert len(fila) == 3
        codigo, nombre, promedio = fila
        codigos.append(int(codigo))
        assert nombre == f"alumno{int(codigo)}"
        assert len(nombre) <= 16
        assert len(nombre.encode("utf-8")) <= 16
        assert "\0" not in nombre
        assert re.fullmatch(r"\d{1,2}\.\d{2}", promedio)
        assert 0.0 <= float(promedio) <= 20.0

    assert len(set(codigos)) == tamano
    assert set(codigos) == set(range(1, tamano + 1))
    assert codigos != sorted(codigos)
    assert codigos != sorted(codigos, reverse=True)


@pytest.mark.parametrize("tamano", TAMANOS_ESPERADOS)
def test_dos_ejecuciones_producen_los_mismos_bytes(conjuntos, tamano):
    nombre = f"alumnos_{tamano}.csv"
    assert (conjuntos[0] / nombre).read_bytes() == (conjuntos[1] / nombre).read_bytes()


@pytest.mark.parametrize("tamano", TAMANOS_ESPERADOS)
def test_generacion_individual_coincide_con_la_conjunta(conjuntos, tmp_path, tamano):
    ejecutar_script(tmp_path, tamano)
    nombre = f"alumnos_{tamano}.csv"
    assert {ruta.name for ruta in tmp_path.iterdir()} == {nombre}
    assert (tmp_path / nombre).read_bytes() == (conjuntos[0] / nombre).read_bytes()


def test_el_orden_de_las_llamadas_no_cambia_los_datasets(conjuntos, tmp_path):
    for tamano in reversed(TAMANOS_ESPERADOS):
        ruta = generar_dataset(tamano, tmp_path)
        assert ruta.read_bytes() == (conjuntos[0] / ruta.name).read_bytes()


def test_referencia_fija_del_dataset_1k(conjuntos):
    contenido = (conjuntos[0] / "alumnos_1000.csv").read_bytes()
    # Detecta cambios del algoritmo o la semilla aunque dos corridas nuevas coincidan.
    assert hashlib.sha256(contenido).hexdigest() == SHA256_1K


@pytest.mark.parametrize("tamano", [-1, 0, 999])
def test_rechaza_tamanos_no_soportados_sin_crear_archivos(tmp_path, tamano):
    salida = tmp_path / "no_creada"
    with pytest.raises(ValueError, match="tamano no soportado"):
        generar_dataset(tamano, salida)
    assert not salida.exists()
