import assert from "node:assert/strict";
import test from "node:test";
import { apagarStream, BucleCamara, errorCamara, Estabilizador, INTERVALO_MS } from "../src/camara.ts";

const flush = () => new Promise(resolve => setImmediate(resolve));

function entorno(tarea) {
  let ahora = 0;
  const pendientes = new Map();
  let id = 0;
  const vistos = [], errores = [];
  const loop = new BucleCamara(tarea, x => vistos.push(x), e => errores.push(e), () => ahora,
    (fn, ms) => { pendientes.set(++id, { fn, ms }); return id; }, x => pendientes.delete(x));
  async function siguiente() {
    const [key, { fn, ms }] = pendientes.entries().next().value;
    pendientes.delete(key); ahora += ms; fn(); await flush(); return ms;
  }
  return { loop, vistos, errores, pendientes, siguiente };
}

test("la cadencia permite como maximo 3 inicios en un segundo", async () => {
  assert.equal(INTERVALO_MS, 334);
  let peticiones = 0;
  const e = entorno(async () => ++peticiones);
  e.loop.iniciar(); await flush();
  assert.equal(await e.siguiente(), 334);
  assert.equal(await e.siguiente(), 334);
  assert.equal(peticiones, 3);
  e.loop.detener();
  assert.equal(e.pendientes.size, 0);
});

test("no hay dos peticiones simultaneas ni resultados despues de apagar", async () => {
  let finalizar;
  let peticiones = 0;
  let signal;
  const e = entorno(s => { signal = s; peticiones++; return new Promise(resolve => { finalizar = resolve; }); });
  e.loop.iniciar(); e.loop.iniciar();
  assert.equal(peticiones, 1);
  assert.equal(e.pendientes.size, 0);
  e.loop.detener(); assert.equal(signal.aborted, true);
  finalizar("viejo"); await flush();
  assert.deepEqual(e.vistos, []);
  assert.equal(e.pendientes.size, 0);
});

test("reiniciar mientras una peticion abortada termina no duplica el bucle", async () => {
  let finalizar;
  let peticiones = 0;
  const e = entorno(() => { peticiones++; return new Promise(resolve => { finalizar = resolve; }); });
  e.loop.iniciar(); e.loop.detener(); e.loop.iniciar();
  assert.equal(peticiones, 1);
  finalizar("viejo"); await flush();
  assert.equal(e.pendientes.size, 1);
  await e.siguiente(); assert.equal(peticiones, 2);
  e.loop.detener(); finalizar("nuevo"); await flush();
  assert.deepEqual(e.vistos, []);
});

test("un error no crea una cola y permite el siguiente cuadro", async () => {
  const e = entorno(async () => { throw new Error("sin rostro"); });
  e.loop.iniciar(); await flush();
  assert.equal(e.errores.length, 1); assert.equal(e.pendientes.size, 1);
  await e.siguiente(); assert.equal(e.errores.length, 2);
  e.loop.detener();
});

test("apagar cierra todos los tracks y explica el permiso negado", () => {
  let cerrados = 0;
  apagarStream({ getTracks: () => [{ stop: () => cerrados++ }, { stop: () => cerrados++ }] });
  assert.equal(cerrados, 2);
  assert.match(errorCamara(new DOMException("denegado", "NotAllowedError")), /subida de imagen/);
});

const rostro = (persona_id, caja = [0, 0, 100, 100]) => ({ persona_id, persona: persona_id === null ? null : `Persona ${persona_id}`, caja, foto: null, confianza: .8, distancia: .1, ms_motor: 1 });

test("un cambio de nombre requiere tres cuadros; desconocido nunca hereda nombre", () => {
  const e = new Estabilizador();
  assert.equal(e.aplicar([rostro(1)])[0].persona_id, 1);
  assert.equal(e.aplicar([rostro(2)])[0].persona_id, 1);
  assert.equal(e.aplicar([rostro(2)])[0].persona_id, 1);
  assert.equal(e.aplicar([rostro(2)])[0].persona_id, 2);
  assert.equal(e.aplicar([rostro(null)])[0].persona, null);
});

test("el seguimiento usa cajas, no el orden de las caras", () => {
  const e = new Estabilizador();
  const a = rostro(1), b = rostro(2, [200, 0, 100, 100]);
  e.aplicar([a, b]);
  assert.deepEqual(e.aplicar([b, a]).map(r => r.persona_id), [2, 1]);
  e.limpiar();
  assert.equal(e.aplicar([rostro(3)])[0].persona_id, 3);
});
