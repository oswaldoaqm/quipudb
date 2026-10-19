"""Casos #172: TF-IDF + coseno vs BM25; medicion, repeticiones y CSV son del banco.

La construccion del indice es UNA sola para las dos tecnicas: SPIMI arma el
mismo indice invertido y lo que cambia es como se puntua una consulta. Por eso
hay un unico caso de construccion, con tecnica `spimi`, y los casos de consulta
si van por ranking.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .banco_pruebas import Caso, Operacion
    from .cargar_documentos import (
        INDICE,
        TABLA,
        ConsultaTexto,
        DatasetDocumentos,
        cargar_quipudb,
        opciones_ingles,
    )
    from .generar_documentos import LONGITUDES, SEMILLA
else:
    from banco_pruebas import Caso, Operacion
    from cargar_documentos import (
        INDICE,
        TABLA,
        ConsultaTexto,
        DatasetDocumentos,
        cargar_quipudb,
        opciones_ingles,
    )
    from generar_documentos import LONGITUDES, SEMILLA

RANKINGS = ("tf_idf", "bm25")
# Los 10 primeros: lo que miden Precision@10 y Recall@10 (#173).
K = 10
CONSULTAS_POR_LONGITUD = 100
# Fan-in de la mezcla del core (kSpimiDefaultFanIn): bloques abiertos a la vez.
FAN_IN = 64
# Bytes de un RID en memoria (u32 pagina, u16 slot y relleno).
BYTES_RID = 8


def nombre_longitud(longitud: str) -> str:
    """'1', '3', '5+' -> '1', '3', '5omas', que sirve en un nombre de caso."""
    return longitud.replace("+", "omas")


def consultas_de_longitud(
    consultas: tuple[ConsultaTexto, ...], longitud: str, cantidad: int
) -> tuple[ConsultaTexto, ...]:
    """Las primeras `cantidad` consultas de esa longitud, en el orden del CSV."""
    if longitud not in LONGITUDES:
        raise ValueError(f"longitud desconocida: {longitud}")
    elegidas = tuple(c for c in consultas if c.longitud == longitud)[:cantidad]
    if len(elegidas) < cantidad or cantidad < 1:
        raise ValueError(f"se pidieron {cantidad} consultas de {longitud} palabras")
    return elegidas


def _huella(valores) -> str:
    return hashlib.sha256(repr(tuple(valores)).encode("utf-8")).hexdigest()


@dataclass
class EstadoTexto:
    """Un Heap con los documentos y su indice invertido."""

    nativo: object
    db: object
    tabla: object
    directorio: Path
    indice: object | None = None

    def ranking(self, nombre):
        if nombre not in RANKINGS:
            raise ValueError(f"ranking desconocido: {nombre}")
        return (
            self.nativo.TextRanking.TF_IDF if nombre == "tf_idf" else self.nativo.TextRanking.BM25
        )

    def reiniciar(self):
        self.tabla.reset_stats()
        if self.indice is not None:
            self.indice.reset_stats()

    def contadores(self):
        # La construccion lee el Heap y escribe el indice; la consulta solo lee
        # el indice. Se suman los dos para no esconder nada.
        estadisticas = [self.tabla.stats()]
        if self.indice is not None:
            estadisticas.append(self.indice.stats())
        return (
            sum(e.pages_read for e in estadisticas),
            sum(e.pages_written for e in estadisticas),
        )

    def espacio(self):
        """(bytes del Heap, bytes del indice): su archivo mas el de RIDs."""
        info = self.db.table_info(TABLA)
        datos = (self.directorio / info.file).stat().st_size
        indices = 0
        if self.indice is not None:
            archivo = self.directorio / next(ix.file for ix in info.indexes if ix.name == INDICE)
            rids = archivo.with_name(archivo.name + ".rids")
            indices = archivo.stat().st_size + rids.stat().st_size
        return datos, indices


@contextmanager
def abrir_estado(nativo, directorio: Path, dataset: DatasetDocumentos, *, con_indice: bool):
    db = nativo.Database(directorio / "catalogo.txt")
    try:
        tabla, indice = cargar_quipudb(nativo, db, dataset, con_indice=con_indice)
        estado = EstadoTexto(nativo, db, tabla, directorio, indice)
        config = {
            "page_size": db.table_info(TABLA).page_size,
            "modulo_nativo": nativo.__file__,
            "almacenamiento": "heap",
            "orden_carga": "csv_sin_reordenar",
            "semilla": SEMILLA,
            "tokenizador": "ingles_stopwords_y_stemmer_snowball",
            "contadores": "heap_mas_indice",
        }
        yield estado, config
    finally:
        db.close(TABLA)


def caso_construccion(nativo) -> Caso:
    """Tiempo, paginas, espacio y memoria de construir el indice con SPIMI.

    El Heap se carga fuera del reloj y `create_index` registra el indice con las
    opciones por omision; el reloj mide solo el `build` con las inglesas, que es
    SPIMI mas la mezcla mas el archivo de RIDs.
    """

    @contextmanager
    def preparar(directorio, dataset):
        with abrir_estado(nativo, directorio, dataset, con_indice=False) as (estado, config):
            estado.indice = estado.db.create_index(
                TABLA, INDICE, "texto", nativo.kind.INVERTED_INDEX
            )
            estado.db.flush()
            opciones = opciones_ingles(nativo)
            config.update(
                flush="final_incluido",
                alcance="build_spimi_mezcla_rids_flush",
                tecnicas="comun_a_tf_idf_y_bm25",
            )

            def ejecutar():
                estado.indice.build(opciones)
                estado.db.flush()

            def validar():
                construccion = estado.indice.last_build
                if estado.indice.documents != len(dataset.registros) or construccion is None:
                    raise RuntimeError("el indice no tiene un documento por registro")
                if estado.indice.tokenizer_options != opciones:
                    raise RuntimeError("el indice no quedo con las opciones inglesas")
                if construccion.peak_memory > construccion.memory_budget:
                    raise RuntimeError("SPIMI paso su presupuesto de memoria")
                # Todo esto es determinista: el banco comprueba que se repite
                # identico en cada repeticion.
                lectores = min(construccion.blocks, FAN_IN) * config["page_size"]
                config.update(
                    terminos=estado.indice.terms,
                    bloques_spimi=construccion.blocks,
                    pasadas_mezcla=construccion.passes,
                    memoria_presupuesto_spimi_bytes=construccion.memory_budget,
                    memoria_pico_spimi_bytes=construccion.peak_memory,
                    memoria_lectores_mezcla_bytes=lectores,
                    memoria_rids_bytes=BYTES_RID * len(dataset.registros),
                )

            yield Operacion(
                1, ejecutar, estado.reiniciar, estado.contadores, estado.espacio, validar, config
            )

    return Caso("spimi_construccion_indice", "spimi", "construccion_indice", preparar)


# Respuestas por dataset, ranking y consultas, de la primera repeticion. Las
# siguientes tienen que dar exactamente lo mismo: el indice se reconstruye en
# cada una sobre el mismo CSV y es identico byte a byte (#162).
_REFERENCIAS: dict[tuple, list] = {}


def caso_consulta(
    nativo,
    ranking: str,
    longitud: str,
    consultas: tuple[ConsultaTexto, ...],
    cantidad: int = CONSULTAS_POR_LONGITUD,
) -> Caso:
    """Tiempo y paginas de `cantidad` consultas de una longitud, top-10."""
    if ranking not in RANKINGS:
        raise ValueError(f"ranking desconocido: {ranking}")
    lote = consultas_de_longitud(consultas, longitud, cantidad)
    textos = [c.texto for c in lote]
    operacion = f"consulta_{nombre_longitud(longitud)}_palabras"

    @contextmanager
    def preparar(directorio, dataset):
        resultados = []
        with abrir_estado(nativo, directorio, dataset, con_indice=True) as (estado, config):
            r = estado.ranking(ranking)
            opciones = opciones_ingles(nativo)
            terminos = [len(nativo.tokenize(t, opciones)) for t in textos]
            # Cuantos documentos tienen algun termino de la consulta: es lo que
            # el ranking tiene que puntuar antes de quedarse con 10.
            coincidentes = [len(estado.indice.search_text(t, r)) for t in textos]
            config.update(
                flush="solo_preparacion",
                ranking=ranking,
                k=K,
                longitud=longitud,
                consultas=len(textos),
                consultas_sha256=_huella(textos),
                terminos_por_consulta=sum(terminos) / len(terminos),
                coincidentes_por_consulta=sum(coincidentes) / len(coincidentes),
                resolucion="rids_sin_leer_registros",
            )

            def respuesta(top):
                # El id del registro y el puntaje: los RID son del Heap de esta
                # repeticion y el id no cambia.
                return [(estado.tabla.read(rid)[0], puntaje) for rid, puntaje in top]

            def ejecutar():
                for texto in textos:
                    resultados.append(estado.indice.search_text(texto, r, K))

            def validar():
                obtenidos = [respuesta(top) for top in resultados]
                resultados.clear()
                for top in obtenidos:
                    puntajes = [p for _, p in top]
                    if len(top) > K or puntajes != sorted(puntajes, reverse=True):
                        raise RuntimeError("el top-10 no esta completo u ordenado")
                clave = (dataset.sha256, ranking, _huella(textos))
                esperados = _REFERENCIAS.setdefault(clave, obtenidos)
                if obtenidos != esperados:
                    raise RuntimeError("la respuesta cambio entre repeticiones")

            yield Operacion(
                len(textos),
                ejecutar,
                estado.reiniciar,
                estado.contadores,
                estado.espacio,
                validar,
                config,
            )

    return Caso(f"{ranking}_{operacion}", ranking, operacion, preparar)


def casos_texto(
    nativo, consultas: tuple[ConsultaTexto, ...], cantidad: int = CONSULTAS_POR_LONGITUD
) -> list[Caso]:
    """La construccion comun y, por ranking, las consultas de 1, 3 y 5+ palabras."""
    casos = [caso_construccion(nativo)]
    for ranking in RANKINGS:
        casos.extend(
            caso_consulta(nativo, ranking, longitud, consultas, cantidad) for longitud in LONGITUDES
        )
    return casos
