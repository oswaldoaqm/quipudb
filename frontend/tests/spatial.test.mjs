import assert from "node:assert/strict";
import { test } from "node:test";
import { extractSpatialPoints, isPointValue } from "../src/lib/spatial.ts";

const result = (columns, column_types, rows) => ({
  columns, column_types, rows, affected_rows: 0, plan: null,
});

test("conserva latitud/longitud, todas las columnas POINT y la fila sin mutarla", () => {
  const origin = { latitude: -12.0464, longitude: -77.0428 };
  const destination = { latitude: -12.12, longitude: -77.03 };
  const row = Object.freeze([1, origin, destination]);
  const query = result(["id", "origen", "destino"], ["INT", "POINT", "POINT"], [row]);
  const { points, pointColumns } = extractSpatialPoints(query);
  assert.deepEqual(pointColumns, [1, 2]);
  assert.deepEqual(points.map(p => p.position), [[-12.0464, -77.0428], [-12.12, -77.03]]);
  assert.equal(points[0].row, row);
  assert.equal(points[1].columnName, "destino");
  assert.notEqual(points[0].key, points[1].key);
});

test("rechaza nulos, tipos incorrectos, coordenadas incompletas y no finitas", () => {
  const invalid = [null, undefined, "POINT(0, 0)", [0, 0], {},
    { latitude: "0", longitude: 0 }, { latitude: 0 },
    { latitude: NaN, longitude: 0 }, { latitude: 0, longitude: Infinity },
    { latitude: -91, longitude: 0 }, { latitude: 91, longitude: 0 },
    { latitude: 0, longitude: -181 }, { latitude: 0, longitude: 181 }];
  const rows = invalid.map(p => [p]);
  rows.push([{ latitude: 0, longitude: 0 }]);
  const spatial = extractSpatialPoints(result(["p"], ["POINT"], rows));
  assert.equal(spatial.invalidCount, invalid.length);
  assert.equal(spatial.points.length, 1);
  assert.deepEqual(spatial.points[0].position, [0, 0]);
  invalid.forEach(value => assert.equal(isPointValue(value), false));
});

test("acepta los limites geograficos inclusivos", () => {
  assert.ok(isPointValue({ latitude: -90, longitude: -180 }));
  assert.ok(isPointValue({ latitude: 90, longitude: 180 }));
});

test("no deduce puntos de columnas de otro tipo ni elimina puntos coincidentes", () => {
  const p = { latitude: 0, longitude: 0 };
  const spatial = extractSpatialPoints(result(["texto", "p"], ["VARCHAR", "POINT"], [[p, p], [p, p]]));
  assert.equal(spatial.points.length, 2);
  assert.notEqual(spatial.points[0].key, spatial.points[1].key);
});

test("distingue ausencia de POINT de un resultado espacial sin filas", () => {
  assert.deepEqual(extractSpatialPoints(null), { points: [], pointColumns: [], invalidCount: 0 });
  assert.equal(extractSpatialPoints(result(["id"], ["INT"], [[1]])).pointColumns.length, 0);
  assert.deepEqual(extractSpatialPoints(result(["p"], ["POINT"], [])), {
    points: [], pointColumns: [0], invalidCount: 0,
  });
});
