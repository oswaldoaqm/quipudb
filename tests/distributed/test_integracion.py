"""Integración distribuida (#280): dos nodos y un coordinador como procesos.

Todo pasa por HTTP. Las consultas entran por ``POST /query`` del coordinador,
como las mandaría el frontend; para ver dónde quedó cada fila se pregunta
directamente a los nodos por sus rutas internas.
"""

import signal
import time

import pytest

INT_MIN = -2147483648
INT_MAX = 2147483647


def insert(key: int, nombre: str = "fila", precio: float = 1.5, fecha: str = "2026-11-13") -> str:
    return f"INSERT INTO items VALUES ({key}, '{nombre}', {precio}, DATE '{fecha}')"


def ids(body: dict) -> list[int]:
    return [row[0] for row in body["rows"]]


# -- clúster sano -----------------------------------------------------------------


def test_salud_y_tablas_logicas(cluster):
    health = cluster.get("/health")
    assert health.status_code == 200
    assert [(n["node_id"], n["ready"]) for n in health.json()["nodes"]] == [
        ("n1", True),
        ("n2", True),
    ]
    tables = cluster.get("/tables").json()
    assert [(t["name"], t["record_count"]) for t in tables] == [("items", 0), ("notas", 0)]


def test_cada_registro_queda_en_su_nodo(cluster):
    for key in (INT_MIN, -1, 0, 9999, 10000, 10001, INT_MAX):
        assert cluster.ok(insert(key))["affected_rows"] == 1

    assert cluster.keys("n1", "items_f0") == [INT_MIN, -1, 0, 9999]
    assert cluster.keys("n2", "items_f1") == [10000, 10001, INT_MAX]
    assert cluster.get("/tables").json()[0]["record_count"] == 7


def test_busqueda_por_id_en_la_frontera(cluster):
    cluster.ok(insert(9999, "ultimo de n1"))
    cluster.ok(insert(10000, "primero de n2"))
    assert cluster.ok("SELECT nombre FROM items WHERE id = 9999")["rows"] == [["ultimo de n1"]]
    assert cluster.ok("SELECT nombre FROM items WHERE id = 10000")["rows"] == [["primero de n2"]]
    assert cluster.ok("SELECT * FROM items WHERE id = 5")["rows"] == []


def test_consultas_que_combinan_los_dos_nodos(cluster):
    for key, nombre, precio in [
        (3, "quipu", 10.0),
        (9999, "chasqui", 2.5),
        (10000, "quipu", 7.0),
        (25000, "tambo", 10.0),
    ]:
        cluster.ok(insert(key, nombre, precio))

    body = cluster.ok("SELECT nombre, id FROM items ORDER BY id DESC LIMIT 3")
    assert body["columns"] == ["nombre", "id"]
    assert body["column_types"] == ["VARCHAR", "INT"]
    assert body["rows"] == [["tambo", 25000], ["quipu", 10000], ["chasqui", 9999]]
    assert body["plan"] is None

    # Valores repetidos en otra columna son datos y se conservan.
    assert ids(cluster.ok("SELECT id FROM items WHERE nombre = 'quipu' ORDER BY id")) == [3, 10000]
    assert ids(cluster.ok("SELECT id FROM items WHERE precio = 10 ORDER BY id")) == [3, 25000]
    assert ids(cluster.ok("SELECT id FROM items WHERE id >= 9999 ORDER BY id")) == [
        9999,
        10000,
        25000,
    ]
    assert cluster.ok("SELECT * FROM items WHERE id > 2147483647")["rows"] == []


def test_borrado_por_id_y_borrado_inexistente(cluster):
    cluster.ok(insert(10000))
    assert cluster.ok("DELETE FROM items WHERE id = 10000")["affected_rows"] == 1
    assert cluster.ok("DELETE FROM items WHERE id = 10000")["affected_rows"] == 0
    assert cluster.ok("DELETE FROM items WHERE id = 42")["affected_rows"] == 0
    assert cluster.keys("n2", "items_f1") == []


def test_clave_duplicada_no_cambia_nada(cluster):
    cluster.ok(insert(10000, "original"))
    response = cluster.query(insert(10000, "repetida"))
    assert response.status_code == 409
    assert response.json()["error"].startswith("[DUPLICATE_KEY] nodo n2, fragmento items_f1:")
    assert cluster.ok("SELECT nombre FROM items WHERE id = 10000")["rows"] == [["original"]]


def test_lo_no_admitido_se_rechaza_sin_tocar_los_nodos(cluster):
    cluster.ok(insert(1))
    for sql, column in [
        ("SELECT * FROM items ORDER BY nombre", 21),
        ("DELETE FROM items WHERE nombre = 'fila'", 25),
        ("SELECT nombre, COUNT(*) FROM items GROUP BY nombre", 36),
    ]:
        response = cluster.query(sql)
        assert response.status_code == 400, sql
        assert response.json()["error"].startswith("[UNSUPPORTED_DISTRIBUTED]")
        assert response.json()["column"] == column
    assert cluster.keys("n1", "items_f0") == [1]


def test_los_indices_del_nodo_se_mantienen(cluster):
    for key in (1, 2, 10000):
        cluster.ok(insert(key, "inca"))
    cluster.ok("DELETE FROM items WHERE id = 2")
    cluster.ok(insert(3, "colonia"))

    # El nodo resuelve el WHERE con su hash por nombre y no encuentra la fila borrada.
    response = cluster.node_post(
        "n1",
        "/fragments/items_f0/scan",
        table="items",
        sql="SELECT * FROM items WHERE nombre = 'inca';",
    )
    result = response.json()["result"]
    assert [row[0] for row in result["rows"]] == [1]
    search = result["plan"]["root"]["children"][0]
    assert (search["op"], search["structure"], search["column"]) == (
        "index_search",
        "extendible_hash",
        "nombre",
    )
    assert ids(cluster.ok("SELECT id FROM items WHERE nombre = 'inca' ORDER BY id")) == [1, 10000]


def test_los_datos_sobreviven_al_reinicio_de_los_nodos(cluster):
    for key in (7, 10007):
        cluster.ok(insert(key, f"k{key}"))
    cluster.ok("INSERT INTO notas VALUES (1, 'persistente')")
    for node in cluster.nodes.values():
        node.stop()
    for node in cluster.nodes.values():
        node.start()

    assert cluster.ok("SELECT id, nombre FROM items ORDER BY id")["rows"] == [
        [7, "k7"],
        [10007, "k10007"],
    ]
    assert cluster.ok("SELECT texto FROM notas")["rows"] == [["persistente"]]
    assert [t["record_count"] for t in cluster.get("/tables").json()] == [2, 1]


# -- tabla single -----------------------------------------------------------------


def test_tabla_single_por_el_coordinador_igual_que_directo(cluster):
    for codigo, texto in [(2, "beta"), (1, "alfa"), (3, "gamma")]:
        cluster.ok(f"INSERT INTO notas VALUES ({codigo}, '{texto}')")

    for sql in (
        "SELECT texto FROM notas ORDER BY texto DESC LIMIT 2",
        "SELECT * FROM notas WHERE codigo = 3",
        "EXPLAIN SELECT * FROM notas WHERE codigo = 3",
    ):
        via_coordinator = cluster.ok(sql)
        direct = cluster.node_post(
            "n2", "/fragments/notas_f0/execute", table="notas", sql=sql
        ).json()["result"]
        for field in ("columns", "column_types", "rows", "is_explain"):
            assert via_coordinator[field] == direct[field], (sql, field)
        # A diferencia de una tabla range, el plan del dueño sí describe la consulta.
        assert (via_coordinator["plan"] is None) == (direct["plan"] is None)
    assert cluster.ok("SELECT texto FROM notas ORDER BY texto DESC LIMIT 2")["rows"] == [
        ["gamma"],
        ["beta"],
    ]


# -- fallos -------------------------------------------------------------------------


def test_un_nodo_caido(cluster):
    cluster.ok(insert(1))
    cluster.ok(insert(10000))
    cluster.nodes["n2"].stop()

    health = cluster.get("/health")
    assert health.status_code == 503
    assert health.json()["nodes"][1]["reason"].startswith("[NODE_UNAVAILABLE] nodo n2")

    response = cluster.query("SELECT * FROM items")
    assert response.status_code == 503
    assert "rows" not in response.json()
    # Lo que solo vive en n1 sigue respondiendo.
    assert ids(cluster.ok("SELECT id FROM items WHERE id < 10000")) == [1]
    assert cluster.ok(insert(2))["affected_rows"] == 1
    assert cluster.query(insert(10001)).status_code == 503
    assert cluster.get("/tables").status_code == 503

    cluster.nodes["n2"].start()
    assert ids(cluster.ok("SELECT id FROM items ORDER BY id")) == [1, 2, 10000]


def test_nodo_con_otra_version_de_catalogo(make_cluster):
    cluster = make_cluster(node_versions={"n2": 2}).start()
    health = cluster.get("/health").json()
    assert health["status"] == "degraded"
    assert "usa catalog_version 2 y el coordinador 1" in health["nodes"][1]["reason"]

    response = cluster.query(insert(10000))
    assert response.status_code == 409
    assert response.json()["error"].startswith("[CATALOG_VERSION_MISMATCH] nodo n2")
    # n1 tiene la misma versión que el coordinador y sigue atendiendo su rango.
    assert cluster.ok(insert(1))["affected_rows"] == 1
    assert cluster.query("SELECT * FROM items").status_code == 409


@pytest.mark.skipif(not hasattr(signal, "SIGSTOP"), reason="congela el nodo con SIGSTOP")
def test_escritura_sin_respuesta_no_se_reintenta(make_cluster):
    cluster = make_cluster(timeout=1.0).start()
    cluster.nodes["n2"].pause()
    try:
        started = time.monotonic()
        response = cluster.query(insert(10001, "lenta"))
        elapsed = time.monotonic() - started
    finally:
        cluster.nodes["n2"].resume()

    assert response.status_code == 504
    assert response.json()["error"] == (
        "[NODE_TIMEOUT] nodo n2, fragmento items_f1: el nodo no respondió a tiempo; "
        "resultado de escritura incierto; no se reintentó"
    )
    assert elapsed < 10
    # El nodo procesa la petición al despertar o no la procesa; nunca dos veces.
    deadline = time.monotonic() + 5
    while cluster.query("SELECT * FROM items WHERE id = 10001").status_code != 200:
        assert time.monotonic() < deadline
        time.sleep(0.1)
    assert len(cluster.ok("SELECT * FROM items WHERE id = 10001")["rows"]) <= 1


@pytest.mark.parametrize("sql", ["SELECT * FROM nada", "SELECT FROM items"])
def test_errores_de_sql_con_la_forma_de_la_api_local(cluster, sql):
    response = cluster.query(sql)
    assert response.status_code == 400
    assert set(response.json()) == {"error", "kind", "line", "column", "end_line", "end_column"}
    assert response.json()["line"] == 1
