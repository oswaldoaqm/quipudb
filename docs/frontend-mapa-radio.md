# Visualización de búsquedas por radio — issue #125

Implementación sobre `feat/frontend-mapa-resultados`. No modifica el núcleo C++,
RTree, bindings, gramática ni planner. Leaflet/React-Leaflet y sus tipos ya estaban
instalados; no se agregan dependencias.

## Contrato y recorrido

`BoundDistanceCondition` → `spatial_context_of` → `QueryResult` →
`QueryResponse`/`to_response` → `App` → `MapPanel`/`SpatialRegion`.

Ejemplo de los campos nuevos, aditivos:

```json
{
  "spatial_context": {
    "kind": "radius",
    "table": "mapa125",
    "column": "ubicacion",
    "center": { "latitude": -12.0464, "longitude": -77.0428 },
    "radius": 1000,
    "metric": "HAVERSINE",
    "unit": "meters",
    "operator": "<="
  },
  "is_explain": false
}
```

- Sin condición espacial: `spatial_context: null`. Los clientes anteriores siguen
  recibiendo columnas, tipos, filas, filas afectadas y plan sin cambios.
- EUCLIDEAN usa `unit: "degrees"`; nunca se convierte a metros.
- Coordenadas: `{latitude, longitude}` → `[latitude, longitude]` en Leaflet.
- `table` es nulo en joins o agrupaciones: la región sigue siendo válida, pero no
  hay identidad de registros de una sola tabla garantizada en la salida.
- En lotes se conserva el contexto de la **última sentencia**, igual que sus filas.
- `is_explain` distingue EXPLAIN/EXPLAIN ANALYZE sin inspeccionar SQL o `Step.detail`.
  Ambos devuelven solamente el plan. El simulador ahora coincide con el backend.
- App guarda juntos ID de ejecución, SQL ejecutado y resultado. Editar SQL no
  cambia el resultado visible; iniciar una consulta limpia el anterior y se
  descartan respuestas y errores de ejecuciones anteriores.

## Capas y encuadre

- Azul: cada POINT válido de las filas devueltas, con su popup original.
- Gris: puntos base de la tabla. `useSpatialContext` los carga con `executeQuery`,
  seleccionando únicamente PK y columnas POINT. MapPanel no ejecuta SQL.
- Ámbar: referencia de la condición.
- Contorno azul: círculo Haversine, en metros; borde discontinuo para `<`.

El círculo define el encuadre Haversine; radio cero centra en la referencia.
Euclidean encuadra referencia más resultados e informa que no hay círculo
geográfico. Sin contexto se conserva el encuadre por puntos. La capa base nunca
participa del encuadre principal.

La cache comparte cargas en curso de la misma tabla/esquema/conteo/revisión,
se invalida tras escrituras locales y cargas CSV, y no conserva errores.
No detecta automáticamente cambios hechos desde otros clientes con idéntico
esquema y conteo; recargar la aplicación renueva la cache. La capa base carga
los POINT de toda la tabla; no incluye paginación ni clustering.

La identidad usa tabla + PK tipada + columna POINT. Nunca compara coordenadas
ni índices de fila entre capas. Si falta PK en la proyección, permanecen los
resultados y todos los puntos base, con un aviso; no se inventa una asociación.
Dos registros coincidentes siguen siendo distintos. Al superponerse sus iconos,
la tabla de resultados sigue mostrando ambos registros.

Con cero filas se conservan región/referencia actuales, sin marcadores anteriores,
y aparece `0 coincidencias`, incluso si la proyección no incluye POINT. Hay estados
separados para carga, error, EXPLAIN, ausencia de POINT y coordenadas inválidas.

`IndexStructure` acepta ahora `rtree`: previamente `/tables` fallaba al serializar
un catálogo con ese índice. La corrección se limita al Literal y está probada.

## Prueba manual reproducible

Desde `frontend`: `npm run dev`. Por defecto usa el simulador en memoria.
Para motor real, usar la API existente y arrancar con
`VITE_USE_MOCK=false VITE_API_URL=http://localhost:8000 npm run dev`.
La API actual admite CORS de desarrollo en el puerto 5173.

Ejecutar una vez en una sesión de prueba (usar otro nombre si ya existe la tabla):

```sql
CREATE TABLE mapa125 (id INT PRIMARY KEY, nombre VARCHAR(24), ubicacion POINT) USING HEAP;
INSERT INTO mapa125 VALUES (1, 'Centro', POINT(-12.0464, -77.0428));
INSERT INTO mapa125 VALUES (2, 'Cercano', POINT(-12.05, -77.04));
INSERT INTO mapa125 VALUES (3, 'Fuera del radio', POINT(-12.057, -77.045));
INSERT INTO mapa125 VALUES (4, 'Lejano', POINT(-16.3989, -71.5375));
CREATE INDEX mapa125_geo ON mapa125 (ubicacion) USING RTREE;
SELECT * FROM mapa125 WHERE distancia(ubicacion, POINT(-12.0464, -77.0428)) <= 1000;
```

Debe haber dos resultados azules, dos puntos base (alguno puede quedar fuera de
la vista), referencia ámbar y círculo de 1000 m. Abrir un marcador muestra la fila.

Después ejecutar por separado:

```sql
-- Un resultado, radio cero: referencia y zoom cercano, sin círculo.
SELECT * FROM mapa125 WHERE distancia(ubicacion, POINT(-12.0464, -77.0428)) <= 0;

-- Tres resultados: referencia y aviso de grados, sin círculo geográfico.
SELECT * FROM mapa125 WHERE distancia(ubicacion, POINT(-12.0464, -77.0428), EUCLIDEAN) <= 0.02;

-- Cero, sin POINT proyectado: conservar la NUEVA región, sin resultados azules.
SELECT id FROM mapa125 WHERE distancia(ubicacion, POINT(-12.06, -77.05)) <= 100;

-- Sin PK: resultados y capa base independientes, con aviso.
SELECT ubicacion FROM mapa125 WHERE distancia(ubicacion, POINT(-12.0464, -77.0428)) <= 1000;

-- Estado propio, sin puntos/región; el panel Plan continúa funcionando.
EXPLAIN SELECT * FROM mapa125 WHERE distancia(ubicacion, POINT(-12.0464, -77.0428)) <= 1000;

-- Sin contexto espacial: solo puntos, encuadrados como antes.
SELECT * FROM mapa125;
```

También editar el centro en el editor **sin ejecutar**: el mapa debe seguir
mostrando el resultado anterior. Ejecutarlo actualiza resultado, región y plan.

Para producción: `npm run build` y `npm run preview` (simulador por defecto).
Los PNG de Leaflet se importan explícitamente y funcionan con Vite.

## Verificación realizada

- `npm run typecheck`: correcto.
- `npm run test:spatial`: 16 pruebas; incluye las 5 existentes y nuevas pruebas
  de contexto, encuadre, identidad, cache, respuestas tardías y simulador.
- `npm run build`: correcto. Vite avisa del bundle mayor a 500 kB.
- Pytest `engine`: 874 pruebas correctas, con bindings nativos cargados.
- Pytest `benchmarks`: 179 pruebas correctas.
- Ruff sobre los archivos Python modificados/nuevos: correcto.
- No hay script de lint de frontend configurado.
- Verificación en navegador del build de producción con simulador: varios/uno/cero,
  Haversine/Euclidean, cambios de consulta, edición sin ejecución, sin PK, popup,
  EXPLAIN, error, vacío normal y diseño estrecho. API real cubierta por pytest.
- Advertencia del entorno Python: deprecación de httpx en Starlette TestClient.

## Alcance de los criterios del issue

1. Resultados distinguibles del resto: implementado cuando hay contexto de tabla;
   sin identidad fiable se mantienen capas independientes y se explica.
2. Región: círculo Haversine implementado. Euclidean deliberadamente sin conversión
   inventada. Polígonos pendientes de integración SQL/API de otro trabajo.
3. k-NN: referencia/k resultados pendientes de su contrato real; no se añadió UI falsa.
4. Centrado: implementado para los contextos actuales y fallback por POINT.
5. Sin resultados: implementado, sin visualización obsoleta.

Futuros k-NN/polígonos con columnas POINT ya podrán mostrar sus filas genéricamente.
Para dibujar sus regiones/referencias habrá que ampliar `SpatialContext`, el
adaptador de vista y `SpatialRegion` cuando exista metadata real.

## División propuesta en commits (sin ejecutar)

1. `feat(api): exponer contexto de busquedas por radio`:
   `engine/executor/{result,processor,spatial}.py`,
   `engine/api/{schemas,service,test_spatial_context}.py`.
2. `feat(frontend): asociar contexto espacial al resultado`:
   `src/api/{types.ts,mock/index.ts}`, `src/lib/query-execution.ts`,
   parte de `App.tsx` que maneja la ejecución.
3. `feat(frontend): mostrar region y referencia de busqueda por radio`:
   `src/components/SpatialRegion.tsx`, contexto/estado/encuadre en
   `src/lib/spatial.ts` y presentación de región/referencia en `MapPanel.tsx`.
4. `feat(frontend): distinguir puntos base y resultados`:
   `src/hooks/useSpatialContext.ts`, `src/lib/spatial-context.ts`, identidad en
   `src/lib/spatial.ts`, integración de base en `App.tsx` y `MapPanel.tsx`.
5. `test(frontend): cubrir contexto y resultado espacial`:
   `frontend/tests/{context,mock-spatial}.test.mjs`, `frontend/package.json`
   y este documento.

Los pasos 2–4 comparten archivos y requieren seleccionar bloques de diff al
preparar los commits. No se ha hecho staging, commit ni push.
