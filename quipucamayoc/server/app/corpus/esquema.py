"""Las tablas de Quipucamayoc en QuipuDB (ADR 0010, seccion 2).

Un solo lugar para nombres, tamanos y DDL: la carga (#244) y la recuperacion
(#245) leen de aqui, asi que un cambio de esquema no queda a medias.
"""

from __future__ import annotations

from dataclasses import dataclass

# Bytes de cada VARCHAR. Un registro de heap admite 4 087 bytes (paginas de
# 4 KiB): papers ocupa 3 048 y chunks 4 016.
BYTES_PAPERS = {
    "arxiv_id": 20,
    "titulo": 300,
    "autores": 600,
    "categoria": 20,
    "abstract": 2000,
    "url": 100,
}
BYTES_TEXTO_CHUNK = 4000

CHUNKS_POR_PAPER = 10_000
"""El id de un chunk es ``paper_id * CHUNKS_POR_PAPER + orden``: determinista,
asi que volver a cargar un paper no necesita preguntar por el ultimo id."""


@dataclass(frozen=True, slots=True)
class Tablas:
    """Nombres de las tablas. El prefijo deja que las pruebas no pisen el corpus."""

    prefijo: str = ""

    @property
    def papers(self) -> str:
        return f"{self.prefijo}papers"

    @property
    def chunks(self) -> str:
        return f"{self.prefijo}chunks"

    def ddl(self) -> list[str]:
        p, c = self.papers, self.chunks
        return [
            (
                f"CREATE TABLE {p} (arxiv_id VARCHAR(20) PRIMARY KEY, id INT, "
                "titulo VARCHAR(300), autores VARCHAR(600), anio INT, categoria VARCHAR(20), "
                "abstract VARCHAR(2000), url VARCHAR(100)) USING HEAP"
            ),
            f"CREATE INDEX {p}_por_id ON {p} (id) USING HASH",
            f"CREATE INDEX {p}_por_anio ON {p} (anio) USING BPLUS",
            (
                f"CREATE TABLE {c} (id INT PRIMARY KEY, paper_id INT, orden INT, pagina INT, "
                f"texto VARCHAR({BYTES_TEXTO_CHUNK})) USING HEAP"
            ),
            f"CREATE INDEX {c}_por_texto ON {c} (texto) USING FULLTEXT WITH LANGUAGE=english",
            f"CREATE INDEX {c}_por_paper ON {c} (paper_id) USING HASH",
        ]

    columnas_papers = ("arxiv_id", "id", "titulo", "autores", "anio", "categoria", "abstract", "url")
    columnas_chunks = ("id", "paper_id", "orden", "pagina", "texto")


def recortar(texto: str, max_bytes: int) -> str:
    """``texto`` en a lo sumo ``max_bytes`` bytes UTF-8, sin partir un caracter."""

    datos = texto.encode("utf-8")
    if len(datos) <= max_bytes:
        return texto
    return datos[:max_bytes].decode("utf-8", errors="ignore")


def chunk_id(paper_id: int, orden: int) -> int:
    if not 0 <= orden < CHUNKS_POR_PAPER:
        raise ValueError(f"un paper no puede tener mas de {CHUNKS_POR_PAPER} chunks")
    return paper_id * CHUNKS_POR_PAPER + orden
