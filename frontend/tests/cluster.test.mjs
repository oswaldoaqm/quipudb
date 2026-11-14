import assert from 'node:assert/strict';
import { test } from 'node:test';
import { cabeceraDe } from '../src/lib/cluster.ts';

const nodo = (id, ready, extra = {}) => ({
  node_id: id, base_url: `http://127.0.0.1:800${id.at(-1)}`, ready, reason: null, fragment_ids: [`items_f${id.at(-1) - 1}`], ...extra,
});

test('sin coordinador la cabecera queda como siempre', () => {
  assert.deepEqual(cabeceraDe(true, null), { texto: 'datos falsos', detalle: null, completo: true });
  assert.deepEqual(cabeceraDe(false, null), { texto: 'motor conectado', detalle: null, completo: true });
});

test('con el coordinador cuenta los nodos listos y detalla cada uno', () => {
  const sano = cabeceraDe(false, { status: 'ready', catalog_version: 1, nodes: [nodo('n1', true), nodo('n2', true)] });
  assert.equal(sano.texto, 'coordinador · 2 de 2 nodos listos');
  assert.equal(sano.completo, true);
  assert.equal(sano.detalle, 'n1 (http://127.0.0.1:8001): listo, items_f0\nn2 (http://127.0.0.1:8002): listo, items_f1');

  const caido = cabeceraDe(false, {
    status: 'degraded', catalog_version: 1,
    nodes: [nodo('n1', true), nodo('n2', false, { fragment_ids: [], reason: '[NODE_UNAVAILABLE] nodo n2: no se pudo conectar' })],
  });
  assert.equal(caido.texto, 'coordinador · 1 de 2 nodos listos');
  assert.equal(caido.completo, false);
  assert.match(caido.detalle, /n2 \(http:\/\/127\.0\.0\.1:8002\): \[NODE_UNAVAILABLE\]/);
});
