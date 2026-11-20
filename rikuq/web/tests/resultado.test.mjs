import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

// Renderizar el componente real con Vite: sin navegador, camara ni modelos.
const cache = await mkdtemp(join(tmpdir(), "rikuq-render-"));
let server;
after(async () => {
  await server?.close();
  await rm(cache, { recursive: true, force: true, maxRetries: 3, retryDelay: 50 });
});
server = await createServer({
  root: fileURLToPath(new URL("..", import.meta.url)),
  cacheDir: cache,
  envDir: false,
  server: { middlewareMode: true, hmr: false, ws: false, watch: null },
  appType: "custom",
});
const { Resultado } = await server.ssrLoadModule("/src/Resultado.tsx");
const { default: App } = await server.ssrLoadModule("/src/App.tsx");
const { default: Subida } = await server.ssrLoadModule("/src/Subida.tsx");
const { default: Camara } = await server.ssrLoadModule("/src/Camara.tsx");
const { default: Registro } = await server.ssrLoadModule("/src/Registro.tsx");
const { default: Registradas } = await server.ssrLoadModule("/src/Registradas.tsx");

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
  assert.equal(resolve(server.config.cacheDir), resolve(cache));
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

test("la interfaz empieza sin consentimiento y ofrece pestañas accesibles", () => {
  const html = renderToStaticMarkup(createElement(App));
  assert.match(html, /role="tablist"/);
  assert.match(html, /id="tab-imagen"[^>]*aria-selected="true"/);
  assert.match(html, /id="tab-camara"[^>]*aria-selected="false"/);
  assert.match(html, /role="tabpanel"[^>]*aria-labelledby="tab-imagen"/);
  assert.match(html, /Saltar al reconocimiento/);
  assert.match(html, /type="checkbox"/);
  assert.doesNotMatch(html, /checked=""/);
});

test("la subida explica el permiso y conserva el selector accesible por boton", () => {
  const html = renderToStaticMarkup(createElement(Subida, { permiso: false }));
  assert.match(html, /Confirma el permiso de arriba/);
  assert.match(html, /Elegir imagen/);
  assert.match(html, /aria-label="Seleccionar imagen para identificar"/);
  assert.match(html, /<input[^>]*hidden=""[^>]*type="file"/);
  assert.match(html, /<button[^>]*disabled=""[^>]*>[\s\S]*?Identificar rostro/);
});

test("con permiso pero sin archivo la ayuda pide seleccionar una imagen", () => {
  const html = renderToStaticMarkup(createElement(Subida, { permiso: true }));
  assert.match(html, /Selecciona una imagen para activar el análisis/);
  assert.doesNotMatch(html, /Confirma el permiso de arriba/);
});

test("la camara no arranca automaticamente y explica el permiso pendiente", () => {
  const html = renderToStaticMarkup(createElement(Camara, { permiso: false }));
  assert.match(html, /Tu cámara está apagada/);
  assert.match(html, /Confirma el permiso de arriba para encender la cámara/);
  assert.match(html, /<button[^>]*disabled=""[^>]*>[\s\S]*?Encender cámara/);
  assert.match(html, /<video[^>]*hidden=""/);
});

test("el estado de analisis es real y no muestra resultados ni tiempos inventados", () => {
  const html = renderToStaticMarkup(createElement(Resultado, { resultado: null, analizando: true }));
  assert.match(html, /aria-busy="true"/);
  assert.match(html, /Analizando tu imagen/);
  assert.doesNotMatch(html, /<dd>/);
});

test("una distancia mayor que uno se conserva y no se presenta como porcentaje", () => {
  const html = render(resultado({ persona: null, persona_id: null, distancia: 1.2, confianza: 0 }));
  assert.match(html, /1\.2000/);
  assert.match(html, /Desconocido/);
  assert.match(html, /No aplica/);
});

test("el aviso de baja resolucion depende del dato recibido del servidor", () => {
  assert.match(render(resultado({ fiable: false })), /Rostro pequeño/);
  assert.doesNotMatch(render(resultado()), /Rostro pequeño/);
});

test("la camara evita anunciar todas las metricas en cada cuadro", () => {
  const html = renderToStaticMarkup(createElement(Resultado, { resultado: resultado(), vivo: true }));
  assert.match(html, /aria-live="off"/);
  assert.match(render(resultado()), /aria-live="polite"/);
});

test("un desconocido no expone una foto de referencia aunque la respuesta la traiga", () => {
  const html = render(resultado({ persona: null, persona_id: null, foto: "/personas/1/foto", distancia: 0.9 }));
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /La referencia más cercana no pasa el umbral/);
});

test("el nombre recibido se escapa como texto, no como codigo HTML", () => {
  const html = render(resultado({ persona: '<img src=x onerror="alert(1)">' }));
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /<img src=x/);
});

// --- Registrar y Fotos registradas (#342) ---------------------------------------

test("las cuatro pestañas se pueden elegir y la primera sigue siendo la consulta", () => {
  const html = renderToStaticMarkup(createElement(App));
  for (const id of ["imagen", "camara", "registrar", "registradas"]) assert.match(html, new RegExp(`id="tab-${id}"`));
  assert.match(html, /id="tab-registrar"[^>]*aria-selected="false"[^>]*tabindex="-1"/);
  assert.match(html, /solo Registrar añade fotos a la base/);
});

test("registrar empieza deshabilitado y pide el permiso general", () => {
  const html = renderToStaticMarkup(createElement(Registro, { permiso: false }));
  assert.match(html, /Confirma el permiso de arriba para registrar fotos/);
  assert.match(html, /<button[^>]*disabled=""[^>]*>[\s\S]*?Registrar en la base/);
  assert.match(html, /<label[^>]*for="registro-nombre"/);
  assert.match(html, /id="registro-consentimiento"/);
  assert.doesNotMatch(html, /checked=""/);
});

test("con permiso la guia pide primero el nombre", () => {
  const html = renderToStaticMarkup(createElement(Registro, { permiso: true }));
  assert.match(html, /Escribe el nombre de la persona/);
  assert.match(html, /<button[^>]*disabled=""[^>]*>[\s\S]*?Registrar en la base/);
});

test("fotos registradas carga sin pedir el permiso: retirar una foto siempre se puede", () => {
  const html = renderToStaticMarkup(createElement(Registradas));
  assert.match(html, /Fotos registradas desde la app/);
  assert.match(html, /Cargando fotos/);
  assert.match(html, /no se borran desde aquí/);
});

// --- Todas las fotos de la persona que coinciden (#344) ------------------------

const dos = [
  { rostro_id: 201, foto: "/rostros/201/foto", distancia: 0.3 },
  { rostro_id: 202, foto: "/rostros/202/foto", distancia: 0.4 },
];

test("con dos fotos que coinciden se ven las dos y la mejor es la principal", () => {
  const html = render(resultado({ persona: "Colin_Powell", foto: "/personas/10/foto", coincidencias: dos }));
  assert.match(html, /Fotos de Colin_Powell que coinciden \(2\)/);
  assert.match(html, /2 fotos de referencia coinciden/);
  assert.match(html, /class="reference" src="\/api\/rostros\/201\/foto"/);
  assert.match(html, /class="match-photo" src="\/api\/rostros\/201\/foto"/);
  assert.match(html, /class="match-photo" src="\/api\/rostros\/202\/foto"/);
  assert.match(html, /Distancia 0\.4000/);
  assert.doesNotMatch(html, /personas\/10\/foto/);
});

test("con una sola coincidencia no repite la foto en una galeria", () => {
  const html = render(resultado({ persona: "Colin_Powell", foto: "/personas/10/foto", coincidencias: dos.slice(0, 1) }));
  assert.match(html, /class="reference" src="\/api\/rostros\/201\/foto"/);
  assert.doesNotMatch(html, /que coinciden/);
});

test("un servidor anterior sin coincidencias sigue mostrando su foto de referencia", () => {
  const html = render(resultado({ persona: "Colin_Powell", foto: "/personas/10/foto" }));
  assert.match(html, /src="\/api\/personas\/10\/foto"/);
  assert.match(html, /Foto de referencia registrada/);
});

test("la camara en vivo no muestra la galeria de coincidencias", () => {
  const datos = resultado({ persona: "Colin_Powell", foto: "/personas/10/foto", coincidencias: dos });
  const html = renderToStaticMarkup(createElement(Resultado, { resultado: datos, vivo: true }));
  assert.doesNotMatch(html, /que coinciden/);
  assert.doesNotMatch(html, /rostros\/202/);
});

test("un desconocido no muestra coincidencias aunque la respuesta las traiga", () => {
  const html = render(resultado({ persona: null, persona_id: null, distancia: 0.9, coincidencias: dos }));
  assert.doesNotMatch(html, /<img/);
});
