// Pruebas de la insercion del R-Tree (issue #116).

#include <gtest/gtest.h>

#include <algorithm>
#include <chrono>
#include <cmath>
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

// Orden chico para tener arboles altos con pocos puntos, como el #14.
constexpr std::size_t kOrdenChico = 4;

class RTreeInsercionTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_rtree_ins_" + std::string(info->name()));
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

/// Clave ordenable de una entrada, para comparar lo que se inserto con lo que
/// el arbol devuelve sin depender del orden de las hojas.
using Clave = std::tuple<double, double, PageId, SlotId>;
Clave clave(const RTreeLeafEntry& e) { return {e.point.x, e.point.y, e.rid.page, e.rid.slot}; }

std::multiset<Clave> claves(const std::vector<RTreeLeafEntry>& entradas) {
  std::multiset<Clave> s;
  for (const auto& e : entradas) s.insert(clave(e));
  return s;
}

// ---------------------------------------------------------------------------
// Forma del arbol
// ---------------------------------------------------------------------------

TEST_F(RTreeInsercionTest, ElPrimerPuntoCreaUnaRaizHoja) {
  RTree arbol(path_, kDefaultPageSize, kOrdenChico);
  arbol.insert(Point{-77.04, -12.05}, RID{1, 0});

  EXPECT_EQ(arbol.size(), 1u);
  EXPECT_EQ(arbol.height(), 1u);
  EXPECT_EQ(arbol.check_invariants(), "");
  ASSERT_EQ(arbol.scan().size(), 1u);
  EXPECT_EQ(arbol.scan()[0], (RTreeLeafEntry{Point{-77.04, -12.05}, RID{1, 0}}));
}

TEST_F(RTreeInsercionTest, LaRaizSeLlenaSinPartirse) {
  RTree arbol(path_, kDefaultPageSize, kOrdenChico);
  for (std::size_t i = 0; i < kOrdenChico; ++i) {
    arbol.insert(Point{static_cast<double>(i), 0.0}, rid_de(i));
  }
  EXPECT_EQ(arbol.height(), 1u);
  EXPECT_EQ(arbol.check_invariants(), "");
}

TEST_F(RTreeInsercionTest, CuandoLaRaizSeParteElArbolSubeDeAltura) {
  RTree arbol(path_, kDefaultPageSize, kOrdenChico);
  for (std::size_t i = 0; i <= kOrdenChico; ++i) {
    arbol.insert(Point{static_cast<double>(i), static_cast<double>(i)}, rid_de(i));
  }
  EXPECT_EQ(arbol.height(), 2u);
  EXPECT_EQ(arbol.size(), kOrdenChico + 1);
  // check_invariants exige, entre otras cosas, que una raiz interna tenga al
  // menos dos hijos y que cada uno tenga al menos min_fill entradas.
  EXPECT_EQ(arbol.check_invariants(), "");
}

TEST_F(RTreeInsercionTest, LosInvariantesSeCumplenDespuesDeCadaInsercion) {
  RTree arbol(path_, kDefaultPageSize, kOrdenChico);
  std::mt19937 gen(42);
  std::uniform_real_distribution<double> lon(-77.2, -76.8);
  std::uniform_real_distribution<double> lat(-12.3, -11.8);
  std::vector<RTreeLeafEntry> insertadas;

  std::size_t altura_previa = 0;
  for (std::size_t i = 0; i < 2000; ++i) {
    const RTreeLeafEntry e{Point{lon(gen), lat(gen)}, rid_de(i)};
    arbol.insert(e.point, e.rid);
    insertadas.push_back(e);
    ASSERT_EQ(arbol.check_invariants(), "") << "tras insertar el punto " << i;
    // La altura solo sube, y de a un nivel.
    ASSERT_GE(arbol.height(), altura_previa);
    ASSERT_LE(arbol.height(), altura_previa + 1);
    altura_previa = arbol.height();
  }
  EXPECT_EQ(arbol.size(), 2000u);
  EXPECT_EQ(claves(arbol.scan()), claves(insertadas));
}

// ---------------------------------------------------------------------------
// Datos degenerados: donde las areas no dicen nada
// ---------------------------------------------------------------------------

TEST_F(RTreeInsercionTest, PuntosRepetidosEnElMismoLugar) {
  // Varios registros en la misma direccion es lo normal, no un caso raro.
  RTree arbol(path_, kDefaultPageSize, kOrdenChico);
  for (std::size_t i = 0; i < 300; ++i) arbol.insert(Point{-77.0, -12.0}, rid_de(i));

  EXPECT_EQ(arbol.size(), 300u);
  EXPECT_EQ(arbol.check_invariants(), "");
  EXPECT_EQ(arbol.scan().size(), 300u);
}

TEST_F(RTreeInsercionTest, PuntosAlineados) {
  // Todos los MBR tienen area 0: sin el desempate por semiperimetro el split
  // elige siempre el primer par y el arbol queda muy mal repartido.
  RTree arbol(path_, kDefaultPageSize, kOrdenChico);
  for (std::size_t i = 0; i < 500; ++i) {
    arbol.insert(Point{static_cast<double>(i), 5.0}, rid_de(i));
  }
  EXPECT_EQ(arbol.check_invariants(), "");
  EXPECT_EQ(arbol.scan().size(), 500u);
}

TEST_F(RTreeInsercionTest, RechazaCoordenadasNoFinitas) {
  RTree arbol(path_, kDefaultPageSize, kOrdenChico);
  const double nan = std::numeric_limits<double>::quiet_NaN();
  const double inf = std::numeric_limits<double>::infinity();

  EXPECT_THROW(arbol.insert(Point{nan, 0.0}, RID{1, 0}), InvalidRecord);
  EXPECT_THROW(arbol.insert(Point{0.0, inf}, RID{1, 0}), InvalidRecord);
  EXPECT_THROW(arbol.insert(Point{-inf, nan}, RID{1, 0}), InvalidRecord);
  EXPECT_EQ(arbol.size(), 0u);
  EXPECT_EQ(arbol.check_invariants(), "");
}

// ---------------------------------------------------------------------------
// Persistencia
// ---------------------------------------------------------------------------

TEST_F(RTreeInsercionTest, RechazaCoordenadasFueraDeRango) {
  RTree arbol(path_, kDefaultPageSize, 4);
  const double max = RTree::kMaxCoordinate;
  EXPECT_THROW(arbol.insert(Point{max * 10, 0}, RID{1, 0}), InvalidRecord);
  EXPECT_THROW(arbol.insert(Point{0, -max * 10}, RID{1, 0}), InvalidRecord);
  EXPECT_THROW(arbol.insert(Point{std::numeric_limits<double>::max(), 0}, RID{1, 0}),
               InvalidRecord);
  EXPECT_EQ(arbol.size(), 0u);
  // Lo que nunca pudo entrar tampoco se puede borrar.
  EXPECT_FALSE(arbol.remove(Point{max * 10, 0}, RID{1, 0}));
}

// Con coordenadas cerca de DBL_MAX el area de un MBR era infinita, la
// ampliacion salia inf - inf = NaN y el split leia fuera de su vector: el
// archivo crecia a 126 000 paginas y terminaba pidiendo la pagina 0. En el
// borde del rango admitido las cuentas tienen que seguir siendo finitas.
TEST_F(RTreeInsercionTest, EnElBordeDelRangoLasCuentasSiguenSiendoFinitas) {
  RTree arbol(path_, kDefaultPageSize, 4);
  const double max = RTree::kMaxCoordinate;
  std::mt19937 gen(8);
  std::uniform_real_distribution<double> u(-1.0, 1.0);
  std::vector<RTreeLeafEntry> puntos;
  for (std::size_t i = 0; i < 2000; ++i) {
    puntos.push_back({Point{u(gen) * max, u(gen) * max}, rid_de(i)});
    arbol.insert(puntos.back().point, puntos.back().rid);
  }
  puntos.push_back({Point{max, -max}, rid_de(2000)});
  arbol.insert(puntos.back().point, puntos.back().rid);

  ASSERT_EQ(arbol.check_invariants(), "");
  EXPECT_EQ(arbol.search(Rect{-max, -max, max, max}).size(), puntos.size());
  for (const auto& e : puntos) ASSERT_TRUE(arbol.remove(e.point, e.rid));
  EXPECT_EQ(arbol.check_invariants(), "");
  EXPECT_EQ(arbol.free_pages(), arbol.page_count());
}

TEST_F(RTreeInsercionTest, SeReabreConTodosSusPuntosYSigueCreciendo) {
  std::vector<RTreeLeafEntry> insertadas;
  std::mt19937 gen(7);
  std::uniform_real_distribution<double> d(-100.0, 100.0);
  {
    RTree arbol(path_, kDefaultPageSize, kOrdenChico);
    for (std::size_t i = 0; i < 500; ++i) {
      const RTreeLeafEntry e{Point{d(gen), d(gen)}, rid_de(i)};
      arbol.insert(e.point, e.rid);
      insertadas.push_back(e);
    }
    // Sin flush a proposito: la meta se guarda en cada insercion, como el B+.
  }
  RTree reabierto(path_, kDefaultPageSize, kOrdenChico);
  EXPECT_EQ(reabierto.size(), 500u);
  EXPECT_EQ(reabierto.check_invariants(), "");
  EXPECT_EQ(claves(reabierto.scan()), claves(insertadas));

  for (std::size_t i = 500; i < 800; ++i) {
    const RTreeLeafEntry e{Point{d(gen), d(gen)}, rid_de(i)};
    reabierto.insert(e.point, e.rid);
    insertadas.push_back(e);
  }
  EXPECT_EQ(reabierto.check_invariants(), "");
  EXPECT_EQ(claves(reabierto.scan()), claves(insertadas));
}

// ---------------------------------------------------------------------------
// Split cuadratico, como funcion pura
// ---------------------------------------------------------------------------

/// Cada indice aparece exactamente una vez entre los dos grupos.
void esperar_particion(const RTree::SplitGroups& g, std::size_t n, std::size_t minimo) {
  EXPECT_GE(g.first.size(), minimo);
  EXPECT_GE(g.second.size(), minimo);
  std::vector<std::size_t> todos = g.first;
  todos.insert(todos.end(), g.second.begin(), g.second.end());
  std::sort(todos.begin(), todos.end());
  std::vector<std::size_t> esperado(n);
  for (std::size_t i = 0; i < n; ++i) esperado[i] = i;
  EXPECT_EQ(todos, esperado);
}

TEST(RTreeSplitTest, SeparaDosGruposLejanos) {
  // Tres puntos cerca del origen y dos lejos, mezclados en el orden.
  const std::vector<Rect> rects = {Rect::of({0, 0}),     Rect::of({100, 100}), Rect::of({1, 1}),
                                   Rect::of({101, 100}), Rect::of({0, 1})};
  const auto g = RTree::quadratic_split(rects, 2);
  esperar_particion(g, rects.size(), 2);

  std::set<std::size_t> uno(g.first.begin(), g.first.end());
  std::set<std::size_t> otro(g.second.begin(), g.second.end());
  const std::set<std::size_t> cerca{0, 2, 4};
  const std::set<std::size_t> lejos{1, 3};
  EXPECT_TRUE((uno == cerca && otro == lejos) || (uno == lejos && otro == cerca));
}

TEST(RTreeSplitTest, LosDosGruposNoSeSolapanCuandoHayComoEvitarlo) {
  std::vector<Rect> rects;
  for (int i = 0; i < 6; ++i) rects.push_back(Rect::of({static_cast<double>(i), 0}));
  for (int i = 0; i < 6; ++i) rects.push_back(Rect::of({static_cast<double>(i + 50), 0}));
  const auto g = RTree::quadratic_split(rects, 4);
  esperar_particion(g, rects.size(), 4);

  Rect a = rects[g.first[0]];
  for (const auto i : g.first) a = a.united(rects[i]);
  Rect b = rects[g.second[0]];
  for (const auto i : g.second) b = b.united(rects[i]);
  EXPECT_FALSE(a.intersects(b));
}

TEST(RTreeSplitTest, RespetaElMinimoAunqueTodoPrefieraUnLado) {
  // Uno lejos y el resto amontonado: sin la regla de "llevarse lo que falta",
  // todo iria al grupo del monton y el otro quedaria con una sola entrada.
  std::vector<Rect> rects = {Rect::of({1000, 1000})};
  for (int i = 0; i < 8; ++i) rects.push_back(Rect::of({i * 0.01, i * 0.01}));
  const auto g = RTree::quadratic_split(rects, 3);
  esperar_particion(g, rects.size(), 3);
}

TEST(RTreeSplitTest, RectangulosIdenticosSeRepartenAmbosLadosValidos) {
  const std::vector<Rect> rects(9, Rect::of({3, 3}));
  const auto g = RTree::quadratic_split(rects, 3);
  esperar_particion(g, rects.size(), 3);
}

TEST(RTreeSplitTest, RechazaUnMinimoImposible) {
  const std::vector<Rect> rects(5, Rect::of({0, 0}));
  EXPECT_THROW((void)RTree::quadratic_split(rects, 3), std::logic_error);
}

// ---------------------------------------------------------------------------
// Volumen: 100 000 puntos
// ---------------------------------------------------------------------------

TEST_F(RTreeInsercionTest, CienMilPuntosConElOrdenPorDefecto) {
  RTree arbol(path_);
  std::mt19937 gen(2026);
  // Caja de Lima Metropolitana.
  std::uniform_real_distribution<double> lon(-77.2, -76.8);
  std::uniform_real_distribution<double> lat(-12.3, -11.8);

  constexpr std::size_t n = 100'000;
  const auto inicio = std::chrono::steady_clock::now();
  for (std::size_t i = 0; i < n; ++i) arbol.insert(Point{lon(gen), lat(gen)}, rid_de(i));
  const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                      std::chrono::steady_clock::now() - inicio)
                      .count();
  const OpStats s = arbol.stats();

  EXPECT_EQ(arbol.size(), n);
  // Con M = 113 y m = 45, tres niveles cubren mas de un millon de puntos.
  EXPECT_LE(arbol.height(), 4u);

  // Cada insercion lee un nodo por nivel. Escribe la hoja, a veces algunos
  // ancestros cuyo MBR crecio, y cada tanto un split: en promedio poco mas de
  // una pagina, no un camino entero.
  const double leidas = static_cast<double>(s.pages_read) / n;
  const double escritas = static_cast<double>(s.pages_written) / n;
  EXPECT_LE(leidas, static_cast<double>(arbol.height()) + 0.1);
  EXPECT_LT(escritas, 2.5);

  std::cout << "[ R-Tree ] " << n << " puntos, orden " << arbol.order() << ", altura "
            << arbol.height() << ", " << arbol.page_count() << " paginas, " << ms << " ms\n"
            << "[ R-Tree ] paginas leidas: " << s.pages_read << " (" << leidas
            << " por insercion), escritas: " << s.pages_written << " (" << escritas
            << " por insercion)\n";
  RecordProperty("paginas_leidas", std::to_string(s.pages_read));
  RecordProperty("paginas_escritas", std::to_string(s.pages_written));

  EXPECT_EQ(arbol.check_invariants(), "");
}

}  // namespace
}  // namespace quipudb
