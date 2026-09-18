"""Adaptadores al storage real; el banco compartido se encarga de medir y exportar."""

from __future__ import annotations

import hashlib
import math
import random
from contextlib import contextmanager
from pathlib import Path

if __package__:
    from .banco_pruebas import Caso, Dataset, Operacion
    from .generar_datasets import SEMILLA
else:
    from banco_pruebas import Caso, Dataset, Operacion
    from generar_datasets import SEMILLA


def seleccionar_claves(n: int, cantidad: int) -> tuple[int, ...]:
    """Muestreo sin reemplazo por codigo, independiente del orden del CSV y de otras llamadas."""
    if not 1 <= cantidad <= n:
        raise ValueError("la cantidad de claves debe estar entre 1 y N")
    return tuple(random.Random(SEMILLA).sample(range(1, n + 1), cantidad))


def cantidad_borrados(n: int) -> int:
    """30% exacto para los tamanos comunes, sin listas de resultados precalculados."""
    if n <= 0 or n % 10:
        raise ValueError("N debe ser positivo y multiplo de 10 para preparar exactamente 30%")
    return 3 * n // 10


def _hash_claves(claves: tuple[int, ...]) -> str:
    return hashlib.sha256(",".join(map(str, claves)).encode("ascii")).hexdigest()


@contextmanager
def _abrir_tabla(nativo, directorio: Path, tecnica: str, page_size: int | None):
    if tecnica not in ("heap", "sequential"):
        raise ValueError("tecnica debe ser heap o sequential")
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
        tipo = nativo.kind.HEAP if tecnica == "heap" else nativo.kind.SEQUENTIAL
        opciones = {} if page_size is None else {"page_size": page_size}
        tabla = db.create_table(esquema, tipo, **opciones)
        info = db.table_info("alumnos")
        yield (
            db,
            tabla,
            directorio / info.file,
            {
                "page_size": info.page_size,
                "page_size_origen": "binding" if page_size is None else "explicito",
                "columna_clave": "codigo",
                "modulo_nativo": nativo.__file__,
                "orden_insercion": "csv_sin_reordenar",
            },
        )
    finally:
        db.close("alumnos")


def _insertar(db, tabla, registros):
    for registro in registros:
        tabla.insert(registro)
    db.flush()


def _validar_registros(tabla, esperados):
    # Solo se llama en validacion, despues de capturar los contadores del banco.
    filas = tuple(tuple(fila) for fila in tabla.scan())
    if tabla.size() != len(esperados) or sorted(filas) != sorted(esperados):
        raise RuntimeError("la tabla no contiene exactamente los registros esperados")


def _operacion(tabla, archivo, n_operaciones, ejecutar, validar, configuracion):
    def contadores():
        stats = tabla.stats()
        return int(stats.pages_read), int(stats.pages_written)

    return Operacion(
        n_operaciones=n_operaciones,
        ejecutar=ejecutar,
        reiniciar_contadores=tabla.reset_stats,
        capturar_contadores=contadores,
        medir_espacio=lambda: (archivo.stat().st_size, 0),
        validar=validar,
        configuracion=configuracion,
    )


def caso_insercion(nativo, tecnica: str, page_size: int | None = None) -> Caso:
    """N insert() en el orden original y flush final, sobre una tabla nueva."""

    @contextmanager
    def preparar(directorio: Path, dataset: Dataset):
        with _abrir_tabla(nativo, directorio, tecnica, page_size) as (db, tabla, archivo, config):
            db.flush()
            config["flush"] = "incluido_al_final_de_la_insercion"
            yield _operacion(
                tabla,
                archivo,
                len(dataset.registros),
                lambda: _insertar(db, tabla, dataset.registros),
                lambda: _validar_registros(tabla, dataset.registros),
                config,
            )

    return Caso(f"{tecnica}_insercion", tecnica, "insercion", preparar)


def caso_busqueda_pk(
    nativo, tecnica: str, consultas: int = 1000, page_size: int | None = None
) -> Caso:
    """Lote de busquedas exitosas; resultados materializados dentro y validados fuera."""

    @contextmanager
    def preparar(directorio: Path, dataset: Dataset):
        claves = seleccionar_claves(len(dataset.registros), consultas)
        por_codigo = {fila[0]: fila for fila in dataset.registros}
        esperados = [por_codigo[clave] for clave in claves]
        resultados = []
        with _abrir_tabla(nativo, directorio, tecnica, page_size) as (db, tabla, archivo, config):
            _insertar(db, tabla, dataset.registros)
            config.update(
                semilla_consultas=SEMILLA,
                consultas=consultas,
                seleccion="sample_sin_reemplazo_sobre_codigos_1_a_N",
                claves_sha256=_hash_claves(claves),
                flush="solo_en_preparacion",
            )

            def buscar():
                for clave in claves:
                    resultados.append(tabla.search(clave))

            def validar():
                if len(resultados) != consultas or any(
                    [tuple(fila) for fila in filas] != [esperado]
                    for filas, esperado in zip(resultados, esperados, strict=True)
                ):
                    raise RuntimeError("las busquedas PK no devolvieron los registros esperados")

            yield _operacion(tabla, archivo, consultas, buscar, validar, config)

    return Caso(f"{tecnica}_busqueda_pk", tecnica, "busqueda_pk", preparar)


def caso_reorganizacion(nativo, *, automatica: bool = False, page_size: int | None = None) -> Caso:
    """Prepara 30% sin reorganizar; mide reorganize() o la eliminacion siguiente, no ambos."""
    operacion = "eliminacion_con_reorganizacion" if automatica else "reorganizacion_explicita"

    @contextmanager
    def preparar(directorio: Path, dataset: Dataset):
        n = len(dataset.registros)
        borrados = cantidad_borrados(n)
        claves = seleccionar_claves(n, borrados + 1)
        eliminados_finales = set(claves[: borrados + int(automatica)])
        esperados = tuple(fila for fila in dataset.registros if fila[0] not in eliminados_finales)
        with _abrir_tabla(nativo, directorio, "sequential", page_size) as (
            db,
            tabla,
            archivo,
            config,
        ):
            _insertar(db, tabla, dataset.registros)
            for clave in claves[:borrados]:
                if tabla.remove(clave) != 1:
                    raise RuntimeError("no se pudo preparar la eliminacion del 30%")
            desperdicio = nativo.wasted_ratio(tabla)
            if tabla.size() != n - borrados or not math.isclose(
                desperdicio, 0.3, rel_tol=0, abs_tol=1e-12
            ):
                raise RuntimeError("se esperaba 30% de desperdicio sin reorganizacion automatica")
            db.flush()
            config.update(
                semilla_eliminaciones=SEMILLA,
                claves_eliminacion_sha256=_hash_claves(claves[:borrados]),
                borrados_preparacion=borrados,
                desperdicio_antes=desperdicio,
                registros_vivos_antes=n - borrados,
                registros_vivos_despues_esperados=len(esperados),
                desperdicio_despues_esperado=0,
                clave_disparadora=claves[borrados] if automatica else "no_aplica",
                flush="incluido_al_final_de_la_operacion",
                alcance_tiempo=(
                    "remove_busqueda_eliminacion_metadatos_reorganizacion_flush"
                    if automatica
                    else "reorganize_y_flush"
                ),
            )
            eliminados = None

            def ejecutar():
                nonlocal eliminados
                if automatica:
                    eliminados = tabla.remove(claves[borrados])
                else:
                    nativo.reorganize(tabla)
                db.flush()

            def validar():
                if automatica and eliminados != 1:
                    raise RuntimeError(
                        "la eliminacion disparadora no elimino exactamente un registro"
                    )
                if nativo.wasted_ratio(tabla) != 0:
                    raise RuntimeError("la reorganizacion no dejo desperdicio cero")
                _validar_registros(tabla, esperados)

            yield _operacion(tabla, archivo, 1, ejecutar, validar, config)

    return Caso(f"sequential_{operacion}", "sequential", operacion, preparar)


def casos_archivos(nativo, consultas: int = 1000, page_size: int | None = None) -> list[Caso]:
    """Suite #39: seis casos, sin duplicar el banco ni crear una reorganizacion Heap ficticia."""
    return [
        caso_insercion(nativo, "heap", page_size),
        caso_insercion(nativo, "sequential", page_size),
        caso_busqueda_pk(nativo, "heap", consultas, page_size),
        caso_busqueda_pk(nativo, "sequential", consultas, page_size),
        caso_reorganizacion(nativo, page_size=page_size),
        caso_reorganizacion(nativo, automatica=True, page_size=page_size),
    ]
