import assert from "node:assert/strict";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

// Renderizar el componente real con Vite: sin navegador, camara ni modelos.
const server = await createServer({
  root: fileURLToPath(new URL("..", import.meta.url)),
  envDir: false,
  server: { middlewareMode: true, hmr: false, ws: false, watch: null },
  appType: "custom",
});
after(() => server.close());
const { Resultado } = await server.ssrLoadModule("/src/Resultado.tsx");

function resultado(cambios = {}) {
  const { umbral = 0.55, ...rostro } = cambios;
  const r = {
    persona: "Persona de prueba", persona_id: 1, foto: null,
    distancia: 0.4363, confianza: (0.55 - 0.4363) / 0.55,
    caja: [0, 0, 10, 10], ms_motor: 1, ...rostro,
  };
  return { ...r, rostros: [r], ancho: 640, alto: 480, ms_total: 10, umbral };
}

const render = datos => renderToStaticMarkup(createElement(Resultado, { resultado: datos }));

test("el renderizado de pruebas no escucha HTTP ni WebSocket", () => {
  assert.equal(server.httpServer, null);
  assert.equal(server.config.server.ws, false);
});

test("el caso 0.4363 explica el 20.7 por ciento de forma visible", () => {
  const html = render(resultado());
  assert.match(html, /Margen al umbral/);
  assert.match(html, /20\.7 %/);
  assert.match(html, /0\.4363/);
  assert.match(html, /0\.5500/);
  assert.match(html, /No es una probabilidad ni un porcentaje de acierto/);
  assert.doesNotMatch(html, /Margen de confianza/);
});

test("muestra el umbral recibido sin fijar el valor en el frontend", () => {
  const html = render(resultado({ umbral: 0.6 }));
  assert.match(html, /0\.6000/);
  assert.doesNotMatch(html, /0\.5500/);
});

test("un servidor anterior sin umbral no hace que se invente uno", () => {
  const datos = resultado();
  delete datos.umbral;
  const html = render(datos);
  assert.match(html, /Umbral de aceptación no informado por el servidor/);
  assert.match(html, /20\.7 %/);
  assert.doesNotMatch(html, /0\.5500/);
});

test("una coincidencia justo en el limite conserva su nombre con margen cero", () => {
  const html = render(resultado({ distancia: 0.55, confianza: 0 }));
  assert.match(html, /Persona de prueba/);
  assert.match(html, /0\.0 %/);
  assert.doesNotMatch(html, /No aplica/);
});

test("distancia cero tiene margen completo, no una probabilidad calibrada", () => {
  const html = render(resultado({ distancia: 0, confianza: 1 }));
  assert.match(html, /100\.0 %/);
  assert.match(html, /No es una probabilidad/);
});

test("desconocido no muestra cero por ciento como si fuera una probabilidad", () => {
  const html = render(resultado({ persona: null, persona_id: null, distancia: 0.9, confianza: 0 }));
  assert.match(html, /Desconocido/);
  assert.match(html, /No aplica/);
  assert.doesNotMatch(html, /0\.0 %/);
});

test("una base sin candidatos distingue ausencia de comparacion", () => {
  const html = render(resultado({ persona: null, persona_id: null, distancia: null, confianza: 0 }));
  assert.match(html, /Sin candidatos para comparar/);
  assert.match(html, /No aplica/);
});
