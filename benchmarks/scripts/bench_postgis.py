"""Mide GiST de PostGIS con las mismas consultas de la suite espacial (issue #132).

Mismos datasets (#130), mismos centros, radios, valores de k y metricas que
`casos_espaciales.py` (#131), y el mismo formato de CSV que el banco, para que
las tres tecnicas se grafiquen juntas. No necesita los bindings de QuipuDB:
habla con un PostgreSQL con PostGIS, normalmente el contenedor de Docker que
describe el README.

Que se mide
-----------

- Tiempo de consulta: la suma de los `Execution Time` de `EXPLAIN (ANALYZE,
  BUFFERS, TIMING OFF)` de las 100 consultas del lote. Es tiempo del servidor,
  sin red ni parseo del cliente: con Docker en Windows un viaje de ida y vuelta
  cuesta mas que una consulta chica por indice, y compararia la red y no GiST.
  El tiempo visto por el cliente se registra aparte en el entorno.
- Paginas: los bloques compartidos que toco el plan (hit + read), de 8 KiB.
- Construccion: `CREATE INDEX ... USING gist`, cronometrado desde el cliente.
- Espacio: `pg_relation_size` de la tabla y del indice.

Para medir GiST se desactiva el Seq Scan (`enable_seqscan = off`); lo que el
planner habria elegido solo queda registrado como `plan_natural`, y eso es
parte de lo que el 2.2.4 tiene que explicar.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

if __package__:
    from .banco_pruebas import (
        CABECERA_ENTORNO,
        CABECERA_MEDICIONES,
        CABECERA_RESUMEN,
        RESULTADOS,
        escribir_csv,
        registrar_entorno,
        resumir,
    )
    from .cargar_puntos import leer_puntos
    from .casos_espaciales import (
        CONSULTAS,
        METRICAS,
        RADIOS_CRUCE_KM,
        RADIOS_KM,
        VALORES_K,
        radio_en_unidades,
        seleccionar_centros,
    )
    from .generar_puntos import SALIDA_PREDETERMINADA, TAMANOS
else:
    from banco_pruebas import (
        CABECERA_ENTORNO,
        CABECERA_MEDICIONES,
        CABECERA_RESUMEN,
        RESULTADOS,
        escribir_csv,
        registrar_entorno,
        resumir,
    )
    from cargar_puntos import leer_puntos
    from casos_espaciales import (
        CONSULTAS,
        METRICAS,
        RADIOS_CRUCE_KM,
        RADIOS_KM,
        VALORES_K,
        radio_en_unidades,
        seleccionar_centros,
    )
    from generar_puntos import SALIDA_PREDETERMINADA, TAMANOS

TECNICA = "postgis_gist"
DSN_PREDETERMINADO = "host=localhost port=55432 user=postgres password=quipudb dbname=postgres"
PAGINA_POSTGRES = 8_192
# Con Haversine se usa geography sobre la esfera (use_spheroid = false): es la
# misma formula que el core. Con la euclidiana, geometry en grados, sin proyectar.
TIPOS = {"haversine": "geography", "euclidiana": "geometry"}


def tabla_de(metrica: str) -> str:
    return "puntos_geog" if metrica == "haversine" else "puntos_geom"


def indice_de(metrica: str) -> str:
    return f"{tabla_de(metrica)}_gist"


def punto_sql(latitud: float, longitud: float, metrica: str) -> str:
    """Literal del centro. repr de un float es exacto y no admite inyeccion."""
    punto = f"ST_SetSRID(ST_MakePoint({float(longitud)!r}, {float(latitud)!r}), 4326)"
    return punto + "::geography" if metrica == "haversine" else punto


def sql_radio(centro, radio: float, metrica: str) -> str:
    extra = ", false" if metrica == "haversine" else ""
    return (
        f"SELECT id FROM {tabla_de(metrica)} "
        f"WHERE ST_DWithin(ubicacion, {punto_sql(*centro, metrica)}, {float(radio)!r}{extra})"
    )


def sql_knn(centro, k: int, metrica: str) -> str:
    return (
        f"SELECT id FROM {tabla_de(metrica)} "
        f"ORDER BY ubicacion <-> {punto_sql(*centro, metrica)} LIMIT {int(k)}"
    )


def nodos(plan: dict):
    """Todos los nodos de un plan JSON, en preorden."""
    yield plan
    for hijo in plan.get("Plans", ()):
        yield from nodos(hijo)


def resumen_plan(plan: dict) -> str:
    """'Bitmap Heap Scan > Bitmap Index Scan(indice)', para el entorno."""
    partes = []
    for nodo in nodos(plan):
        nombre = nodo["Node Type"]
        if "Index Name" in nodo:
            nombre += f"({nodo['Index Name']})"
        partes.append(nombre)
    return " > ".join(partes)


def conectar(dsn: str, intentos: int = 30):
    """Conexion en autocommit; reintenta mientras el contenedor termina de arrancar."""
    import psycopg

    for intento in range(intentos):
        try:
            conexion = psycopg.connect(dsn, autocommit=True)
            conexion.execute("CREATE EXTENSION IF NOT EXISTS postgis")
            return conexion
        except psycopg.OperationalError:
            if intento == intentos - 1:
                raise
            time.sleep(1)
    raise RuntimeError("no se pudo conectar")


def preparar_tabla(conexion, dataset, metrica: str) -> None:
    """Crea la tabla de la metrica y copia las filas en el orden del CSV, sin indice."""
    tabla = tabla_de(metrica)
    conexion.execute(f"DROP TABLE IF EXISTS {tabla}")
    conexion.execute(
        f"CREATE TABLE {tabla} (id integer PRIMARY KEY, ciudad varchar(16) NOT NULL, "
        f"ubicacion {TIPOS[metrica]}(Point, 4326) NOT NULL)"
    )
    with conexion.cursor().copy(f"COPY {tabla} (id, ciudad, ubicacion) FROM STDIN") as copia:
        for identificador, ciudad, latitud, longitud in dataset.registros:
            copia.write_row((identificador, ciudad, f"SRID=4326;POINT({longitud!r} {latitud!r})"))
    # Estadisticas y mapa de visibilidad al dia, como despues de una carga real.
    conexion.execute(f"VACUUM ANALYZE {tabla}")


def tamanos(conexion, metrica: str) -> tuple[int, int]:
    fila = conexion.execute(
        "SELECT pg_relation_size(%s), COALESCE(pg_relation_size(to_regclass(%s)), 0)",
        (tabla_de(metrica), indice_de(metrica)),
    ).fetchone()
    return int(fila[0]), int(fila[1])


def construir(conexion, metrica: str) -> int:
    """Reconstruye el indice y devuelve los ns de CREATE INDEX."""
    conexion.execute(f"DROP INDEX IF EXISTS {indice_de(metrica)}")
    inicio = time.perf_counter_ns()
    conexion.execute(
        f"CREATE INDEX {indice_de(metrica)} ON {tabla_de(metrica)} USING gist (ubicacion)"
    )
    return time.perf_counter_ns() - inicio


def explicar(conexion, sql: str) -> dict:
    fila = conexion.execute("EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON) " + sql).fetchone()
    resultado = fila[0]
    return (json.loads(resultado) if isinstance(resultado, str) else resultado)[0]


def medir_lote(conexion, consultas: list[str], indice: str) -> dict:
    """Una repeticion: el lote con EXPLAIN ANALYZE y despues sin el, desde el cliente."""
    tiempo_ns = paginas = filas = 0
    devueltos = []
    for sql in consultas:
        salida = explicar(conexion, sql)
        plan = salida["Plan"]
        if not any(nodo.get("Index Name") == indice for nodo in nodos(plan)):
            raise RuntimeError(f"el plan no usa {indice}: {resumen_plan(plan)}")
        tiempo_ns += round(salida["Execution Time"] * 1_000_000)
        paginas += plan.get("Shared Hit Blocks", 0) + plan.get("Shared Read Blocks", 0)
        filas += plan["Actual Rows"]
        devueltos.append(plan["Actual Rows"])
    inicio = time.perf_counter_ns()
    for sql in consultas:
        conexion.execute(sql).fetchall()
    cliente_ns = time.perf_counter_ns() - inicio
    return {
        "tiempo_ns": tiempo_ns,
        "paginas": paginas,
        "resultados": filas,
        "devueltos": devueltos,
        "cliente_ns": cliente_ns,
    }


def operaciones(cruce: bool):
    """(operacion, metrica, parametro) en el mismo orden que la suite espacial."""
    for metrica in METRICAS:
        for radio in RADIOS_KM:
            yield f"radio_{radio}km", metrica, radio
        for k in VALORES_K:
            yield f"knn_{k}", metrica, k
    if cruce:
        for radio in RADIOS_CRUCE_KM:
            yield f"radio_{radio}km", "haversine", radio


def referencias_quipudb(ruta: Path | None) -> dict[tuple[str, int], int]:
    """resultados_totales por (caso sin tecnica, N) de una corrida de #131."""
    if ruta is None:
        return {}
    import csv

    referencias = {}
    with ruta.open(encoding="utf-8") as archivo:
        for fila in csv.DictReader(archivo):
            partes = fila["clave"].split(".")
            if (
                len(partes) == 4
                and partes[0] == "caso"
                and partes[1].startswith("rtree_")
                and partes[3] == "resultados_totales"
            ):
                caso = partes[1].removeprefix("rtree_")
                referencias[(caso, int(partes[2]))] = int(fila["valor"])
    return referencias


def fila_medicion(identificador, caso, operacion, n, n_operaciones, repeticion, valores):
    return {
        "ejecucion": identificador,
        "caso": caso,
        "tecnica": TECNICA,
        "operacion": operacion,
        "n_registros": n,
        "n_operaciones": n_operaciones,
        "repeticion": repeticion,
        "estado": "ok",
        **valores,
    }


def ejecutar(
    conexion,
    datasets,
    *,
    salida: Path,
    calentamientos: int = 1,
    repeticiones: int = 5,
    consultas: int = CONSULTAS,
    cruce: bool = True,
    referencias: dict | None = None,
    notas: dict | None = None,
) -> dict[str, Path]:
    """Corre todos los casos y exporta los tres CSV solo si todo termino bien."""
    if calentamientos < 0 or repeticiones < 1:
        raise ValueError("calentamientos debe ser >= 0 y repeticiones debe ser >= 1")
    referencias = referencias or {}
    identificador = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    entorno = registrar_entorno()
    version = conexion.execute("SELECT version(), postgis_full_version()").fetchone()
    configuracion = {
        clave: conexion.execute(f"SHOW {clave}").fetchone()[0]
        for clave in ("shared_buffers", "work_mem", "jit", "block_size")
    }
    entorno.update(
        {
            "tecnica": TECNICA,
            "postgres": version[0],
            "postgis": version[1],
            **{f"postgres.{clave}": valor for clave, valor in configuracion.items()},
            "calentamientos": calentamientos,
            "repeticiones": repeticiones,
            "salida": str(salida.resolve()),
            "tiempo_consultas": "suma de Execution Time de EXPLAIN ANALYZE, TIMING OFF",
            "tiempo_construccion": "CREATE INDEX cronometrado desde el cliente",
            "paginas": "shared hit + read del plan, bloques de 8 KiB",
            "paginas_construccion": "no_instrumentadas",
            "enable_seqscan_medicion": "off",
        }
    )
    entorno.update({f"declarado.{clave}": valor for clave, valor in (notas or {}).items()})
    mediciones = []

    for dataset in datasets:
        n = len(dataset.registros)
        entorno[f"dataset.{n}.ruta"] = str(dataset.ruta)
        entorno[f"dataset.{n}.sha256"] = dataset.sha256
        centros = seleccionar_centros(dataset, consultas)
        for metrica in METRICAS:
            preparar_tabla(conexion, dataset, metrica)
            caso = f"{TECNICA}_construccion_indice_{metrica}"
            for repeticion in range(-calentamientos + 1, repeticiones + 1):
                tiempo_ns = construir(conexion, metrica)
                datos, indice = tamanos(conexion, metrica)
                if repeticion > 0:
                    mediciones.append(
                        fila_medicion(
                            identificador,
                            caso,
                            "construccion_indice",
                            n,
                            1,
                            repeticion,
                            {
                                "tiempo_ns": tiempo_ns,
                                "paginas_leidas": 0,
                                "paginas_escritas": 0,
                                "datos_bytes": datos,
                                "indices_bytes": indice,
                                "espacio_antes_bytes": datos,
                                "espacio_despues_bytes": datos + indice,
                            },
                        )
                    )
            conexion.execute(f"ANALYZE {tabla_de(metrica)}")

        for operacion, metrica, parametro in operaciones(cruce):
            caso = f"{TECNICA}_{operacion}_{metrica}"
            if operacion.startswith("radio"):
                radio = radio_en_unidades(parametro, metrica)
                lote = [sql_radio(c, radio, metrica) for c in centros]
            else:
                lote = [sql_knn(c, parametro, metrica) for c in centros]

            conexion.execute("SET enable_seqscan = on")
            natural = conexion.execute("EXPLAIN (FORMAT JSON) " + lote[0]).fetchone()[0]
            natural = json.loads(natural) if isinstance(natural, str) else natural
            entorno[f"caso.{caso}.{n}.plan_natural"] = resumen_plan(natural[0]["Plan"])
            conexion.execute("SET enable_seqscan = off")

            datos, indice = tamanos(conexion, metrica)
            clientes = []
            devueltos = None
            for repeticion in range(-calentamientos + 1, repeticiones + 1):
                medido = medir_lote(conexion, lote, indice_de(metrica))
                if devueltos is not None and medido["devueltos"] != devueltos:
                    raise RuntimeError(f"{caso}: la respuesta cambio entre repeticiones")
                devueltos = medido["devueltos"]
                if operacion.startswith("knn") and any(d != parametro for d in devueltos):
                    raise RuntimeError(f"{caso}: un k-NN no devolvio k filas")
                if repeticion > 0:
                    clientes.append(medido["cliente_ns"])
                    mediciones.append(
                        fila_medicion(
                            identificador,
                            caso,
                            operacion,
                            n,
                            len(lote),
                            repeticion,
                            {
                                "tiempo_ns": medido["tiempo_ns"],
                                "paginas_leidas": medido["paginas"],
                                "paginas_escritas": 0,
                                "datos_bytes": datos,
                                "indices_bytes": indice,
                                "espacio_antes_bytes": datos + indice,
                                "espacio_despues_bytes": datos + indice,
                            },
                        )
                    )
            conexion.execute("SET enable_seqscan = on")
            total = sum(devueltos)
            prefijo = f"caso.{caso}.{n}"
            entorno[f"{prefijo}.metrica"] = metrica
            entorno[f"{prefijo}.consultas"] = len(lote)
            entorno[f"{prefijo}.resultados_totales"] = total
            entorno[f"{prefijo}.tiempo_cliente_ns_mediana"] = statistics.median(clientes)
            esperado = referencias.get((f"{operacion}_{metrica}", n))
            if esperado is not None:
                entorno[f"{prefijo}.resultados_quipudb"] = esperado
                if esperado != total:
                    raise RuntimeError(
                        f"{caso} N={n}: PostGIS devolvio {total} filas y QuipuDB {esperado}"
                    )

    salida.mkdir(parents=True, exist_ok=True)
    rutas = {
        tipo: salida / f"{identificador}_{tipo}.csv"
        for tipo in ("mediciones", "resumen", "entorno")
    }
    escribir_csv(rutas["mediciones"], CABECERA_MEDICIONES, mediciones)
    escribir_csv(rutas["resumen"], CABECERA_RESUMEN, resumir(mediciones))
    escribir_csv(
        rutas["entorno"],
        CABECERA_ENTORNO,
        [
            {"ejecucion": identificador, "clave": clave, "valor": valor}
            for clave, valor in sorted(entorno.items())
        ],
    )
    return rutas


def nota_entorno(texto: str) -> tuple[str, str]:
    clave, separador, valor = texto.partition("=")
    if not separador or not clave.strip() or not valor.strip():
        raise argparse.ArgumentTypeError("usa CLAVE=VALOR")
    return clave.strip(), valor.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dsn", default=DSN_PREDETERMINADO)
    parser.add_argument("--datasets", type=Path, default=SALIDA_PREDETERMINADA)
    parser.add_argument("--salida", type=Path, default=RESULTADOS)
    parser.add_argument("--tamanos", type=int, nargs="+", choices=TAMANOS, default=TAMANOS)
    parser.add_argument("--calentamientos", type=int, default=1)
    parser.add_argument("--repeticiones", type=int, default=5)
    parser.add_argument("--consultas", type=int, default=CONSULTAS)
    parser.add_argument("--sin-cruce", action="store_true")
    parser.add_argument(
        "--referencia",
        type=Path,
        help="<id>_entorno.csv de una corrida de #131: aborta si PostGIS devuelve otra cantidad",
    )
    parser.add_argument(
        "--entorno", type=nota_entorno, action="append", default=[], metavar="CLAVE=VALOR"
    )
    args = parser.parse_args()
    if len(set(args.tamanos)) != len(args.tamanos):
        parser.error("no repitas tamanos")
    try:
        datasets = [leer_puntos(args.datasets / f"puntos_{n}.csv", n) for n in args.tamanos]
        conexion = conectar(args.dsn)
        with conexion:
            rutas = ejecutar(
                conexion,
                datasets,
                salida=args.salida,
                calentamientos=args.calentamientos,
                repeticiones=args.repeticiones,
                consultas=args.consultas,
                cruce=not args.sin_cruce,
                referencias=referencias_quipudb(args.referencia),
                notas=dict(args.entorno),
            )
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"error: {error}\n")
    for tipo, ruta in rutas.items():
        print(f"{tipo}: {ruta}")


if __name__ == "__main__":
    main()
