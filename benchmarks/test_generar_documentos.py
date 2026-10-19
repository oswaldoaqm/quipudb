"""Comprueba el corpus de texto del 2.3.2: consultas, juicios y reproducibilidad (#171).

Las pruebas usan un zip sintetico con la estructura del de BEIR Quora, asi no
dependen de la red. La que usa el zip real se salta si no se descargo.
"""

from __future__ import annotations

import csv
import hashlib
import importlib
import json
import random
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path

import pytest

from benchmarks.scripts.cargar_documentos import (
    INDICE,
    TABLA,
    cargar_quipudb,
    leer_consultas,
    leer_documentos,
    leer_relevancia,
    opciones_ingles,
)
from benchmarks.scripts.generar_documentos import (
    LARGO_TEXTO,
    STOPWORDS_INGLES,
    ZIP_PREDETERMINADO,
    consultas_de,
    generar,
    leer_quora,
    obtener_zip,
    palabras,
    sha256_de,
)

SCRIPT = Path(__file__).resolve().parent / "scripts" / "generar_documentos.py"
# Hash de consultas.csv y de documentos_1000.csv generados desde el zip real.
SHA256_CONSULTAS = "9807a6280572c0c4345bc9d46839b274289da8f503f75e786f85a243aa011304"
SHA256_1K = "63e0d9ccdf8d35065dc0f8ea909b9831b804103492fca9b1856dd239928ca88f"

# Preguntas del zip sintetico con un caso especial.
CORTA = "1"  # cuatro palabras: no da para la consulta de 5+
RELEVANTE_LARGO = "2"  # su relevante no cabe en VARCHAR(256)
CON_SI_MISMA = "3"  # qrels con el par (3, 3), que se ignora
ENCADENADA = "4"  # su relevante es la pregunta 5
PREGUNTAS_SINTETICAS = 130
UTILIZABLES = PREGUNTAS_SINTETICAS - 3  # menos CORTA, RELEVANTE_LARGO y una de 4 o 5
DOCUMENTOS_SINTETICOS = 1_600


def _zip_sintetico(ruta: Path) -> Path:
    """Preguntas 1..130 con un duplicado cada una (1000 + i) y relleno al azar.

    Cada pregunta tiene cuatro palabras propias que solo comparte con su
    duplicado, asi que su consulta de 5+ palabras siempre lo encuentra.
    """
    rng = random.Random(7)
    comunes = [f"tema{n}" for n in range(40)]
    documentos: dict[int, str] = {}
    relevantes: dict[int, list[int]] = {}
    for i in range(1, PREGUNTAS_SINTETICAS + 1):
        propias = f"alfa{i} beta{i} gama{i} delta{i}"
        documentos[i] = f"How can I learn {propias}?"
        documentos[1000 + i] = f"What is the best way to learn {propias} {rng.choice(comunes)}?"
        relevantes[i] = [1000 + i]
    documentos[int(CORTA)] = "Learn alfa1 beta1 gama1?"
    documentos[1000 + int(RELEVANTE_LARGO)] += " very" * 60
    relevantes[int(CON_SI_MISMA)].append(int(CON_SI_MISMA))
    relevantes[int(ENCADENADA)].append(5)
    for i in range(1, DOCUMENTOS_SINTETICOS + 1):
        if i not in documentos:
            documentos[i] = " ".join(rng.choices(comunes, k=rng.randint(3, 9))) + "?"
    documentos[1599] = "what " * 60  # no cabe en la tabla: nunca entra al corpus

    corpus = "".join(
        json.dumps({"_id": str(i), "title": "", "text": texto, "metadata": {}}) + "\n"
        for i, texto in sorted(documentos.items())
    )
    consultas = "".join(
        json.dumps({"_id": str(i), "text": documentos[i], "metadata": {}}) + "\n"
        for i in relevantes
    )
    qrels = "query-id\tcorpus-id\tscore\r\n" + "".join(
        f"{i}\t{doc}\t1\r\n" for i, docs in relevantes.items() for doc in docs
    )
    with zipfile.ZipFile(ruta, "w") as archivo:
        archivo.writestr("quora/corpus.jsonl", corpus)
        archivo.writestr("quora/queries.jsonl", consultas)
        archivo.writestr("quora/qrels/test.tsv", qrels)
        archivo.writestr("quora/qrels/dev.tsv", "query-id\tcorpus-id\tscore\r\n")
    return ruta


def _leer_csv(ruta: Path) -> list[list[str]]:
    with ruta.open(encoding="utf-8", newline="") as archivo:
        return list(csv.reader(archivo))[1:]


@pytest.fixture(scope="module")
def quora_zip(tmp_path_factory) -> Path:
    return _zip_sintetico(tmp_path_factory.mktemp("quora") / "quora.zip")


@pytest.fixture(scope="module")
def generado(quora_zip, tmp_path_factory) -> Path:
    salida = tmp_path_factory.mktemp("generado")
    generar(leer_quora(quora_zip), salida, (300, 1_200), preguntas=UTILIZABLES)
    return salida


def test_genera_consultas_y_un_par_de_archivos_por_tamano(generado):
    assert {ruta.name for ruta in generado.iterdir()} == {
        "consultas.csv",
        "documentos_300.csv",
        "relevancia_300.csv",
        "documentos_1200.csv",
        "relevancia_1200.csv",
    }
    for ruta in generado.iterdir():
        contenido = ruta.read_bytes()
        assert contenido.endswith(b"\n")
        assert b"\r" not in contenido


def test_la_misma_semilla_da_los_mismos_bytes(quora_zip, generado, tmp_path):
    generar(leer_quora(quora_zip), tmp_path, (300, 1_200), preguntas=UTILIZABLES)
    for ruta in generado.iterdir():
        assert (tmp_path / ruta.name).read_bytes() == ruta.read_bytes(), ruta.name


def test_tres_consultas_por_pregunta_de_1_3_y_5_o_mas_palabras(generado):
    consultas = leer_consultas(generado / "consultas.csv")
    assert len(consultas) == 3 * UTILIZABLES
    filas = _leer_csv(generado / "consultas.csv")
    for i in range(0, len(filas), 3):
        corta, media, larga = filas[i : i + 3]
        assert [corta[1], media[1], larga[1]] == ["1", "3", "5+"]
        assert corta[2] == media[2] == larga[2]  # la misma pregunta
        assert len(palabras(corta[3])) == 1
        assert len(set(palabras(media[3]))) == 3
        assert len(palabras(larga[3])) >= 5
        # Las cortas salen de la larga, sin stopwords y en su mismo orden.
        assert set(palabras(media[3])) <= set(palabras(larga[3]))
        assert palabras(corta[3])[0] in palabras(media[3])
        assert not set(palabras(media[3])) & STOPWORDS_INGLES
        posiciones = [palabras(larga[3]).index(p) for p in palabras(media[3])]
        assert posiciones == sorted(posiciones)


def test_la_consulta_corta_es_la_palabra_mas_rara():
    # alfa1 aparece en dos documentos; learn y tema0, en todos.
    df = Counter({"learn": 100, "alfa1": 2, "beta1": 3, "tema0": 100, "gama1": 1})
    assert consultas_de("How can I learn alfa1 beta1 tema0 gama1?", df) == (
        "alfa1",
        "learn alfa1 beta1",
        "how can i learn alfa1 beta1 tema0 gama1",
    )
    # gama1 esta en un solo documento: no encontraria ningun otro.
    assert consultas_de("Is alfa1 the gama1 beta1?", df) is None


def test_las_preguntas_especiales_quedan_fuera(generado):
    preguntas = {fila[2] for fila in _leer_csv(generado / "consultas.csv")}
    assert CORTA not in preguntas
    assert RELEVANTE_LARGO not in preguntas
    assert CON_SI_MISMA in preguntas
    # Si la 4 y la 5 estuvieran las dos, la 5 tendria que estar en el corpus
    # como relevante de la 4 y a la vez fuera de el como pregunta elegida.
    assert len({ENCADENADA, "5"} & preguntas) == 1


@pytest.mark.parametrize("tamano", (300, 1_200))
def test_corpus_con_todos_los_relevantes_y_sin_las_preguntas(generado, tamano):
    documentos = _leer_csv(generado / f"documentos_{tamano}.csv")
    assert [int(fila[0]) for fila in documentos] == list(range(1, tamano + 1))
    quora_ids = [fila[1] for fila in documentos]
    assert len(set(quora_ids)) == tamano
    assert all(len(fila[2].encode("utf-8")) <= LARGO_TEXTO for fila in documentos)
    assert "1599" not in quora_ids

    consultas = _leer_csv(generado / "consultas.csv")
    preguntas = {fila[2] for fila in consultas}
    assert not preguntas & set(quora_ids)

    relevancia = leer_relevancia(generado / f"relevancia_{tamano}.csv")
    assert set(relevancia) == {int(fila[0]) for fila in consultas}
    pregunta_de = {int(fila[0]): fila[2] for fila in consultas}
    for consulta, docs in relevancia.items():
        # Cada pregunta del zip sintetico tiene un duplicado, el 1000 + i; la
        # encadenada tambien tiene a la pregunta 5.
        pregunta = pregunta_de[consulta]
        esperados = {str(1000 + int(pregunta))} | ({"5"} if pregunta == ENCADENADA else set())
        assert {quora_ids[doc - 1] for doc in docs} == esperados


def test_los_relevantes_no_son_los_primeros_ids(generado):
    # Si lo fueran, un empate de puntaje (que se rompe por id) los favoreceria.
    docs = set().union(*leer_relevancia(generado / "relevancia_1200.csv").values())
    assert max(docs) > 1_200 // 2


def test_el_corpus_chico_esta_dentro_del_grande(generado):
    chico = {fila[1] for fila in _leer_csv(generado / "documentos_300.csv")}
    grande = {fila[1] for fila in _leer_csv(generado / "documentos_1200.csv")}
    assert chico < grande


def test_pide_mas_preguntas_o_documentos_de_los_que_hay(quora_zip, tmp_path):
    quora = leer_quora(quora_zip)
    with pytest.raises(ValueError, match="preguntas utilizables"):
        generar(quora, tmp_path, (300,), preguntas=UTILIZABLES + 1)
    with pytest.raises(ValueError, match="corpus de"):
        generar(quora, tmp_path, (DOCUMENTOS_SINTETICOS,), preguntas=10)
    with pytest.raises(ValueError, match="corpus de"):
        generar(quora, tmp_path, (50,), preguntas=UTILIZABLES)


def test_un_zip_con_otro_hash_no_se_usa(quora_zip):
    assert obtener_zip(quora_zip, sha256_de(quora_zip)) == quora_zip
    with pytest.raises(ValueError, match="SHA-256"):
        obtener_zip(quora_zip, "0" * 64)


def test_el_script_corre_fuera_del_repositorio(quora_zip, tmp_path):
    salida = tmp_path / "salida"
    comando = [
        sys.executable,
        "-B",
        str(SCRIPT),
        "--salida",
        str(salida),
        "--tamanos",
        "1000",
        "--zip",
        str(quora_zip),
        "--sha256",
        sha256_de(quora_zip),
    ]
    subprocess.run(comando, cwd=tmp_path, check=True, capture_output=True, text=True)
    assert len(leer_documentos(salida / "documentos_1000.csv", 1_000).registros) == 1_000
    assert len(leer_consultas(salida / "consultas.csv")) == 300


def test_la_lectura_rechaza_archivos_invalidos(tmp_path):
    ruta = tmp_path / "documentos.csv"
    with pytest.raises(ValueError, match="generar_documentos.py"):
        leer_documentos(ruta)
    ruta.write_text("id,quora_id,texto\n2,9,hola\n", encoding="utf-8")
    with pytest.raises(ValueError, match="se esperaba el id 1"):
        leer_documentos(ruta)
    ruta.write_text("id,quora_id,texto\n1,9," + "a" * (LARGO_TEXTO + 1) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="VARCHAR"):
        leer_documentos(ruta)
    ruta.write_text("id,quora_id,texto\n1,9,hola\n", encoding="utf-8")
    with pytest.raises(ValueError, match="se esperaban 1000"):
        leer_documentos(ruta, 1_000)

    consultas = tmp_path / "consultas.csv"
    consultas.write_text("id,longitud,pregunta,texto\n1,2,9,hola mundo\n", encoding="utf-8")
    with pytest.raises(ValueError, match="longitud"):
        leer_consultas(consultas)
    consultas.write_text("id,longitud,pregunta,texto\n1,1,9,don't\n", encoding="utf-8")
    with pytest.raises(ValueError, match="comilla"):
        leer_consultas(consultas)


@pytest.mark.skipif(not ZIP_PREDETERMINADO.exists(), reason="quora.zip no se descargo")
def test_con_el_zip_real_los_hashes_de_referencia(tmp_path):
    rutas = generar(leer_quora(obtener_zip(ZIP_PREDETERMINADO)), tmp_path, (1_000,))
    assert hashlib.sha256(rutas[0].read_bytes()).hexdigest() == SHA256_CONSULTAS
    assert hashlib.sha256(rutas[1].read_bytes()).hexdigest() == SHA256_1K


def _bindings():
    try:
        return importlib.import_module("quipudb_native")
    except ImportError:
        pytest.skip("bindings quipudb_native no disponibles")


def test_integracion_las_stopwords_son_las_del_core():
    nativo = _bindings()
    opciones = opciones_ingles(nativo)
    assert len(STOPWORDS_INGLES) == 127
    assert all(nativo.tokenize(palabra, opciones) == [] for palabra in STOPWORDS_INGLES)


def test_integracion_carga_y_cada_consulta_larga_encuentra_su_duplicado(generado, tmp_path):
    nativo = _bindings()
    dataset = leer_documentos(generado / "documentos_300.csv")
    db = nativo.Database(tmp_path / "catalogo.txt")
    tabla, indice = cargar_quipudb(nativo, db, dataset)

    assert len(tabla) == 300
    assert indice.documents == 300
    assert indice.tokenizer_options == opciones_ingles(nativo)
    assert db.table(TABLA) is tabla
    assert db.index(TABLA, INDICE) is indice

    relevancia = leer_relevancia(generado / "relevancia_300.csv")
    for consulta in leer_consultas(generado / "consultas.csv"):
        assert nativo.tokenize(consulta.texto, opciones_ingles(nativo)) != []
        if consulta.longitud == "5+":
            top = indice.search_text(consulta.texto, nativo.TextRanking.BM25, 10)
            assert relevancia[consulta.id] <= {tabla.read(rid)[0] for rid, _ in top}
