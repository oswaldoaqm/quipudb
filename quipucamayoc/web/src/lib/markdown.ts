/**
 * La parte pura del Markdown de las respuestas: bloques y marcas en linea.
 * Sin JSX, para probarla con node --test (tests/markdown.test.mjs).
 */

export type Bloque =
  | { tipo: "parrafo"; texto: string }
  | { tipo: "titulo"; texto: string }
  | { tipo: "lista"; ordenada: boolean; items: string[] };

const VINETA = /^\s*[*-]\s+(.*)$/;
const NUMERO = /^\s*\d+[.)]\s+(.*)$/;
const TITULO = /^\s*#{1,6}\s+(.*)$/;

export function bloques(texto: string): Bloque[] {
  const salida: Bloque[] = [];
  let parrafo: string[] = [];
  const cerrarParrafo = () => {
    if (parrafo.length) salida.push({ tipo: "parrafo", texto: parrafo.join(" ") });
    parrafo = [];
  };
  for (const linea of texto.split("\n")) {
    const vineta = VINETA.exec(linea);
    const numero = NUMERO.exec(linea);
    const titulo = TITULO.exec(linea);
    if (vineta || numero) {
      cerrarParrafo();
      const ordenada = !vineta;
      const ultimo = salida.at(-1);
      const item = (vineta ?? numero)![1]!;
      if (ultimo?.tipo === "lista" && ultimo.ordenada === ordenada) ultimo.items.push(item);
      else salida.push({ tipo: "lista", ordenada, items: [item] });
    } else if (titulo) {
      cerrarParrafo();
      salida.push({ tipo: "titulo", texto: titulo[1]! });
    } else if (!linea.trim()) {
      cerrarParrafo();
    } else {
      parrafo.push(linea.trim());
    }
  }
  cerrarParrafo();
  return salida;
}

// La negrita puede llevar cursivas adentro: **titulo (*recall*):**.
export const MARCAS = /(\*\*(?:[^*]|\*[^*]+\*)+?\*\*|\*[^*\s][^*]*\*|`[^`]+`|\$[^$]+\$|\[\d+\])/g;

export interface Parte {
  texto: string;
  /** Si es una marca (negrita, cita...) o texto tal cual. */
  marca: boolean;
}

/**
 * El texto partido en trozos: cada marca sola y lo demas entre ellas. Con un
 * grupo de captura, split deja las marcas en las posiciones impares.
 */
export function partes(texto: string): Parte[] {
  return texto
    .split(MARCAS)
    .map((trozo, i) => ({ texto: trozo, marca: i % 2 === 1 }))
    .filter((parte) => parte.texto !== "");
}
