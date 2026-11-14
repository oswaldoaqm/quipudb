import assert from 'node:assert/strict';
import { test } from 'node:test';
import path from 'node:path';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { createServer } from 'vite';

/** Un servidor por prueba: el catalogo falso es estado del modulo. */
async function conSimulador(prueba) {
  const cacheDir = await mkdtemp(path.join(tmpdir(), 'quipu-csv-'));
  const server = await createServer({ cacheDir, optimizeDeps: { noDiscovery: true, include: [] }, configFile: false, resolve: { alias: { '@': path.resolve(import.meta.dirname, '../src') } }, server: { middlewareMode: true, watch: null, ws: false } });
  try {
    const mock = await server.ssrLoadModule('/src/api/mock/index.ts');
    await prueba(mock);
  } finally {
    await server.close();
    await rm(cacheDir, { recursive: true, force: true });
  }
}

const csv = (texto) => new File([texto], 'alumnos.csv', { type: 'text/csv' });
const CON_UNA_MALA = 'codigo,nombre,promedio\n50001,ana,15.5\n50002,luis,no es numero\n50003,rosa,12\n';
const contar = async (mock) => (await mock.mockListTables()).find((t) => t.name === 'alumnos').record_count;

test('sin atomic entran las filas buenas y se informa la mala', () => conSimulador(async (mock) => {
  const antes = await contar(mock);
  const informe = await mock.mockLoadCsv('alumnos', csv(CON_UNA_MALA));
  assert.equal(informe.inserted, 2);
  assert.equal(informe.failed, 1);
  assert.equal(informe.errors[0].line, 3);
  assert.equal(await contar(mock), antes + 2);
}));

test('con atomic la primera fila mala deja la tabla como estaba', () => conSimulador(async (mock) => {
  const antes = await contar(mock);
  await assert.rejects(mock.mockLoadCsv('alumnos', csv(CON_UNA_MALA), true), (error) => {
    assert.match(error.message, /^linea 3: .*\. La carga es atomica y no se inserto ninguna fila$/);
    assert.equal(error.line, 3);
    return true;
  });
  assert.equal(await contar(mock), antes);
  const r = await mock.mockExecuteQuery('SELECT * FROM alumnos WHERE codigo = 50001');
  assert.equal(r.rows.length, 0);
}));

test('con atomic y sin errores entran todas', () => conSimulador(async (mock) => {
  const informe = await mock.mockLoadCsv('alumnos', csv('codigo,nombre,promedio\n50011,ana,15\n50012,luis,14\n'), true);
  assert.deepEqual([informe.inserted, informe.failed], [2, 0]);
}));
