"""Casos #131: R-Tree vs busqueda secuencial; medicion, repeticiones y CSV son del banco."""

from __future__ import annotations

import hashlib
import math
import random
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .banco_pruebas import Caso, Operacion
    from .cargar_puntos import INDICE, TABLA, DatasetPuntos, esquema_puntos
    from .generar_puntos import SEMILLA
else:
    from banco_pruebas import Caso, Operacion
    from cargar_puntos import INDICE, TABLA, DatasetPuntos, esquema_puntos
    from generar_puntos import SEMILLA

TECNICAS = ("secuencial", "rtree")
METRICAS = ("haversine", "euclidiana")
# Los del enunciado. Los de cruce solo se miden con Haversine: buscan el radio
# a partir del cual el R-Tree ya no poda nada y deja de convenir.
RADIOS_KM = (1, 5, 10)
RADIOS_CRUCE_KM = (25, 50, 100, 250, 500, 2_000)
VALORES_K = (10, 50, 100)
CONSULTAS = 100
# Metros por grado de latitud con el radio medio de la Tierra del core. Con la
# euclidiana el radio va en grados: se usa el equivalente de los mismos km.
METROS_POR_GRADO = 111_195.0
# Los centros salen de los primeros 1 000 puntos. Como los datasets estan
# anidados (#130), son las MISMAS coordenadas en los tres tamanos: lo unico que
# cambia entre 1k, 10k y 100k es cuantos puntos hay alrededor.
POBLACION_CENTROS = 1_000


def seleccionar_centros(dataset: DatasetPuntos, cantidad: int = CONSULTAS):
    """Coordenadas (latitud, longitud) de `cantidad` puntos del propio dataset.

    Centrar en puntos reales hace que las consultas caigan donde hay datos, que
    es el caso que importa ("tiendas cerca de mi"), y no en el mar o la selva.
    """
    if not 1 <= cantidad <= min(POBLACION_CENTROS, len(dataset.registros)):
        raise ValueError(f"consultas debe estar entre 1 y {POBLACION_CENTROS}")
    ids = random.Random(SEMILLA).sample(range(1, POBLACION_CENTROS + 1), cantidad)
    return tuple(dataset.registros[i - 1][2:] for i in ids)


def radio_en_unidades(radio_km: float, metrica: str) -> float:
    """Metros con Haversine; grados equivalentes con la euclidiana."""
    metros = radio_km * 1_000.0
    return metros if metrica == "haversine" else metros / METROS_POR_GRADO


def distancia(a: tuple[float, float], b: tuple[float, float], metrica: str) -> float:
    """Oraculo en Python, solo para validar fuera del reloj."""
    if metrica == "euclidiana":
        return math.hypot(a[0] - b[0], a[1] - b[1])
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    h = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    )
    return 2 * 6_371_008.8 * math.asin(math.sqrt(h))


def _huella(valores) -> str:
    return hashlib.sha256(repr(tuple(valores)).encode("utf-8")).hexdigest()


@dataclass
class EstadoEspacial:
    """Un Heap con los puntos y, si la tecnica lo usa, su R-Tree."""

    nativo: object
    db: object
    tabla: object
    directorio: Path
    tecnica: str
    # (page, slot) -> (latitud, longitud), para validar sin leer la tabla.
    puntos: dict
    indice: object | None = None

    def construir_indice(self):
        self.indice = self.db.create_index(TABLA, INDICE, "ubicacion", self.nativo.kind.RTREE)

    def metrica(self, nombre):
        return (
            self.nativo.Metric.HAVERSINE if nombre == "haversine" else self.nativo.Metric.EUCLIDEAN
        )

    def radio(self, centro, radio, metrica):
        if self.tecnica == "rtree":
            return self.indice.search_radius(centro, radio, metrica)
        return self.nativo.scan_radius(self.tabla, "ubicacion", centro, radio, metrica)

    def knn(self, centro, k, metrica):
        if self.tecnica == "rtree":
            return self.indice.k_nearest(centro, k, metrica)
        return self.nativo.scan_k_nearest(self.tabla, "ubicacion", centro, k, metrica)

    def reiniciar(self):
        self.tabla.reset_stats()
        if self.indice is not None:
            self.indice.reset_stats()

    def contadores(self):
        # Las consultas devuelven RID: el R-Tree solo lee sus nodos y el
        # secuencial solo el Heap. Se suman los dos para no esconder nada.
        estadisticas = [self.tabla.stats()]
        if self.indice is not None:
            estadisticas.append(self.indice.stats())
        return (
            sum(e.pages_read for e in estadisticas),
            sum(e.pages_written for e in estadisticas),
        )

    def espacio(self):
        info = self.db.table_info(TABLA)
        datos = (self.directorio / info.file).stat().st_size
        indices = 0
        if self.indice is not None:
            archivo = next(ix.file for ix in info.indexes if ix.name == INDICE)
            indices = (self.directorio / archivo).stat().st_size
        return datos, indices


@contextmanager
def abrir_estado(nativo, directorio, dataset, tecnica, *, con_indice):
    if tecnica not in TECNICAS:
        raise ValueError(f"tecnica espacial desconocida: {tecnica}")
    db = nativo.Database(directorio / "catalogo.txt")
    try:
        tabla = db.create_table(esquema_puntos(nativo), nativo.kind.HEAP)
        puntos = {}
        for identificador, ciudad, latitud, longitud in dataset.registros:
            rid = tabla.insert([identificador, ciudad, nativo.GeoPoint(latitud, longitud)])
            puntos[(rid.page, rid.slot)] = (latitud, longitud)
        estado = EstadoEspacial(nativo, db, tabla, directorio, tecnica, puntos)
        if con_indice:
            estado.construir_indice()
        db.flush()
        config = {
            "page_size": db.table_info(TABLA).page_size,
            "modulo_nativo": nativo.__file__,
            "almacenamiento": "heap",
            "orden_carga": "csv_sin_reordenar",
            "semilla": SEMILLA,
            "resolucion": "rids_sin_leer_registros",
            "contadores": "heap_mas_indice",
        }
        yield estado, config
    finally:
        db.close(TABLA)


# Referencias por dataset y consulta, calculadas una vez con la busqueda
# secuencial y compartidas entre repeticiones y tecnicas. Los RID coinciden
# porque cada repeticion carga el mismo CSV, en el mismo orden, en un Heap nuevo.
_REFERENCIAS: dict[tuple, list] = {}


def _clave_rid(rid) -> tuple[int, int]:
    return rid.page, rid.slot


def _firma_radio(rids) -> tuple[int, str]:
    """Cantidad y hash del conjunto de RIDs. Guardar las listas completas como
    referencia llega a 10 millones de tuplas con un radio que cubre 100k puntos:
    mas de 1 GB de RAM y un recolector de basura que mete ruido en los tiempos."""
    ordenados = sorted(map(_clave_rid, rids))
    return len(ordenados), _huella(ordenados)


def caso_construccion(nativo) -> Caso:
    """Tiempo de construir el R-Tree sobre un Heap ya cargado. El secuencial no
    tiene construccion: no se genera una fila ficticia con tiempo cero."""

    @contextmanager
    def preparar(directorio, dataset):
        with abrir_estado(nativo, directorio, dataset, "rtree", con_indice=False) as (
            estado,
            config,
        ):
            config.update(
                flush="final_incluido",
                entradas_construidas=len(dataset.registros),
                alcance="create_index_catalogo_archivo_recorrido_build_flush",
            )

            def ejecutar():
                estado.construir_indice()
                estado.db.flush()

            def validar():
                if len(estado.indice) != len(dataset.registros):
                    raise RuntimeError("el R-Tree no tiene una entrada por punto")

            yield Operacion(
                1, ejecutar, estado.reiniciar, estado.contadores, estado.espacio, validar, config
            )

    return Caso("rtree_construccion_indice", "rtree", "construccion_indice", preparar)


def caso_radio(nativo, tecnica, radio_km, metrica, consultas=CONSULTAS) -> Caso:
    if metrica not in METRICAS:
        raise ValueError(f"metrica desconocida: {metrica}")
    operacion = f"radio_{radio_km}km"

    @contextmanager
    def preparar(directorio, dataset):
        centros = seleccionar_centros(dataset, consultas)
        radio = radio_en_unidades(radio_km, metrica)
        resultados = []
        with abrir_estado(nativo, directorio, dataset, tecnica, con_indice=tecnica == "rtree") as (
            estado,
            config,
        ):
            geo = [nativo.GeoPoint(lat, lon) for lat, lon in centros]
            m = estado.metrica(metrica)
            clave = (dataset.sha256, operacion, metrica, _huella(centros))
            if clave not in _REFERENCIAS:
                _REFERENCIAS[clave] = [
                    _firma_radio(nativo.scan_radius(estado.tabla, "ubicacion", c, radio, m))
                    for c in geo
                ]
            esperados = _REFERENCIAS[clave]
            config.update(
                flush="solo_preparacion",
                metrica=metrica,
                radio_km=radio_km,
                radio_unidades=radio,
                consultas=len(centros),
                centros_sha256=_huella(centros),
                resultados_totales=sum(cantidad for cantidad, _ in esperados),
                fraccion_media_devuelta=sum(cantidad for cantidad, _ in esperados)
                / (len(centros) * len(dataset.registros)),
            )

            def ejecutar():
                for centro in geo:
                    resultados.append(estado.radio(centro, radio, m))

            def validar():
                obtenidos = [_firma_radio(r) for r in resultados]
                # Se liberan antes de la siguiente repeticion, no al cerrar el caso.
                resultados.clear()
                if obtenidos != esperados:
                    raise RuntimeError("el radio no coincide con la busqueda secuencial")

            yield Operacion(
                len(centros),
                ejecutar,
                estado.reiniciar,
                estado.contadores,
                estado.espacio,
                validar,
                config,
            )

    return Caso(f"{tecnica}_{operacion}_{metrica}", tecnica, operacion, preparar)


def caso_knn(nativo, tecnica, k, metrica, consultas=CONSULTAS) -> Caso:
    if metrica not in METRICAS:
        raise ValueError(f"metrica desconocida: {metrica}")
    operacion = f"knn_{k}"

    @contextmanager
    def preparar(directorio, dataset):
        if k > len(dataset.registros):
            raise ValueError("k no puede superar N")
        centros = seleccionar_centros(dataset, consultas)
        resultados = []
        with abrir_estado(nativo, directorio, dataset, tecnica, con_indice=tecnica == "rtree") as (
            estado,
            config,
        ):
            geo = [nativo.GeoPoint(lat, lon) for lat, lon in centros]
            m = estado.metrica(metrica)

            def distancias(centro, rids):
                return [distancia(centro, estado.puntos[_clave_rid(r)], metrica) for r in rids]

            clave = (dataset.sha256, operacion, metrica, _huella(centros))
            if clave not in _REFERENCIAS:
                _REFERENCIAS[clave] = [
                    distancias(c, nativo.scan_k_nearest(estado.tabla, "ubicacion", g, k, m))
                    for c, g in zip(centros, geo, strict=True)
                ]
            esperados = _REFERENCIAS[clave]
            config.update(
                flush="solo_preparacion",
                metrica=metrica,
                k=k,
                consultas=len(centros),
                centros_sha256=_huella(centros),
            )

            def ejecutar():
                for centro in geo:
                    resultados.append(estado.knn(centro, k, m))

            def validar():
                # Con empates cual entra es indistinto: se comparan distancias.
                obtenidos = [distancias(c, r) for c, r in zip(centros, resultados, strict=True)]
                if obtenidos != esperados:
                    raise RuntimeError("el k-NN no coincide con la busqueda secuencial")
                if any(d != sorted(d) or len(d) != k for d in obtenidos):
                    raise RuntimeError("el k-NN no esta completo u ordenado")

            yield Operacion(
                len(centros),
                ejecutar,
                estado.reiniciar,
                estado.contadores,
                estado.espacio,
                validar,
                config,
            )

    return Caso(f"{tecnica}_{operacion}_{metrica}", tecnica, operacion, preparar)


def casos_espaciales(nativo, consultas=CONSULTAS, *, cruce=True) -> list[Caso]:
    """Construccion, radios y k-NN del 2.2.4 con las dos tecnicas y las dos metricas,
    mas los radios de cruce con Haversine."""
    casos = [caso_construccion(nativo)]
    for metrica in METRICAS:
        for tecnica in TECNICAS:
            casos.extend(caso_radio(nativo, tecnica, r, metrica, consultas) for r in RADIOS_KM)
            casos.extend(caso_knn(nativo, tecnica, k, metrica, consultas) for k in VALORES_K)
    if cruce:
        for tecnica in TECNICAS:
            casos.extend(
                caso_radio(nativo, tecnica, r, "haversine", consultas) for r in RADIOS_CRUCE_KM
            )
    return casos
