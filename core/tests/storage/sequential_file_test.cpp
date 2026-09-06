// Pruebas del Archivo Secuencial Paginado (issue #10).

#include <gtest/gtest.h>

#include <algorithm>
#include <filesystem>
#include <numeric>
#include <random>
#include <string>
#include <vector>

#include "quipudb/error.hpp"
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

std::vector<std::int32_t> codigos(const std::vector<Record>& rs) {
  std::vector<std::int32_t> out;
  out.reserve(rs.size());
  for (const auto& r : rs) out.push_back(codigo_de(r));
  return out;
}

bool ordenado(const std::vector<Record>& rs) {
  return std::is_sorted(rs.begin(), rs.end(),
                        [](const Record& a, const Record& b) { return codigo_de(a) < codigo_de(b); });
}

class SequentialFileTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_seq_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
    path_ = dir_ / "alumnos.seq";
  }
  void TearDown() override { fs::remove_all(dir_); }

  fs::path dir_;
  fs::path path_;
};

TEST_F(SequentialFileTest, EmpiezaVacio) {
  SequentialFile s(path_, alumnos(), 512);
  EXPECT_EQ(s.size(), 0u);
  EXPECT_EQ(s.kind(), kind::kSequential);
  EXPECT_EQ(s.main_pages(), 0u);
  EXPECT_EQ(s.overflow_pages(), 0u);
  EXPECT_TRUE(s.scan().empty());
  EXPECT_TRUE(s.search(Value{1}).empty());
  EXPECT_EQ(s.remove(Value{1}), 0u);
  // 512 - 8 de cabecera - 4 de la cabeza de overflow = 500; slot = 29.
  EXPECT_EQ(s.slot_size(), 29u);
  EXPECT_EQ(s.slots_per_page(), 500u / 29u);
}

TEST_F(SequentialFileTest, InsercionAleatoriaYElScanSaleOrdenado) {
  // La prueba que pide el issue.
  SequentialFile s(path_, alumnos(), 512);
  std::vector<std::int32_t> cs(1000);
  std::iota(cs.begin(), cs.end(), 1);
  std::shuffle(cs.begin(), cs.end(), std::mt19937{20260906});
  for (const auto c : cs) s.insert(alumno(c));

  EXPECT_EQ(s.size(), 1000u);
  const auto todos = s.scan();
  ASSERT_EQ(todos.size(), 1000u);
  EXPECT_TRUE(ordenado(todos)) << "el scan tiene que salir ordenado por clave";
  std::vector<std::int32_t> esperado(1000);
  std::iota(esperado.begin(), esperado.end(), 1);
  EXPECT_EQ(codigos(todos), esperado);
  EXPECT_GT(s.overflow_pages(), 0u) << "con insercion aleatoria tiene que haber overflow";
}

TEST_F(SequentialFileTest, NoDegeneraEnUnMontonDeCadenasDeOverflow) {
  // Sin partir los grupos, el area principal deja de crecer en cuanto se
  // llena la primera pagina y todo termina en overflow: 3 paginas principales
  // contra 714 de overflow con 100 000 claves, y la carga pasa de medio
  // segundo a 93. Esta prueba fija el limite.
  SequentialFile s(path_, alumnos(), 512);
  std::vector<std::int32_t> cs(2000);
  std::iota(cs.begin(), cs.end(), 1);
  std::shuffle(cs.begin(), cs.end(), std::mt19937{2026});
  for (const auto c : cs) s.insert(alumno(c));

  EXPECT_EQ(s.size(), 2000u);
  EXPECT_TRUE(ordenado(s.scan()));
  EXPECT_GT(s.main_pages(), 20u) << "el area principal tiene que crecer con los datos";
  EXPECT_LT(s.overflow_pages(), s.main_pages())
      << "el overflow tiene que quedar acotado, no absorber el archivo";
}

TEST_F(SequentialFileTest, CargaEnOrdenAscendenteNoGeneraOverflow) {
  SequentialFile s(path_, alumnos(), 512);
  for (std::int32_t i = 1; i <= 1000; ++i) s.insert(alumno(i));
  EXPECT_EQ(s.size(), 1000u);
  EXPECT_EQ(s.overflow_pages(), 0u) << "cada clave es mayor que todas: solo area principal";
  EXPECT_TRUE(ordenado(s.scan()));
  const auto por_pagina = s.slots_per_page();
  EXPECT_EQ(s.main_pages(), (1000u + por_pagina - 1) / por_pagina) << "paginas llenas";
}

TEST_F(SequentialFileTest, InsercionDescendenteTambienQuedaOrdenada) {
  SequentialFile s(path_, alumnos(), 512);
  for (std::int32_t i = 500; i >= 1; --i) s.insert(alumno(i));
  const auto todos = s.scan();
  ASSERT_EQ(todos.size(), 500u);
  EXPECT_TRUE(ordenado(todos));
  EXPECT_EQ(codigo_de(todos.front()), 1);
  EXPECT_EQ(codigo_de(todos.back()), 500);
}

TEST_F(SequentialFileTest, ElOrdenSeMantieneDentroYEntrePaginas) {
  SequentialFile s(path_, alumnos(), 256);
  std::vector<std::int32_t> cs{50, 10, 90, 30, 70, 20, 80, 40, 60, 100};
  for (const auto c : cs) s.insert(alumno(c));
  EXPECT_EQ(codigos(s.scan()),
            (std::vector<std::int32_t>{10, 20, 30, 40, 50, 60, 70, 80, 90, 100}));
  // Insertar al principio de todo tambien respeta el orden.
  s.insert(alumno(5));
  s.insert(alumno(1));
  const auto todos = s.scan();
  EXPECT_EQ(codigo_de(todos[0]), 1);
  EXPECT_EQ(codigo_de(todos[1]), 5);
  EXPECT_TRUE(ordenado(todos));
}

TEST_F(SequentialFileTest, ClaveDuplicadaLanzaEsteDondeEste) {
  SequentialFile s(path_, alumnos(), 256);
  const auto por_pagina = s.slots_per_page();
  // Se llena la primera pagina y se fuerza overflow con claves intercaladas.
  for (std::size_t i = 0; i < por_pagina; ++i) {
    s.insert(alumno(static_cast<std::int32_t>(i * 10)));
  }
  s.insert(alumno(5));  // cae en medio: va a overflow
  ASSERT_GT(s.overflow_pages(), 0u);
  EXPECT_THROW(s.insert(alumno(0)), DuplicateKey) << "duplicado en el area principal";
  EXPECT_THROW(s.insert(alumno(5)), DuplicateKey) << "duplicado en el area de overflow";
  EXPECT_EQ(s.size(), por_pagina + 1);
}

TEST_F(SequentialFileTest, RegistroInvalidoLanza) {
  SequentialFile s(path_, alumnos(), 512);
  EXPECT_THROW(s.insert(Record{1, std::string{"x"}}), InvalidRecord);
  EXPECT_THROW(s.insert(Record{1, std::string(17, 'x'), 0.0}), InvalidRecord);
  EXPECT_EQ(s.size(), 0u);
}

TEST_F(SequentialFileTest, BusquedaYRangoEncuentranEnPrincipalYEnOverflow) {
  SequentialFile s(path_, alumnos(), 256);
  std::vector<std::int32_t> cs(200);
  std::iota(cs.begin(), cs.end(), 1);
  std::shuffle(cs.begin(), cs.end(), std::mt19937{7});
  for (const auto c : cs) s.insert(alumno(c));
  ASSERT_GT(s.overflow_pages(), 0u);

  for (const std::int32_t c : {1, 57, 128, 199, 200}) {
    const auto out = s.search(Value{c});
    ASSERT_EQ(out.size(), 1u) << c;
    EXPECT_EQ(out[0], alumno(c)) << c;
  }
  EXPECT_TRUE(s.search(Value{999}).empty());

  const auto rango = s.range_search(Value{50}, Value{60});
  ASSERT_EQ(rango.size(), 11u) << "inclusivo en ambos extremos";
  EXPECT_TRUE(ordenado(rango));
  EXPECT_EQ(codigo_de(rango.front()), 50);
  EXPECT_EQ(codigo_de(rango.back()), 60);
  EXPECT_TRUE(s.range_search(Value{500}, Value{600}).empty());
}

TEST_F(SequentialFileTest, RemoveMarcaYDejaDeAparecer) {
  SequentialFile s(path_, alumnos(), 256);
  for (std::int32_t i = 1; i <= 100; ++i) s.insert(alumno(i));
  const auto bytes = s.file_size();

  EXPECT_EQ(s.remove(Value{50}), 1u);
  EXPECT_EQ(s.remove(Value{50}), 0u);
  EXPECT_EQ(s.size(), 99u);
  EXPECT_TRUE(s.search(Value{50}).empty());
  const auto todos = s.scan();
  EXPECT_EQ(todos.size(), 99u);
  EXPECT_TRUE(ordenado(todos));
  const auto cs = codigos(todos);  // en una variable: begin() y end() de dos
  EXPECT_EQ(std::count(cs.begin(), cs.end(), 50), 0);  // temporales distintos no valen
  EXPECT_EQ(s.file_size(), bytes) << "borrar no reorganiza ni encoge el archivo";
}

TEST_F(SequentialFileTest, ReadResuelveElRidRecienDevuelto) {
  SequentialFile s(path_, alumnos(), 512);
  const RID a = s.insert(alumno(10));
  ASSERT_TRUE(s.read(a).has_value());
  EXPECT_EQ(*s.read(a), alumno(10));
  EXPECT_FALSE(s.read(RID{99, 0}).has_value());
  EXPECT_FALSE(s.read(RID{1, 200}).has_value());
  s.remove(Value{10});
  EXPECT_FALSE(s.read(a).has_value()) << "un registro marcado no se lee";
}

TEST_F(SequentialFileTest, TodoSobreviveAlCerrarYReabrir) {
  std::vector<std::int32_t> cs(400);
  std::iota(cs.begin(), cs.end(), 1);
  std::shuffle(cs.begin(), cs.end(), std::mt19937{99});
  {
    SequentialFile s(path_, alumnos(), 512);
    for (const auto c : cs) s.insert(alumno(c));
    s.remove(Value{200});
    s.flush();
  }
  SequentialFile s(path_, alumnos(), 512);
  EXPECT_EQ(s.size(), 399u);
  const auto todos = s.scan();
  ASSERT_EQ(todos.size(), 399u);
  EXPECT_TRUE(ordenado(todos)) << "el directorio se reconstruyo bien";
  EXPECT_TRUE(s.search(Value{200}).empty());
  EXPECT_EQ(s.search(Value{201}).size(), 1u);
  EXPECT_THROW(s.insert(alumno(201)), DuplicateKey);
  EXPECT_NO_THROW(s.insert(alumno(200))) << "la clave marcada se puede reinsertar";
  EXPECT_TRUE(ordenado(s.scan()));
}

TEST_F(SequentialFileTest, RechazaAbrirConOtroEsquemaOTamano) {
  {
    SequentialFile s(path_, alumnos(), 512);
    s.insert(alumno(1));
  }
  Schema otro = alumnos();
  otro.columns[1].length = 32;
  EXPECT_THROW(SequentialFile(path_, otro, 512), SchemaError);
  EXPECT_THROW(SequentialFile(path_, alumnos(), 1024), IoError);
}

TEST_F(SequentialFileTest, RechazaUnRegistroQueNoDejaDosSlotsPorPagina) {
  Schema gordo = alumnos();
  gordo.columns[1].length = 200;
  EXPECT_THROW(SequentialFile(dir_ / "gordo.seq", gordo, 256), SchemaError);
}

TEST_F(SequentialFileTest, FuncionaConVariosTamanosDePaginaYClaveDeTexto) {
  for (const std::size_t sz : {256u, 1024u, 4096u}) {
    const fs::path p = dir_ / ("t" + std::to_string(sz) + ".seq");
    SequentialFile s(p, alumnos(), sz);
    std::vector<std::int32_t> cs(300);
    std::iota(cs.begin(), cs.end(), 1);
    std::shuffle(cs.begin(), cs.end(), std::mt19937{static_cast<unsigned>(sz)});
    for (const auto c : cs) s.insert(alumno(c));
    EXPECT_EQ(s.size(), 300u) << sz;
    EXPECT_TRUE(ordenado(s.scan())) << sz;
  }
  const Schema cursos{
      .table_name = "cursos",
      .columns = {{"codigo", DataType::Varchar, 8}, {"creditos", DataType::Int}},
      .key_column = 0,
  };
  SequentialFile s(dir_ / "cursos.seq", cursos, 256);
  for (const auto* c : {"CS2032", "BD2", "MA1101", "AI501", "ZZ999"}) {
    s.insert(Record{std::string{c}, 3});
  }
  const auto todos = s.scan();
  ASSERT_EQ(todos.size(), 5u);
  EXPECT_EQ(std::get<std::string>(todos[0][0]), "AI501");
  EXPECT_EQ(std::get<std::string>(todos[4][0]), "ZZ999");
}

TEST_F(SequentialFileTest, LasEstadisticasCuentanPaginas) {
  SequentialFile s(path_, alumnos(), 256);
  for (std::int32_t i = 1; i <= 100; ++i) s.insert(alumno(i));
  s.reset_stats();
  static_cast<void>(s.scan());
  EXPECT_GE(s.stats().pages_read, s.main_pages());
  EXPECT_EQ(s.stats().records_returned, 100u);
  s.reset_stats();
  s.insert(alumno(101));
  EXPECT_GT(s.stats().pages_written, 0u);
}

}  // namespace
}  // namespace quipudb
