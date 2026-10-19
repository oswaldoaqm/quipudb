"""Mide el GIN de PostgreSQL con los mismos corpus y consultas de la suite texto (issue #174).

Mismos documentos y consultas de #171, el mismo formato de CSV que el banco
(#172) y el mismo formato de rankings que `relevancia_texto.py` (#173), para que
las tres tecnicas se grafiquen y se evaluen juntas. No necesita los bindings de
QuipuDB: habla con un PostgreSQL (sin extensiones).

Como se arma
------------

Tabla `documentos(id, texto varchar(256), tsv tsvector)`, con `tsv` como
columna generada `to_tsvector('english', texto)`: el mismo idioma, las mismas
stopwords de Snowball y el mismo stemmer que el indice de QuipuDB (#160). El
GIN se crea sobre `tsv`, que es como la documentacion de PostgreSQL recomienda
hacerlo para poder rankear sin recalcular el tsvector.

Dos formas de consultar
-----------------------

- `gin_or`: `plainto_tsquery` con los `&` cambiados por `|`. Encuentra los
  documentos con ALGUN termino, como `MATCH` en QuipuDB: es la comparable.
- `gin_and`: `plainto_tsquery` tal cual, con TODOS los terminos. Es la forma
  habitual de PostgreSQL, y muestra lo que cambia al exigir todos.

En las dos se ordena por `ts_rank(tsv, consulta)` de mayor a menor y, a igual
puntaje, por id: el mismo desempate que QuipuDB. `ts_rank` no es TF-IDF ni
BM25: pondera la frecuencia de los terminos en el documento, sin idf.

Que se mide
-----------

- Consultas: la suma de los `Execution Time` de `EXPLAIN (ANALYZE, BUFFERS,
  TIMING OFF, FORMAT JSON)` del lote, igual que #132: tiempo del servidor, sin
  red. El del cliente queda aparte en el entorno. Paginas: los bloques
  compartidos que toco el plan (hit + read, de 8 KiB). Se fuerza el indice con
  `enable_seqscan = off` y se verifica que el plan lo use; el plan que habria
  elegido PostgreSQL queda como `plan_natural`.
- Construccion: `CREATE INDEX ... USING gin (tsv)` desde el cliente. La
  tokenizacion no esta ahi, porque la hizo la columna generada al cargar; se
  mide aparte (`tokenizacion`) recalculando `to_tsvector` sobre todo el corpus.
  QuipuDB tokeniza dentro de su `build`.
- Espacio: `pg_relation_size` de la tabla (con la columna `tsv`) y del indice.
- Rankings: despues de medir, el top-10 de cada consulta mas los empatados con
  el 10.o, para `relevancia_texto.py`.

La sesion apaga `jit` y las consultas en paralelo: QuipuDB responde con un
solo hilo y una compilacion JIT entraria en consultas de milisegundos.
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
    from .bench_postgis import nodos, nota_entorno, resumen_plan
    from .cargar_documentos import LARGO_TEXTO, leer_consultas, leer_documentos
    from .casos_texto import CONSULTAS_POR_LONGITUD, consultas_de_longitud, nombre_longitud
    from .generar_documentos import LONGITUDES, SALIDA_PREDETERMINADA, TAMANOS
    from .relevancia_texto import K, escribir_rankings, recortar
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
    from bench_postgis import nodos, nota_entorno, resumen_plan
    from cargar_documentos import LARGO_TEXTO, leer_consultas, leer_documentos
    from casos_texto import CONSULTAS_POR_LONGITUD, consultas_de_longitud, nombre_longitud
    from generar_documentos import LONGITUDES, SALIDA_PREDETERMINADA, TAMANOS
    from relevancia_texto import K, escribir_rankings, recortar

TABLA = "documentos"
INDICE = "documentos_gin"
CONFIGURACION_TEXTO = "english"
MODOS = ("gin_or", "gin_and")
DSN_PREDETERMINADO = "host=localhost port=5432 user=postgres password=quipudb dbname=quipudb_bench"
AJUSTES_SESION = {"jit": "off", "max_parallel_workers_per_gather": "0"}


def tsquery_sql(modo: str) -> str:
    """La expresion de la consulta, con un parametro para el texto."""
    plana = f"plainto_tsquery('{CONFIGURACION_TEXTO}', %s)"
    if modo == "gin_and":
        return plana
    if modo == "gin_or":
        # plainto_tsquery normaliza (stopwords y stemmer) y une con &; cambiar
        # cada & por | da la misma consulta con OR. Los lexemas van entre
        # comillas en el texto del tsquery, asi que el reemplazo no los toca.
        return f"CAST(replace({plana}::text, ' & ', ' | ') AS tsquery)"
    raise ValueError(f"modo desconocido: {modo}")


def sql_top(modo: str, k: int | None = K) -> str:
    """Los k mas relevantes, o todos los que coinciden si k es None."""
    limite = "" if k is None else f" LIMIT {int(k)}"
    return (
        f"SELECT id, ts_rank(tsv, q) AS puntaje FROM {TABLA}, {tsquery_sql(modo)} AS q "
        f"WHERE tsv @@ q ORDER BY puntaje DESC, id{limite}"
    )


def conectar(dsn: str):
    """Conexion en autocommit con los ajustes de sesion de la medicion."""
    import psycopg

    try:
        conexion = psycopg.connect(dsn, autocommit=True)
    except psycopg.OperationalError as error:
        raise RuntimeError(
            f"no se pudo conectar a PostgreSQL ({error}); si falta la base, "
            'crearla con: psql -U postgres -c "CREATE DATABASE quipudb_bench"'
        ) from error
    for clave, valor in AJUSTES_SESION.items():
        conexion.execute(f"SET {clave} = {valor}")
    return conexion


def preparar_tabla(conexion, dataset) -> None:
    """Crea la tabla y copia los documentos en el orden del CSV, sin indice."""
    conexion.execute(f"DROP TABLE IF EXISTS {TABLA}")
    conexion.execute(
        f"CREATE TABLE {TABLA} (id integer PRIMARY KEY, texto varchar({LARGO_TEXTO}) NOT NULL, "
        f"tsv tsvector GENERATED ALWAYS AS (to_tsvector('{CONFIGURACION_TEXTO}', texto)) STORED)"
    )
    with conexion.cursor().copy(f"COPY {TABLA} (id, texto) FROM STDIN") as copia:
        for registro in dataset.registros:
            copia.write_row(registro)
    # Estadisticas y mapa de visibilidad al dia, como despues de una carga real.
    conexion.execute(f"VACUUM ANALYZE {TABLA}")


def tamanos(conexion) -> tuple[int, int]:
    fila = conexion.execute(
        "SELECT pg_relation_size(%s), COALESCE(pg_relation_size(to_regclass(%s)), 0)",
        (TABLA, INDICE),
    ).fetchone()
    return int(fila[0]), int(fila[1])


def construir(conexion) -> int:
    """Reconstruye el GIN y devuelve los ns de CREATE INDEX."""
    conexion.execute(f"DROP INDEX IF EXISTS {INDICE}")
    inicio = time.perf_counter_ns()
    conexion.execute(f"CREATE INDEX {INDICE} ON {TABLA} USING gin (tsv)")
    return time.perf_counter_ns() - inicio


def tokenizar(conexion) -> int:
    """Los ns de recalcular to_tsvector sobre todo el corpus: lo que QuipuDB hace en su build."""
    inicio = time.perf_counter_ns()
    conexion.execute(
        f"SELECT sum(length(to_tsvector('{CONFIGURACION_TEXTO}', texto))) FROM {TABLA}"
    ).fetchone()
    return time.perf_counter_ns() - inicio


def explicar(conexion, sql: str, parametros: tuple) -> dict:
    fila = conexion.execute(
        "EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON) " + sql, parametros
    ).fetchone()
    resultado = fila[0]
    return (json.loads(resultado) if isinstance(resultado, str) else resultado)[0]


def medir_lote(conexion, modo: str, textos: list[str]) -> dict:
    """Una repeticion: el lote con EXPLAIN ANALYZE y despues sin el, desde el cliente."""
    sql = sql_top(modo)
    tiempo_ns = paginas = 0
    devueltos = []
    for texto in textos:
        salida = explicar(conexion, sql, (texto,))
        plan = salida["Plan"]
        if not any(nodo.get("Index Name") == INDICE for nodo in nodos(plan)):
            # Una consulta hecha solo de stopwords no tiene terminos: PostgreSQL
            # ni toca el indice. Las de #171 no son asi, y si una lo fuera se
            # estaria midiendo otra cosa.
            raise RuntimeError(f"el plan no usa {INDICE}: {resumen_plan(plan)}")
        tiempo_ns += round(salida["Execution Time"] * 1_000_000)
        paginas += plan.get("Shared Hit Blocks", 0) + plan.get("Shared Read Blocks", 0)
        devueltos.append(plan["Actual Rows"])
    inicio = time.perf_counter_ns()
    for texto in textos:
        conexion.execute(sql, (texto,)).fetchall()
    cliente_ns = time.perf_counter_ns() - inicio
    return {
        "tiempo_ns": tiempo_ns,
        "paginas": paginas,
        "devueltos": devueltos,
        "cliente_ns": cliente_ns,
    }


def rankings(conexion, modo: str, n: int, consultas) -> list[tuple]:
    """Filas de rankings: el top-10 de cada consulta y los empatados con el 10.o."""
    filas = []
    sql = sql_top(modo, None)
    for consulta in consultas:
        todos = conexion.execute(sql, (consulta.texto,)).fetchall()
        for posicion, (documento, puntaje) in enumerate(recortar(todos, K), start=1):
            filas.append((modo, n, consulta.id, posicion, documento, puntaje))
    return filas


def coincidentes(conexion, modo: str, texto: str) -> int:
    sql = f"SELECT count(*) FROM {TABLA}, {tsquery_sql(modo)} AS q WHERE tsv @@ q"
    return int(conexion.execute(sql, (texto,)).fetchone()[0])


def fila_medicion(identificador, caso, tecnica, operacion, n, n_operaciones, repeticion, valores):
    return {
        "ejecucion": identificador,
        "caso": caso,
        "tecnica": tecnica,
        "operacion": operacion,
        "n_registros": n,
        "n_operaciones": n_operaciones,
        "repeticion": repeticion,
        "estado": "ok",
        **valores,
    }


def _espacio(datos: int, indice: int, *, antes: int) -> dict:
    return {
        "paginas_leidas": 0,
        "paginas_escritas": 0,
        "datos_bytes": datos,
        "indices_bytes": indice,
        "espacio_antes_bytes": antes,
        "espacio_despues_bytes": datos + indice,
    }


def ejecutar(
    conexion,
    datasets,
    consultas,
    *,
    salida: Path,
    calentamientos: int = 1,
    repeticiones: int = 5,
    cantidad: int = CONSULTAS_POR_LONGITUD,
    notas: dict | None = None,
) -> dict[str, Path]:
    """Corre todos los casos y exporta los CSV solo si todo termino bien."""
    if calentamientos < 0 or repeticiones < 1:
        raise ValueError("calentamientos debe ser >= 0 y repeticiones debe ser >= 1")
    lotes = {
        longitud: consultas_de_longitud(consultas, longitud, cantidad) for longitud in LONGITUDES
    }
    identificador = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    entorno = registrar_entorno()
    entorno.update(
        {
            "postgres": conexion.execute("SELECT version()").fetchone()[0],
            **{
                f"postgres.{clave}": conexion.execute(f"SHOW {clave}").fetchone()[0]
                for clave in ("shared_buffers", "work_mem", "block_size", *AJUSTES_SESION)
            },
            "configuracion_texto": CONFIGURACION_TEXTO,
            "ranking": "ts_rank(tsv, q) sin normalizacion; desempate por id",
            "calentamientos": calentamientos,
            "repeticiones": repeticiones,
            "salida": str(salida.resolve()),
            "tiempo_consultas": "suma de Execution Time de EXPLAIN ANALYZE, TIMING OFF",
            "tiempo_construccion": "CREATE INDEX cronometrado desde el cliente",
            "tiempo_tokenizacion": "SELECT sum(length(to_tsvector(...))) desde el cliente",
            "paginas": "shared hit + read del plan, bloques de 8 KiB",
            "paginas_construccion": "no_instrumentadas",
            "enable_seqscan_medicion": "off",
        }
    )
    entorno.update({f"declarado.{clave}": valor for clave, valor in (notas or {}).items()})
    mediciones = []
    filas_rankings = []

    for dataset in datasets:
        n = len(dataset.registros)
        entorno[f"dataset.{n}.ruta"] = str(dataset.ruta)
        entorno[f"dataset.{n}.sha256"] = dataset.sha256
        preparar_tabla(conexion, dataset)
        base, _ = tamanos(conexion)
        tsvector = conexion.execute(f"SELECT sum(pg_column_size(tsv)) FROM {TABLA}").fetchone()[0]
        entorno[f"tabla.{n}.tsvector_bytes"] = int(tsvector)

        for repeticion in range(-calentamientos + 1, repeticiones + 1):
            tiempo_ns = construir(conexion)
            datos, indice = tamanos(conexion)
            tokenizacion_ns = tokenizar(conexion)
            if repeticion > 0:
                mediciones.append(
                    fila_medicion(
                        identificador,
                        "gin_construccion_indice",
                        "gin",
                        "construccion_indice",
                        n,
                        1,
                        repeticion,
                        {"tiempo_ns": tiempo_ns, **_espacio(datos, indice, antes=base)},
                    )
                )
                mediciones.append(
                    fila_medicion(
                        identificador,
                        "gin_tokenizacion",
                        "gin",
                        "tokenizacion",
                        n,
                        1,
                        repeticion,
                        {
                            "tiempo_ns": tokenizacion_ns,
                            **_espacio(datos, indice, antes=datos + indice),
                        },
                    )
                )
        conexion.execute(f"ANALYZE {TABLA}")
        datos, indice = tamanos(conexion)

        for modo in MODOS:
            for longitud, lote in lotes.items():
                textos = [c.texto for c in lote]
                operacion = f"consulta_{nombre_longitud(longitud)}_palabras"
                caso = f"{modo}_{operacion}"

                conexion.execute("SET enable_seqscan = on")
                natural = explicar_sin_analizar(conexion, sql_top(modo), (textos[0],))
                entorno[f"caso.{caso}.{n}.plan_natural"] = resumen_plan(natural)
                conexion.execute("SET enable_seqscan = off")

                clientes = []
                devueltos = None
                for repeticion in range(-calentamientos + 1, repeticiones + 1):
                    medido = medir_lote(conexion, modo, textos)
                    if devueltos is not None and medido["devueltos"] != devueltos:
                        raise RuntimeError(f"{caso}: la respuesta cambio entre repeticiones")
                    devueltos = medido["devueltos"]
                    if repeticion > 0:
                        clientes.append(medido["cliente_ns"])
                        mediciones.append(
                            fila_medicion(
                                identificador,
                                caso,
                                modo,
                                operacion,
                                n,
                                len(textos),
                                repeticion,
                                {
                                    "tiempo_ns": medido["tiempo_ns"],
                                    **_espacio(datos, indice, antes=datos + indice),
                                    "paginas_leidas": medido["paginas"],
                                },
                            )
                        )
                conexion.execute("SET enable_seqscan = on")
                prefijo = f"caso.{caso}.{n}"
                entorno[f"{prefijo}.consultas"] = len(textos)
                entorno[f"{prefijo}.k"] = K
                entorno[f"{prefijo}.devueltos_por_consulta"] = sum(devueltos) / len(devueltos)
                entorno[f"{prefijo}.coincidentes_por_consulta"] = sum(
                    coincidentes(conexion, modo, t) for t in textos
                ) / len(textos)
                entorno[f"{prefijo}.tiempo_cliente_ns_mediana"] = statistics.median(clientes)
            filas_rankings.extend(rankings(conexion, modo, n, consultas))
        conexion.execute(f"DROP TABLE {TABLA}")

    salida.mkdir(parents=True, exist_ok=True)
    rutas = {
        tipo: salida / f"{identificador}_{tipo}.csv"
        for tipo in ("mediciones", "resumen", "entorno", "rankings")
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
    escribir_rankings(rutas["rankings"], filas_rankings)
    return rutas


def explicar_sin_analizar(conexion, sql: str, parametros: tuple) -> dict:
    """El plan que elegiria PostgreSQL, sin ejecutarlo."""
    fila = conexion.execute("EXPLAIN (FORMAT JSON) " + sql, parametros).fetchone()
    resultado = fila[0]
    return (json.loads(resultado) if isinstance(resultado, str) else resultado)[0]["Plan"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dsn", default=DSN_PREDETERMINADO)
    parser.add_argument("--datasets", type=Path, default=SALIDA_PREDETERMINADA)
    parser.add_argument("--salida", type=Path, default=RESULTADOS)
    parser.add_argument("--tamanos", type=int, nargs="+", choices=TAMANOS, default=TAMANOS)
    parser.add_argument("--calentamientos", type=int, default=1)
    parser.add_argument("--repeticiones", type=int, default=5)
    parser.add_argument("--consultas", type=int, default=CONSULTAS_POR_LONGITUD)
    parser.add_argument(
        "--entorno", type=nota_entorno, action="append", default=[], metavar="CLAVE=VALOR"
    )
    args = parser.parse_args()
    if len(set(args.tamanos)) != len(args.tamanos):
        parser.error("no repitas tamanos")
    if not 1 <= args.consultas <= CONSULTAS_POR_LONGITUD:
        parser.error(f"consultas debe estar entre 1 y {CONSULTAS_POR_LONGITUD}")
    try:
        datasets = [leer_documentos(args.datasets / f"documentos_{n}.csv", n) for n in args.tamanos]
        consultas = leer_consultas(args.datasets / "consultas.csv")
        conexion = conectar(args.dsn)
        with conexion:
            rutas = ejecutar(
                conexion,
                datasets,
                consultas,
                salida=args.salida,
                calentamientos=args.calentamientos,
                repeticiones=args.repeticiones,
                cantidad=args.consultas,
                notas=dict(args.entorno),
            )
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"error: {error}\n")
    for tipo, ruta in rutas.items():
        print(f"{tipo}: {ruta}")


if __name__ == "__main__":
    main()
