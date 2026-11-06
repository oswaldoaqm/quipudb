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
| `GEMINI_FALLBACK_MODELS` | Modelos que se prueban, en orden, si el principal está saturado (503) o sin cuota (429). Vacío: sin respaldo |
| `QUIPUCAMAYOC_CORS_ORIGINS` | Orígenes que pueden llamar al servidor, separados por comas |

La interfaz solo necesita `VITE_SERVER_URL` (ver `web/.env.example`), y no lleva
claves: todo lo que empieza con `VITE_` llega al navegador.

## Pruebas

```bash
cd quipucamayoc/server && .venv/Scripts/python -m pytest -q
cd quipucamayoc/web && npm run build
```

Las pruebas del servidor usan un motor y un LLM falsos: no necesitan red ni
clave.
