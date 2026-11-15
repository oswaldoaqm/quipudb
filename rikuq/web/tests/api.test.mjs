import assert from "node:assert/strict";
import test from "node:test";
import { health } from "../src/api.ts";

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
