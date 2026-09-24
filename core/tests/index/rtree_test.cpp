// Pruebas del R-Tree: nodos, MBR y persistencia (issue #115).

#include <gtest/gtest.h>

#include <cstring>
#include <filesystem>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

#include "quipudb/error.hpp"
#include "quipudb/index/rtree.hpp"
#include "quipudb/storage/disk_manager.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

class RTreeTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_rtree_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
    path_ = dir_ / "indice.rtree";
  }
  void TearDown() override { fs::remove_all(dir_); }

  fs::path dir_;
  fs::path path_;
};

// ---------------------------------------------------------------------------
// Geometria
// ---------------------------------------------------------------------------

TEST(RectTest, ContieneUnPuntoIncluidosLosBordes) {
  const Rect r{0, 0, 10, 5};
  EXPECT_TRUE(r.contains(Point{5, 2}));
  EXPECT_TRUE(r.contains(Point{0, 0}));
  EXPECT_TRUE(r.contains(Point{10, 5}));
  EXPECT_FALSE(r.contains(Point{10.0001, 2}));
  EXPECT_FALSE(r.contains(Point{5, -0.0001}));
}

TEST(RectTest, ContieneOtroRectangulo) {
  const Rect r{0, 0, 10, 10};
  EXPECT_TRUE(r.contains(Rect{2, 2, 8, 8}));
  EXPECT_TRUE(r.contains(r));
  EXPECT_FALSE(r.contains(Rect{2, 2, 11, 8}));
}

TEST(RectTest, SeSolapaTambienSiSoloSeTocanEnUnBorde) {
  const Rect r{0, 0, 10, 10};
  EXPECT_TRUE(r.intersects(Rect{5, 5, 15, 15}));
  EXPECT_TRUE(r.intersects(Rect{10, 0, 20, 10}));  // borde comun
  EXPECT_TRUE(r.intersects(Rect{10, 10, 20, 20}));  // esquina comun
  EXPECT_TRUE(r.intersects(Rect{2, 2, 3, 3}));     // uno dentro del otro
  EXPECT_FALSE(r.intersects(Rect{10.5, 0, 20, 10}));
  EXPECT_FALSE(r.intersects(Rect{0, -5, 10, -0.5}));
}

TEST(RectTest, AreaUnionYAmpliacion) {
  const Rect a{0, 0, 4, 3};
  EXPECT_DOUBLE_EQ(a.area(), 12.0);
  EXPECT_DOUBLE_EQ(Rect::of(Point{7, 7}).area(), 0.0);

  const Rect b{2, 1, 6, 5};
  EXPECT_EQ(a.united(b), (Rect{0, 0, 6, 5}));
  EXPECT_DOUBLE_EQ(a.enlargement(b), 30.0 - 12.0);
  // Incluir algo que ya esta dentro no amplia nada.
  EXPECT_DOUBLE_EQ(a.enlargement(Rect{1, 1, 2, 2}), 0.0);
  EXPECT_DOUBLE_EQ(a.enlargement(Rect::of(Point{1, 1})), 0.0);
}

TEST(RectTest, FuncionaConCoordenadasGeograficasNegativas) {
  // Lima: longitud y latitud negativas.
  const Rect lima{-77.2, -12.3, -76.8, -11.8};
  EXPECT_TRUE(lima.contains(Point{-77.0428, -12.0464}));
  EXPECT_FALSE(lima.contains(Point{-71.53, -16.40}));  // Arequipa
}

TEST(RTreeNodeTest, ElMbrCubreTodasLasEntradas) {
  RTreeNode hoja;
  hoja.points = {{Point{1, 5}, RID{1, 0}}, {Point{-2, 3}, RID{1, 1}}, {Point{4, -1}, RID{2, 0}}};
  EXPECT_EQ(hoja.mbr(), (Rect{-2, -1, 4, 5}));

  RTreeNode interno;
  interno.leaf = false;
  interno.children = {{Rect{0, 0, 1, 1}, 3}, {Rect{5, -2, 6, 0}, 4}};
  EXPECT_EQ(interno.mbr(), (Rect{0, -2, 6, 1}));
}

TEST(RTreeNodeTest, UnNodoVacioNoTieneMbr) {
  EXPECT_THROW((void)RTreeNode{}.mbr(), std::logic_error);
}

// ---------------------------------------------------------------------------
// Orden
// ---------------------------------------------------------------------------

TEST_F(RTreeTest, OrdenCeroUsaElMaximoQueCabeEnLaPagina) {
  RTree arbol(path_);
  // Pagina de 4096: body de 4088; el interno manda con (4088 - 1) / 36.
  EXPECT_EQ(RTree::max_order(4096), 113u);
  EXPECT_EQ(arbol.order(), 113u);
  EXPECT_EQ(arbol.min_fill(), 45u);
}

TEST_F(RTreeTest, LaOcupacionMinimaNuncaPasaDeLaMitad) {
  for (std::size_t orden = RTree::kMinOrder; orden <= RTree::max_order(4096); ++orden) {
    fs::remove(path_);
    RTree arbol(path_, kDefaultPageSize, orden);
    EXPECT_GE(arbol.min_fill(), 2u) << "orden " << orden;
    EXPECT_LE(arbol.min_fill() * 2, orden) << "orden " << orden;
  }
}

TEST_F(RTreeTest, RechazaUnOrdenDemasiadoChicoODemasiadoGrande) {
  EXPECT_THROW(RTree(path_, kDefaultPageSize, 3), SchemaError);
  fs::remove(path_);
  EXPECT_THROW(RTree(path_, kDefaultPageSize, 114), SchemaError);
}

// ---------------------------------------------------------------------------
// Serializacion de nodos
// ---------------------------------------------------------------------------

TEST(RTreeCodecTest, UnaHojaIdaYVueltaDevuelveLoMismo) {
  RTreeNode hoja;
  hoja.points = {
      {Point{-77.0428, -12.0464}, RID{1, 0}},
      {Point{0.0, 0.0}, RID{2, 7}},
      {Point{std::numeric_limits<double>::max(), -1e-300}, RID{4000000000u, 65535}},
  };
  Page pagina(kDefaultPageSize);
  RTree::encode(hoja, pagina);
  EXPECT_EQ(RTree::decode(pagina), hoja);
}

TEST(RTreeCodecTest, UnNodoInternoIdaYVueltaDevuelveLoMismo) {
  RTreeNode interno;
  interno.leaf = false;
  interno.children = {{Rect{-77.2, -12.3, -76.8, -11.8}, 5}, {Rect{0, 0, 1, 1}, 9}};
  Page pagina(kDefaultPageSize);
  RTree::encode(interno, pagina);
  EXPECT_EQ(RTree::decode(pagina), interno);
}

TEST(RTreeCodecTest, UnNodoLlenoCabeEnSuPagina) {
  const std::size_t maximo = RTree::max_order(kDefaultPageSize);
  RTreeNode hoja;
  RTreeNode interno;
  interno.leaf = false;
  for (std::size_t i = 0; i < maximo; ++i) {
    const double v = static_cast<double>(i);
    hoja.points.push_back({Point{v, -v}, RID{static_cast<PageId>(i + 1), 0}});
    interno.children.push_back({Rect{v, v, v + 1, v + 1}, static_cast<PageId>(i + 1)});
  }
  Page pagina(kDefaultPageSize);
  RTree::encode(hoja, pagina);
  EXPECT_EQ(RTree::decode(pagina), hoja);
  RTree::encode(interno, pagina);
  EXPECT_EQ(RTree::decode(pagina), interno);
}

TEST(RTreeCodecTest, UnaPaginaQueNoEsUnNodoSeDenuncia) {
  Page pagina(kDefaultPageSize);
  pagina.clear();
  pagina.write<std::byte>(0, std::byte{7});
  EXPECT_THROW((void)RTree::decode(pagina), IoError);
}

TEST_F(RTreeTest, UnNodoSobreviveAlDisco) {
  RTreeNode hoja;
  hoja.points = {{Point{-77.0428, -12.0464}, RID{3, 1}}, {Point{-71.53, -16.40}, RID{3, 2}}};
  {
    DiskManager disco(path_);
    Page pagina(disco.page_size());
    RTree::encode(hoja, pagina);
    const PageId id = disco.allocate_page();
    disco.write_page(id, pagina);
    disco.flush();
  }
  DiskManager disco(path_);
  Page leida(disco.page_size());
  disco.read_page(1, leida);
  EXPECT_EQ(RTree::decode(leida), hoja);
}

// ---------------------------------------------------------------------------
// Persistencia del area meta
// ---------------------------------------------------------------------------

TEST_F(RTreeTest, UnArbolNuevoEstaVacioYCumpleSusInvariantes) {
  RTree arbol(path_, kDefaultPageSize, 4);
  EXPECT_EQ(arbol.size(), 0u);
  EXPECT_EQ(arbol.height(), 0u);
  EXPECT_EQ(arbol.root(), kInvalidPage);
  EXPECT_EQ(arbol.free_pages(), 0u);
  EXPECT_EQ(arbol.check_invariants(), "");
}

TEST_F(RTreeTest, SeCreaSeCierraYSeReabreConservandoSuMeta) {
  {
    RTree arbol(path_, kDefaultPageSize, 6);
    arbol.flush();
  }
  RTree reabierto(path_, kDefaultPageSize, 6);
  EXPECT_EQ(reabierto.order(), 6u);
  EXPECT_EQ(reabierto.size(), 0u);
  EXPECT_EQ(reabierto.height(), 0u);
  EXPECT_EQ(reabierto.check_invariants(), "");
}

TEST_F(RTreeTest, ReabrirConOtroOrdenEsUnSchemaError) {
  { RTree arbol(path_, kDefaultPageSize, 6); }
  EXPECT_THROW(RTree(path_, kDefaultPageSize, 8), SchemaError);
}

TEST_F(RTreeTest, ReabrirOtraVersionDelFormatoEsUnIoError) {
  { RTree arbol(path_, kDefaultPageSize, 6); }
  {
    // Se falsifica la version en el area meta, que es su primer campo.
    DiskManager disco(path_);
    std::vector<std::byte> meta(DiskManager::kMetaSize);
    disco.read_meta(meta);
    const std::uint32_t otra = 99;
    std::memcpy(meta.data(), &otra, sizeof otra);
    disco.write_meta(meta);
    disco.flush();
  }
  EXPECT_THROW(RTree(path_, kDefaultPageSize, 6), IoError);
}

TEST_F(RTreeTest, LasEstadisticasEmpiezanEnCero) {
  RTree arbol(path_, kDefaultPageSize, 4);
  EXPECT_EQ(arbol.stats().pages_read, 0u);
  EXPECT_EQ(arbol.stats().pages_written, 0u);
}

}  // namespace
}  // namespace quipudb
