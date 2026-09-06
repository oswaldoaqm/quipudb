// Pruebas de HeapFile (issue #8).

#include <gtest/gtest.h>

#include <algorithm>
#include <array>
#include <cstring>
#include <filesystem>
#include <set>
#include <string>

#include "quipudb/error.hpp"
#include "quipudb/storage/disk_manager.hpp"
#include "quipudb/storage/heap_file.hpp"

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

class HeapFileTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_heap_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
    path_ = dir_ / "alumnos.heap";
  }
  void TearDown() override { fs::remove_all(dir_); }

  fs::path dir_;
  fs::path path_;
};

TEST_F(HeapFileTest, EmpiezaVacio) {
  HeapFile h(path_, alumnos(), 512);
  EXPECT_EQ(h.size(), 0u);
  EXPECT_EQ(h.kind(), kind::kHeap);
  EXPECT_EQ(h.page_count(), 0u);
  EXPECT_TRUE(h.scan().empty());
  EXPECT_TRUE(h.search(Value{1}).empty());
  EXPECT_FALSE(h.read(RID{1, 0}).has_value());
  // 512 - 8 de cabecera = 504 bytes de body; slot = 1 + 4 + 16 + 8 = 29.
  EXPECT_EQ(h.slot_size(), 29u);
  EXPECT_EQ(h.slots_per_page(), 504u / 29u);
}

TEST_F(HeapFileTest, InsertDevuelveRidEstableYReadLoResuelve) {
  HeapFile h(path_, alumnos(), 512);
  const RID a = h.insert(alumno(10));
  const RID b = h.insert(alumno(20));
  EXPECT_EQ(a.page, 1u);
  EXPECT_EQ(a.slot, 0);
  EXPECT_EQ(b.page, 1u);
  EXPECT_EQ(b.slot, 1);
  EXPECT_EQ(h.size(), 2u);

  const auto r = h.read(a);
  ASSERT_TRUE(r.has_value());
  EXPECT_EQ(*r, alumno(10));
  EXPECT_EQ(*h.read(b), alumno(20));
  EXPECT_FALSE(h.read(RID{9, 0}).has_value());
  EXPECT_FALSE(h.read(RID{1, 99}).has_value());
}

TEST_F(HeapFileTest, MilRegistrosSeRecuperanTodos) {
  HeapFile h(path_, alumnos(), 512);
  for (std::int32_t i = 1; i <= 1000; ++i) h.insert(alumno(i));
  EXPECT_EQ(h.size(), 1000u);

  const auto todos = h.scan();
  ASSERT_EQ(todos.size(), 1000u);
  std::set<std::int32_t> vistos;
  for (const auto& r : todos) vistos.insert(codigo_de(r));
  EXPECT_EQ(vistos.size(), 1000u);
  EXPECT_EQ(*vistos.begin(), 1);
  EXPECT_EQ(*vistos.rbegin(), 1000);
  // Orden de llegada.
  EXPECT_EQ(codigo_de(todos.front()), 1);
  EXPECT_EQ(codigo_de(todos.back()), 1000);

  // Y cada uno se encuentra por su clave.
  for (std::int32_t i = 1; i <= 1000; i += 97) {
    const auto encontrado = h.search(Value{i});
    ASSERT_EQ(encontrado.size(), 1u) << i;
    EXPECT_EQ(encontrado[0], alumno(i));
  }
}

TEST_F(HeapFileTest, LlenaVariasPaginasSinDesperdiciarSlots) {
  HeapFile h(path_, alumnos(), 512);
  const auto por_pagina = h.slots_per_page();
  for (std::size_t i = 0; i < por_pagina * 3; ++i) {
    h.insert(alumno(static_cast<std::int32_t>(i)));
  }
  EXPECT_EQ(h.page_count(), 3u) << "cada pagina se llena antes de abrir la siguiente";
  EXPECT_EQ(h.size(), por_pagina * 3);
  h.insert(alumno(999999));
  EXPECT_EQ(h.page_count(), 4u);
}

TEST_F(HeapFileTest, ClaveDuplicadaLanzaYNoDejaRastro) {
  HeapFile h(path_, alumnos(), 512);
  h.insert(alumno(5));
  EXPECT_THROW(h.insert(alumno(5)), DuplicateKey);
  EXPECT_EQ(h.size(), 1u);
  EXPECT_EQ(h.scan().size(), 1u);
}

TEST_F(HeapFileTest, RegistroInvalidoLanza) {
  HeapFile h(path_, alumnos(), 512);
  EXPECT_THROW(h.insert(Record{1, std::string{"x"}}), InvalidRecord);
  EXPECT_THROW(h.insert(Record{1, std::string(17, 'x'), 0.0}), InvalidRecord);
  EXPECT_EQ(h.size(), 0u);
}

TEST_F(HeapFileTest, BusquedasQueNoEncuentranDevuelvenVacio) {
  HeapFile h(path_, alumnos(), 512);
  for (std::int32_t i = 10; i <= 50; i += 10) h.insert(alumno(i));
  EXPECT_TRUE(h.search(Value{999}).empty());
  EXPECT_TRUE(h.range_search(Value{100}, Value{200}).empty());
}

TEST_F(HeapFileTest, RangeSearchEsInclusivoYEnOrdenDeLlegada) {
  HeapFile h(path_, alumnos(), 512);
  for (const std::int32_t c : {50, 10, 40, 20, 30}) h.insert(alumno(c));
  const auto out = h.range_search(Value{20}, Value{40});
  ASSERT_EQ(out.size(), 3u);
  EXPECT_EQ(codigo_de(out[0]), 40) << "el heap no ordena: sale en el orden en que se inserto";
  EXPECT_EQ(codigo_de(out[1]), 20);
  EXPECT_EQ(codigo_de(out[2]), 30);
}

TEST_F(HeapFileTest, RemoveQuitaElRegistroYLoDejaInvisible) {
  HeapFile h(path_, alumnos(), 512);
  const RID a = h.insert(alumno(1));
  h.insert(alumno(2));
  EXPECT_EQ(h.remove(Value{1}), 1u);
  EXPECT_EQ(h.remove(Value{1}), 0u) << "borrar dos veces no borra dos veces";
  EXPECT_EQ(h.size(), 1u);
  EXPECT_FALSE(h.read(a).has_value());
  EXPECT_TRUE(h.search(Value{1}).empty());
  const auto todos = h.scan();
  ASSERT_EQ(todos.size(), 1u);
  EXPECT_EQ(codigo_de(todos[0]), 2);
  // La clave liberada se puede volver a insertar.
  EXPECT_NO_THROW(h.insert(alumno(1)));
  EXPECT_EQ(h.size(), 2u);
}

TEST_F(HeapFileTest, TodoSobreviveAlCerrarYReabrir) {
  {
    HeapFile h(path_, alumnos(), 512);
    for (std::int32_t i = 1; i <= 200; ++i) h.insert(alumno(i));
    h.remove(Value{7});
    h.remove(Value{100});
    h.flush();
  }
  HeapFile h(path_, alumnos(), 512);
  EXPECT_EQ(h.size(), 198u);
  EXPECT_EQ(h.scan().size(), 198u);
  EXPECT_TRUE(h.search(Value{7}).empty());
  EXPECT_EQ(h.search(Value{8}).size(), 1u);
  EXPECT_THROW(h.insert(alumno(8)), DuplicateKey) << "las claves vivas se reconstruyeron";
  EXPECT_NO_THROW(h.insert(alumno(7))) << "la clave borrada quedo libre";
}

TEST_F(HeapFileTest, RechazaAbrirConOtroEsquema) {
  {
    HeapFile h(path_, alumnos(), 512);
    h.insert(alumno(1));
  }
  Schema otro = alumnos();
  otro.columns[1].length = 32;  // cambia el tamano del registro
  EXPECT_THROW(HeapFile(path_, otro, 512), SchemaError);
  EXPECT_THROW(HeapFile(path_, alumnos(), 1024), IoError) << "otro tamano de pagina";
}

TEST_F(HeapFileTest, RechazaUnRegistroQueNoEntraEnUnaPagina) {
  Schema gordo = alumnos();
  gordo.columns[1].length = 600;
  EXPECT_THROW(HeapFile(dir_ / "gordo.heap", gordo, 512), SchemaError);
}

TEST_F(HeapFileTest, LasEstadisticasCuentanPaginasYRegistros) {
  HeapFile h(path_, alumnos(), 512);
  const auto por_pagina = h.slots_per_page();
  for (std::size_t i = 0; i < por_pagina * 2; ++i) {
    h.insert(alumno(static_cast<std::int32_t>(i)));
  }
  h.reset_stats();

  // Buscar el ultimo obliga a recorrer las dos paginas.
  const auto out = h.search(Value{static_cast<std::int32_t>(por_pagina * 2 - 1)});
  ASSERT_EQ(out.size(), 1u);
  EXPECT_EQ(h.stats().pages_read, 2u);
  EXPECT_EQ(h.stats().records_examined, por_pagina * 2);
  EXPECT_EQ(h.stats().records_returned, 1u);

  h.reset_stats();
  static_cast<void>(h.scan());
  EXPECT_EQ(h.stats().pages_read, 2u);
  EXPECT_EQ(h.stats().records_returned, por_pagina * 2);

  h.reset_stats();
  h.insert(alumno(123456));
  EXPECT_EQ(h.stats().pages_written, 1u);
}

TEST_F(HeapFileTest, FuncionaConVariosTamanosDePagina) {
  for (const std::size_t sz : {256u, 1024u, 4096u}) {
    const fs::path p = dir_ / ("t" + std::to_string(sz) + ".heap");
    HeapFile h(p, alumnos(), sz);
    for (std::int32_t i = 1; i <= 300; ++i) h.insert(alumno(i));
    EXPECT_EQ(h.size(), 300u) << sz;
    EXPECT_EQ(h.scan().size(), 300u) << sz;
    EXPECT_EQ(h.search(Value{250})[0], alumno(250)) << sz;
    // Mas grande la pagina, menos paginas para los mismos registros.
    EXPECT_EQ(h.page_count(), (300u + h.slots_per_page() - 1) / h.slots_per_page()) << sz;
  }
}

TEST_F(HeapFileTest, ClaveDeTextoTambienFunciona) {
  const Schema cursos{
      .table_name = "cursos",
      .columns = {{"codigo", DataType::Varchar, 8}, {"creditos", DataType::Int}},
      .key_column = 0,
  };
  HeapFile h(dir_ / "cursos.heap", cursos, 512);
  h.insert(Record{std::string{"CS2032"}, 4});
  h.insert(Record{std::string{"BD2"}, 3});
  EXPECT_THROW(h.insert(Record{std::string{"BD2"}, 5}), DuplicateKey);
  const auto out = h.search(Value{std::string{"BD2"}});
  ASSERT_EQ(out.size(), 1u);
  EXPECT_EQ(std::get<std::int32_t>(out[0][1]), 3);
  // El orden de VARCHAR es lexicografico, no por prefijo: "C" < "CS2032", asi
  // que el rango ["A", "C"] deja fuera a CS2032 y solo trae BD2.
  EXPECT_EQ(h.range_search(Value{std::string{"A"}}, Value{std::string{"C"}}).size(), 1u);
  EXPECT_EQ(h.range_search(Value{std::string{"A"}}, Value{std::string{"D"}}).size(), 2u);
}


// ---------------------------------------------------------------------------
// Free list y reutilizacion de espacio (issue #9)
// ---------------------------------------------------------------------------

TEST_F(HeapFileTest, MilInsertaTrescientosBorraTrescientosInsertaYNoCrece) {
  // La prueba que pide el issue: el archivo no debe crecer al reponer.
  HeapFile h(path_, alumnos(), 512);
  for (std::int32_t i = 1; i <= 1000; ++i) h.insert(alumno(i));
  const auto paginas_llenas = h.page_count();
  const auto bytes_llenos = h.file_size();
  EXPECT_EQ(h.free_pages(), 1u) << "solo la ultima pagina queda a medias";

  for (std::int32_t i = 1; i <= 300; ++i) h.remove(Value{i});
  EXPECT_EQ(h.size(), 700u);
  EXPECT_GT(h.free_pages(), 1u) << "las paginas liberadas entraron a la free list";

  for (std::int32_t i = 1001; i <= 1300; ++i) h.insert(alumno(i));
  EXPECT_EQ(h.size(), 1000u);
  EXPECT_EQ(h.page_count(), paginas_llenas) << "reutilizo los huecos en vez de crecer";
  EXPECT_EQ(h.file_size(), bytes_llenos);

  // Y el contenido es el correcto: se fueron los 300 viejos, estan los 300 nuevos.
  const auto todos = h.scan();
  ASSERT_EQ(todos.size(), 1000u);
  std::set<std::int32_t> vistos;
  for (const auto& r : todos) vistos.insert(codigo_de(r));
  EXPECT_EQ(vistos.count(1), 0u);
  EXPECT_EQ(vistos.count(300), 0u);
  EXPECT_EQ(vistos.count(301), 1u);
  EXPECT_EQ(vistos.count(1300), 1u);
}

TEST_F(HeapFileTest, ReutilizaElHuecoAntesDeCrecerElArchivo) {
  HeapFile h(path_, alumnos(), 512);
  const auto por_pagina = h.slots_per_page();
  for (std::size_t i = 0; i < por_pagina; ++i) {
    h.insert(alumno(static_cast<std::int32_t>(i)));
  }
  ASSERT_EQ(h.page_count(), 1u);
  EXPECT_EQ(h.free_pages(), 0u) << "la unica pagina esta llena: la lista queda vacia";

  // Se libera un slot del medio, no el ultimo.
  const std::int32_t victima = static_cast<std::int32_t>(por_pagina / 2);
  const RID hueco{1, static_cast<SlotId>(victima)};
  ASSERT_EQ(codigo_de(*h.read(hueco)), victima);
  ASSERT_EQ(h.remove(Value{victima}), 1u);
  EXPECT_EQ(h.free_pages(), 1u) << "la pagina llena volvio a la lista";

  const RID reusado = h.insert(alumno(777));
  EXPECT_EQ(reusado, hueco) << "el registro nuevo ocupo exactamente el hueco liberado";
  EXPECT_EQ(h.page_count(), 1u) << "no se agrego una pagina";
  EXPECT_EQ(h.free_pages(), 0u);
}

TEST_F(HeapFileTest, LosRidsDeLosDemasNoSeMuevenAlBorrar) {
  // FREE_LIST y no MOVE_THE_LAST: nadie cambia de sitio, porque los indices
  // no agrupados (#16) van a guardar estos RIDs.
  HeapFile h(path_, alumnos(), 512);
  std::vector<RID> antes;
  for (std::int32_t i = 1; i <= 50; ++i) antes.push_back(h.insert(alumno(i)));

  h.remove(Value{1});
  h.remove(Value{25});
  for (std::int32_t i = 2; i <= 50; ++i) {
    if (i == 25) continue;
    const auto r = h.read(antes[static_cast<std::size_t>(i - 1)]);
    ASSERT_TRUE(r.has_value()) << i;
    EXPECT_EQ(codigo_de(*r), i) << "el registro " << i << " se movio de sitio";
  }
}

TEST_F(HeapFileTest, LaFreeListPersisteYNoSeReconstruye) {
  std::uint32_t libres_antes = 0;
  {
    HeapFile h(path_, alumnos(), 512);
    for (std::int32_t i = 1; i <= 500; ++i) h.insert(alumno(i));
    for (std::int32_t i = 1; i <= 200; ++i) h.remove(Value{i});
    libres_antes = h.free_pages();
    EXPECT_GT(libres_antes, 1u);
    h.flush();
  }
  HeapFile h(path_, alumnos(), 512);
  EXPECT_EQ(h.free_pages(), libres_antes) << "la lista se leyo del disco tal cual";
  EXPECT_EQ(h.size(), 300u);

  // Y sigue sirviendo: repone sin crecer.
  const auto paginas = h.page_count();
  for (std::int32_t i = 501; i <= 700; ++i) h.insert(alumno(i));
  EXPECT_EQ(h.page_count(), paginas);
  EXPECT_EQ(h.size(), 500u);
}

TEST_F(HeapFileTest, BorrarTodoDejaElArchivoReutilizableEntero) {
  HeapFile h(path_, alumnos(), 512);
  for (std::int32_t i = 1; i <= 200; ++i) h.insert(alumno(i));
  const auto paginas = h.page_count();
  for (std::int32_t i = 1; i <= 200; ++i) h.remove(Value{i});
  EXPECT_EQ(h.size(), 0u);
  EXPECT_TRUE(h.scan().empty());
  EXPECT_EQ(h.free_pages(), paginas) << "todas las paginas tienen huecos";

  for (std::int32_t i = 1; i <= 200; ++i) h.insert(alumno(i));
  EXPECT_EQ(h.size(), 200u);
  EXPECT_EQ(h.page_count(), paginas) << "no crecio: reutilizo todo";
}

TEST_F(HeapFileTest, RechazaUnArchivoDeLaVersionAnterior) {
  {
    HeapFile h(path_, alumnos(), 512);
    h.insert(alumno(1));
  }
  {  // Se pisa el numero de version del area meta con el formato viejo (#8).
    DiskManager dm(path_, 512);
    std::array<std::byte, 4> v1{std::byte{1}, std::byte{0}, std::byte{0}, std::byte{0}};
    dm.write_meta(v1);
    dm.flush();
  }
  EXPECT_THROW(HeapFile(path_, alumnos(), 512), IoError);
}

TEST_F(HeapFileTest, DetectaUnaFreeListInconsistente) {
  {
    HeapFile h(path_, alumnos(), 512);
    for (std::int32_t i = 1; i <= 100; ++i) h.insert(alumno(i));
    h.remove(Value{1});
    h.flush();
  }
  // Se miente en el area meta: la cabeza apunta a una pagina que no existe.
  {
    DiskManager dm(path_, 512);
    // Meta = version(4) + record_size(4) + live(8) + free_head(4): la cabeza
    // de la lista empieza en el byte 16.
    constexpr std::size_t kOffsetFreeHead = 16;
    std::array<std::byte, kOffsetFreeHead + sizeof(std::uint32_t)> meta{};
    dm.read_meta(meta);
    const std::uint32_t pagina_inexistente = 9999;
    std::memcpy(meta.data() + kOffsetFreeHead, &pagina_inexistente, sizeof pagina_inexistente);
    dm.write_meta(meta);
    dm.flush();
  }
  EXPECT_THROW(HeapFile(path_, alumnos(), 512), IoError);
}


// --- cursor y update (#56) ---

TEST_F(HeapFileTest, UpdateReescribeElMismoSlot) {
  HeapFile h(path_, alumnos(), 512);
  const RID rid = h.insert(alumno(10));
  h.insert(alumno(20));

  Record nuevo = alumno(10);
  nuevo[1] = std::string("cambiado");
  nuevo[2] = 99.5;
  EXPECT_EQ(h.update(Value{10}, nuevo), 1u);
  EXPECT_EQ(h.size(), 2u);
  const auto leido = h.read(rid);
  ASSERT_TRUE(leido.has_value()) << "el RID se conserva: los indices que lo apuntan siguen bien";
  EXPECT_EQ(*leido, nuevo);
  EXPECT_EQ(h.search(Value{10})[0], nuevo);

  EXPECT_EQ(h.update(Value{404}, alumno(404)), 0u);
  EXPECT_THROW(h.update(Value{10}, alumno(11)), SchemaError);
  EXPECT_EQ(h.page_count(), 1u) << "no crecio el archivo";
}

TEST_F(HeapFileTest, ElCursorDevuelveLoMismoQueScanYNoSeSaltaHuecos) {
  HeapFile h(path_, alumnos(), 512);
  for (std::int32_t i = 1; i <= 500; ++i) h.insert(alumno(i));
  for (std::int32_t i = 1; i <= 500; i += 3) h.remove(Value{i});

  std::vector<Record> del_cursor;
  auto c = h.cursor();
  Record r;
  while (c->next(r)) del_cursor.push_back(r);
  EXPECT_EQ(del_cursor, h.scan());
  EXPECT_EQ(del_cursor.size(), h.size());
  EXPECT_FALSE(c->next(r));
}

TEST_F(HeapFileTest, ElCursorLeeCadaPaginaUnaSolaVez) {
  HeapFile h(path_, alumnos(), 512);
  for (std::int32_t i = 1; i <= 300; ++i) h.insert(alumno(i));
  h.reset_stats();
  auto c = h.cursor();
  Record r;
  std::size_t n = 0;
  while (c->next(r)) ++n;
  EXPECT_EQ(n, 300u);
  EXPECT_EQ(h.stats().pages_read, h.page_count())
      << "una lectura por pagina, no una por registro";
}

TEST_F(HeapFileTest, ElCursorDeUnaTablaVaciaNoDevuelveNada) {
  HeapFile h(path_, alumnos(), 512);
  Record r;
  EXPECT_FALSE(h.cursor()->next(r));
}

}  // namespace
}  // namespace quipudb
