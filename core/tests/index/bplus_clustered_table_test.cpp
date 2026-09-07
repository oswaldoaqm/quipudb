// Pruebas del indice B+ agrupado (issue #15).
//
// El criterio central del issue es comparar los resultados contra el escaneo
// secuencial: casi todas las pruebas cargan los mismos datos en un
// SequentialFile y en un BPlusClusteredTable y exigen que devuelvan lo mismo.

#include <gtest/gtest.h>

#include <algorithm>
#include <filesystem>
#include <numeric>
#include <random>
#include <string>
#include <vector>

#include "quipudb/error.hpp"
#include "quipudb/index/bplus_clustered_table.hpp"
#include "quipudb/storage/sequential_file.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

Schema alumnos() {
  return Schema{
      .table_name = "alumnos",
      .columns = {{"codigo", DataType::Int},
                  {"nombre", DataType::Varchar, 16},
                  {"promedio", DataType::Double}},
      .key_column = 0,
  };
}

Record alumno(std::int32_t codigo) {
  return {codigo, "alumno" + std::to_string(codigo), codigo * 0.25};
}

std::int32_t codigo_de(const Record& r) { return std::get<std::int32_t>(r[0]); }

bool ordenado(const std::vector<Record>& rs) {
  return std::is_sorted(rs.begin(), rs.end(),
                        [](const Record& a, const Record& b) { return codigo_de(a) < codigo_de(b); });
}

class BPlusClusteredTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_bpc_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
    path_ = dir_ / "alumnos.bplus_clustered";
  }
  void TearDown() override { fs::remove_all(dir_); }

  /// Las mismas claves, en el mismo orden, en las dos organizaciones.
  std::vector<std::int32_t> claves_barajadas(int n, unsigned semilla) {
    std::vector<std::int32_t> cs(static_cast<std::size_t>(n));
    std::iota(cs.begin(), cs.end(), 1);
    std::shuffle(cs.begin(), cs.end(), std::mt19937{semilla});
    return cs;
  }

  fs::path dir_;
  fs::path path_;
};

TEST_F(BPlusClusteredTest, EmpiezaVaciaYSeDeclaraComoAgrupada) {
  BPlusClusteredTable t(path_, alumnos(), 512);
  EXPECT_EQ(t.kind(), kind::kBPlusClustered)
      << "es la cadena que el plan de ejecucion muestra como estructura usada";
  EXPECT_EQ(t.size(), 0u);
  EXPECT_EQ(t.height(), 0u);
  EXPECT_TRUE(t.scan().empty());
  EXPECT_TRUE(t.search(Value{1}).empty());
  EXPECT_TRUE(t.range_search(Value{1}, Value{100}).empty());
  EXPECT_EQ(t.remove(Value{1}), 0u);
  EXPECT_EQ(t.check_invariants(), "");
}

TEST_F(BPlusClusteredTest, LosRegistrosViejanEnLasHojasYSalenOrdenados) {
  BPlusClusteredTable t(path_, alumnos(), 512, 4);
  for (const auto c : claves_barajadas(1000, 11)) t.insert(alumno(c));
  EXPECT_EQ(t.size(), 1000u);
  EXPECT_EQ(t.check_invariants(), "");
  EXPECT_GT(t.height(), 2u);

  const auto todos = t.scan();
  ASSERT_EQ(todos.size(), 1000u);
  EXPECT_TRUE(ordenado(todos));
  for (std::size_t i = 0; i < todos.size(); ++i) {
    EXPECT_EQ(todos[i], alumno(static_cast<std::int32_t>(i) + 1)) << i;
  }
}

TEST_F(BPlusClusteredTest, DevuelveLoMismoQueElArchivoSecuencial) {
  // El criterio del issue: comparar contra el escaneo secuencial.
  const auto cs = claves_barajadas(800, 23);
  BPlusClusteredTable arbol(path_, alumnos(), 512, 4);
  SequentialFile secuencial(dir_ / "alumnos.seq", alumnos(), 512, 1.0);
  for (const auto c : cs) {
    arbol.insert(alumno(c));
    secuencial.insert(alumno(c));
  }
  ASSERT_EQ(arbol.size(), secuencial.size());

  EXPECT_EQ(arbol.scan(), secuencial.scan()) << "mismos registros, mismo orden";

  for (std::int32_t c = 0; c <= 810; c += 7) {
    EXPECT_EQ(arbol.search(Value{c}), secuencial.search(Value{c})) << "clave " << c;
  }
  const std::vector<std::pair<std::int32_t, std::int32_t>> rangos{
      {1, 800}, {100, 200}, {1, 1}, {800, 800}, {0, 0}, {801, 900}, {-5, 3}, {200, 100},
  };
  for (const auto& [lo, hi] : rangos) {
    EXPECT_EQ(arbol.range_search(Value{lo}, Value{hi}),
              secuencial.range_search(Value{lo}, Value{hi}))
        << "rango [" << lo << ", " << hi << "]";
  }
}

TEST_F(BPlusClusteredTest, SigueCoincidiendoDespuesDeBorrarYActualizar) {
  const auto cs = claves_barajadas(500, 31);
  BPlusClusteredTable arbol(path_, alumnos(), 512, 4);
  SequentialFile secuencial(dir_ / "alumnos.seq", alumnos(), 512, 1.0);
  for (const auto c : cs) {
    arbol.insert(alumno(c));
    secuencial.insert(alumno(c));
  }
  for (std::int32_t c = 1; c <= 500; c += 3) {
    EXPECT_EQ(arbol.remove(Value{c}), secuencial.remove(Value{c})) << c;
  }
  for (std::int32_t c = 2; c <= 500; c += 11) {
    Record nuevo = alumno(c);
    nuevo[2] = -1.0;
    EXPECT_EQ(arbol.update(Value{c}, nuevo), secuencial.update(Value{c}, nuevo)) << c;
  }
  EXPECT_EQ(arbol.size(), secuencial.size());
  EXPECT_EQ(arbol.scan(), secuencial.scan());
  EXPECT_EQ(arbol.check_invariants(), "") << "borrar rebalancea y deja el arbol correcto (#17)";
}

TEST_F(BPlusClusteredTest, LaBusquedaBajaUnaVezPorNivel) {
  BPlusClusteredTable t(path_, alumnos(), 512, 4);
  for (const auto c : claves_barajadas(2000, 5)) t.insert(alumno(c));
  const auto altura = t.height();
  ASSERT_GE(altura, 4u);

  t.reset_stats();
  ASSERT_EQ(t.search(Value{1}).size(), 1u);
  EXPECT_EQ(t.stats().pages_read, altura);
  t.reset_stats();
  ASSERT_EQ(t.search(Value{2000}).size(), 1u);
  EXPECT_EQ(t.stats().pages_read, altura) << "buscar el ultimo cuesta lo mismo que el primero";
}

TEST_F(BPlusClusteredTest, DuplicadosYRegistrosInvalidos) {
  BPlusClusteredTable t(path_, alumnos(), 512);
  t.insert(alumno(5));
  EXPECT_THROW(t.insert(alumno(5)), DuplicateKey);
  EXPECT_EQ(t.size(), 1u);
  EXPECT_THROW(t.insert(Record{1, std::string{"x"}}), InvalidRecord);
  EXPECT_THROW(t.insert(Record{1, std::string(17, 'x'), 0.0}), InvalidRecord);
  EXPECT_EQ(t.size(), 1u);
  EXPECT_EQ(t.check_invariants(), "");
}

TEST_F(BPlusClusteredTest, UpdateNoMueveNadaYExigeLaMismaClave) {
  BPlusClusteredTable t(path_, alumnos(), 512, 4);
  for (std::int32_t c = 1; c <= 200; ++c) t.insert(alumno(c));
  const auto altura = t.height();
  const auto paginas = t.page_count();

  Record nuevo = alumno(100);
  nuevo[1] = std::string("cambiado");
  EXPECT_EQ(t.update(Value{100}, nuevo), 1u);
  EXPECT_EQ(t.search(Value{100})[0], nuevo);
  EXPECT_EQ(t.height(), altura) << "actualizar no toca la estructura";
  EXPECT_EQ(t.page_count(), paginas);
  EXPECT_EQ(t.update(Value{9999}, alumno(9999)), 0u);
  EXPECT_THROW(t.update(Value{100}, alumno(101)), SchemaError);
}

TEST_F(BPlusClusteredTest, ElCursorRecorreLoMismoQueScanYConMemoriaAcotada) {
  BPlusClusteredTable t(path_, alumnos(), 512, 4);
  for (const auto c : claves_barajadas(600, 13)) t.insert(alumno(c));
  for (std::int32_t c = 1; c <= 600; c += 5) t.remove(Value{c});

  std::vector<Record> del_cursor;
  auto cur = t.cursor();
  Record r;
  while (cur->next(r)) del_cursor.push_back(r);
  EXPECT_EQ(del_cursor, t.scan());
  EXPECT_TRUE(ordenado(del_cursor));
  EXPECT_FALSE(cur->next(r));

  BPlusClusteredTable vacia(dir_ / "vacia.bplus_clustered", alumnos(), 512);
  EXPECT_FALSE(vacia.cursor()->next(r));
}

TEST_F(BPlusClusteredTest, ReadResuelveElRidRecienDevuelto) {
  BPlusClusteredTable t(path_, alumnos(), 512, 4);
  const RID rid = t.insert(alumno(10));
  const auto leido = t.read(rid);
  ASSERT_TRUE(leido.has_value());
  EXPECT_EQ(*leido, alumno(10));
  EXPECT_FALSE(t.read(RID{999, 0}).has_value());
  EXPECT_FALSE(t.read(RID{rid.page, 200}).has_value());
}

TEST_F(BPlusClusteredTest, SobreviveAlCerrarYReabrir) {
  {
    BPlusClusteredTable t(path_, alumnos(), 512, 4);
    for (const auto c : claves_barajadas(700, 17)) t.insert(alumno(c));
    for (std::int32_t c = 1; c <= 700; c += 9) t.remove(Value{c});
    t.flush();
  }
  BPlusClusteredTable t(path_, alumnos(), 512, 4);
  EXPECT_EQ(t.check_invariants(), "");
  const auto todos = t.scan();
  EXPECT_TRUE(ordenado(todos));
  EXPECT_EQ(todos.size(), t.size());
  EXPECT_TRUE(t.search(Value{1}).empty()) << "el 1 se habia borrado";
  EXPECT_EQ(t.search(Value{2}).size(), 1u);
  t.insert(alumno(1));
  EXPECT_EQ(t.check_invariants(), "");
}

TEST_F(BPlusClusteredTest, ClaveDeTexto) {
  const Schema cursos{
      .table_name = "cursos",
      .columns = {{"codigo", DataType::Varchar, 8}, {"creditos", DataType::Int}},
      .key_column = 0,
  };
  BPlusClusteredTable t(dir_ / "cursos.bplus_clustered", cursos, 512, 4);
  for (const auto* c : {"CS2032", "BD2", "MA1101", "AI501", "ZZ999", "FI203", "QU100"}) {
    t.insert(Record{std::string{c}, 3});
  }
  const auto todos = t.scan();
  ASSERT_EQ(todos.size(), 7u);
  EXPECT_EQ(std::get<std::string>(todos.front()[0]), "AI501");
  EXPECT_EQ(std::get<std::string>(todos.back()[0]), "ZZ999");
  EXPECT_EQ(t.search(Value{std::string("BD2")}).size(), 1u);
  EXPECT_EQ(t.range_search(Value{std::string("A")}, Value{std::string("D")}).size(), 3u);
  EXPECT_EQ(t.check_invariants(), "");
}

}  // namespace
}  // namespace quipudb
