/**
 * Las tablas de texto (Parte 3) y multimedia (Parte 4) del catalogo falso.
 *
 * Llevan los nombres de las consultas del enunciado —`documentos`,
 * `articulos`, `imagenes`, `canciones`— para que esas consultas corran tal
 * cual en el simulador, sin adaptarlas.
 *
 * Los vectores no salen de SIFT ni de MFCC: el navegador no tiene OpenCV ni
 * los vocabularios. Cada objeto es el centroide de su categoria mas un ruido
 * determinista, normalizado como el histograma TF-IDF que guarda el motor.
 * Alcanza para que los vecinos de una foto de gato sean gatos, que es lo que
 * la demo tiene que mostrar.
 */

import type { TablaFalsa } from "@/api/mock/datos";

/** Generador congruencial: el mismo indice da siempre el mismo vector. */
function aleatorio(semilla: number): () => number {
  let estado = semilla >>> 0;
  return () => {
    estado = (estado * 1_664_525 + 1_013_904_223) >>> 0;
    return estado / 2 ** 32;
  };
}

/**
 * Centroide + ruido, sin componentes negativas y con norma 1.
 *
 * Un histograma de palabras visuales no tiene frecuencias negativas, y el
 * motor lo normaliza antes de guardarlo. Cuatro decimales porque el motor
 * guarda float32 y mas digitos solo ensucian la celda.
 */
function vectorDe(centroide: number[], semilla: number, ruido = 0.18): number[] {
  const azar = aleatorio(semilla);
  const crudo = centroide.map((c) => Math.max(0, c + (azar() - 0.5) * 2 * ruido));
  const norma = Math.hypot(...crudo) || 1;
  return crudo.map((c) => Number((c / norma).toFixed(4)));
}

// --- Parte 3: texto ---------------------------------------------------------

const CONTENIDOS = [
  "Una base de datos vectorial guarda embeddings y responde busquedas por similitud con indices HNSW o IVF",
  "El indice invertido asocia cada termino con la lista de documentos donde aparece y su frecuencia",
  "SPIMI construye el indice invertido por bloques en memoria y los mezcla en disco en una sola pasada",
  "BM25 satura la frecuencia del termino y normaliza por la longitud del documento",
  "TF-IDF pondera cada termino por su frecuencia y por lo raro que es en la coleccion; el coseno compara vectores",
  "Las bases de datos relacionales organizan los datos en tablas con filas y columnas",
  "Un arbol B+ mantiene las claves ordenadas y resuelve busquedas por rango en pocas lecturas de disco",
  "El hashing extensible duplica el directorio cuando un bucket se llena y no reorganiza el archivo entero",
  "El R-Tree agrupa puntos cercanos en rectangulos minimos y acelera las consultas espaciales por radio",
  "La busqueda de vecinos mas cercanos en alta dimension sufre la maldicion de la dimensionalidad",
  "HNSW navega un grafo jerarquico de mundos pequenos y encuentra vecinos aproximados en tiempo logaritmico",
  "IVF agrupa los vectores con k-means y solo revisa las listas de los centroides mas cercanos a la consulta",
  "El control de concurrencia con bloqueos evita que dos transacciones escriban el mismo registro a la vez",
  "Una transaccion agrupa operaciones que se confirman o se deshacen juntas",
  "El external sorting ordena archivos mas grandes que la memoria con runs y una mezcla k-way",
  "Un plan de ejecucion muestra que indices uso el motor y en que orden corrio cada operacion",
  "PostgreSQL ofrece indices GIN para busqueda de texto completo y GIST para datos espaciales",
  "Los embeddings de texto representan el significado de una frase como un vector denso",
  "La recuperacion aumentada por generacion busca fragmentos relevantes y se los entrega a un modelo de lenguaje",
  "Precision y recall miden cuantos de los documentos recuperados son relevantes y cuantos relevantes se recuperaron",
  "La tokenizacion separa el texto en terminos, quita las stopwords y reduce cada palabra a su raiz",
  "El archivo secuencial mantiene los registros ordenados por clave y se reorganiza al acumular espacio libre",
  "Un heap file inserta los registros donde haya espacio y reutiliza los huecos de los eliminados",
  "La receta de ceviche lleva pescado fresco, limon, cebolla morada y aji limo",
  "El lomo saltado combina carne, cebolla, tomate y papas fritas al estilo chifa",
  "Machu Picchu es una ciudadela inca construida en lo alto de los Andes",
  "El rio Amazonas atraviesa la selva y es el mas caudaloso del mundo",
  "El futbol peruano celebro su clasificacion al mundial despues de muchos anos",
  "Los datos geograficos se consultan con latitud, longitud y la distancia de Haversine",
  "Una base de datos multimodal combina tablas, coordenadas, texto e imagenes en un mismo motor",
];

const documentos: TablaFalsa = {
  info: {
    name: "documentos",
    storage: "heap",
    record_count: CONTENIDOS.length,
    columns: [
      { name: "id", type: "INT", size: null, is_primary_key: true },
      { name: "titulo", type: "VARCHAR", size: 32, is_primary_key: false },
      { name: "contenido", type: "VARCHAR", size: 160, is_primary_key: false },
    ],
    indexes: [
      {
        name: "documentos_texto",
        column: "contenido",
        structure: "inverted_index",
        supports_range: false,
      },
    ],
  },
  tipos: ["INT", "VARCHAR", "VARCHAR"],
  filas: CONTENIDOS.map((contenido, i) => [
    1 + i,
    `documento ${String(1 + i).padStart(2, "0")}`,
    contenido,
  ]),
  opciones: { documentos_texto: { idioma: "spanish" } },
};

/** Titulo, autor, anio y resumen. Los autores son inventados. */
const ARTICULOS: [string, string, number, string][] = [
  ["Introduccion al machine learning", "r. salas", 2019,
    "El machine learning aprende patrones a partir de datos etiquetados para predecir casos nuevos"],
  ["Deep learning para vision", "m. quispe", 2021,
    "Las redes neuronales convolucionales aprenden a reconocer objetos en imagenes sin descriptores manuales"],
  ["Machine learning en bases de datos", "a. torres", 2022,
    "Integrar machine learning dentro del motor evita mover los datos y acelera el entrenamiento"],
  ["Optimizacion de consultas aprendida", "l. vega", 2020,
    "Un optimizador basado en learning estima cardinalidades mejor que los histogramas clasicos"],
  ["Indices aprendidos", "c. ramos", 2018,
    "Un modelo de machine learning puede reemplazar a un arbol B+ prediciendo la posicion de cada clave"],
  ["Recuperacion de informacion clasica", "j. paz", 2015,
    "El modelo vectorial con TF-IDF y similitud del coseno sigue siendo la base de los buscadores"],
  ["BM25 en la practica", "e. mora", 2017,
    "BM25 supera a TF-IDF en colecciones con documentos de longitudes muy distintas"],
  ["Busqueda semantica con embeddings", "d. lima", 2023,
    "Los embeddings capturan el significado y complementan la busqueda por palabras clave"],
  ["Busqueda hibrida", "g. cano", 2024,
    "Fusionar BM25 con similitud vectorial mediante reciprocal rank fusion mejora la recuperacion"],
  ["Grafos de vecinos para busqueda aproximada", "h. soto", 2020,
    "HNSW construye capas de grafos y logra alto recall con pocas comparaciones de distancia"],
  ["Cuantizacion de vectores", "i. bravo", 2019,
    "IVF y la cuantizacion por productos reducen la memoria de un indice vectorial de alta dimension"],
  ["Bag of visual words", "k. rojas", 2012,
    "SIFT extrae descriptores locales y k-means los agrupa en palabras visuales para comparar imagenes"],
  ["Coeficientes MFCC para audio", "n. flores", 2014,
    "Los MFCC resumen el espectro de cada ventana de audio en la escala mel"],
  ["Aprendizaje por refuerzo", "o. chavez", 2021,
    "Un agente aprende una politica por ensayo y error maximizando una recompensa acumulada"],
  ["Machine learning responsable", "p. guzman", 2023,
    "Los modelos de machine learning deben auditarse para detectar sesgos en los datos de entrenamiento"],
  ["Transacciones distribuidas", "s. ibarra", 2016,
    "El protocolo de dos fases coordina la confirmacion de una transaccion entre varios nodos"],
  ["Bases de datos espaciales", "t. ponce", 2013,
    "El R-Tree y sus variantes indexan rectangulos y responden consultas de rango y vecinos cercanos"],
  ["Procesamiento de lenguaje natural", "v. castro", 2022,
    "Los modelos de lenguaje aprenden representaciones de texto con learning auto supervisado"],
  ["Sistemas de recomendacion", "w. medina", 2018,
    "El filtrado colaborativo recomienda productos a partir de las preferencias de usuarios similares"],
  ["Evaluacion de buscadores", "x. silva", 2016,
    "Precision@10 y recall@10 comparan rankings contra juicios de relevancia humanos"],
];

const articulos: TablaFalsa = {
  info: {
    name: "articulos",
    storage: "heap",
    record_count: ARTICULOS.length,
    columns: [
      { name: "id", type: "INT", size: null, is_primary_key: true },
      { name: "titulo", type: "VARCHAR", size: 48, is_primary_key: false },
      { name: "autor", type: "VARCHAR", size: 16, is_primary_key: false },
      { name: "anio", type: "INT", size: null, is_primary_key: false },
      { name: "texto", type: "VARCHAR", size: 128, is_primary_key: false },
    ],
    indexes: [
      {
        name: "articulos_texto",
        column: "texto",
        structure: "inverted_index",
        supports_range: false,
      },
    ],
  },
  tipos: ["INT", "VARCHAR", "VARCHAR", "INT", "VARCHAR"],
  filas: ARTICULOS.map(([titulo, autor, anio, texto], i) => [1 + i, titulo, autor, anio, texto]),
  opciones: { articulos_texto: { idioma: "spanish" } },
};

// --- Parte 4: multimedia ----------------------------------------------------

/** Dimension de los histogramas: K palabras visuales o auditivas. */
export const DIMENSION_MULTIMEDIA = 8;

const CATEGORIAS_IMAGEN: [string, number[]][] = [
  ["gato", [0.9, 0.7, 0.1, 0.0, 0.2, 0.0, 0.1, 0.0]],
  ["perro", [0.1, 0.8, 0.5, 0.1, 0.0, 0.0, 0.0, 0.4]],
  ["playa", [0.0, 0.1, 0.9, 0.8, 0.0, 0.2, 0.0, 0.1]],
  ["montana", [0.1, 0.0, 0.3, 0.2, 0.9, 0.7, 0.0, 0.0]],
  ["auto", [0.0, 0.0, 0.0, 0.1, 0.1, 0.2, 0.9, 0.8]],
  ["flor", [0.2, 0.0, 0.1, 0.0, 0.0, 0.9, 0.3, 0.7]],
];

const POR_CATEGORIA = 8;

const imagenes: TablaFalsa = {
  info: {
    name: "imagenes",
    storage: "heap",
    record_count: CATEGORIAS_IMAGEN.length * POR_CATEGORIA,
    columns: [
      { name: "id", type: "INT", size: null, is_primary_key: true },
      { name: "nombre", type: "VARCHAR", size: 24, is_primary_key: false },
      { name: "ruta", type: "VARCHAR", size: 48, is_primary_key: false },
      { name: "rasgos", type: "VECTOR", size: DIMENSION_MULTIMEDIA, is_primary_key: false },
    ],
    indexes: [
      { name: "imagenes_hnsw", column: "rasgos", structure: "hnsw", supports_range: false },
      { name: "imagenes_ivf", column: "rasgos", structure: "ivf", supports_range: false },
    ],
  },
  tipos: ["INT", "VARCHAR", "VARCHAR", "VECTOR"],
  filas: CATEGORIAS_IMAGEN.flatMap(([categoria, centroide], c) =>
    Array.from({ length: POR_CATEGORIA }, (_, i) => {
      const id = 1 + c * POR_CATEGORIA + i;
      const nombre = `${categoria}_${String(i + 1).padStart(2, "0")}`;
      return [id, nombre, `fotos/${nombre}.jpg`, vectorDe(centroide, id * 7919)];
    }),
  ),
  opciones: { imagenes_hnsw: { metrica: "cosine" } },
  vocabulario: "fotos.npz",
};

const GENEROS: [string, string, string[], number[]][] = [
  ["huayno", "los andinos del sur", ["cielo de puna", "rio de altura", "palomita viajera", "carnaval de mayo", "la cosecha", "viento serrano"],
    [0.9, 0.2, 0.6, 0.0, 0.1, 0.0, 0.3, 0.0]],
  ["salsa", "orquesta caribe norte", ["noche de malecon", "la clave", "son del barrio", "trompeta mayor", "pa bailar", "timbal y cuero"],
    [0.1, 0.9, 0.0, 0.7, 0.3, 0.1, 0.0, 0.0]],
  ["rock", "ciudad electrica", ["ruido blanco", "carretera", "amplificador", "luces de neon", "sin frenos", "estatica"],
    [0.0, 0.1, 0.1, 0.2, 0.9, 0.8, 0.0, 0.1]],
  ["cumbia", "sonora del rimac", ["la chichera", "guitarra de selva", "amor de verano", "baila morena", "fiesta en el cerro", "la ultima"],
    [0.7, 0.1, 0.0, 0.6, 0.0, 0.0, 0.5, 0.0]],
  ["jazz", "trio de medianoche", ["blue en re", "contrabajo", "humo azul", "cuatro tiempos", "improvisacion", "terraza"],
    [0.0, 0.2, 0.0, 0.1, 0.2, 0.1, 0.9, 0.6]],
  ["clasica", "camerata lima", ["preludio", "sonata breve", "adagio", "allegro", "nocturno", "fuga en sol"],
    [0.1, 0.0, 0.7, 0.0, 0.0, 0.2, 0.4, 0.9]],
];

const canciones: TablaFalsa = {
  info: {
    name: "canciones",
    storage: "heap",
    record_count: GENEROS.length * 6,
    columns: [
      { name: "id", type: "INT", size: null, is_primary_key: true },
      { name: "nombre", type: "VARCHAR", size: 32, is_primary_key: false },
      { name: "artista", type: "VARCHAR", size: 32, is_primary_key: false },
      { name: "ruta", type: "VARCHAR", size: 48, is_primary_key: false },
      { name: "rasgos", type: "VECTOR", size: DIMENSION_MULTIMEDIA, is_primary_key: false },
    ],
    // Solo IVF: asi `USING HNSW` sobre canciones muestra la busqueda exacta.
    indexes: [
      { name: "canciones_ivf", column: "rasgos", structure: "ivf", supports_range: false },
    ],
  },
  tipos: ["INT", "VARCHAR", "VARCHAR", "VARCHAR", "VECTOR"],
  filas: GENEROS.flatMap(([genero, artista, temas, centroide], g) =>
    temas.map((tema, i) => {
      const id = 1 + g * temas.length + i;
      const archivo = `${genero}_${String(i + 1).padStart(2, "0")}`;
      return [id, tema, artista, `audio/${archivo}.mp3`, vectorDe(centroide, id * 104_729)];
    }),
  ),
  vocabulario: "audio.npz",
};

/**
 * Los archivos de consulta que el simulador sabe "extraer".
 *
 * Los del enunciado se parecen a una categoria a proposito —la foto a un
 * gato, el audio a una salsa—, para que el resultado se pueda juzgar a ojo.
 * Ademas, toda `ruta` de las tablas multimedia es un archivo valido: su
 * extraccion da el vector guardado, como haria ExtractFeatures con el mismo
 * vocabulario.
 */
export const ARCHIVOS_DE_CONSULTA: Record<string, number[]> = {
  "foto_consulta.jpg": vectorDe(CATEGORIAS_IMAGEN[0][1], 424_242, 0.25),
  "audio_query.mp3": vectorDe(GENEROS[1][3], 515_151, 0.25),
};

export const TABLAS_MULTIMODALES: TablaFalsa[] = [documentos, articulos, imagenes, canciones];
