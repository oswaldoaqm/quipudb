"""Precision@10 y Recall@10 de las tecnicas de texto contra los juicios de #171 (issue #173).

Lee uno o mas CSV de rankings, el top-10 de cada consulta por tecnica y tamano,
y los compara con `relevancia_N.csv`. El calculo es el mismo para todas las
tecnicas porque no sabe de donde salio cada ranking: TF-IDF y BM25 los escribe
`rankings_texto.py` con los bindings, y el GIN de PostgreSQL (#174) escribe el
suyo con el mismo formato. No necesita los bindings ni PostgreSQL.

Definiciones, por consulta, con R los documentos relevantes y T los 10 primeros
del ranking:

    Precision@10 = |T n R| / 10
    Recall@10    = |T n R| / |R|

Si una tecnica devuelve menos de 10 documentos, los que faltan cuentan como no
relevantes: el denominador de Precision@10 sigue siendo 10, como en trec_eval.
Cada metrica se promedia sobre las consultas (macro promedio), por tecnica,
tamano de corpus y longitud de consulta.

Empates en el corte
-------------------

Si el 10.o documento empata en puntaje con el 11.o, cual de los dos entra no lo
decide el ranking sino su desempate: en QuipuDB, el id menor. Con una consulta
de una palabra BM25 empata seguido (el puntaje solo depende del tf y del largo
del documento), y la metrica pasaria a medir el desempate. Por eso el ranking
guarda, ademas del top-10, TODOS los documentos empatados con el 10.o, y se
informan dos versiones:

- la del desempate de la tecnica, la Precision@10 de siempre;
- la de empates promediados (McSherry y Najork, ECIR 2008): el valor esperado
  sobre todos los ordenes posibles dentro del grupo empatado. Si quedan s
  lugares para un grupo de g documentos con r relevantes, ese grupo aporta
  s * r / g aciertos.
"""

from __future__ import annotations

import argparse
import csv
import io
import random
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

if __package__:
    from .cargar_documentos import ConsultaTexto, leer_consultas, leer_relevancia
    from .generar_documentos import LONGITUDES, SALIDA_PREDETERMINADA, SEMILLA
else:
    from cargar_documentos import ConsultaTexto, leer_consultas, leer_relevancia
    from generar_documentos import LONGITUDES, SALIDA_PREDETERMINADA, SEMILLA

K = 10
CABECERA_RANKINGS = ("tecnica", "n_documentos", "consulta", "posicion", "documento", "puntaje")
CABECERA_POR_CONSULTA = (
    "tecnica",
    "n_documentos",
    "longitud",
    "consulta",
    "relevantes",
    "aciertos",
    "aciertos_esperados",
    "empate_en_corte",
)
METRICAS = ("precision_10", "recall_10", "precision_10_empates", "recall_10_empates")
CABECERA_RESUMEN = (
    "tecnica",
    "n_documentos",
    "longitud",
    "consultas",
    *METRICAS,
    "techo_precision_10",
    "consultas_con_acierto",
    "empates_en_corte",
)
CABECERA_PAREADA = (
    "n_documentos",
    "longitud",
    "tecnica_a",
    "tecnica_b",
    "metrica",
    "diferencia_media",
    "consultas_distintas",
    "p_valor",
)
PERMUTACIONES = 10_000


def aciertos(ranking: Sequence[int], relevantes: Iterable[int], k: int = K) -> int:
    """Cuantos de los k primeros son relevantes. Un documento repetido cuenta una vez."""
    return len(set(ranking[:k]) & set(relevantes))


def precision_at_k(ranking: Sequence[int], relevantes: Iterable[int], k: int = K) -> float:
    """|top-k n relevantes| / k. Un ranking con menos de k documentos no achica el denominador."""
    if k < 1:
        raise ValueError("k debe ser >= 1")
    return aciertos(ranking, relevantes, k) / k


def recall_at_k(ranking: Sequence[int], relevantes: Iterable[int], k: int = K) -> float:
    """|top-k n relevantes| / |relevantes|. Sin relevantes no esta definido."""
    relevantes = set(relevantes)
    if not relevantes:
        raise ValueError("Recall@k no esta definido para una consulta sin relevantes")
    if k < 1:
        raise ValueError("k debe ser >= 1")
    return aciertos(ranking, relevantes, k) / len(relevantes)


def techo_precision(relevantes: int, k: int = K) -> float:
    """La Precision@k maxima posible: con menos de k relevantes, ni el ranking
    perfecto llena el top-k."""
    return min(relevantes, k) / k


def recortar(resultados: Sequence[tuple], k: int = K) -> list[tuple]:
    """Los k primeros (documento, puntaje) mas los que empatan con el k-esimo.

    `resultados` tiene que estar ordenado por puntaje de mayor a menor y completo
    al menos hasta el primero que ya no empata: con menos no se sabe donde
    termina el grupo.
    """
    if len(resultados) <= k:
        return list(resultados)
    corte = resultados[k - 1][1]
    fin = k
    while fin < len(resultados) and resultados[fin][1] == corte:
        fin += 1
    return list(resultados[:fin])


@dataclass(frozen=True)
class Ranking:
    """El top-k de una consulta, mas los empatados con el k-esimo: documentos y
    puntajes, del primero al ultimo."""

    documentos: tuple[int, ...]
    puntajes: tuple[float, ...]

    def empate_en_corte(self, k: int = K) -> bool:
        """Si hay empatados con el k-esimo que quedaron afuera."""
        return len(self.documentos) > k

    def aciertos_esperados(self, relevantes: Iterable[int], k: int = K) -> float:
        """Aciertos en el top-k promediando todos los desempates del grupo del corte."""
        relevantes = set(relevantes)
        if len(self.documentos) <= k:
            return float(aciertos(self.documentos, relevantes, k))
        corte = self.puntajes[k - 1]
        arriba = [d for d, p in zip(self.documentos, self.puntajes, strict=True) if p > corte]
        grupo = [d for d, p in zip(self.documentos, self.puntajes, strict=True) if p == corte]
        lugares = k - len(arriba)
        return len(set(arriba) & relevantes) + lugares * len(set(grupo) & relevantes) / len(grupo)


@dataclass(frozen=True)
class Evaluacion:
    tecnica: str
    n_documentos: int
    longitud: str
    consulta: int
    relevantes: int
    aciertos: int
    aciertos_esperados: float
    empate_en_corte: bool

    def metrica(self, nombre: str) -> float:
        """Una de METRICAS para esta consulta."""
        if nombre not in METRICAS:
            raise ValueError(f"metrica desconocida: {nombre}")
        valor = self.aciertos_esperados if nombre.endswith("_empates") else self.aciertos
        return valor / (K if nombre.startswith("precision") else self.relevantes)


def escribir_rankings(ruta: Path, filas: Iterable[tuple]) -> Path:
    """Escribe (tecnica, n, consulta, posicion, documento, puntaje); creacion exclusiva."""
    with ruta.open("x", encoding="utf-8", newline="") as archivo:
        escritor = csv.writer(archivo, lineterminator="\n")
        escritor.writerow(CABECERA_RANKINGS)
        for tecnica, n, consulta, posicion, documento, puntaje in filas:
            # repr conserva todos los digitos: dos puntajes iguales en la
            # tecnica siguen iguales al leerlos, y el empate se detecta.
            escritor.writerow((tecnica, n, consulta, posicion, documento, repr(float(puntaje))))
    return ruta


def _validar(ruta: Path, clave, consulta: int, filas: list[tuple[int, int, float]]) -> None:
    donde = f"{ruta}: {clave[0]} con {clave[1]} documentos, consulta {consulta}"
    if [posicion for posicion, _, _ in filas] != list(range(1, len(filas) + 1)):
        raise ValueError(f"{donde}: las posiciones no son 1..m")
    puntajes = [p for _, _, p in filas]
    if puntajes != sorted(puntajes, reverse=True):
        raise ValueError(f"{donde}: los puntajes no bajan")
    if len({d for _, d, _ in filas}) != len(filas):
        raise ValueError(f"{donde}: un documento aparece dos veces")
    if len(filas) > K and any(p != puntajes[K - 1] for p in puntajes[K:]):
        raise ValueError(f"{donde}: despues del puesto {K} solo van empatados con el {K}.o")


def leer_rankings(ruta: Path) -> dict[tuple[str, int], dict[int, Ranking]]:
    """(tecnica, n) -> consulta -> Ranking, validado."""
    partes: dict[tuple[str, int], dict[int, list[tuple[int, int, float]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    try:
        lector = csv.reader(io.StringIO(ruta.read_text(encoding="utf-8"), newline=""), strict=True)
        if next(lector, None) != list(CABECERA_RANKINGS):
            raise ValueError("la cabecera debe ser " + ",".join(CABECERA_RANKINGS))
        for numero, fila in enumerate(lector, start=2):
            if len(fila) != len(CABECERA_RANKINGS):
                raise ValueError(f"fila {numero}: se requieren {len(CABECERA_RANKINGS)} columnas")
            try:
                n, consulta, posicion, documento = map(int, fila[1:5])
                puntaje = float(fila[5])
            except ValueError as error:
                raise ValueError(f"fila {numero}: valores no numericos") from error
            partes[(fila[0], n)][consulta].append((posicion, documento, puntaje))
    except FileNotFoundError as error:
        raise ValueError(f"rankings inexistentes: {ruta}") from error
    except (ValueError, UnicodeError, csv.Error) as error:
        raise ValueError(f"rankings invalidos {ruta}: {error}") from error

    rankings: dict[tuple[str, int], dict[int, Ranking]] = {}
    for clave, por_consulta in partes.items():
        rankings[clave] = {}
        for consulta, filas in por_consulta.items():
            filas.sort()
            _validar(ruta, clave, consulta, filas)
            rankings[clave][consulta] = Ranking(
                tuple(d for _, d, _ in filas), tuple(p for _, _, p in filas)
            )
    return rankings


def evaluar(
    tecnica: str,
    n_documentos: int,
    rankings: Mapping[int, Ranking],
    consultas: Sequence[ConsultaTexto],
    relevancia: Mapping[int, frozenset[int]],
) -> list[Evaluacion]:
    """Una evaluacion por consulta. Una consulta sin ranking no encontro nada."""
    desconocidas = set(rankings) - {c.id for c in consultas}
    if desconocidas:
        raise ValueError(f"{tecnica} {n_documentos}: consultas desconocidas {sorted(desconocidas)}")
    vacio = Ranking((), ())
    evaluaciones = []
    for consulta in consultas:
        relevantes = relevancia.get(consulta.id)
        if not relevantes:
            raise ValueError(f"la consulta {consulta.id} no tiene juicios de relevancia")
        ranking = rankings.get(consulta.id, vacio)
        evaluaciones.append(
            Evaluacion(
                tecnica,
                n_documentos,
                consulta.longitud,
                consulta.id,
                len(relevantes),
                aciertos(ranking.documentos, relevantes),
                ranking.aciertos_esperados(relevantes),
                ranking.empate_en_corte(),
            )
        )
    return evaluaciones


def resumir(evaluaciones: Iterable[Evaluacion]) -> list[dict]:
    """Promedios por (tecnica, n, longitud), en el orden en que aparecen."""
    grupos: dict[tuple, list[Evaluacion]] = defaultdict(list)
    for e in evaluaciones:
        grupos[(e.tecnica, e.n_documentos, e.longitud)].append(e)
    resumen = []
    for (tecnica, n, longitud), grupo in grupos.items():
        cantidad = len(grupo)
        fila = {"tecnica": tecnica, "n_documentos": n, "longitud": longitud, "consultas": cantidad}
        for metrica in METRICAS:
            fila[metrica] = sum(e.metrica(metrica) for e in grupo) / cantidad
        fila["techo_precision_10"] = sum(techo_precision(e.relevantes) for e in grupo) / cantidad
        fila["consultas_con_acierto"] = sum(e.aciertos > 0 for e in grupo)
        fila["empates_en_corte"] = sum(e.empate_en_corte for e in grupo)
        resumen.append(fila)
    return resumen


def prueba_pareada(
    a: Sequence[float], b: Sequence[float], permutaciones: int = PERMUTACIONES
) -> float:
    """p-valor bilateral de que la media de a - b sea 0, por permutacion de signos.

    Es pareada porque las dos tecnicas responden las mismas consultas: si no hay
    diferencia, cambiar de signo la diferencia de cualquier consulta da una
    muestra igual de probable. Semilla fija: el mismo p-valor en cada corrida.
    """
    if len(a) != len(b) or not a:
        raise ValueError("se requieren dos listas de igual largo, no vacias")
    if permutaciones < 1:
        raise ValueError("permutaciones debe ser >= 1")
    # Las diferencias nulas no cambian con el signo: solo se permutan las demas.
    distintas = [x - y for x, y in zip(a, b, strict=True) if x != y]
    if not distintas:
        return 1.0
    observada = abs(sum(distintas))
    # Para que la propia muestra observada cuente como igual de extrema pese al
    # redondeo de sumar en otro orden.
    tolerancia = 1e-9 * max(1.0, observada)
    rng = random.Random(SEMILLA)
    extremas = 0
    for _ in range(permutaciones):
        suma = sum(d if rng.random() < 0.5 else -d for d in distintas)
        if abs(suma) >= observada - tolerancia:
            extremas += 1
    return (extremas + 1) / (permutaciones + 1)


def comparar(evaluaciones: Iterable[Evaluacion], permutaciones: int = PERMUTACIONES) -> list[dict]:
    """Cada par de tecnicas, por tamano, longitud y metrica, sobre las mismas consultas."""
    por_grupo: dict[tuple, dict[str, dict[int, Evaluacion]]] = defaultdict(dict)
    for e in evaluaciones:
        por_grupo[(e.n_documentos, e.longitud)].setdefault(e.tecnica, {})[e.consulta] = e
    filas = []
    for (n, longitud), por_tecnica in por_grupo.items():
        for tecnica_a, tecnica_b in combinations(sorted(por_tecnica), 2):
            a, b = por_tecnica[tecnica_a], por_tecnica[tecnica_b]
            if set(a) != set(b):
                raise ValueError(f"{tecnica_a} y {tecnica_b} no respondieron las mismas consultas")
            consultas = sorted(a)
            for metrica in METRICAS:
                xa = [a[c].metrica(metrica) for c in consultas]
                xb = [b[c].metrica(metrica) for c in consultas]
                filas.append(
                    {
                        "n_documentos": n,
                        "longitud": longitud,
                        "tecnica_a": tecnica_a,
                        "tecnica_b": tecnica_b,
                        "metrica": metrica,
                        "diferencia_media": (sum(xa) - sum(xb)) / len(consultas),
                        "consultas_distintas": sum(x != y for x, y in zip(xa, xb, strict=True)),
                        "p_valor": prueba_pareada(xa, xb, permutaciones),
                    }
                )
    return filas


def evaluar_archivos(rutas: Sequence[Path], datasets: Path) -> list[Evaluacion]:
    """Evalua todos los rankings de los archivos contra los juicios de su tamano."""
    consultas = leer_consultas(datasets / "consultas.csv")
    relevancias: dict[int, dict[int, frozenset[int]]] = {}
    evaluaciones: list[Evaluacion] = []
    vistos: set[tuple[str, int]] = set()
    for ruta in rutas:
        for (tecnica, n), rankings in sorted(leer_rankings(ruta).items()):
            if (tecnica, n) in vistos:
                raise ValueError(f"{tecnica} con {n} documentos aparece dos veces")
            vistos.add((tecnica, n))
            if n not in relevancias:
                relevancias[n] = leer_relevancia(datasets / f"relevancia_{n}.csv")
            evaluaciones.extend(evaluar(tecnica, n, rankings, consultas, relevancias[n]))
    return evaluaciones


def _escribir(ruta: Path, cabecera: tuple[str, ...], filas: Iterable[dict]) -> Path:
    with ruta.open("x", encoding="utf-8", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=cabecera, lineterminator="\n")
        escritor.writeheader()
        escritor.writerows(filas)
    return ruta


def tablas(resumen: Sequence[dict], pareada: Sequence[dict]) -> list[str]:
    """Las tablas Markdown del informe."""
    orden = {longitud: i for i, longitud in enumerate(LONGITUDES)}
    lineas = [
        (
            "| Tecnica | N | Palabras | P@10 | P@10 empates prom. | Techo P@10 | R@10 "
            "| R@10 empates prom. | Con acierto | Empates en el corte |"
        ),
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for f in sorted(resumen, key=lambda f: (f["tecnica"], f["n_documentos"], orden[f["longitud"]])):
        lineas.append(
            f"| {f['tecnica']} | {f['n_documentos']:,} | {f['longitud']} | "
            f"{f['precision_10']:.3f} | {f['precision_10_empates']:.3f} | "
            f"{f['techo_precision_10']:.3f} | {f['recall_10']:.3f} | "
            f"{f['recall_10_empates']:.3f} | {f['consultas_con_acierto']}/{f['consultas']} | "
            f"{f['empates_en_corte']} |".replace(",", " ")
        )
    if pareada:
        lineas += [
            "",
            "| N | Palabras | A - B | Metrica | Diferencia media | Consultas distintas | p |",
            "|---:|---|---|---|---:|---:|---:|",
        ]
        for f in sorted(
            pareada,
            key=lambda f: (f["n_documentos"], orden[f["longitud"]], METRICAS.index(f["metrica"])),
        ):
            lineas.append(
                f"| {f['n_documentos']:,} | {f['longitud']} | {f['tecnica_a']} - {f['tecnica_b']} "
                f"| {f['metrica']} | {f['diferencia_media']:+.3f} | {f['consultas_distintas']} "
                f"| {f['p_valor']:.3f} |".replace(",", " ")
            )
    return lineas


def main() -> None:
    """Evalua los rankings, escribe tres CSV junto al primero e imprime las tablas."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("rankings", type=Path, nargs="+", help="CSV de rankings_texto.py o #174")
    parser.add_argument(
        "--datasets",
        type=Path,
        default=SALIDA_PREDETERMINADA,
        help="carpeta con consultas.csv y relevancia_N.csv (por defecto: benchmarks/datasets)",
    )
    parser.add_argument("--permutaciones", type=int, default=PERMUTACIONES)
    args = parser.parse_args()
    if args.permutaciones < 1:
        parser.error("permutaciones debe ser >= 1")
    try:
        evaluaciones = evaluar_archivos(args.rankings, args.datasets)
    except ValueError as error:
        parser.exit(1, f"error: {error}\n")
    resumen = resumir(evaluaciones)
    pareada = comparar(evaluaciones, args.permutaciones)

    nombre = args.rankings[0].name.removesuffix(".csv").removesuffix("_rankings")
    base = args.rankings[0].with_name(nombre)
    por_consulta = (
        {
            "tecnica": e.tecnica,
            "n_documentos": e.n_documentos,
            "longitud": e.longitud,
            "consulta": e.consulta,
            "relevantes": e.relevantes,
            "aciertos": e.aciertos,
            "aciertos_esperados": e.aciertos_esperados,
            "empate_en_corte": e.empate_en_corte,
        }
        for e in evaluaciones
    )
    salidas = {
        Path(f"{base}_relevancia_por_consulta.csv"): (CABECERA_POR_CONSULTA, por_consulta),
        Path(f"{base}_relevancia_resumen.csv"): (CABECERA_RESUMEN, resumen),
        Path(f"{base}_relevancia_pareada.csv"): (CABECERA_PAREADA, pareada),
    }
    # Como en el banco, un resultado existente nunca se reemplaza; se comprueba
    # antes de escribir para no dejar la mitad.
    existentes = [str(ruta) for ruta in salidas if ruta.exists()]
    if existentes:
        parser.exit(1, f"error: ya existen {', '.join(existentes)}\n")
    rutas = [_escribir(ruta, *contenido) for ruta, contenido in salidas.items()]
    # Las tablas van a la salida estandar, para pegarlas en el informe; las
    # rutas, a la de errores.
    origen = ", ".join(ruta.name for ruta in args.rankings)
    print(f"<!-- generado por relevancia_texto.py desde {origen} -->")
    print("\n".join(tablas(resumen, pareada)))
    for ruta in rutas:
        print(ruta, file=sys.stderr)


if __name__ == "__main__":
    main()
