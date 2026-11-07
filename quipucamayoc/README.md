# Quipucamayoc

Chatbot que responde preguntas sobre papers académicos con los documentos
guardados en QuipuDB (Parte 5, opción A del enunciado: RAG). El nombre viene de
los quipucamayoc, los funcionarios incas que guardaban y leían los quipus.

Las decisiones de diseño están en el
[ADR 0010](../docs/adr/0010-aplicacion-rag-quipucamayoc.md).

```
web (React, :5174)  ──►  server (FastAPI, :8001)  ──►  Gemini
                                   │
                                   ▼
                     API de QuipuDB (engine/api, :8000)
```

La aplicación solo habla con el motor por su API REST: el servidor no importa
`engine` ni el módulo nativo.

## Levantarla en local

Son tres procesos, cada uno en su terminal.

**1. El motor**, desde la raíz del repositorio, con el módulo nativo compilado
(ver el README principal):

```bash
uvicorn engine.api.main:app --port 8000
```

**2. El servidor**, desde `quipucamayoc/server`:

```bash
cp ../.env.example ../.env      # y pon tu GEMINI_API_KEY en ../.env
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # en Linux/macOS: .venv/bin/pip
.venv/Scripts/uvicorn app.main:app --reload --port 8001
```

`GET http://localhost:8001/health` dice si el servidor alcanza al motor y si
Gemini está configurado.

**3. La interfaz**, desde `quipucamayoc/web`:

```bash
npm install
npm run dev                     # http://localhost:5174
```

Con `VITE_USE_MOCK=true` (en `web/.env`) la interfaz usa los datos falsos de
`web/src/api/mock.ts` y funciona sin servidor, motor ni clave.

La interfaz tiene dos vistas:

- **Preguntar**: la respuesta llega por `POST /ask/stream`, así que las fuentes
  aparecen mientras Gemini todavía escribe. Cada cita `[n]` abre su fuente. Debajo,
  *Cómo respondió la base de datos* muestra cada SQL que se ejecutó en QuipuDB con
  su plan (los pasos que usan un índice van resaltados) y un botón para copiarlo al
  Panel de Consultas del motor. La barra de tiempos separa la reescritura, la
  recuperación en QuipuDB y la generación.
- **Biblioteca**: los papers con sus chunks, filtrados por rango de años (B+ sobre
  `anio`, en QuipuDB) y por autor (en el servidor: el SQL del motor no tiene
  `LIKE`), y un formulario para agregar un PDF.

### Búsqueda por significado (opcional)

Para los modos *Significado* e *Híbrido* hacen falta los embeddings de los
chunks. Con el motor levantado y el corpus cargado, desde `quipucamayoc/server`:

```bash
.venv/Scripts/python -m app.corpus.vectores embeber   # ~3 h por la cuota de Gemini; se puede cortar y retomar
.venv/Scripts/python -m app.corpus.vectores cargar    # tabla chunk_vectores e índice HNSW, ~1 min
```

`embeber` guarda los vectores en `quipucamayoc/datos/embeddings/`: volver a
cargar el corpus no vuelve a gastar cuota. Sin embeddings, la interfaz deja
esos modos deshabilitados.

## Configuración

Todo vive en `quipucamayoc/.env`, que git ignora. `.env.example` lista las
variables:

| Variable | Para qué |
|---|---|
| `QUIPUDB_API_URL` | URL del API del motor (`http://127.0.0.1:8000`; con `localhost`, Windows prueba IPv6 primero y cada petición tarda ~0,8 s más) |
| `QUIPUDB_TIMEOUT_S` | Segundos antes de dar al motor por caído |
| `GEMINI_API_KEY` | Clave de Gemini. Solo la lee el servidor; nunca llega al navegador ni a los logs |
| `GEMINI_MODEL` | Modelo que responde (`gemini-3.5-flash`). Los 3.7 y 3.8 estaban saturados (503) al medirlo; Google ya no da `gemini-2.5-flash` a cuentas nuevas |
| `GEMINI_REWRITE_MODEL` | Modelo que reescribe la pregunta como palabras clave en inglés (`gemini-3.5-flash-lite`: rápido) |
| `GEMINI_EMBEDDING_MODEL` | Modelo de embeddings para la búsqueda por significado (`gemini-embedding-001`) |
| `GEMINI_FALLBACK_MODELS` | Modelos que se prueban, en orden, si el principal está saturado (503) o sin cuota (429). Vacío: sin respaldo |
| `QUIPUCAMAYOC_CORS_ORIGINS` | Orígenes que pueden llamar al servidor, separados por comas |

La interfaz solo necesita `VITE_SERVER_URL` (ver `web/.env.example`), y no lleva
claves: todo lo que empieza con `VITE_` llega al navegador.

## Preguntar: `POST /ask`

```json
{"pregunta": "¿Cuáles son las ventajas de HNSW sobre IVF?", "k": 8, "ranking": "BM25", "reescribir": true, "modo": "hibrido"}
```

Solo `pregunta` es obligatoria. `k` va de 1 a 20, `ranking` es `BM25` o
`TF_IDF` y `modo` es `texto` (por omisión), `vectores` o `hibrido`; los dos
últimos responden 409 si no hay embeddings cargados. La respuesta trae:

| Campo | Qué es |
|---|---|
| `consulta` | Lo que se buscó con `MATCH`: la pregunta reescrita en inglés, o la original |
| `respuesta` | El texto de Gemini, con citas `[n]` que apuntan a `fuentes[n - 1]` |
| `fuentes` | Los chunks enviados a Gemini, numerados, con su puntaje, qué búsqueda los encontró (`origen`: `texto`, `vectores` o las dos), si se citaron y su paper |
| `citas`, `citas_descartadas` | Los números citados, y los que Gemini inventó y se quitaron del texto |
| `sql` | Cada sentencia ejecutada en QuipuDB, con su plan y su tiempo |
| `tiempos` | Embedding, reescritura, recuperación en QuipuDB, generación y total, en ms |
| `modelo` | El modelo de Gemini que respondió (`null` si no hubo fragmentos y no se lo llamó) |

Los errores responden `{"error": "...", "origen": "motor" | "llm"}`: 503 si el
motor no responde o falta la clave de Gemini, y 502 si el motor rechazó la
consulta o Gemini falló. Los modelos están en `server/app/contrato.py` y la
documentación interactiva en `http://127.0.0.1:8001/docs`.

## Evaluación

Con 30 preguntas de prueba, reescribir la pregunta en inglés sube el MRR@10 de
BM25 de 0,21 a 0,93, y QuipuDB tarda unos 45 ms de los ~5 s de una respuesta.
Detalle en [`docs/informe/rag_evaluacion.md`](../docs/informe/rag_evaluacion.md)
y cómo se mide en [`benchmarks/README.md`](../benchmarks/README.md#evaluación-del-rag-de-quipucamayoc-issue-253).

## Pruebas

```bash
cd quipucamayoc/server && .venv/Scripts/python -m pytest -q
cd quipucamayoc/web && npm run build
```

Las pruebas del servidor usan un motor y un LLM falsos: no necesitan red ni
clave.
