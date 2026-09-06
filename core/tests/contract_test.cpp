// Pruebas del contrato del core (issue #3).
//
// Aqui no hay disco: las dos implementaciones de abajo viven en memoria y
// existen para tres cosas:
//   1. demostrar que las interfaces `TableFile` e `Index` se pueden
//      implementar tal como estan (si el contrato tuviera una firma
//      imposible, esto no compilaria);
//   2. fijar la semantica esperada (rango inclusivo, duplicados, vacios,
//      excepciones) como pruebas ejecutables, para que heap file, secuencial,
//      B+ y hash puedan reutilizarlas;
//   3. servir de oraculo en los benchmarks: lo que devuelve la version en
//      memoria es lo que debe devolver la version en disco.

#include <gtest/gtest.h>

#include <cmath>
#include <limits>
#include <map>
#include <string>
#include <vector>

#include "quipudb/catalog/table.hpp"

namespace quipudb {
namespace {

// ---------------------------------------------------------------------------
// Implementaciones en memoria
// ---------------------------------------------------------------------------

class MemoryTable final : public TableFile {
 public:
  explicit MemoryTable(Schema schema) : schema_(std::move(schema)) {}

  const Schema& schema() const noexcept override { return schema_; }
  std::string_view kind() const noexcept override { return kind::kHeap; }

  RID insert(const Record& record) override {
    schema_.validate(record);
    const Key& key = schema_.key_of(record);
    if (by_key_.contains(key)) throw DuplicateKey("clave repetida");
    const RID rid{0, static_cast<SlotId>(slots_.size())};
    slots_.push_back(record);
    by_key_.emplace(key, rid);
    stats_.pages_written += 1;
    return rid;
  }

  std::size_t remove(const Key& key) override {
    auto it = by_key_.find(key);
    if (it == by_key_.end()) return 0;
    slots_[it->second.slot].reset();
    by_key_.erase(it);
    stats_.pages_written += 1;
    return 1;
  }

  std::vector<Record> search(const Key& key) override {
    std::vector<Record> out;
    if (auto it = by_key_.find(key); it != by_key_.end()) {
      out.push_back(*slots_[it->second.slot]);
    }
    stats_.pages_read += 1;
    stats_.records_examined += 1;
    stats_.records_returned += out.size();
    return out;
  }

  std::vector<Record> range_search(const Key& lo, const Key& hi) override {
    std::vector<Record> out;
    for (auto it = by_key_.lower_bound(lo); it != by_key_.end() && compare(it->first, hi) <= 0;
         ++it) {
      out.push_back(*slots_[it->second.slot]);
      stats_.records_examined += 1;
    }
    stats_.pages_read += 1;
    stats_.records_returned += out.size();
    return out;
  }

  std::size_t update(const Key& key, const Record& record) override {
    schema_.validate(record);
    if (compare(schema_.key_of(record), key) != 0) throw SchemaError("cambia la clave");
    auto it = by_key_.find(key);
    if (it == by_key_.end()) return 0;
    slots_[it->second.slot] = record;
    stats_.pages_written += 1;
    return 1;
  }

  std::vector<Record> scan() override {
    std::vector<Record> out;
    for (const auto& s : slots_) {
      stats_.records_examined += 1;
      if (s) out.push_back(*s);
    }
    stats_.pages_read += 1;
    stats_.records_returned += out.size();
    return out;
  }

  std::unique_ptr<RecordCursor> cursor() override {
    // Cursor de juguete sobre el vector: fija la semantica que las
    // implementaciones de verdad tienen que respetar.
    class C final : public RecordCursor {
     public:
      explicit C(MemoryTable& t) : t_(t) {}
      bool next(Record& out) override {
        while (i_ < t_.slots_.size()) {
          const auto& s = t_.slots_[i_++];
          if (s) {
            out = *s;
            return true;
          }
        }
        return false;
      }

     private:
      MemoryTable& t_;
      std::size_t i_ = 0;
    };
    return std::make_unique<C>(*this);
  }

  std::optional<Record> read(RID rid) override {
    stats_.pages_read += 1;
    if (rid.page != 0 || rid.slot >= slots_.size()) return std::nullopt;
    return slots_[rid.slot];
  }

  std::size_t size() const override { return by_key_.size(); }
  const OpStats& stats() const noexcept override { return stats_; }
  void reset_stats() noexcept override { stats_.reset(); }

 private:
  Schema schema_;
  std::vector<std::optional<Record>> slots_;
  std::map<Key, RID, KeyLess> by_key_;
  OpStats stats_;
};

class MemoryIndex final : public Index {
 public:
  MemoryIndex(DataType key_type, bool ordered) : key_type_(key_type), ordered_(ordered) {}

  std::string_view kind() const noexcept override {
    return ordered_ ? kind::kBPlusUnclustered : kind::kExtendibleHash;
  }
  DataType key_type() const noexcept override { return key_type_; }
  bool supports_range() const noexcept override { return ordered_; }

  void insert(const Key& key, RID rid) override {
    check(key);
    entries_.emplace(key, rid);
    stats_.pages_written += 1;
  }

  std::size_t remove(const Key& key) override {
    check(key);
    const auto n = entries_.erase(key);
    stats_.pages_written += n > 0 ? 1 : 0;
    return n;
  }

  bool remove(const Key& key, RID rid) override {
    check(key);
    auto [lo, hi] = entries_.equal_range(key);
    for (auto it = lo; it != hi; ++it) {
      if (it->second == rid) {
        entries_.erase(it);
        stats_.pages_written += 1;
        return true;
      }
    }
    return false;
  }

  std::vector<RID> search(const Key& key) override {
    check(key);
    std::vector<RID> out;
    auto [lo, hi] = entries_.equal_range(key);
    for (auto it = lo; it != hi; ++it) out.push_back(it->second);
    stats_.pages_read += 1;
    stats_.records_examined += out.size();
    stats_.records_returned += out.size();
    return out;
  }

  std::vector<RID> range_search(const Key& lo, const Key& hi) override {
    if (!ordered_) throw Unsupported("extendible hashing no soporta rangos");
    check(lo);
    check(hi);
    std::vector<RID> out;
    for (auto it = entries_.lower_bound(lo); it != entries_.end() && compare(it->first, hi) <= 0;
         ++it) {
      out.push_back(it->second);
    }
    stats_.pages_read += 1;
    stats_.records_examined += out.size();
    stats_.records_returned += out.size();
    return out;
  }

  std::vector<std::pair<Key, RID>> scan() override {
    std::vector<std::pair<Key, RID>> out(entries_.begin(), entries_.end());
    stats_.pages_read += 1;
    stats_.records_examined += out.size();
    stats_.records_returned += out.size();
    return out;
  }

  std::size_t size() const override { return entries_.size(); }
  const OpStats& stats() const noexcept override { return stats_; }
  void reset_stats() noexcept override { stats_.reset(); }

 private:
  void check(const Key& key) const {
    if (type_of(key) != key_type_) throw SchemaError("tipo de clave incorrecto");
  }

  DataType key_type_;
  bool ordered_;
  std::multimap<Key, RID, KeyLess> entries_;
  OpStats stats_;
};

// ---------------------------------------------------------------------------
// Utilidades
// ---------------------------------------------------------------------------

Schema alumnos() {
  return Schema{
      .table_name = "alumnos",
      .columns = {{"codigo", DataType::Int},
                  {"nombre", DataType::Varchar, 20},
                  {"promedio", DataType::Double},
                  {"activo", DataType::Bool},
                  {"ingreso", DataType::Date}},
      .key_column = 0,
  };
}

Record alumno(std::int32_t codigo, std::string nombre, double promedio) {
  return {codigo, std::move(nombre), promedio, true, Date{20000}};
}

// ---------------------------------------------------------------------------
// Tipos y esquema
// ---------------------------------------------------------------------------

TEST(Tipos, CompareOrdenaCadaTipo) {
  EXPECT_LT(compare(Value{1}, Value{2}), 0);
  EXPECT_GT(compare(Value{2.5}, Value{2.0}), 0);
  EXPECT_EQ(compare(Value{std::string{"ana"}}, Value{std::string{"ana"}}), 0);
  EXPECT_LT(compare(Value{std::string{"ana"}}, Value{std::string{"beto"}}), 0);
  EXPECT_LT(compare(Value{false}, Value{true}), 0);
  EXPECT_LT(compare(Value{Date{1}}, Value{Date{2}}), 0);
}

TEST(Tipos, CompareRechazaTiposDistintos) {
  EXPECT_THROW(static_cast<void>(compare(Value{1}, Value{1.0})), SchemaError);
  EXPECT_THROW(static_cast<void>(compare(Value{Date{1}}, Value{1})),
               SchemaError);  // Date no es Int
}

TEST(Tipos, TypeOfCoincideConDataType) {
  EXPECT_EQ(type_of(Value{1}), DataType::Int);
  EXPECT_EQ(type_of(Value{1.0}), DataType::Double);
  EXPECT_EQ(type_of(Value{std::string{}}), DataType::Varchar);
  EXPECT_EQ(type_of(Value{"literal"}), DataType::Varchar);  // no cae en bool (C++20)
  EXPECT_EQ(type_of(Value{true}), DataType::Bool);
  EXPECT_EQ(type_of(Value{Date{}}), DataType::Date);
}

TEST(Esquema, TamanoDeRegistroEsFijo) {
  const auto s = alumnos();
  EXPECT_EQ(s.record_size(), 4u + 20u + 8u + 1u + 4u);
  EXPECT_EQ(s.key().name, "codigo");
  EXPECT_EQ(s.find("promedio"), 2u);
  EXPECT_FALSE(s.find("edad").has_value());
}

TEST(Esquema, ValidateDetectaRegistrosMalFormados) {
  const auto s = alumnos();
  EXPECT_NO_THROW(s.validate(alumno(1, "ana", 15.5)));
  EXPECT_THROW(s.validate(Record{1, std::string{"ana"}}), InvalidRecord);  // faltan columnas
  EXPECT_THROW(s.validate(Record{1.0, std::string{"ana"}, 15.5, true, Date{}}),
               InvalidRecord);  // codigo no es Int
  EXPECT_THROW(s.validate(alumno(1, std::string(21, 'x'), 15.5)),
               InvalidRecord);  // supera VARCHAR(20)
}

// --- regresiones de la auditoria de 2.1.1 (#55) ---

TEST(Tipos, NaNTieneUnLugarEnElOrden) {
  // Con `<` a secas un NaN seria "igual" a todo y KeyLess dejaria de ser un
  // orden estricto, que es comportamiento indefinido para los std::map y
  // std::sort del core. Va despues de todo y solo es igual a otro NaN.
  const Value nan{std::nan("")};
  EXPECT_GT(compare(nan, Value{1.0}), 0);
  EXPECT_LT(compare(Value{1.0}, nan), 0);
  EXPECT_GT(compare(nan, Value{1e308}), 0);
  EXPECT_EQ(compare(nan, nan), 0);
  // Y sigue siendo un orden estricto: a<b y b<c implica a<c.
  EXPECT_LT(compare(Value{1.0}, Value{2.0}), 0);
  EXPECT_LT(compare(Value{2.0}, nan), 0);
  EXPECT_LT(compare(Value{1.0}, nan), 0);
}

TEST(Esquema, RechazaUnNaNComoValor) {
  const Schema s{.table_name = "t",
                 .columns = {{"k", DataType::Int}, {"d", DataType::Double}},
                 .key_column = 0};
  EXPECT_NO_THROW(s.validate(Record{1, 1.5}));
  EXPECT_NO_THROW(s.validate(Record{1, std::numeric_limits<double>::infinity()}))
      << "el infinito si tiene lugar en el orden";
  EXPECT_THROW(s.validate(Record{1, std::nan("")}), InvalidRecord);
}

TEST(Esquema, RechazaUnTextoConByteNuloAdentro) {
  // Al serializar se guarda tal cual, pero al leer se corta en el nulo, asi
  // que "ab\0cd" volveria como "ab": perdida silenciosa, y dos claves
  // distintas podrian colapsar en una.
  const Schema s{.table_name = "t",
                 .columns = {{"k", DataType::Int}, {"t", DataType::Varchar, 10}},
                 .key_column = 0};
  EXPECT_NO_THROW(s.validate(Record{1, std::string("hola")}));
  EXPECT_NO_THROW(s.validate(Record{1, std::string{}}));
  EXPECT_THROW(s.validate(Record{1, std::string("ab\0cd", 5)}), InvalidRecord);
  EXPECT_THROW(s.validate(Record{1, std::string("\0", 1)}), InvalidRecord);
}

// ---------------------------------------------------------------------------
// TableFile
// ---------------------------------------------------------------------------

class TablaMemoria : public ::testing::Test {
 protected:
  MemoryTable t{alumnos()};
};

TEST_F(TablaMemoria, InsertDevuelveRidYReadLoResuelve) {
  const RID rid = t.insert(alumno(10, "ana", 15.0));
  ASSERT_TRUE(rid.valid());
  const auto r = t.read(rid);
  ASSERT_TRUE(r.has_value());
  EXPECT_EQ(std::get<std::string>((*r)[1]), "ana");
  EXPECT_FALSE(t.read(RID{0, 99}).has_value());
}

TEST_F(TablaMemoria, ClavePrimariaDuplicadaLanza) {
  t.insert(alumno(10, "ana", 15.0));
  EXPECT_THROW(t.insert(alumno(10, "otra", 12.0)), DuplicateKey);
  EXPECT_EQ(t.size(), 1u);
}

TEST_F(TablaMemoria, RegistroInvalidoLanza) {
  EXPECT_THROW(t.insert(Record{1, std::string{"ana"}}), InvalidRecord);
  EXPECT_EQ(t.size(), 0u);
}

TEST_F(TablaMemoria, SearchDevuelveVacioSiNoExiste) {
  EXPECT_TRUE(t.search(Value{404}).empty());
}

TEST_F(TablaMemoria, RangeSearchEsInclusivoYOrdenado) {
  for (std::int32_t c : {50, 10, 30, 20, 40}) t.insert(alumno(c, "x", 0.0));
  const auto out = t.range_search(Value{20}, Value{40});
  ASSERT_EQ(out.size(), 3u);
  EXPECT_EQ(std::get<std::int32_t>(out[0][0]), 20);
  EXPECT_EQ(std::get<std::int32_t>(out[1][0]), 30);
  EXPECT_EQ(std::get<std::int32_t>(out[2][0]), 40);
  EXPECT_TRUE(t.range_search(Value{60}, Value{70}).empty());
}

TEST_F(TablaMemoria, RemoveDevuelveCuantosQuitoYScanNoLosMuestra) {
  t.insert(alumno(1, "a", 0.0));
  t.insert(alumno(2, "b", 0.0));
  EXPECT_EQ(t.remove(Value{1}), 1u);
  EXPECT_EQ(t.remove(Value{1}), 0u);
  const auto todos = t.scan();
  ASSERT_EQ(todos.size(), 1u);
  EXPECT_EQ(std::get<std::int32_t>(todos[0][0]), 2);
  EXPECT_EQ(t.size(), 1u);
}

TEST_F(TablaMemoria, StatsSeAcumulanYReinician) {
  t.insert(alumno(1, "a", 0.0));
  t.search(Value{1});
  EXPECT_EQ(t.stats().pages_written, 1u);
  EXPECT_EQ(t.stats().records_returned, 1u);
  t.reset_stats();
  EXPECT_EQ(t.stats().pages_read, 0u);
  EXPECT_EQ(t.kind(), kind::kHeap);
}

TEST_F(TablaMemoria, UpdateReemplazaSinMoverYExigeLaMismaClave) {
  t.insert(alumno(1, "ana", 15.0));
  t.insert(alumno(2, "beto", 12.0));
  EXPECT_EQ(t.update(Value{1}, alumno(1, "ana maria", 18.0)), 1u);
  const auto out = t.search(Value{1});
  ASSERT_EQ(out.size(), 1u);
  EXPECT_EQ(std::get<std::string>(out[0][1]), "ana maria");
  EXPECT_EQ(t.size(), 2u) << "actualizar no agrega ni quita filas";

  EXPECT_EQ(t.update(Value{404}, alumno(404, "nadie", 0.0)), 0u);
  EXPECT_THROW(t.update(Value{1}, alumno(9, "otra", 1.0)), SchemaError)
      << "cambiar la clave es remove mas insert";
  EXPECT_THROW(t.update(Value{1}, Record{1, std::string{"x"}}), InvalidRecord);
}

TEST_F(TablaMemoria, ElCursorRecorreLoMismoQueScan) {
  for (std::int32_t i = 1; i <= 20; ++i) t.insert(alumno(i, "n" + std::to_string(i), i * 1.0));
  t.remove(Value{7});
  t.remove(Value{13});

  std::vector<Record> del_cursor;
  auto c = t.cursor();
  Record r;
  while (c->next(r)) del_cursor.push_back(r);
  EXPECT_EQ(del_cursor, t.scan());
  EXPECT_EQ(del_cursor.size(), 18u);

  // Un cursor agotado sigue diciendo que no hay mas.
  EXPECT_FALSE(c->next(r));
  EXPECT_FALSE(c->next(r));
}

// ---------------------------------------------------------------------------
// Index
// ---------------------------------------------------------------------------

TEST(IndiceMemoria, AdmiteClavesRepetidasYRemuevePorPar) {
  MemoryIndex idx(DataType::Int, /*ordered=*/true);
  idx.insert(Value{7}, RID{1, 0});
  idx.insert(Value{7}, RID{1, 1});
  idx.insert(Value{9}, RID{2, 0});
  EXPECT_EQ(idx.search(Value{7}).size(), 2u);
  EXPECT_TRUE(idx.remove(Value{7}, RID{1, 0}));
  EXPECT_FALSE(idx.remove(Value{7}, RID{1, 0}));
  EXPECT_EQ(idx.search(Value{7}).size(), 1u);
  EXPECT_EQ(idx.remove(Value{7}), 1u);
  EXPECT_TRUE(idx.search(Value{7}).empty());
  EXPECT_EQ(idx.size(), 1u);
}

TEST(IndiceMemoria, RangoOrdenadoEnBPlus) {
  MemoryIndex idx(DataType::Int, true);
  for (std::int32_t k : {5, 1, 3, 4, 2}) idx.insert(Value{k}, RID{0, static_cast<SlotId>(k)});
  const auto out = idx.range_search(Value{2}, Value{4});
  ASSERT_EQ(out.size(), 3u);
  EXPECT_EQ(out[0].slot, 2);
  EXPECT_EQ(out[2].slot, 4);
  const auto todo = idx.scan();
  ASSERT_EQ(todo.size(), 5u);
  EXPECT_EQ(std::get<std::int32_t>(todo[0].first), 1);
  EXPECT_EQ(idx.kind(), kind::kBPlusUnclustered);
}

TEST(IndiceMemoria, HashNoSoportaRango) {
  MemoryIndex idx(DataType::Varchar, /*ordered=*/false);
  idx.insert(Value{std::string{"lima"}}, RID{0, 0});
  EXPECT_FALSE(idx.supports_range());
  EXPECT_THROW(idx.range_search(Value{std::string{"a"}}, Value{std::string{"z"}}), Unsupported);
  EXPECT_EQ(idx.search(Value{std::string{"lima"}}).size(), 1u);
  EXPECT_EQ(idx.kind(), kind::kExtendibleHash);
}

TEST(IndiceMemoria, ClaveDeOtroTipoLanza) {
  MemoryIndex idx(DataType::Int, true);
  EXPECT_THROW(idx.insert(Value{1.5}, RID{0, 0}), SchemaError);
}

}  // namespace
}  // namespace quipudb
