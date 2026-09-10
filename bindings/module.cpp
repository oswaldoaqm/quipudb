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
//                Existe para que el external sorting del #20 sea externo de
//                verdad, y el #20 es C++: nadie en Python lo necesita.
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
