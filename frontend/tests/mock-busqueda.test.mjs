import assert from 'node:assert/strict';
import { test } from 'node:test';
import path from 'node:path';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { createServer } from 'vite';

/** Un servidor por prueba: el catalogo falso es estado del modulo. */
async function conSimulador(prueba) {
  const cacheDir = await mkdtemp(path.join(tmpdir(), 'quipu-busqueda-'));
  const server = await createServer({ cacheDir, optimizeDeps: { noDiscovery: true, include: [] }, configFile: false, resolve: { alias: { '@': path.resolve(import.meta.dirname, '../src') } }, server: { middlewareMode: true, watch: null, ws: false } });
  try {
    const { mockExecuteQuery, mockListTables } = await server.ssrLoadModule('/src/api/mock/index.ts');
    await prueba(mockExecuteQuery, mockListTables);
  } finally {
    await server.close();
    await rm(cacheDir, { recursive: true, force: true });
  }
}

/** Los pasos en orden de ejecucion: hijos antes que el padre. */
const pasos = (raiz) => [...raiz.children.flatMap(pasos), raiz];
const ops = (resultado) => pasos(resultado.plan.root).map((p) => p.op);
const paso = (resultado, op) => pasos(resultado.plan.root).find((p) => p.op === op);
const columna = (resultado, nombre) => resultado.rows.map((fila) => fila[resultado.columns.indexOf(nombre)]);
const noCrece = (valores) => valores.every((v, i) => i === 0 || valores[i - 1] >= v);

async function falla(execute, sql, kind, mensaje) {
  await assert.rejects(execute(sql), (error) => {
    assert.equal(error.kind, kind, sql);
    assert.match(error.message, mensaje, sql);
    return true;
  });
}

test('el catalogo trae las tablas de texto y multimedia del enunciado', () => conSimulador(async (_, listar) => {
  const tablas = await listar();
  const indices = (nombre) => tablas.find((t) => t.name === nombre).indexes.map((i) => i.structure);
  assert.deepEqual(indices('documentos'), ['inverted_index']);
  assert.deepEqual(indices('articulos'), ['inverted_index']);
  assert.deepEqual(indices('imagenes'), ['hnsw', 'ivf']);
  assert.deepEqual(indices('canciones'), ['ivf']);
}));

test('las filas de esas tablas caben en sus VARCHAR y VECTOR', () => conSimulador(async (execute, listar) => {
  for (const tabla of (await listar()).filter((t) => ['documentos', 'articulos', 'imagenes', 'canciones'].includes(t.name))) {
    const r = await execute(`SELECT * FROM ${tabla.name}`);
    tabla.columns.forEach((c, i) => {
      for (const fila of r.rows) {
        if (c.type === 'VARCHAR') assert.ok(new TextEncoder().encode(fila[i]).length <= c.size, `${tabla.name}.${c.name}: ${fila[i]}`);
        if (c.type === 'VECTOR') assert.equal(fila[i].length, c.size, `${tabla.name}.${c.name}`);
      }
    });
  }
}));

test('MATCH ... USING TF_IDF LIMIT 10 corta el top-k en el indice', () => conSimulador(async (execute) => {
  const r = await execute("SELECT * FROM documentos WHERE MATCH(contenido, 'base de datos vectorial') USING TF_IDF LIMIT 10;");
  assert.deepEqual(r.columns, ['id', 'titulo', 'contenido']);
  assert.ok(r.rows.length > 0 && r.rows.length <= 10);
  assert.equal(r.rows[0][0], 1);
  assert.deepEqual(ops(r), ['text_search', 'fetch', 'limit']);
  const texto = paso(r, 'text_search');
  assert.equal(texto.structure, 'inverted_index');
  assert.equal(texto.ranking, 'tf_idf');
  assert.equal(texto.detail, "indice documentos_texto; MATCH(contenido, 'base de datos vectorial') USING TF_IDF; ranking tf_idf (elegido con USING); top-10; idioma spanish");
}));

test('SCORE() AS relevancia y ORDER BY relevancia DESC usan el orden del indice', () => conSimulador(async (execute) => {
  const r = await execute("SELECT *, SCORE() as relevancia FROM articulos WHERE MATCH(texto, 'machine learning') USING BM25 ORDER BY relevancia DESC;");
  assert.equal(r.columns.at(-1), 'relevancia');
  assert.equal(r.column_types.at(-1), 'DOUBLE');
  assert.ok(noCrece(columna(r, 'relevancia')));
  assert.ok(columna(r, 'texto').every((t) => /learning/i.test(t)));
  assert.deepEqual(ops(r), ['text_search', 'fetch', 'sort', 'project']);
  assert.equal(paso(r, 'text_search').ranking, 'bm25');
  assert.equal(paso(r, 'sort').detail, 'por relevancia DESC: el indice ya entrega este orden; sin external sort');
}));

test('sin USING el ranking es BM25 y SCORE() ASC invierte el orden sin top-k', () => conSimulador(async (execute) => {
  const r = await execute("SELECT id, SCORE() FROM documentos WHERE MATCH(contenido, 'indice') ORDER BY SCORE() ASC LIMIT 2");
  assert.deepEqual(r.columns, ['id', 'score']);
  assert.equal(r.rows.length, 2);
  assert.ok(r.rows[0][1] <= r.rows[1][1]);
  assert.match(paso(r, 'text_search').detail, /ranking bm25 \(por omision\); todos los que coinciden/);
  assert.equal(paso(r, 'sort').detail, 'por relevancia ASC: invierte en memoria el orden del indice; sin external sort');
  assert.deepEqual(ops(r).slice(-2), ['project', 'limit']);
}));

test('SIMILAR_TO con HNSW y coseno encuentra las fotos de la misma categoria', () => conSimulador(async (execute) => {
  const r = await execute("SELECT * FROM imagenes WHERE SIMILAR_TO('foto_consulta.jpg', k=10) USING HNSW WITH METRIC=cosine;");
  assert.equal(r.rows.length, 10);
  assert.equal(r.column_types.at(-1), 'VECTOR');
  assert.ok(columna(r, 'nombre').slice(0, 5).every((n) => n.startsWith('gato_')));
  assert.deepEqual(ops(r), ['knn_search', 'fetch']);
  const knn = paso(r, 'knn_search');
  assert.equal(knn.structure, 'hnsw');
  assert.equal(knn.metric, 'cosine');
  assert.equal(knn.detail, 'indice imagenes_hnsw; ef_search=64; k=10; metrica cosine');
}));

test('SIMILARITY_SCORE() euclidiano con ORDER BY score DESC invierte los vecinos', () => conSimulador(async (execute) => {
  const r = await execute("SELECT nombre, artista, SIMILARITY_SCORE() as score FROM canciones WHERE SIMILAR_TO('audio_query.mp3', k=5) USING IVF WITH METRIC=euclidean ORDER BY score DESC;");
  assert.deepEqual(r.columns, ['nombre', 'artista', 'score']);
  assert.equal(r.rows.length, 5);
  assert.ok(noCrece(columna(r, 'score')));
  const knn = paso(r, 'knn_search');
  assert.equal(knn.structure, 'ivf');
  assert.equal(knn.detail, 'indice canciones_ivf; nprobe=1; k=5; metrica euclidean');
  assert.equal(paso(r, 'sort').detail, 'SIMILARITY_SCORE DESC; valor natural euclidean; menor es mejor; invierte en memoria el orden de vecinos; sin external sort');
}));

test('sin un indice del tipo pedido la busqueda es exacta sobre la tabla', () => conSimulador(async (execute) => {
  const r = await execute("SELECT nombre FROM canciones WHERE SIMILAR_TO('audio/salsa_01.mp3', k=3) USING HNSW");
  assert.equal(r.rows[0][0], 'noche de malecon');
  const knn = paso(r, 'knn_search');
  assert.equal(knn.structure, 'heap');
  assert.equal(knn.detail, 'busqueda exacta; sin indice HNSW aplicable; k=3; metrica cosine');
}));

test('una tabla nueva con VECTOR se llena, se indexa y se busca con un vector literal', () => conSimulador(async (execute) => {
  const r = await execute(
    'CREATE TABLE emb (id INT PRIMARY KEY, v VECTOR(3)); ' +
    'INSERT INTO emb VALUES (1, VECTOR(1, 0, 0)); INSERT INTO emb VALUES (2, VECTOR(0.6, 0.8, 0)); ' +
    'CREATE INDEX emb_dot ON emb (v) USING HNSW WITH METRIC=dot; ' +
    'SELECT id, SIMILARITY_SCORE() AS s FROM emb WHERE SIMILAR_TO(VECTOR(0.8, 0.6, 0), k=2) USING HNSW WITH METRIC=dot',
  );
  assert.equal(r.affected_rows, 2);
  assert.deepEqual(columna(r, 'id'), [2, 1]);
  assert.ok(Math.abs(r.rows[0][1] - 0.96) < 1e-9);
  assert.equal(paso(r, 'knn_search').detail, 'consulta VECTOR literal de 3 componentes; indice emb_dot; ef_search=64; k=2; metrica dot');

  await falla(execute, 'SELECT id FROM emb WHERE SIMILAR_TO(VECTOR(0, 0, 1), k=1) USING HNSW', 'semantic', /^la metrica solicitada no coincide con la del indice HNSW/);
  await falla(execute, 'SELECT id FROM emb WHERE SIMILAR_TO(VECTOR(1, 0), k=1) USING IVF', 'semantic', /^columna v: el vector tiene 2 componentes y la columna declara VECTOR\(3\)$/);
  await falla(execute, "SELECT id FROM emb WHERE SIMILAR_TO('fotos/gato_01.jpg', k=1) USING IVF", 'semantic', /^falta configurar el vocabulario de 'emb'/);
}));

test('CREATE INDEX e INSERT rechazan lo que el motor rechaza sobre VECTOR', () => conSimulador(async (execute) => {
  await execute('CREATE TABLE vacia (id INT PRIMARY KEY, v VECTOR(2))');
  await falla(execute, 'CREATE INDEX vacia_ivf ON vacia (v) USING IVF', 'semantic', /^no se pudo crear el indice 'vacia_ivf': IVF necesita filas/);
  await falla(execute, 'CREATE INDEX vacia_bp ON vacia (v) USING BPLUS', 'semantic', /^la columna 'v' es VECTOR y se busca por similitud; BPLUS_UNCLUSTERED indexa por igualdad u orden$/);
  await falla(execute, 'CREATE INDEX vacia_id ON vacia (id) USING HNSW', 'semantic', /^IVF y HNSW requieren una columna VECTOR$/);
  await falla(execute, 'CREATE INDEX vacia_h ON vacia (v) USING HNSW WITH LANGUAGE=spanish', 'parse', /^se esperaba METRIC despues de WITH$/);
  await falla(execute, 'CREATE INDEX vacia_h ON vacia (v) USING HNSW WITH METRIC=manhattan', 'parse', /^la metrica debe ser euclidean, cosine o dot$/);
  await falla(execute, 'CREATE INDEX vacia_h ON vacia (v) USING GIST', 'parse', /HNSW o IVF despues de USING$/);
  await falla(execute, 'INSERT INTO vacia VALUES (1, 5)', 'semantic', /^columna v: se esperaba VECTOR y se recibio INT$/);
}));

test('los puntajes y las busquedas fallan con los mensajes del motor', () => conSimulador(async (execute) => {
  await falla(execute, 'SELECT codigo, SCORE() FROM alumnos WHERE codigo = 1000', 'semantic', /^SCORE\(\) es el puntaje de MATCH: la consulta necesita MATCH en el WHERE$/);
  await falla(execute, "SELECT SIMILARITY_SCORE() FROM documentos WHERE MATCH(contenido, 'x')", 'semantic', /^SIMILARITY_SCORE\(\) es el puntaje de SIMILAR_TO/);
  await falla(execute, "SELECT titulo FROM documentos WHERE MATCH(titulo, 'documento')", 'semantic', /^la columna 'titulo' no tiene un indice de texto; crealo con CREATE INDEX \.\.\. ON documentos \(titulo\) USING FULLTEXT$/);
  await falla(execute, "SELECT id FROM documentos WHERE MATCH(id, 'x')", 'semantic', /^MATCH requiere una columna VARCHAR; id es INT$/);
  await falla(execute, "SELECT id, SCORE() FROM documentos WHERE MATCH(contenido, 'x') ORDER BY titulo", 'unsupported', /^SCORE\(\) con ORDER BY sobre otra columna no esta soportado/);
  await falla(execute, "SELECT * FROM documentos WHERE MATCH(contenido, 'x') USING LSI", 'parse', /^se esperaba TF_IDF o BM25 como ranking de MATCH$/);
  await falla(execute, "SELECT * FROM imagenes WHERE SIMILAR_TO('foto_consulta.jpg', k=3)", 'parse', /^SIMILAR_TO requiere USING HNSW o IVF$/);
  await falla(execute, "SELECT * FROM imagenes WHERE SIMILAR_TO('foto_consulta.jpg', k=3) USING HNSW WITH METRIC=euclidean", 'semantic', /^la metrica solicitada no coincide/);
  await falla(execute, "SELECT * FROM imagenes WHERE SIMILAR_TO('../secreto.jpg', k=3) USING IVF", 'semantic', /^la ruta debe estar dentro de QUIPUDB_MEDIA_ROOT$/);
  await falla(execute, "SELECT * FROM imagenes WHERE SIMILAR_TO('nada.jpg', k=3) USING IVF", 'semantic', /^no se puede abrir la ruta/);
  await falla(execute, "DELETE FROM documentos WHERE MATCH(contenido, 'x')", 'unsupported', /^MATCH solo se admite en SELECT: un DELETE no borra por relevancia$/);
  await falla(execute, "DELETE FROM imagenes WHERE SIMILAR_TO('foto_consulta.jpg', k=1) USING HNSW", 'unsupported', /^SIMILAR_TO solo se admite en SELECT$/);
  // AS sigue fuera del subconjunto en cualquier otro lugar.
  await falla(execute, 'SELECT codigo AS c FROM alumnos', 'unsupported', /^AS no esta soportado/);
}));

test('EXPLAIN de una busqueda devuelve solo el plan', () => conSimulador(async (execute) => {
  const r = await execute("EXPLAIN SELECT id FROM documentos WHERE MATCH(contenido, 'indice invertido')");
  assert.equal(r.is_explain, true);
  assert.equal(r.rows.length, 0);
  assert.ok(paso(r, 'text_search'));
}));
