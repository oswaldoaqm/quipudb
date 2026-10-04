import assert from 'node:assert/strict';
import { test } from 'node:test';
import path from 'node:path';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { createServer } from 'vite';

test('simulador propaga radio, unidades, lotes, vacios y EXPLAIN como la API', async () => {
  const cacheDir = await mkdtemp(path.join(tmpdir(), 'quipu-mock-test-'));
  const server = await createServer({ cacheDir, optimizeDeps: { noDiscovery: true, include: [] }, configFile: false, resolve: { alias: { '@': path.resolve(import.meta.dirname, '../src') } }, server: { middlewareMode: true, watch: null, ws: false } });
  try {
    const { mockExecuteQuery: execute } = await server.ssrLoadModule('/src/api/mock/index.ts');
    const q = 'SELECT * FROM visual_test WHERE distancia(p, POINT(-12, -77)) <= 5000';
    const first = await execute(`CREATE TABLE visual_test (id INT PRIMARY KEY, p POINT) USING HEAP; INSERT INTO visual_test VALUES (1, POINT(-12, -77)); ${q}`);
    assert.equal(first.affected_rows, 1);
    assert.equal(first.rows.length, 1);
    assert.deepEqual(first.spatial_context, { kind: 'radius', table: 'visual_test', column: 'p', center: { latitude: -12, longitude: -77 }, radius: 5000, metric: 'HAVERSINE', unit: 'meters', operator: '<=' });
    const empty = await execute('SELECT id FROM visual_test WHERE distancia(p, POINT(0, 0), EUCLIDEAN) < 0');
    assert.equal(empty.rows.length, 0);
    assert.equal(empty.spatial_context.unit, 'degrees');
    assert.equal(empty.spatial_context.metric, 'EUCLIDEAN');
    assert.equal(empty.spatial_context.radius, 0);
    assert.equal((await execute(`${q}; SELECT * FROM visual_test`)).spatial_context, null);
    for (const prefix of ['EXPLAIN', 'EXPLAIN ANALYZE']) {
      const explain = await execute(`${prefix} ${q}`);
      assert.equal(explain.is_explain, true);
      assert.equal(explain.rows.length, 0);
      assert.equal(explain.spatial_context ?? null, null);
    }
  } finally {
    await server.close();
    await rm(cacheDir, { recursive: true, force: true });
  }
});

test('simulador distingue knn, sort completo, filtros y metricas en ORDER BY', async () => {
  const cacheDir = await mkdtemp(path.join(tmpdir(), 'quipu-knn-test-'));
  const server = await createServer({ cacheDir, optimizeDeps: { noDiscovery: true, include: [] }, configFile: false, resolve: { alias: { '@': path.resolve(import.meta.dirname, '../src') } }, server: { middlewareMode: true, watch: null, ws: false } });
  const ops = root => [...root.children.flatMap(child => ops(child)), root.op];
  try {
    const { mockExecuteQuery: execute } = await server.ssrLoadModule('/src/api/mock/index.ts');
    await execute(`CREATE TABLE nearest_test (id INT PRIMARY KEY, p POINT) USING HEAP;
      INSERT INTO nearest_test VALUES (1, POINT(80, 10));
      INSERT INTO nearest_test VALUES (2, POINT(82, 0));`);
    const q = 'SELECT * FROM nearest_test ORDER BY distancia(p, POINT(80, 0)) LIMIT 1';
    const scan = await execute(q);
    assert.equal(scan.rows[0][0], 1);
    assert.deepEqual(ops(scan.plan.root), ['scan', 'sort', 'limit']);
    await execute('CREATE INDEX nearest_p ON nearest_test (p) USING RTREE');
    const knn = await execute(q);
    assert.equal(knn.rows[0][0], 1);
    assert.deepEqual(ops(knn.plan.root), ['knn_search', 'fetch']);
    assert.equal(knn.plan.root.children[0].structure, 'rtree');
    const euclidean = await execute('SELECT id FROM nearest_test ORDER BY distancia(p, POINT(80, 0), EUCLIDEAN) LIMIT 1');
    assert.deepEqual(euclidean.rows, [[2]]);
    assert.deepEqual(ops(euclidean.plan.root), ['knn_search', 'fetch', 'project']);
    const all = await execute('SELECT id FROM nearest_test ORDER BY distancia(p, POINT(80, 0))');
    assert.deepEqual(all.rows, [[1], [2]]);
    assert.ok(ops(all.plan.root).includes('sort'));
    const filtered = await execute('SELECT id FROM nearest_test WHERE id > 1 ORDER BY distancia(p, POINT(80, 0)) LIMIT 1');
    assert.deepEqual(filtered.rows, [[2]]);
    assert.ok(!ops(filtered.plan.root).includes('knn_search'));
    const desc = await execute('SELECT id FROM nearest_test ORDER BY distancia(p, POINT(80, 0)) DESC LIMIT 1');
    assert.deepEqual(desc.rows, [[2]]);
    assert.ok(ops(desc.plan.root).includes('sort'));
    const explain = await execute('EXPLAIN ' + q);
    assert.deepEqual(explain.rows, []);
    assert.deepEqual(ops(explain.plan.root), ['knn_search', 'fetch']);
  } finally {
    await server.close();
    await rm(cacheDir, { recursive: true, force: true });
  }
});
