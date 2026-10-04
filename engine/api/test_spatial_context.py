"""Contrato de visualizacion de radio, usando el ejecutor y API reales."""
import pytest
from fastapi.testclient import TestClient

from engine.api.main import create_app
from engine.api.service import to_response
from engine.executor.test_spatial import _CENTER, _processor


@pytest.mark.parametrize('metric,unit,radius', [('HAVERSINE', 'meters', 5000), ('EUCLIDEAN', 'degrees', 0.01)])
@pytest.mark.parametrize('indexed', [False, True])
def test_contexto_validado_independiente_del_plan(tmp_path, metric, unit, radius, indexed):
    _, processor = _processor(tmp_path)
    if indexed:
        processor.execute('CREATE INDEX ubicaciones ON tiendas (ubicacion) USING RTREE')
    result = processor.execute(f'SELECT id, ubicacion FROM tiendas WHERE distancia(ubicacion, {_CENTER}, {metric}) <= {radius}')
    body = to_response(result).model_dump(mode='json')
    assert body['spatial_context'] == {
        'kind': 'radius', 'table': 'tiendas', 'column': 'ubicacion',
        'center': {'latitude': -12.0464, 'longitude': -77.0428},
        'radius': radius, 'metric': metric, 'unit': unit, 'operator': '<=',
    }
    assert body['rows'][0][1] == body['spatial_context']['center']
    assert len(body['rows']) == 2
    assert body['is_explain'] is False


def test_vacio_sin_point_conserva_contexto_y_operador(tmp_path):
    _, processor = _processor(tmp_path)
    result = processor.execute(f'SELECT nombre FROM tiendas WHERE distancia(ubicacion, {_CENTER}) < 0')
    assert result.rows == ()
    assert result.column_types == ('VARCHAR',)
    assert result.spatial_context.radius == 0
    assert result.spatial_context.operator == '<'


def test_lote_conserva_solo_contexto_de_ultima_sentencia(tmp_path):
    _, processor = _processor(tmp_path)
    spatial = f'SELECT id, ubicacion FROM tiendas WHERE distancia(ubicacion, {_CENTER}) <= 0'
    result = processor.execute(f'INSERT INTO tiendas VALUES (4, \'otro\', POINT(0, 0)); {spatial}')
    assert result.affected_rows == 1
    assert len(result.rows) == 1
    assert result.spatial_context.radius == 0
    assert processor.execute(f'{spatial}; SELECT id FROM tiendas').spatial_context is None
    assert processor.execute(f'{spatial}; EXPLAIN {spatial}').is_explain is True


@pytest.mark.parametrize('prefix', ['EXPLAIN', 'EXPLAIN ANALYZE'])
def test_explain_no_anuncia_coincidencias_ni_region(tmp_path, prefix):
    _, processor = _processor(tmp_path)
    result = processor.execute(f'{prefix} SELECT * FROM tiendas WHERE distancia(ubicacion, {_CENTER}) <= 5000')
    assert result.is_explain is True
    assert result.rows == ()
    assert result.spatial_context is None
    assert result.plan is not None


def test_cambio_de_consulta_no_arrastra_metadata(tmp_path):
    _, processor = _processor(tmp_path)
    first = processor.execute(f'SELECT * FROM tiendas WHERE distancia(ubicacion, {_CENTER}) <= 5000')
    second = processor.execute('SELECT nombre FROM tiendas WHERE distancia(ubicacion, POINT(0, 0), EUCLIDEAN) < 0')
    assert first.spatial_context.center.latitude == -12.0464
    assert second.spatial_context.center.latitude == 0
    assert second.rows == ()
    assert processor.execute('SELECT * FROM tiendas').spatial_context is None


def test_agrupacion_conserva_region_sin_identidad_de_tabla(tmp_path):
    _, processor = _processor(tmp_path)
    result = processor.execute(f'SELECT nombre, COUNT(*) FROM tiendas WHERE distancia(ubicacion, {_CENTER}) <= 5000 GROUP BY nombre')
    assert result.spatial_context.table is None
    assert result.spatial_context.column == 'ubicacion'


def test_api_contexto_y_catalogo_con_rtree(tmp_path):
    import quipudb_native
    database, processor = _processor(tmp_path)
    processor.execute('CREATE INDEX ubicaciones ON tiendas (ubicacion) USING RTREE')
    with TestClient(create_app(processor=processor, database=database, native=quipudb_native)) as client:
        response = client.get('/tables')
        assert response.status_code == 200
        assert response.json()[0]['indexes'][0]['structure'] == 'rtree'
        assert response.json()[0]['columns'][0]['is_primary_key'] is True
        result = client.post('/query', json={'sql': f'SELECT id FROM tiendas WHERE distancia(ubicacion, {_CENTER}) < 0'})
        assert result.status_code == 200
        assert result.json()['rows'] == []
        assert result.json()['spatial_context']['unit'] == 'meters'
        assert result.json()['spatial_context']['center']['longitude'] == -77.0428


def test_api_knn_conserva_point_y_expone_el_operador(tmp_path):
    import quipudb_native
    database, processor = _processor(tmp_path)
    processor.execute('CREATE INDEX ubicaciones ON tiendas (ubicacion) USING RTREE')
    query = f'SELECT * FROM tiendas ORDER BY distancia(ubicacion, {_CENTER}) LIMIT 2'
    with TestClient(create_app(processor=processor, database=database, native=quipudb_native)) as client:
        result = client.post('/query', json={'sql': query})
        assert result.status_code == 200
        body = result.json()
        assert [row[0] for row in body['rows']] == [1, 2]
        assert body['rows'][0][2] == {'latitude': -12.0464, 'longitude': -77.0428}
        assert body['plan']['root']['children'][0]['op'] == 'knn_search'
        assert body['plan']['root']['children'][0]['structure'] == 'rtree'
        assert body['spatial_context'] is None
