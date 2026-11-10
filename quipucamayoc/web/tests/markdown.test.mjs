// El Markdown minimo de las respuestas de Gemini (src/lib/markdown.ts).
import assert from "node:assert/strict";
import { test } from "node:test";

import { arbol, bloques, partes } from "../src/lib/markdown.ts";

const marcas = (texto) => partes(texto).filter((p) => p.marca).map((p) => p.texto);

test("la negrita admite una cursiva adentro, como la escribe Gemini", () => {
  assert.deepEqual(marcas("**Mejor compensación (*recall*):** HNSW gana [2]."), [
    "**Mejor compensación (*recall*):**",
    "[2]",
  ]);
});

test("reconoce cursiva, codigo, formulas y citas de varios digitos", () => {
  assert.deepEqual(marcas("*cursiva* y `nprobe` con $k_1$ [10][3]"), [
    "*cursiva*",
    "`nprobe`",
    "$k_1$",
    "[10]",
    "[3]",
  ]);
});

test("asteriscos sueltos y corchetes que no son citas quedan como texto", () => {
  assert.deepEqual(marcas("a * b * c, el vector [x] y [1a]"), []);
  assert.equal(partes("a * b * c").map((p) => p.texto).join(""), "a * b * c");
});

test("el texto se reconstruye entero a partir de sus partes", () => {
  const texto = "Según **HNSW (*grafo*)** y `IVF` [1], *ver* [2].";
  assert.equal(partes(texto).map((p) => p.texto).join(""), texto);
});

test("agrupa parrafos, titulos y listas de vinetas o numeradas", () => {
  const texto = [
    "## Resumen",
    "Primera linea",
    "segunda linea.",
    "",
    "* uno",
    "- dos",
    "1. primero",
    "2) segundo",
    "Cierre.",
  ].join("\n");

  assert.deepEqual(bloques(texto), [
    { tipo: "titulo", texto: "Resumen" },
    { tipo: "parrafo", texto: "Primera linea segunda linea." },
    { tipo: "lista", ordenada: false, items: ["uno", "dos"] },
    { tipo: "lista", ordenada: true, items: ["primero", "segundo"] },
    { tipo: "parrafo", texto: "Cierre." },
  ]);
});

test("las citas dentro de una cursiva siguen siendo citas (#271)", () => {
  const texto = "*(Nota: IVF admite más inserciones [8], y VQ es más chico [3]).*";

  assert.deepEqual(arbol(texto), [
    {
      tipo: "cursiva",
      hijos: [
        { tipo: "texto", texto: "(Nota: IVF admite más inserciones " },
        { tipo: "cita", numero: 8 },
        { tipo: "texto", texto: ", y VQ es más chico " },
        { tipo: "cita", numero: 3 },
        { tipo: "texto", texto: ")." },
      ],
    },
  ]);
});

test("la negrita con una cursiva adentro queda anidada, y el codigo no se recorre", () => {
  assert.deepEqual(arbol("**Mejor (*recall*):** `k=[1]` [2]"), [
    {
      tipo: "negrita",
      hijos: [
        { tipo: "texto", texto: "Mejor (" },
        { tipo: "cursiva", hijos: [{ tipo: "texto", texto: "recall" }] },
        { tipo: "texto", texto: "):" },
      ],
    },
    { tipo: "texto", texto: " " },
    { tipo: "codigo", texto: "k=[1]" },
    { tipo: "texto", texto: " " },
    { tipo: "cita", numero: 2 },
  ]);
});
