// Pruebas de la busqueda por rectangulo del R-Tree (issue #117).

#include <gtest/gtest.h>

#include <filesystem>
#include <iostream>
#include <limits>
#include <random>
#include <set>
#include <string>
#include <tuple>
#include <vector>

#include "quipudb/error.hpp"
#include "quipudb/index/rtree.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

class RTreeBusquedaTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_rtree_bus_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
    path_ = dir_ / "indice.rtree";
  }
  void TearDown() override { fs::remove_all(dir_); }

  fs::path dir_;
  fs::path path_;
};

RID rid_de(std::size_t i) {
  return RID{static_cast<PageId>(i / 100 + 1), static_cast<SlotId>(i % 100)};
}

using Clave = std::tuple<double, double, PageId, SlotId>;
std::multiset<Clave> claves(const std::vector<RTreeLeafEntry>& entradas) {
  std::multiset<Clave> s;
  for (const auto& e : entradas) s.insert({e.point.x, e.point.y, e.rid.page, e.rid.slot});
  return s;
}

/// Lo que deberia devolver la busqueda, revisando los puntos uno por uno.
std::vector<RTreeLeafEntry> a_fuerza_bruta(const std::vector<RTreeLeafEntry>& todos,
                                           const Rect& region) {
  std::vector<RTreeLeafEntry> out;
  for (const auto& e : todos) {
    if (region.contains(e.point)) out.push_back(e);
  }
  return out;
}

// Caja de Lima Metropolitana.
constexpr Rect kLima{-77.2, -12.3, -76.8, -11.8};

std::vector<RTreeLeafEntry> puntos_en(const Rect& caja, std::size_t n, unsigned semilla) {
  std::mt19937 gen(semilla);
  std::uniform_real_distribution<double> x(caja.min_x, caja.max_x);
  std::uniform_real_distribution<double> y(caja.min_y, caja.max_y);
  std::vector<RTreeLeafEntry> out;
  out.reserve(n);
  for (std::size_t i = 0; i < n; ++i) out.push_back({Point{x(gen), y(gen)}, rid_de(i)});
  return out;
}

// ---------------------------------------------------------------------------
// Correccion
// ---------------------------------------------------------------------------

TEST_F(RTreeBusquedaTest, UnArbolVacioNoDevuelveNadaNiLeeNada) {
  RTree arbol(path_, kDefaultPageSize, 4);
  EXPECT_TRUE(arbol.search(kLima).empty());
  EXPECT_EQ(arbol.stats().pages_read, 0u);
}

TEST_F(RTreeBusquedaTest, CoincideConRevisarTodosLosPuntosUnoPorUno) {
  RTree arbol(path_, kDefaultPageSize, 4);
  const auto todos = puntos_en(kLima, 5000, 11);
  for (const auto& e : todos) arbol.insert(e.point, e.rid);

  std::mt19937 gen(99);
  std::uniform_real_distribution<double> x(kLima.min_x - 0.05, kLima.max_x + 0.05);
  std::uniform_real_distribution<double> y(kLima.min_y - 0.05, kLima.max_y + 0.05);
  for (int consulta = 0; consulta < 300; ++consulta) {
    double x1 = x(gen), x2 = x(gen), y1 = y(gen), y2 = y(gen);
    if (x1 > x2) std::swap(x1, x2);
    if (y1 > y2) std::swap(y1, y2);
    const Rect region{x1, y1, x2, y2};
    ASSERT_EQ(claves(arbol.search(region)), claves(a_fuerza_bruta(todos, region)))
        << "consulta " << consulta;
  }
}

TEST_F(RTreeBusquedaTest, LosBordesDeLaRegionEstanIncluidos) {
  RTree arbol(path_, kDefaultPageSize, 4);
  arbol.insert(Point{0, 0}, RID{1, 0});
  arbol.insert(Point{10, 10}, RID{1, 1});
  arbol.insert(Point{10, 0}, RID{1, 2});
  arbol.insert(Point{10.0001, 5}, RID{1, 3});

  const auto encontrados = arbol.search(Rect{0, 0, 10, 10});
  EXPECT_EQ(encontrados.size(), 3u);
}

TEST_F(RTreeBusquedaTest, UnaRegionQueLoCubreTodoDevuelveTodo) {
  RTree arbol(path_, kDefaultPageSize, 4);
  const auto todos = puntos_en(kLima, 1000, 3);
  for (const auto& e : todos) arbol.insert(e.point, e.rid);
  EXPECT_EQ(claves(arbol.search(Rect{-180, -90, 180, 90})), claves(todos));
}

TEST_F(RTreeBusquedaTest, DevuelveTodosLosRegistrosDeUnPuntoRepetido) {
  RTree arbol(path_, kDefaultPageSize, 4);
  for (std::size_t i = 0; i < 50; ++i) arbol.insert(Point{-77.0, -12.0}, rid_de(i));
  arbol.insert(Point{-76.9, -12.1}, rid_de(50));

  EXPECT_EQ(arbol.search(Rect::of(Point{-77.0, -12.0})).size(), 50u);
}

TEST_F(RTreeBusquedaTest, UnaRegionInvertidaNoContieneNadaYNoLee) {
  RTree arbol(path_, kDefaultPageSize, 4);
  for (const auto& e : puntos_en(kLima, 100, 5)) arbol.insert(e.point, e.rid);
  arbol.reset_stats();

  EXPECT_TRUE(arbol.search(Rect{-76.8, -11.8, -77.2, -12.3}).empty());
  EXPECT_EQ(arbol.stats().pages_read, 0u);
}

TEST_F(RTreeBusquedaTest, RechazaUnaRegionConCoordenadasNoFinitas) {
  RTree arbol(path_, kDefaultPageSize, 4);
  const double nan = std::numeric_limits<double>::quiet_NaN();
  EXPECT_THROW((void)arbol.search(Rect{nan, 0, 1, 1}), InvalidRecord);
  EXPECT_THROW((void)arbol.search(Rect{0, 0, std::numeric_limits<double>::infinity(), 1}),
               InvalidRecord);
}

TEST_F(RTreeBusquedaTest, ReportaLoQueExaminoYLoQueDevolvio) {
  RTree arbol(path_, kDefaultPageSize, 4);
  for (const auto& e : puntos_en(kLima, 2000, 8)) arbol.insert(e.point, e.rid);
  arbol.reset_stats();

  const auto encontrados = arbol.search(Rect{-77.05, -12.1, -76.95, -12.0});
  const OpStats s = arbol.stats();
  EXPECT_EQ(s.records_returned, encontrados.size());
  EXPECT_GE(s.records_examined, s.records_returned);
  EXPECT_GE(s.pages_read, arbol.height());
  EXPECT_EQ(s.pages_written, 0u);
}

// ---------------------------------------------------------------------------
// La poda
// ---------------------------------------------------------------------------

// Esta es la prueba que demuestra que la poda ocurre. Sin ella, una busqueda
// que recorriera el arbol entero y filtrara al final pasaria todas las demas.
TEST_F(RTreeBusquedaTest, UnaRegionVaciaLeeMuyPocasPaginas) {
  RTree arbol(path_);
  for (const auto& e : puntos_en(kLima, 100'000, 2026)) arbol.insert(e.point, e.rid);
  const std::size_t total = arbol.page_count();

  // Arequipa: lejos de todo. Ningun hijo de la raiz se solapa, asi que solo se
  // lee la raiz.
  arbol.reset_stats();
  EXPECT_TRUE(arbol.search(Rect{-71.6, -16.5, -71.4, -16.3}).empty());
  EXPECT_EQ(arbol.stats().pages_read, 1u);
  EXPECT_EQ(arbol.stats().records_examined, 0u);

  // Pegada al borde de Lima pero fuera: el MBR de la raiz la roza, y aun asi
  // la busqueda no pasa de la primera capa de hijos.
  arbol.reset_stats();
  EXPECT_TRUE(arbol.search(Rect{-77.3, -12.0, -77.21, -11.9}).empty());
  EXPECT_LE(arbol.stats().pages_read, 1u + arbol.order());

  // Una region chica con datos lee una fraccion minima del archivo.
  arbol.reset_stats();
  const auto encontrados = arbol.search(Rect{-77.01, -12.01, -77.0, -12.0});
  const std::uint64_t leidas = arbol.stats().pages_read;
  EXPECT_FALSE(encontrados.empty());
  EXPECT_LT(leidas * 50, total) << "leyo " << leidas << " de " << total << " paginas";

  std::cout << "[ R-Tree ] " << total << " paginas; region vacia lejana: 1 pagina; "
            << "region de ~1 km2 con " << encontrados.size() << " puntos: " << leidas
            << " paginas\n";
}

}  // namespace
}  // namespace quipudb
