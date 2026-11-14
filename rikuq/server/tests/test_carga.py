"""Las sentencias que meten el dataset en QuipuDB (#302)."""

from app.carga.dataset import insertar_persona, insertar_rostro, por_persona
from app.carga.esquema import PERSONAS, ROSTROS
from app.dataset.lfw import Rostro


def _rostro(persona: str, numero: int, origen: str = "lfw") -> Rostro:
    return Rostro(persona, f"{origen}/{persona}/{persona}_{numero:04d}.jpg", "abc", origen)


def test_insertar_persona_escapa_el_nombre() -> None:
    sql = insertar_persona(1, "O'Brien", "equipo", 3)

    assert "'O''Brien'" in sql
    assert sql.startswith(f"INSERT INTO {PERSONAS}")


def test_insertar_rostro_escribe_el_vector_sin_notacion_cientifica() -> None:
    sql = insertar_rostro(7, 1, "lfw/Ana/a.jpg", [0.00003, 0.5])

    assert "VECTOR(0.0000300, 0.5000000)" in sql
    assert sql.startswith(f"INSERT INTO {ROSTROS}")


def test_por_persona_agrupa_conservando_el_orden() -> None:
    rostros = [_rostro("Ana", 1), _rostro("Beto", 1), _rostro("Ana", 2)]

    agrupado = por_persona(rostros)

    assert list(agrupado) == ["Ana", "Beto"]
    assert len(agrupado["Ana"]) == 2
