"""PDFs de prueba escritos a mano: sin archivos binarios ni licencias ajenas.

Cada pagina es una lista de lineas en Helvetica; pypdf las extrae una por
linea, como las de un paper real.
"""

from __future__ import annotations

from pathlib import Path


def _escapar(linea: str) -> str:
    return linea.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def pdf(paginas: list[list[str]]) -> bytes:
    objetos: list[bytes] = []
    n = len(paginas)
    # 1 catalogo, 2 paginas, 3 fuente, y por cada pagina su objeto y su contenido.
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objetos.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objetos.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    objetos.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, lineas in enumerate(paginas):
        texto = " T* ".join(f"({_escapar(linea)}) Tj" for linea in lineas)
        flujo = f"BT /F1 11 Tf 14 TL 72 740 Td {texto} ET".encode("latin-1")
        objetos.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode()
        )
        objetos.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(flujo), flujo))

    salida = bytearray(b"%PDF-1.4\n")
    posiciones = []
    for numero, cuerpo in enumerate(objetos, start=1):
        posiciones.append(len(salida))
        salida += b"%d 0 obj\n%s\nendobj\n" % (numero, cuerpo)
    xref = len(salida)
    salida += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    salida += b"".join(b"%010d 00000 n \n" % posicion for posicion in posiciones)
    salida += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objetos) + 1,
        xref,
    )
    return bytes(salida)


def escribir(ruta: Path, paginas: list[list[str]]) -> Path:
    ruta.write_bytes(pdf(paginas))
    return ruta
