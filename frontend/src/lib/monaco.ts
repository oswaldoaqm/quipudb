/**
 * Monaco servido desde el bundle, no desde un CDN.
 *
 * `@monaco-editor/react` por defecto lo descarga de jsdelivr en tiempo de
 * ejecucion, lo que deja el editor —y con el, el panel de consultas— sin
 * funcionar si no hay red durante la demostracion. Empaquetarlo cuesta peso y
 * quita esa dependencia.
 *
 * SQL no necesita worker de lenguaje: su resaltado es Monarch, que corre en el
 * hilo principal. Basta el worker base del editor.
 */

import { loader } from "@monaco-editor/react";
// "monaco-editor" a secas arrastra los ~90 lenguajes y sus servicios: 15 MB de
// bundle, con un worker de TypeScript de 6,7 MB que aqui no pinta nada. Esta
// entrada es solo el editor, y el lenguaje se registra por separado.
import * as monaco from "monaco-editor/editor";
import "monaco-editor/languages/definitions/sql/register";
// Los `exports` del paquete mapean "monaco-editor/X" a "esm/vs/X.js", asi que
// la ruta NO lleva el prefijo esm/vs que documentan las guias viejas. Con el
// prefijo, el fallo solo aparece al cargar la pagina: el typecheck lo da bueno.
import editorWorker from "monaco-editor/editor/editor.worker.js?worker";

self.MonacoEnvironment = {
  getWorker: () => new editorWorker(),
};

loader.config({ monaco });
