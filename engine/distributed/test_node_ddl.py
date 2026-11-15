"""El nodo aplica un catálogo nuevo en su motor real (#296)."""

import pytest
from fastapi.testclient import TestClient

from engine.distributed.catalog_store import FILENAME, newest_catalog, save_catalog
from engine.distributed.ddl import apply_ddl
from engine.distributed.fragments import FragmentService
from engine.distributed.node_api import create_node_app
from engine.distributed.node_errors import NodeError
from engine.distributed.node_test_support import envelope
from engine.distributed.protocol import CatalogAppliedResponse
from engine.parser import parse_sql

VENTAS = """CREATE TABLE ventas (id INT PRIMARY KEY, monto DOUBLE)
PARTITION BY RANGE (id) (
  PARTITION ventas_f0 VALUES LESS THAN (5000) ON n1,
  PARTITION ventas_f1 VALUES LESS THAN (MAXVALUE) ON n2
)"""


def ddl(catalog, sql):
    return apply_ddl(catalog, parse_sql(sql), sql)


def apply(service, catalog, **changes):
    body = envelope(**{"catalog_version": service.catalog.catalog_version, **changes})
    body["catalog"] = catalog.model_dump(mode="json")
    return service.apply_catalog(body)


def tables_of(service):
    return sorted(service._database.table_names())


def test_crear_tabla_particionada_indice_y_borrarla(node_services, node_catalog):
    n1, n2 = node_services["n1"], node_services["n2"]
    v2 = ddl(node_catalog, VENTAS)
    for service in (n1, n2):
        response = apply(service, v2)
        assert isinstance(response, CatalogAppliedResponse)
        assert response.catalog_version == 2
        assert tables_of(service) == ["items", "ventas"]
    assert n1.health().catalog_version == 2
    assert sorted(n1.health().fragment_ids) == ["items_f0", "ventas_f0"]

    insert = envelope("INSERT INTO ventas VALUES (7, 1.5)", table="ventas", catalog_version=2)
    assert n1.execute("ventas_f0", insert).result.affected_rows == 1
    # La admisión del nodo ya conoce el rango nuevo.
    outside = envelope("INSERT INTO ventas VALUES (9000, 1.5)", table="ventas", catalog_version=2)
    with pytest.raises(NodeError) as caught:
        n1.execute("ventas_f0", outside)
    assert caught.value.code in {"UNSUPPORTED_DISTRIBUTED", "WRONG_FRAGMENT"}

    v3 = ddl(v2, "CREATE INDEX por_monto ON ventas (monto) USING BPLUS")
    for service in (n1, n2):
        apply(service, v3)
        info = service._database.table_info("ventas")
        assert [i.name for i in info.indexes] == ["por_monto"]

    v4 = ddl(v3, "DROP TABLE ventas")
    for service in (n1, n2):
        apply(service, v4)
        assert tables_of(service) == ["items"]
        assert service.catalog.catalog_version == 4
        assert newest_catalog(node_catalog, service.directory) == v4


def test_tabla_single_solo_se_crea_en_su_nodo(node_services, node_catalog):
    v2 = ddl(node_catalog, "CREATE TABLE notas (id INT PRIMARY KEY, texto VARCHAR(10)) ON n2")
    apply(node_services["n1"], v2)
    apply(node_services["n2"], v2)
    assert tables_of(node_services["n1"]) == ["items"]
    assert tables_of(node_services["n2"]) == ["items", "notas"]


def test_un_indice_que_falla_deja_el_nodo_como_estaba(node_services, node_catalog):
    n1 = node_services["n1"]
    # El catálogo es válido, pero el motor no puede crear un segundo FULLTEXT.
    v2 = ddl(node_catalog, "CREATE TABLE textos (id INT PRIMARY KEY, a VARCHAR(20)) ON n1")
    v3 = ddl(v2, "CREATE INDEX t1 ON textos (a) USING FULLTEXT")
    bad = v3.model_dump(mode="json")
    bad["catalog_version"] = 3
    table = next(t for t in bad["tables"] if t["name"] == "textos")
    table["indexes"].append(
        {"name": "t2", "column": "a", "structure": "inverted_index", "supports_range": False}
    )
    bad_catalog = type(v3).model_validate(bad)
    apply(n1, v2)
    with pytest.raises(NodeError) as caught:
        apply(n1, bad_catalog)
    assert (caught.value.code, caught.value.detail.execution_state) == ("SQL_ERROR", "not_started")
    assert n1.catalog == v2
    assert [i.name for i in n1._database.table_info("textos").indexes] == []
    assert n1.health().status == "ready"


def test_rechaza_version_vieja_cambios_de_nodos_y_sobre_desfasado(node_services, node_catalog):
    n1 = node_services["n1"]
    same = node_catalog.model_copy(update={"catalog_version": 1})
    with pytest.raises(NodeError, match="no es mayor") as caught:
        apply(n1, same)
    assert caught.value.code == "INVALID_REQUEST"
    with pytest.raises(NodeError) as caught:
        apply(n1, ddl(node_catalog, VENTAS), catalog_version=7)
    assert caught.value.code == "CATALOG_VERSION_MISMATCH"
    assert tables_of(n1) == ["items"]


def test_ruta_http_y_reinicio_con_el_catalogo_guardado(node_services, node_catalog):
    n1 = node_services["n1"]
    v2 = ddl(node_catalog, VENTAS)
    with TestClient(create_node_app(n1)) as client:
        body = envelope()
        body["catalog"] = v2.model_dump(mode="json")
        response = client.post("/internal/v1/catalog/apply", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["catalog_version"] == 2
        again = client.post("/internal/v1/catalog/apply", json=body)
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "CATALOG_VERSION_MISMATCH"
    assert (n1.directory / FILENAME).is_file()

    # TestClient cerró el servicio; arranca de nuevo con lo guardado.
    restarted = FragmentService(newest_catalog(node_catalog, n1.directory), "n1", n1.directory)
    try:
        restarted.start()
        assert restarted.health().status == "ready"
        assert sorted(restarted.health().fragment_ids) == ["items_f0", "ventas_f0"]
    finally:
        restarted.close()


def test_newest_catalog_prefiere_el_mas_nuevo(tmp_path, node_catalog):
    assert newest_catalog(node_catalog, tmp_path) is node_catalog
    v2 = node_catalog.model_copy(update={"catalog_version": 2})
    save_catalog(tmp_path, v2)
    assert newest_catalog(node_catalog, tmp_path) == v2
    v5 = node_catalog.model_copy(update={"catalog_version": 5})
    assert newest_catalog(v5, tmp_path) is v5
    other = v2.model_copy(update={"catalog_version": 9, "nodes": v2.nodes[::-1]})
    save_catalog(tmp_path, other)
    with pytest.raises(ValueError, match="nodos"):
        newest_catalog(node_catalog, tmp_path)
