"""Comprueba el dataset espacial del 2.2.4: formato, plausibilidad y reproducibilidad (#130)."""

from __future__ import annotations

import csv
import hashlib
import math
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from benchmarks.scripts.cargar_puntos import INDICE, TABLA, cargar_quipudb, leer_puntos
from benchmarks.scripts.generar_puntos import (
    CIUDADES,
    FACTOR_PERIFERIA,
    generar_dataset,
    generar_puntos,
)

SCRIPT = Path(__file__).resolve().parent / "scripts" / "generar_puntos.py"
TAMANOS_ESPERADOS = (1_000, 10_000, 100_000)
SHA256_1K = "a71f78dc15cd138a90c7be4d8cb6a925bbb2afacfc22e613a80b743061e6c82e"
CIUDAD = {ciudad.nombre: ciudad for ciudad in CIUDADES}


def ejecutar_script(salida: Path, *tamanos: int) -> None:
    comando = [sys.executable, "-B", str(SCRIPT), "--salida", str(salida)]
    if tamanos:
        comando.extend(["--tamanos", *(str(tamano) for tamano in tamanos)])
    # Ejecutar fuera del repositorio tambien comprueba que el script es autonomo.
    subprocess.run(comando, cwd=salida.parent, check=True, capture_output=True, text=True)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Oraculo independiente del core, con el mismo radio terrestre."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * 6_371_008.8 * math.asin(math.sqrt(h))


@pytest.fixture(scope="module")
def conjuntos(tmp_path_factory):
    raiz = tmp_path_factory.mktemp("puntos")
    primera = raiz / "primera"
    segunda = raiz / "segunda"
    ejecutar_script(primera)
    ejecutar_script(segunda)
    return primera, segunda


def test_genera_exactamente_los_tres_archivos(conjuntos):
    esperados = {f"puntos_{tamano}.csv" for tamano in TAMANOS_ESPERADOS}
    for carpeta in conjuntos:
        assert {ruta.name for ruta in carpeta.iterdir()} == esperados


@pytest.mark.parametrize("tamano", TAMANOS_ESPERADOS)
def test_contenido_y_formato(conjuntos, tamano):
    ruta = conjuntos[0] / f"puntos_{tamano}.csv"
    contenido = ruta.read_bytes()
    assert contenido.startswith(b"id,ciudad,latitud,longitud\n")
    assert contenido.endswith(b"\n")
    assert b"\r" not in contenido
    with ruta.open(encoding="utf-8", newline="") as archivo:
        filas = list(csv.reader(archivo))[1:]

    assert len(filas) == tamano
    assert [int(fila[0]) for fila in filas] == list(range(1, tamano + 1))
    for _, ciudad, latitud, longitud in filas:
        assert ciudad in CIUDAD
        # Seis decimales fijos: el texto no depende de como se imprime un double.
        assert len(latitud.split(".")[1]) == 6
        assert len(longitud.split(".")[1]) == 6


@pytest.mark.parametrize("tamano", TAMANOS_ESPERADOS)
def test_dos_ejecuciones_producen_los_mismos_bytes(conjuntos, tamano):
    nombre = f"puntos_{tamano}.csv"
    assert (conjuntos[0] / nombre).read_bytes() == (conjuntos[1] / nombre).read_bytes()


def test_referencia_fija_del_dataset_1k(conjuntos):
    contenido = (conjuntos[0] / "puntos_1000.csv").read_bytes()
    # Detecta cambios del algoritmo o la semilla aunque dos corridas nuevas coincidan.
    assert hashlib.sha256(contenido).hexdigest() == SHA256_1K


def test_los_datasets_chicos_son_prefijos_de_los_grandes(conjuntos):
    """Cada punto consume los mismos numeros aleatorios en todos los tamanos, asi
    que 1k son las primeras 1 000 filas de 10k y 10k las primeras de 100k: al
    crecer el dataset solo se agregan puntos, nunca cambian los que ya estaban."""
    lineas = {
        tamano: (conjuntos[0] / f"puntos_{tamano}.csv").read_bytes().splitlines()
        for tamano in TAMANOS_ESPERADOS
    }
    assert lineas[10_000][:1_001] == lineas[1_000]
    assert lineas[100_000][:10_001] == lineas[10_000]


def test_el_orden_de_las_llamadas_no_cambia_los_datasets(conjuntos, tmp_path):
    for tamano in reversed(TAMANOS_ESPERADOS):
        ruta = generar_dataset(tamano, tmp_path)
        assert ruta.read_bytes() == (conjuntos[0] / ruta.name).read_bytes()


def test_generacion_individual_coincide_con_la_conjunta(conjuntos, tmp_path):
    ejecutar_script(tmp_path, 1_000)
    assert {ruta.name for ruta in tmp_path.iterdir()} == {"puntos_1000.csv"}
    nombre = "puntos_1000.csv"
    assert (tmp_path / nombre).read_bytes() == (conjuntos[0] / nombre).read_bytes()


def test_los_puntos_caen_cerca_de_su_ciudad():
    """Plausibilidad: ningun punto se va mas alla de seis desviaciones de la
    periferia de su ciudad, y todos quedan dentro del Peru continental."""
    for _, nombre, latitud, longitud in generar_puntos(100_000):
        ciudad = CIUDAD[nombre]
        lat, lon = float(latitud), float(longitud)
        limite_m = 6 * FACTOR_PERIFERIA * ciudad.sigma_km * 1_000
        assert haversine_m(ciudad.latitud, ciudad.longitud, lat, lon) <= limite_m
        assert -19.0 <= lat <= -0.0
        assert -82.0 <= lon <= -68.0


def test_la_distribucion_sigue_a_la_poblacion_y_no_es_uniforme():
    conteo = Counter(nombre for _, nombre, _, _ in generar_puntos(100_000))
    assert set(conteo) == set(CIUDAD)
    total_poblacion = sum(ciudad.poblacion for ciudad in CIUDADES)
    for nombre, cantidad in conteo.items():
        esperado = 100_000 * CIUDAD[nombre].poblacion / total_poblacion
        # Un margen de cinco desviaciones binomiales: falla solo si el peso esta mal.
        assert abs(cantidad - esperado) <= 5 * math.sqrt(esperado)
    # Lima concentra la mayoria: es el caso denso que exige al R-Tree.
    assert conteo["lima"] > 50_000


def test_las_nubes_no_estan_estiradas_este_oeste():
    """La correccion por el coseno de la latitud deja la dispersion en kilometros
    igual en las dos direcciones."""
    lima = CIUDAD["lima"]
    norte, este = [], []
    for _, nombre, latitud, longitud in generar_puntos(100_000):
        if nombre != "lima":
            continue
        norte.append(haversine_m(lima.latitud, lima.longitud, float(latitud), lima.longitud))
        este.append(haversine_m(lima.latitud, lima.longitud, lima.latitud, float(longitud)))
    assert sum(este) / sum(norte) == pytest.approx(1.0, abs=0.03)


@pytest.mark.parametrize("tamano", [-1, 0, 999])
def test_rechaza_tamanos_no_soportados_sin_crear_archivos(tmp_path, tamano):
    salida = tmp_path / "no_creada"
    with pytest.raises(ValueError, match="tamano no soportado"):
        generar_dataset(tamano, salida)
    assert not salida.exists()


# ---------------------------------------------------------------------------
# Lectura del CSV para la carga
# ---------------------------------------------------------------------------


def test_leer_puntos_convierte_y_conserva_el_orden(conjuntos):
    dataset = leer_puntos(conjuntos[0] / "puntos_1000.csv", 1_000)
    assert dataset.sha256 == SHA256_1K
    assert len(dataset.filas) == 1_000
    assert dataset.filas[0] == (1, "chimbote", -9.079577, -78.596628)


@pytest.mark.parametrize(
    ("contenido", "mensaje"),
    [
        ("id,lat,lon\n", "cabecera"),
        ("id,ciudad,latitud,longitud\n1,lima,-12.0\n", "columnas"),
        ("id,ciudad,latitud,longitud\n2,lima,-12.0,-77.0\n", "id 1"),
        ("id,ciudad,latitud,longitud\n1,lima,x,-77.0\n", "no numericos"),
        ("id,ciudad,latitud,longitud\n1,lima,nan,-77.0\n", "latitud"),
        ("id,ciudad,latitud,longitud\n1,lima,-91.0,-77.0\n", "latitud"),
        ("id,ciudad,latitud,longitud\n1,lima,-12.0,181.0\n", "longitud"),
        ("id,ciudad,latitud,longitud\n1,,-12.0,-77.0\n", "VARCHAR"),
        ("id,ciudad,latitud,longitud\n1,lima,-12.0,-77.0\n", "se esperaban 1000"),
    ],
)
def test_leer_puntos_rechaza_entradas_invalidas(tmp_path, contenido, mensaje):
    ruta = tmp_path / "puntos.csv"
    ruta.write_text(contenido, encoding="utf-8", newline="")
    with pytest.raises(ValueError, match=mensaje):
        leer_puntos(ruta, 1_000)


def test_leer_puntos_explica_como_generar_un_dataset_inexistente(tmp_path):
    with pytest.raises(ValueError, match="generar_puntos.py"):
        leer_puntos(tmp_path / "no_existe.csv")


# ---------------------------------------------------------------------------
# Carga real en QuipuDB
# ---------------------------------------------------------------------------


@pytest.fixture
def nativo_real():
    return pytest.importorskip(
        "quipudb_native",
        reason="requiere bindings compilados para este Python y configurados en PYTHONPATH",
    )


def test_integracion_carga_en_heap_con_rtree(nativo_real, conjuntos, tmp_path):
    dataset = leer_puntos(conjuntos[0] / "puntos_1000.csv", 1_000)
    db = nativo_real.Database(tmp_path / "catalogo.txt")
    tabla, indice = cargar_quipudb(nativo_real, db, dataset)

    assert db.table_info(TABLA).storage == nativo_real.kind.HEAP
    assert len(tabla) == 1_000
    assert len(indice) == 1_000
    assert db.index(TABLA, INDICE) is indice

    # El indice responde lo mismo que un recorrido completo con un oraculo propio.
    _, _, lat_c, lon_c = dataset.filas[0]
    centro = nativo_real.GeoPoint(lat_c, lon_c)
    rids = indice.search_radius(centro, 10_000.0, nativo_real.Metric.HAVERSINE)
    obtenidos = sorted(tabla.read(rid)[0] for rid in rids)
    esperados = sorted(
        identificador
        for identificador, _, lat, lon in dataset.filas
        if haversine_m(lat_c, lon_c, lat, lon) <= 10_000.0
    )
    assert obtenidos == esperados
    assert len(esperados) > 1


def test_integracion_carga_sin_indice(nativo_real, conjuntos, tmp_path):
    dataset = leer_puntos(conjuntos[0] / "puntos_1000.csv", 1_000)
    db = nativo_real.Database(tmp_path / "catalogo.txt")
    tabla, indice = cargar_quipudb(nativo_real, db, dataset, con_indice=False)

    assert indice is None
    assert len(tabla) == 1_000
    fila = tabla.search(1)[0]
    assert fila[1] == "chimbote"
    assert fila[2].latitude == pytest.approx(-9.079577)
    assert fila[2].longitude == pytest.approx(-78.596628)
