"""Reproduce #190 en procesos secuenciales, con informes de tiempo, RSS y verificacion."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from unittest.mock import patch

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarks.scripts.multimedia_dataset import (  # noqa: E402
    MODALIDADES, SALIDA, SEMILLA, sha256,
)
from benchmarks.scripts.verificar_multimedia import archivos_de, verificar  # noqa: E402

ETAPAS = ("generar", "precalcular", "cargar", "verificar")


def rss_max_bytes() -> int:
    if sys.platform != "darwin" and not sys.platform.startswith("linux"):
        raise RuntimeError("la medicion de RSS de este ejecutor requiere macOS o Linux")
    import resource

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(rss if sys.platform == "darwin" else rss * 1024)


def guardar(ruta: Path, datos: dict) -> None:
    with ruta.open("x", encoding="utf-8") as archivo:
        archivo.write(json.dumps(datos, indent=2) + "\n")


def comprobar_recursos(args) -> None:
    if args.max_rss_mib and rss_max_bytes() > args.max_rss_mib * 2**20:
        raise RuntimeError("se supero el limite de RSS; se detiene esta etapa")
    if shutil.disk_usage(args.salida).free < args.min_libre_mib * 2**20:
        raise RuntimeError("espacio libre insuficiente; se detiene esta etapa")


def ejecutar_etapa(args) -> dict:
    from benchmarks.scripts import cargar_multimedia, generar_multimedia
    from engine.multimedia import features, kmeans

    n = args.tamanos[0]
    raiz = args.salida / f"{args.modalidad}_{n}"
    catalogo = args.salida / "catalogos" / raiz.name
    extras = {}
    llamadas = 0
    comprobar = lambda: comprobar_recursos(args)  # noqa: E731
    with ExitStack() as cambios:
        if args.etapa in ("generar", "precalcular"):
            modulo = generar_multimedia if args.etapa == "generar" else features
            nombre = ("imagen" if args.modalidad == "imagenes" else "audio")
            if args.etapa == "precalcular":
                nombre = "describe"
            original = getattr(modulo, nombre)

            def contar(*pos, **kwargs):
                nonlocal llamadas
                resultado = original(*pos, **kwargs)
                llamadas += 1
                if llamadas % 100 == 0:
                    comprobar()
                if llamadas % 5000 == 0:
                    print(f"{args.etapa}: {llamadas} objetos/extracciones", flush=True)
                return resultado

            cambios.enter_context(patch.object(modulo, nombre, contar))
        if args.etapa == "cargar":
            import quipudb_native as nativo
            from engine.executor import QueryProcessor

            def prohibido(*pos, **kwargs):
                raise RuntimeError("la carga intento extraer, entrenar, indexar o materializar")

            for modulo, nombres in (
                (features, ("extract_features", "describe", "build_vocabulary")),
                (kmeans, ("build_codebook",)),
                (nativo.Database, ("create_index", "create_ivf_index", "create_hnsw_index")),
                (nativo.TableFile, ("scan", "scan_with_rids")),
            ):
                for nombre in nombres:
                    cambios.enter_context(patch.object(modulo, nombre, prohibido))
            insertar = QueryProcessor.load_media_batch
            extras.update(lotes=0, filas=0, segundos_lotes=0.0)

            def lote(self, tabla, rows, **kwargs):
                if len(rows) > args.lote:
                    raise RuntimeError("el lote excede el limite configurado")
                inicio = time.perf_counter()
                resultado = insertar(self, tabla, rows, **kwargs)
                extras["segundos_lotes"] += time.perf_counter() - inicio
                extras["lotes"] += 1
                extras["filas"] += resultado.inserted
                comprobar()
                return resultado

            cambios.enter_context(patch.object(QueryProcessor, "load_media_batch", lote))
        comprobar()
        inicio = time.perf_counter()
        if args.etapa == "generar":
            generar_multimedia.generar(args.salida, args.modalidad, n, args.semilla)
        elif args.etapa == "precalcular":
            generar_multimedia.precalcular(raiz, k=args.k, muestra=args.muestra)
        elif args.etapa == "cargar":
            if cargar_multimedia.cargar(raiz, catalogo, lote=args.lote) != n:
                raise RuntimeError("la carga no completo todos los registros")
            extras["extraccion_e_indices_bloqueados"] = True
        else:
            extras = verificar(raiz, catalogo, consultas=args.consultas, comprobar=comprobar)
        segundos = time.perf_counter() - inicio
        comprobar()
    return dict(extras, modalidad=args.modalidad, cantidad=n, etapa=args.etapa,
                segundos=segundos, rss_max_bytes=rss_max_bytes(), semilla=args.semilla, k=args.k)


def ejecutar(args) -> None:
    rss_max_bytes()  # Comprobar plataforma antes de crear carpetas.
    modalidades = MODALIDADES if args.modalidad == "ambas" else (args.modalidad,)
    for modulo in ("quipudb_native", *(["cv2"] if "imagenes" in modalidades else []),
                   *(["soundfile"] if "audio" in modalidades else [])):
        importlib.import_module(modulo)
    destinos = [args.salida / f"{m}_{n}" for m in modalidades for n in args.tamanos]
    destinos += [args.salida / "catalogos" / d.name for d in destinos]
    if any(args.reportes.is_relative_to(d) or d.is_relative_to(args.reportes) for d in destinos):
        raise ValueError("los reportes deben estar separados de los datasets y catalogos")
    for destino in [args.reportes, *destinos]:
        if destino.exists():
            raise ValueError(f"{destino} ya existe; no se sobrescribe")
    for anterior in args.preservar:
        if not anterior.is_dir():
            raise ValueError(f"carpeta a preservar inexistente: {anterior}")
        if any(d.is_relative_to(anterior) or anterior.is_relative_to(d)
               for d in [args.reportes, *destinos]):
            raise ValueError("las carpetas a preservar deben ser ajenas a los destinos nuevos")
    existente = args.salida
    while not existente.exists():
        existente = existente.parent
    if shutil.disk_usage(existente).free < args.min_libre_mib * 2**20:
        raise RuntimeError("espacio libre insuficiente antes de comenzar")
    args.salida.mkdir(parents=True, exist_ok=True)
    args.reportes.mkdir(parents=True)
    guardar(args.reportes / "configuracion.json", dict(
        modalidad=args.modalidad, tamanos=args.tamanos, semilla=args.semilla, k=args.k,
        muestra=args.muestra, lote=args.lote, consultas=args.consultas,
        max_rss_mib=args.max_rss_mib, min_libre_mib=args.min_libre_mib,
        disco_inicial=shutil.disk_usage(args.salida)._asdict(),
    ))
    with (args.reportes / "anteriores.jsonl").open("x", encoding="utf-8") as salida:
        for anterior in args.preservar:
            for archivo in archivos_de(anterior):
                salida.write(json.dumps([str(archivo), sha256(archivo)]) + "\n")
    for modalidad in modalidades:
        for cantidad in args.tamanos:
            for etapa in ETAPAS:
                nombre = f"{modalidad}_{cantidad}_{etapa}"
                print(nombre, flush=True)
                comando = [sys.executable, "-B", str(Path(__file__).resolve()),
                           "--etapa", etapa, "--modalidad", modalidad,
                           "--tamanos", str(cantidad), "--salida", str(args.salida),
                           "--reportes", str(args.reportes)]
                for clave in ("semilla", "k", "muestra", "lote", "consultas",
                              "max_rss_mib", "min_libre_mib"):
                    comando += ["--" + clave.replace("_", "-"), str(getattr(args, clave))]
                with (args.reportes / f"{nombre}.log").open("x", encoding="utf-8") as log:
                    with subprocess.Popen(comando, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, text=True) as proceso:
                        for linea in proceso.stdout:
                            log.write(linea)
                            log.flush()
                            print(linea, end="", flush=True)
                        if proceso.wait() != 0:
                            raise RuntimeError(f"fallo en {nombre}; consulte su log")
    cantidad = 0
    with (args.reportes / "anteriores.jsonl").open(encoding="utf-8") as entrada:
        for linea in entrada:
            ruta, huella = json.loads(linea)
            if sha256(Path(ruta)) != huella:
                raise RuntimeError(f"archivo anterior modificado: {ruta}")
            cantidad += 1
    guardar(args.reportes / "final.json", dict(archivos_anteriores_inalterados=cantidad,
                                               disco_final=shutil.disk_usage(args.salida)._asdict()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modalidad", choices=(*MODALIDADES, "ambas"), default="ambas")
    parser.add_argument("--tamanos", type=int, nargs="+", default=[1000])
    parser.add_argument("--salida", type=Path, default=SALIDA)
    parser.add_argument("--reportes", type=Path, required=True, help="carpeta nueva de JSON y logs")
    parser.add_argument("--semilla", type=int, default=SEMILLA)
    parser.add_argument("--k", type=int, default=64)
    parser.add_argument("--muestra", type=int, default=8192)
    parser.add_argument("--lote", type=int, default=32)
    parser.add_argument("--consultas", type=int, default=20)
    parser.add_argument("--max-rss-mib", type=int, default=1024, help="0 desactiva el control de RSS")
    parser.add_argument("--min-libre-mib", type=int, default=2048)
    parser.add_argument("--preservar", type=Path, nargs="*", default=[])
    parser.add_argument("--etapa", choices=ETAPAS, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if (any(not 1 <= n <= 100000 for n in args.tamanos)
            or len(set(args.tamanos)) != len(args.tamanos) or args.semilla < 0
            or not 2 <= args.k <= 256 or args.muestra < args.k or not 1 <= args.lote <= 256
            or min(args.consultas, args.max_rss_mib, args.min_libre_mib) < 0):
            raise ValueError("parametros fuera de rango o tamanos repetidos")
        args.salida = args.salida.resolve()
        args.reportes = args.reportes.resolve()
        args.preservar = [p.resolve() for p in args.preservar]
        if args.etapa:
            if args.modalidad == "ambas" or len(args.tamanos) != 1:
                raise ValueError("una etapa requiere una modalidad y un tamano")
            resultado = ejecutar_etapa(args)
            nombre = f"{args.modalidad}_{args.tamanos[0]}_{args.etapa}.json"
            guardar(args.reportes / nombre, resultado)
            print(json.dumps(resultado), flush=True)
        else:
            ejecutar(args)
    except (ValueError, OSError, ImportError, RuntimeError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    main()
