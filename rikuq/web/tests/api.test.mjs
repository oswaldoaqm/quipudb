import assert from "node:assert/strict";
import test from "node:test";
import {
  ApiError, eliminarRostro, fotoUrl, health, identificar, listarSubidos, MAX_IMAGEN, registrar, validarImagen, validarNombre,
} from "../src/api.ts";

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

// --- Registrar y Fotos registradas (#342) ---------------------------------------

test("la foto de un rostro registrado tambien es una ruta opaca del API", () => {
  assert.equal(fotoUrl("/rostros/12/foto", "/api"), "/api/rostros/12/foto");
  assert.equal(fotoUrl("/rostros/12/../../foto"), "");
});

test("valida el nombre antes de subir nada", () => {
  assert.equal(validarNombre("Ana Quispe"), null);
  assert.equal(validarNombre("  Ñusta   Huamán "), null);
  assert.equal(validarNombre("O'Brien Jean-Pierre Jr."), null);
  assert.match(validarNombre("   "), /Escribe el nombre/);
  assert.match(validarNombre("Ana2"), /solo letras/);
  assert.match(validarNombre("x".repeat(51)), /50 caracteres/);
});

test("registrar manda la foto, el nombre y el consentimiento", async () => {
  const signal = new AbortController().signal;
  const registro = await registrar(new Blob(["foto"], { type: "image/jpeg" }), " Ana Quispe ", true, signal, "/api", async (url, options) => {
    const direccion = new URL(url, "http://rikuq.local");
    assert.equal(direccion.pathname, "/api/rostros");
    assert.equal(direccion.searchParams.get("nombre"), "Ana Quispe");
    assert.equal(direccion.searchParams.get("consentimiento"), "true");
    assert.equal(options.method, "POST");
    assert.equal(options.signal, signal);
    assert.equal(await options.body.get("imagen").text(), "foto");
    return Response.json({ id: 1, persona_id: 1, persona: "Ana_Quispe", persona_nueva: true, fotos: 1, foto: "/rostros/1/foto" }, { status: 201 });
  });
  assert.equal(registro.persona, "Ana_Quispe");
});

test("registrar no llama al servidor con un nombre invalido", async () => {
  let llamadas = 0;
  await assert.rejects(
    registrar(new Blob(["foto"], { type: "image/jpeg" }), "", true, new AbortController().signal, "/api", async () => { llamadas++; return Response.json({}); }),
    e => e instanceof ApiError && /nombre/.test(e.message),
  );
  assert.equal(llamadas, 0);
});

test("registrar conserva el motivo del servidor", async () => {
  await assert.rejects(
    registrar(new Blob(["foto"], { type: "image/jpeg" }), "Ana", true, new AbortController().signal, "/api",
      async () => Response.json({ codigo: "varios_rostros", error: "la foto tiene 2 rostros" }, { status: 422 })),
    e => e instanceof ApiError && e.codigo === "varios_rostros" && /2 rostros/.test(e.message),
  );
});

test("lista y elimina fotos registradas por su id", async () => {
  const lista = await listarSubidos("/api", async url => {
    assert.equal(url, "/api/rostros/subidos");
    return Response.json([{ id: 3, persona_id: 1, persona: "Ana_Quispe", foto: "/rostros/3/foto" }]);
  });
  assert.equal(lista[0].id, 3);
  await eliminarRostro(3, "/api", async (url, options) => {
    assert.equal(url, "/api/rostros/3");
    assert.equal(options.method, "DELETE");
    return Response.json({ id: 3, persona_eliminada: true });
  });
  await assert.rejects(
    eliminarRostro(1, "/api", async () => Response.json({ codigo: "no_permitido", error: "solo fotos subidas" }, { status: 403 })),
    e => e instanceof ApiError && e.codigo === "no_permitido",
  );
});
