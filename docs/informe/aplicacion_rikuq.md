# Rikuq: aplicación de reconocimiento facial

Parte 5, opción D. Complementa Quipucamayoc, no lo reemplaza. Usa tablas
relacionales para personas y vectores para rostros; el navegador consume
Rikuq por HTTP y Rikuq consume exclusivamente el API REST del motor.

## Representación y acceso

YuNet detecta cajas y cinco landmarks; la alineación produce recortes
112×112 y ArcFace/MobileFaceNet genera 512 floats, normalizados a norma uno.
No se utilizan los histogramas SIFT de la Parte 4 para reconocer identidades.

Personas ocupa 118 bytes por fila y rostros 2 256, dentro del máximo de
4 087 del heap. Solo cabe **un** registro de rostro por página de 4 KiB.
El [esquema reproducible](../rikuq-esquema.md) documenta columnas e índices.
HNSW/coseno permite cargar incrementalmente sin entrenar centroides de IVF;
esto es una elección de este flujo, no una superioridad universal de HNSW.

## Decisión de identidad

Se aplica distancia máxima 0,55, tomada del
[barrido de calibración](../rikuq-umbral.md), no un número escogido en la UI.
Una distancia mayor produce Desconocido aunque siempre exista algún vecino.
La confianza expresa margen al umbral, no una probabilidad calibrada.

## Cifras históricas y lo que miden

La [evaluación #306](rikuq_evaluacion.md) reporta 30 aciertos entre 32
consultas conocidas (93,8 %), dos rechazos y ninguna confusión; hubo cero
aceptaciones entre 351 consultas ajenas. Es evidencia de esa corrida LFW,
no una garantía de cero falsos positivos futuros.

Mediana de HTTP de consulta vectorial: **14,73 ms**, p95 18,48 ms. La columna
histórica «Vuelta completa» es **37,97 ms**, p95 47,45 ms, pero el script usa
vectores precalculados y ejecuta una consulta cronometrada seguida de la
identificación: **no mide extracción ni el recorrido de la cámara**. No se
atribuyen esos 37,97 ms al endpoint nuevo ni se vuelven a generar cifras.

En POST /identificar se miden por separado:

- ms_motor: ida/vuelta HTTP de los SELECT vectoriales, sin consultas de nombres;
- ms_total: lectura, detección, embedding e identificación completas en Rikuq;
- ambos excluyen el envío final de la respuesta al navegador; la experiencia
  del usuario puede tardar más. Con varios rostros, ms_motor suma búsquedas.

## Interfaz y seguridad del flujo

React/Vite ofrece subir/arrastrar imagen o capturar cámara con permiso
explícito. Muestra cajas, nombre o Desconocido, foto registrada, distancia,
margen y tiempos. No implementa detección en JavaScript. El servidor lee
multipart incrementalmente en memoria; no persiste fotos de consulta.

La cámara inicia como máximo tres cuadros/s y espera cada respuesta antes
de enviar otro. Apagar, cambiar de modo o retirar permiso cancela la captura.
La estabilización de nombres se basa en cajas solapadas y no permite que un
desconocido herede una identidad anterior.

No hay Redis ni autenticación: uso local académico. La decisión de diferir
caché, sus costos de invalidación y la privacidad están en el
[ADR 0011](../adr/0011-aplicacion-rikuq.md). El consentimiento no se presume
por pertenecer al equipo y no se publican fotos ni vectores.

## Verificación y evidencia pendiente

CI tiene un job independiente con Ruff, pytest, npm test y build. La API se
prueba con pipeline inyectable, sin modelos. Las pruebas reales de LFW/ONNX
declaran sus recursos faltantes como omisiones; no cuentan como ejecutadas.

Faltan la validación manual con navegador/cámara física, capturas y el video
real con caso Desconocido de #312. No se sustituyen por imágenes o resultados
simulados. Instrucciones y lista de comprobación en
[rikuq/README.md](../../rikuq/README.md).
