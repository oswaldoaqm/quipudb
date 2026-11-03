# ADR 0008 - Consultas multimedia por similitud

- **Fecha:** 2026-11-02
- **Estado:** Propuesta para revisión en el PR de #188
- **Issue:** #188

## Contexto

El core ya implementa k-NN exacto, IVF y HNSW; `engine/multimedia` transforma
objetos en histogramas TF-IDF. Faltaba conectarlos al SQL, al plan y a la
API sin confundir el vocabulario visual con los centroides de IVF.

## Decisión

- `SIMILAR_TO([columna,] 'archivo', [k=] entero) USING HNSW|IVF
  [WITH METRIC=euclidean|cosine|dot]` representa un top-k, no un predicado
  booleano por fila. Se resuelve una sola columna VECTOR y una sola tabla.
- El parser y la semántica permanecen puros. La extracción ocurre antes de
  ejecutar el acceso; EXPLAIN no la ejecuta.
- `QueryProcessor` recibe raíz y vocabularios por tabla/columna; la API los
  toma de `QUIPUDB_MEDIA_ROOT` y `QUIPUDB_MEDIA_VOCABULARIES`. Se reutiliza
  el `.npz` completo de la ingesta; no se reentrena al consultar.
- `CREATE INDEX ... USING HNSW [WITH METRIC=...]` delega al core con métrica
  fija. `USING IVF` entrena centroides sobre una muestra reservoir de hasta
  4096 histogramas, usando el K-Means existente, y delega su persistencia y
  construcción al core. No se cambia el formato del catálogo.
- La estructura pedida se respeta; si falta, se utiliza el k-NN exacto.
  Un HNSW existente pero de métrica incompatible produce un error ubicado,
  en vez de buscar incorrectamente en el mismo grafo.
- Se reutiliza `knn_search -> fetch` y se añade el campo `metric` al paso,
  conforme al ADR 0002: datos consumibles no se esconden en `detail`.
  `Structure` y el catálogo HTTP incorporan `ivf` y `hnsw`. El frontend
  identifica la búsqueda exacta por su estructura de tabla.

## Consecuencias y límites

IVF exige datos para entrenar; HNSW puede crearse vacío. Sus filas e índices
siguen usando DML, locks y undo existentes. Los índices aproximados no se
presentan como exactos y `ORDER BY` se aplica al top-k ya obtenido.

La configuración del vocabulario debe conservarse al reiniciar; aún no se
persiste su identidad por columna. La igualdad de dimensión no demuestra
igualdad de vocabulario/IDF. No se añaden JOIN, GROUP BY, DELETE por similitud,
un simulador vectorial en el frontend ni búsqueda HTTP asíncrona.

El contrato y el procedimiento reproducible están en
[consultas-multimedia.md](../consultas-multimedia.md). El aviso previo se
publicó en el issue antes de modificar las interfaces compartidas.
