"""Literales SQL para el motor (#302)."""

from app.motor import literal, literal_vector, usa_tls


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


def test_literal_vector_usa_decimal_fijo() -> None:
    """El lexer del motor no lee notacion cientifica."""

    texto = literal_vector([0.00003, -1.0, 0.5])

    assert "e" not in texto.lower().replace("vector", "")
    assert texto == "VECTOR(0.0000300, -1.0000000, 0.5000000)"


def test_literal_vector_conserva_la_precision_de_un_float32() -> None:
    """Siete decimales alcanzan porque los vectores estan normalizados."""

    componente = 0.1234567
    texto = literal_vector([componente] * 3)

    assert texto.count("0.1234567") == 3


def test_usa_tls_solo_con_https() -> None:
    assert usa_tls("https://ejemplo.test") is True
    assert usa_tls("http://127.0.0.1:8000") is False
