"""Detectar un rostro y alinearlo (#304).

Pasos 1 y 2 de la lamina de reconocimiento facial de la clase 10:
*Input & Landmark Detection* y *Geometric Alignment*.

YuNet devuelve, por cada rostro, su caja, una confianza y cinco landmarks:
los dos ojos, la nariz y las dos comisuras de la boca. Con esos cinco puntos
se endereza y se escala el recorte hasta dejar el rostro frontal y del mismo
tamano siempre, que es lo que la lamina llama *Normalized, Frontal Face*.

Alinear importa porque el descriptor de #305 no es invariante a la pose ni a
la escala: si no se normaliza, la misma persona de perfil y de frente cae en
puntos distintos del espacio y el umbral de #303 no puede separar nada.

Para mirar que encuentra en una foto, desde ``rikuq/server``::

    python -m app.rostros.deteccion foto.jpg
    python -m app.rostros.deteccion foto.jpg --guardar recortes/
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from app.rostros.errores import EmptyObjectError
from app.rostros.lectura import leer_color
from app.rostros.modelos import MODELOS, YUNET, traer

UMBRAL_CONFIANZA = 0.7
"""Confianza minima para dar un rostro por bueno.

Antes era 0.9, con el argumento de que YuNet "marca manchas" si se afloja.
**Eso era falso y costaba rostros de verdad.** Medido:

- En imagenes sin ninguna cara **sinteticas** -- ruido, degradados, damero --
  la confianza maxima de YuNet es 0.182. Pero un **fondo real** si tiene
  estructuras que puntuan mas: en una foto de dos personas aparecio un rostro
  inexistente entre ellas con **0.617**. Medir solo con imagenes sinteticas
  daba una falsa sensacion de margen.
- Con 0.9, de 120 fotos de LFW -- recortadas, frontales, bien iluminadas --
  **una no detectaba nada**. Con 0.8 o menos, ninguna se pierde.
- La confianza cae con el tamano del rostro: 0.93 con la cara a 150 px, 0.90
  a 50 px y 0.79 a 35 px. En una foto cualquiera un rostro mide 60-150 px, y
  con 0.9 se descartaban casi todos.

0.7 deja fuera ese falso de 0.617 y conserva los rostros chicos de verdad,
que puntuan 0.785 incluso a 35 px. Se probo primero con 0.6 y aparecia el
falso; 0.7 es el valor que separa los dos casos observados.

Ojo: que un rostro se detecte no quiere decir que su vector sirva. Ver
``LADO_FIABLE``.
"""

LADO_MAXIMO_DETECCION = 1280
"""Lado mayor con el que se le pasa la imagen a YuNet.

**YuNet se degrada y despues falla con imagenes grandes.** Medido con la misma
cara ocupando toda la imagen: 0.909 de confianza a 1000 px, 0.865 a 1200,
0.762 a 1400, 0.701 a 1600 y **ninguna deteccion desde 1800**. Una foto de
celular ronda los 3000-4000 px, asi que sin esto el caso mas comun -- subir
una foto recien tomada -- devolvia "no hay ningun rostro".

La imagen se reduce solo para detectar; las coordenadas vuelven a la original
y el recorte se hace a resolucion completa, que es lo que ArcFace necesita.
"""

LADO_FIABLE = 80
"""Lado minimo, en pixeles, para fiarse del vector de un rostro.

El descriptor se degrada con la resolucion aunque la deteccion acierte.
Medido contra la misma foto a distintos tamanos, la distancia a su propio
vector de referencia crece asi: 0.05 a 150 px, 0.06 a 100 px, 0.15 a 70 px,
0.27 a 50 px y 0.50 a 35 px -- esto ultimo ya roza el umbral de #303.

No se descarta el rostro: se marca, para que la interfaz pueda avisar que el
resultado es poco fiable en vez de dar un nombre dudoso como si fuera seguro.
"""

LADO_ALINEADO = 112
"""Lado del recorte alineado, en pixeles. Es el tamano de entrada de ArcFace."""

PLANTILLA = np.array(
    [
        [38.2946, 51.6963],  # ojo derecho de la persona (izquierda de la imagen)
        [73.5318, 51.5014],  # ojo izquierdo
        [56.0252, 71.7366],  # nariz
        [41.5493, 92.3655],  # comisura derecha
        [70.7299, 92.2041],  # comisura izquierda
    ],
    dtype=np.float32,
)
"""Donde tienen que caer los cinco landmarks en el recorte de 112x112.

Es la plantilla publicada de ArcFace (`arcface_src` de insightface). No se
inventa: el recorte tiene que caer exactamente donde el modelo fue entrenado
a esperarlo, y unos pixeles de corrimiento empeoran el vector sin que nada
avise.

Comprobado: alinear con esta plantilla da un recorte identico pixel a pixel
al que devolvia ``FaceRecognizerSF.alignCrop``, que usaba la misma.
"""


@dataclass(frozen=True, slots=True)
class RostroDetectado:
    """Un rostro encontrado en una imagen, con lo que hace falta para alinearlo."""

    caja: tuple[int, int, int, int]  # x, y, ancho, alto
    confianza: float
    landmarks: tuple[tuple[int, int], ...]  # ojo der, ojo izq, nariz, boca der, boca izq

    # La fila cruda de 15 numeros que devolvio YuNet. `alignCrop` la quiere
    # tal cual: convertirla a tuplas y volver a armarla solo agrega una
    # oportunidad de equivocarse en el orden de los puntos.
    fila: np.ndarray = field(repr=False)

    @property
    def lado(self) -> int:
        """El lado menor de la caja: cuanta cara hay de verdad."""

        return min(self.caja[2], self.caja[3])

    @property
    def fiable(self) -> bool:
        """Si el rostro es lo bastante grande para fiarse de su vector.

        Un rostro chico se detecta bien pero se describe mal, asi que la
        respuesta puede traer un nombre con poco fundamento. Quien consulte
        esto puede avisarlo en vez de presentarlo como seguro.
        """

        return self.lado >= LADO_FIABLE


def crear_detector(
    umbral: float = UMBRAL_CONFIANZA,
    carpeta: Path = MODELOS,
) -> cv2.FaceDetectorYN:
    """Carga YuNet. El tamano de entrada se fija despues, por imagen."""

    return cv2.FaceDetectorYN.create(str(traer(YUNET, carpeta)), "", (320, 320), umbral)


def detectar(
    imagen: np.ndarray,
    detector: cv2.FaceDetectorYN,
    *,
    ruta: Path | None = None,
) -> tuple[RostroDetectado, ...]:
    """Devuelve todos los rostros de la imagen, el mas confiable primero.

    Una imagen sin rostros lanza ``EmptyObjectError``, no un error de archivo:
    una foto de un paisaje es valida y vacia, y quien carga el dataset la
    anota y sigue.
    """

    alto, ancho = imagen.shape[:2]
    mayor = max(alto, ancho)
    if mayor > LADO_MAXIMO_DETECCION:
        escala = LADO_MAXIMO_DETECCION / mayor
        chica = cv2.resize(
            imagen,
            (max(1, round(ancho * escala)), max(1, round(alto * escala))),
            interpolation=cv2.INTER_AREA,
        )
    else:
        escala, chica = 1.0, imagen

    detector.setInputSize((chica.shape[1], chica.shape[0]))
    _, caras = detector.detect(chica)
    if caras is None or len(caras) == 0:
        nombre = ruta.name if ruta is not None else "la imagen"
        raise EmptyObjectError(f"no hay ningun rostro en {nombre}", ruta)

    if escala != 1.0:
        # Las coordenadas vuelven a la imagen original para que el recorte se
        # haga a resolucion completa; la confianza (indice 14) no se escala.
        caras = caras.copy()
        caras[:, :14] /= escala

    rostros = []
    for fila in caras:
        x, y, w, h = (int(v) for v in fila[:4])
        puntos = tuple((int(fila[4 + 2 * i]), int(fila[5 + 2 * i])) for i in range(5))
        rostros.append(
            RostroDetectado(
                caja=(x, y, w, h),
                confianza=float(fila[14]),
                landmarks=puntos,
                fila=fila,
            )
        )
    return tuple(sorted(rostros, key=lambda r: r.confianza, reverse=True))


ORIENTACIONES = (0, 45, 90, 135, 180, 225, 270, 315)
"""Giros con los que se le prueba la imagen a YuNet, en grados.

**YuNet solo acierta los landmarks con la cara mas o menos derecha**, y el
alineado de ``alinear`` depende por completo de esos puntos. Medido sobre 25
identidades de LFW giradas de 15 en 15 grados, la vuelta entera: con una sola
pasada se reconoce el 23% de los casos, y desde los 60 grados el 0%.

Lo insidioso es que no avisa. A 60 grados YuNet sigue devolviendo 0.87 de
confianza: se cree una cara y entrega landmarks cruzados, asi que el vector
sale inservible pero marcado como fiable. La confianza dice "hay algo con
forma de cara", no "los puntos estan bien puestos".

``alinear`` ya absorbe por su cuenta hasta unos 50 grados de giro. Probando
estas ocho orientaciones la cara nunca queda a mas de 22.5 grados de la
vertical, holgadamente dentro de ese margen: el reconocimiento sube al 98% y
la deteccion al 100% en todos los angulos. Con solo cuatro -- los multiplos
de 90 -- se queda en el 84%, porque las diagonales caen justo en el peor
caso, a 45 grados de todas.
"""

LADO_SONDEO = 320
"""Lado mayor con el que se sondea cada orientacion, en pixeles.

Probar las ocho orientaciones a resolucion entera costaba 13 a 15 veces mas
que una sola pasada: 306 ms en un cuadro de camara de 640x480 y 1 705 ms en
una foto de celular. La camara pide 3 cuadros por segundo, asi que se encolaba
y se congelaba.

Para **elegir** la orientacion no hace falta la imagen entera: solo saber en
cual la cara se lee derecha, y eso ya se ve en una miniatura. Se sondea a 320
px, se gira la imagen completa una sola vez a la orientacion ganadora y se
detecta ahi a resolucion entera, que es lo que necesita el recorte.

Si la miniatura no encuentra nada -- una cara pequena dentro de una foto
grande se pierde al reducir -- se cae al barrido completo, que es lento pero
no deja de funcionar.
"""

_ROTACIONES_EXACTAS = {
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


def girar(imagen: np.ndarray, grados: int) -> np.ndarray:
    """Gira la imagen entera, sin recortar ninguna esquina.

    Los multiplos de 90 van por ``cv2.rotate``, que reordena pixeles sin
    interpolar: no pierde nada. Los demas angulos se dibujan sobre un lienzo
    cuadrado del tamano de la diagonal, porque al girar en diagonal las
    esquinas se salen del encuadre original y ahi puede estar la cara.
    """

    grados %= 360
    if grados == 0:
        return imagen
    if grados in _ROTACIONES_EXACTAS:
        return cv2.rotate(imagen, _ROTACIONES_EXACTAS[grados])

    alto, ancho = imagen.shape[:2]
    diagonal = int(np.ceil(np.hypot(alto, ancho)))
    lienzo = np.zeros((diagonal, diagonal, *imagen.shape[2:]), imagen.dtype)
    arriba, izquierda = (diagonal - alto) // 2, (diagonal - ancho) // 2
    lienzo[arriba : arriba + alto, izquierda : izquierda + ancho] = imagen
    matriz = cv2.getRotationMatrix2D((diagonal / 2, diagonal / 2), grados, 1.0)
    return cv2.warpAffine(lienzo, matriz, (diagonal, diagonal))


def puntos_al_original(
    puntos: np.ndarray,
    forma_original: tuple[int, int],
    grados: int,
) -> np.ndarray:
    """Lleva puntos de la imagen girada de vuelta al sistema de la original.

    Hace falta porque quien consulta dibuja el recuadro sobre **su** imagen,
    que no esta girada. Sin esto el rostro se detectaria bien y el recuadro
    apareceria en otro sitio.

    Las coordenadas son de pixel, asi que el ultimo pixel de un lado de ``n``
    es ``n - 1``: de ahi los ``- 1`` al espejar.
    """

    grados %= 360
    alto, ancho = forma_original
    xs, ys = puntos[:, 0], puntos[:, 1]
    if grados == 0:
        return puntos.copy()
    if grados == 90:  # giro horario: (x, y) -> (alto - 1 - y, x)
        return np.stack([ys, (alto - 1) - xs], axis=1)
    if grados == 180:
        return np.stack([(ancho - 1) - xs, (alto - 1) - ys], axis=1)
    if grados == 270:  # giro antihorario: (x, y) -> (y, ancho - 1 - x)
        return np.stack([(ancho - 1) - ys, xs], axis=1)

    # Diagonales: se deshace la afin del lienzo y se resta el centrado.
    diagonal = int(np.ceil(np.hypot(alto, ancho)))
    matriz = cv2.getRotationMatrix2D((diagonal / 2, diagonal / 2), grados, 1.0)
    inversa = cv2.invertAffineTransform(matriz)
    homogeneos = np.hstack([puntos, np.ones((len(puntos), 1), dtype=puntos.dtype)])
    en_lienzo = homogeneos @ inversa.T
    return en_lienzo - [(diagonal - ancho) // 2, (diagonal - alto) // 2]


def caja_al_original(
    caja: tuple[int, int, int, int],
    forma_original: tuple[int, int],
    grados: int,
) -> tuple[int, int, int, int]:
    """La caja envolvente de una caja girada, en coordenadas de la original.

    Al girar en diagonal un rectangulo deja de ser paralelo a los ejes, asi
    que se devuelve el rectangulo recto que lo contiene. Queda algo mas grande
    que el rostro, que es el error correcto de cometer: recortar el recuadro
    dejaria parte de la cara fuera.
    """

    x, y, ancho_caja, alto_caja = caja
    esquinas = np.array(
        [
            [x, y],
            [x + ancho_caja, y],
            [x + ancho_caja, y + alto_caja],
            [x, y + alto_caja],
        ],
        dtype=np.float64,
    )
    vueltas = puntos_al_original(esquinas, forma_original, grados)
    alto, ancho = forma_original
    x0 = int(np.clip(np.floor(vueltas[:, 0].min()), 0, ancho - 1))
    y0 = int(np.clip(np.floor(vueltas[:, 1].min()), 0, alto - 1))
    x1 = int(np.clip(np.ceil(vueltas[:, 0].max()), 0, ancho))
    y1 = int(np.clip(np.ceil(vueltas[:, 1].max()), 0, alto))
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


@dataclass(frozen=True, slots=True)
class DeteccionOrientada:
    """Lo que hace falta para alinear y para dibujar, tras probar giros.

    ``imagen`` y ``rostros`` van juntos a proposito: las coordenadas de un
    ``RostroDetectado`` son de la imagen en la que se detecto, asi que a
    ``alinear`` hay que pasarle ``imagen`` y no la original. Para dibujar
    sobre la original esta ``cajas_originales``.
    """

    imagen: np.ndarray = field(repr=False)
    rostros: tuple[RostroDetectado, ...]
    grados: int
    forma_original: tuple[int, int]

    @property
    def cajas_originales(self) -> tuple[tuple[int, int, int, int], ...]:
        """Las cajas de ``rostros``, en coordenadas de la imagen sin girar."""

        return tuple(
            caja_al_original(rostro.caja, self.forma_original, self.grados)
            for rostro in self.rostros
        )

    @property
    def sujeto(self) -> RostroDetectado:
        """A quien retrata la foto, cuando sale mas de una cara.

        **El mas centrado, no el mas confiable.** Quien hace una foto encuadra
        a quien retrata; el detector, en cambio, puede ver mejor a alguien del
        fondo, y entonces se guarda a esa persona bajo el nombre equivocado.
        Eso no solo pierde aciertos: deja un vector ajeno con el nombre del
        sujeto, listo para identificar a un extrano como si fuera el.

        Medido sobre 400 identidades de LFW, dos fotos cada una, donde **133
        traen mas de una cara** en alguna de las dos:

        | regla elegida | no se reconocen |
        |---|---|
        | mas confiable | 15.0% |
        | mas grande | 10.0% |
        | **mas centrada** | **9.2%** |

        La mediana apenas se mueve (0.411 a 0.403): quien ya se reconocia bien
        sigue igual y el cambio esta todo en la cola. Las seis identidades mas
        dificiles de la muestra estaban **mas lejos de si mismas que de un
        desconocido** -- distancia propia sobre 1.0 contra 0.79 de un extrano
        tipico -- y con esta regla caen a 0.32-0.60.

        No es una ley universal: vale porque el sujeto suele estar encuadrado,
        y en LFW ademas por construccion. Una foto de grupo sin sujeto claro
        no la resuelve nadie desde la imagen sola.
        """

        alto, ancho = self.imagen.shape[:2]
        cx, cy = ancho / 2.0, alto / 2.0

        def lejania(rostro: RostroDetectado) -> float:
            x, y, lado_x, lado_y = rostro.caja
            return float(np.hypot(x + lado_x / 2 - cx, y + lado_y / 2 - cy))

        return min(self.rostros, key=lejania)


def _sondear_orientacion(imagen: np.ndarray, detector: cv2.FaceDetectorYN) -> int | None:
    """En que giro se lee mejor la cara, decidido sobre una miniatura.

    Devuelve los grados ganadores, o ``None`` si reducida no se ve ninguna
    cara y hay que mirar a resolucion entera.
    """

    alto, ancho = imagen.shape[:2]
    mayor = max(alto, ancho)
    if mayor > LADO_SONDEO:
        factor = LADO_SONDEO / mayor
        miniatura = cv2.resize(
            imagen,
            (max(1, round(ancho * factor)), max(1, round(alto * factor))),
            interpolation=cv2.INTER_AREA,
        )
    else:
        miniatura = imagen

    mejor_grados, mejor_confianza = None, -1.0
    for grados in ORIENTACIONES:
        try:
            rostros = detectar(girar(miniatura, grados), detector)
        except EmptyObjectError:
            continue
        if rostros[0].confianza > mejor_confianza:
            mejor_confianza = rostros[0].confianza
            mejor_grados = grados
    return mejor_grados


def detectar_orientado(
    imagen: np.ndarray,
    detector: cv2.FaceDetectorYN,
    *,
    ruta: Path | None = None,
) -> DeteccionOrientada:
    """Encuentra los rostros aunque la imagen venga girada.

    Prueba las ``ORIENTACIONES`` y se queda con la vuelta en la que YuNet da
    el rostro mas confiable.

    Con varias caras, la orientacion la decide la mas confiable y se devuelven
    todas las de esa vuelta. Dos personas giradas entre si quedan fuera de lo
    que esto resuelve, y no es el caso que se busca cubrir: una foto llega
    girada entera, no por mitades.
    """

    forma_original = (imagen.shape[0], imagen.shape[1])
    grados = _sondear_orientacion(imagen, detector)

    if grados is not None:
        candidata = girar(imagen, grados)
        try:
            rostros = detectar(candidata, detector, ruta=ruta)
        except EmptyObjectError:
            grados = None  # la miniatura acerto una cara que a tamano real no esta
        else:
            return DeteccionOrientada(candidata, rostros, grados, forma_original)

    # Sin sondeo util: se prueban todas a resolucion entera. Es lento, pero
    # solo se paga cuando la cara es demasiado pequena para verse reducida.
    mejor = None
    mejor_confianza = -1.0
    for grados in ORIENTACIONES:
        candidata = girar(imagen, grados)
        try:
            rostros = detectar(candidata, detector, ruta=ruta)
        except EmptyObjectError:
            continue
        # `detectar` ya los devuelve de mas a menos confiable.
        if rostros[0].confianza > mejor_confianza:
            mejor_confianza = rostros[0].confianza
            mejor = DeteccionOrientada(candidata, rostros, grados, forma_original)

    if mejor is None:
        nombre = ruta.name if ruta is not None else "la imagen"
        raise EmptyObjectError(f"no hay ningun rostro en {nombre}", ruta)
    return mejor


def alinear(imagen: np.ndarray, rostro: RostroDetectado) -> np.ndarray:
    """Devuelve el recorte alineado de 112x112 en BGR.

    Lleva los cinco landmarks a su sitio en la plantilla con una transformada
    de semejanza -- rotacion, escala y traslacion, sin deformar la cara -- que
    es lo que la lamina de la clase 10 llama *Geometric Alignment*.
    """

    # Los landmarks crudos y no `rostro.landmarks`, que vienen redondeados a
    # entero para mostrarlos: la precision sub-pixel mueve el recorte lo
    # suficiente para que el vector cambie.
    puntos = np.asarray(rostro.fila[4:14], dtype=np.float32).reshape(5, 2)
    matriz, _ = cv2.estimateAffinePartial2D(puntos, PLANTILLA, method=cv2.LMEDS)
    if matriz is None:
        raise EmptyObjectError("no se pudo alinear el rostro con sus landmarks")
    return cv2.warpAffine(imagen, matriz, (LADO_ALINEADO, LADO_ALINEADO), borderValue=0.0)


def rostros_de(ruta: str | Path, detector: cv2.FaceDetectorYN) -> tuple[np.ndarray, ...]:
    """Lee la imagen y devuelve un recorte alineado por cada rostro."""

    archivo = Path(ruta)
    hallazgo = detectar_orientado(leer_color(archivo), detector, ruta=archivo)
    return tuple(alinear(hallazgo.imagen, rostro) for rostro in hallazgo.rostros)


def main(argv: list[str] | None = None) -> int:
    """Dice cuantos rostros hay en cada imagen y, si se pide, guarda los recortes.

    Una imagen sin rostros se anota y no detiene a las demas, igual que hara
    la carga del dataset en #302: devuelve 1 al final para que se note, pero
    recorre todo.
    """

    import argparse

    from app.rostros.errores import FeatureExtractionError

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("imagenes", nargs="+", type=Path)
    parser.add_argument("--umbral", type=float, default=UMBRAL_CONFIANZA)
    parser.add_argument("--guardar", type=Path, help="carpeta donde dejar los recortes alineados")
    args = parser.parse_args(argv)

    detector = crear_detector(args.umbral)
    if args.guardar is not None:
        args.guardar.mkdir(parents=True, exist_ok=True)

    problemas = 0
    for ruta in args.imagenes:
        try:
            hallazgo = detectar_orientado(leer_color(ruta), detector, ruta=ruta)
        except FeatureExtractionError as error:
            print(f"  {error}", file=sys.stderr)
            problemas += 1
            continue

        rostros = hallazgo.rostros
        confianzas = ", ".join(f"{r.confianza:.2f}" for r in rostros)
        girada = "" if hallazgo.grados == 0 else f", leida a {hallazgo.grados} grados"
        print(f"{ruta.name}: {len(rostros)} rostro(s) [{confianzas}]{girada}")
        if args.guardar is None:
            continue
        for numero, rostro in enumerate(rostros, start=1):
            destino = args.guardar / f"{ruta.stem}_{numero:02d}.png"
            destino.write_bytes(cv2.imencode(".png", alinear(hallazgo.imagen, rostro))[1])
            print(f"  -> {destino}")

    return 1 if problemas else 0


__all__ = [
    "LADO_ALINEADO",
    "LADO_FIABLE",
    "LADO_MAXIMO_DETECCION",
    "ORIENTACIONES",
    "PLANTILLA",
    "UMBRAL_CONFIANZA",
    "DeteccionOrientada",
    "RostroDetectado",
    "alinear",
    "caja_al_original",
    "crear_detector",
    "detectar",
    "detectar_orientado",
    "girar",
    "main",
    "puntos_al_original",
    "rostros_de",
]


if __name__ == "__main__":
    sys.exit(main())
