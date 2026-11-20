"""Registrar, listar y eliminar fotos desde la interfaz (#342).

Las pruebas HTTP usan un pipeline falso y un motor falso con estado, que
entiende solo el SQL que manda el registro. La ultima corre contra un QuipuDB
real y aislado (se salta sin bindings): registra, identifica, elimina y
vuelve a identificar, para comprobar que el DELETE tambien saca el vector
del indice HNSW.
"""

import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import registro
from app.carga.esquema import PERSONAS, ROSTROS
from app.config import Settings
from app.consulta.identificar import identificar
from app.main import create_app
from app.motor import ClienteMotor
from app.pipeline import ImagenEntrada, RostroEntrada
from app.rostros.constantes import DIMENSIONES
from tests.motor_aislado import motor_aislado as _motor_aislado  # noqa: F401 -- fixture de pytest
from tests.pipeline_falso import PipelineFalso

JPEG = b"\xff\xd8\xff\xe0" + b"foto" * 10
PNG = b"\x89PNG\r\n\x1a\n" + b"foto" * 10


class MotorFalso:
    """Dos tablas en memoria y el SQL del registro, nada mas."""

    def __init__(self, *, con_tablas: bool = True) -> None:
        self.tablas: dict[str, dict[int, tuple]] = {PERSONAS: {}, ROSTROS: {}} if con_tablas else {}
        self.sentencias: list[str] = []

    def transporte(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._atender)

    def _atender(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tables":
            return httpx.Response(200, json=[{"name": nombre} for nombre in self.tablas])
        lote = json.loads(request.content)["sql"]
        columnas, filas = [], []
        for sentencia in lote.split(";\n"):
            self.sentencias.append(sentencia)
            columnas, filas = self._ejecutar(sentencia.strip())
        return httpx.Response(200, json={"columns": columnas, "rows": filas})

    def _ejecutar(self, sql: str) -> tuple[list[str], list[list]]:
        if m := re.fullmatch(r"CREATE TABLE (\w+) \(.*", sql, re.S):
            self.tablas[m[1]] = {}
            return [], []
        if sql.startswith("CREATE INDEX"):
            return [], []
        if m := re.fullmatch(r"INSERT INTO (\w+) VALUES \((.*)\)", sql, re.S):
            valores = self._valores(m[2])
            assert valores[0] not in self.tablas[m[1]], f"id repetido en {m[1]}"
            self.tablas[m[1]][valores[0]] = tuple(valores)
            return [], []
        if m := re.fullmatch(r"DELETE FROM (\w+) WHERE id = (\d+)", sql):
            self.tablas[m[1]].pop(int(m[2]))
            return [], []
        if m := re.fullmatch(r"SELECT id FROM (\w+) ORDER BY id DESC LIMIT 1", sql):
            ids = sorted(self.tablas[m[1]], reverse=True)[:1]
            return ["id"], [[i] for i in ids]
        if m := re.fullmatch(
            r"SELECT id, nombre, origen, fotos FROM personas WHERE (nombre|id) = (.+)", sql
        ):
            buscado = self._valores(m[2])[0]
            columna = 1 if m[1] == "nombre" else 0
            return ["id", "nombre", "origen", "fotos"], [
                list(f) for f in self.tablas[PERSONAS].values() if f[columna] == buscado
            ]
        if sql == "SELECT id, nombre, origen, fotos FROM personas":
            return ["id", "nombre", "origen", "fotos"], [
                list(f) for f in self.tablas[PERSONAS].values()
            ]
        if sql == "SELECT id, persona_id, ruta FROM rostros":
            return ["id", "persona_id", "ruta"], [
                list(f[:3]) for f in self.tablas[ROSTROS].values()
            ]
        if m := re.fullmatch(r"SELECT (persona_id, ruta|ruta) FROM rostros WHERE id = (\d+)", sql):
            fila = self.tablas[ROSTROS].get(int(m[2]))
            if not fila:
                return [], []
            return (
                (["persona_id", "ruta"], [[fila[1], fila[2]]])
                if "," in m[1]
                else (
                    ["ruta"],
                    [[fila[2]]],
                )
            )
        if m := re.fullmatch(r"SELECT id FROM rostros WHERE persona_id = (\d+)", sql):
            return ["id"], [[f[0]] for f in self.tablas[ROSTROS].values() if f[1] == int(m[1])]
        raise AssertionError(f"SQL inesperado: {sql[:120]}")

    @staticmethod
    def _valores(texto: str) -> list:
        texto = re.sub(r"VECTOR\([^)]*\)", "'<vector>'", texto)
        valores = []
        for token in re.findall(r"'(?:[^']|'')*'|-?\d+(?:\.\d+)?", texto):
            if token.startswith("'"):
                valores.append(token[1:-1].replace("''", "'"))
            else:
                valores.append(float(token) if "." in token else int(token))
        return valores


def _cliente(motor: MotorFalso, raiz: Path, pipeline=None) -> TestClient:
    return TestClient(
        create_app(
            Settings(quipudb_api_url="http://motor-falso"),
            pipeline=pipeline or PipelineFalso(),
            engine_transport=motor.transporte(),
            datos=raiz,
        )
    )


def _registrar(client: TestClient, nombre="Ana Quispe", datos=JPEG, consentimiento=True):
    return client.post(
        "/rostros",
        params={"nombre": nombre, "consentimiento": str(consentimiento).lower()},
        files={"imagen": ("foto.jpg", datos, "image/jpeg")},
    )


# --- nombre e imagen -----------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Ana Quispe", "Ana_Quispe"),
        ("  ana   maría  ", "ana_maría"),
        ("Ann_Veneman", "Ann_Veneman"),
        ("O'Brien Jean-Pierre Jr.", "O'Brien_Jean-Pierre_Jr."),
        ("Ñusta Huamán", "Ñusta_Huamán"),
    ],
)
def test_normaliza_el_nombre_como_las_carpetas_del_dataset(texto, esperado) -> None:
    assert registro.normalizar_nombre(texto) == esperado


@pytest.mark.parametrize(
    "texto", ["", "   ", "Ana2", "Ana; DROP TABLE personas", "Ana--", "x" * 51, "<script>"]
)
def test_rechaza_nombres_que_no_son_de_persona(texto) -> None:
    with pytest.raises(registro.ErrorRegistro) as error:
        registro.normalizar_nombre(texto)
    assert error.value.codigo == "nombre_invalido"


def test_la_extension_sale_de_los_bytes_y_no_del_cliente() -> None:
    assert registro.extension_de(JPEG) == ".jpg"
    assert registro.extension_de(PNG) == ".png"
    assert registro.extension_de(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == ".webp"
    with pytest.raises(registro.ErrorRegistro):
        registro.extension_de(b"<svg></svg>")


# --- registrar -------------------------------------------------------------------


def test_registra_una_persona_nueva_y_guarda_la_foto(tmp_path) -> None:
    motor = MotorFalso()
    with _cliente(motor, tmp_path) as client:
        respuesta = _registrar(client)
    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo == {
        "id": 1,
        "persona_id": 1,
        "persona": "Ana_Quispe",
        "persona_nueva": True,
        "fotos": 1,
        "foto": "/rostros/1/foto",
    }
    assert motor.tablas[PERSONAS] == {1: (1, "Ana_Quispe", "subida", 1)}
    rostro_id, persona_id, ruta, _ = motor.tablas[ROSTROS][1]
    assert (rostro_id, persona_id) == (1, 1)
    assert ruta.startswith("subidas/1-") and ruta.endswith(".jpg")
    assert (tmp_path / ruta).read_bytes() == JPEG


def test_una_segunda_foto_suma_a_la_persona_existente(tmp_path) -> None:
    motor = MotorFalso()
    motor.tablas[PERSONAS][7] = (7, "Ann_Veneman", "lfw", 3)
    motor.tablas[ROSTROS][40] = (40, 7, "lfw/Ann_Veneman/Ann_Veneman_0001.jpg", "<vector>")
    with _cliente(motor, tmp_path) as client:
        cuerpo = _registrar(client, nombre="Ann Veneman").json()
    assert cuerpo["persona_id"] == 7
    assert cuerpo["persona_nueva"] is False
    assert cuerpo["id"] == 41
    # Sin UPDATE: la fila se reescribe con el conteo nuevo y el origen de siempre.
    assert motor.tablas[PERSONAS][7] == (7, "Ann_Veneman", "lfw", 4)


def test_el_nombre_no_distingue_mayusculas(tmp_path) -> None:
    motor = MotorFalso()
    motor.tablas[PERSONAS][10] = (10, "Colin_Powell", "subida", 1)
    with _cliente(motor, tmp_path) as client:
        cuerpo = _registrar(client, nombre="colin powell").json()
    assert (cuerpo["persona_id"], cuerpo["persona"], cuerpo["persona_nueva"]) == (
        10,
        "Colin_Powell",
        False,
    )
    assert list(motor.tablas[PERSONAS]) == [10]


def test_crea_el_esquema_si_la_base_esta_vacia(tmp_path) -> None:
    motor = MotorFalso(con_tablas=False)
    with _cliente(motor, tmp_path) as client:
        assert _registrar(client).status_code == 201
    assert any("USING HNSW" in s for s in motor.sentencias)
    assert set(motor.tablas) == {PERSONAS, ROSTROS}


def test_sin_consentimiento_no_procesa_ni_guarda_nada(tmp_path) -> None:
    motor, pipeline = MotorFalso(), PipelineFalso()
    with _cliente(motor, tmp_path, pipeline) as client:
        respuesta = _registrar(client, consentimiento=False)
    assert respuesta.status_code == 422
    assert respuesta.json()["codigo"] == "consentimiento_requerido"
    assert pipeline.recibidos == []
    assert motor.sentencias == []
    assert not (tmp_path / registro.CARPETA).exists()


def test_un_nombre_invalido_se_rechaza_antes_de_cargar_los_modelos(tmp_path) -> None:
    motor, pipeline = MotorFalso(), PipelineFalso()
    with _cliente(motor, tmp_path, pipeline) as client:
        respuesta = _registrar(client, nombre="Ana'); DROP TABLE rostros; --")
    assert respuesta.status_code == 422
    assert respuesta.json()["codigo"] == "nombre_invalido"
    assert pipeline.recibidos == []
    assert motor.sentencias == []


def test_varios_rostros_no_se_registran(tmp_path) -> None:
    motor = MotorFalso()
    with _cliente(motor, tmp_path, PipelineFalso(rostros=2)) as client:
        respuesta = _registrar(client)
    assert respuesta.status_code == 422
    assert respuesta.json()["codigo"] == "varios_rostros"
    assert motor.tablas[ROSTROS] == {}


def test_un_rostro_pequeno_no_se_registra(tmp_path) -> None:
    class PipelineChico:
        def extraer(self, datos):
            vector = (1.0,) + (0.0,) * (DIMENSIONES - 1)
            return ImagenEntrada(640, 480, (RostroEntrada((1, 1, 20, 20), vector, False),))

    motor = MotorFalso()
    with _cliente(motor, tmp_path, PipelineChico()) as client:
        respuesta = _registrar(client)
    assert respuesta.status_code == 422
    assert respuesta.json()["codigo"] == "rostro_pequeno"
    assert motor.tablas[ROSTROS] == {}


def test_un_archivo_que_no_es_imagen_se_rechaza(tmp_path) -> None:
    with _cliente(MotorFalso(), tmp_path) as client:
        respuesta = _registrar(client, datos=b"no soy una imagen")
    assert respuesta.status_code == 415
    assert respuesta.json()["codigo"] == "imagen_invalida"


def test_si_el_motor_falla_no_queda_el_archivo(tmp_path) -> None:
    def handler(request):
        if request.url.path == "/tables":
            return httpx.Response(200, json=[{"name": PERSONAS}, {"name": ROSTROS}])
        sql = json.loads(request.content)["sql"]
        if sql.startswith("INSERT"):
            return httpx.Response(400, json={"detail": "fallo"})
        return httpx.Response(200, json={"columns": [], "rows": []})

    app = create_app(
        Settings(quipudb_api_url="http://motor-falso"),
        pipeline=PipelineFalso(),
        engine_transport=httpx.MockTransport(handler),
        datos=tmp_path,
    )
    with TestClient(app) as client:
        respuesta = _registrar(client)
    assert respuesta.status_code == 502
    assert list((tmp_path / registro.CARPETA).iterdir()) == []


# --- listar, ver y eliminar ----------------------------------------------------


def test_lista_solo_las_fotos_subidas_de_la_mas_nueva_a_la_mas_vieja(tmp_path) -> None:
    motor = MotorFalso()
    motor.tablas[PERSONAS][1] = (1, "Ann_Veneman", "lfw", 1)
    motor.tablas[ROSTROS][1] = (1, 1, "lfw/Ann_Veneman/Ann_Veneman_0001.jpg", "<vector>")
    with _cliente(motor, tmp_path) as client:
        _registrar(client, nombre="Ana Quispe")
        _registrar(client, nombre="Luis Mamani")
        lista = client.get("/rostros/subidos").json()
    assert [(r["id"], r["persona"]) for r in lista] == [(3, "Luis_Mamani"), (2, "Ana_Quispe")]
    assert lista[0]["foto"] == "/rostros/3/foto"


def test_la_lista_esta_vacia_si_no_hay_tablas(tmp_path) -> None:
    with _cliente(MotorFalso(con_tablas=False), tmp_path) as client:
        assert client.get("/rostros/subidos").json() == []


def test_sirve_la_foto_registrada(tmp_path) -> None:
    with _cliente(MotorFalso(), tmp_path) as client:
        _registrar(client)
        respuesta = client.get("/rostros/1/foto")
    assert respuesta.status_code == 200
    assert respuesta.content == JPEG


def test_eliminar_la_ultima_foto_borra_la_persona_y_el_archivo(tmp_path) -> None:
    motor = MotorFalso()
    with _cliente(motor, tmp_path) as client:
        _registrar(client)
        ruta = motor.tablas[ROSTROS][1][2]
        respuesta = client.delete("/rostros/1")
    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "id": 1,
        "persona_id": 1,
        "persona": "Ana_Quispe",
        "persona_eliminada": True,
    }
    assert motor.tablas[ROSTROS] == {} and motor.tablas[PERSONAS] == {}
    assert not (tmp_path / ruta).exists()


def test_eliminar_una_de_varias_fotos_descuenta_sin_borrar_la_persona(tmp_path) -> None:
    motor = MotorFalso()
    with _cliente(motor, tmp_path) as client:
        _registrar(client)
        _registrar(client)
        cuerpo = client.delete("/rostros/2").json()
    assert cuerpo["persona_eliminada"] is False
    assert motor.tablas[PERSONAS][1] == (1, "Ana_Quispe", "subida", 1)
    assert list(motor.tablas[ROSTROS]) == [1]


def test_no_se_eliminan_fotos_del_dataset(tmp_path) -> None:
    motor = MotorFalso()
    motor.tablas[PERSONAS][1] = (1, "Ann_Veneman", "lfw", 1)
    motor.tablas[ROSTROS][1] = (1, 1, "lfw/Ann_Veneman/Ann_Veneman_0001.jpg", "<vector>")
    with _cliente(motor, tmp_path) as client:
        respuesta = client.delete("/rostros/1")
    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "no_permitido"
    assert 1 in motor.tablas[ROSTROS]


def test_eliminar_una_foto_que_no_existe_responde_404(tmp_path) -> None:
    with _cliente(MotorFalso(), tmp_path) as client:
        respuesta = client.delete("/rostros/99")
    assert respuesta.status_code == 404
    assert respuesta.json()["codigo"] == "no_existe"


def test_cors_permite_eliminar_desde_la_interfaz(tmp_path) -> None:
    with _cliente(MotorFalso(), tmp_path) as client:
        respuesta = client.options(
            "/rostros/1",
            headers={
                "Origin": "http://localhost:5175",
                "Access-Control-Request-Method": "DELETE",
            },
        )
    assert respuesta.status_code == 200
    assert "DELETE" in respuesta.headers["access-control-allow-methods"]


# --- contra el motor real --------------------------------------------------------


def _vector(eje: int) -> tuple[float, ...]:
    return tuple(1.0 if i == eje else 0.0 for i in range(DIMENSIONES))


def test_registrar_y_eliminar_en_un_quipudb_real(motor_aislado, tmp_path) -> None:
    settings = Settings(quipudb_api_url=motor_aislado, quipudb_timeout_s=30)
    with ClienteMotor.desde(settings) as cliente:
        registro.registrar(cliente, "Ana_Quispe", JPEG, _vector(0), tmp_path)
        registro.registrar(cliente, "Luis_Mamani", PNG, _vector(1), tmp_path)

        assert identificar(cliente, _vector(0)).persona == "Ana_Quispe"
        assert [r["persona"] for r in registro.subidos(cliente)] == ["Luis_Mamani", "Ana_Quispe"]

        registro.eliminar(cliente, 1, tmp_path)

        # Fuera del heap y del HNSW: el vecino mas cercano ya es Luis, lejos.
        respuesta = identificar(cliente, _vector(0))
        assert respuesta.persona is None
        assert [c.persona for c in respuesta.candidatos] == ["Luis_Mamani"]
        assert cliente.query(f"SELECT id FROM {PERSONAS}").rows == ((2,),)
