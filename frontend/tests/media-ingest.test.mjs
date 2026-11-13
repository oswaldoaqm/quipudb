import assert from 'node:assert/strict';
import { test } from 'node:test';
import { cargarCarpeta, consultaDeEjemplo, filtrarArchivos, nombreDeVocabulario, revisarTabla } from '../src/lib/media-ingest.ts';

const archivo = (name) => ({ name, file: new Blob(['x']) });

/** API simulada: guarda las llamadas y avanza cada trabajo en dos consultas. */
function apiFalsa({ rechazar = [], entrenamiento = {}, carga = {} } = {}) {
  const llamadas = [];
  let consultasEntreno = 0;
  let consultasCarga = 0;
  const api = {
    async upload(archivos, destino) {
      llamadas.push(['upload', archivos.map((a) => a.name), destino]);
      const guardados = archivos.filter((a) => !rechazar.includes(a.name));
      return {
        folder: destino.folder ?? `subidas/${destino.name}-abc12345`,
        saved: guardados.length,
        files: guardados.map((a) => ({ name: a.name, path: `subidas/${destino.name ?? 'x'}-abc12345/${a.name}`, bytes: 1 })),
        rejected: archivos.filter((a) => rechazar.includes(a.name)).map((a) => ({ name: a.name, error: 'rechazado' })),
      };
    },
    async train(peticion) {
      llamadas.push(['train', peticion]);
      return { job_id: 'v1', status: 'pending' };
    },
    async trainStatus(id) {
      consultasEntreno += 1;
      const listo = consultasEntreno >= 2;
      return {
        job_id: id, status: listo ? 'completed' : 'running', selected: 3, described: listo ? 3 : 1, failed: 0,
        errors: [], errors_truncated: false, vocabulary: 'vocabularios/fotos-k8.npz', documents: 3,
        codebook_id: 'h1', error: null, ...entrenamiento,
      };
    },
    async register(tabla, vocabulary) {
      llamadas.push(['register', tabla, vocabulary]);
    },
    async nextId(tabla) {
      llamadas.push(['nextId', tabla]);
      return 7;
    },
    async load(tabla, peticion) {
      llamadas.push(['load', tabla, peticion]);
      return { job_id: 'm1', status: 'pending' };
    },
    async loadStatus(tabla, id) {
      consultasCarga += 1;
      const listo = consultasCarga >= 2;
      return {
        job_id: id, table: tabla, status: listo ? 'completed' : 'running', discovered: 3, processed: listo ? 3 : 1,
        inserted: listo ? 2 : 1, failed: listo ? 1 : 0, errors: listo ? [{ path: 'b.png', error: 'sin descriptores' }] : [],
        errors_truncated: false, error: null, ...carga,
      };
    },
  };
  return { api, llamadas };
}

const sinPausa = { pausa: async () => {}, intervaloMs: 0 };

test('filtra lo que no es multimedia y cuenta imagenes y audios', () => {
  const r = filtrarArchivos(['a.PNG', 'b.jpg', 'c.wav', 'Thumbs.db', 'leeme.txt', 'sin_ext'].map(archivo));
  assert.deepEqual(r.multimedia.map((a) => a.name), ['a.PNG', 'b.jpg', 'c.wav']);
  assert.equal(r.omitidos, 3);
  assert.deepEqual([r.imagenes, r.audios], [2, 1]);
});

test('sube en tandas a la misma carpeta, entrena para la tabla y carga', async () => {
  const { api, llamadas } = apiFalsa();
  const avances = [];
  const informe = await cargarCarpeta(
    api,
    { tabla: 'fotos', nombre: 'fotos', archivos: ['a.png', 'b.png', 'c.png'].map(archivo), vocabulario: { tipo: 'nuevo', k: 8 } },
    (a) => avances.push(a),
    { ...sinPausa, tanda: 2 },
  );
  assert.deepEqual(llamadas, [
    ['upload', ['a.png', 'b.png'], { name: 'fotos' }],
    ['upload', ['c.png'], { folder: 'subidas/fotos-abc12345' }],
    ['train', { folder: 'subidas/fotos-abc12345', k: 8, table: 'fotos', name: 'fotos-k8', recursive: false }],
    ['nextId', 'fotos'],
    ['load', 'fotos', { folder: 'subidas/fotos-abc12345', vocabulary: 'vocabularios/fotos-k8.npz', recursive: false, start_id: 7 }],
  ]);
  assert.deepEqual(avances.map((a) => `${a.fase} ${a.hechos}/${a.total}`), [
    'subiendo 0/3', 'subiendo 2/3', 'subiendo 3/3',
    'entrenando 1/3', 'entrenando 3/3',
    'cargando 1/3', 'cargando 3/3',
  ]);
  assert.equal(informe.inserted, 2);
  assert.equal(informe.failed, 1);
  assert.deepEqual(informe.entrenado, { documents: 3, codebook_id: 'h1' });
  assert.deepEqual(informe.errors, [{ path: 'b.png', error: 'sin descriptores' }]);
  assert.equal(informe.ejemplo, 'subidas/fotos-abc12345/a.png');
});

test('con un vocabulario existente lo asocia a la tabla y no entrena', async () => {
  const { api, llamadas } = apiFalsa();
  const informe = await cargarCarpeta(
    api,
    { tabla: 'fotos', nombre: 'fotos', archivos: [archivo('viaje/a.png')], vocabulario: { tipo: 'existente', path: 'vocabularios/v.npz' } },
    () => {},
    sinPausa,
  );
  assert.deepEqual(llamadas.map((l) => l[0]), ['upload', 'register', 'nextId', 'load']);
  assert.deepEqual(llamadas[1], ['register', 'fotos', 'vocabularios/v.npz']);
  // Las subcarpetas obligan a recorrer la carpeta subida entera.
  assert.equal(llamadas[3][2].recursive, true);
  assert.equal(informe.entrenado, null);
  assert.equal(informe.vocabulary, 'vocabularios/v.npz');
});

test('los rechazos de la subida van al informe y la carga sigue', async () => {
  const { api } = apiFalsa({ rechazar: ['b.png'] });
  const informe = await cargarCarpeta(
    api,
    { tabla: 'fotos', nombre: 'fotos', archivos: ['a.png', 'b.png'].map(archivo), vocabulario: { tipo: 'existente', path: 'v.npz' } },
    () => {},
    sinPausa,
  );
  assert.equal(informe.subidos, 1);
  assert.deepEqual(informe.errors[0], { path: 'b.png', error: 'rechazado' });
});

test('si no se sube nada o un trabajo falla, termina con el motivo', async () => {
  const todo = apiFalsa({ rechazar: ['a.png'] });
  await assert.rejects(
    cargarCarpeta(todo.api, { tabla: 'f', nombre: 'f', archivos: [archivo('a.png')], vocabulario: { tipo: 'nuevo', k: 8 } }, () => {}, sinPausa),
    /no se subio ningun archivo: a.png: rechazado/,
  );
  const entreno = apiFalsa({ entrenamiento: { status: 'failed', error: 'la carpeta mezcla 1 imágenes y 1 audios' } });
  await assert.rejects(
    cargarCarpeta(entreno.api, { tabla: 'f', nombre: 'f', archivos: [archivo('a.png')], vocabulario: { tipo: 'nuevo', k: 8 } }, () => {}, sinPausa),
    /mezcla/,
  );
  assert.ok(!entreno.llamadas.some((l) => l[0] === 'load'));
  await assert.rejects(
    cargarCarpeta(apiFalsa().api, { tabla: 'f', nombre: 'f', archivos: [], vocabulario: { tipo: 'nuevo', k: 8 } }, () => {}, sinPausa),
    /no tiene imagenes ni audios/,
  );
});

test('la consulta de ejemplo usa el indice vectorial que exista, o HNSW', () => {
  assert.equal(
    consultaDeEjemplo('fotos', "subidas/x/o'brien.png", ['bplus_unclustered', 'ivf']),
    "SELECT id, nombre, SIMILARITY_SCORE() AS score FROM fotos\nWHERE SIMILAR_TO('subidas/x/o''brien.png', k=5)\nUSING IVF WITH METRIC=cosine\nORDER BY score DESC",
  );
  // Sin indice vectorial tambien hay consulta: USING HNSW cae a la busqueda exacta.
  assert.match(consultaDeEjemplo('fotos', 'a.png', ['extendible_hash']), /USING HNSW/);
});

test('solo admite tablas con el esquema de la carga multimedia', () => {
  const col = (name, type, extra = {}) => ({ name, type, size: null, is_primary_key: false, ...extra });
  const buena = {
    name: 'fotos', storage: 'heap', indexes: [], record_count: 0,
    columns: [col('id', 'INT', { is_primary_key: true }), col('nombre', 'VARCHAR', { size: 64 }), col('ruta', 'VARCHAR', { size: 512 }), col('rasgos', 'VECTOR', { size: 8 })],
  };
  assert.deepEqual(revisarTabla(buena), { dimension: 8 });
  assert.ok('motivo' in revisarTabla({ ...buena, columns: [...buena.columns, col('extra', 'INT')] }));
  assert.ok('motivo' in revisarTabla({ ...buena, columns: buena.columns.map((c) => (c.name === 'rasgos' ? col('rasgos', 'VARCHAR', { size: 8 }) : c)) }));
  assert.ok('motivo' in revisarTabla({ ...buena, columns: buena.columns.map((c) => (c.name === 'id' ? col('id', 'INT') : c)) }));
});

test('el nombre del vocabulario sale de la carpeta elegida', () => {
  assert.equal(nombreDeVocabulario('Fotos de Perú', 64), 'Fotos-de-Peru-k64');
  assert.equal(nombreDeVocabulario('???', 8), 'vocabulario-k8');
});
