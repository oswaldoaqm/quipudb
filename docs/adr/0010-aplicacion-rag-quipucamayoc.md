# ADR 0010 - Quipucamayoc: RAG sobre papers académicos

- **Fecha:** 2026-11-06
- **Estado:** Aceptada
- **Issue:** #238

## Contexto

La Parte 5 pide una aplicación real que consuma el API REST del motor, integre
al menos dos tipos de datos y tenga una interfaz que muestre lo que el motor
sabe hacer. El equipo eligió la opción A del Anexo A: un chatbot que responde
preguntas a partir de una colección de papers académicos (RAG).

La aplicación se llama **Quipucamayoc**, por los funcionarios incas que
guardaban y leían los quipus: consulta los papers que QuipuDB guarda. Vive en
`quipucamayoc/`, aparte del motor.

Tres límites del motor, medidos en `main`, condicionan el diseño:

- Un registro creado desde SQL ocupa como máximo 4 087 bytes en un heap
  (páginas de 4 KiB). `VARCHAR(n)` es de largo fijo y cuenta bytes UTF-8.
- `MATCH` no se combina con `JOIN` ni con otras condiciones en el `WHERE`
  (#168), y solo funciona sobre un heap.
- El índice de texto se crea en español salvo que se pida otro idioma con
  `WITH LANGUAGE` (#237).

## Decisión

### 1. Tres piezas, y la aplicación solo habla HTTP con el motor

```
quipucamayoc/web  (React + Vite)
        │  POST /ask, GET /papers ...
        ▼
quipucamayoc/server (FastAPI)  ──►  Gemini (google-genai)
        │  POST /query, POST /tables/{tabla}/load
        ▼
API REST de QuipuDB (engine/api)
```

- El servidor de la aplicación es el único que conoce la clave del LLM. Por
  eso existe: la clave no puede llegar al navegador.
- El servidor no importa `engine` ni `quipudb_native`. Todo lo que sabe del
  motor lo sabe por `POST /query`, `GET /tables` y la carga CSV, que es lo
  que exige el enunciado ("debe consumir el API REST").
- Cada pieza corre en su propio proceso: el motor en su API (un solo
  proceso, como pide #187), el servidor en otro puerto y la interfaz en Vite.

### 2. Esquema en QuipuDB

```sql
CREATE TABLE papers (
  arxiv_id VARCHAR(20) PRIMARY KEY,
  id INT,
  titulo VARCHAR(300),
  autores VARCHAR(600),
  anio INT,
  categoria VARCHAR(20),
  abstract VARCHAR(2000),
  url VARCHAR(100)
) USING HEAP;                                   -- 3 048 bytes por registro

CREATE INDEX papers_por_id ON papers (id) USING HASH;
CREATE INDEX papers_por_anio ON papers (anio) USING BPLUS;

CREATE TABLE chunks (
  id INT PRIMARY KEY,
  paper_id INT,
  orden INT,
  pagina INT,
  texto VARCHAR(4000)
) USING HEAP;                                   -- 4 016 bytes por registro

CREATE INDEX chunks_por_texto ON chunks (texto) USING FULLTEXT WITH LANGUAGE=english;
CREATE INDEX chunks_por_paper ON chunks (paper_id) USING HASH;
```

| Decisión | Por qué |
|---|---|
| `papers` y `chunks` separadas | Los metadatos son el dato relacional y los chunks el textual: son los dos tipos de datos que exige el enunciado. Repetir título y autores en cada chunk no cabría en 4 087 bytes |
| Ambas en HEAP | `MATCH` y los índices secundarios solo funcionan sobre heap. Un archivo secuencial admite registros de 2 041 bytes como máximo y el abstract no cabría |
| Hash sobre `papers.id`, que no es la clave primaria | La recuperación pide un paper por id. En un heap, `WHERE` sobre la clave primaria va por la búsqueda de la tabla, que la recorre hasta encontrarla (150 páginas leídas para el paper 150 de 300) aunque haya un hash sobre esa columna. Sobre una columna que no es la clave, el optimizador usa el hash (2 páginas). Por eso la clave es `arxiv_id`, que además es la identidad natural del paper, y la aplicación asigna `id`. #255 corrige el optimizador, pero el esquema se queda: `arxiv_id` sigue siendo la clave natural |
| B+ sobre `papers.anio` | Los filtros de la biblioteca por rango de años (`WHERE anio >= 2020`) usan el B+ de la Parte 1 |
| Hash sobre `chunks.paper_id` | Contar los chunks de un paper y leer los vecinos de un chunk sin recorrer la tabla |
| `texto VARCHAR(4000)` | Es lo más grande que cabe con las cuatro columnas `INT` (16 bytes) en 4 087. Cada chunk ocupa una página aunque tenga pocas palabras |
| `abstract VARCHAR(2000)` | arXiv limita el abstract a 1 920 caracteres; lo que pase de 2 000 bytes se recorta en el límite de un carácter |

### 3. Chunks

- Objetivo de 300 palabras, con 50 de solapamiento entre chunks consecutivos.
  Ningún chunk baja de 200 palabras salvo el último de un paper o un paper más
  corto que eso, y ninguno pasa de 500, para respetar el rango del enunciado.
- Se corta de preferencia al final de un párrafo o una oración.
- El tope duro es de 4 000 bytes UTF-8: un chunk que lo supere se recorta antes
  de insertarlo y nunca a mitad de un carácter. 300 palabras en inglés son unos
  1 900 caracteres, así que el tope solo actúa con texto muy denso en símbolos.
- Se descarta la sección de referencias: sus nombres y títulos atraen
  coincidencias de BM25 que no responden ninguna pregunta.

### 4. Idioma: corpus en inglés, preguntas en español

Los papers de arXiv están en inglés y el equipo pregunta en español. BM25 compara
términos: "ventajas" nunca coincide con "advantages".

- El índice se crea con `WITH LANGUAGE=english`.
- Antes de buscar, Gemini reescribe la pregunta como una consulta de palabras
  clave en inglés (por ejemplo, "¿Cuáles son las ventajas de HNSW sobre IVF?"
  → `HNSW IVF advantages comparison graph index`). La consulta reescrita es la
  que va dentro de `MATCH` y se muestra en la interfaz.
- Si la reescritura falla, se busca con la pregunta original: las siglas y los
  nombres técnicos suelen coincidir igual.
- La respuesta se genera en el idioma de la pregunta.

Se descartó traducir el corpus (cuesta una llamada por chunk y cambia el texto
citado) y usar papers en español (hay pocos sobre los temas del curso).

### 5. LLM: Gemini

- SDK oficial `google-genai`. La clave va en `GEMINI_API_KEY` y el modelo en
  `GEMINI_MODEL`, ambos en `quipucamayoc/.env`, que git ya ignora. Un
  `.env.example` documenta las variables sin valores reales.
- La clave no se escribe en logs ni viaja en ninguna respuesta.
- Las pruebas usan un LLM falso: el CI no necesita red ni clave y no gasta
  cuota.

#### Qué modelos, medido al implementar (2026-11-06)

| Hallazgo | Decisión |
|---|---|
| `gemini-2.5-flash` responde 404: Google ya no lo da a cuentas nuevas | No se usa |
| `gemini-3.8-flash` y `3.7-flash` responden 503 por alta demanda, hasta 87 s después | No van por omisión; se pueden elegir con `GEMINI_MODEL` |
| `gemini-3.5-flash` responde en 5 a 11 s | `GEMINI_MODEL` por omisión |
| `gemini-3.5-flash-lite` reescribe en menos de 2 s, contra 6 a 9 s de un modelo que razona | `GEMINI_REWRITE_MODEL` por omisión |
| En Gemini 3 el razonamiento no se apaga y sus tokens cuentan contra `max_output_tokens`: con 60, una reescritura volvía cortada | Límite de 8 192 tokens |

Si un modelo responde 429, 500 o 503, o se agota el tiempo (45 s), se prueba el
siguiente de `GEMINI_FALLBACK_MODELS` y el que falló se salta durante 2
minutos: sin eso, cada pregunta esperaba primero a que el saturado se rindiera.
La respuesta dice qué modelo contestó.

### 6. Recuperación y citas

- `SELECT id, paper_id, orden, pagina, texto, SCORE() AS relevancia FROM chunks
  WHERE MATCH(texto, '<consulta>') USING BM25 LIMIT <k>`. BM25 por omisión;
  TF-IDF se puede elegir desde la interfaz para compararlos.
- Después, una consulta `SELECT ... FROM papers WHERE id = <n>` por cada paper
  distinto. `MATCH` no admite `JOIN`, y con pocos papers distintos el hash es
  más barato que recorrer la tabla.
- A lo sumo 3 chunks por paper, para que el contexto no salga de un solo
  documento.
- Cada chunk entra al prompt numerado (`[1]`, `[2]`...) con su referencia
  (autores, año, título). La respuesta cita con esos números y el servidor los
  convierte en papers; un número que no se envió se descarta.
- Sin chunks, se responde que los documentos no tienen la información, sin
  llamar al LLM.
- La pregunta del usuario viaja dentro de un literal SQL. Se escapa duplicando
  la comilla simple, que es lo que acepta el lexer: el API no tiene parámetros
  preparados.

### 7. La respuesta llega por etapas

`POST /ask/stream` devuelve una línea JSON por etapa (`consulta`,
`recuperacion`, `respuesta` o `error`). La recuperación en QuipuDB tarda
decenas de ms y Gemini varios segundos: con una sola respuesta al final, la
interfaz no podía mostrar las fuentes mientras Gemini escribe ni distinguir
"buscando" de "generando". `POST /ask` sigue existiendo con la respuesta entera.

### 8. Lo que cambió en el motor

- `CREATE INDEX ... USING FULLTEXT WITH LANGUAGE=english` (#237).
- El optimizador usa un índice sobre la clave primaria de un heap en vez de
  recorrerlo (#255). El esquema de la sección 2 no cambia: `arxiv_id` sigue
  siendo la clave natural.

### 9. Búsqueda por significado e híbrida (#251, #252)

La recuperación tiene tres modos, que se eligen por pregunta:

| Modo | Búsqueda en QuipuDB | Consulta |
|---|---|---|
| `texto` | `MATCH ... USING BM25` (índice invertido, Parte 3) | La pregunta reescrita en inglés |
| `vectores` | `SIMILAR_TO(embedding, VECTOR(...), k)` con HNSW y coseno (Parte 4) | El embedding de la pregunta original |
| `hibrido` | Las dos, fusionadas con Reciprocal Rank Fusion (k = 60) | La reescrita para BM25 y el embedding para HNSW |

- **Un tercer tipo de dato.** `chunk_vectores (chunk_id INT PRIMARY KEY,
  paper_id INT, embedding VECTOR(768))` con un HNSW de coseno. Va aparte de
  `chunks` porque texto y vector no caben juntos en 4 087 bytes: el vector
  solo ocupa 3 072. `paper_id` permite aplicar el tope de 3 chunks por paper
  sin traer el texto.
- **El motor acepta un vector en SQL (#251).** `SIMILAR_TO` recibía solo un
  archivo, que pasaba por `ExtractFeatures`. Ahora también acepta el literal
  `VECTOR(...)` de `INSERT`, validado igual, y lo busca tal cual. El plan lo
  dice en el paso `knn_search`.
- **El texto de cada chunk por su id.** El HNSW devuelve `chunk_id`; el texto
  sale de `chunks WHERE id = n`, que usa el hash nuevo sobre `chunks.id` gracias
  a #255. Sin ese arreglo, cada chunk recorría los 25 MB de la tabla.
- **Embeddings de Gemini.** `gemini-embedding-001` con 768 dimensiones,
  normalizados. Los chunks se embeben como `RETRIEVAL_DOCUMENT` y la pregunta
  como `RETRIEVAL_QUERY`. El embedding cruza idiomas: la pregunta en español
  se busca sin reescribirla. `gemini-embedding-2` se descartó porque junta una
  lista de textos en un solo vector.
- **Cuota.** La cuota gratuita de `gemini-embedding-001` permite 100
  peticiones y 30 000 tokens por minuto, y 1 000 peticiones por día; cada
  texto de un lote cuenta como una petición. Con ~670 tokens por chunk, los
  6 242 chunks llevan unos 7 días de cuota gratuita (con facturación, minutos
  y menos de un dólar). Por eso `python -m app.corpus.vectores embeber` guarda
  cada lote en una caché binaria en `datos/embeddings/`, se corta solo cuando
  se agota la cuota y retoma donde quedó; `--lote` y `--tokens-por-minuto`
  ajustan el ritmo a un nivel pagado.
  `cargar` inserta con lotes de `INSERT` (la carga CSV no admite `VECTOR`) y
  crea el HNSW después de cargar, que es más barato que mantenerlo fila por
  fila.
- **Números en decimal fijo.** El lexer no lee notación científica, así que
  las componentes van con 7 decimales; están normalizadas y nunca pasan de 1.
- **PDFs subidos.** Si existe la tabla de embeddings, la biblioteca también
  embebe los chunks del PDF nuevo. Si Gemini falla, el paper queda cargado y la
  respuesta lo avisa.

## Alternativas descartadas

- **Interfaz que llama directo al motor y a Gemini.** Expondría la clave en el
  navegador.
- **Importar `engine` desde el servidor.** Más rápido, pero no consume el API
  REST como pide el enunciado, y ataría la aplicación al Python que carga el
  módulo nativo.
- **Una sola tabla con metadatos y texto.** No cabe en 4 087 bytes y repite los
  metadatos en cada chunk.
- **Embeddings como única recuperación.** El enunciado los deja como
  opcionales, embeber el corpus cuesta días de cuota gratuita, y BM25 no necesita un
  modelo extra. Se agregaron como modo aparte y como mitad del híbrido (#252),
  sin quitar el modo de solo texto.
- **Embeddings locales (sentence-transformers).** Evitan la cuota, pero
  arrastran torch al servidor y al CI.

## Consecuencias

- Cada chunk ocupa una página de 4 KiB: 10 000 chunks son unos 40 MB. Es
  aceptable para un corpus de 100 a 300 papers.
- Agregar un paper deja el índice de texto desactualizado y la siguiente
  pregunta lo reconstruye entero (#167). La interfaz tiene que avisar que esa
  pregunta tarda más.
- La reescritura de la pregunta suma una llamada al LLM por pregunta. La
  interfaz muestra los tiempos de recuperación y de generación por separado.
- Si cambia el esquema, cambian a la vez la carga (#244), la recuperación
  (#245) y este ADR.
