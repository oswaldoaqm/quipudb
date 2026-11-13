"""Harness de pruebas de nodos HTTP reales; no es un cliente del coordinador."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class RunningNode:
    def __init__(self, config, node_id, directory, port, *, ready=True):
        self.url = f"http://127.0.0.1:{port}"
        self.config = config
        self.node_id = node_id
        self.directory = directory
        self.port = port
        self.ready = ready
        self.process = None
        self.log = None

    def start(self):
        import quipudb_native

        root = Path(__file__).resolve().parents[2]
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([str(root), str(Path(quipudb_native.__file__).parent)])
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.log = (self.directory.parent / f"server-{self.port}.log").open("w+")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "engine.distributed.node_server",
                "--catalog",
                str(self.config),
                "--catalog-version",
                "1",
                "--node-id",
                self.node_id,
                "--data-dir",
                str(self.directory),
                "--port",
                str(self.port),
            ],
            cwd=root,
            env=env,
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    break
                try:
                    status, body = self.request("/health")
                    if status == (200 if self.ready else 503):
                        return body
                    raise AssertionError(f"salud inesperada: {status}, {body}")
                except (URLError, TimeoutError, ConnectionError):
                    time.sleep(0.05)
            self.log.seek(0)
            raise AssertionError(self.log.read())
        except BaseException:
            self.close()
            raise

    def request(self, path, body=None):
        request = Request(
            self.url + "/internal/v1" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)

    def close(self):
        try:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
                    raise
        finally:
            if self.log is not None:
                self.log.close()
            self.process = None
            self.log = None
