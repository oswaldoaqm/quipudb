import assert from 'node:assert/strict';
import { test } from 'node:test';
import { conteoDe, rangoDe } from '../src/lib/fragments.ts';

test('el rango se lee como en el catalogo: lower incluido, upper excluido', () => {
  assert.equal(rangoDe('id', null, 10000), `id < ${(10000).toLocaleString('es-PE')}`);
  assert.equal(rangoDe('id', 10000, null), `id ≥ ${(10000).toLocaleString('es-PE')}`);
  assert.equal(rangoDe('id', 0, 50), '0 ≤ id < 50');
  assert.equal(rangoDe('codigo', null, null), 'tabla completa');
  assert.equal(rangoDe('id', -5, 5), '-5 ≤ id < 5');
});

test('un fragmento sin nodo no muestra un conteo inventado', () => {
  const f = { fragment_id: 'f', node_id: 'n1', lower: null, upper: null, available: true, record_count: 1, reason: null };
  assert.equal(conteoDe(f), '1 fila');
  assert.equal(conteoDe({ ...f, record_count: 2500 }), `${(2500).toLocaleString('es-PE')} filas`);
  assert.equal(conteoDe({ ...f, available: false, record_count: null, reason: '[NODE_UNAVAILABLE] nodo n1' }), 'nodo no disponible');
});
