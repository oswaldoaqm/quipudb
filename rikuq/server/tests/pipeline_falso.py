"""Pipeline inyectable: ninguna foto, modelo o import de OpenCV."""

from app.pipeline import ImagenEntrada, RostroEntrada


class PipelineFalso:
    def __init__(self, error=None, rostros: int = 1) -> None:
        self.error = error
        self.rostros = rostros
        self.recibidos: list[bytes] = []

    def extraer(self, datos: bytes) -> ImagenEntrada:
        self.recibidos.append(datos)
        if self.error:
            raise self.error
        vector = (1.0,) + (0.0,) * 511
        return ImagenEntrada(
            640,
            480,
            tuple(RostroEntrada((10 + i * 100, 20, 80, 90), vector) for i in range(self.rostros)),
        )
