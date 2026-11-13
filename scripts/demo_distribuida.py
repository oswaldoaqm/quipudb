"""Recorrido de QuipuDB distribuido para la demo y las evidencias del #280.

Inserta en los dos nodos, busca por id, combina resultados, borra, muestra un
error y, si levantó el clúster él mismo, apaga n2 para mostrar qué deja de
funcionar. Imprime cada petición con su respuesta.

Levantando todo en local (n1 en 8001, n2 en 8002, coordinador en 8000)::

    python -m scripts.demo_distribuida --root /tmp/quipudb-demo

Contra un clúster ya levantado, por ejemplo el de docker-compose.distribuido.yml::

    python -m scripts.demo_distribuida --url http://localhost:8000 \\
        --nodo n1=http://localhost:8001 --nodo n2=http://localhost:8002
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
CATALOGO = ROOT / "docs/ejemplos/catalogo-nodos-locales-v1.json"

FILAS = [
    (1, "quipu"),
    (42, "chasqui"),
    (9999, "frontera n1"),
    (10000, "frontera n2"),
    (25000, "tambo"),
    (-2147483648, "minimo int"),
    (2147483647, "maximo int"),
]


class Demo:
    def __init__(self, url: str, nodos: dict[str, str]) -> None:
        self.url = url.rstrip("/")
        self.nodos = nodos

    # -- salida ---------------------------------------------------------------

    @staticmethod
    def titulo(texto: str) -> None:
        print(f"\n== {texto}")

    @staticmethod
    def mostrar(response: httpx.Response) -> dict:
        body = response.json()
        if "error" in body:
            print(f"   {response.status_code}  {body['error']}")
        elif "rows" in body and body["columns"]:
            print(f"   {response.status_code}  {body['columns']}")
            for row in body["rows"]:
                print(f"        {row}")
            if not body["rows"]:
                print("        (sin filas)")
        elif "affected_rows" in body:
            print(f"   {response.status_code}  affected_rows={body['affected_rows']}")
        return body

    # -- peticiones -----------------------------------------------------------

    def sql(self, sentencia: str) -> dict:
        print(f"-> {sentencia}")
        return self.mostrar(httpx.post(f"{self.url}/query", json={"sql": sentencia}, timeout=60))

    def health(self) -> None:
        print("-> GET /health")
        response = httpx.get(f"{self.url}/health", timeout=30)
        body = response.json()
        print(f"   {response.status_code}  {body['status']}")
        for nodo in body["nodes"]:
            estado = "listo" if nodo["ready"] else f"no listo: {nodo['reason']}"
            print(f"        {nodo['node_id']} {nodo['fragment_ids']} {estado}")

    def tables(self) -> None:
        print("-> GET /tables")
        response = httpx.get(f"{self.url}/tables", timeout=30)
        body = response.json()
        if response.status_code != 200:
            print(f"   {response.status_code}  {body['error']}")
            return
        for tabla in body:
            print(f"   {response.status_code}  {tabla['name']}: {tabla['record_count']} registros")

    def claves_por_nodo(self) -> None:
        """Pregunta a cada nodo, por su ruta interna, qué claves guarda."""

        for nodo, fragmento in (("n1", "items_f0"), ("n2", "items_f1")):
            body = {
                "protocol_version": 1,
                "catalog_version": 1,
                "request_id": str(uuid4()),
                "table": "items",
                "sql": "SELECT * FROM items",
            }
            url = f"{self.nodos[nodo]}/internal/v1/fragments/{fragmento}/scan"
            rows = httpx.post(url, json=body, timeout=30).json()["result"]["rows"]
            print(f"   {nodo} / {fragmento}: {sorted(r[0] for r in rows)}")

    # -- recorrido ------------------------------------------------------------

    def recorrido(self) -> None:
        self.titulo("Estado del clúster")
        self.health()

        # Si la demo ya corrió contra este clúster, empieza de cero.
        for key, _ in FILAS:
            httpx.post(
                f"{self.url}/query", json={"sql": f"DELETE FROM items WHERE id = {key}"}, timeout=30
            )

        self.titulo("Inserción: cada fila va al nodo dueño de su id")
        for key, nombre in FILAS:
            self.sql(f"INSERT INTO items VALUES ({key}, '{nombre}')")

        self.titulo("Dónde quedó cada fila, preguntando a cada nodo")
        self.claves_por_nodo()

        self.titulo("Búsqueda por id: solo consulta al dueño")
        self.sql("SELECT * FROM items WHERE id = 9999")
        self.sql("SELECT * FROM items WHERE id = 10000")

        self.titulo("Consultas que combinan los dos nodos")
        self.sql("SELECT nombre, id FROM items ORDER BY id DESC LIMIT 3")
        self.sql("SELECT id, nombre FROM items WHERE id >= 9999 ORDER BY id")
        self.sql("SELECT id FROM items WHERE nombre = 'tambo'")

        self.titulo("Clave duplicada")
        self.sql("INSERT INTO items VALUES (10000, 'otra vez')")

        self.titulo("Borrado por id")
        self.sql("DELETE FROM items WHERE id = 42")
        self.sql("DELETE FROM items WHERE id = 42")
        self.claves_por_nodo()

        self.titulo("Fuera de lo que admite v1")
        self.sql("SELECT * FROM items ORDER BY nombre")

        self.titulo("Vista lógica")
        self.tables()

    def nodo_caido(self) -> None:
        self.titulo("n2 apagado")
        self.health()
        self.sql("SELECT * FROM items ORDER BY id")
        self.sql("SELECT id, nombre FROM items WHERE id < 10000 ORDER BY id")
        self.sql("INSERT INTO items VALUES (10001, 'sin dueno')")


# -- levantar en local ----------------------------------------------------------


def lanzar(args: list[str], log: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with log.open("w") as salida:
        return subprocess.Popen(
            [sys.executable, "-m", *args], cwd=ROOT, env=env, stdout=salida, stderr=salida
        )


def esperar(url: str, proceso: subprocess.Popen, log: Path) -> None:
    limite = time.monotonic() + 30
    while time.monotonic() < limite:
        if proceso.poll() is not None:
            break
        try:
            httpx.get(url, timeout=2)
            return
        except httpx.TransportError:
            time.sleep(0.1)
    raise SystemExit(f"no arrancó {url}:\n{log.read_text()}")


def levantar(root: Path) -> dict[str, subprocess.Popen]:
    root.mkdir(parents=True, exist_ok=True)
    comun = ["--catalog", str(CATALOGO), "--catalog-version", "1"]
    procesos = {}
    for nodo, puerto in (("n1", 8001), ("n2", 8002)):
        datos = root / nodo
        subprocess.run(
            [sys.executable, "-m", "scripts.preparar_nodo", *comun, "--node-id", nodo,
             "--data-dir", str(datos)],
            cwd=ROOT,
            check=True,
        )  # fmt: skip
        log = root / f"{nodo}.log"
        procesos[nodo] = lanzar(
            ["engine.distributed.node_server", *comun, "--node-id", nodo, "--data-dir",
             str(datos), "--port", str(puerto)],
            log,
        )  # fmt: skip
        esperar(f"http://127.0.0.1:{puerto}/internal/v1/health", procesos[nodo], log)
    log = root / "coordinador.log"
    procesos["coordinador"] = lanzar(["engine.distributed.coordinator", *comun], log)
    esperar("http://127.0.0.1:8000/health", procesos["coordinador"], log)
    return procesos


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, help="levanta n1, n2 y el coordinador aquí")
    parser.add_argument("--url", help="coordinador ya levantado")
    parser.add_argument("--nodo", action="append", default=[], help="n1=http://... para mirarlo")
    args = parser.parse_args()
    if (args.root is None) == (args.url is None):
        parser.error("usa --root para levantar el clúster o --url para uno ya levantado")

    if args.url:
        nodos = dict(n.split("=", 1) for n in args.nodo)
        if set(nodos) != {"n1", "n2"}:
            parser.error("con --url indica --nodo n1=URL y --nodo n2=URL")
        Demo(args.url, nodos).recorrido()
        return

    procesos = levantar(args.root)
    try:
        demo = Demo(
            "http://127.0.0.1:8000", {"n1": "http://127.0.0.1:8001", "n2": "http://127.0.0.1:8002"}
        )
        demo.recorrido()
        procesos["n2"].terminate()
        procesos["n2"].wait(timeout=10)
        demo.nodo_caido()
    finally:
        for proceso in procesos.values():
            if proceso.poll() is None:
                proceso.terminate()
                proceso.wait(timeout=10)


if __name__ == "__main__":
    main()
