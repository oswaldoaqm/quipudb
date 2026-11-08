"""Cliente del API REST de QuipuDB (#243).

Es la unica puerta de la aplicacion hacia el motor: nadie mas arma
peticiones HTTP ni escapa literales a mano. Habla solo con el contrato
publico de ``engine/api`` (ADR 0010): ``POST /query``, ``GET /tables`` y
``POST /tables/{tabla}/load``.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Self

import httpx

from app.config import Settings


class MotorError(Exception):
    """El motor rechazo la sentencia. Conserva el mensaje y la ubicacion originales."""

    def __init__(
        self,
        mensaje: str,
        *,
        kind: str | None = None,
        line: int | None = None,
        column: int | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.kind = kind
        self.line = line
        self.column = column
        self.status = status


class MotorNoDisponible(MotorError):
    """No hubo respuesta: el motor esta caido, la URL esta mal o tardo demasiado."""


@dataclass(frozen=True, slots=True)
class Resultado:
    columns: tuple[str, ...]
    column_types: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    affected_rows: int = 0
    plan: dict[str, Any] | None = None

    def dicts(self) -> list[dict[str, Any]]:
        """Las filas como diccionarios ``columna -> valor``."""
        return [dict(zip(self.columns, fila, strict=True)) for fila in self.rows]


@dataclass(frozen=True, slots=True)
class Carga:
    table: str
    inserted: int
    failed: int
    errors: tuple[tuple[int, str], ...] = field(default=())
    """``(linea del CSV, mensaje)`` de las primeras filas que no entraron."""
    errors_truncated: bool = False


def usa_tls(url: str) -> bool:
    """Si hay que cargar los certificados para hablar con ``url``.

    El motor suele estar en ``http://127.0.0.1``: sin TLS no hay nada que
    verificar, y cargar los certificados de certifi tarda segundos en algunas
    maquinas Windows. Con ``https`` se verifica siempre.
    """

    return url.lower().startswith("https://")


def literal(texto: str) -> str:
    """Un literal de texto SQL que el lexer del motor lee tal cual.

    El API no tiene parametros preparados: la pregunta del usuario termina
    dentro de ``MATCH(texto, '...')``. El lexer solo reconoce una cosa dentro
    de un literal, la comilla duplicada (``_scan_string`` en
    ``engine/parser/lexer.py``); ``;``, ``--`` y los saltos de linea ya no
    significan nada ahi. El caracter nulo se quita: no cabe en un VARCHAR.
    """

    return "'" + texto.replace("\x00", "").replace("'", "''") + "'"


def literal_vector(vector: Sequence[float]) -> str:
    """``VECTOR(...)`` en decimal fijo: el lexer no lee notacion cientifica.

    Los embeddings estan normalizados (cada componente entre -1 y 1), asi que
    7 decimales conservan la precision de un float32.
    """

    return "VECTOR(" + ", ".join(f"{x:.7f}" for x in vector) + ")"


class ClienteMotor:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        """``http`` es un cliente ya armado, como el ``TestClient`` de FastAPI
        con el que las pruebas corren el motor dentro del mismo proceso."""

        self.base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(
            base_url=self.base_url,
            timeout=timeout_s,
            transport=transport,
            verify=usa_tls(self.base_url),
        )

    @classmethod
    def desde(cls, settings: Settings, transport: httpx.BaseTransport | None = None) -> Self:
        return cls(
            settings.quipudb_api_url, timeout_s=settings.quipudb_timeout_s, transport=transport
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _enviar(self, metodo: str, ruta: str, **kwargs: Any) -> Any:
        try:
            respuesta = self._http.request(metodo, ruta, **kwargs)
        except httpx.TimeoutException as error:
            raise MotorNoDisponible(
                f"el motor en {self.base_url} no respondio a tiempo ({type(error).__name__})"
            ) from error
        except httpx.TransportError as error:
            raise MotorNoDisponible(
                f"no se pudo contactar al motor en {self.base_url}: {error}"
            ) from error
        try:
            cuerpo = respuesta.json()
        except ValueError:
            cuerpo = None
        if respuesta.is_success:
            return cuerpo
        if isinstance(cuerpo, dict) and "error" in cuerpo:
            raise MotorError(
                str(cuerpo["error"]),
                kind=cuerpo.get("kind"),
                line=cuerpo.get("line"),
                column=cuerpo.get("column"),
                status=respuesta.status_code,
            )
        raise MotorError(
            f"el motor respondio {respuesta.status_code}: {respuesta.text[:200]}",
            status=respuesta.status_code,
        )

    def query(self, sql: str) -> Resultado:
        """Ejecuta una sentencia, o un lote separado por ``;``."""

        cuerpo = self._enviar("POST", "/query", json={"sql": sql})
        return Resultado(
            columns=tuple(cuerpo["columns"]),
            column_types=tuple(cuerpo["column_types"]),
            rows=tuple(tuple(fila) for fila in cuerpo["rows"]),
            affected_rows=int(cuerpo.get("affected_rows", 0)),
            plan=cuerpo.get("plan"),
        )

    def tables(self) -> list[dict[str, Any]]:
        return list(self._enviar("GET", "/tables"))

    def table_names(self) -> set[str]:
        return {tabla["name"] for tabla in self.tables()}

    def load_csv(
        self,
        tabla: str,
        columnas: Sequence[str],
        filas: Iterable[Sequence[Any]],
        *,
        atomic: bool = False,
    ) -> Carga:
        """Carga masiva por ``POST /tables/{tabla}/load`` (#114).

        Mucho mas rapido que un INSERT por fila: el motor reconstruye el indice
        de texto una sola vez despues. Con ``atomic``, si una fila falla no
        queda ninguna.
        """

        buffer = io.StringIO(newline="")
        escritor = csv.writer(buffer, lineterminator="\n")
        escritor.writerow(columnas)
        escritor.writerows(filas)
        archivo = buffer.getvalue().encode("utf-8")
        cuerpo = self._enviar(
            "POST",
            f"/tables/{tabla}/load",
            params={"atomic": str(atomic).lower()},
            files={"file": (f"{tabla}.csv", archivo, "text/csv")},
        )
        return Carga(
            table=cuerpo["table"],
            inserted=int(cuerpo["inserted"]),
            failed=int(cuerpo["failed"]),
            errors=tuple((int(e["line"]), str(e["error"])) for e in cuerpo["errors"]),
            errors_truncated=bool(cuerpo["errors_truncated"]),
        )
