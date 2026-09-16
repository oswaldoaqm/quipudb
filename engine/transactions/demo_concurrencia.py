"""Simulacion con hilos: race conditions y como el lock manager las evita (#32).

Corre el mismo ciclo de transacciones concurrentes (BEGIN, SELECT, DELETE,
INSERT, END sobre una fila compartida — el equivalente a un UPDATE con lo que
ofrece el subconjunto SQL) en dos modos, sin cambiar una linea de ese codigo:

  - ``sin-lock``: cada hilo usa su propio ``LockManager`` privado (nadie
    coordina de verdad entre "conexiones"). Se ven actualizaciones perdidas
    y, a veces, errores por la interseccion de un DELETE/INSERT ajeno.
  - ``con-lock``: todos los hilos comparten un unico ``LockManager`` (ADR
    0005). El resultado final es correcto y reproducible en cada corrida; si
    dos transacciones compiten por el mismo upgrade compartido->exclusivo
    (el interbloqueo que muestra ``material/05 Recuperación ante Fallos.pdf``),
    el timeout aborta una de las dos automaticamente y el hilo reintenta.

Uso (pensado para grabar en pantalla, corriendo cada modo por separado):

    python -m engine.transactions.demo_concurrencia --modo sin-lock
    python -m engine.transactions.demo_concurrencia --modo con-lock
    python -m engine.transactions.demo_concurrencia               # ambos

Necesita los bindings compilados: ``cmake -S . -B build-py
-DQUIPUDB_BUILD_PYTHON=ON && cmake --build build-py``.
"""

from __future__ import annotations

import argparse
import random
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from engine.executor import QueryProcessor
from engine.executor.native import load_native
from engine.transactions import LockManager, LockTimeoutError

_LOCK_TIMEOUT = 0.3
_TABLE = "contador"

_console_lock = threading.Lock()


def _log(start: float, label: str, message: str) -> None:
    elapsed = time.monotonic() - start
    with _console_lock:
        print(f"[t+{elapsed:6.3f}s] [{label:>8}] {message}")


def _increment_once(
    processor: QueryProcessor,
    label: str,
    attempt: int,
    delay: float,
    start: float,
    *,
    reintentar_en_timeout: bool,
) -> bool:
    """Un ciclo BEGIN/SELECT/DELETE/INSERT/END sobre la fila compartida.

    Devuelve True si confirmo, False si fallo sin coordinacion (el caso que
    ``sin-lock`` esta pensado para mostrar). El retraso entre leer y escribir
    ensancha la ventana de la carrera para que se alcance a ver en pantalla;
    sin el, la carrera igual existe pero puede pasar demasiado rapido para
    notarla a simple vista.
    """

    while True:
        try:
            _log(start, label, f"intento #{attempt}: BEGIN TRANSACTION")
            processor.execute("BEGIN TRANSACTION")
            (row,) = processor.execute(f"SELECT valor FROM {_TABLE} WHERE id = 1").rows
            valor_leido = row[0]
            _log(start, label, f"leyo valor={valor_leido}")
            time.sleep(delay)
            nuevo_valor = valor_leido + 1
            processor.execute(f"DELETE FROM {_TABLE} WHERE id = 1")
            processor.execute(f"INSERT INTO {_TABLE} VALUES (1, {nuevo_valor})")
            processor.execute("END TRANSACTION")
            _log(start, label, f"confirmo valor={nuevo_valor}")
            return True
        except LockTimeoutError:
            if not reintentar_en_timeout:
                raise
            # Sin un retraso aleatorio antes de reintentar, dos hilos que
            # chocaron una vez tienden a seguir chocando ronda tras ronda:
            # ambos llegan de nuevo a BEGIN TRANSACTION casi en el mismo
            # instante y repiten el mismo interbloqueo de upgrade. El jitter
            # rompe esa sincronizacion.
            espera = random.uniform(0.02, 0.2)
            _log(
                start,
                label,
                "tiempo de espera agotado (posible interbloqueo de upgrade "
                f"compartido->exclusivo); se deshizo y reintenta en {espera:.3f}s",
            )
            time.sleep(espera)
            continue
        except Exception as error:  # noqa: BLE001 - es justo lo que "sin-lock" debe mostrar
            _log(start, label, f"fallo sin coordinacion: {error}")
            return False


def _worker(
    thread_index: int,
    database: Any,
    native: Any,
    lock_manager: LockManager | None,
    incrementos: int,
    delay: float,
    start: float,
    listos: threading.Barrier,
    resultados: list[bool],
) -> None:
    label = f"hilo-{thread_index}"
    processor = QueryProcessor(
        database,
        native,
        lock_manager=lock_manager if lock_manager is not None else LockManager(),
        lock_timeout=_LOCK_TIMEOUT,
    )
    listos.wait()
    for attempt in range(1, incrementos + 1):
        resultados.append(
            _increment_once(
                processor,
                label,
                attempt,
                delay,
                start,
                reintentar_en_timeout=lock_manager is not None,
            )
        )


def run_mode(modo: str, *, hilos: int, incrementos_por_hilo: int, delay: float) -> bool:
    """Corre un modo de la simulacion. Devuelve True si el resultado fue correcto."""

    native = load_native()
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"quipudb_demo_{modo}_"))
    try:
        database = native.Database(tmp_dir / "catalogo.txt")
        setup = QueryProcessor(database, native)
        setup.execute(f"CREATE TABLE {_TABLE} (id INT PRIMARY KEY, valor INT) USING HEAP")
        setup.execute(f"INSERT INTO {_TABLE} VALUES (1, 0)")

        titulo = "SIN LOCK MANAGER COMPARTIDO" if modo == "sin-lock" else "CON LOCK MANAGER COMPARTIDO"
        print()
        print("=" * 72)
        print(f" MODO: {titulo}")
        print("=" * 72)

        lock_manager = LockManager() if modo == "con-lock" else None
        resultados: list[bool] = []
        listos = threading.Barrier(hilos)
        start = time.monotonic()
        threads = [
            threading.Thread(
                target=_worker,
                args=(
                    i,
                    database,
                    native,
                    lock_manager,
                    incrementos_por_hilo,
                    delay,
                    start,
                    listos,
                    resultados,
                ),
                name=f"hilo-{i}",
            )
            for i in range(1, hilos + 1)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        (final_row,) = setup.execute(f"SELECT valor FROM {_TABLE} WHERE id = 1").rows
        valor_final = final_row[0]
        esperado = hilos * incrementos_por_hilo
        confirmados = sum(resultados)

        print("-" * 72)
        print(f" intentos confirmados: {confirmados}/{len(resultados)}")
        print(f" valor final: {valor_final}  (esperado sin perdidas: {esperado})")
        correcto = valor_final == esperado and confirmados == esperado
        if correcto:
            print(" resultado: CORRECTO -- ninguna actualizacion se perdio")
        else:
            print(
                " resultado: INCORRECTO -- race condition (actualizaciones "
                "perdidas y/o fallidas por falta de coordinacion)"
            )
        print("=" * 72)
        return correcto
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--modo", choices=["sin-lock", "con-lock", "ambos"], default="ambos")
    parser.add_argument("--hilos", type=int, default=4)
    parser.add_argument("--incrementos-por-hilo", type=int, default=5)
    parser.add_argument(
        "--retraso",
        type=float,
        default=0.05,
        help="segundos entre leer y escribir, para que la carrera se vea en pantalla",
    )
    args = parser.parse_args()

    modos = ["sin-lock", "con-lock"] if args.modo == "ambos" else [args.modo]
    for modo in modos:
        run_mode(
            modo,
            hilos=args.hilos,
            incrementos_por_hilo=args.incrementos_por_hilo,
            delay=args.retraso,
        )


if __name__ == "__main__":
    main()
