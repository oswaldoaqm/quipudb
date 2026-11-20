# Rikuq

Aplicación académica de reconocimiento facial de la Parte 5, opción D.
Combina datos relacionales (`personas`) y multimedia (`rostros`) en QuipuDB.
Una foto se procesa con YuNet y ArcFace/MobileFaceNet; HNSW busca embeddings
de 512 componentes y el umbral decide una identidad o **Desconocido**.

La interfaz tiene subida de imagen y cámara en vivo. El navegador no ejecuta
modelos: manda la imagen a Rikuq y recibe resultados, cajas y tiempos. Rikuq
solo habla con el motor por HTTP; nunca importa sus módulos internos.

## Estado y evidencia

- #308: esqueleto FastAPI/React/Vite y job propio de CI.
- #309: API de identificación con pipeline inyectable y dos tiempos.
- #310: elegir/arrastrar imagen, vista previa, referencias y resultados.
- #311: cámara, máximo tres cuadros/s, una petición activa y apagado.
- #307: [ADR 0011](../docs/adr/0011-aplicacion-rikuq.md).
- **#312 sigue pendiente** de video real y capturas con autorización de las
  personas. No se incluyen capturas inventadas ni se presenta un pipeline
  falso como evidencia del reconocimiento.

La comprobación automática cubre contratos y lógica. No reemplaza verificar
manualmente el flujo del navegador, la cámara física y el dataset real.

## Requisitos

Python 3.11+, Node 22+, CMake y compilador C++20 para el motor. La instalación
completa está en el [README principal](../README.md#manual-de-instalación).
Son **tres procesos**:

| Proceso | Directorio | Puerto |
|---|---|---:|
| API del motor (propia de Rikuq) | raíz del repo | 8004 |
| API de Rikuq | rikuq/server | 8002 |
| Interfaz de Rikuq | rikuq/web | 5175 |

El motor de Rikuq va en el 8004 para correr a la vez que el motor general
(8000), Quipucamayoc y el distribuido; la tabla de todos los puertos está en
[docs/puertos.md](../docs/puertos.md).

### 1. Motor: terminal desde la raíz

Si no existe el build, configúralo primero:

```bash
cmake -S . -B build-py -DCMAKE_BUILD_TYPE=Release -DQUIPUDB_BUILD_PYTHON=ON
```

Con el motor apagado, recompila los bindings. Deben corresponder al mismo
Python que ejecutará el motor; Windows bloquea el .pyd si está cargado.

```bash
cmake --build build-py --parallel
python -m pip install -r requirements.txt
```

Linux/macOS:

```bash
export PYTHONPATH="$PWD/build-py/bindings:$PWD"
export QUIPUDB_CATALOG=datos/rikuq/catalogo.txt
python -m uvicorn engine.api.main:app --host 127.0.0.1 --port 8004
```

PowerShell, también desde la raíz:

```powershell
$env:PYTHONPATH = "$PWD\build-py\bindings;$PWD"
$env:QUIPUDB_CATALOG = "datos/rikuq/catalogo.txt"
python -m uvicorn engine.api.main:app --host 127.0.0.1 --port 8004
```

El catálogo separado evita mezclar la demo con otras bases del equipo. No
arranques el motor desde rikuq/server: no encontraría el paquete engine.

### 2. Servidor de Rikuq: segunda terminal

Desde la raíz, prepara la configuración local (no se versiona):

```bash
cp rikuq/.env.example rikuq/.env
cd rikuq/server
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

PowerShell: `Copy-Item rikuq/.env.example rikuq/.env` y, después de crear el
entorno, usa `.venv\Scripts\python` en lugar de `python` si no está activado.

Descarga explícitamente los modelos y prepara los datos antes de consultar:

```bash
python -m app.rostros.modelos
python -m app.dataset.lfw descargar
python -m app.dataset.lfw manifiesto --minimo 10
python -m app.dataset.lfw verificar
python -m app.carga.dataset --limite 200
```

El límite sirve para una primera prueba. Sin `--limite` se carga el
manifiesto completo. **`--rehacer` borra personas y rostros**: no lo uses si
no quieres reconstruir esas tablas. La aplicación no lo ejecuta sola.

Para fotos del equipo, completa el consentimiento de
[dataset/README.md](dataset/README.md) antes de ponerlas en
`rikuq/datos/equipo/Nombre_Apellido/`; después regenera y verifica el
manifiesto. No subas esas imágenes a Git ni rellenes permisos por terceros.

Arranca el servidor:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8002 --env-file ../.env
```

`http://127.0.0.1:8002/health` solo confirma que Rikuq está vivo; no implica
que el motor, los modelos o la base estén listos.

### 3. Interfaz: tercera terminal

Desde la raíz:

```bash
cd rikuq/web
npm ci
npm run dev
```

Abre <http://127.0.0.1:5175> (o <http://localhost:5175>: Vite escucha en
la IP y el navegador la prueba si `localhost` no responde por IPv6). Vite
envía /api al servidor local en 8002; los puertos de los otros frontends
quedan libres.

## Variables

| Variable | Predeterminado | Uso |
|---|---|---|
| QUIPUDB_URL | http://127.0.0.1:8004 | API del motor |
| QUIPUDB_API_URL | alias anterior | Se usa si QUIPUDB_URL está vacía |
| QUIPUDB_TIMEOUT_S | 60 | Timeout HTTP hacia el motor |
| RIKUQ_WEB_ORIGINS | localhost:5175 y 127.0.0.1:5175, con http:// | Orígenes CORS separados por comas |
| VITE_RIKUQ_API_URL | /api | URL de Rikuq para el navegador; configuración en web/.env |

`--env-file ../.env` carga las variables para uvicorn. Los comandos CLI leen
el entorno del proceso: si cambias el destino del motor, exporta también
QUIPUDB_URL antes de cargar el dataset. Usa la IP local para evitar el intento
IPv6 de localhost en Windows.

## Dónde están las fotos y cuáles se cargaron

Desde la raíz del repositorio, los originales están en:

```text
rikuq/datos/lfw/<Nombre_Apellido>/<foto>.jpg
rikuq/datos/equipo/<Nombre_Apellido>/<foto>.jpg
```

Por ejemplo, las fotos descargadas de Adrien Brody están en
`rikuq/datos/lfw/Adrien_Brody/`. El archivo
`rikuq/dataset/rostros.tsv` es un manifiesto de imágenes disponibles, **no una
lista de filas insertadas**: una carga con `--limite 200` solo procesa las
primeras 200 imágenes y las que no dan un rostro utilizable se omiten.

La fuente de verdad es el catálogo abierto por la API del motor. Con el
motor encendido, estas consultas de solo lectura enumeran personas y rutas:

```bash
curl -sS http://127.0.0.1:8004/query \
  -H 'Content-Type: application/json' \
  -d '{"sql":"SELECT id, nombre, fotos FROM personas ORDER BY nombre;"}' \
  | python3 -m json.tool

curl -sS http://127.0.0.1:8004/query \
  -H 'Content-Type: application/json' \
  -d '{"sql":"SELECT persona_id, ruta FROM rostros ORDER BY persona_id LIMIT 20;"}' \
  | python3 -m json.tool
```

Las rutas de `rostros` son relativas a `rikuq/datos/`. Para una persona
concreta, usa `WHERE persona_id = 2` con el id obtenido en la primera
consulta; no supongas que siempre tiene ese id. La foto que muestra la UI es
una referencia de esa persona, no una galería de todas sus fotos ni
necesariamente el vector que produjo la mejor distancia.

Si `personas` no existe, falta cargar el esquema en ese catálogo; si no hay
filas, no hay identidades cargadas. Tener una carpeta descargada no hace que
la aplicación la conozca. Subir una foto desde la interfaz tampoco registra
personas: solo realiza una consulta. No uses `--rehacer` solo para listar las
fotos, porque borra las tablas.

## Usar la demo

1. Confirma que tienes permiso para procesar las imágenes de las personas.
2. En **Subir imagen**, elige o arrastra un JPEG, PNG o WebP de hasta 8 MiB.
3. Pulsa **Identificar rostro**. Verás nombre o Desconocido, distancia coseno,
   margen al umbral y foto de referencia si está en esta máquina.
4. En **Cámara en vivo**, pulsa **Encender cámara** y acepta el permiso del
   navegador. No se solicita audio. **Apagar cámara**, cambiar de pestaña o
   retirar el consentimiento cierra la captura.

El permiso empieza desmarcado en cada sesión. La interfaz explica por qué
una acción está deshabilitada; puedes **Cambiar imagen**, **Quitar imagen**
o **Cancelar análisis** sin esperar la respuesta anterior. Las pestañas
admiten las flechas izquierda/derecha y las teclas Inicio/Fin; los controles
mantienen foco visible y el diseño apila entrada y resultado en móvil.

El indicador **API de Rikuq disponible** solo comprueba `/health`: no
certifica que el motor, el dataset o los modelos estén listos. Si falla,
ofrece **Reintentar conexión**. Un aviso de rostro pequeño se muestra solo
cuando el servidor informa `fiable: false`; no modifica el umbral.

La cámara necesita localhost o HTTPS. Si se niega el permiso o no existe una
cámara, puedes seguir usando la subida. El envío se limita a tres cuadros/s,
sin peticiones solapadas. Un nombre nuevo se confirma durante tres cuadros
para reducir parpadeos; Desconocido nunca conserva el nombre anterior.

Un archivo sin rostro produce 422 con motivo. Una base vacía no inventa una
identidad. Modelo ausente, motor apagado, imagen inválida y archivo grande
tienen mensajes distintos. Si el servidor está ocupado devuelve 429 y no
acumula cuadros antiguos.

## Resultados y límites

El umbral de distancia coseno es **0,55**, calibrado para el modelo ArcFace
actual: [procedimiento](../docs/rikuq-umbral.md). La interfaz muestra el
**Margen al umbral**, no una probabilidad ni un porcentaje de acierto. El
campo `confianza` del API conserva su nombre por compatibilidad.

Ejemplo: distancia 0,4363 y umbral 0,55 dan
`(0,55 - 0,4363) / 0,55 × 100 ≈ 20,7 %`. Ese porcentaje mide la separación
normalizada respecto al límite, no significa «20,7 % seguro». Una distancia
menor tiene más margen. Una coincidencia justo en el límite tiene margen
cero y puede estar aceptada. Para Desconocido o sin candidatos se muestra
**No aplica**. El campo aditivo `umbral` viene del servidor, no se fija en
JavaScript; si un servidor anterior no lo informa, se indica explícitamente.

La interfaz separa **HTTP motor** de **servidor total**. El primero mide la
consulta vectorial con su ida y vuelta local; el segundo incluye detección,
embedding y nombres. El navegador puede tardar más por transferencia y
codificación. No se mezclan estos tiempos ni se llama CPU puro a ms_motor.

La evaluación histórica #306 reporta 30/32 conocidos identificados, 0/351
intrusos aceptados y mediana de búsqueda de 14,73 ms. No se ha repetido en
esta implementación de la interfaz ni garantiza el rendimiento de cámara.
Ver [informe](../docs/informe/aplicacion_rikuq.md) y
[fronteras del benchmark](../docs/informe/rikuq_evaluacion.md).

No hay Redis: la razón y la limitación quedan registradas en el ADR. Tampoco
hay autenticación; **no exponer estos servidores a Internet**. Fotos de
consulta y cuadros solo se procesan en memoria, no se guardan ni se envían a
terceros. Las fotos de referencia se sirven desde el dataset local con
Cache-Control: no-store. Esto no garantiza ausencia de memoria virtual o
cachés ajenas al proceso; es una política de la aplicación, no una garantía
de borrado forense.

## Pruebas

Servidor, desde rikuq/server:

```bash
python -m ruff check .
python -m pytest -q -rs -p no:cacheprovider
```

Interfaz, desde rikuq/web:

```bash
npm test
npm run build
```

Las pruebas de renderizado verifican los componentes reales sin cámara ni
modelos: consentimiento, estados vacíos y de análisis, etiquetas accesibles,
distancias, umbral y ausencia de una referencia para Desconocido. La revisión
visual y sus límites están en [QA de la interfaz #335](../docs/rikuq-interfaz-qa.md).

El job **Probar Rikuq (servidor e interfaz)** ejecuta ambos bloques. Las
pruebas de API usan PipelineFalso y MockTransport, sin ONNX ni motor real.
La suite también incluye casos reales que se omiten cuando faltan modelos,
LFW o bindings compatibles; `-rs` muestra cada omisión. Las dos pruebas de
carga CLI crean su propio proceso de motor con puerto efímero y catálogo
bajo tmp_path: **no utilizan el servidor ni el catálogo del entorno del
usuario**, aunque haya un motor encendido (#322). No se afirma que todos
esos casos se ejecuten en CI.

## Pendientes de #312: capturas y video

No hay todavía capturas verificadas de estas pantallas ni un video de la
demo real. Para cerrar el issue:

1. Usa fotos y cámara de personas que hayan consentido; evita otros rostros
   en el fondo y no expongas nombres o rutas que no deban publicarse.
2. Graba una subida reconocida y muestra la foto de referencia, distancia y
   los dos tiempos; después, una imagen autorizada de alguien no cargado
   que aparezca como **Desconocido**.
3. Muestra encendido de cámara, cajas/nombres y apagado; comprueba que el
   indicador de uso de cámara se apague.
4. Incluye capturas de los dos modos y enlaza el video revisado desde aquí.

Hasta tener esa evidencia y validación manual, #312 no está terminado.
