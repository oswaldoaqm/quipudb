// Pruebas del Archivo Secuencial Paginado (issue #10).

#include <gtest/gtest.h>

#include <algorithm>
#include <array>
#include <cstring>
#include <filesystem>
#include <numeric>
#include <random>
#include <string>
#include <vector>

#include "quipudb/error.hpp"
#include "quipudb/storage/disk_manager.hpp"
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


// ---------------------------------------------------------------------------
// Eliminacion lazy y cuenta del desperdicio (issue #11)
// ---------------------------------------------------------------------------

TEST_F(SequentialFileTest, BorrarNoCambiaElTamanoDelArchivo) {
  // La prueba que pide el issue.
  SequentialFile s(path_, alumnos(), 512);
  for (std::int32_t i = 1; i <= 500; ++i) s.insert(alumno(i));
  const auto bytes = s.file_size();
  const auto paginas = s.page_count();

  for (std::int32_t i = 1; i <= 100; ++i) s.remove(Value{i});
  EXPECT_EQ(s.size(), 400u);
  EXPECT_EQ(s.file_size(), bytes) << "la eliminacion lazy no encoge el archivo";
  EXPECT_EQ(s.page_count(), paginas);

  // Y ninguno de los borrados aparece por ningun camino.
  const auto todos = s.scan();
  ASSERT_EQ(todos.size(), 400u);
  EXPECT_TRUE(ordenado(todos));
  EXPECT_EQ(codigo_de(todos.front()), 101);
  for (std::int32_t i = 1; i <= 100; i += 7) {
    EXPECT_TRUE(s.search(Value{i}).empty()) << i;
  }
  EXPECT_TRUE(s.range_search(Value{1}, Value{100}).empty());
  EXPECT_EQ(s.range_search(Value{95}, Value{105}).size(), 5u);
}

TEST_F(SequentialFileTest, LaCuentaDelDesperdicioSubeConCadaBorrado) {
  SequentialFile s(path_, alumnos(), 512);
  EXPECT_EQ(s.deleted_records(), 0u);
  EXPECT_EQ(s.wasted_bytes(), 0u);
  EXPECT_DOUBLE_EQ(s.wasted_ratio(), 0.0) << "un archivo vacio no desperdicia nada";

  for (std::int32_t i = 1; i <= 100; ++i) s.insert(alumno(i));
  EXPECT_DOUBLE_EQ(s.wasted_ratio(), 0.0);

  for (std::int32_t i = 1; i <= 30; ++i) s.remove(Value{i});
  EXPECT_EQ(s.deleted_records(), 30u);
  EXPECT_EQ(s.wasted_bytes(), 30u * s.slot_size());
  EXPECT_DOUBLE_EQ(s.wasted_ratio(), 0.30) << "30 marcados de 100 guardados";

  // Borrar algo que no existe no mueve la cuenta.
  EXPECT_EQ(s.remove(Value{999}), 0u);
  EXPECT_EQ(s.deleted_records(), 30u);
}

TEST_F(SequentialFileTest, LaCuentaDelDesperdicioPersiste) {
  {
    SequentialFile s(path_, alumnos(), 512);
    for (std::int32_t i = 1; i <= 200; ++i) s.insert(alumno(i));
    for (std::int32_t i = 1; i <= 50; ++i) s.remove(Value{i});
    s.flush();
  }
  SequentialFile s(path_, alumnos(), 512);
  EXPECT_EQ(s.size(), 150u);
  EXPECT_EQ(s.deleted_records(), 50u);
  EXPECT_DOUBLE_EQ(s.wasted_ratio(), 0.25);
}

TEST_F(SequentialFileTest, PartirUnGrupoRecuperaElEspacioMarcado) {
  // Al partir, los marcados no se copian a las paginas nuevas: es el unico
  // momento en que el desperdicio baja solo, y el contador tiene que seguirlo.
  SequentialFile s(path_, alumnos(), 512);
  const auto por_pagina = s.slots_per_page();
  for (std::size_t i = 0; i < por_pagina * 2; ++i) {
    s.insert(alumno(static_cast<std::int32_t>(i * 10)));
  }
  ASSERT_EQ(s.remove(Value{10}), 1u);
  ASSERT_EQ(s.remove(Value{20}), 1u);
  ASSERT_EQ(s.deleted_records(), 2u);

  // Se llena el overflow del primer grupo hasta que parta, metiendo claves
  // intercaladas dentro de su rango (las que no son multiplo de 10).
  const auto grupos_antes = s.main_pages();
  const std::int32_t tope = static_cast<std::int32_t>(por_pagina) * 10;
  for (std::int32_t k = 1; k < tope; ++k) {
    if (k % 10 != 0) s.insert(alumno(k));
  }
  ASSERT_GT(s.main_pages(), grupos_antes) << "el grupo tuvo que partirse";
  EXPECT_EQ(s.deleted_records(), 0u) << "los marcados del grupo partido se descontaron";

  // Y el archivo sigue coherente al reabrirlo, que es lo que valida los
  // contadores contra las paginas.
  const auto vivos = s.size();
  const auto marcados = s.deleted_records();
  s.flush();
  SequentialFile r(path_, alumnos(), 512);
  EXPECT_EQ(r.size(), vivos);
  EXPECT_EQ(r.deleted_records(), marcados);
  EXPECT_TRUE(ordenado(r.scan()));
}

TEST_F(SequentialFileTest, DesperdicioConBorradosEnOverflow) {
  SequentialFile s(path_, alumnos(), 256);
  std::vector<std::int32_t> cs(300);
  std::iota(cs.begin(), cs.end(), 1);
  std::shuffle(cs.begin(), cs.end(), std::mt19937{11});
  for (const auto c : cs) s.insert(alumno(c));
  ASSERT_GT(s.overflow_pages(), 0u);

  for (std::int32_t i = 1; i <= 60; ++i) s.remove(Value{i * 5});
  EXPECT_EQ(s.size(), 240u);
  EXPECT_EQ(s.deleted_records(), 60u);
  const auto todos = s.scan();
  EXPECT_EQ(todos.size(), 240u);
  EXPECT_TRUE(ordenado(todos));
  for (const auto& r : todos) EXPECT_NE(codigo_de(r) % 5, 0);

  s.flush();
  SequentialFile r(path_, alumnos(), 256);
  EXPECT_EQ(r.deleted_records(), 60u) << "tambien se cuentan los marcados del overflow";
}

TEST_F(SequentialFileTest, DetectaContadoresQueNoCuadran) {
  {
    SequentialFile s(path_, alumnos(), 512);
    for (std::int32_t i = 1; i <= 50; ++i) s.insert(alumno(i));
    s.remove(Value{1});
    s.flush();
  }
  // Meta = version(4) + record_size(4) + live(8) + deleted(8): se miente en
  // la cantidad de marcados.
  {
    DiskManager dm(path_, 512);
    constexpr std::size_t kOffsetDeleted = 16;
    std::array<std::byte, kOffsetDeleted + sizeof(std::uint64_t)> meta{};
    dm.read_meta(meta);
    const std::uint64_t mentira = 99;
    std::memcpy(meta.data() + kOffsetDeleted, &mentira, sizeof mentira);
    dm.write_meta(meta);
    dm.flush();
  }
  EXPECT_THROW(SequentialFile(path_, alumnos(), 512), IoError);
}


// ---------------------------------------------------------------------------
// Reorganizacion al superar el umbral (issue #12)
// ---------------------------------------------------------------------------

TEST_F(SequentialFileTest, SeReorganizaSolaAlPasarElUmbralYMantieneElOrden) {
  // La prueba que pide el issue: llenar, borrar hasta pasar el umbral y
  // verificar que reorganiza y que el orden se mantiene.
  SequentialFile s(path_, alumnos(), 512);
  for (std::int32_t i = 1; i <= 1000; ++i) s.insert(alumno(i));
  const auto bytes_llenos = s.file_size();
  ASSERT_EQ(s.reorganizations(), 0u);

  // 300 de 1000 = 0,30 exacto, que todavia no supera el umbral.
  for (std::int32_t i = 1; i <= 300; ++i) s.remove(Value{i});
  EXPECT_EQ(s.reorganizations(), 0u) << "el umbral es estricto: 0,30 no lo supera";
  EXPECT_DOUBLE_EQ(s.wasted_ratio(), 0.30);

  s.remove(Value{301});  // 301/1000: ahora si
  EXPECT_EQ(s.reorganizations(), 1u);
  EXPECT_EQ(s.deleted_records(), 0u) << "reorganizar deja el desperdicio en cero";
  EXPECT_DOUBLE_EQ(s.wasted_ratio(), 0.0);
  EXPECT_EQ(s.size(), 699u);
  EXPECT_EQ(s.overflow_pages(), 0u) << "el overflow quedo fusionado";
  EXPECT_LT(s.file_size(), bytes_llenos) << "el archivo devolvio el espacio";

  const auto todos = s.scan();
  ASSERT_EQ(todos.size(), 699u);
  EXPECT_TRUE(ordenado(todos));
  EXPECT_EQ(codigo_de(todos.front()), 302);
  EXPECT_EQ(codigo_de(todos.back()), 1000);
  for (std::int32_t i = 302; i <= 1000; i += 43) {
    ASSERT_EQ(s.search(Value{i}).size(), 1u) << i;
  }
  for (std::int32_t i = 1; i <= 301; i += 37) {
    EXPECT_TRUE(s.search(Value{i}).empty()) << i;
  }
}

TEST_F(SequentialFileTest, LaReorganizacionRegistraCuantoTardo) {
  SequentialFile s(path_, alumnos(), 512);
  for (std::int32_t i = 1; i <= 2000; ++i) s.insert(alumno(i));
  EXPECT_DOUBLE_EQ(s.last_reorganize_ms(), 0.0) << "todavia no se reorganizo";

  const double ms = s.reorganize();
  EXPECT_GT(ms, 0.0);
  EXPECT_DOUBLE_EQ(s.last_reorganize_ms(), ms);
  EXPECT_EQ(s.reorganizations(), 1u);
}

TEST_F(SequentialFileTest, ReorganizarFusionaElOverflowYDejaElArchivoSecuencial) {
  SequentialFile s(path_, alumnos(), 256);
  std::vector<std::int32_t> cs(400);
  std::iota(cs.begin(), cs.end(), 1);
  std::shuffle(cs.begin(), cs.end(), std::mt19937{5});
  for (const auto c : cs) s.insert(alumno(c));
  ASSERT_GT(s.overflow_pages(), 0u);
  const auto antes = s.scan();

  s.reorganize();
  EXPECT_EQ(s.overflow_pages(), 0u);
  EXPECT_EQ(s.scan(), antes) << "mismos registros, mismo orden";
  EXPECT_EQ(s.size(), 400u);

  // Y sigue aceptando inserciones normalmente.
  s.insert(alumno(401));
  EXPECT_EQ(s.size(), 401u);
  EXPECT_TRUE(ordenado(s.scan()));
}

TEST_F(SequentialFileTest, LasPaginasQuedanConHolguraDespuesDeReorganizar) {
  SequentialFile s(path_, alumnos(), 512);
  for (std::int32_t i = 1; i <= 1000; ++i) s.insert(alumno(i));
  s.reorganize();
  const auto por_pagina = s.slots_per_page();
  const auto esperadas =
      (1000u + static_cast<std::size_t>(por_pagina * 0.8) - 1) / static_cast<std::size_t>(por_pagina * 0.8);
  EXPECT_EQ(s.main_pages(), esperadas) << "se llenan al 80%, no al tope";

  // La holgura sirve: insertar en medio no manda nada al overflow.
  for (std::int32_t i = 1; i <= 20; ++i) s.insert(alumno(i * 10 + 1000000));
  EXPECT_EQ(s.overflow_pages(), 0u);
}

TEST_F(SequentialFileTest, ElUmbralEsConfigurable) {
  SequentialFile s(path_, alumnos(), 512, 0.50);
  EXPECT_DOUBLE_EQ(s.waste_threshold(), 0.50);
  for (std::int32_t i = 1; i <= 100; ++i) s.insert(alumno(i));
  for (std::int32_t i = 1; i <= 40; ++i) s.remove(Value{i});
  EXPECT_EQ(s.reorganizations(), 0u) << "0,40 no pasa un umbral de 0,50";
  for (std::int32_t i = 41; i <= 51; ++i) s.remove(Value{i});
  EXPECT_EQ(s.reorganizations(), 1u);
  EXPECT_EQ(s.size(), 49u);

  s.set_waste_threshold(0.10);
  EXPECT_DOUBLE_EQ(s.waste_threshold(), 0.10);
  for (std::int32_t i = 52; i <= 58; ++i) s.remove(Value{i});
  EXPECT_EQ(s.reorganizations(), 2u);
}

TEST_F(SequentialFileTest, ReorganizarUnArchivoVacioOTodoBorrado) {
  SequentialFile s(path_, alumnos(), 512);
  EXPECT_NO_THROW(s.reorganize());
  EXPECT_EQ(s.size(), 0u);
  EXPECT_EQ(s.main_pages(), 0u);
  EXPECT_TRUE(s.scan().empty());

  for (std::int32_t i = 1; i <= 50; ++i) s.insert(alumno(i));
  for (std::int32_t i = 1; i <= 50; ++i) s.remove(Value{i});
  EXPECT_EQ(s.size(), 0u);
  EXPECT_EQ(s.deleted_records(), 0u) << "se reorganizo por el camino";
  EXPECT_TRUE(s.scan().empty());
  EXPECT_EQ(s.file_size(), 512u) << "sin registros vivos no queda ninguna pagina de datos";

  // Y despues de eso el archivo sigue sirviendo.
  s.insert(alumno(7));
  EXPECT_EQ(s.size(), 1u);
  EXPECT_EQ(s.search(Value{7}).size(), 1u);
}

TEST_F(SequentialFileTest, LoReorganizadoSobreviveAlReabrir) {
  std::uint64_t marcados_al_cerrar = 0;
  {
    SequentialFile s(path_, alumnos(), 512);
    std::vector<std::int32_t> cs(600);
    std::iota(cs.begin(), cs.end(), 1);
    std::shuffle(cs.begin(), cs.end(), std::mt19937{13});
    for (const auto c : cs) s.insert(alumno(c));
    for (std::int32_t i = 1; i <= 250; ++i) s.remove(Value{i});
    ASSERT_GE(s.reorganizations(), 1u);
    // Los borrados posteriores a la ultima reorganizacion siguen marcados:
    // vuelven a acumularse hasta el proximo umbral.
    marcados_al_cerrar = s.deleted_records();
    EXPECT_LE(s.wasted_ratio(), s.waste_threshold());
    s.flush();
  }
  SequentialFile s(path_, alumnos(), 512);
  EXPECT_EQ(s.size(), 350u);
  EXPECT_EQ(s.deleted_records(), marcados_al_cerrar);
  const auto todos = s.scan();
  ASSERT_EQ(todos.size(), 350u);
  EXPECT_TRUE(ordenado(todos));
  EXPECT_EQ(codigo_de(todos.front()), 251);
}


// ---------------------------------------------------------------------------
// Busqueda binaria y por rango (issue #13)
// ---------------------------------------------------------------------------

TEST_F(SequentialFileTest, LaBusquedaBinariaCoincideConLaLineal) {
  // La prueba que pide el issue: mismo resultado que la busqueda lineal.
  SequentialFile s(path_, alumnos(), 256, 1.0);  // sin reorganizaciones de por medio
  std::vector<std::int32_t> cs(500);
  std::iota(cs.begin(), cs.end(), 1);
  std::shuffle(cs.begin(), cs.end(), std::mt19937{31});
  for (const auto c : cs) s.insert(alumno(c));
  for (std::int32_t i = 1; i <= 500; i += 11) s.remove(Value{i});
  ASSERT_GT(s.overflow_pages(), 0u) << "tiene que haber overflow que consultar";

  for (std::int32_t c = 0; c <= 520; ++c) {
    EXPECT_EQ(s.search(Value{c}), s.search_linear(Value{c})) << "clave " << c;
  }
}

TEST_F(SequentialFileTest, ElRangoBinarioCoincideConElLineal) {
  SequentialFile s(path_, alumnos(), 256, 1.0);
  std::vector<std::int32_t> cs(400);
  std::iota(cs.begin(), cs.end(), 1);
  std::shuffle(cs.begin(), cs.end(), std::mt19937{17});
  for (const auto c : cs) s.insert(alumno(c));
  for (std::int32_t i = 3; i <= 400; i += 7) s.remove(Value{i});
  ASSERT_GT(s.overflow_pages(), 0u);

  const std::vector<std::pair<std::int32_t, std::int32_t>> rangos{
      {1, 400},   {50, 60},  {1, 1},     {400, 400}, {0, 0},    {401, 500},
      {-10, 5},   {199, 201}, {100, 300}, {395, 405}, {60, 50},
  };
  for (const auto& [lo, hi] : rangos) {
    const auto binario = s.range_search(Value{lo}, Value{hi});
    EXPECT_EQ(binario, s.range_search_linear(Value{lo}, Value{hi}))
        << "rango [" << lo << ", " << hi << "]";
    EXPECT_TRUE(ordenado(binario)) << "rango [" << lo << ", " << hi << "]";
  }
}

TEST_F(SequentialFileTest, LaBusquedaLeePocasPaginas) {
  SequentialFile s(path_, alumnos(), 512);
  for (std::int32_t i = 1; i <= 2000; ++i) s.insert(alumno(i));
  ASSERT_GT(s.main_pages(), 20u);

  s.reset_stats();
  ASSERT_EQ(s.search(Value{1}).size(), 1u);
  const auto primera = s.stats().pages_read;
  s.reset_stats();
  ASSERT_EQ(s.search(Value{2000}).size(), 1u);
  const auto ultima = s.stats().pages_read;
  EXPECT_LE(primera, 2u) << "una pagina principal y a lo sumo una de overflow";
  EXPECT_LE(ultima, 2u) << "buscar el ultimo cuesta lo mismo que buscar el primero";

  s.reset_stats();
  static_cast<void>(s.search_linear(Value{2000}));
  EXPECT_GT(s.stats().pages_read, s.main_pages() - 1)
      << "la version lineal si recorre el archivo entero";
}

TEST_F(SequentialFileTest, EncuentraUnaClaveBorradaYVueltaAInsertar) {
  // Puede quedar el slot marcado y el vivo con la misma clave en la misma
  // pagina: la busqueda tiene que dar con el vivo.
  SequentialFile s(path_, alumnos(), 512, 1.0);
  for (std::int32_t i = 1; i <= 100; ++i) s.insert(alumno(i));
  ASSERT_EQ(s.remove(Value{42}), 1u);
  EXPECT_TRUE(s.search(Value{42}).empty());

  s.insert(alumno(42));
  const auto out = s.search(Value{42});
  ASSERT_EQ(out.size(), 1u) << "el registro reinsertado tiene que aparecer";
  EXPECT_EQ(out[0], alumno(42));
  EXPECT_EQ(s.search(Value{42}), s.search_linear(Value{42}));
  EXPECT_EQ(s.range_search(Value{41}, Value{43}).size(), 3u);
  EXPECT_EQ(s.remove(Value{42}), 1u) << "y remove tambien encuentra el vivo";
}

TEST_F(SequentialFileTest, RangoQueEmpiezaAntesDelPrimerRegistro) {
  SequentialFile s(path_, alumnos(), 256, 1.0);
  for (std::int32_t i = 100; i <= 400; ++i) s.insert(alumno(i));
  EXPECT_EQ(s.range_search(Value{1}, Value{99}).size(), 0u);
  EXPECT_EQ(s.range_search(Value{1}, Value{105}).size(), 6u);
  EXPECT_EQ(s.range_search(Value{1}, Value{9999}).size(), 301u);
  EXPECT_EQ(s.range_search(Value{1}, Value{9999}), s.range_search_linear(Value{1}, Value{9999}));
}

TEST_F(SequentialFileTest, BusquedaConClaveDeTexto) {
  const Schema cursos{
      .table_name = "cursos",
      .columns = {{"codigo", DataType::Varchar, 8}, {"creditos", DataType::Int}},
      .key_column = 0,
  };
  SequentialFile s(dir_ / "cursos.seq", cursos, 256, 1.0);
  for (const auto* c : {"CS2032", "BD2", "MA1101", "AI501", "ZZ999", "FI203", "QU100"}) {
    s.insert(Record{std::string{c}, 3});
  }
  EXPECT_EQ(s.search(Value{std::string{"MA1101"}}).size(), 1u);
  EXPECT_TRUE(s.search(Value{std::string{"NOEXISTE"}}).empty());
  EXPECT_EQ(s.search(Value{std::string{"BD2"}}), s.search_linear(Value{std::string{"BD2"}}));
  const auto rango = s.range_search(Value{std::string{"B"}}, Value{std::string{"N"}});
  EXPECT_EQ(rango, s.range_search_linear(Value{std::string{"B"}}, Value{std::string{"N"}}));
  EXPECT_EQ(rango.size(), 4u) << "BD2, CS2032, FI203, MA1101";
}


// ---------------------------------------------------------------------------
// Regresiones de la auditoria de 2.1.1 (#55)
// ---------------------------------------------------------------------------

TEST_F(SequentialFileTest, InsertarEnUnGrupoEnteramenteBorrado) {
  // C1: si todos los registros de un grupo estan marcados, no hay nada que
  // repartir y `split_group` indexaba un vector vacio. El umbral es global y
  // el vaciado es local, asi que la reorganizacion no salva del caso.
  SequentialFile s(path_, alumnos(), 512, 0.99);  // umbral alto: sin reorganizar
  const auto sp = static_cast<std::int32_t>(s.slots_per_page());
  for (std::int32_t i = 0; i < sp * 20; ++i) s.insert(alumno(i * 100));
  for (std::int32_t i = 0; i < sp; ++i) s.insert(alumno(i * 100 + 1));  // llena su overflow
  const auto grupos_antes = s.main_pages();

  for (std::int32_t i = 0; i < sp; ++i) {
    ASSERT_EQ(s.remove(Value{i * 100}), 1u);
    ASSERT_EQ(s.remove(Value{i * 100 + 1}), 1u);
  }
  ASSERT_LT(s.wasted_ratio(), s.waste_threshold()) << "no se reorganizo por el camino";

  // Insertar en el rango del grupo vacio: antes reventaba aqui.
  ASSERT_NO_THROW(s.insert(alumno(50)));
  EXPECT_EQ(s.main_pages(), grupos_antes - 1) << "el grupo vacio salio de la cadena";
  EXPECT_TRUE(ordenado(s.scan()));
  EXPECT_EQ(s.search(Value{50}).size(), 1u);

  // Y el archivo se puede reabrir, que es donde una pagina sin registros
  // haria fallar a build_directory.
  s.flush();
  SequentialFile r(path_, alumnos(), 512, 0.99);
  EXPECT_EQ(r.size(), s.size());
  EXPECT_TRUE(ordenado(r.scan()));
}

TEST_F(SequentialFileTest, BorrarElPrimerGrupoEnteroYSeguirUsandoElArchivo) {
  // El mismo caso pero en el grupo 0, donde hay que mover main_head_.
  SequentialFile s(path_, alumnos(), 512, 0.99);
  const auto sp = static_cast<std::int32_t>(s.slots_per_page());
  for (std::int32_t i = 0; i < sp * 3; ++i) s.insert(alumno(i * 100));
  for (std::int32_t i = 0; i < sp; ++i) s.insert(alumno(i * 100 + 1));
  for (std::int32_t i = 0; i < sp; ++i) {
    s.remove(Value{i * 100});
    s.remove(Value{i * 100 + 1});
  }
  ASSERT_NO_THROW(s.insert(alumno(50)));
  EXPECT_TRUE(ordenado(s.scan()));
  s.flush();
  SequentialFile r(path_, alumnos(), 512, 0.99);
  EXPECT_EQ(r.size(), s.size());
  EXPECT_TRUE(ordenado(r.scan()));
}

TEST_F(SequentialFileTest, UnaPaginaIncoherenteLanzaIoError) {
  // I6: physical_slots() restaba sin signo y devolvia SIZE_MAX, de donde
  // salia un std::out_of_range en vez de un error del contrato.
  {
    SequentialFile s(path_, alumnos(), 512);
    for (std::int32_t i = 1; i <= 20; ++i) s.insert(alumno(i));
    s.flush();
  }
  {  // se miente en el free_space de la pagina 1 (offset 6 de la cabecera)
    DiskManager dm(path_, 512);
    Page p(512);
    dm.read_page(1, p);
    // Tiene que pasar de slots_per_page * slot_size (17 * 29 = 493) por mas
    // de un slot entero, o la resta no llega a desbordar.
    p.set_free_space(600);
    dm.write_page(1, p);
    dm.flush();
  }
  // Sin la comprobacion, physical_slots() devuelve SIZE_MAX y el error sale
  // como std::out_of_range al leer fuera del body, rompiendo la promesa de
  // error.hpp de que todo hereda de quipudb::Error.
  try {
    SequentialFile s(path_, alumnos(), 512);
    FAIL() << "tendria que haber lanzado";
  } catch (const Error& e) {
    EXPECT_NE(std::string(e.what()).find("incoherente"), std::string::npos) << e.what();
  } catch (const std::exception& e) {
    FAIL() << "lanzo fuera de la jerarquia de quipudb: " << e.what();
  }
}

}  // namespace
}  // namespace quipudb
