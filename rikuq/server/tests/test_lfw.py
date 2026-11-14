"""El dataset de rostros: descarga, manifiesto y verificacion (#301), sin red."""

import hashlib
import tarfile
from pathlib import Path

import httpx
import pytest

from app.dataset.lfw import (
    DatasetError,
    Rostro,
    construir,
    descargar,
    escribir,
    leer,
    main,
    recolectar,
    resumen,
    sha256,
    verificar,
)


def _imagen(carpeta: Path, nombre: str, contenido: bytes = b"\xff\xd8\xff falsa") -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    archivo = carpeta / nombre
    archivo.write_bytes(contenido)
    return archivo


def _poblar(datos: Path, personas: dict[str, int], origen: str = "lfw") -> None:
    """Crea ``datos/<origen>/<persona>/<persona>_000N.jpg`` con N fotos."""

    for persona, cuantas in personas.items():
        for i in range(1, cuantas + 1):
            _imagen(datos / origen / persona, f"{persona}_{i:04d}.jpg", f"{persona}{i}".encode())


def test_sha256_es_el_del_archivo(tmp_path: Path) -> None:
    archivo = _imagen(tmp_path, "x.jpg", b"unos bytes")

    assert sha256(archivo) == hashlib.sha256(b"unos bytes").hexdigest()


def test_sha256_de_un_archivo_grande_no_lo_carga_entero(tmp_path: Path) -> None:
    """Se lee por trozos: LFW son 173 MB y el manifiesto los recorre todos."""

    contenido = b"x" * (3 * (1 << 20) + 7)
    archivo = _imagen(tmp_path, "grande.jpg", contenido)

    assert sha256(archivo) == hashlib.sha256(contenido).hexdigest()


def test_el_rostro_resuelve_su_ruta_contra_la_carpeta_de_datos(tmp_path: Path) -> None:
    rostro = Rostro("Ana_Lopez", "lfw/Ana_Lopez/Ana_Lopez_0001.jpg", "abc", "lfw")

    assert rostro.archivo(tmp_path) == tmp_path / "lfw" / "Ana_Lopez" / "Ana_Lopez_0001.jpg"


def test_recolectar_toma_el_nombre_de_la_carpeta_como_persona(tmp_path: Path) -> None:
    _poblar(tmp_path, {"Ana_Lopez": 2})

    rostros = list(recolectar(tmp_path / "lfw", "lfw"))

    assert [r.ruta for r in rostros] == [
        "lfw/Ana_Lopez/Ana_Lopez_0001.jpg",
        "lfw/Ana_Lopez/Ana_Lopez_0002.jpg",
    ]
    assert {r.persona for r in rostros} == {"Ana_Lopez"}
    assert all(r.origen == "lfw" for r in rostros)


def test_recolectar_ignora_lo_que_no_es_imagen(tmp_path: Path) -> None:
    _poblar(tmp_path, {"Ana_Lopez": 1})
    (tmp_path / "lfw" / "Ana_Lopez" / "notas.txt").write_text("no es una foto")
    (tmp_path / "lfw" / "suelta.jpg").write_bytes(b"fuera de una carpeta de persona")

    rostros = list(recolectar(tmp_path / "lfw", "lfw"))

    assert [r.ruta for r in rostros] == ["lfw/Ana_Lopez/Ana_Lopez_0001.jpg"]


def test_recolectar_calcula_el_sha_de_cada_imagen(tmp_path: Path) -> None:
    _poblar(tmp_path, {"Ana_Lopez": 2})

    rostros = list(recolectar(tmp_path / "lfw", "lfw"))

    assert [r.sha256 for r in rostros] == [sha256(r.archivo(tmp_path)) for r in rostros]
    assert rostros[0].sha256 != rostros[1].sha256


def test_recolectar_una_carpeta_que_no_existe_no_es_error(tmp_path: Path) -> None:
    """El equipo puede no haber puesto sus fotos todavia."""

    assert list(recolectar(tmp_path / "equipo", "equipo")) == []


def test_construir_descarta_las_identidades_con_pocas_fotos(tmp_path: Path) -> None:
    """Con una sola foto no queda ninguna para preguntar que no se haya cargado."""

    _poblar(tmp_path, {"Con_Muchas": 3, "Con_Una": 1, "Con_Dos": 2})

    rostros = construir(tmp_path, minimo=2)

    assert {r.persona for r in rostros} == {"Con_Muchas", "Con_Dos"}
    assert len(rostros) == 5


def test_construir_siempre_incluye_al_equipo(tmp_path: Path) -> None:
    """Son pocas fotos y son las que la demostracion tiene que reconocer."""

    _poblar(tmp_path, {"Con_Muchas": 10})
    _poblar(tmp_path, {"Danna": 1}, origen="equipo")

    rostros = construir(tmp_path, minimo=10)

    assert ("equipo", "Danna") in {(r.origen, r.persona) for r in rostros}


def test_construir_sin_imagenes_explica_que_hacer(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match="descargar"):
        construir(tmp_path, minimo=1)


def test_construir_rechaza_un_minimo_invalido(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match="al menos 1"):
        construir(tmp_path, minimo=0)


def test_escribir_y_leer_dan_lo_mismo(tmp_path: Path) -> None:
    _poblar(tmp_path, {"Ana_Lopez": 2})
    _poblar(tmp_path, {"Danna": 1}, origen="equipo")
    originales = construir(tmp_path, minimo=1)
    salida = tmp_path / "dataset" / "rostros.tsv"

    filas = escribir(originales, salida)

    assert filas == 3
    assert leer(salida) == originales


def test_el_manifiesto_es_un_tsv_con_cabecera(tmp_path: Path) -> None:
    _poblar(tmp_path, {"Ana_Lopez": 1})
    salida = tmp_path / "rostros.tsv"

    escribir(construir(tmp_path, minimo=1), salida)

    lineas = salida.read_text(encoding="utf-8").splitlines()
    assert lineas[0] == "persona\truta\tsha256\torigen"
    assert lineas[1].split("\t")[:2] == ["Ana_Lopez", "lfw/Ana_Lopez/Ana_Lopez_0001.jpg"]


def test_leer_un_manifiesto_que_no_existe_explica_que_hacer(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match="manifiesto"):
        leer(tmp_path / "no-esta.tsv")


def test_leer_rechaza_una_cabecera_distinta(tmp_path: Path) -> None:
    ruta = tmp_path / "rostros.tsv"
    ruta.write_text("persona\truta\n", encoding="utf-8")

    with pytest.raises(DatasetError, match="cabecera"):
        leer(ruta)


def test_leer_rechaza_una_fila_incompleta(tmp_path: Path) -> None:
    """Saltarla en silencio volveria inutil la garantia del manifiesto."""

    ruta = tmp_path / "rostros.tsv"
    ruta.write_text("persona\truta\tsha256\torigen\nAna\tlfw/Ana/a.jpg\n", encoding="utf-8")

    with pytest.raises(DatasetError, match=r":2 "):
        leer(ruta)


def test_verificar_acepta_el_dataset_intacto(tmp_path: Path) -> None:
    _poblar(tmp_path, {"Ana_Lopez": 2})
    rostros = construir(tmp_path, minimo=1)

    assert verificar(rostros, tmp_path) == []


def test_verificar_avisa_de_un_archivo_que_falta(tmp_path: Path) -> None:
    _poblar(tmp_path, {"Ana_Lopez": 2})
    rostros = construir(tmp_path, minimo=1)
    rostros[0].archivo(tmp_path).unlink()

    assert verificar(rostros, tmp_path) == [(rostros[0].ruta, "no esta en disco")]


def test_verificar_avisa_de_un_archivo_cambiado(tmp_path: Path) -> None:
    """Es para lo que sirve el SHA-256: el dataset no se versiona, se comprueba."""

    _poblar(tmp_path, {"Ana_Lopez": 2})
    rostros = construir(tmp_path, minimo=1)
    rostros[0].archivo(tmp_path).write_bytes(b"otra imagen")

    assert verificar(rostros, tmp_path) == [(rostros[0].ruta, "el contenido cambio")]


def test_verificar_los_junta_todos_en_vez_de_cortar_al_primero(tmp_path: Path) -> None:
    _poblar(tmp_path, {"Ana_Lopez": 3})
    rostros = construir(tmp_path, minimo=1)
    rostros[0].archivo(tmp_path).unlink()
    rostros[2].archivo(tmp_path).write_bytes(b"otra imagen")

    assert verificar(rostros, tmp_path) == [
        (rostros[0].ruta, "no esta en disco"),
        (rostros[2].ruta, "el contenido cambio"),
    ]


# --- descarga -----------------------------------------------------------------


def _tgz(ruta: Path, contenido: Path) -> Path:
    with tarfile.open(ruta, "w:gz") as tar:
        tar.add(contenido, arcname=contenido.name)
    return ruta


def test_descargar_extrae_el_comprimido_que_ya_esta(tmp_path: Path) -> None:
    """Un .tgz ya bajado no se vuelve a pedir: la prueba no toca la red."""

    fuente = tmp_path / "fuente" / "lfw"
    _imagen(fuente / "Ana_Lopez", "Ana_Lopez_0001.jpg")
    datos = tmp_path / "datos"
    datos.mkdir()
    _tgz(datos / "lfw.tgz", fuente)

    raiz = descargar(destino=datos)

    assert raiz == datos / "lfw"
    assert (raiz / "Ana_Lopez" / "Ana_Lopez_0001.jpg").is_file()


def test_descargar_deja_el_sha_del_comprimido(tmp_path: Path) -> None:
    fuente = tmp_path / "fuente" / "lfw"
    _imagen(fuente / "Ana_Lopez", "Ana_Lopez_0001.jpg")
    datos = tmp_path / "datos"
    datos.mkdir()
    comprimido = _tgz(datos / "lfw.tgz", fuente)

    descargar(destino=datos)

    anotado = (datos / "lfw.tgz.sha256").read_text(encoding="utf-8")
    assert anotado == f"{sha256(comprimido)}  lfw.tgz\n"


def test_descargar_no_rehace_el_trabajo(tmp_path: Path) -> None:
    datos = tmp_path / "datos"
    _imagen(datos / "lfw" / "Ana_Lopez", "Ana_Lopez_0001.jpg")

    # Sin lfw.tgz y sin red: si intentara bajar o extraer, fallaria.
    assert descargar(destino=datos) == datos / "lfw"


def test_descargar_avisa_si_el_comprimido_no_trae_lfw(tmp_path: Path) -> None:
    otra = tmp_path / "fuente" / "otra_cosa"
    _imagen(otra, "x.jpg")
    datos = tmp_path / "datos"
    datos.mkdir()
    _tgz(datos / "lfw.tgz", otra)

    with pytest.raises(DatasetError, match="lfw/"):
        descargar(destino=datos)


def test_lfw_se_baja_de_un_espejo_porque_umass_no_resuelve() -> None:
    """El host oficial dejo de resolver; figshare es el espejo de scikit-learn."""

    from app.dataset.lfw import URL_LFW, URL_LFW_OFICIAL

    assert URL_LFW.startswith("https://")
    assert "umass" not in URL_LFW
    assert "umass" in URL_LFW_OFICIAL


def test_descargar_no_deja_un_comprimido_a_medias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si no se borrara el .part, la siguiente corrida lo daria por bueno."""

    def cae(*_args: object, **_kwargs: object) -> None:
        raise httpx.ConnectError("sin red")

    monkeypatch.setattr(httpx, "stream", cae)
    datos = tmp_path / "datos"
    datos.mkdir()

    with pytest.raises(DatasetError, match="no se pudo bajar"):
        descargar(destino=datos)

    assert not (datos / "lfw.part").exists()
    assert not (datos / "lfw.tgz").exists()


# --- linea de comandos --------------------------------------------------------


def test_resumen_separa_lfw_del_equipo(tmp_path: Path) -> None:
    _poblar(tmp_path, {"Ana_Lopez": 2, "Beto_Diaz": 2})
    _poblar(tmp_path, {"Danna": 3}, origen="equipo")

    texto = resumen(construir(tmp_path, minimo=1))

    assert "equipo: 1 personas, 3 imagenes" in texto
    assert "lfw: 2 personas, 4 imagenes" in texto


def test_main_arma_el_manifiesto_y_lo_verifica(tmp_path: Path, capsys) -> None:
    _poblar(tmp_path, {"Ana_Lopez": 2})
    salida = tmp_path / "rostros.tsv"

    armar = main(["manifiesto", "--datos", str(tmp_path), "--salida", str(salida), "--minimo", "1"])
    assert armar == 0
    assert "2 imagenes" in capsys.readouterr().out

    comprobar = main(["verificar", "--manifiesto", str(salida), "--datos", str(tmp_path)])
    assert comprobar == 0
    assert "todas coinciden" in capsys.readouterr().out


def test_main_devuelve_1_si_el_dataset_no_coincide(tmp_path: Path, capsys) -> None:
    _poblar(tmp_path, {"Ana_Lopez": 2})
    salida = tmp_path / "rostros.tsv"
    main(["manifiesto", "--datos", str(tmp_path), "--salida", str(salida), "--minimo", "1"])
    capsys.readouterr()
    (tmp_path / "lfw" / "Ana_Lopez" / "Ana_Lopez_0001.jpg").write_bytes(b"otra")

    assert main(["verificar", "--manifiesto", str(salida), "--datos", str(tmp_path)]) == 1
    assert "el contenido cambio" in capsys.readouterr().err


def test_main_explica_el_error_en_vez_de_reventar(tmp_path: Path, capsys) -> None:
    """Un DatasetError sale como mensaje y codigo 1, no como traza."""

    assert main(["manifiesto", "--datos", str(tmp_path), "--salida", str(tmp_path / "x.tsv")]) == 1
    assert "error: no hay imagenes" in capsys.readouterr().err
