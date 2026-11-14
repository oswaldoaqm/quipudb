"""El dataset de rostros de Rikuq: LFW mas las fotos del equipo (#301).

Las imagenes no se versionan: LFW se vuelve a bajar y las fotos del equipo son
datos biometricos que no salen de nuestras maquinas. Lo que si se versiona es
el manifiesto, una fila por imagen con su persona, su ruta y su SHA-256. Con
el se comprueba que todos tenemos exactamente los mismos archivos sin tener
que subirlos.
"""

from __future__ import annotations

import hashlib
import tarfile
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx

# La pagina oficial de LFW, en la Universidad de Massachusetts.
URL_LFW = "http://vis-www.cs.umass.edu/lfw/lfw.tgz"
USER_AGENT = "rikuq/0.1 (proyecto academico, UTEC)"

RIKUQ = Path(__file__).resolve().parents[3]
DATOS = RIKUQ / "datos"
MANIFIESTO = RIKUQ / "dataset" / "rostros.tsv"

# Carpetas dentro de DATOS. El origen tambien es la primera parte de la ruta en
# el manifiesto, asi que leerlo no necesita adivinar de donde salio cada imagen.
LFW = "lfw"
EQUIPO = "equipo"

EXTENSIONES = (".jpg", ".jpeg", ".png")
MINIMO_POR_DEFECTO = 10

# Un trozo grande calcula el SHA-256 y baja el comprimido sin cargarlos
# enteros en memoria: LFW pesa unos 173 MB.
TROZO = 1 << 20


class DatasetError(RuntimeError):
    """La descarga o el manifiesto no se pudieron completar."""


@dataclass(frozen=True, slots=True)
class Rostro:
    """Una imagen del dataset, tal como la guarda el manifiesto."""

    persona: str
    ruta: str  # relativa a la carpeta de datos, siempre con "/"
    sha256: str
    origen: str

    def archivo(self, datos: Path = DATOS) -> Path:
        return datos / Path(self.ruta)


def sha256(ruta: Path) -> str:
    """SHA-256 del archivo, leido por trozos."""

    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        while trozo := archivo.read(TROZO):
            digest.update(trozo)
    return digest.hexdigest()


def descargar(
    url: str = URL_LFW,
    destino: Path = DATOS,
    log: Callable[[str], None] = lambda _: None,
) -> Path:
    """Baja ``lfw.tgz`` a ``destino`` y lo extrae; salta lo que ya esta.

    Devuelve la carpeta extraida. El SHA-256 del comprimido queda en
    ``lfw.tgz.sha256`` para poder confirmar despues que es el mismo archivo.
    """

    destino.mkdir(parents=True, exist_ok=True)
    raiz = destino / LFW
    if raiz.is_dir() and any(raiz.iterdir()):
        log(f"{raiz} ya existe, no se vuelve a bajar")
        return raiz

    comprimido = destino / "lfw.tgz"
    if not comprimido.is_file():
        log(f"bajando {url}")
        # Se escribe al lado y se renombra: un corte a mitad no deja un .tgz
        # truncado que la siguiente corrida daria por bueno.
        temporal = comprimido.with_suffix(".part")
        try:
            with httpx.stream(
                "GET",
                url,
                follow_redirects=True,
                timeout=120.0,
                headers={"User-Agent": USER_AGENT},
            ) as respuesta:
                respuesta.raise_for_status()
                with temporal.open("wb") as salida:
                    for trozo in respuesta.iter_bytes(TROZO):
                        salida.write(trozo)
        except httpx.HTTPError as error:
            temporal.unlink(missing_ok=True)
            raise DatasetError(f"no se pudo bajar {url}: {error}") from error
        temporal.replace(comprimido)

    huella = sha256(comprimido)
    (destino / "lfw.tgz.sha256").write_text(f"{huella}  lfw.tgz\n", encoding="utf-8")
    log(f"sha256 del comprimido: {huella}")

    log(f"extrayendo en {raiz}")
    try:
        with tarfile.open(comprimido, "r:gz") as tar:
            # filter="data" rechaza rutas absolutas, enlaces fuera del destino
            # y permisos raros: el .tgz viene de fuera y no se abre a ciegas.
            tar.extractall(destino, filter="data")
    except (tarfile.TarError, OSError) as error:
        raise DatasetError(f"no se pudo extraer {comprimido}: {error}") from error

    if not raiz.is_dir():
        raise DatasetError(f"el comprimido no trajo la carpeta {LFW}/")
    return raiz


def recolectar(raiz: Path, origen: str) -> Iterator[Rostro]:
    """Recorre ``raiz/<persona>/<imagen>`` y devuelve un Rostro por imagen.

    El nombre de la carpeta es el nombre de la persona, que es como vienen
    tanto LFW como las fotos del equipo. Una carpeta que no existe no es un
    error: el equipo puede no haber puesto sus fotos todavia.
    """

    if not raiz.is_dir():
        return
    for carpeta in sorted(p for p in raiz.iterdir() if p.is_dir()):
        for imagen in sorted(carpeta.iterdir()):
            if imagen.suffix.lower() not in EXTENSIONES:
                continue
            yield Rostro(
                persona=carpeta.name,
                ruta=f"{origen}/{carpeta.name}/{imagen.name}",
                sha256=sha256(imagen),
                origen=origen,
            )


def construir(datos: Path = DATOS, minimo: int = MINIMO_POR_DEFECTO) -> list[Rostro]:
    """Arma el manifiesto: LFW acotado por ``minimo``, mas todo el equipo.

    LFW tiene 13 233 imagenes de 5 749 personas, pero la mayoria aparece una
    sola vez. Con una sola foto no se puede evaluar nada: si esa imagen se usa
    para cargar la base, no queda ninguna para preguntar, y si se usa para
    preguntar, la persona no esta cargada.

    El minimo solo se aplica a LFW. Las fotos del equipo entran siempre: son
    pocas y son justamente las que la demostracion tiene que reconocer.
    """

    if minimo < 1:
        raise DatasetError("el minimo de fotos por persona tiene que ser al menos 1")

    lfw = list(recolectar(datos / LFW, LFW))
    cuenta = Counter(rostro.persona for rostro in lfw)
    rostros = [rostro for rostro in lfw if cuenta[rostro.persona] >= minimo]
    rostros.extend(recolectar(datos / EQUIPO, EQUIPO))
    if not rostros:
        raise DatasetError(
            f"no hay imagenes en {datos}: corre primero `descargar`, o baja el "
            f"minimo (ahora {minimo})"
        )
    return rostros


def escribir(rostros: Iterable[Rostro], ruta: Path = MANIFIESTO) -> int:
    """Escribe el manifiesto como TSV con cabecera. Devuelve cuantas filas."""

    ruta.parent.mkdir(parents=True, exist_ok=True)
    filas = 0
    with ruta.open("w", encoding="utf-8", newline="\n") as salida:
        salida.write("persona\truta\tsha256\torigen\n")
        for rostro in rostros:
            salida.write(f"{rostro.persona}\t{rostro.ruta}\t{rostro.sha256}\t{rostro.origen}\n")
            filas += 1
    return filas


def leer(ruta: Path = MANIFIESTO) -> list[Rostro]:
    """Lee el manifiesto. Una linea mal formada es un error, no se salta.

    El manifiesto es la garantia de que todos trabajamos sobre los mismos
    archivos: si una fila esta rota, saltarla en silencio la volveria inutil.
    """

    if not ruta.is_file():
        raise DatasetError(f"no existe {ruta}: corre primero `manifiesto`")
    rostros = []
    with ruta.open(encoding="utf-8") as entrada:
        cabecera = entrada.readline().rstrip("\n").split("\t")
        if cabecera != ["persona", "ruta", "sha256", "origen"]:
            raise DatasetError(f"{ruta} no tiene la cabecera esperada: {cabecera}")
        for numero, linea in enumerate(entrada, start=2):
            campos = linea.rstrip("\n").split("\t")
            if len(campos) != 4:
                raise DatasetError(f"{ruta}:{numero} tiene {len(campos)} columnas, se esperaban 4")
            rostros.append(Rostro(*campos))
    return rostros


def verificar(rostros: Iterable[Rostro], datos: Path = DATOS) -> list[tuple[str, str]]:
    """Compara el disco con el manifiesto. Devuelve (ruta, motivo) por problema.

    Para esto sirve guardar el SHA-256: el dataset no se versiona, pero si se
    puede comprobar que el de cada quien es el mismo.
    """

    problemas = []
    for rostro in rostros:
        archivo = rostro.archivo(datos)
        if not archivo.is_file():
            problemas.append((rostro.ruta, "no esta en disco"))
        elif sha256(archivo) != rostro.sha256:
            problemas.append((rostro.ruta, "el contenido cambio"))
    return problemas
