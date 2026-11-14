"""Pertenencia e intersección de rangos; sin SQL, red ni acceso a disco."""

from engine.distributed.contracts import INT32_MAX, INT32_MIN, DistributedTable, Fragment


def _validate(table: DistributedTable, value: int) -> None:
    if table.mode != "range":
        raise ValueError("la partición INT32 solo se aplica a tablas range")
    if type(value) is not int or not INT32_MIN <= value <= INT32_MAX:
        raise ValueError("la clave debe ser INT32; bool, float, string y null no son claves INT")


def owner_for_key(table: DistributedTable, key: int) -> Fragment:
    """Devuelve el único fragmento propietario, incluyendo su node_id."""
    _validate(table, key)
    for fragment in table.ordered_fragments:
        if (fragment.lower is None or fragment.lower <= key) and (
            fragment.upper is None or key < fragment.upper
        ):
            return fragment
    raise ValueError("catálogo inválido: clave sin propietario")


def fragments_for_comparison(
    table: DistributedTable,
    operator: str,
    value: int,
) -> tuple[Fragment, ...]:
    """Fragmentos que pueden satisfacer =, <, <=, > o >= sobre la PK.

    Orden por lower, independiente del orden del JSON. No interpreta predicados
    de otras columnas ni combina consultas: esas tareas pertenecen a #279.
    """
    _validate(table, value)
    if operator not in {"=", "<", "<=", ">", ">="}:
        raise ValueError(f"comparación no soportada: {operator!r}")
    if operator == "=":
        return (owner_for_key(table, value),)
    result = []
    for fragment in table.ordered_fragments:
        low = INT32_MIN if fragment.lower is None else fragment.lower
        # Un límite superior exclusivo nulo contiene INT32_MAX; uno finito no.
        if operator == "<":
            intersects = low < value
        elif operator == "<=":
            intersects = low <= value
        elif operator == ">=":
            intersects = fragment.upper is None or value < fragment.upper
        else:
            # Python tiene enteros arbitrarios; la diferencia evita fabricar
            # una clave value+1 fuera de INT32 en la frontera superior.
            intersects = value < INT32_MAX if fragment.upper is None else fragment.upper - value > 1
        if intersects:
            result.append(fragment)
    return tuple(result)
