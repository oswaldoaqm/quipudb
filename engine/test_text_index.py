"""Pruebas del indice de texto desde Python (issue #166).

Construyen el indice sobre una tabla chica a traves de `Database`, como lo hara
el motor, y buscan con los dos rankings de la Parte 3: TF-IDF con coseno (#164)
y BM25 (#165).

Se saltan si el modulo no esta compilado; el job de bindings del CI lo compila,
asi que ahi no se saltan.
"""

from __future__ import annotations

import pytest

quipudb = pytest.importorskip(
    "quipudb_native",
    reason="los bindings no estan compilados: cmake -DQUIPUDB_BUILD_PYTHON=ON",
)

# Los documentos del ejemplo de pesos TF-IDF de la diapositiva.
TEXTOS = [
    "Cargamento de oro dañado por el fuego",
    "La entrega de la plata llegó en el camión color plata",
    "El cargamento de oro llegó en un camión",
]


def esquema() -> quipudb.Schema:
    return quipudb.Schema(
        table_name="documentos",
        columns=[
            quipudb.Column("id", quipudb.DataType.INT),
            quipudb.Column("contenido", quipudb.DataType.VARCHAR, 200),
        ],
        key_column=0,
    )


@pytest.fixture()
def db(tmp_path):
    base = quipudb.Database(tmp_path / "catalogo.txt")
    tabla = base.create_table(esquema(), quipudb.kind.HEAP)
    for i, texto in enumerate(TEXTOS, start=1):
        tabla.insert([i, texto])
    return base


def ids(db, resultado) -> list[int]:
    """Los ids de los registros que devolvio una busqueda, en orden."""
    tabla = db.table("documentos")
    return [tabla.read(rid)[0] for rid, _ in resultado]


def crear(db):
    return db.create_index("documentos", "por_contenido", "contenido", quipudb.kind.INVERTED_INDEX)


def test_el_indice_llega_como_text_index(db):
    ix = crear(db)
    assert isinstance(ix, quipudb.TextIndex)
    assert ix.kind == "inverted_index" == quipudb.kind.INVERTED_INDEX
    assert ix.documents == 3
    assert ix.terms > 0


@pytest.mark.parametrize("ranking", [quipudb.TextRanking.TF_IDF, quipudb.TextRanking.BM25])
def test_busca_con_los_dos_rankings(db, ranking):
    ix = crear(db)
    resultado = ix.search_text("oro plata camión", ranking, 10)
    # D2 primero con los dos: es el unico con plata, y plata es el termino mas raro.
    assert ids(db, resultado)[0] == 2
    assert sorted(ids(db, resultado)) == [1, 2, 3]
    puntajes = [score for _, score in resultado]
    assert puntajes == sorted(puntajes, reverse=True)
    assert all(score > 0 for score in puntajes)


def test_tf_idf_da_el_orden_de_la_diapositiva(db):
    ix = crear(db)
    assert ids(db, ix.search_text("oro plata camión", quipudb.TextRanking.TF_IDF)) == [2, 3, 1]


def test_k_corta_y_sin_k_devuelve_todos(db):
    ix = crear(db)
    assert len(ix.search_text("oro plata camión", quipudb.TextRanking.BM25, 1)) == 1
    assert len(ix.search_text("oro plata camión", quipudb.TextRanking.BM25)) == 3
    assert ix.search_text("helicóptero", quipudb.TextRanking.BM25, 10) == []


def test_se_reabre_desde_el_catalogo(db, tmp_path):
    antes = ids(db, crear(db).search_text("oro camión", quipudb.TextRanking.BM25))
    del db
    otra = quipudb.Database(tmp_path / "catalogo.txt")
    ix = otra.index("documentos", "por_contenido")
    assert isinstance(ix, quipudb.TextIndex)
    assert ids(otra, ix.search_text("oro camión", quipudb.TextRanking.BM25)) == antes


def test_se_reconstruye_con_otras_opciones(db):
    ix = crear(db)
    db.table("documentos").insert([4, "The gold mines of the north"])
    assert ix.search_text("mine", quipudb.TextRanking.BM25) == []

    opciones = quipudb.TokenizerOptions()
    opciones.stopword_languages = [quipudb.TextLanguage.ENGLISH]
    opciones.stemmer = quipudb.TextLanguage.ENGLISH
    ix.build(opciones)
    assert ix.documents == 4
    assert ix.tokenizer_options == opciones
    # "mines" y "mine" dan la misma raiz con el stemmer ingles.
    assert ids(db, ix.search_text("mine", quipudb.TextRanking.BM25)) == [4]


def test_tokenize_muestra_lo_que_ve_el_indice():
    assert quipudb.tokenize("Mis amigas y amigos peruanos") == ["amig", "amig", "peruan"]
    sin_stemmer = quipudb.TokenizerOptions()
    sin_stemmer.stemmer = None
    assert quipudb.tokenize("Mis amigas", sin_stemmer) == ["amigas"]


def test_lo_que_no_aplica_lanza_unsupported(db):
    ix = crear(db)
    with pytest.raises(quipudb.Unsupported, match="search_text"):
        ix.search("oro")
    with pytest.raises(quipudb.Unsupported, match="build"):
        ix.insert("oro", quipudb.RID())


def test_solo_sobre_columnas_varchar(db):
    with pytest.raises(quipudb.SchemaError):
        db.create_index("documentos", "mal", "id", quipudb.kind.INVERTED_INDEX)
