"""Cliente del coordinador (#278): sobre v1, validación de respuestas y fallos.

Las pruebas con nodos falsos no necesitan el motor. Las de la última sección
hablan con el servidor real del #277 en memoria, sin abrir puertos.
"""

import asyncio
import json
from uuid import UUID

import httpx
import pytest

from engine.distributed.client import NodeClient, NodeFailure
from engine.distributed.coordinator_test_support import FakeNode, error_body


def run(coro):
    return asyncio.run(coro)


def client_for(catalog, node: FakeNode) -> NodeClient:
    return NodeClient(catalog, catalog.node(node.node_id), transport=node.transport)


async def call(client: NodeClient, coro_factory):
    try:
        return await coro_factory(client)
    finally:
        await client.aclose()


def failure_of(catalog, node, coro_factory) -> NodeFailure:
    with pytest.raises(NodeFailure) as caught:
        run(call(client_for(catalog, node), coro_factory))
    return caught.value


INSERT = "INSERT INTO items VALUES (1, 'a')"


def insert(c):
    return c.execute("items_f0", "items", INSERT, write=True)


def scan(c):
    return c.scan("items_f0", "items", "SELECT * FROM items")


# -- la petición que sale -----------------------------------------------------


def test_cada_peticion_lleva_el_sobre_v1_y_un_uuid_nuevo(node_catalog):
    node = FakeNode(node_catalog, "n1")

    async def dos(c):
        await c.execute("items_f0", "items", INSERT, write=True)
        await c.scan("items_f0", "items", "SELECT * FROM items")

    run(call(client_for(node_catalog, node), dos))
    (r1, b1), (r2, b2) = node.requests
    assert r1.method == "POST"
    assert r1.url == "http://n1:8000/internal/v1/fragments/items_f0/execute"
    assert r2.url.path == "/internal/v1/fragments/items_f0/scan"
    assert r1.headers["content-type"] == "application/json"
    assert set(b1) == {"protocol_version", "catalog_version", "request_id", "table", "sql"}
    assert (b1["protocol_version"], b1["catalog_version"]) == (1, 1)
    assert (b1["table"], b1["sql"]) == ("items", INSERT)
    assert str(UUID(b1["request_id"])) == b1["request_id"]
    assert b1["request_id"] != b2["request_id"]


def test_salud_es_get_sin_cuerpo_y_catalogo_solo_lleva_el_sobre(node_catalog):
    node = FakeNode(node_catalog, "n1")

    async def ambos(c):
        return await c.health(), await c.describe()

    health, description = run(call(client_for(node_catalog, node), ambos))
    (r1, b1), (r2, b2) = node.requests
    assert (r1.method, r1.url.path, b1) == ("GET", "/internal/v1/health", None)
    assert r2.url.path == "/internal/v1/catalog"
    assert set(b2) == {"protocol_version", "catalog_version", "request_id"}
    assert health.status == "ready" and health.fragment_ids == ["items_f0"]
    assert [f.fragment_id for f in description.fragments] == ["items_f0"]


def test_no_reintenta_ni_sigue_redirecciones(node_catalog):
    def redirige(request, body):
        return httpx.Response(307, headers={"location": "http://n2:8000" + request.url.path})

    node = FakeNode(node_catalog, "n1", override=redirige)
    failure = failure_of(node_catalog, node, insert)
    assert failure.code == "INVALID_NODE_RESPONSE"
    assert len(node.requests) == 1


# -- errores que manda el nodo --------------------------------------------------


def test_el_error_del_nodo_conserva_codigo_estado_y_ubicacion(node_catalog):
    def sql_error(request, body):
        return httpx.Response(
            400,
            json=error_body(
                node,
                body,
                "SQL_ERROR",
                kind="semantic",
                line=1,
                column=8,
                end_line=1,
                end_column=12,
            ),
        )

    node = FakeNode(node_catalog, "n1", override=sql_error)
    failure = failure_of(node_catalog, node, scan)
    assert (failure.code, failure.status, failure.execution_state) == (
        "SQL_ERROR",
        400,
        "not_started",
    )
    assert (failure.kind, failure.line, failure.column, failure.end_column) == (
        "semantic",
        1,
        8,
        12,
    )
    assert failure.public_message() == "[SQL_ERROR] nodo n1, fragmento items_f0: falla SQL_ERROR"


def test_un_duplicado_es_escritura_incierta(node_catalog):
    def duplicado(request, body):
        return httpx.Response(409, json=error_body(node, body, "DUPLICATE_KEY", "unknown"))

    node = FakeNode(node_catalog, "n1", override=duplicado)
    failure = failure_of(node_catalog, node, insert)
    assert (failure.code, failure.status, failure.execution_state) == (
        "DUPLICATE_KEY",
        409,
        "unknown",
    )
    assert failure.public_message().endswith("resultado de escritura incierto; no se reintentó")


@pytest.mark.parametrize(
    "code,status",
    [
        ("WRONG_FRAGMENT", 409),
        ("CATALOG_VERSION_MISMATCH", 409),
        ("FRAGMENT_NOT_FOUND", 404),
        ("UNSUPPORTED_DISTRIBUTED", 400),
        ("NODE_NOT_READY", 503),
        ("INVALID_REQUEST", 422),
    ],
)
def test_los_rechazos_previos_no_son_escrituras_inciertas(node_catalog, code, status):
    def rechazo(request, body):
        return httpx.Response(status, json=error_body(node, body, code))

    node = FakeNode(node_catalog, "n1", override=rechazo)
    failure = failure_of(node_catalog, node, insert)
    assert (failure.code, failure.status, failure.execution_state) == (code, status, "not_started")
    assert "incierto" not in failure.public_message()


# -- fallos de transporte -------------------------------------------------------


def lanza(error):
    def override(request, body):
        raise error("simulado", request=request)

    return override


def test_sin_conexion_es_nodo_no_disponible_y_no_se_envio(node_catalog):
    node = FakeNode(node_catalog, "n1", override=lanza(httpx.ConnectError))
    failure = failure_of(node_catalog, node, insert)
    assert (failure.code, failure.status, failure.execution_state) == (
        "NODE_UNAVAILABLE",
        503,
        "not_started",
    )
    assert "incierto" not in failure.public_message()


def test_timeout_de_escritura_deja_el_resultado_incierto(node_catalog):
    node = FakeNode(node_catalog, "n1", override=lanza(httpx.ReadTimeout))
    failure = failure_of(node_catalog, node, insert)
    assert (failure.code, failure.status, failure.execution_state) == (
        "NODE_TIMEOUT",
        504,
        "unknown",
    )
    assert "resultado de escritura incierto" in failure.public_message()


def test_timeout_de_lectura_no_habla_de_escritura(node_catalog):
    node = FakeNode(node_catalog, "n1", override=lanza(httpx.ReadTimeout))
    failure = failure_of(node_catalog, node, scan)
    assert failure.code == "NODE_TIMEOUT"
    assert "escritura" not in failure.public_message()


def test_sin_conexion_libre_en_el_pool_no_se_envio(node_catalog):
    node = FakeNode(node_catalog, "n1", override=lanza(httpx.PoolTimeout))
    failure = failure_of(node_catalog, node, insert)
    assert (failure.code, failure.execution_state) == ("NODE_TIMEOUT", "not_started")


def test_conexion_cortada_despues_de_enviar_es_respuesta_invalida(node_catalog):
    node = FakeNode(node_catalog, "n1", override=lanza(httpx.RemoteProtocolError))
    failure = failure_of(node_catalog, node, insert)
    assert (failure.code, failure.status, failure.execution_state) == (
        "INVALID_NODE_RESPONSE",
        502,
        "unknown",
    )


# -- respuestas que no se aceptan como éxito -----------------------------------


def cambia(catalog, **campos):
    """Una respuesta correcta del nodo n1 con algunos campos reemplazados."""

    def override(request, body):
        data = FakeNode(catalog, "n1").handle(request).json()
        data.update(campos)
        return httpx.Response(200, json=data)

    return override


@pytest.mark.parametrize(
    "campos,motivo",
    [
        ({"node_id": "n2"}, "respondió el nodo n2"),
        ({"fragment_id": "items_f1"}, "respondió el fragmento items_f1"),
        ({"request_id": "00000000-0000-4000-8000-000000000000"}, "otro request_id"),
        ({"catalog_version": 2}, "catalog_version 2"),
        ({"protocol_version": 2}, "ilegible"),
        ({"result": {"columns": "x"}}, "ilegible"),
    ],
)
def test_una_respuesta_ajena_o_rota_no_es_exito(node_catalog, campos, motivo):
    node = FakeNode(node_catalog, "n1", override=cambia(node_catalog, **campos))
    failure = failure_of(node_catalog, node, insert)
    assert failure.code == "INVALID_NODE_RESPONSE"
    assert failure.execution_state == "unknown"  # era una escritura: pudo aplicarse
    assert motivo in failure.message


def test_un_cuerpo_que_no_es_json_no_es_exito(node_catalog):
    node = FakeNode(node_catalog, "n1", override=lambda r, b: httpx.Response(200, text="hola"))
    failure = failure_of(node_catalog, node, scan)
    assert (failure.code, failure.execution_state) == ("INVALID_NODE_RESPONSE", "not_started")


def test_un_500_sin_sobre_de_error_es_respuesta_invalida(node_catalog):
    node = FakeNode(node_catalog, "n1", override=lambda r, b: httpx.Response(500, text="boom"))
    failure = failure_of(node_catalog, node, insert)
    assert (failure.code, failure.status) == ("INVALID_NODE_RESPONSE", 502)


def test_un_error_firmado_por_otro_nodo_es_respuesta_invalida(node_catalog):
    def ajeno(request, body):
        data = error_body(node, body, "SQL_ERROR")
        data["node_id"] = "n2"
        return httpx.Response(400, json=data)

    node = FakeNode(node_catalog, "n1", override=ajeno)
    assert failure_of(node_catalog, node, scan).code == "INVALID_NODE_RESPONSE"


def test_salud_no_lista_responde_503_con_su_cuerpo(node_catalog):
    def no_lista(request, body):
        return httpx.Response(
            503,
            json=node.base(status="not_ready", fragment_ids=["items_f0"], reason="sin catálogo"),
        )

    node = FakeNode(node_catalog, "n1", override=no_lista)
    health = run(call(client_for(node_catalog, node), lambda c: c.health()))
    assert (health.status, health.reason) == ("not_ready", "sin catálogo")


def test_salud_200_con_cuerpo_no_listo_es_contradictoria(node_catalog):
    def contradictoria(request, body):
        return httpx.Response(200, json=node.base(status="not_ready", fragment_ids=[], reason=None))

    node = FakeNode(node_catalog, "n1", override=contradictoria)
    failure = failure_of(node_catalog, node, lambda c: c.health())
    assert failure.code == "INVALID_NODE_RESPONSE"


# -- contra el servidor real del #277 ------------------------------------------


def real_client(node_catalog, node_services, node_id) -> NodeClient:
    from engine.distributed.node_api import create_node_app

    transport = httpx.ASGITransport(app=create_node_app(node_services[node_id]))
    return NodeClient(node_catalog, node_catalog.node(node_id), transport=transport)


def test_ciclo_completo_contra_un_nodo_real(node_catalog, node_services):
    async def ciclo(c):
        health = await c.health()
        inserted = await c.execute(
            "items_f0", "items", "INSERT INTO items VALUES (7, 'q')", write=True
        )
        rows = await c.scan("items_f0", "items", "SELECT * FROM items WHERE id = 7")
        description = await c.describe()
        deleted = await c.execute("items_f0", "items", "DELETE FROM items WHERE id = 7", write=True)
        return health, inserted, rows, description, deleted

    health, inserted, rows, description, deleted = run(
        call(real_client(node_catalog, node_services, "n1"), ciclo)
    )
    assert health.status == "ready"
    assert inserted.result.affected_rows == 1
    assert rows.result.columns == ["id", "nombre"] and rows.result.rows == [[7, "q"]]
    assert description.fragments[0].table_info.record_count == 1
    assert deleted.result.affected_rows == 1


def test_errores_reales_del_nodo_llegan_con_su_codigo(node_catalog, node_services):
    async def errores(c):
        fallos = []
        for coro in (
            # 10000 es de items_f1: el nodo rechaza antes de escribir.
            c.execute("items_f0", "items", "INSERT INTO items VALUES (10000, 'x')", write=True),
            c.execute("items_f0", "items", "INSERT INTO items VALUES (1, 'x')", write=True),
            c.execute("items_f0", "items", "INSERT INTO items VALUES (1, 'x')", write=True),
            c.scan("items_f0", "items", "SELECT * FROM items WHERE nada = 1"),
        ):
            try:
                await coro
                fallos.append(None)
            except NodeFailure as failure:
                fallos.append(failure)
        return fallos

    wrong, ok, duplicate, sql = run(call(real_client(node_catalog, node_services, "n1"), errores))
    assert (wrong.code, wrong.status, wrong.execution_state) == (
        "WRONG_FRAGMENT",
        409,
        "not_started",
    )
    assert ok is None
    assert (duplicate.code, duplicate.status) == ("DUPLICATE_KEY", 409)
    assert "incierto" in duplicate.public_message()
    assert (sql.code, sql.kind) == ("SQL_ERROR", "semantic")
    assert sql.line == 1 and sql.column is not None
    assert json.dumps(sql.public_message())  # serializable para la API pública
