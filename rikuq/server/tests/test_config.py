"""La configuracion del servidor, leida del entorno (#302)."""

from app.config import TIMEOUT_POR_DEFECTO, URL_POR_DEFECTO, Settings


def test_sin_variables_apunta_al_motor_local() -> None:
    settings = Settings.del_entorno({})

    assert settings.quipudb_api_url == URL_POR_DEFECTO
    assert settings.quipudb_timeout_s == TIMEOUT_POR_DEFECTO


def test_la_url_por_defecto_es_una_ip_y_no_localhost() -> None:
    """En Windows localhost prueba IPv6 primero y cada peticion pierde ~0.8 s."""

    assert "127.0.0.1" in URL_POR_DEFECTO
    assert "localhost" not in URL_POR_DEFECTO


def test_lee_la_url_del_entorno() -> None:
    settings = Settings.del_entorno({"QUIPUDB_API_URL": "http://otro:9000"})

    assert settings.quipudb_api_url == "http://otro:9000"


def test_url_del_issue_es_prioritaria_sin_romper_la_variable_anterior() -> None:
    settings = Settings.del_entorno(
        {"QUIPUDB_URL": "http://nuevo:8000/", "QUIPUDB_API_URL": "http://anterior:8000"}
    )
    assert settings.quipudb_api_url == "http://nuevo:8000"


def test_lee_los_origenes_permitidos() -> None:
    settings = Settings.del_entorno({"RIKUQ_WEB_ORIGINS": " http://uno:5175, http://dos:5175 "})
    assert settings.web_origins == ("http://uno:5175", "http://dos:5175")


def test_le_quita_la_barra_final_a_la_url() -> None:
    """Si no, cada ruta queda con una barra doble."""

    settings = Settings.del_entorno({"QUIPUDB_API_URL": "http://otro:9000/"})

    assert settings.quipudb_api_url == "http://otro:9000"


def test_una_variable_vacia_vale_como_ausente() -> None:
    """Un `.env` a medio llenar no deberia dejar al cliente sin destino."""

    settings = Settings.del_entorno({"QUIPUDB_API_URL": "   ", "QUIPUDB_TIMEOUT_S": ""})

    assert settings.quipudb_api_url == URL_POR_DEFECTO
    assert settings.quipudb_timeout_s == TIMEOUT_POR_DEFECTO


def test_lee_el_timeout_del_entorno() -> None:
    assert Settings.del_entorno({"QUIPUDB_TIMEOUT_S": "5.5"}).quipudb_timeout_s == 5.5


def test_un_timeout_que_no_es_numero_no_rompe_el_arranque() -> None:
    assert Settings.del_entorno({"QUIPUDB_TIMEOUT_S": "pronto"}).quipudb_timeout_s == (
        TIMEOUT_POR_DEFECTO
    )


def test_un_timeout_de_cero_o_negativo_se_descarta() -> None:
    """Cero haria que toda peticion expire antes de salir."""

    for valor in ("0", "-3"):
        assert Settings.del_entorno({"QUIPUDB_TIMEOUT_S": valor}).quipudb_timeout_s == (
            TIMEOUT_POR_DEFECTO
        )


def test_el_timeout_por_defecto_aguanta_una_carga_larga() -> None:
    """Cargar 4324 rostros son miles de INSERT y una reconstruccion de indice."""

    assert TIMEOUT_POR_DEFECTO >= 60.0
