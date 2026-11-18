GATE: ERROR — el tercer proceso terminó con código 2 sin emitir veredicto final.

# Revisión de la interfaz de Rikuq · #335

Fecha: 2026-11-18. No se declara `GATE: PASS`. La última ronda completa
fue `GATE: FAIL 27 (round 2 of 3)`; la tercera produjo un informe parcial de
consistencia, pero no actualizó el estado agregado. No se repitió el gate
ni se modificó el verificador para obtener una aprobación.

## Alcance y dirección

Se usaron `frontend-design` y `design-taste-frontend` para jerarquía,
tipografía, contraste, estados, controles y revisión con Chrome aislado.
Es una **aplicación operativa**, no una página comercial: consentimiento,
entrada y resultado deben permanecer próximos. Se mantuvo React/Vite y el
contrato HTTP; no cambiaron modelos, umbral, índices ni datos del motor.

El sorteo de la skill propuso `cool-grey-cobalt`, Epilogue + JetBrains Mono,
radios suaves, hero `split` y momento `line-carries-colour`. Se aplicaron la
paleta y las fuentes locales: neutros para trabajar, azul para acciones y
monoespaciada para métricas. El marco de escaneo une marca y estado vacío.
Se conservó el espacio de trabajo compacto en lugar del hero y del momento
de scroll. No hay escena, shader, fotografías de stock ni identidades de
demostración en producción. La capa de microinteracciones de la skill se
copió sin modificar; contempla la preferencia de movimiento reducido.

Voz: instrucciones técnicas directas en español, una acción concreta y un
siguiente paso ante el error. Rikuq es el nombre existente: no se inventaron
marca, personas, cifras de reconocimiento ni hechos comerciales. No hay
formulario comercial, dominio canónico, contactos `.example`, fotografías
pendientes del cliente ni material de relleno.

## Cambios comprobables

- Consentimiento explícito, inicialmente desmarcado y no persistido.
- Pestañas con roles, selección y navegación por flechas/Inicio/Fin;
  enlace para saltar al contenido y foco visible.
- Selección, cambio y retirada de imagen; cancelación de una consulta con
  `AbortController`. Las respuestas anteriores se ignoran.
- Ayuda visible para las acciones deshabilitadas, estados vacíos y errores.
- Cámara inicialmente apagada; se conserva su ciclo de cierre, cadencia y
  ausencia de audio. No se activa por cambiar de pestaña.
- Distancia sin recortar a 0–1, umbral recibido del servidor, margen que
  **no** es probabilidad, dos tiempos separados y aviso opcional de baja
  resolución. Desconocido no muestra una fotografía de referencia.
- `/health` se presenta como disponibilidad de la **API de Rikuq**, no del
  motor/modelos/dataset; permite reintentar.
- Fuentes y recursos locales, sin solicitudes de fuentes a terceros.

## Verificaciones ejecutadas

Desde `rikuq/web`:

```bash
npm test
node --experimental-strip-types tests/resultado.test.mjs
npm run build
```

Pasaron los tres archivos de pruebas de interfaz/API/cámara; el archivo de
renderizado ejecutó 18 casos, diez nuevos. El renderizado usa componentes
reales con Vite en modo middleware, sin escuchar HTTP/WebSocket ni acceder
a la cámara. TypeScript y la compilación de producción pasaron. Desde la
raíz también pasaron `git diff --check` y
`.venv/bin/ruff check engine benchmarks tests`. No se modificó el servidor
de Rikuq ni se presenta esto como una nueva evaluación del reconocimiento.

Se inspeccionaron capturas de escritorio, tablet y móvil con perfiles
temporales de Chrome de la skill. La matriz de layout incluyó 1440×900,
1024×768, 820×1180, 768×1024 y 390×844; no informó desbordamiento horizontal.
También se revisó una captura de 320 px. Se corrigieron contraste, escala
de títulos, alineación superior y un recorte del estado vacío a 1024 px;
este último se volvió a capturar y revisar.

La medición de producción en el preview local, tres repeticiones a 1440 y
390 px, pasó con 0 FAIL/0 WARN: aproximadamente 131 KiB transferidos,
0 solicitudes rotas, 0 tareas largas y CLS 0. Los valores medianos de
FCP/LCP fueron 164 ms en escritorio y 208 ms en móvil. Son datos de esta
máquina en la carga inicial, **no** latencias de reconocimiento. La medición
precede al último ajuste de altura automática del estado vacío; no se
afirma que sea una medición de ese ajuste final.

## Hallazgos abiertos del gate

No se aplicaron waivers (0). Los 27 hallazgos de la última ronda agregada
completa se enumeran abajo; permanecen visibles en el informe, no se
presentan como corregidos ni como una aprobación automática. Los criterios
de landing se apartan del objetivo funcional de esta aplicación.

| # | Regla y ubicación | Motivo para conservar la composición |
|---|---|---|
| 1 | `hero-fill`, consentimiento visible al cargar | El permiso forma parte de la tarea inicial. |
| 2 | `flush`, texto introductorio | Se conserva una introducción compacta y separación propia de controles. |
| 3 | `cta`, introducción sin botón | La acción está junto a su imagen y requiere permiso/archivo. |
| 4 | `moment-none`, página | Un momento de scroll desplazaría la tarea sin aportar función. |
| 5 | `hero-screen`, introducción corta | No se dedica una pantalla a presentar una herramienta ya abierta. |
| 6 | `hero-screen`, consentimiento en primera pantalla | Es información necesaria antes de procesar. |
| 7 | `section-height`, pie/barra/consentimiento cortos | Son controles y avisos, no capítulos de 900 px. |
| 8 | `section-height`, panel empieza fuera de la cuadrícula de pantallas | El flujo sigue a los controles, no al múltiplo del viewport. |
| 9 | `section-air`, barra–panel | No se agregan 128–480 px entre modo y entrada. |
| 10 | `section-air`, consentimiento–barra | Se conserva proximidad entre permiso y acción. |
| 11 | `one-section`, varios bloques en primera pantalla | Se necesita ver entrada y contexto de resultado juntos. |
| 12 | `one-section`, consentimiento/barra con vecinos visibles | Son partes del mismo flujo operativo. |
| 13 | `one-section`, `div#recognition-panel`/pie | El contenedor tiene semántica `tabpanel`; el pie no es un capítulo. |
| 14 | `one-heading`, dos encabezados en el workspace | Entrada y resultado necesitan títulos independientes. |
| 15 | `container-edges`, contenido interior del consentimiento | Se respeta el padding y el espacio del icono/checkbox. |
| 16 | `container-edges`, `div#root` | Es el punto de montaje de React, no una sección comercial. |
| 17 | `container-edges`, hijos de introducción | El contenedor común está en `main`; título y ayuda son controles distintos. |
| 18 | `container-edges`, título del resultado | Está dentro del panel derecho, no en el borde de la página. |
| 19 | `container-edges`, nombre de archivo | Se alinea con su icono dentro de la barra del archivo. |
| 20 | `container-edges`, consejo de imagen | Se conserva el sangrado del icono de ayuda. |
| 21 | `split-pair`, proporción de columnas | Se reserva mayor ancho para la imagen; no se fuerza una cuadrícula editorial de doce columnas. |
| 22 | `flush`, pestañas móviles | La barra es un control con padding propio, no un cambio de capítulo. |
| 23 | `flush`, final del panel móvil | El resultado termina dentro de su tarjeta antes del pie. |
| 24 | `edge-left`, consejo de imagen | El icono precede al texto y explica el sangrado. |
| 25 | `edge-left`, nota del pie | Comparte una fila adaptable con marca y herramientas. |
| 26 | `screen-short`, introducción | Se conserva el resumen breve para llegar a la tarea. |
| 27 | `hero-fold`, introducción | No hay hero de pantalla completa ni acción comercial. |

El informe parcial del tercer intento también señaló `nesting` en
`dropzone > stage > stage-empty`, y `container-edges` en el título centrado
y tipos de archivo del estado vacío. Esa estructura separa marco, preview,
overlay y contenido; centrar las instrucciones dentro del área de imagen
es intencional. También desglosa por separado las reglas `section-height`
para barra/consentimiento y `one-section` para la barra, agrupadas en la
ronda anterior. No hay un recuento agregado final válido de ese intento.

El mismo informe parcial no encontró problemas de overflow, colisiones o
tamaño táctil en el recorrido de 320×640, ni hallazgos de contraste en las
dos anchuras principales. No equivale a una auditoría de accesibilidad.

Los avisos de copia de consistencia se conservan por estos motivos:
`copy-buttons` no reconoce «Elegir»/«Identificar» como verbos españoles;
`copy-actions` espera repetir la acción en un hero/nav que aquí no existe;
`copy-length` cuenta instrucciones de UI como texto comercial; la ronda
anterior también marcó `copy-headings` en el título del estado vacío, que
sí nombra su función. No se informaron WARN del linter estático de copia.
Otros WARN afectan al padding, tarjetas que siguen debajo del primer
viewport, centrado de leyendas con iconos, distancias entre controles y
escala de títulos. El aviso `skip-link` no reconoce el texto español;
el enlace real apunta a un `main` enfocable. `share-meta` queda pendiente:
no se inventó una URL pública ni una imagen social para una demo local.

## Límites y comprobación manual pendiente

El tercer gate terminó antes de integrar todos los verificadores. Su modo
rápido tampoco verifica recorridos de scroll/reveal, hover, pausa de loops,
formularios ni una auditoría completa de movimiento reducido. No se
declaran comprobados esos comportamientos. No se activó la cámara física,
no se enviaron fotos personales y no se grabó evidencia del reconocimiento.
Los atributos de teclado están cubiertos por SSR y revisión del código;
queda probar el recorrido interactivo con teclado y lector de pantalla.

Antes del merge, revisar manualmente consentimiento, selección/cambio/
retirada de imagen, cancelación durante una petición, reintento de conexión,
permiso de cámara denegado, encendido/apagado y retirada de consentimiento.
Para validar reconocimiento real deben usarse únicamente imágenes con
consentimiento. Estas capturas de UI vacía **no cierran #312**.
