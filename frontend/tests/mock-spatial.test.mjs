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
