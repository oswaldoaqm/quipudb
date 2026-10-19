"""Convierte una corrida de `--suite texto` (#172) en las tablas Markdown del informe.

Lee exclusivamente `<id>_resumen.csv` y `<id>_entorno.csv`; no ejecuta
benchmarks ni importa bindings. Imprime las tablas por salida estandar para
pegarlas en `comparacion_tfidf_bm25.md`, de modo que ningun numero del informe
se copie a mano.
"""

from __future__ import annotations

import argparse
from pathlib import Path

if __package__:
    from .banco_pruebas import RESULTADOS
    from .casos_texto import RANKINGS, nombre_longitud
    from .generar_documentos import LONGITUDES, TAMANOS
    from .resumir_espacial import leer, paginas_por_consulta, por_consulta_ms
else:
    from banco_pruebas import RESULTADOS
    from casos_texto import RANKINGS, nombre_longitud
    from generar_documentos import LONGITUDES, TAMANOS
    from resumir_espacial import leer, paginas_por_consulta, por_consulta_ms

CONSTRUCCION = "spimi_construccion_indice"


def _ms(valor: float) -> str:
    return f"{valor:.3f}" if valor < 10 else f"{valor:.1f}"


def _miles(texto: str) -> str:
    """Separador de miles con espacio, como el resto de los informes."""
    return texto.replace(",", " ")


def dispersion(fila: dict) -> float:
    """(max - min) / mediana del tiempo, en %: cuanto se movieron las repeticiones."""
    return (
        (int(fila["max_tiempo_ns"]) - int(fila["min_tiempo_ns"]))
        / float(fila["mediana_tiempo_ns"])
        * 100
    )


def tabla_construccion(resumen, entorno) -> list[str]:
    lineas = [
        (
            "| N | Construccion ms | Dispersion | Pag. leidas | Pag. escritas | Terminos "
            "| Heap KiB | Indice KiB | Indice / Heap |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for n in TAMANOS:
        fila = resumen.get((CONSTRUCCION, n))
        if fila is None:
            continue
        heap = float(fila["mediana_datos_bytes"])
        indice = float(fila["mediana_indices_bytes"])
        terminos = int(entorno[f"caso.{CONSTRUCCION}.{n}.terminos"])
        lineas.append(
            _miles(
                f"| {n:,} | {float(fila['mediana_tiempo_ns']) / 1e6:,.1f} | "
                f"{dispersion(fila):.0f} % | {float(fila['mediana_paginas_leidas']):,.0f} | "
                f"{float(fila['mediana_paginas_escritas']):,.0f} | {terminos:,} | "
                f"{heap / 1024:,.0f} | {indice / 1024:,.0f} | {indice / heap:.2f} |"
            )
        )
    return lineas


def tabla_memoria(resumen, entorno) -> list[str]:
    lineas = [
        (
            "| N | Bloques SPIMI | Pasadas de mezcla | Pico del diccionario KiB | Presupuesto KiB "
            "| Lectores de la mezcla KiB | RIDs KiB |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for n in TAMANOS:
        if (CONSTRUCCION, n) not in resumen:
            continue

        def valor(clave: str, n: int = n) -> int:
            return int(entorno[f"caso.{CONSTRUCCION}.{n}.{clave}"])

        lineas.append(
            _miles(
                f"| {n:,} | {valor('bloques_spimi')} | {valor('pasadas_mezcla')} | "
                f"{valor('memoria_pico_spimi_bytes') / 1024:,.1f} | "
                f"{valor('memoria_presupuesto_spimi_bytes') / 1024:,.0f} | "
                f"{valor('memoria_lectores_mezcla_bytes') / 1024:,.0f} | "
                f"{valor('memoria_rids_bytes') / 1024:,.0f} |"
            )
        )
    return lineas


def tabla_consultas(resumen, entorno) -> list[str]:
    lineas = [
        (
            "| Palabras | N | TF-IDF ms | BM25 ms | BM25 / TF-IDF | Dispersion TF-IDF / BM25 "
            "| Pag./consulta | Terminos | Coincidentes |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for longitud in LONGITUDES:
        operacion = f"consulta_{nombre_longitud(longitud)}_palabras"
        for n in TAMANOS:
            filas = {r: resumen.get((f"{r}_{operacion}", n)) for r in RANKINGS}
            if None in filas.values():
                continue
            tfidf, bm25 = filas["tf_idf"], filas["bm25"]
            clave = f"caso.bm25_{operacion}.{n}."
            lineas.append(
                _miles(
                    f"| {longitud} | {n:,} | {_ms(por_consulta_ms(tfidf))} | "
                    f"{_ms(por_consulta_ms(bm25))} | "
                    f"{por_consulta_ms(bm25) / por_consulta_ms(tfidf):.2f} | "
                    f"{dispersion(tfidf):.0f} % / {dispersion(bm25):.0f} % | "
                    f"{paginas_por_consulta(bm25):.1f} | "
                    f"{float(entorno[clave + 'terminos_por_consulta']):.2f} | "
                    f"{float(entorno[clave + 'coincidentes_por_consulta']):,.0f} |"
                )
            )
    return lineas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ejecucion", help="identificador <fecha>_<uuid> de la corrida")
    parser.add_argument("--entrada", type=Path, default=RESULTADOS)
    args = parser.parse_args()
    resumen, entorno = leer(args.ejecucion, args.entrada)

    grupos = {
        "Construccion y espacio (comunes a TF-IDF y BM25)": tabla_construccion(resumen, entorno),
        "Memoria de la construccion": tabla_memoria(resumen, entorno),
        "Consultas, top-10": tabla_consultas(resumen, entorno),
    }
    print(f"<!-- generado por resumir_texto.py desde {args.ejecucion} -->")
    for titulo, lineas in grupos.items():
        print(f"\n#### {titulo}\n")
        print("\n".join(lineas))


if __name__ == "__main__":
    main()
