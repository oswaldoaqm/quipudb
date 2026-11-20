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
la imagen original), `ancho`, `alto`, `umbral` y `rostros`. Este ultimo contiene una
identificacion y una caja por rostro. Los campos principales describen el
primer rostro, salvo `ms_motor`, que suma las busquedas vectoriales.

- Desconocido: `persona`, `persona_id` y `foto` son null; confianza es cero.
- Base vacia: distancia es null, no Infinity. No se inventa una identidad.
- Confianza es el margen normalizado al umbral 0.55, **no una probabilidad**.
- `umbral` informa el limite usado para identificar todos los rostros de la
  respuesta. Es aditivo (#321): un cliente nuevo admite respuestas antiguas
  sin este campo y no supone un valor. `confianza` conserva su nombre y
  formula por compatibilidad; se presenta como **Margen al umbral**.
- Para distancia 0.4363 y umbral 0.55, el margen es
  `(0.55 - 0.4363) / 0.55 = 0.2067`, mostrado como 20.7 %. No expresa una
  probabilidad de acertar la identidad. En el limite una coincidencia puede
  estar aceptada con margen cero. Para desconocido la API conserva confianza
  cero, pero la interfaz muestra **No aplica**, no una probabilidad de 0 %.
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

## Registrar, listar y eliminar fotos (#342)

Es lo unico que escribe en la base desde la interfaz; `/identificar` sigue
sin guardar nada. El motor no tiene `UPDATE` ni `MAX` sin `GROUP BY`: el
siguiente id sale de `SELECT id ... ORDER BY id DESC LIMIT 1` y el conteo
`personas.fotos` se reescribe con `DELETE` + `INSERT` de la fila. Registrar y
eliminar se serializan con un lock para que dos peticiones no tomen el mismo id.

### POST /rostros?nombre=...&consentimiento=true

La foto va como en `/identificar` (campo `imagen`, hasta 8 MiB). Sin
`consentimiento=true` responde 422 `consentimiento_requerido` sin leer ni
guardar nada. El nombre se normaliza como las carpetas de LFW: los espacios
pasan a `_` ("Ana Quispe" queda `Ana_Quispe`), hasta 50 caracteres, solo
letras (con tildes y ñ), apostrofo, guion y punto. Si ya hay una persona con
ese nombre, sin distinguir mayusculas, la foto se suma a ella; si no, se crea
con `origen = 'subida'`. Si las tablas no existen, las crea con sus indices.

La foto tiene que tener **un solo rostro y fiable**. El archivo se guarda en
`rikuq/datos/subidas/<id>-<sufijo>.<ext>`, con la extension segun sus
primeros bytes, y el vector entra al HNSW con el `INSERT`. Si el motor
rechaza el `INSERT`, el archivo se borra.

```bash
curl -F 'imagen=@ana.jpg;type=image/jpeg' \
  'http://127.0.0.1:8002/rostros?nombre=Ana%20Quispe&consentimiento=true'
```

Respuesta 201: `id` (del rostro), `persona_id`, `persona`, `persona_nueva`,
`fotos` (cuantas tiene ahora) y `foto` (`/rostros/{id}/foto`). Errores 422:
`consentimiento_requerido`, `nombre_invalido`, `sin_rostro`,
`varios_rostros`, `rostro_pequeno`; 415 `imagen_invalida`; los de motor y
modelos son los mismos de `/identificar`.

### GET /rostros/subidos

Las fotos registradas asi, de la mas nueva a la mas vieja:
`[{id, persona_id, persona, foto}]`. Se reconocen por su ruta (`subidas/...`);
el motor no tiene `LIKE`, asi que se leen `id, persona_id, ruta` de `rostros`,
sin el vector, y se filtra en el servidor. Sin tablas devuelve `[]`.

### GET /rostros/{id}/foto

La imagen de un rostro, con las mismas protecciones de ruta que
`/personas/{id}/foto`.

### DELETE /rostros/{id}

Solo para fotos subidas: una del dataset responde 403 `no_permitido` (se
rehacen con `python -m app.carga.dataset`). Borra la fila (y su vector del
HNSW), el archivo y la persona si se queda sin fotos; si no, descuenta
`fotos`. Respuesta: `id`, `persona_id`, `persona`, `persona_eliminada`. Una
foto que no existe responde 404 `no_existe`.

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
