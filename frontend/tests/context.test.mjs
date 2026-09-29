import assert from 'node:assert/strict';
import { test } from 'node:test';
import { basePointsOutsideResults, extractSpatialPoints, mapMessage, radiusContext, spatialView } from '../src/lib/spatial.ts';
import { createSpatialBaseCache } from '../src/lib/spatial-context.ts';
import { executionReducer, initialExecution } from '../src/lib/query-execution.ts';

const center = { latitude: -12.0464, longitude: -77.0428 };
const context = (extra = {}) => ({ kind: 'radius', table: 'tiendas', column: 'p', center, radius: 5000, metric: 'HAVERSINE', unit: 'meters', operator: '<=', ...extra });
const result = (rows = [], extra = {}) => ({ columns: ['id', 'p'], column_types: ['INT', 'POINT'], rows, affected_rows: 0, plan: null, spatial_context: context(), ...extra });
const table = { name: 'tiendas', storage: 'heap', indexes: [], record_count: 3, columns: [
  { name: 'id', type: 'INT', is_primary_key: true, size: null }, { name: 'p', type: 'POINT', is_primary_key: false, size: null },
] };
const points = r => extractSpatialPoints(r).points;

test('Haversine conserva metros y centra por region incluso sin POINT ni filas', () => {
  const r = result([], { columns: ['id'], column_types: ['INT'] });
  assert.equal(mapMessage(r, false, false), null);
  assert.deepEqual(spatialView(radiusContext(r), points(r)), { kind: 'circle', center: [-12.0464, -77.0428], radius: 5000 });
});
test('Euclidean encuadra referencia y resultados; nunca crea Circle ni convierte grados', () => {
  const r = result([[1, { latitude: 0, longitude: 0 }]], { spatial_context: context({ metric: 'EUCLIDEAN', unit: 'degrees', radius: 0.1 }) });
  assert.deepEqual(spatialView(radiusContext(r), points(r)), { kind: 'points', positions: [[-12.0464, -77.0428], [0, 0]] });
  assert.equal(radiusContext(result([], { spatial_context: context({ metric: 'EUCLIDEAN' }) })), null);
});
test('radio cero encuadra solo la referencia; sin contexto preserva encuadre por puntos', () => {
  assert.deepEqual(spatialView(context({ radius: 0 }), points(result([[1, center]]))), { kind: 'points', positions: [[center.latitude, center.longitude]] });
  assert.deepEqual(spatialView(null, []), { kind: 'empty' });
  assert.equal(spatialView(null, points(result([[1, center]]))).positions.length, 1);
  assert.equal(spatialView(null, points(result([[1, center], [2, center]]))).positions.length, 2);
});
test('contextos invalidos no producen geometria insegura', () => {
  for (const extra of [{ radius: NaN }, { radius: Infinity }, { radius: -1 }, { center: null }, { center: { latitude: 91, longitude: 0 } }, { kind: 'unknown' }, { operator: '=' }]) {
    assert.equal(radiusContext(result([], { spatial_context: context(extra) })), null);
  }
});
test('carga, error, EXPLAIN, vacio y sin POINT tienen estados distintos', () => {
  const r = result();
  assert.match(mapMessage(r, true, false), /Ejecutando/);
  assert.match(mapMessage(r, false, true), /error/);
  assert.match(mapMessage({ ...r, is_explain: true }, false, false), /EXPLAIN/);
  assert.match(mapMessage({ ...r, spatial_context: null }, false, false), /0 coincidencias/);
  assert.match(mapMessage(result([[1]], { columns: ['id'], column_types: ['INT'], spatial_context: null }), false, false), /no contiene columnas POINT/);
});
test('PK distingue registros coincidentes y conserva otras columnas POINT', () => {
  const withTwoPoints = { ...table, columns: [...table.columns, { name: 'destino', type: 'POINT', is_primary_key: false }] };
  const base = result([[1, center, center], [2, center, center]], { columns: ['id', 'p', 'destino'], column_types: ['INT', 'POINT', 'POINT'], spatial_context: null });
  const selection = result([[1, center]]);
  const layer = basePointsOutsideResults(base, selection, withTwoPoints);
  assert.equal(layer.hasIdentity, true);
  assert.deepEqual(layer.points.map(p => [p.row[0], p.columnName]), [[1, 'destino'], [2, 'p'], [2, 'destino']]);
});
test('sin PK en proyeccion o con PK invalida no se asocian coordenadas', () => {
  const base = result([[1, center], [2, center]]);
  for (const selected of [result([[center]], { columns: ['p'], column_types: ['POINT'] }), result([[null, center]]), result([['1', center]])]) {
    const layer = basePointsOutsideResults(base, selected, table);
    assert.equal(layer.hasIdentity, false);
    assert.equal(layer.points.length, 2);
  }
  assert.equal(basePointsOutsideResults(base, result([[1, center]]), { ...table, name: 'otra' }).points.length, 0);
});
test('cambio de consulta y vacio eliminan resultados anteriores; base no altera encuadre', () => {
  const previous = result([[1, center], [2, center]]);
  const next = result([], { spatial_context: context({ center: { latitude: 0, longitude: 0 } }) });
  assert.equal(points(previous).length, 2);
  assert.equal(points(next).length, 0);
  assert.deepEqual(spatialView(radiusContext(next), points(next)).center, [0, 0]);
  assert.equal(basePointsOutsideResults(previous, next, table).points.length, 2);
});
test('ejecucion conserva SQL ejecutado, limpia al empezar y descarta respuestas/errores tardios', () => {
  let state = executionReducer(initialExecution, { type: 'begin', id: 1, sql: 'primera' });
  state = executionReducer(state, { type: 'complete', id: 1, result: result([[1, center]]) });
  assert.equal(state.sql, 'primera');
  state = executionReducer(state, { type: 'begin', id: 2, sql: 'segunda' });
  assert.equal(state.result, null);
  assert.equal(state.pending, true);
  assert.equal(executionReducer(state, { type: 'complete', id: 1, result: result([[1, center]]) }), state);
  assert.equal(executionReducer(state, { type: 'fail', id: 1, error: new Error('tardio') }), state);
  state = executionReducer(state, { type: 'complete', id: 2, result: result() });
  assert.equal(state.sql, 'segunda');
  assert.equal(state.result.rows.length, 0);
  assert.equal(state.pending, false);
});
test('cache comparte solicitudes y se invalida por escritura/esquema/tabla; fallo permite reintento', async () => {
  const queries = [];
  let resolve;
  const cache = createSpatialBaseCache(sql => { queries.push(sql); return new Promise(done => { resolve = done; }); });
  const first = cache.load(table, 0);
  assert.equal(first, cache.load({ ...table }, 0));
  assert.deepEqual(queries, ['SELECT id, p FROM tiendas;']);
  resolve(result()); await first;
  assert.equal(cache.load(table, 0), first);
  const changed = cache.load(table, 1); assert.notEqual(changed, first); resolve(result()); await changed;
  const other = cache.load({ ...table, name: 'otra' }, 1); resolve(result()); await other;
  assert.equal(queries.length, 3);
  let calls = 0;
  const retry = createSpatialBaseCache(async () => { if (++calls === 1) throw new Error('offline'); return result(); });
  await assert.rejects(retry.load(table, 0)); await retry.load(table, 0);
  assert.equal(calls, 2);
});
