// Superficie que el core expone a Python (issue #23).
//
// El enunciado no menciona bindings: son el puente por el que 2.1.3 (parser),
// 2.1.4 (transacciones), 2.1.5 (frontend) y 2.1.6 (benchmarks) llegan al core.
// Por eso la superficie no se elige "a ojo" sino de lo que esas secciones
// necesitan, que resulta ser `TableFile` e `Index` enteros mas `Database` como
// puerta de entrada.
//
// Lo que NO se expone y por que
// -----------------------------
//
//   `cursor()`   Un RecordCursor deja de valer en cuanto la tabla se modifica.
//                En C++ eso es una regla que se respeta; expuesto a Python es
//                una invitacion a guardar un cursor, insertar, y llevarse un
//                uso-despues-de-liberar que ocurre dentro del interprete.
//
//                OJO: la razon que estaba escrita aqui -- "el #20 es C++, nadie
//                en Python lo necesita" -- dejo de ser cierta. El #28 conecta
//                ORDER BY y GROUP BY del parser con los external algorithms, y
//                para eso hacen falta desde Python. Lo que se expone es
//                `source_of(tabla)`, que envuelve el cursor sin entregarlo: no
//                se puede guardar, adelantar ni releer a mano, solo pasarselo a
//                un sort, un group by o un join. La regla de invalidacion sigue
//                viva y esta escrita en su docstring.
//
//   BPlusTree,   Es la maquinaria de los indices, no los indices. Se llega a
//   ExtendibleHash   ellos por `Index`.
//
//   Page,        Detalle de implementacion del storage. Nadie fuera del core
//   DiskManager      los toca.
//
// Los dos problemas de conversion que esto resuelve
// -------------------------------------------------
//
//   1. `True` llegaba como INT. En Python `bool` es subclase de `int`, asi que
//      el caster de int32 lo acepta primero y `INSERT ... VALUES (True)` en una
//      columna BOOL moria con "se esperaba BOOL y llego INT". Peor: la ida y
//      vuelta convertia True en 1, asi que un SELECT sobre una columna BOOL
//      devolvia enteros. Se arregla con un caster propio que prueba bool exacto
//      ANTES que int.
//
//   2. `1` en una columna DOUBLE. Aqui el caster no tiene la culpa: 1 es
//      genuinamente un int, y reordenar el variant romperia los INT de verdad.
//      Se arregla donde se sabe el tipo de la columna -- al insertar, buscar o
//      actualizar -- promoviendo con el Schema. `adaptar_*` hace eso.
//
// Las excepciones se mapean UNA POR UNA. Colapsarlas todas a RuntimeError
// obliga a la capa Python a leer el texto del mensaje para saber que paso, y
// eso convierte un mensaje en una API.

#include <pybind11/operators.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/stl/filesystem.h>

#include <cmath>
#include <optional>
#include <string>
#include <vector>

#include "quipudb/catalog/database.hpp"
#include "quipudb/catalog/table.hpp"
#include "quipudb/catalog/types.hpp"
#include "quipudb/error.hpp"
#include "quipudb/external/external_group.hpp"
#include "quipudb/external/external_join.hpp"
#include "quipudb/external/external_sort.hpp"
#include "quipudb/index/bplus_clustered_table.hpp"
#include "quipudb/storage/heap_file.hpp"
#include "quipudb/storage/sequential_file.hpp"
#include "quipudb/version.hpp"

namespace py = pybind11;
using namespace quipudb;

// ---------------------------------------------------------------------------
// Value: bool antes que int
// ---------------------------------------------------------------------------

namespace pybind11::detail {

/// Caster propio para `Value`. El automatico del variant prueba las
/// alternativas en orden y `bool` es subclase de `int` en Python, asi que un
/// `True` entraba como int32. Aqui bool se prueba primero y con tipo exacto.
template <>
struct type_caster<Value> {
 public:
  PYBIND11_TYPE_CASTER(Value, const_name("Value"));

  bool load(handle src, bool convertir) {
    if (!src) return false;

    // Bool exacto primero: PyBool_Check y no py::isinstance<py::int_>, que
    // tambien acepta True.
    if (PyBool_Check(src.ptr())) {
      value = src.ptr() == Py_True;
      return true;
    }
    // Date antes que los numericos: es una clase registrada, no un escalar.
    if (py::isinstance<Date>(src)) {
      value = src.cast<Date>();
      return true;
    }
    if (PyLong_Check(src.ptr())) {
      int desbordo = 0;
      const long long v = PyLong_AsLongLongAndOverflow(src.ptr(), &desbordo);
      if (desbordo != 0 || v < INT32_MIN || v > INT32_MAX) {
        // Decirlo en vez de truncar en silencio.
        throw py::value_error("el entero " + py::str(src).cast<std::string>() +
                              " no entra en un INT de 32 bits");
      }
      value = static_cast<std::int32_t>(v);
      return true;
    }
    if (PyFloat_Check(src.ptr())) {
      value = PyFloat_AsDouble(src.ptr());
      return true;
    }
    if (py::isinstance<py::str>(src)) {
      value = src.cast<std::string>();
      return true;
    }
    (void)convertir;
    return false;
  }

  static handle cast(const Value& v, return_value_policy politica, handle padre) {
    return std::visit(
        [&](const auto& x) -> handle {
          using T = std::decay_t<decltype(x)>;
          if constexpr (std::is_same_v<T, Date>) {
            return py::cast(x, politica, padre).release();
          } else {
            return py::cast(x).release();
          }
        },
        v);
  }
};

}  // namespace pybind11::detail

namespace {

// ---------------------------------------------------------------------------
// Promocion segun el esquema
// ---------------------------------------------------------------------------

/// Un `1` de Python es un INT legitimo, pero si la columna es DOUBLE lo que el
/// usuario quiso decir es `1.0`. Promover aqui -- donde el esquema dice el tipo
/// de cada columna -- es lo correcto; reordenar el caster del variant no lo es,
/// porque romperia los INT de verdad.
Value adaptar(const Column& col, Value v) {
  if (col.type == DataType::Double && std::holds_alternative<std::int32_t>(v)) {
    return static_cast<double>(std::get<std::int32_t>(v));
  }
  if (col.type == DataType::Date && std::holds_alternative<std::int32_t>(v)) {
    return Date{std::get<std::int32_t>(v)};
  }
  return v;
}

Record adaptar_registro(const Schema& esquema, Record r) {
  const std::size_t n = std::min(r.size(), esquema.columns.size());
  for (std::size_t i = 0; i < n; ++i) r[i] = adaptar(esquema.columns[i], std::move(r[i]));
  return r;
}

Value adaptar_clave(const Schema& esquema, Value k) { return adaptar(esquema.key(), std::move(k)); }

}  // namespace

// ---------------------------------------------------------------------------

PYBIND11_MODULE(quipudb_native, m) {
  m.doc() =
      "Core de QuipuDB. `Database` es la puerta de entrada: abre tablas e "
      "indices desde el catalogo y garantiza un solo objeto por archivo.";

  m.def("version", &quipudb::version, "Version del motor");

  // --- excepciones, una por una --------------------------------------------
  // Colapsarlas todas a RuntimeError obligaria a la capa Python a leer el
  // texto del mensaje para saber que paso.
  static py::exception<Error> base(m, "QuipuDBError", PyExc_RuntimeError);
  static py::exception<IoError> io(m, "IoError", base.ptr());
  static py::exception<SchemaError> esq(m, "SchemaError", base.ptr());
  static py::exception<InvalidRecord> inv(m, "InvalidRecord", base.ptr());
  static py::exception<DuplicateKey> dup(m, "DuplicateKey", base.ptr());
  static py::exception<Unsupported> nos(m, "Unsupported", base.ptr());

  py::register_exception_translator([](std::exception_ptr p) {
    try {
      if (p) std::rethrow_exception(p);
    } catch (const IoError& e) {
      py::set_error(io, e.what());
    } catch (const SchemaError& e) {
      py::set_error(esq, e.what());
    } catch (const InvalidRecord& e) {
      py::set_error(inv, e.what());
    } catch (const DuplicateKey& e) {
      py::set_error(dup, e.what());
    } catch (const Unsupported& e) {
      py::set_error(nos, e.what());
    } catch (const Error& e) {
      py::set_error(base, e.what());
    }
  });

  // --- nombres canonicos de estructura -------------------------------------
  // Las mismas cadenas que el plan de ejecucion (ADR 0002) y los benchmarks.
  auto kinds = m.def_submodule("kind", "Nombres canonicos de cada estructura");
  kinds.attr("HEAP") = std::string(kind::kHeap);
  kinds.attr("SEQUENTIAL") = std::string(kind::kSequential);
  kinds.attr("BPLUS_CLUSTERED") = std::string(kind::kBPlusClustered);
  kinds.attr("BPLUS_UNCLUSTERED") = std::string(kind::kBPlusUnclustered);
  kinds.attr("EXTENDIBLE_HASH") = std::string(kind::kExtendibleHash);

  // --- tipos ----------------------------------------------------------------

  py::enum_<DataType>(m, "DataType")
      .value("INT", DataType::Int)
      .value("DOUBLE", DataType::Double)
      .value("VARCHAR", DataType::Varchar)
      .value("BOOL", DataType::Bool)
      .value("DATE", DataType::Date);

  py::class_<Date>(m, "Date", "Dias desde 1970-01-01")
      .def(py::init<>())
      .def(py::init([](std::int32_t d) { return Date{d}; }), py::arg("days"))
      .def_readwrite("days", &Date::days)
      .def(py::self == py::self)
      .def(py::self < py::self)
      .def("__repr__", [](const Date& d) { return "Date(" + std::to_string(d.days) + ")"; });

  py::class_<RID>(m, "RID", "Direccion fisica de un registro")
      .def(py::init<>())
      .def(py::init([](PageId p, SlotId s) { return RID{p, s}; }), py::arg("page"),
           py::arg("slot"))
      .def_readwrite("page", &RID::page)
      .def_readwrite("slot", &RID::slot)
      .def("valid", &RID::valid)
      .def(py::self == py::self)
      .def(py::self < py::self)
      .def("__repr__", [](const RID& r) {
        return "RID(" + std::to_string(r.page) + ", " + std::to_string(r.slot) + ")";
      });

  py::class_<Column>(m, "Column")
      .def(py::init([](std::string nombre, DataType tipo, std::uint16_t largo) {
             return Column{std::move(nombre), tipo, largo};
           }),
           py::arg("name"), py::arg("type"), py::arg("length") = 0)
      .def_readwrite("name", &Column::name)
      .def_readwrite("type", &Column::type)
      .def_readwrite("length", &Column::length)
      .def("byte_size", &Column::byte_size)
      .def("__repr__", [](const Column& c) {
        return "Column('" + c.name + "', " + std::string(to_string(c.type)) +
               (c.type == DataType::Varchar ? "(" + std::to_string(c.length) + ")" : "") + ")";
      });

  py::class_<Schema>(m, "Schema")
      .def(py::init([](std::string tabla, std::vector<Column> cols, std::size_t clave) {
             return Schema{std::move(tabla), std::move(cols), clave};
           }),
           py::arg("table_name"), py::arg("columns"), py::arg("key_column") = 0)
      .def_readwrite("table_name", &Schema::table_name)
      .def_readwrite("columns", &Schema::columns)
      .def_readwrite("key_column", &Schema::key_column)
      .def("record_size", &Schema::record_size)
      .def("find", &Schema::find, py::arg("name"),
           "Posicion de una columna por nombre, o None")
      .def("validate", &Schema::validate, py::arg("record"),
           "Lanza InvalidRecord si el registro no calza")
      .def("__repr__", [](const Schema& s) {
        return "Schema('" + s.table_name + "', " + std::to_string(s.columns.size()) +
               " columnas)";
      });

  py::class_<OpStats>(m, "OpStats",
                      "Contadores de una operacion. Es lo que alimenta `stats` del plan "
                      "de ejecucion (ADR 0002) y las mediciones del 2.1.6.")
      .def(py::init<>())
      .def_readwrite("pages_read", &OpStats::pages_read)
      .def_readwrite("pages_written", &OpStats::pages_written)
      .def_readwrite("records_examined", &OpStats::records_examined)
      .def_readwrite("records_returned", &OpStats::records_returned)
      .def("reset", &OpStats::reset)
      .def("as_dict",
           [](const OpStats& s) {
             py::dict d;
             d["pages_read"] = s.pages_read;
             d["pages_written"] = s.pages_written;
             d["records_examined"] = s.records_examined;
             d["records_returned"] = s.records_returned;
             return d;
           },
           "Los cuatro contadores como dict, listo para el JSON del plan")
      .def("__repr__", [](const OpStats& s) {
        return "OpStats(pages_read=" + std::to_string(s.pages_read) +
               ", pages_written=" + std::to_string(s.pages_written) +
               ", records_examined=" + std::to_string(s.records_examined) +
               ", records_returned=" + std::to_string(s.records_returned) + ")";
      });

  // --- TableFile ------------------------------------------------------------
  // Sin `cursor()`: ver la cabecera.

  py::class_<TableFile>(m, "TableFile",
                        "Organizacion fisica de una tabla. No se construye directamente: "
                        "se obtiene de Database.table() o Database.create_table().")
      .def_property_readonly("schema", &TableFile::schema,
                             py::return_value_policy::reference_internal)
      .def_property_readonly(
          "kind", [](const TableFile& t) { return std::string(t.kind()); },
          "Una de las constantes de `kind`")
      .def("insert",
           [](TableFile& t, Record r) { return t.insert(adaptar_registro(t.schema(), std::move(r))); },
           py::arg("record"), "Inserta y devuelve el RID donde quedo")
      .def("remove",
           [](TableFile& t, Value k) { return t.remove(adaptar_clave(t.schema(), std::move(k))); },
           py::arg("key"), "Elimina por clave primaria. Devuelve 0 o 1")
      .def("update",
           [](TableFile& t, Value k, Record r) {
             return t.update(adaptar_clave(t.schema(), std::move(k)),
                             adaptar_registro(t.schema(), std::move(r)));
           },
           py::arg("key"), py::arg("record"))
      .def("search",
           [](TableFile& t, Value k) { return t.search(adaptar_clave(t.schema(), std::move(k))); },
           py::arg("key"), "Registros con esa clave primaria. Lista vacia si no hay")
      .def("range_search",
           [](TableFile& t, Value lo, Value hi) {
             return t.range_search(adaptar_clave(t.schema(), std::move(lo)),
                                   adaptar_clave(t.schema(), std::move(hi)));
           },
           py::arg("lo"), py::arg("hi"), "Registros con clave en [lo, hi], ambos inclusive")
      .def("scan", &TableFile::scan, "Todos los registros vivos")
      .def("read", &TableFile::read, py::arg("rid"),
           "Registro en esa direccion fisica, o None")
      .def("size", &TableFile::size)
      .def("stats", &TableFile::stats, py::return_value_policy::copy)
      .def("reset_stats", &TableFile::reset_stats)
      .def("__len__", &TableFile::size);

  // --- Index ----------------------------------------------------------------

  py::class_<Index>(m, "Index",
                    "Indice secundario sobre una columna. Guarda pares (clave, RID); el "
                    "registro se lee despues con TableFile.read(rid).")
      .def_property_readonly(
          "kind", [](const Index& i) { return std::string(i.kind()); })
      .def_property_readonly("key_type", &Index::key_type)
      .def("supports_range", &Index::supports_range,
           "True en B+, False en hash. Consultalo ANTES de llamar a range_search")
      .def("insert", &Index::insert, py::arg("key"), py::arg("rid"))
      .def("remove", py::overload_cast<const Key&>(&Index::remove), py::arg("key"),
           "Quita todas las entradas de esa clave. Devuelve cuantas")
      .def("remove_one", py::overload_cast<const Key&, RID>(&Index::remove), py::arg("key"),
           py::arg("rid"), "Quita solo esa entrada. Devuelve si existia")
      .def("search", &Index::search, py::arg("key"), "RIDs de las entradas con esa clave")
      .def("range_search", &Index::range_search, py::arg("lo"), py::arg("hi"),
           "RIDs con clave en [lo, hi]. Lanza Unsupported si supports_range() es False")
      .def("scan", &Index::scan, "Todas las entradas (clave, RID)")
      .def("size", &Index::size)
      .def("stats", &Index::stats, py::return_value_policy::copy)
      .def("reset_stats", &Index::reset_stats)
      .def("__len__", &Index::size);

  // --- Database -------------------------------------------------------------

  py::class_<Database>(m, "Database",
                       "Catalogo mas archivos abiertos. Devuelve SIEMPRE el mismo objeto "
                       "para una tabla o indice dado: dos handles sobre el mismo archivo "
                       "se pisan al escribir.")
      .def(py::init<std::filesystem::path>(), py::arg("catalog_path"))
      .def("create_table", &Database::create_table, py::arg("schema"), py::arg("storage"),
           py::arg("page_size") = kDefaultPageSize,
           py::return_value_policy::reference_internal,
           "Registra la tabla, crea su archivo y devuelve el handle")
      .def("table", &Database::table, py::arg("name"),
           py::return_value_policy::reference_internal)
      .def("create_index", &Database::create_index, py::arg("table"), py::arg("index_name"),
           py::arg("column"), py::arg("kind"), py::return_value_policy::reference_internal,
           "Registra el indice, crea su archivo y lo construye sobre los datos existentes")
      .def("index", &Database::index, py::arg("table"), py::arg("index_name"),
           py::return_value_policy::reference_internal)
      .def("drop_table", &Database::drop_table, py::arg("name"))
      .def("drop_index", &Database::drop_index, py::arg("table"), py::arg("index_name"))
      .def("close", &Database::close, py::arg("name"))
      .def("flush", &Database::flush, "Vacia a disco tablas e indices abiertos")
      .def("is_open", &Database::is_open, py::arg("name"))
      .def("open_tables", &Database::open_tables)
      .def("open_indexes", &Database::open_indexes)
      .def("table_names", [](Database& db) { return db.catalog().table_names(); },
           "Nombres de las tablas registradas en el catalogo")
      .def("has_table", [](Database& db, std::string_view n) { return db.catalog().has_table(n); },
           py::arg("name"));

  // --- medidas por organizacion, para el 2.1.6 ------------------------------
  // Estas NO estan en `TableFile` porque cada organizacion mide cosas
  // distintas. La comparacion experimental las necesita.


  // --- external algorithms (#20, #21, #22) ---------------------------------
  //
  // Son lo que el #28 necesita para conectar ORDER BY y GROUP BY del parser con
  // el core. Hasta este issue estaban implementados, probados y encerrados: no
  // salian de C++.
  //
  // TIEMPOS DE VIDA. Los tres objetos son DUEÑOS de sus archivos temporales, y
  // el flujo que devuelven deja de valer si el objeto muere. En C++ eso es una
  // regla que se lee en la cabecera; en Python, donde el recolector decide
  // cuando destruir, seria un uso-despues-de-liberar dentro del interprete:
  //
  //     flujo = quipudb_native.ExternalSort(esquema, 0).sorted(fuente)
  //     for fila in flujo: ...      # el ExternalSort ya murio
  //
  // Por eso cada `sorted`, `grouped` y `joined` lleva `py::keep_alive` sobre el
  // objeto Y sobre sus entradas: mientras el flujo viva, nada de lo que
  // necesita se destruye. El caso peor no es el sort sino el index nested loop,
  // cuya salida recorre el flujo externo y sondea el indice mientras se lee, o
  // sea que guarda punteros a los dos.
  //
  // `PruebasDeTiemposDeVida` en test_bindings.py cubre exactamente los patrones
  // que se caerian sin esto.

  py::class_<RecordSource>(
      m, "RecordSource",
      "Flujo de registros de a uno: la entrada y la salida de los external "
      "algorithms. Se recorre con un for; no se puede rebobinar ni recorrer dos "
      "veces.")
      .def(
          "__iter__", [](RecordSource& s) -> RecordSource& { return s; },
          py::keep_alive<0, 1>())
      .def("__next__", [](RecordSource& s) {
        Record r;
        if (!s.next(r)) throw py::stop_iteration();
        return r;
      });

  m.def(
      "source_of", [](TableFile& t) { return source_of(t); }, py::arg("table"),
      py::keep_alive<0, 1>(),
      "Recorre una tabla como flujo, sin materializarla.\n\n"
      "Envuelve el cursor de la tabla, que NO se expone suelto. Vale la misma "
      "regla: el flujo deja de valer en cuanto la tabla se modifica. Insertar o "
      "borrar mientras se lee es comportamiento indefinido, no una excepcion.");

  m.def(
      "source_of", [](std::vector<Record> rs) { return source_of(std::move(rs)); },
      py::arg("records"),
      "Adapta una lista ya materializada. Para pruebas y para trozos chicos que "
      "alguien ya tiene en memoria; una tabla entera va por la otra forma.");

  // --- external sorting (#20) ----------------------------------------------

  py::class_<ExternalSort> sort(
      m, "ExternalSort",
      "ORDER BY sobre mas datos de los que caben en memoria: k-way merge.\n\n"
      "De un solo uso: se construye, se llama a `sorted()` una vez y se lee el "
      "flujo. `buffers` son PAGINAS, no megabytes -- es lo que hace "
      "interpretable la formula de costo 2N(1 + ceil(log_{B-1}(N/B))).");
  sort.attr("MIN_BUFFERS") = ExternalSort::kMinBuffers;
  sort.attr("DEFAULT_BUFFERS") = ExternalSort::kDefaultBuffers;
  sort.def(py::init<Schema, std::size_t, std::size_t, std::size_t, std::filesystem::path>(),
           py::arg("schema"), py::arg("key_column"),
           py::arg("buffers") = ExternalSort::kDefaultBuffers,
           py::arg("page_size") = kDefaultPageSize,
           py::arg("dir") = std::filesystem::path{})
      .def("sorted", &ExternalSort::sorted, py::arg("source"), py::keep_alive<0, 1>(),
           py::keep_alive<0, 2>(),
           "Ordena el flujo y devuelve otro. Si todo cabe en `buffers` paginas "
           "no toca disco, y `passes()` devuelve 0.")
      .def("size", &ExternalSort::size, "Registros ordenados")
      .def("runs", &ExternalSort::runs, "Runs que produjo la fase 1")
      .def("passes", &ExternalSort::passes, "Pasadas de fusion; 0 si todo cupo en memoria")
      .def("buffers", &ExternalSort::buffers)
      .def("records_per_page", &ExternalSort::records_per_page)
      .def("stats", &ExternalSort::stats, py::return_value_policy::copy,
           "Paginas leidas y escritas de verdad, para contrastar contra la formula")
      .def("reset_stats", &ExternalSort::reset_stats)
      .def("predicted_pages", &ExternalSort::predicted_pages, py::arg("data_pages"),
           "Costo en paginas que la teoria predice para N paginas de datos")
      .def("temp_bytes", &ExternalSort::temp_bytes, "Bytes de los temporales vivos");

  // --- GROUP BY externo (#21) ----------------------------------------------

  py::enum_<Aggregate>(m, "Aggregate", "Funcion de agregacion de un GROUP BY")
      .value("COUNT", Aggregate::Count)
      .value("SUM", Aggregate::Sum)
      .value("MIN", Aggregate::Min)
      .value("MAX", Aggregate::Max)
      .value("AVG", Aggregate::Avg);

  py::class_<AggregateSpec>(
      m, "AggregateSpec",
      "Una agregacion pedida: que funcion, sobre que columna de la ENTRADA, y "
      "como se llama la columna resultante. Un nombre vacio lo genera el motor.")
      .def(py::init<>())
      .def_static("count", &AggregateSpec::count, py::arg("name") = std::string{},
                  "COUNT(*): cuenta filas, la columna se ignora")
      .def_static("of", &AggregateSpec::of, py::arg("func"), py::arg("column"),
                  py::arg("name") = std::string{})
      .def_readwrite("func", &AggregateSpec::func)
      .def_readwrite("column", &AggregateSpec::column)
      .def_readwrite("name", &AggregateSpec::name)
      .def("__repr__", [](const AggregateSpec& a) {
        return "AggregateSpec(" + std::string(to_string(a.func)) + ", col=" +
               std::to_string(a.column) + ")";
      });

  py::class_<ExternalGroupBy> grupo(
      m, "ExternalGroupBy",
      "GROUP BY sobre volumenes que no caben en memoria.\n\n"
      "Dos caminos: por hash, particionando a disco; y por sort, apoyandose en "
      "el external sorting. `AUTO` empieza por hash y cae a sort si una "
      "particion no se deja separar, asi que siempre termina.");
  py::enum_<ExternalGroupBy::Strategy>(grupo, "Strategy")
      .value("AUTO", ExternalGroupBy::Strategy::kAuto)
      .value("HASH", ExternalGroupBy::Strategy::kHash)
      .value("SORT", ExternalGroupBy::Strategy::kSort);
  grupo.attr("MIN_PARTITIONS") = ExternalGroupBy::kMinPartitions;
  grupo.def(py::init<Schema, std::size_t, std::vector<AggregateSpec>,
                     ExternalGroupBy::Strategy, std::size_t, std::size_t,
                     std::filesystem::path>(),
            py::arg("schema"), py::arg("key_column"), py::arg("aggregates"),
            py::arg("strategy") = ExternalGroupBy::Strategy::kAuto,
            py::arg("buffers") = ExternalSort::kDefaultBuffers,
            py::arg("page_size") = kDefaultPageSize,
            py::arg("dir") = std::filesystem::path{})
      .def("output_schema", &ExternalGroupBy::output_schema,
           py::return_value_policy::copy,
           "Esquema de la SALIDA: la clave seguida de una columna por "
           "agregacion. No es el de la entrada.")
      .def("grouped", &ExternalGroupBy::grouped, py::arg("source"), py::keep_alive<0, 1>(),
           py::keep_alive<0, 2>(),
           "Agrupa y devuelve el resultado como flujo. Con SORT las filas salen "
           "ordenadas por la clave; con HASH salen en orden de particion, que no "
           "es ningun orden util: `used()` dice cual fue.")
      .def("used", &ExternalGroupBy::used, "Estrategia que se termino usando")
      .def("groups", &ExternalGroupBy::groups)
      .def("rows", &ExternalGroupBy::rows)
      .def("partitions", &ExternalGroupBy::partitions)
      .def("repartitions", &ExternalGroupBy::repartitions,
           "Veces que hubo que re-particionar: la medida del sesgo de la clave")
      .def("fell_back", &ExternalGroupBy::fell_back, "true si el hash no alcanzo y cayo a sort")
      .def("stats", &ExternalGroupBy::stats, py::return_value_policy::copy)
      .def("reset_stats", &ExternalGroupBy::reset_stats)
      .def("temp_bytes", &ExternalGroupBy::temp_bytes);

  // --- JOIN externo (#22) --------------------------------------------------

  py::class_<JoinProbe>(
      m, "JoinProbe",
      "El lado interno de un index nested loop: dame las filas cuya clave de "
      "join es esta. Se construye con `probe_of`.")
      .def("matches", &JoinProbe::matches, py::arg("key"))
      .def("schema", &JoinProbe::schema, py::return_value_policy::copy)
      .def("structure", &JoinProbe::structure,
           "El `kind::` de lo que sondea: es lo que va como `structure` del paso "
           "en el plan (ADR 0002)")
      .def("rows", &JoinProbe::rows)
      .def("probe_cost", &JoinProbe::probe_cost, "Paginas que cuesta una sonda, medido")
      .def("stats", &JoinProbe::stats, py::return_value_policy::copy)
      .def("reset_stats", &JoinProbe::reset_stats);

  m.def(
      "probe_of", [](Index& ix, TableFile& datos) { return probe_of(ix, datos); },
      py::arg("index"), py::arg("data"), py::keep_alive<0, 1>(), py::keep_alive<0, 2>(),
      "Sonda por indice secundario: busca los RIDs en el indice y los resuelve "
      "en la tabla.");

  m.def(
      "probe_of", [](TableFile& tabla) { return probe_of(tabla); }, py::arg("table"),
      py::keep_alive<0, 1>(),
      "Sonda por clave primaria, con `TableFile::search` sobre la propia tabla. "
      "Es lo que convierte al B+ agrupado y al secuencial en caminos de join. "
      "Lanza SchemaError si la clave de join no es la clave de la tabla.");

  py::class_<ExternalJoin> join(
      m, "ExternalJoin",
      "Equijoin INTERNO de dos flujos por una columna de cada lado.\n\n"
      "Dos caminos: hash join por particiones en disco, e index nested loop "
      "cuando el lado derecho se puede sondear. Cual gana NO es cuestion de que "
      "exista un indice sino del tamaño relativo -- ver la tabla medida en "
      "external_join.hpp -- y `AUTO` lo decide con esa regla.");
  py::enum_<ExternalJoin::Strategy>(join, "Strategy")
      .value("AUTO", ExternalJoin::Strategy::kAuto)
      .value("HASH", ExternalJoin::Strategy::kHash)
      .value("INDEX_NESTED", ExternalJoin::Strategy::kIndexNested);
  join.attr("MIN_PARTITIONS") = ExternalJoin::kMinPartitions;
  join.attr("MIN_BUFFERS") = ExternalJoin::kMinBuffers;
  join.def(py::init<Schema, std::size_t, Schema, std::size_t, ExternalJoin::Strategy,
                    std::size_t, std::size_t, std::filesystem::path>(),
           py::arg("left"), py::arg("left_column"), py::arg("right"), py::arg("right_column"),
           py::arg("strategy") = ExternalJoin::Strategy::kAuto,
           py::arg("buffers") = ExternalSort::kDefaultBuffers,
           py::arg("page_size") = kDefaultPageSize,
           py::arg("dir") = std::filesystem::path{})
      .def("output_schema", &ExternalJoin::output_schema, py::return_value_policy::copy,
           "Las columnas de la izquierda seguidas de las de la derecha. Las que "
           "se llaman igual en los dos lados salen prefijadas por su tabla; la "
           "columna de join sale dos veces, una por lado.")
      .def("joined",
           py::overload_cast<RecordSource&, RecordSource&>(&ExternalJoin::joined),
           py::arg("left"), py::arg("right"), py::keep_alive<0, 1>(), py::keep_alive<0, 2>(),
           py::keep_alive<0, 3>(),
           "Junta dos flujos. Sin sonda no hay camino por indice, asi que "
           "siempre es hash join; INDEX_NESTED lanza Unsupported aqui.")
      .def("joined",
           py::overload_cast<RecordSource&, JoinProbe&, RecordSource&, std::size_t>(
               &ExternalJoin::joined),
           py::arg("left"), py::arg("probe"), py::arg("right"), py::arg("left_rows"),
           py::keep_alive<0, 1>(), py::keep_alive<0, 2>(), py::keep_alive<0, 3>(),
           py::keep_alive<0, 4>(),
           "Junta cuando el lado derecho se puede sondear. Pide las dos formas "
           "del lado derecho porque AUTO elige entre ellas. `left_rows` es "
           "cuantas filas trae el lado externo, que AUTO necesita para decidir "
           "y no puede averiguar sin consumir el flujo: un 0 significa 'no lo "
           "se' y elige hash join, que es la respuesta segura.")
      .def_static("conviene_index_nested", &ExternalJoin::conviene_index_nested,
                  py::arg("outer_rows"), py::arg("outer_pages"), py::arg("inner_pages"),
                  py::arg("probe_cost"),
                  "La regla medida, expuesta aparte para que el planner pueda "
                  "explicar por que eligio lo que eligio sin correr el join.")
      .def("used", &ExternalJoin::used)
      .def("structure", &ExternalJoin::structure,
           "'external_hash' con hash join, y el `kind::` de lo que sondeo con INL")
      .def("left_rows", &ExternalJoin::left_rows)
      .def("right_rows", &ExternalJoin::right_rows)
      .def("output_rows", &ExternalJoin::output_rows)
      .def("partitions", &ExternalJoin::partitions)
      .def("blocked_partitions", &ExternalJoin::blocked_partitions,
           "Particiones que no cupieron y hubo que recorrer por bloques")
      .def("left_records_per_page", &ExternalJoin::left_records_per_page)
      .def("right_records_per_page", &ExternalJoin::right_records_per_page)
      .def("stats", &ExternalJoin::stats, py::return_value_policy::copy)
      .def("reset_stats", &ExternalJoin::reset_stats)
      .def_static("predicted_pages", &ExternalJoin::predicted_pages, py::arg("left_pages"),
                  py::arg("right_pages"),
                  "3(N_R + N_S): dos pasadas de particionado mas una de sondeo")
      .def("temp_bytes", &ExternalJoin::temp_bytes);

  // --- utilidades de archivo ----------------------------------------------

  m.def("file_size",
        [](TableFile& t) -> std::uintmax_t {
          if (auto* h = dynamic_cast<HeapFile*>(&t)) return h->file_size();
          if (auto* s = dynamic_cast<SequentialFile*>(&t)) return s->file_size();
          if (auto* b = dynamic_cast<BPlusClusteredTable*>(&t)) return b->file_size();
          throw Unsupported("esa organizacion no reporta tamano de archivo");
        },
        py::arg("table"), "Bytes que ocupa el archivo de la tabla (espacio en disco, 2.1.6)");

  m.def("wasted_ratio",
        [](TableFile& t) -> double {
          if (auto* s = dynamic_cast<SequentialFile*>(&t)) return s->wasted_ratio();
          throw Unsupported("solo el archivo secuencial lleva cuenta del espacio desperdiciado");
        },
        py::arg("table"), "Fraccion desperdiciada por el borrado lazy (umbral del 30%, #12)");

  m.def("reorganize",
        [](TableFile& t) -> double {
          if (auto* s = dynamic_cast<SequentialFile*>(&t)) return s->reorganize();
          throw Unsupported("solo el archivo secuencial se reorganiza");
        },
        py::arg("table"), "Reorganiza y devuelve los milisegundos que tardo (#12)");
}
