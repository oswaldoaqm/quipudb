"""Cliente del API REST de QuipuDB (#302).

Rikuq no importa `engine` ni `quipudb_native`: todo lo que sabe del motor lo
sabe por HTTP, que es lo que exige el enunciado de la Parte 5. Es el mismo
trato que tiene Quipucamayoc (ADR 0010), y por eso este modulo se parece a su
``app/motor.py``: se repite en vez de compartirse porque cada aplicacion es
autocontenida y tiene su propio ``requirements.txt``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Self

import httpx

from app.config import Settings


class MotorError(RuntimeError):
    """El motor respondio algo que no se puede usar."""

    def __init__(self, mensaje: str, *, sql: str | None = None, status: int | None = None) -> None:
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.sql = sql
        self.status = status


class MotorNoDisponible(MotorError):
    """No se pudo llegar al motor: no esta levantado, o tardo demasiado.

    Se distingue de ``MotorError`` porque la salida es distinta: ante un SQL
    mal escrito hay que corregir la consulta, y ante esto hay que levantar el
    servidor. Mezclarlos manda a leer el SQL cuando el problema es que nadie
    atiende en el puerto.
    """


@dataclass(frozen=True, slots=True)
class Resultado:
    """Lo que devuelve una sentencia."""

    columns: tuple[str, ...] = ()
    column_types: tuple[str, ...] = ()
    rows: tuple[tuple[Any, ...], ...] = ()
    affected_rows: int = 0
    plan: Any = field(default=None)

    def dicts(self) -> list[dict[str, Any]]:
        """Las filas por nombre de columna, que es como se leen sin contar."""

        return [dict(zip(self.columns, fila, strict=False)) for fila in self.rows]


def literal(texto: str) -> str:
    """Un literal de texto SQL que el lexer del motor lee tal cual.

    El API no tiene parametros preparados: el nombre de una persona termina
    dentro de un ``INSERT``. El lexer solo reconoce una cosa dentro de un
    literal, la comilla duplicada; ``;``, ``--`` y los saltos de linea ya no
    significan nada ahi. El caracter nulo se quita: no cabe en un VARCHAR.
    """

    return "'" + texto.replace("\x00", "").replace("'", "''") + "'"


def literal_vector(vector: Sequence[float]) -> str:
    """``VECTOR(...)`` en decimal fijo: el lexer no lee notacion cientifica.

    Sin esto, una componente como ``3.4e-05`` saldria en notacion cientifica y
    el motor la rechazaria. Los vectores de #305 estan normalizados (cada
    componente entre -1 y 1), asi que 7 decimales conservan la precision de un
    float32.
    """

    return "VECTOR(" + ", ".join(f"{float(x):.7f}" for x in vector) + ")"


def usa_tls(url: str) -> bool:
    """Si hay que cargar los certificados para hablar con ``url``.

    El motor suele estar en ``http://127.0.0.1``: sin TLS no hay nada que
    verificar, y cargar los certificados tarda segundos en algunas maquinas
    Windows.
    """

    return url.lower().startswith("https://")


class ClienteMotor:
    """Habla con el API del motor. Se usa como contexto para cerrar la conexion."""

    def __init__(
        self,
        url: str,
        timeout: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self._cliente = httpx.Client(
            base_url=self.url,
            timeout=timeout,
            transport=transport,
            verify=usa_tls(self.url),
        )

    @classmethod
    def desde(cls, settings: Settings, transport: httpx.BaseTransport | None = None) -> Self:
        return cls(settings.quipudb_api_url, settings.quipudb_timeout_s, transport)

    def close(self) -> None:
        self._cliente.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _enviar(self, metodo: str, ruta: str, *, sql: str | None = None, **kwargs: Any) -> Any:
        try:
            respuesta = self._cliente.request(metodo, ruta, **kwargs)
        except httpx.TimeoutException as error:
            raise MotorNoDisponible(
                f"el motor no respondio a tiempo en {self.url}: {error}", sql=sql
            ) from error
        except httpx.HTTPError as error:
            raise MotorNoDisponible(
                f"no se pudo llegar al motor en {self.url}: {error}; revisa que este levantado",
                sql=sql,
            ) from error

        if respuesta.status_code >= 400:
            raise MotorError(
                f"el motor respondio {respuesta.status_code}: {_detalle(respuesta)}",
                sql=sql,
                status=respuesta.status_code,
            )
        try:
            return respuesta.json()
        except ValueError as error:
            raise MotorError(
                f"el motor respondio algo que no es JSON: {respuesta.text[:200]!r}",
                sql=sql,
                status=respuesta.status_code,
            ) from error

    def query(self, sql: str) -> Resultado:
        """Ejecuta una sentencia, o un lote separado por ``;``."""

        cuerpo = self._enviar("POST", "/query", sql=sql, json={"sql": sql})
        return Resultado(
            columns=tuple(cuerpo.get("columns", ())),
            column_types=tuple(cuerpo.get("column_types", ())),
            rows=tuple(tuple(fila) for fila in cuerpo.get("rows", ())),
            affected_rows=int(cuerpo.get("affected_rows", 0)),
            plan=cuerpo.get("plan"),
        )

    def tables(self) -> list[dict[str, Any]]:
        return list(self._enviar("GET", "/tables"))

    def upload(self, archivos: Sequence[tuple[str, bytes]], *, nombre: str = "rikuq") -> Any:
        """Carga explicita al motor; NO se usa para fotos de consulta ni camara."""

        if not archivos:
            raise ValueError("la carga necesita al menos un archivo")
        return self._enviar(
            "POST",
            "/media/uploads",
            data={"name": nombre},
            files=[("files", (archivo, datos)) for archivo, datos in archivos],
        )

    def table_names(self) -> set[str]:
        return {str(tabla.get("name")) for tabla in self.tables()}


def _detalle(respuesta: httpx.Response) -> str:
    """El mensaje del motor, que viene en ``detail`` cuando es un error suyo."""

    try:
        cuerpo = respuesta.json()
    except ValueError:
        return respuesta.text[:200]
    if isinstance(cuerpo, dict) and "detail" in cuerpo:
        return str(cuerpo["detail"])[:400]
    return str(cuerpo)[:200]


__all__ = [
    "ClienteMotor",
    "MotorError",
    "MotorNoDisponible",
    "Resultado",
    "literal",
    "literal_vector",
    "usa_tls",
]
