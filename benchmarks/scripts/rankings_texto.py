"""Escribe los rankings de TF-IDF y BM25 de QuipuDB para medir su relevancia (#173).

Carga cada corpus de #171 con su indice invertido, corre las 300 consultas con
los dos rankings y guarda, por consulta, el top-10 con sus puntajes y, si el
10.o empata con los siguientes, todos los empatados: `relevancia_texto.py` los
necesita para promediar el desempate. El GIN de PostgreSQL (#174) escribe el
mismo formato, asi las tres tecnicas pasan por el mismo calculo.

No mide tiempos (eso es #172): los rankings son deterministas, salen iguales
en cada corrida sobre los mismos CSV.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

if __package__:
    from .banco_pruebas import CABECERA_ENTORNO, RESULTADOS, escribir_csv, registrar_entorno
    from .cargar_documentos import (
        ConsultaTexto,
        DatasetDocumentos,
        cargar_quipudb,
        importar_bindings,
        leer_consultas,
        leer_documentos,
    )
    from .casos_texto import RANKINGS
    from .generar_documentos import SALIDA_PREDETERMINADA, TAMANOS
    from .relevancia_texto import K, escribir_rankings, recortar
else:
    from banco_pruebas import CABECERA_ENTORNO, RESULTADOS, escribir_csv, registrar_entorno
    from cargar_documentos import (
        ConsultaTexto,
        DatasetDocumentos,
        cargar_quipudb,
        importar_bindings,
        leer_consultas,
        leer_documentos,
    )
    from casos_texto import RANKINGS
    from generar_documentos import SALIDA_PREDETERMINADA, TAMANOS
    from relevancia_texto import K, escribir_rankings, recortar


def rankings_quipudb(
    nativo, dataset: DatasetDocumentos, consultas: Sequence[ConsultaTexto], temporal: Path
) -> Iterator[tuple]:
    """Filas (tecnica, n, consulta, posicion, documento, puntaje) de los dos rankings.

    `documento` es el id del CSV (la clave de la tabla), no el RID: es lo que
    usan los juicios de relevancia, y no depende de donde quedo cada registro.
    """
    n = len(dataset.registros)
    db = nativo.Database(temporal / "catalogo.txt")
    tabla, indice = cargar_quipudb(nativo, db, dataset)
    try:
        for tecnica in RANKINGS:
            ranking = nativo.TextRanking.TF_IDF if tecnica == "tf_idf" else nativo.TextRanking.BM25
            for consulta in consultas:
                # Todos y no solo 10: hace falta ver donde termina el grupo
                # empatado con el 10.o.
                todos = indice.search_text(consulta.texto, ranking, nativo.ALL_RESULTS)
                for posicion, (rid, puntaje) in enumerate(recortar(todos, K), start=1):
                    yield tecnica, n, consulta.id, posicion, tabla.read(rid)[0], puntaje
    finally:
        db.close("documentos")


def main() -> None:
    """Genera `<id>_rankings.csv` y `<id>_entorno.csv` en la carpeta de resultados."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--datasets", type=Path, default=SALIDA_PREDETERMINADA)
    parser.add_argument("--salida", type=Path, default=RESULTADOS)
    parser.add_argument("--tamanos", type=int, nargs="+", choices=TAMANOS, default=TAMANOS)
    args = parser.parse_args()
    if len(set(args.tamanos)) != len(args.tamanos):
        parser.error("no repitas tamanos")
    try:
        datasets = [leer_documentos(args.datasets / f"documentos_{n}.csv", n) for n in args.tamanos]
        consultas = leer_consultas(args.datasets / "consultas.csv")
        nativo = importar_bindings()
    except (ValueError, RuntimeError) as error:
        parser.exit(1, f"error: {error}\n")

    identificador = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    entorno = registrar_entorno()
    entorno.update({"proposito": "rankings_relevancia_texto", "k": K, "incluye": "empatados_en_k"})
    filas = []
    for dataset in datasets:
        n = len(dataset.registros)
        entorno[f"dataset.{n}.ruta"] = str(dataset.ruta)
        entorno[f"dataset.{n}.sha256"] = dataset.sha256
        with TemporaryDirectory(prefix="quipudb_rankings_") as temporal:
            filas.extend(rankings_quipudb(nativo, dataset, consultas, Path(temporal)))

    args.salida.mkdir(parents=True, exist_ok=True)
    rankings = escribir_rankings(args.salida / f"{identificador}_rankings.csv", filas)
    entorno_ruta = args.salida / f"{identificador}_entorno.csv"
    escribir_csv(
        entorno_ruta,
        CABECERA_ENTORNO,
        [
            {"ejecucion": identificador, "clave": clave, "valor": valor}
            for clave, valor in sorted(entorno.items())
        ],
    )
    print(f"rankings: {rankings}")
    print(f"entorno: {entorno_ruta}")


if __name__ == "__main__":
    main()
