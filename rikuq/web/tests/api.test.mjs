import assert from "node:assert/strict";
import test from "node:test";
import { ApiError, fotoUrl, health, identificar, MAX_IMAGEN, validarImagen } from "../src/api.ts";

test("health comprueba que responde Rikuq y no otro servidor", async () => {
  assert.equal(await health("/api", async url => {
    assert.equal(url, "/api/health");
    return Response.json({ status: "ok", app: "rikuq" });
  }), true);
  assert.equal(await health("/api", async () => Response.json({ status: "ok" })), false);
});

test("health distingue un error HTTP de una respuesta valida", async () => {
  await assert.rejects(health("/api", async () => new Response(null, { status: 503 })), /no responde/);
});

test("valida formato, vacios y limite antes de enviar", () => {
  assert.equal(validarImagen({ size: MAX_IMAGEN, type: "image/jpeg" }), null);
  assert.match(validarImagen({ size: MAX_IMAGEN + 1, type: "image/jpeg" }), /8 MiB/);
  assert.match(validarImagen({ size: 0, type: "image/png" }), /vacía/);
  assert.match(validarImagen({ size: 10, type: "image/svg+xml" }), /JPEG/);
});

test("identificar solo envia la imagen al servidor de Rikuq", async () => {
  const signal = new AbortController().signal;
  const result = await identificar(new Blob(["foto"], { type: "image/jpeg" }), signal, "/api", async (url, options) => {
    assert.equal(url, "/api/identificar");
    assert.equal(options.method, "POST");
    assert.equal(options.signal, signal);
    assert.equal(await options.body.get("imagen").text(), "foto");
    return Response.json({ ancho: 640, alto: 480, rostros: [], persona: null });
  });
  assert.equal(result.persona, null);
});

test("sin rostro conserva el codigo para mostrar un estado distinto", async () => {
  await assert.rejects(identificar(new Blob(["foto"], { type: "image/jpeg" }), new AbortController().signal, "/api",
    async () => Response.json({ error: "No hay rostro", codigo: "sin_rostro" }, { status: 422 })), e => e instanceof ApiError && e.codigo === "sin_rostro");
});

test("no acepta fotos de referencia de terceros", () => {
  assert.equal(fotoUrl("/personas/7/foto", "/api"), "/api/personas/7/foto");
  assert.equal(fotoUrl("https://tercero.invalid/foto.jpg"), "");
  assert.equal(fotoUrl("/personas/../foto"), "");
});
