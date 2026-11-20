# Puertos

Cada proceso del proyecto tiene su propio puerto, así que el motor, el modo
distribuido y las dos aplicaciones se pueden tener levantados a la vez. Antes
el coordinador y sus nodos usaban 8000, 8001 y 8002, los mismos que el motor,
el servidor de Quipucamayoc y el de Rikuq. Además, cada aplicación levantaba su
propio motor en el 8000.

| Proceso | Puerto | Cómo se levanta |
|---|---:|---|
| Motor general (API de `engine/api`) | 8000 | `docker compose up -d` o `uvicorn engine.api.main:app --port 8000` |
| Frontend contra el motor general | 5173 | `cd frontend && npm run dev` |
| Servidor de Quipucamayoc | 8001 | `uvicorn app.main:app --port 8001` en `quipucamayoc/server` |
| Servidor de Rikuq | 8002 | `uvicorn app.main:app --port 8002` en `rikuq/server` |
| Motor de Quipucamayoc | 8003 | `uvicorn engine.api.main:app --port 8003`, catálogo `datos/quipucamayoc/catalogo.txt` |
| Motor de Rikuq | 8004 | `uvicorn engine.api.main:app --port 8004`, catálogo `datos/rikuq/catalogo.txt` |
| Interfaz de Quipucamayoc | 5174 | `npm run dev` en `quipucamayoc/web` |
| Interfaz de Rikuq | 5175 | `npm run dev` en `rikuq/web` |
| Coordinador distribuido | 8010 | `docker compose -f docker-compose.distribuido.yml up -d` |
| Nodo n1 | 8011 | el mismo compose; en local, `scripts/demo_distribuida.py` |
| Nodo n2 | 8012 | ídem |
| Frontend contra el coordinador | 5176 | `cd frontend && npm run dev:distribuido` |

## Por qué cada aplicación tiene su motor

Un motor abre un solo catálogo y dos procesos no deben abrir el mismo: los dos
escribirían las mismas páginas. Rikuq ya usaba su propio catálogo para no
mezclar su demo con otras bases; ahora Quipucamayoc hace lo mismo, y los dos
dejan el 8000 al motor general, que es el que usa el frontend del motor con
las tablas de las Partes 1 a 4.

Los valores por defecto de los servidores ya apuntan a su motor:
`QUIPUDB_API_URL=http://127.0.0.1:8003` en Quipucamayoc y
`QUIPUDB_URL=http://127.0.0.1:8004` en Rikuq. Si tienes un `quipucamayoc/.env`
o un `rikuq/.env` copiado de antes, cambia ahí la URL del motor.

## Dentro de Docker

Los contenedores del distribuido escuchan todos en su 8000 interno: el catálogo
[`catalogo-distribuido-v1.json`](ejemplos/catalogo-distribuido-v1.json) dice
`http://n1:8000` y `http://n2:8000`, que son nombres de la red de Docker. Solo
cambian los puertos publicados en la máquina (8010, 8011 y 8012). El catálogo
de los nodos locales,
[`catalogo-nodos-locales-v1.json`](ejemplos/catalogo-nodos-locales-v1.json),
sí usa 8011 y 8012.

## Todo a la vez

Cada línea en su terminal, desde la raíz del repositorio salvo donde se indica
(en PowerShell, las variables se ponen antes con `$env:NOMBRE = "valor"`):

```bash
# Motor general y su frontend
docker compose up -d                                          # :8000
cd frontend && npm run dev                                    # :5173

# Distribuido y su frontend
docker compose -f docker-compose.distribuido.yml up -d        # :8010, :8011, :8012
cd frontend && npm run dev:distribuido                        # :5176

# Quipucamayoc
QUIPUDB_CATALOG=datos/quipucamayoc/catalogo.txt \
  uvicorn engine.api.main:app --host 127.0.0.1 --port 8003
cd quipucamayoc/server && .venv/Scripts/uvicorn app.main:app --port 8001
cd quipucamayoc/web && npm run dev                            # :5174

# Rikuq
QUIPUDB_CATALOG=datos/rikuq/catalogo.txt \
  uvicorn engine.api.main:app --host 127.0.0.1 --port 8004
cd rikuq/server && python -m uvicorn app.main:app --host 127.0.0.1 --port 8002 --env-file ../.env
cd rikuq/web && npm run dev                                   # :5175
```

Para revisar que todos responden:

```bash
# Los tres motores no tienen /health: se les pide la lista de tablas.
for p in 8000 8003 8004; do curl -s -o /dev/null -w "$p %{http_code}\n" http://127.0.0.1:$p/tables; done
for p in 8001 8002 8010; do curl -s -o /dev/null -w "$p %{http_code}\n" http://127.0.0.1:$p/health; done
```
