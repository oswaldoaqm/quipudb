# API de Rikuq (#309)

Servidor independiente del motor. Desde `rikuq/server`:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8002 --env-file ../.env
```

Crear `rikuq/.env` a partir de `.env.example` o exportar las variables.
`QUIPUDB_URL` tiene prioridad sobre el nombre anterior `QUIPUDB_API_URL`.
`GET /health` es liveness de Rikuq: no comprueba modelos ni disponibilidad de
QuipuDB. El arranque no descarga modelos ni importa OpenCV, numpy o el motor.

## POST /identificar

Acepta un unico archivo multipart en el campo `imagen`, o un cuerpo binario
con Content-Type `image/jpeg`, `image/png` o `image/webp`. Maximo 8 MiB.
La lectura multipart se hace con un parser incremental y buffers acotados en
memoria, no con UploadFile/SpooledTemporaryFile. No se conserva el nombre
original, ni se escribe la foto ni un cuadro de camara a disco. Solo se envia
el vector a QuipuDB mediante POST /query, nunca la imagen al endpoint de carga.

```bash
curl -F 'imagen=@foto.jpg;type=image/jpeg' http://127.0.0.1:8002/identificar
```

Respuesta: `persona`, `distancia`, `confianza`, `ms_motor`, `ms_total`.
Adicionalmente: `persona_id`, `foto`, `caja` (`[x,y,ancho,alto]` en pixeles de
la imagen original), `ancho`, `alto` y `rostros`. Este ultimo contiene una
identificacion y una caja por rostro. Los campos principales describen el
primer rostro, salvo `ms_motor`, que suma las busquedas vectoriales.

- Desconocido: `persona`, `persona_id` y `foto` son null; confianza es cero.
- Base vacia: distancia es null, no Infinity. No se inventa una identidad.
- Confianza es el margen normalizado al umbral 0.55, **no una probabilidad**.
- `ms_motor`: ida y vuelta HTTP del SELECT vectorial HNSW. Incluye red local
  y procesamiento de la respuesta; excluye extraer rasgos y consultar nombres.
  Es la misma frontera de medicion que la columna ms_motor de #306; no es
  tiempo CPU puro ni el `time_ms` del plan. No se realiza un SELECT adicional
  solo para cronometrar.
- `ms_total`: desde que el endpoint empieza a leer el cuerpo hasta terminar
  deteccion, embedding e identificacion de todos los rostros. Incluye buscar
  nombres, pero no la transferencia de la respuesta al navegador. El tiempo
  percibido por el navegador puede ser mayor. No es intercambiable con las
  cifras historicas de #306, que consulta vectores precalculados.

Errores: 413 (tamano), 415 (tipo), 422 (`sin_rostro` o `imagen_invalida`),
429 (otro cuadro en proceso), 503 (modelos o motor no disponibles), 502
(consulta rechazada por el motor). Los errores propios incluyen `codigo` y
`error`; los de validacion de entrada incluyen `detail`.

YuNet y ArcFace son mutables: se protegen con un lock no bloqueante. Una
segunda inferencia simultanea recibe 429, no crece una cola de cuadros viejos.
Los modelos se cargan una vez al primer reconocimiento, deben estar
descargados antes con `python -m app.rostros.modelos`.

## GET /personas/{id}/foto

Consulta por HTTP una ruta del dataset de la persona y sirve esa foto local.
Nunca acepta una ruta arbitraria del navegador. Rechaza rutas absolutas,
escapes `..`, enlaces simbolicos fuera de `rikuq/datos` y formatos no
permitidos. Si la foto no esta en esta maquina devuelve 404.

Las respuestas de identificacion y fotos llevan Cache-Control: no-store.
Uso academico local: no hay autenticacion, asi que no exponer el servidor a
Internet o una red compartida con fotos biometricas.

## Pruebas sin modelos

```bash
cd rikuq/server
python -m pytest tests/test_api_identificar.py tests/test_main.py -q
```

PipelineFalso devuelve vectores sinteticos de 512 componentes y MockTransport
atiende el HTTP del motor. Una prueba bloquea los imports de OpenCV, numpy,
engine y quipudb_native para comprobar el arranque independiente.
