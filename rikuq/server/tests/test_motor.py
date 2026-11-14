"""Literales SQL para el motor (#302)."""

from app.motor import literal, usa_tls


def test_literal_envuelve_en_comillas() -> None:
    assert literal("Aaron Peirsol") == "'Aaron Peirsol'"


def test_literal_duplica_la_comilla_simple() -> None:
    """Es lo unico que el lexer reconoce dentro de un literal."""

    assert literal("O'Brien") == "'O''Brien'"


def test_literal_no_deja_escapar_del_literal() -> None:
    """Un nombre con punto y coma o guiones sigue siendo un solo literal."""

    peligroso = "x'; DROP TABLE personas; --"

    escapado = literal(peligroso)

    assert escapado == "'x''; DROP TABLE personas; --'"
    assert escapado.count("'") % 2 == 0


def test_literal_quita_el_caracter_nulo() -> None:
    """No cabe en un VARCHAR y el motor lo rechazaria."""

    assert literal("a\x00b") == "'ab'"


def test_usa_tls_solo_con_https() -> None:
    assert usa_tls("https://ejemplo.test") is True
    assert usa_tls("http://127.0.0.1:8000") is False
