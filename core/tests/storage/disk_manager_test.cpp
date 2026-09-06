// Pruebas de DiskManager (issue #6).
//
// Cada prueba trabaja sobre un archivo propio en el directorio temporal del
// sistema, que se borra al terminar. La prueba central es la que pide el
// issue: escribir N paginas, releerlas y comparar byte a byte.

#include <gtest/gtest.h>

#include <array>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <memory>
#include <random>
#include <string>

#include "quipudb/error.hpp"
#include "quipudb/storage/disk_manager.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

class DiskManagerTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    path_ = fs::temp_directory_path() / ("quipudb_" + std::string(info->name()) + ".dat");
    fs::remove(path_);
  }
  void TearDown() override { fs::remove(path_); }

  /// Pagina llena de bytes pseudoaleatorios reproducibles a partir de `seed`.
  static Page random_page(std::size_t page_size, std::uint32_t seed) {
    Page p(page_size);
    std::mt19937 rng(seed);
    for (std::size_t i = 0; i < p.body_size(); ++i) {
      p.body()[i] = static_cast<std::byte>(rng() & 0xFF);
    }
    p.set_next(seed + 1);
    p.set_record_count(static_cast<std::uint16_t>(seed % 100));
    p.set_free_space(static_cast<std::uint16_t>(seed % 1000));
    return p;
  }

  fs::path path_;
};

TEST_F(DiskManagerTest, CreaElArchivoConSoloLaPaginaCero) {
  DiskManager dm(path_, 512);
  EXPECT_TRUE(fs::exists(path_));
  EXPECT_EQ(dm.page_size(), 512u);
  EXPECT_EQ(dm.page_count(), 0u);
  EXPECT_EQ(dm.file_size(), 512u);
  EXPECT_EQ(dm.reads(), 0u);
  EXPECT_EQ(dm.writes(), 0u);
}

TEST_F(DiskManagerTest, EscribeNPaginasYLasReleeByteABytes) {
  constexpr std::size_t kPage = 1024;
  constexpr std::uint32_t kN = 200;
  DiskManager dm(path_, kPage);

  for (std::uint32_t i = 1; i <= kN; ++i) {
    const PageId id = dm.allocate_page();
    ASSERT_EQ(id, i);
    dm.write_page(id, random_page(kPage, i));
  }
  EXPECT_EQ(dm.page_count(), kN);
  EXPECT_EQ(dm.writes(), kN);
  EXPECT_EQ(dm.file_size(), kPage * (kN + 1));

  // En orden inverso, para que no dependa de la posicion del cursor.
  Page leida(kPage);
  for (std::uint32_t i = kN; i >= 1; --i) {
    dm.read_page(i, leida);
    EXPECT_EQ(leida, random_page(kPage, i)) << "pagina " << i;
  }
  EXPECT_EQ(dm.reads(), kN);
}

TEST_F(DiskManagerTest, LasPaginasSobrevivenAlCerrarYReabrir) {
  const Page original = random_page(4096, 7);
  {
    DiskManager dm(path_, 4096);
    dm.allocate_page();
    dm.allocate_page();
    dm.write_page(2, original);
  }  // el destructor persiste la cabecera
  DiskManager dm(path_, 4096);
  EXPECT_EQ(dm.page_count(), 2u);
  Page leida(4096);
  dm.read_page(2, leida);
  EXPECT_EQ(leida, original);
  dm.read_page(1, leida);
  EXPECT_EQ(leida, Page(4096)) << "una pagina reservada y nunca escrita esta en blanco";
}

TEST_F(DiskManagerTest, ElTamanoDePaginaQuedaGrabadoYSeValidaAlReabrir) {
  { DiskManager dm(path_, 2048); }
  EXPECT_THROW(DiskManager(path_, 4096), IoError);
  EXPECT_NO_THROW(DiskManager(path_, 2048));
}

TEST_F(DiskManagerTest, FuncionaConVariosTamanosDePagina) {
  for (const std::size_t sz : {128u, 512u, 4096u, 16384u, 65536u}) {
    const fs::path p = path_.string() + "." + std::to_string(sz);
    fs::remove(p);
    {
      DiskManager dm(p, sz);
      const Page escrita = random_page(sz, static_cast<std::uint32_t>(sz));
      dm.write_page(dm.allocate_page(), escrita);
      Page leida(sz);
      dm.read_page(1, leida);
      EXPECT_EQ(leida, escrita) << "tamano " << sz;
      EXPECT_EQ(dm.file_size(), sz * 2);
    }
    fs::remove(p);
  }
}

TEST_F(DiskManagerTest, RechazaIdsFueraDeRangoYPaginasDeOtroTamano) {
  DiskManager dm(path_, 512);
  Page p(512);
  EXPECT_THROW(dm.read_page(0, p), IoError);  // la 0 es la cabecera, no es de datos
  EXPECT_THROW(dm.read_page(1, p), IoError);  // todavia no existe
  dm.allocate_page();
  EXPECT_NO_THROW(dm.read_page(1, p));
  EXPECT_THROW(dm.read_page(2, p), IoError);
  Page otra(1024);
  EXPECT_THROW(dm.write_page(1, otra), IoError);
  EXPECT_THROW(dm.read_page(1, otra), IoError);
  EXPECT_THROW(DiskManager(path_.string() + ".x", 100), IoError);  // menor que kMinSize
}

TEST_F(DiskManagerTest, AreaMetaIdaVueltaYPersistente) {
  const std::array<std::byte, 8> meta{std::byte{1}, std::byte{2}, std::byte{3}, std::byte{4},
                                      std::byte{5}, std::byte{6}, std::byte{7}, std::byte{8}};
  {
    DiskManager dm(path_, 512);
    dm.write_meta(meta);
    dm.allocate_page();  // reescribir la cabecera no debe pisar el area meta
  }
  DiskManager dm(path_, 512);
  std::array<std::byte, 8> leida{};
  dm.read_meta(leida);
  EXPECT_EQ(leida, meta);

  std::array<std::byte, DiskManager::kMetaSize + 1> demasiado{};
  EXPECT_THROW(dm.write_meta(demasiado), IoError);
  EXPECT_THROW(dm.read_meta(demasiado), IoError);
}

TEST_F(DiskManagerTest, RechazaArchivosAjenosOTruncados) {
  {
    std::ofstream f(path_, std::ios::binary);
    f << "esto no es un archivo de paginas";
  }
  EXPECT_THROW(DiskManager(path_, 512), IoError);

  fs::remove(path_);
  {
    DiskManager dm(path_, 512);
    dm.allocate_page();
    dm.allocate_page();
  }
  fs::resize_file(path_, 512 * 2);  // la cabecera promete 2 paginas y solo queda 1
  EXPECT_THROW(DiskManager(path_, 512), IoError);
}

TEST_F(DiskManagerTest, LosContadoresSoloCuentanPaginasDeDatos) {
  DiskManager dm(path_, 512);
  dm.allocate_page();
  dm.allocate_page();
  EXPECT_EQ(dm.writes(), 0u) << "reservar no cuenta como escritura del dueno";
  Page p(512);
  dm.write_page(1, p);
  dm.read_page(1, p);
  dm.read_page(2, p);
  EXPECT_EQ(dm.writes(), 1u);
  EXPECT_EQ(dm.reads(), 2u);
  dm.reset_counters();
  EXPECT_EQ(dm.reads() + dm.writes(), 0u);
}

}  // namespace
}  // namespace quipudb
