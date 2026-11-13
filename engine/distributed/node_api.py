"""Cuatro rutas internas v1. No monta la API pública que eludiría la pertenencia."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from engine.distributed.node_errors import NodeError
from engine.distributed.node_requests import decode_body, fragment_id_of, request_id_of


def create_node_app(service) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_):
        await run_in_threadpool(service.start)
        try:
            yield
        finally:
            await run_in_threadpool(service.close)

    app = FastAPI(title="QuipuDB nodo interno v1", lifespan=lifespan)
    app.state.service = service

    @app.get("/internal/v1/health")
    def health():
        result = service.health()
        return JSONResponse(
            result.model_dump(mode="json"), status_code=200 if result.status == "ready" else 503
        )

    async def dispatch(request, fragment_id=None, *, scan=False):
        request_id = None
        validated_fragment = None
        try:
            if fragment_id is not None:
                validated_fragment = fragment_id_of(fragment_id)
            if (
                request.headers.get("content-type", "").split(";", 1)[0].lower()
                != "application/json"
            ):
                raise NodeError("INVALID_REQUEST", "se requiere Content-Type application/json")
            data = decode_body(await request.body())
            request_id = request_id_of(data)
            if fragment_id is None:
                result = await run_in_threadpool(service.describe, data)
            else:
                result = await run_in_threadpool(service.execute, fragment_id, data, scan=scan)
            return JSONResponse(result.model_dump(mode="json"))
        except NodeError as error:
            body = error.response(service.catalog, service.node_id, request_id, validated_fragment)
            return JSONResponse(body.model_dump(mode="json"), status_code=error.status)
        except Exception as error:  # noqa: BLE001 -- frontera HTTP conserva el sobre v1
            # No incluir trazas, ni inferir que una desconexión revierte escrituras.
            failure = NodeError("EXECUTION_ERROR", str(error), state="unknown")
            body = failure.response(
                service.catalog, service.node_id, request_id, validated_fragment
            )
            return JSONResponse(body.model_dump(mode="json"), status_code=500)

    @app.post("/internal/v1/catalog")
    async def catalog(request: Request):
        return await dispatch(request)

    @app.post("/internal/v1/fragments/{fragment_id}/execute")
    async def execute(fragment_id: str, request: Request):
        return await dispatch(request, fragment_id)

    @app.post("/internal/v1/fragments/{fragment_id}/scan")
    async def scan(fragment_id: str, request: Request):
        return await dispatch(request, fragment_id, scan=True)

    return app
