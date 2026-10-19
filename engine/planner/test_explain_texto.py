"""El plan de una consulta MATCH (#170): indice invertido, ranking y fetch aparte."""

from engine.parser import SelectStatement, parse_sql
from engine.parser.ast import SqlTypeName
from engine.parser.bound_ast import BoundColumn, BoundSchema
from engine.parser.semantic import bind_select
from engine.planner.explain import explain_select
from engine.planner.optimizer import IndexMetadata, TableMetadata, optimize_select
from engine.planner.plan import Op, Plan, Step, Structure

_SCHEMA = BoundSchema(
    "documentos",
    (
        BoundColumn("id", SqlTypeName.INT, None),
        BoundColumn("contenido", SqlTypeName.VARCHAR, 200),
        BoundColumn("autor", SqlTypeName.VARCHAR, 40),
    ),
    0,
)
_TEXTO = IndexMetadata("por_contenido", 1, Structure.INVERTED_INDEX, supports_range=False)
_TABLA = TableMetadata("documentos", Structure.HEAP, (_TEXTO,))


def _explain(source: str) -> Plan:
    statement = parse_sql(source)
    assert isinstance(statement, SelectStatement)
    bound = bind_select(statement, _SCHEMA, source)
    return explain_select(optimize_select(bound, _TABLA), source, source)


def _paso(plan: Plan, op: Op) -> Step:
    pasos = [paso for paso in plan.root.walk() if paso.op is op]
    assert len(pasos) == 1, [paso.op for paso in plan.root.walk()]
    return pasos[0]


def test_el_plan_declara_el_indice_invertido_y_el_ranking_elegido() -> None:
    plan = _explain(
        "SELECT * FROM documentos WHERE MATCH(contenido, 'base de datos') USING TF_IDF LIMIT 3"
    )

    texto = _paso(plan, Op.TEXT_SEARCH)
    assert texto.structure is Structure.INVERTED_INDEX
    assert texto.ranking == "tf_idf"
    assert texto.table == "documentos"
    assert texto.column == "contenido"
    assert "ranking tf_idf (elegido con USING)" in texto.detail
    assert "top-3" in texto.detail
    assert "indice por_contenido" in texto.detail


def test_sin_using_el_plan_dice_bm25_y_que_fue_por_omision() -> None:
    texto = _paso(_explain("SELECT id FROM documentos WHERE MATCH(contenido, 'oro')"), Op.TEXT_SEARCH)

    assert texto.ranking == "bm25"
    assert "por omision" in texto.detail
    assert "todos los que coinciden" in texto.detail


def test_text_search_y_fetch_son_pasos_separados() -> None:
    # Regla 1 del ADR 0002: la busqueda en el indice devuelve RIDs y el fetch
    # los resuelve en la tabla; si fueran un solo paso, el panel no podria
    # mostrar cuanto costo cada parte.
    plan = _explain("SELECT * FROM documentos WHERE MATCH(contenido, 'oro')")

    fetch = plan.root
    assert fetch.op is Op.FETCH
    assert fetch.structure is Structure.HEAP
    assert fetch.ranking is None
    assert [hijo.op for hijo in fetch.children] == [Op.TEXT_SEARCH]
    assert fetch.children[0].children == []


def test_explain_deja_los_contadores_en_cero() -> None:
    plan = _explain("SELECT * FROM documentos WHERE MATCH(contenido, 'oro')")

    for paso in plan.root.walk():
        assert paso.stats.pages_read == 0
        assert paso.time_ms == 0.0


def test_con_order_by_el_indice_entrega_todos() -> None:
    plan = _explain("SELECT id FROM documentos WHERE MATCH(contenido, 'oro') ORDER BY autor LIMIT 2")

    texto = _paso(plan, Op.TEXT_SEARCH)
    assert texto.ranking == "bm25"
    assert "todos los que coinciden" in texto.detail
    assert _paso(plan, Op.SORT).structure is Structure.EXTERNAL_SORT


def test_el_ranking_viaja_en_el_json_y_vuelve() -> None:
    plan = _explain("SELECT id FROM documentos WHERE MATCH(contenido, 'oro') USING BM25")

    d = plan.to_dict()
    vuelta = Plan.from_dict(d)
    texto = _paso(vuelta, Op.TEXT_SEARCH)
    assert texto.ranking == "bm25"
    assert vuelta.to_dict() == d


def test_solo_el_paso_de_texto_lleva_ranking() -> None:
    plan = _explain(
        "SELECT *, SCORE() AS r FROM documentos WHERE MATCH(contenido, 'oro') ORDER BY r DESC"
    )

    con_ranking = [paso.op for paso in plan.root.walk() if paso.ranking is not None]
    assert con_ranking == [Op.TEXT_SEARCH]
