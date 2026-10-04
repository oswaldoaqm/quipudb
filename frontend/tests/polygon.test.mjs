import assert from 'node:assert/strict';
import { test } from 'node:test';
import path from 'node:path';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { createServer } from 'vite';
import { basePointsOutsideResults, containsPoint, mapMessage, polygonContext, searchContext, spatialView } from '../src/lib/spatial.ts';

// Forma de L, no convexa: el hueco cae dentro de la caja pero fuera del poligono.
const L = [[-12.20, -77.20], [-12.20, -76.90], [-12.10, -76.90], [-12.10, -77.05], [-11.90, -77.05], [-11.90, -77.20]]
  .map(([latitude, longitude]) => ({ latitude, longitude }));
const context = (extra = {}) => ({ kind: 'polygon', table: 'tiendas', column: 'p', vertices: L, ...extra });
const result = (rows = [], extra = {}) => ({ columns: ['id', 'p'], column_types: ['INT', 'POINT'], rows, affected_rows: 0, plan: null, spatial_context: context(), ...extra });
const point = (latitude, longitude) => ({ latitude, longitude });

test('containsPoint decide el borde y los poligonos no convexos como el core', () => {
  assert.equal(containsPoint(L, point(-12.15, -77.10)), true);
  assert.equal(containsPoint(L, point(-12.00, -77.10)), true);
  assert.equal(containsPoint(L, point(-12.00, -76.95)), false); // hueco de la L
  assert.equal(containsPoint(L, point(-12.20, -77.00)), true); // arista
  assert.equal(containsPoint(L, point(-12.10, -77.05)), true); // vertice concavo
  assert.equal(containsPoint(L, point(-12.30, -77.10)), false);
  assert.equal(containsPoint(L.slice(0, 2), point(-12.2, -77.2)), false);
});

test('el poligono valido define la region y el encuadre, incluso sin filas', () => {
  const r = result([], { columns: ['id'], column_types: ['INT'] });
  assert.equal(mapMessage(r, false, false), null);
  assert.deepEqual(searchContext(r), context());
  assert.deepEqual(spatialView(polygonContext(r), []), { kind: 'polygon', vertices: L.map(v => [v.latitude, v.longitude]) });
});

test('rechaza poligonos invalidos y no los muestra en EXPLAIN', () => {
  assert.equal(polygonContext(result([], { spatial_context: context({ vertices: L.slice(0, 2) }) })), null);
  assert.equal(polygonContext(result([], { spatial_context: context({ vertices: [...L.slice(0, 2), { latitude: 99, longitude: 0 }] }) })), null);
  assert.equal(polygonContext(result([], { is_explain: true })), null);
});

test('la capa base excluye los resultados del poligono por identidad de tabla', () => {
  const table = { name: 'tiendas', storage: 'heap', indexes: [], record_count: 2, columns: [
    { name: 'id', type: 'INT', is_primary_key: true, size: null }, { name: 'p', type: 'POINT', is_primary_key: false, size: null },
  ] };
  const base = { columns: ['id', 'p'], column_types: ['INT', 'POINT'], rows: [[1, point(-12.15, -77.1)], [2, point(-16.4, -71.5)]], affected_rows: 0, plan: null };
  const { points, hasIdentity } = basePointsOutsideResults(base, result([[1, point(-12.15, -77.1)]]), table);
  assert.equal(hasIdentity, true);
  assert.deepEqual(points.map(p => p.row[0]), [2]);
});

test('el simulador resuelve DENTRO con y sin R-Tree y expone el poligono', async () => {
  const cacheDir = await mkdtemp(path.join(tmpdir(), 'quipu-polygon-test-'));
  const server = await createServer({ cacheDir, optimizeDeps: { noDiscovery: true, include: [] }, configFile: false, resolve: { alias: { '@': path.resolve(import.meta.dirname, '../src') } }, server: { middlewareMode: true, watch: null, ws: false } });
  const ops = root => [...root.children.flatMap(child => ops(child)), root.op];
  const poly = 'POLYGON(' + L.map(v => `POINT(${v.latitude}, ${v.longitude})`).join(', ') + ')';
  try {
    const { mockExecuteQuery: execute } = await server.ssrLoadModule('/src/api/mock/index.ts');
    await execute(`CREATE TABLE polygon_test (id INT PRIMARY KEY, p POINT) USING HEAP;
      INSERT INTO polygon_test VALUES (1, POINT(-12.15, -77.1));
      INSERT INTO polygon_test VALUES (2, POINT(-12.0, -76.95));
      INSERT INTO polygon_test VALUES (3, POINT(-12.2, -77.0));`);
    const q = `SELECT * FROM polygon_test WHERE DENTRO(p, ${poly})`;
    const scan = await execute(q);
    assert.deepEqual(scan.rows.map(r => r[0]).sort(), [1, 3]);
    assert.deepEqual(ops(scan.plan.root), ['scan', 'filter']);
    assert.equal(scan.spatial_context.kind, 'polygon');
    assert.equal(scan.spatial_context.table, 'polygon_test');
    assert.equal(scan.spatial_context.vertices.length, 6);
    await execute('CREATE INDEX polygon_p ON polygon_test (p) USING RTREE');
    const indexed = await execute(q);
    assert.deepEqual(indexed.rows.map(r => r[0]).sort(), [1, 3]);
    assert.deepEqual(ops(indexed.plan.root), ['polygon_search', 'fetch']);
  } finally {
    await server.close();
    await rm(cacheDir, { recursive: true, force: true });
  }
});
