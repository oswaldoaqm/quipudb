"""Clúster real para las pruebas integradas del #280.

Cada nodo y el coordinador son procesos separados que se hablan por TCP, como
en un despliegue. Las pruebas solo usan HTTP: la API pública del coordinador y,
para comprobar dónde quedó cada fila, las rutas internas de los nodos.
"""

from __future__ import annotations

import copy
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]

CATALOGO = {
    "protocol_version": 1,
    "catalog_version": 1,
    "nodes": [
        {"node_id": "n1", "base_url": "http://127.0.0.1:0"},
        {"node_id": "n2", "base_url": "http://127.0.0.1:0"},
    ],
    "tables": [
        {
            "name": "items",
            "mode": "range",
            "storage": "heap",
            "primary_key": "id",
            "columns": [
                {"name": "id", "type": "INT", "size": None},
                {"name": "nombre", "type": "VARCHAR", "size": 40},
                {"name": "precio", "type": "DOUBLE", "size": None},
                {"name": "fecha", "type": "DATE", "size": None},
            ],
            "indexes": [
                {
                    "name": "por_id",
                    "column": "id",
                    "structure": "bplus_unclustered",
                    "supports_range": True,
                },
                {
                    "name": "por_nombre",
                    "column": "nombre",
                    "structure": "extendible_hash",
                    "supports_range": False,
                },
            ],
            "fragments": [
                {"fragment_id": "items_f0", "node_id": "n1", "lower": None, "upper": 10000},
                {"fragment_id": "items_f1", "node_id": "n2", "lower": 10000, "upper": None},
            ],
        },
        {
            "name": "notas",
            "mode": "single",
            "storage": "heap",
            "primary_key": "codigo",
            "columns": [
                {"name": "codigo", "type": "INT", "size": None},
                {"name": "texto", "type": "VARCHAR", "size": 60},
            ],
            "indexes": [],
            "fragments": [
                {"fragment_id": "notas_f0", "node_id": "n2", "lower": None, "upper": None}
            ],
        },
    ],
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def prepare_node(catalog, node_id: str, directory: Path) -> None:
    """Crea las tablas y los índices que el catálogo asigna a ``node_id``.

    ``prepare_range_node`` del #276 solo acepta nodos con tablas ``range``; n2
    también aloja una ``single``, así que aquí se crean las dos con el motor.
    """

    from engine.distributed.storage import _close
    from engine.executor.native import load_native, to_native_schema

    native = load_native()
    directory.mkdir(parents=True)
    database = native.Database(directory / "catalogo.txt")
    try:
        for table in catalog.tables:
            if any(f.node_id == node_id for f in table.fragments):
                database.create_table(to_native_schema(table.bound_schema, native), table.storage)
                for index in table.indexes:
                    database.create_index(table.name, index.name, index.column, index.structure)
    finally:
        _close(database)


class Process:
    """Un proceso Python del repo que expone HTTP en ``port``."""

    def __init__(self, name: str, args: list[str], port: int, health: str, log_dir: Path):
        self.name = name
        self.args = args
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.health = health
        self.log_path = log_dir / f"{name}.log"
        self.process: subprocess.Popen | None = None

    def start(self, *, expect: tuple[int, ...] = (200,)) -> None:
        import quipudb_native

        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(Path(quipudb_native.__file__).parent)])
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        log = self.log_path.open("a")
        self.process = subprocess.Popen(
            [sys.executable, "-m", *self.args, "--port", str(self.port)],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        log.close()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                break
            try:
                status = httpx.get(self.url + self.health, timeout=2).status_code
            except httpx.TransportError:
                time.sleep(0.05)
                continue
            if status in expect:
                return
            break
        self.stop()
        raise AssertionError(f"{self.name} no arrancó:\n{self.log_path.read_text()}")

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            if hasattr(signal, "SIGCONT"):
                self.process.send_signal(signal.SIGCONT)
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process = None

    def pause(self) -> None:
        """Congela el proceso: acepta conexiones pero no responde."""

        self.process.send_signal(signal.SIGSTOP)

    def resume(self) -> None:
        self.process.send_signal(signal.SIGCONT)


class Cluster:
    """Dos nodos y un coordinador sobre ``CATALOGO``.

    ``node_versions`` permite arrancar un nodo con otra ``catalog_version``
    que la del coordinador; ``timeout`` es el de lectura del coordinador.
    """

    def __init__(self, root: Path, *, node_versions=None, timeout: float = 30.0):
        from engine.distributed.catalog import DistributedCatalog

        self.root = root
        root.mkdir()
        ports = {"n1": free_port(), "n2": free_port(), "coordinador": free_port()}
        while len(set(ports.values())) != 3:
            ports["n2"], ports["coordinador"] = free_port(), free_port()
        data = copy.deepcopy(CATALOGO)
        for node in data["nodes"]:
            node["base_url"] = f"http://127.0.0.1:{ports[node['node_id']]}"
        self.catalog = DistributedCatalog.model_validate(data)
        self.configs = {}
        for version in {1, *(node_versions or {}).values()}:
            path = root / f"catalogo-v{version}.json"
            path.write_text(json.dumps({**data, "catalog_version": version}), encoding="utf-8")
            self.configs[version] = path
        self.nodes = {}
        for node_id in ("n1", "n2"):
            version = (node_versions or {}).get(node_id, 1)
            directory = root / node_id
            prepare_node(self.catalog, node_id, directory)
            self.nodes[node_id] = Process(
                node_id,
                [
                    "engine.distributed.node_server",
                    "--catalog",
                    str(self.configs[version]),
                    "--catalog-version",
                    str(version),
                    "--node-id",
                    node_id,
                    "--data-dir",
                    str(directory),
                ],
                ports[node_id],
                "/internal/v1/health",
                root,
            )
        self.coordinator = Process(
            "coordinador",
            [
                "engine.distributed.coordinator",
                "--catalog",
                str(self.configs[1]),
                "--catalog-version",
                "1",
                "--timeout",
                str(timeout),
            ],
            ports["coordinador"],
            "/health",
            root,
        )

    def start(self) -> Cluster:
        for node in self.nodes.values():
            node.start()
        # 503 si algún nodo no está listo; la prueba decide si eso es lo esperado.
        self.coordinator.start(expect=(200, 503))
        return self

    def stop(self) -> None:
        for process in (self.coordinator, *self.nodes.values()):
            process.stop()

    def query(self, sql: str) -> httpx.Response:
        return httpx.post(self.coordinator.url + "/query", json={"sql": sql}, timeout=60)

    def ok(self, sql: str) -> dict:
        response = self.query(sql)
        assert response.status_code == 200, (sql, response.json())
        return response.json()

    def get(self, path: str) -> httpx.Response:
        return httpx.get(self.coordinator.url + path, timeout=30)

    def node_post(self, node_id: str, path: str, **fields) -> httpx.Response:
        """Una ruta interna de un nodo, con el sobre v1 de la versión 1."""

        from uuid import uuid4

        body = {"protocol_version": 1, "catalog_version": 1, "request_id": str(uuid4())}
        body.update(fields)
        url = self.nodes[node_id].url + "/internal/v1" + path
        return httpx.post(url, json=body, timeout=30)

    def keys(self, node_id: str, fragment_id: str) -> list[int]:
        """Las claves que guarda físicamente un fragmento, preguntándole al nodo."""

        response = self.node_post(
            node_id, f"/fragments/{fragment_id}/scan", table="items", sql="SELECT * FROM items"
        )
        assert response.status_code == 200, response.json()
        return sorted(row[0] for row in response.json()["result"]["rows"])


@pytest.fixture
def make_cluster(tmp_path):
    pytest.importorskip("quipudb_native", reason="el clúster necesita el motor compilado")
    pytest.importorskip("uvicorn")
    clusters = []

    def make(**options) -> Cluster:
        cluster = Cluster(tmp_path / f"c{len(clusters)}", **options)
        clusters.append(cluster)
        return cluster

    try:
        yield make
    finally:
        for cluster in clusters:
            cluster.stop()


@pytest.fixture
def cluster(make_cluster):
    return make_cluster().start()
