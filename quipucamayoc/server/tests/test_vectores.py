"""Embeddings de los chunks y su carga en QuipuDB (#252)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.corpus.vectores import (
    agregar_a_cache,
    cargar,
    chunks_del_motor,
    embeber,
    leer_cache,
    tokens_estimados,
)
from app.motor import literal_vector
from app.rag.embeddings import DIMENSION, LimiteDeUso, normalizar
from app.rag.llm import LLMError
from tests.llm_falso import EmbedderFalso


def _v(x: float) -> list[float]:
    return [x] * DIMENSION


def test_la_cache_va_y_vuelve_e_ignora_un_registro_cortado(tmp_path: Path) -> None:
    ruta = tmp_path / "e.bin"
    agregar_a_cache(ruta, [(1, _v(0.5)), (2, _v(-0.25))])
    with ruta.open("ab") as archivo:
        archivo.write(b"\x03\x00")  # un corte a mitad del registro 3

    assert set(leer_cache(ruta)) == {1, 2}
    assert leer_cache(ruta)[2][0] == -0.25

    agregar_a_cache(ruta, [(3, _v(1.0))])
    assert set(leer_cache(ruta)) == {1, 2, 3}


def test_literal_vector_en_decimal_fijo_sin_notacion_cientifica() -> None:
    assert literal_vector([0.5, -0.0000123, 1.0]) == "VECTOR(0.5000000, -0.0000123, 1.0000000)"


def test_normalizar_deja_norma_uno_y_rechaza_el_vector_nulo() -> None:
    assert normalizar([3.0, 4.0]) == [0.6, 0.8]
    with pytest.raises(LLMError):
        normalizar([0.0, 0.0])


class _Reloj:
    def __init__(self) -> None:
        self.ahora = 0.0
        self.siestas: list[float] = []

    def dormir(self, s: float) -> None:
        self.siestas.append(s)
        self.ahora += s

    def reloj(self) -> float:
        return self.ahora


class _ConCuota(EmbedderFalso):
    """Responde 429 las primeras ``fallas`` veces."""

    def __init__(self, fallas: int) -> None:
        super().__init__()
        self.fallas = fallas

    def documentos(self, textos):
        if self.fallas:
            self.fallas -= 1
            raise LimiteDeUso("limite")
        return super().documentos(textos)


def test_embeber_salta_lo_que_ya_esta_y_respeta_el_ritmo(tmp_path: Path) -> None:
    cache = tmp_path / "e.bin"
    agregar_a_cache(cache, [(1, _v(0.1))])
    chunks = [(i, "palabra " * 300) for i in range(1, 6)]
    reloj = _Reloj()

    reporte = embeber(
        EmbedderFalso(), chunks, cache, lote=2, tokens_por_minuto=690,
        dormir=reloj.dormir, reloj=reloj.reloj,
    )

    assert (reporte.nuevos, reporte.ya_estaban) == (4, 1)
    assert set(leer_cache(cache)) == {1, 2, 3, 4, 5}
    # 2 chunks de 300 palabras son ~1 380 tokens: dos minutos a 690 por minuto.
    # Despues del ultimo lote no se espera.
    assert reloj.siestas == [pytest.approx(120.0)]
    assert tokens_estimados(["a b"]) == pytest.approx(4.6)


def test_embeber_espera_ante_el_limite_de_uso_y_reintenta(tmp_path: Path) -> None:
    reloj = _Reloj()

    reporte = embeber(
        _ConCuota(fallas=2), [(1, "hola")], tmp_path / "e.bin",
        dormir=reloj.dormir, reloj=reloj.reloj,
    )

    assert (reporte.nuevos, reporte.esperas_por_cuota) == (1, 2)
    assert reloj.siestas == [60.0, 60.0]


def test_embeber_se_rinde_si_la_cuota_no_vuelve(tmp_path: Path) -> None:
    reloj = _Reloj()

    with pytest.raises(LimiteDeUso):
        embeber(
            _ConCuota(fallas=5), [(1, "hola")], tmp_path / "e.bin", reintentos=3,
            dormir=reloj.dormir, reloj=reloj.reloj,
        )


# ---------------------------------------------------------------------------
# Contra el motor real
# ---------------------------------------------------------------------------


def test_cargar_crea_la_tabla_el_hnsw_y_el_hash_sobre_chunks(corpus) -> None:
    motor, tablas = corpus
    chunks = chunks_del_motor(motor, tablas)
    vectores = dict(zip([c for c, _, _ in chunks], EmbedderFalso().documentos([t for _, _, t in chunks]), strict=True))
    faltante = chunks[-1][0]
    del vectores[faltante]

    reporte = cargar(motor, chunks, vectores, tablas=tablas)

    assert (reporte.insertados, reporte.sin_embedding, reporte.indice_creado) == (
        len(chunks) - 1, [faltante], True
    )
    info = {t["name"]: t for t in motor.tables()}
    assert {i["name"]: i["structure"] for i in info[tablas.vectores]["indexes"]} == {tablas.hnsw: "hnsw"}
    assert tablas.chunks_por_id in {i["name"] for i in info[tablas.chunks]["indexes"]}

    # Volver a cargar solo agrega lo que falta y no recrea el indice.
    vectores[faltante] = EmbedderFalso().documentos(["x"])[0]
    otra = cargar(motor, chunks, vectores, tablas=tablas)
    assert (otra.insertados, otra.ya_estaban, otra.indice_creado) == (1, len(chunks) - 1, False)
