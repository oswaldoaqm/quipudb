"""La carga desde la linea de comandos (#302), con el motor simulado."""

import ast
import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from app.carga.dataset import main
from app.carga.esquema import PERSONAS, ROSTROS
from app.dataset.lfw import escribir
from app.motor import ClienteMotor
from app.rostros.embedding import DIMENSIONES

SERVIDOR = Path(__file__).resolve().parents[1]


# --- el aislamiento que exige el enunciado ------------------------------------


def test_ningun_modulo_de_rikuq_importa_el_motor() -> None:
    """El enunciado de la Parte 5 pide consumir el API REST, no el paquete."""

    prohibidos = {"engine", "quipudb_native"}
    culpables = []

    for archivo in (SERVIDOR / "app").rglob("*.py"):
        arbol = ast.parse(archivo.read_text(encoding="utf-8"), filename=str(archivo))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                nombres = [alias.name for alias in nodo.names]
            elif isinstance(nodo, ast.ImportFrom):
                nombres = [nodo.module or ""]
            else:
                continue
            if any(n.split(".")[0] in prohibidos for n in nombres):
                culpables.append(f"{archivo.name}:{nodo.lineno}")

    assert culpables == [], f"importan el motor: {culpables}"


def _manifiesto(tmp_path: Path, cuantas: int = 3) -> Path:
    from app.dataset.lfw import Rostro

    rostros = [
        Rostro("Ana", f"lfw/Ana/Ana_{i:04d}.jpg", "abc", "lfw") for i in range(1, cuantas + 1)
    ]
    ruta = tmp_path / "rostros.tsv"
    escribir(rostros, ruta)
    return ruta


@pytest.fixture
def motor_falso(monkeypatch: pytest.MonkeyPatch):
    recibidas: list[str] = []

    def handler(peticion: httpx.Request) -> httpx.Response:
        if peticion.url.path == "/tables":
            return httpx.Response(200, json=[])
        recibidas.append(json.loads(peticion.read())["sql"])
        return httpx.Response(200, json={"affected_rows": 1})

    original = ClienteMotor.desde

    @classmethod
    def desde(cls, settings, transport=None):
        return original.__func__(cls, settings, httpx.MockTransport(handler))

    monkeypatch.setattr(ClienteMotor, "desde", desde)
    monkeypatch.setattr(
        "app.carga.dataset.vectores_de",
        lambda *_: (np.full(DIMENSIONES, 0.5, dtype=np.float32),),
    )
    monkeypatch.setattr("app.carga.dataset.crear_detector", lambda *_, **__: object())
    monkeypatch.setattr("app.carga.dataset.crear_descriptor", lambda *_, **__: object())
    return recibidas


def test_main_crea_el_esquema_y_carga(tmp_path: Path, motor_falso, capsys) -> None:
    ruta = _manifiesto(tmp_path)

    assert main(["--manifiesto", str(ruta), "--datos", str(tmp_path)]) == 0

    enviado = "\n".join(motor_falso)
    assert "CREATE TABLE personas" in enviado
    assert "USING HNSW WITH METRIC=cosine" in enviado
    assert "1 personas y 3 rostros cargados" in capsys.readouterr().out


def test_main_respeta_el_limite(tmp_path: Path, motor_falso, capsys) -> None:
    """Para probar la carga sin esperar a las 4324 imagenes."""

    ruta = _manifiesto(tmp_path, cuantas=5)

    main(["--manifiesto", str(ruta), "--datos", str(tmp_path), "--limite", "2"])

    assert "1 personas y 2 rostros cargados" in capsys.readouterr().out


def test_main_rehacer_borra_las_tablas_primero(tmp_path: Path, motor_falso) -> None:
    ruta = _manifiesto(tmp_path, cuantas=1)

    main(["--manifiesto", str(ruta), "--datos", str(tmp_path), "--rehacer"])

    assert motor_falso[0] == f"DROP TABLE {ROSTROS}"
    assert motor_falso[1] == f"DROP TABLE {PERSONAS}"


def test_main_no_recrea_un_esquema_que_ya_esta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    recibidas: list[str] = []

    def handler(peticion: httpx.Request) -> httpx.Response:
        if peticion.url.path == "/tables":
            return httpx.Response(200, json=[{"name": PERSONAS}, {"name": ROSTROS}])
        recibidas.append(json.loads(peticion.read())["sql"])
        return httpx.Response(200, json={"affected_rows": 1})

    original = ClienteMotor.desde

    @classmethod
    def desde(cls, settings, transport=None):
        return original.__func__(cls, settings, httpx.MockTransport(handler))

    monkeypatch.setattr(ClienteMotor, "desde", desde)
    monkeypatch.setattr(
        "app.carga.dataset.vectores_de",
        lambda *_: (np.full(DIMENSIONES, 0.5, dtype=np.float32),),
    )
    monkeypatch.setattr("app.carga.dataset.crear_detector", lambda *_, **__: object())
    monkeypatch.setattr("app.carga.dataset.crear_descriptor", lambda *_, **__: object())

    main(["--manifiesto", str(_manifiesto(tmp_path, 1)), "--datos", str(tmp_path)])

    assert not any(sql.startswith("CREATE TABLE") for sql in recibidas)


def test_main_explica_el_motor_apagado_en_vez_de_reventar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Ante esto hay que levantar QuipuDB, no corregir el SQL."""

    def handler(_peticion: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("conexion rechazada")

    original = ClienteMotor.desde

    @classmethod
    def desde(cls, settings, transport=None):
        return original.__func__(cls, settings, httpx.MockTransport(handler))

    monkeypatch.setattr(ClienteMotor, "desde", desde)

    assert main(["--manifiesto", str(_manifiesto(tmp_path, 1)), "--datos", str(tmp_path)]) == 1
    assert "revisa que este levantado" in capsys.readouterr().err
