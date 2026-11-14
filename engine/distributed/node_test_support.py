"""Peticiones de prueba, sin cliente del coordinador ni enrutamiento."""

from uuid import uuid4


def envelope(sql=None, *, table="items", **changes):
    data = {"protocol_version": 1, "catalog_version": 1, "request_id": str(uuid4())}
    if sql is not None:
        data.update(table=table, sql=sql)
    data.update(changes)
    return data


def insert(service, key, name="fila", *, fragment="items_f0"):
    return service.execute(fragment, envelope(f"INSERT INTO items VALUES ({key}, '{name}')"))


def scan(service, where="", *, fragment="items_f0"):
    return service.execute(fragment, envelope(f"SELECT * FROM items {where}"), scan=True).result
