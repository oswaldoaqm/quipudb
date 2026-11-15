"""Lectura acotada en memoria: no usar UploadFile, que vuelca a temporales."""

from fastapi import HTTPException, Request
from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header

MAX_IMAGEN = 8 * 1024**2
TIPOS = {b"image/jpeg", b"image/png", b"image/webp"}


async def leer_imagen(request: Request, max_bytes: int = MAX_IMAGEN) -> bytes:
    tipo, opciones = parse_options_header(request.headers.get("content-type", ""))
    multipart = tipo == b"multipart/form-data"
    if not multipart and tipo not in TIPOS:
        raise HTTPException(415, "usa una imagen JPEG, PNG o WebP")
    limite = max_bytes + (64 * 1024 if multipart else 0)
    longitud = request.headers.get("content-length")
    if longitud:
        try:
            declarada = int(longitud)
        except ValueError:
            raise HTTPException(400, "Content-Length invalido") from None
        if declarada < 0:
            raise HTTPException(400, "Content-Length invalido")
        if declarada > limite:
            raise HTTPException(413, "la imagen supera el limite de 8 MiB")

    imagen = bytearray()
    if not multipart:
        async for trozo in request.stream():
            imagen.extend(trozo)
            if len(imagen) > max_bytes:
                raise HTTPException(413, "la imagen supera el limite de 8 MiB")
    else:
        boundary = opciones.get(b"boundary")
        if not boundary or len(boundary) > 200:
            raise HTTPException(400, "multipart sin boundary valido")
        headers: dict[bytes, bytes] = {}
        campo, valor = bytearray(), bytearray()
        partes = 0
        terminado = False

        def inicio() -> None:
            nonlocal partes
            partes += 1
            if partes > 1:
                raise HTTPException(422, "envia una sola imagen en el campo imagen")

        def header_field(datos: bytes, start: int, end: int) -> None:
            campo.extend(datos[start:end])
            if len(campo) > 1024:
                raise HTTPException(400, "cabecera multipart demasiado larga")

        def header_value(datos: bytes, start: int, end: int) -> None:
            valor.extend(datos[start:end])
            if len(valor) > 4096:
                raise HTTPException(400, "cabecera multipart demasiado larga")

        def header_end() -> None:
            headers[bytes(campo).lower()] = bytes(valor)
            campo.clear()
            valor.clear()

        def headers_end() -> None:
            _, opciones = parse_options_header(headers.get(b"content-disposition", b""))
            if opciones.get(b"name") != b"imagen" or b"filename" not in opciones:
                raise HTTPException(422, "falta el archivo en el campo imagen")
            tipo_archivo, _ = parse_options_header(headers.get(b"content-type", b""))
            if tipo_archivo not in TIPOS:
                raise HTTPException(415, "usa una imagen JPEG, PNG o WebP")

        def datos(datos: bytes, start: int, end: int) -> None:
            if len(imagen) + end - start > max_bytes:
                raise HTTPException(413, "la imagen supera el limite de 8 MiB")
            imagen.extend(datos[start:end])

        def fin() -> None:
            nonlocal terminado
            terminado = True

        parser = MultipartParser(
            boundary,
            {
                "on_part_begin": inicio,
                "on_header_field": header_field,
                "on_header_value": header_value,
                "on_header_end": header_end,
                "on_headers_finished": headers_end,
                "on_part_data": datos,
                "on_end": fin,
            },
        )
        recibidos = 0
        try:
            async for trozo in request.stream():
                recibidos += len(trozo)
                if recibidos > limite:
                    raise HTTPException(413, "la peticion supera el limite de 8 MiB")
                parser.write(trozo)
            parser.finalize()
        except MultipartParseError:
            raise HTTPException(400, "multipart invalido") from None
        if partes != 1 or not terminado:
            raise HTTPException(422, "falta una imagen completa en el campo imagen")
    if not imagen:
        raise HTTPException(422, "la imagen esta vacia")
    return bytes(imagen)
