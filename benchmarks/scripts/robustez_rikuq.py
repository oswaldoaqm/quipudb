"""Robustez de Rikuq: cuanto maltrato aguanta el reconocimiento facial.

``evaluar_rikuq.py`` mide si acierta y cuanto tarda con fotos limpias. Esto
mide otra cosa: **que pasa cuando la foto no es de catalogo**. Una foto de
celular llega girada, a contraluz, movida, comprimida por WhatsApp o con la
cara pequena, y el informe ya avisaba de ese limite:

> Son de LFW, fotos de prensa bien iluminadas y frontales [...] La precision
> sobre las fotos del equipo, tomadas con un celular, puede ser distinta.

Cada caso toma una foto, la degrada y compara su vector con el de la original.
Se reportan tres cosas por caso, que no son la misma:

- **detectadas**: si YuNet llego a encontrar una cara;
- **distancia**: cuanto se movio el vector respecto del original;
- **reconocidas**: cuantas quedaron dentro del umbral, sobre el total.

La diferencia entre "no detecta" y "detecta mal" importa. Lo primero es el
fallo seguro: el sistema dice que no hay cara. Lo segundo entrega un nombre
con un vector que no vale, y nadie se entera.

Dos pasos, desde ``rikuq/server``::

    python ../../benchmarks/scripts/robustez_rikuq.py medir
    python ../../benchmarks/scripts/robustez_rikuq.py medir --sin-orientar
    python ../../benchmarks/scripts/robustez_rikuq.py tabla

``--sin-orientar`` usa ``detectar`` en lugar de ``detectar_orientado``, que es
como se comportaba el pipeline antes del barrido de orientaciones. Sirve para
medir el antes y el despues con la misma vara.

Necesita los modelos (``python -m app.rostros.modelos``) y LFW bajado (#301).
No necesita el motor levantado: compara vectores a fuerza bruta con numpy,
igual que ``app.consulta.umbral``, para medir el pipeline sin mezclarlo con lo
que HNSW pueda o no encontrar.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

RAIZ = Path(__file__).resolve().parents[2]
RESULTADOS = RAIZ / "benchmarks" / "results"
LFW = RAIZ / "rikuq" / "datos" / "lfw"

IDENTIDADES = 40
"""Cuantas personas distintas entran a la bateria.

Con menos, un caso dificil se decide por dos o tres fotos y el porcentaje
salta. Con 40, un punto de la tabla vale 2.5%.
"""

SEMILLA = 7
"""Las identidades se eligen al azar pero reproducible: dos corridas tienen
que poder compararse entre si, y con semilla libre no se podrian."""


# --- degradaciones ------------------------------------------------------------
#
# Son las que ya aplica rikuq/server/tests/test_robustez.py, con mas niveles,
# mas oclusion y mas resolucion. Cada una imita algo que pasa de verdad al
# tomar una foto, no una deformacion abstracta.


def girar_lienzo(imagen: np.ndarray, grados: int) -> np.ndarray:
    """Gira sobre un lienzo cuadrado para que no se salga ninguna esquina."""

    alto, ancho = imagen.shape[:2]
    diagonal = int(np.ceil(np.hypot(alto, ancho)))
    lienzo = np.zeros((diagonal, diagonal, *imagen.shape[2:]), imagen.dtype)
    arriba, izquierda = (diagonal - alto) // 2, (diagonal - ancho) // 2
    lienzo[arriba : arriba + alto, izquierda : izquierda + ancho] = imagen
    matriz = cv2.getRotationMatrix2D((diagonal / 2, diagonal / 2), grados, 1.0)
    return cv2.warpAffine(lienzo, matriz, (diagonal, diagonal))


def brillo(imagen: np.ndarray, factor: float) -> np.ndarray:
    return np.clip(imagen.astype(np.float32) * factor, 0, 255).astype(np.uint8)


def gamma(imagen: np.ndarray, valor: float) -> np.ndarray:
    tabla = np.array([((i / 255.0) ** (1.0 / valor)) * 255 for i in range(256)])
    return cv2.LUT(imagen, tabla.astype(np.uint8))


def contraste(imagen: np.ndarray, factor: float) -> np.ndarray:
    media = imagen.mean()
    return np.clip((imagen.astype(np.float32) - media) * factor + media, 0, 255).astype(np.uint8)


def contraluz(imagen: np.ndarray) -> np.ndarray:
    """Una ventana a un lado: media cara iluminada y media en sombra."""

    rampa = np.linspace(0.25, 1.25, imagen.shape[1], dtype=np.float32)
    return np.clip(imagen.astype(np.float32) * rampa[None, :, None], 0, 255).astype(np.uint8)


def movimiento(imagen: np.ndarray, pixeles: int) -> np.ndarray:
    """Barrido horizontal: la camara en vivo entrega cuadros movidos."""

    nucleo = np.zeros((pixeles, pixeles), np.float32)
    nucleo[pixeles // 2, :] = 1.0 / pixeles
    return cv2.filter2D(imagen, -1, nucleo)


def ruido(imagen: np.ndarray, sigma: float) -> np.ndarray:
    """Poca luz en la webcam se traduce en ruido."""

    sucio = imagen.astype(np.float32) + np.random.normal(0, sigma, imagen.shape)
    return np.clip(sucio, 0, 255).astype(np.uint8)


def jpeg(imagen: np.ndarray, calidad: int) -> np.ndarray:
    _, buffer = cv2.imencode(".jpg", imagen, [cv2.IMWRITE_JPEG_QUALITY, calidad])
    return cv2.imdecode(buffer, cv2.IMREAD_COLOR)


def escala(imagen: np.ndarray, factor: float) -> np.ndarray:
    """Reduce y vuelve a ampliar: la cara lejos, con menos pixeles reales."""

    alto, ancho = imagen.shape[:2]
    chica = cv2.resize(imagen, (max(8, int(ancho * factor)), max(8, int(alto * factor))))
    return cv2.resize(chica, (ancho, alto), interpolation=cv2.INTER_NEAREST)


def banda(imagen: np.ndarray, desde: float, hasta: float) -> np.ndarray:
    """Tapa una franja horizontal, en fraccion de la altura de la imagen."""

    tapada = imagen.copy()
    alto = imagen.shape[0]
    tapada[int(alto * desde) : int(alto * hasta), :] = 0
    return tapada


def parche(imagen: np.ndarray, fraccion: float) -> np.ndarray:
    tapada = imagen.copy()
    alto, ancho = imagen.shape[:2]
    lado = int(min(alto, ancho) * fraccion)
    arriba = random.randint(0, max(0, alto - lado))
    izquierda = random.randint(0, max(0, ancho - lado))
    tapada[arriba : arriba + lado, izquierda : izquierda + lado] = 0
    return tapada


def gris(imagen: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(cv2.cvtColor(imagen, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)


def tinte(imagen: np.ndarray, canal: int, factor: float) -> np.ndarray:
    tenido = imagen.astype(np.float32)
    tenido[:, :, canal] *= factor
    return np.clip(tenido, 0, 255).astype(np.uint8)


Degradacion = Callable[[np.ndarray], np.ndarray]


def casos() -> tuple[tuple[str, str, Degradacion], ...]:
    """Los casos de la bateria, como (grupo, nombre, funcion)."""

    lista: list[tuple[str, str, Degradacion]] = []
    for grados in (15, 30, 45, 50, 60, 75, 90, 135, 180):
        lista.append(("giro", f"{grados} grados", lambda im, g=grados: girar_lienzo(im, g)))
    lista.append(("giro", "espejo horizontal", lambda im: cv2.flip(im, 1)))
    for factor in (0.25, 0.4, 0.6, 1.4, 1.8, 2.4):
        lista.append(("iluminacion", f"brillo x{factor}", lambda im, f=factor: brillo(im, f)))
    for valor in (0.4, 0.6, 1.6, 2.5):
        lista.append(("iluminacion", f"gamma {valor}", lambda im, v=valor: gamma(im, v)))
    for factor in (0.3, 0.5, 1.8):
        lista.append(("iluminacion", f"contraste x{factor}", lambda im, f=factor: contraste(im, f)))
    lista.append(("iluminacion", "contraluz lateral", contraluz))
    for lado in (3, 7, 11, 15, 21):
        lista.append(
            ("nitidez", f"desenfoque {lado}px", lambda im, k=lado: cv2.GaussianBlur(im, (k, k), 0))
        )
    for lado in (9, 15, 25):
        lista.append(("nitidez", f"movimiento {lado}px", lambda im, k=lado: movimiento(im, k)))
    for sigma in (10, 25, 40, 60):
        lista.append(("nitidez", f"ruido sigma {sigma}", lambda im, s=sigma: ruido(im, s)))
    for calidad in (50, 30, 15, 8, 4):
        lista.append(("compresion", f"jpeg calidad {calidad}", lambda im, q=calidad: jpeg(im, q)))
    for factor in (0.5, 0.3, 0.2, 0.12, 0.08):
        lista.append(
            ("resolucion", f"{int(factor * 100)}% del tamano", lambda im, f=factor: escala(im, f))
        )
    lista.append(("oclusion", "gafas de sol", lambda im: banda(im, 0.30, 0.48)))
    lista.append(("oclusion", "mascarilla", lambda im: banda(im, 0.58, 0.95)))
    lista.append(("oclusion", "gorra sobre la frente", lambda im: banda(im, 0.0, 0.28)))
    lista.append(("oclusion", "parche opaco 25%", lambda im: parche(im, 0.25)))
    lista.append(("oclusion", "parche opaco 40%", lambda im: parche(im, 0.40)))
    lista.append(("color", "blanco y negro", gris))
    lista.append(("color", "tinte calido", lambda im: tinte(im, 2, 1.5)))
    lista.append(("color", "tinte frio", lambda im: tinte(im, 0, 1.5)))
    return tuple(lista)


# --- medicion -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Medicion:
    grupo: str
    caso: str
    identidades: int
    detectadas: int
    reconocidas: int
    distancia_media: float
    distancia_p90: float


def _preparar_importaciones() -> None:
    """Deja importable el paquete ``app`` del servidor de Rikuq.

    El script vive en ``benchmarks/`` y el codigo en ``rikuq/server``; se
    corre desde ahi, pero asi funciona tambien desde la raiz. Es el mismo
    arreglo que usa ``evaluar_rikuq.py``.
    """

    ruta = str(RAIZ / "rikuq" / "server")
    if ruta not in sys.path:
        sys.path.insert(0, ruta)


def _describir_vector(imagen: np.ndarray, detector, descriptor, orientar: bool):
    """El vector de la cara mas confiable, o None si no se detecto ninguna."""

    from app.rostros.deteccion import alinear, detectar, detectar_orientado
    from app.rostros.embedding import describir
    from app.rostros.errores import EmptyObjectError

    try:
        if orientar:
            # Las dos piezas del pipeline de hoy: probar giros y quedarse con
            # el sujeto. `--sin-orientar` reproduce lo de antes, que no hacia
            # ninguna de las dos y tomaba el rostro mas confiable tal cual.
            hallazgo = detectar_orientado(imagen, detector)
            return describir(alinear(hallazgo.imagen, hallazgo.sujeto), descriptor)
        rostros = detectar(imagen, detector)
    except EmptyObjectError:
        return None
    return describir(alinear(imagen, rostros[0]), descriptor)


def fotos_base(identidades: int) -> list[Path]:
    """Una foto por identidad, elegidas al azar pero de forma reproducible."""

    if not LFW.is_dir():
        raise SystemExit(f"falta LFW en {LFW}: corre `python -m app.dataset.lfw descargar`")
    carpetas = sorted(p for p in LFW.iterdir() if p.is_dir() and any(p.glob("*.jpg")))
    random.Random(SEMILLA).shuffle(carpetas)
    return [min(carpeta.glob("*.jpg")) for carpeta in carpetas[:identidades]]


def parejas(identidades: int) -> list[tuple[str, Path, Path]]:
    """Dos fotos por identidad: una entra a la base y otra consulta.

    Hacen falta dos distintas. Consultar con la misma foto que se cargo da
    distancia cero y el experimento diria que todo funciona.
    """

    if not LFW.is_dir():
        raise SystemExit(f"falta LFW en {LFW}: corre `python -m app.dataset.lfw descargar`")
    carpetas = [p for p in sorted(LFW.iterdir()) if p.is_dir() and len(list(p.glob("*.jpg"))) >= 2]
    random.Random(SEMILLA).shuffle(carpetas)
    elegidas = carpetas[:identidades]
    return [(p.name, *sorted(p.glob("*.jpg"))[:2]) for p in elegidas]


def paso_medir(identidades: int, orientar: bool) -> Path:
    _preparar_importaciones()
    from app.consulta.identificar import UMBRAL_POR_DEFECTO
    from app.rostros.deteccion import crear_detector
    from app.rostros.embedding import crear_descriptor
    from app.rostros.lectura import leer_color

    random.seed(SEMILLA)
    np.random.seed(SEMILLA)
    detector, descriptor = crear_detector(), crear_descriptor()

    referencias = []
    for ruta in fotos_base(identidades):
        imagen = leer_color(ruta)
        vector = _describir_vector(imagen, detector, descriptor, orientar)
        if vector is not None:
            referencias.append((imagen, vector))
    if not referencias:
        raise SystemExit("ninguna foto base dio un rostro; revisa los modelos")
    print(f"identidades usables: {len(referencias)}", flush=True)

    mediciones = []
    for grupo, caso, degradar in casos():
        distancias, detectadas = [], 0
        for imagen, referencia in referencias:
            vector = _describir_vector(degradar(imagen), detector, descriptor, orientar)
            if vector is None:
                continue
            detectadas += 1
            distancias.append(float(1.0 - np.dot(referencia, vector)))
        arreglo = np.array(distancias) if distancias else np.array([float("nan")])
        medicion = Medicion(
            grupo=grupo,
            caso=caso,
            identidades=len(referencias),
            detectadas=detectadas,
            reconocidas=int((arreglo < UMBRAL_POR_DEFECTO).sum()) if distancias else 0,
            distancia_media=round(float(arreglo.mean()), 4) if distancias else float("nan"),
            distancia_p90=(
                round(float(np.percentile(arreglo, 90)), 4) if distancias else float("nan")
            ),
        )
        mediciones.append(medicion)
        print(
            f"  {grupo:12s} {caso:22s} detecta {medicion.detectadas:3d}  "
            f"reconoce {medicion.reconocidas:3d}",
            flush=True,
        )

    RESULTADOS.mkdir(parents=True, exist_ok=True)
    sello = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    sufijo = "orientado" if orientar else "sin_orientar"
    destino = RESULTADOS / f"{sello}_rikuq_robustez_{sufijo}.csv"
    with destino.open("w", encoding="utf-8", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=list(asdict(mediciones[0])))
        escritor.writeheader()
        for medicion in mediciones:
            escritor.writerow(asdict(medicion))

    (RESULTADOS / f"{sello}_rikuq_robustez_{sufijo}.json").write_text(
        json.dumps(
            {"orientado": orientar, "casos": [asdict(m) for m in mediciones]},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\n{destino}")
    return destino


# --- tabla --------------------------------------------------------------------


def ultimo_csv(orientado: bool) -> Path | None:
    sufijo = "orientado" if orientado else "sin_orientar"
    candidatos = sorted(RESULTADOS.glob(f"*_rikuq_robustez_{sufijo}.csv"))
    return candidatos[-1] if candidatos else None


def leer_csv(ruta: Path) -> list[dict[str, str]]:
    with ruta.open(encoding="utf-8", newline="") as archivo:
        return list(csv.DictReader(archivo))


def tabla_markdown(filas: list[dict[str, str]]) -> str:
    lineas = [
        "| grupo | caso | detecta | distancia media | p90 | reconoce |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for fila in filas:
        total = int(fila["identidades"])
        detecta = int(fila["detectadas"]) / total * 100
        reconoce = int(fila["reconocidas"]) / total * 100
        lineas.append(
            f"| {fila['grupo']} | {fila['caso']} | {detecta:.0f}% | "
            f"{float(fila['distancia_media']):.3f} | {float(fila['distancia_p90']):.3f} | "
            f"{reconoce:.0f}% |"
        )
    return "\n".join(lineas)


def paso_tabla() -> int:
    ruta = ultimo_csv(orientado=True)
    if ruta is None:
        print("no hay resultados todavia: corre `medir` primero", file=sys.stderr)
        return 1
    print(f"<!-- {ruta.name} -->")
    print(tabla_markdown(leer_csv(ruta)))
    return 0


# --- dispersion ---------------------------------------------------------------


def resumen_dispersion(propias: np.ndarray, impostor: np.ndarray, umbral: float) -> dict:
    """Los numeros de la dispersion, separados de como se imprimen.

    `fallos_en_el_peor_decil` es el que importa: si el error se repartiera
    parejo, el decil mas dificil tendria una decima parte de los fallos.
    """

    orden = np.argsort(propias)
    decil = max(1, len(propias) // 10)
    peor, mejor = propias[orden[-decil:]], propias[orden[:decil]]
    fallan = propias > umbral
    return {
        "identidades": len(propias),
        "mediana": float(np.median(propias)),
        "decil_9": float(np.percentile(propias, 90)),
        "maximo": float(propias.max()),
        "fallos": int(fallan.sum()),
        "media_decil_facil": float(mejor.mean()),
        "media_decil_dificil": float(peor.mean()),
        "fallos_en_el_peor_decil": int((peor > umbral).sum()),
        "impostor_mas_cercano": float(impostor.min()),
        "expuestas": int((impostor < umbral).sum()),
    }


def paso_dispersion(identidades: int, orientar: bool) -> int:
    """DIV-019: si el reconocimiento acierta parejo con todas las personas.

    La pregunta original era comparar precision por tono de piel y por edad.
    LFW no trae esas etiquetas, asi que agruparlas seria inventarselas. Lo que
    si se puede medir sin etiquetar a nadie es la dispersion: si los fallos se
    reparten o se concentran en unas pocas identidades.
    """

    _preparar_importaciones()
    from app.consulta.identificar import UMBRAL_POR_DEFECTO
    from app.rostros.deteccion import crear_detector
    from app.rostros.embedding import crear_descriptor
    from app.rostros.lectura import leer_color

    detector, descriptor = crear_detector(), crear_descriptor()
    nombres, base, consulta = [], [], []
    for nombre, una, otra in parejas(identidades):
        a = _describir_vector(leer_color(una), detector, descriptor, orientar)
        b = _describir_vector(leer_color(otra), detector, descriptor, orientar)
        if a is None or b is None:
            continue
        nombres.append(nombre)
        base.append(a)
        consulta.append(b)
        if len(nombres) % 100 == 0:
            print(f"  {len(nombres)} identidades...", flush=True)

    base, consulta = np.array(base), np.array(consulta)
    propias = 1.0 - np.einsum("ij,ij->i", base, consulta)
    cruzadas = 1.0 - base @ base.T
    np.fill_diagonal(cruzadas, np.inf)
    datos = resumen_dispersion(propias, cruzadas.min(axis=1), UMBRAL_POR_DEFECTO)

    print(f"\nidentidades: {datos['identidades']}")
    print(
        f"distancia propia: mediana {datos['mediana']:.3f}, "
        f"decil 9 {datos['decil_9']:.3f}, maximo {datos['maximo']:.3f}"
    )
    print(
        f"no se reconocen a si mismas: {datos['fallos']} "
        f"({datos['fallos'] / datos['identidades'] * 100:.1f}%)"
    )
    print(
        f"decil mas facil {datos['media_decil_facil']:.3f} contra "
        f"decil mas dificil {datos['media_decil_dificil']:.3f} "
        f"({datos['media_decil_dificil'] / datos['media_decil_facil']:.1f}x)"
    )
    print(
        f"el peor decil concentra {datos['fallos_en_el_peor_decil']} de los "
        f"{datos['fallos']} fallos"
    )
    print(
        f"impostor mas cercano {datos['impostor_mas_cercano']:.3f}; "
        f"identidades con un impostor dentro del umbral: {datos['expuestas']}"
    )

    RESULTADOS.mkdir(parents=True, exist_ok=True)
    sello = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    sufijo = "orientado" if orientar else "sin_orientar"
    destino = RESULTADOS / f"{sello}_rikuq_dispersion_{sufijo}.json"
    destino.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{destino}")
    return 0


# --- escala ---------------------------------------------------------------------


def paso_escala(identidades: int, motor: str, tamanos: tuple[int, ...]) -> int:
    """REC-009: si HNSW aguanta cuando la base crece.

    Necesita un motor **aparte**, con su propio catalogo, porque carga miles
    de rostros y borrarlos despues no es cosa de un benchmark. Desde la raiz::

        QUIPUDB_CATALOG=datos/escala/catalogo.txt python -m uvicorn \\
            engine.api.main:app --host 127.0.0.1 --port 8005
    """

    _preparar_importaciones()
    from app.carga.dataset import crear_esquema, insertar_persona, insertar_rostro
    from app.consulta.identificar import identificar
    from app.motor import ClienteMotor
    from app.rostros.deteccion import crear_detector
    from app.rostros.embedding import crear_descriptor
    from app.rostros.lectura import leer_color

    detector, descriptor = crear_detector(), crear_descriptor()
    base, consulta = [], []
    for nombre, una, otra in parejas(identidades):
        a = _describir_vector(leer_color(una), detector, descriptor, True)
        b = _describir_vector(leer_color(otra), detector, descriptor, True)
        if a is None or b is None:
            continue
        base.append((nombre, a))
        consulta.append((nombre, b))
        if len(base) % 250 == 0:
            print(f"  vectores de {len(base)} identidades...", flush=True)
    print(f"identidades usables: {len(base)}\n", flush=True)

    cliente = ClienteMotor(motor, 120)
    crear_esquema(cliente)
    print(f"{'rostros':>8} {'consultas':>10} {'precision':>10} {'ms p50':>8} {'p95':>8}")
    print("-" * 48)
    cargados = 0
    for tamano in tamanos:
        if tamano > len(base):
            print(f"  (se omite {tamano}: solo hay {len(base)} identidades con dos fotos)")
            continue
        # Los tamanos son anidados: solo se agrega lo que falta.
        for pid in range(cargados + 1, tamano + 1):
            nombre, vector = base[pid - 1]
            cliente.query(insertar_persona(pid, nombre, "lfw", 1))
            cliente.query(insertar_rostro(pid, pid, f"base/{pid}.jpg", vector))
        cargados = tamano

        muestra = random.Random(SEMILLA).sample(range(tamano), min(120, tamano))
        aciertos, tiempos = [], []
        for i in muestra:
            nombre, vector = consulta[i]
            marca = time.perf_counter()
            respuesta = identificar(cliente, vector)
            tiempos.append((time.perf_counter() - marca) * 1000)
            aciertos.append(respuesta.persona == nombre)
        orden = sorted(tiempos)
        print(
            f"{tamano:8d} {len(muestra):10d} {sum(aciertos) / len(muestra) * 100:9.1f}% "
            f"{orden[len(orden) // 2]:7.2f} {orden[int(len(orden) * 0.95) - 1]:8.2f}",
            flush=True,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = parser.add_subparsers(dest="paso", required=True)

    medir = sub.add_parser("medir", help="aplica la bateria y guarda los resultados")
    medir.add_argument("--identidades", type=int, default=IDENTIDADES)
    medir.add_argument(
        "--sin-orientar",
        action="store_true",
        help="usa detectar en vez de detectar_orientado, como antes del arreglo",
    )

    sub.add_parser("tabla", help="imprime la ultima medicion como tabla markdown")

    dispersion = sub.add_parser(
        "dispersion", help="si los fallos se reparten o se concentran en pocas identidades"
    )
    dispersion.add_argument("--identidades", type=int, default=400)
    dispersion.add_argument(
        "--sin-orientar",
        action="store_true",
        help="el pipeline de antes: sin probar giros y con el rostro mas confiable",
    )

    escala = sub.add_parser("escala", help="precision y latencia cuando la base crece")
    escala.add_argument("--identidades", type=int, default=1680)
    escala.add_argument("--motor", default="http://127.0.0.1:8005")
    escala.add_argument("--tamanos", type=int, nargs="+", default=[250, 1000])

    args = parser.parse_args(argv)
    if args.paso == "medir":
        paso_medir(args.identidades, orientar=not args.sin_orientar)
        return 0
    if args.paso == "dispersion":
        return paso_dispersion(args.identidades, orientar=not args.sin_orientar)
    if args.paso == "escala":
        return paso_escala(args.identidades, args.motor, tuple(args.tamanos))
    return paso_tabla()


if __name__ == "__main__":
    sys.exit(main())
