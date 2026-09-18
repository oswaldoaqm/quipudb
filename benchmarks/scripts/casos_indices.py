"""Casos #40 sobre bindings reales; medicion, repeticiones y CSV pertenecen al banco."""

from __future__ import annotations

import hashlib
import random
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .banco_pruebas import Caso, Dataset, Operacion
    from .casos_archivos import seleccionar_claves
    from .generar_datasets import SEMILLA
else:
    from banco_pruebas import Caso, Dataset, Operacion
    from casos_archivos import seleccionar_claves
    from generar_datasets import SEMILLA

TECNICAS = ("bplus_clustered", "bplus_unclustered", "extendible_hash")
CICLOS = 3


def seleccionar_rangos(n: int, cantidad: int) -> tuple[tuple[int, int], ...]:
    """Intervalos inclusivos de 1% de N, con inicios distintos y RNG local por tamano."""
    ancho = n // 100
    if ancho < 1 or not 1 <= cantidad <= n - ancho + 1:
        raise ValueError("consultas-rango debe estar entre 1 y N - ancho + 1 (ancho = N//100)")
    inicios = random.Random(SEMILLA).sample(range(1, n - ancho + 2), cantidad)
    return tuple((inicio, inicio + ancho - 1) for inicio in inicios)


def _huella(valores) -> str:
    return hashlib.sha256(repr(tuple(valores)).encode("utf-8")).hexdigest()


def registros_adicionales(dataset: Dataset) -> tuple[tuple[int, str, float], ...]:
    """D=N//10 altas derivadas en memoria: claves nuevas, mezcladas, sin cambiar el CSV."""
    n = len(dataset.registros)
    por_codigo = {fila[0]: fila for fila in dataset.registros}
    return tuple(
        (n + clave, f"alumno{n + clave}", por_codigo[clave][2])
        for clave in seleccionar_claves(n, n // 10)
    )


@dataclass
class EstadoIndice:
    """Un unico handle por archivo; nunca simula ni implementa una estructura."""

    db: object
    tabla: object
    directorio: Path
    tecnica: str
    indice: object | None = None

    def construir_indice(self):
        self.indice = self.db.create_index("alumnos", "por_codigo", "codigo", self.tecnica)

    def insertar(self, registro):
        rid = self.tabla.insert(registro)
        if self.indice is not None:
            self.indice.insert(registro[0], rid)

    def eliminar(self, clave):
        # No hay indices automaticos. Las cantidades se validan despues de medir.
        entradas = self.indice.remove(clave) if self.indice is not None else 1
        return entradas, self.tabla.remove(clave)

    def resolver(self, rids):
        return [self.tabla.read(rid) for rid in rids]

    def buscar(self, clave):
        if self.indice is None:
            return self.tabla.search(clave)
        return self.resolver(self.indice.search(clave))

    def rango(self, lo, hi):
        if self.indice is None:
            return self.tabla.range_search(lo, hi)
        return self.resolver(self.indice.range_search(lo, hi))

    def recorrer_ordenado(self):
        if self.indice is None:
            return self.tabla.scan()
        return self.resolver(rid for _, rid in self.indice.scan())

    def reiniciar(self):
        self.tabla.reset_stats()
        if self.indice is not None:
            self.indice.reset_stats()

    def contadores(self):
        datos = self.tabla.stats()
        leidas, escritas = int(datos.pages_read), int(datos.pages_written)
        if self.indice is not None:
            indice = self.indice.stats()
            leidas += int(indice.pages_read)
            escritas += int(indice.pages_written)
        return leidas, escritas

    def espacio(self):
        info = self.db.table_info("alumnos")
        datos = (self.directorio / info.file).stat().st_size
        indices = 0
        if self.indice is not None:
            archivo = next(ix.file for ix in info.indexes if ix.name == "por_codigo")
            indices = (self.directorio / archivo).stat().st_size
        return datos, indices

    def validar(self, esperados):
        filas = tuple(tuple(fila) for fila in self.tabla.scan())
        if self.tabla.size() != len(esperados) or sorted(filas) != sorted(esperados):
            raise RuntimeError("la tabla no conserva exactamente los registros esperados")
        if self.indice is not None:
            pares = self.indice.scan()
            por_codigo = {fila[0]: fila for fila in esperados}
            if (
                self.indice.size() != len(esperados)
                or len(pares) != len(esperados)
                or {clave for clave, _ in pares} != set(por_codigo)
            ):
                raise RuntimeError("el indice esta desincronizado con la tabla")
            for clave, rid in pares:
                fila = self.tabla.read(rid)
                if fila is None or tuple(fila) != por_codigo[clave]:
                    raise RuntimeError("el indice contiene un RID incorrecto")


@contextmanager
def abrir_estado(nativo, directorio, tecnica, page_size=None, *, indice_vacio=True):
    if tecnica not in TECNICAS:
        raise ValueError(f"tecnica de indice desconocida: {tecnica}")
    if page_size is not None and page_size <= 0:
        raise ValueError("page_size debe ser positivo")
    db = nativo.Database(directorio / "catalogo.txt")
    try:
        esquema = nativo.Schema(
            "alumnos",
            [
                nativo.Column("codigo", nativo.DataType.INT),
                nativo.Column("nombre", nativo.DataType.VARCHAR, 16),
                nativo.Column("promedio", nativo.DataType.DOUBLE),
            ],
            key_column=0,
        )
        almacenamiento = tecnica if tecnica == "bplus_clustered" else nativo.kind.HEAP
        opciones = {} if page_size is None else {"page_size": page_size}
        tabla = db.create_table(esquema, almacenamiento, **opciones)
        estado = EstadoIndice(db, tabla, directorio, tecnica)
        if tecnica != "bplus_clustered" and indice_vacio:
            estado.construir_indice()
        config = {
            "page_size": db.table_info("alumnos").page_size,
            "page_size_origen": "binding" if page_size is None else "explicito",
            "modulo_nativo": nativo.__file__,
            "columna_clave": "codigo",
            "orden_carga": "csv_sin_reordenar",
            "semilla": SEMILLA,
            "resolucion": "registros_completos",
            "rango_nativo": tecnica != "extendible_hash",
            "orden_nativo": tecnica != "extendible_hash",
            "archivo_integrado": tecnica == "bplus_clustered",
            "contadores": "tabla" if tecnica == "bplus_clustered" else "heap_mas_indice",
        }
        yield estado, config
    finally:
        # Database cierra los indices antes que la tabla. Tambien ante fallo de create_index.
        db.close("alumnos")


def _cargar(estado, registros):
    for registro in registros:
        estado.insertar(registro)
    estado.db.flush()


def _operacion(estado, cantidad, ejecutar, validar, config):
    return Operacion(
        cantidad, ejecutar, estado.reiniciar, estado.contadores, estado.espacio, validar, config
    )


def caso_carga(nativo, tecnica, page_size=None) -> Caso:
    @contextmanager
    def preparar(directorio, dataset):
        with abrir_estado(nativo, directorio, tecnica, page_size) as (estado, config):
            estado.db.flush()
            config.update(
                flush="final_incluido",
                registros_vivos_antes=0,
                registros_vivos_despues_esperados=len(dataset.registros),
            )
            yield _operacion(
                estado,
                len(dataset.registros),
                lambda: _cargar(estado, dataset.registros),
                lambda: estado.validar(dataset.registros),
                config,
            )

    return Caso(f"{tecnica}_carga_indexada", tecnica, "carga_indexada", preparar)


def caso_construccion(nativo, tecnica, page_size=None) -> Caso:
    if tecnica not in TECNICAS[1:]:
        raise ValueError("construccion_indice solo aplica a indices secundarios")

    @contextmanager
    def preparar(directorio, dataset):
        with abrir_estado(nativo, directorio, tecnica, page_size, indice_vacio=False) as (
            estado,
            config,
        ):
            _cargar(estado, dataset.registros)
            config.update(
                flush="final_incluido",
                entradas_construidas=len(dataset.registros),
                alcance="create_index_catalogo_archivo_recorrido_build_flush",
            )

            def ejecutar():
                estado.construir_indice()
                estado.db.flush()

            yield _operacion(estado, 1, ejecutar, lambda: estado.validar(dataset.registros), config)

    return Caso(f"{tecnica}_construccion_indice", tecnica, "construccion_indice", preparar)


def caso_consulta(nativo, tecnica, operacion, cantidad=1000, page_size=None) -> Caso:
    if operacion not in ("busqueda_igualdad", "busqueda_rango", "recorrido_ordenado"):
        raise ValueError("operacion de consulta desconocida")
    if tecnica == "extendible_hash" and operacion != "busqueda_igualdad":
        raise ValueError(
            "Hash no soporta rango ni orden nativos; no se genera una medicion ficticia"
        )

    @contextmanager
    def preparar(directorio, dataset):
        n = len(dataset.registros)
        por_codigo = {fila[0]: fila for fila in dataset.registros}
        if operacion == "busqueda_igualdad":
            consultas = seleccionar_claves(n, cantidad)
            esperados = [(por_codigo[clave],) for clave in consultas]
        elif operacion == "busqueda_rango":
            consultas = seleccionar_rangos(n, cantidad)
            esperados = [tuple(por_codigo[c] for c in range(lo, hi + 1)) for lo, hi in consultas]
        else:
            consultas = (None,)
            esperados = [tuple(sorted(dataset.registros))]
        resultados = []
        with abrir_estado(nativo, directorio, tecnica, page_size) as (estado, config):
            _cargar(estado, dataset.registros)
            config.update(
                flush="solo_preparacion",
                consultas=len(consultas),
                consultas_sha256=_huella(consultas),
                politica_carga="indexada_incremental",
                registros_resultado_por_consulta=len(esperados[0]),
            )
            if operacion == "busqueda_rango":
                config.update(ancho_rango=n // 100, selectividad=0.01)

            def ejecutar():
                for consulta in consultas:
                    if operacion == "busqueda_igualdad":
                        resultados.append(estado.buscar(consulta))
                    elif operacion == "busqueda_rango":
                        resultados.append(estado.rango(*consulta))
                    else:
                        resultados.append(estado.recorrer_ordenado())

            def validar():
                if len(resultados) != len(esperados):
                    raise RuntimeError("cantidad incorrecta de respuestas")
                for filas, esperado in zip(resultados, esperados, strict=True):
                    if any(fila is None for fila in filas) or tuple(map(tuple, filas)) != esperado:
                        raise RuntimeError("respuesta incompleta, incorrecta o fuera de orden")
                estado.validar(dataset.registros)

            yield _operacion(estado, len(consultas), ejecutar, validar, config)

    return Caso(f"{tecnica}_{operacion}", tecnica, operacion, preparar)


def caso_mantenimiento(nativo, tecnica, operacion, page_size=None) -> Caso:
    if operacion not in ("insercion_incremental", "eliminacion_incremental", "mantenimiento_mixto"):
        raise ValueError("operacion de mantenimiento desconocida")

    @contextmanager
    def preparar(directorio, dataset):
        n = len(dataset.registros)
        d = n // 10
        claves = seleccionar_claves(n, d)
        por_codigo = {fila[0]: fila for fila in dataset.registros}
        reinsertar = tuple(por_codigo[clave] for clave in claves)
        adicionales = registros_adicionales(dataset) if operacion == "insercion_incremental" else ()
        excluidos = set(claves) if operacion == "eliminacion_incremental" else set()
        esperados = (
            tuple(fila for fila in dataset.registros if fila[0] not in excluidos) + adicionales
        )
        cantidad = 2 * d * CICLOS if operacion == "mantenimiento_mixto" else d
        borrados = []
        with abrir_estado(nativo, directorio, tecnica, page_size) as (estado, config):
            _cargar(estado, dataset.registros)
            config.update(
                flush="solo_final_del_lote_incluido",
                modificaciones_por_fase=d,
                ciclos=CICLOS if operacion == "mantenimiento_mixto" else 1,
                claves_sha256=_huella(claves),
                altas_sha256=_huella(adicionales),
                registros_vivos_antes=n,
                registros_vivos_despues_esperados=len(esperados),
                politica_carga="indexada_incremental",
                secuencia_mixta="borrar_D_reinsertar_D_mismas_claves_cada_ciclo",
            )

            def ejecutar():
                if operacion == "insercion_incremental":
                    for registro in adicionales:
                        estado.insertar(registro)
                elif operacion == "eliminacion_incremental":
                    for clave in claves:
                        borrados.append(estado.eliminar(clave))
                else:
                    for _ in range(CICLOS):
                        for clave in claves:
                            borrados.append(estado.eliminar(clave))
                        for registro in reinsertar:
                            estado.insertar(registro)
                estado.db.flush()

            def validar():
                if any(resultado != (1, 1) for resultado in borrados):
                    raise RuntimeError("una eliminacion no mantuvo sincronizados tabla e indice")
                estado.validar(esperados)

            yield _operacion(estado, cantidad, ejecutar, validar, config)

    return Caso(f"{tecnica}_{operacion}", tecnica, operacion, preparar)


def casos_indices(nativo, consultas=1000, consultas_rango=100, page_size=None) -> list[Caso]:
    """21 casos por tamano; no se crean filas para capacidades no soportadas."""
    casos = [caso_carga(nativo, t, page_size) for t in TECNICAS]
    casos += [caso_construccion(nativo, t, page_size) for t in TECNICAS[1:]]
    casos += [caso_consulta(nativo, t, "busqueda_igualdad", consultas, page_size) for t in TECNICAS]
    casos += [
        caso_consulta(nativo, t, "busqueda_rango", consultas_rango, page_size) for t in TECNICAS[:2]
    ]
    casos += [caso_consulta(nativo, t, "recorrido_ordenado", 1, page_size) for t in TECNICAS[:2]]
    for operacion in ("insercion_incremental", "eliminacion_incremental", "mantenimiento_mixto"):
        casos += [caso_mantenimiento(nativo, t, operacion, page_size) for t in TECNICAS]
    return casos
