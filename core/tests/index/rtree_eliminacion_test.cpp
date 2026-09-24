// Pruebas de la eliminacion del R-Tree (issue #118).

#include <gtest/gtest.h>

#include <algorithm>
#include <filesystem>
#include <iostream>
#include <limits>
#include <random>
#include <set>
#include <string>
#include <tuple>
#include <vector>

#include "quipudb/index/rtree.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

constexpr std::size_t kOrdenChico = 4;

class RTreeEliminacionTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_rtree_del_" + std::string(info->name()));
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
Clave clave(const RTreeLeafEntry& e) { return {e.point.x, e.point.y, e.rid.page, e.rid.slot}; }
std::multiset<Clave> claves(const std::vector<RTreeLeafEntry>& entradas) {
  std::multiset<Clave> s;
  for (const auto& e : entradas) s.insert(clave(e));
  return s;
}

std::vector<RTreeLeafEntry> puntos_al_azar(std::size_t n, unsigned semilla) {
  std::mt19937 gen(semilla);
  std::uniform_real_distribution<double> lon(-77.2, -76.8);
  std::uniform_real_distribution<double> lat(-12.3, -11.8);
  std::vector<RTreeLeafEntry> out;
  for (std::size_t i = 0; i < n; ++i) out.push_back({Point{lon(gen), lat(gen)}, rid_de(i)});
  return out;
}

// ---------------------------------------------------------------------------
// Casos simples
// ---------------------------------------------------------------------------

TEST_F(RTreeEliminacionTest, BorrarAlgoQueNoEstaDevuelveFalse) {
  RTree arbol(path_, kDefaultPageSize, kOrdenChico);
  EXPECT_FALSE(arbol.remove(Point{0, 0}, RID{1, 0}));  // arbol vacio

  arbol.insert(Point{1, 1}, RID{1, 0});
  EXPECT_FALSE(arbol.remove(Point{2, 2}, RID{1, 0}));  // otro punto
  EXPECT_FALSE(arbol.remove(Point{1, 1}, RID{1, 1}));  // otro registro
  EXPECT_FALSE(arbol.remove(Point{std::numeric_limits<double>::quiet_NaN(), 1}, RID{1, 0}));
  EXPECT_EQ(arbol.size(), 1u);
  EXPECT_EQ(arbol.check_invariants(), "");
}

TEST_F(RTreeEliminacionTest, BorrarElUltimoPuntoDejaElArbolVacio) {
  RTree arbol(path_, kDefaultPageSize, kOrdenChico);
  arbol.insert(Point{1, 1}, RID{1, 0});
  EXPECT_TRUE(arbol.remove(Point{1, 1}, RID{1, 0}));

  EXPECT_EQ(arbol.size(), 0u);
  EXPECT_EQ(arbol.height(), 0u);
  EXPECT_EQ(arbol.root(), kInvalidPage);
  EXPECT_EQ(arbol.check_invariants(), "");
  // La pagina de la raiz quedo libre.
  EXPECT_EQ(arbol.free_pages(), arbol.page_count());
}

TEST_F(RTreeEliminacionTest, EnUnPuntoRepetidoSoloSeBorraElRegistroPedido) {
  RTree arbol(path_, kDefaultPageSize, kOrdenChico);
  for (std::size_t i = 0; i < 40; ++i) arbol.insert(Point{-77.0, -12.0}, rid_de(i));

  EXPECT_TRUE(arbol.remove(Point{-77.0, -12.0}, rid_de(17)));
  EXPECT_FALSE(arbol.remove(Point{-77.0, -12.0}, rid_de(17)));  // ya no esta

  const auto quedan = arbol.search(Rect::of(Point{-77.0, -12.0}));
  EXPECT_EQ(quedan.size(), 39u);
  EXPECT_TRUE(std::none_of(quedan.begin(), quedan.end(),
                           [](const RTreeLeafEntry& e) { return e.rid == rid_de(17); }));
  EXPECT_EQ(arbol.check_invariants(), "");
}

// ---------------------------------------------------------------------------
// Invariantes a lo largo de muchas eliminaciones
// ---------------------------------------------------------------------------

// check_invariants exige, en cada paso: MBR exactamente iguales a la union de
// sus hijos (asi que tienen que haber encogido), todas las hojas a la misma
// altura (asi que las entradas de un nodo interno se reinsertaron como
// subarboles y no como puntos sueltos), y cada nodo dentro de [m, M].
TEST_F(RTreeEliminacionTest, LosInvariantesSeCumplenTrasCadaEliminacion) {
  RTree arbol(path_, kDefaultPageSize, kOrdenChico);
  auto vivos = puntos_al_azar(2000, 21);
  for (const auto& e : vivos) arbol.insert(e.point, e.rid);

  std::mt19937 gen(5);
  std::shuffle(vivos.begin(), vivos.end(), gen);
  std::size_t alturas_bajadas = 0;
  std::size_t altura_previa = arbol.height();

  while (!vivos.empty()) {
    const RTreeLeafEntry e = vivos.back();
    vivos.pop_back();
    ASSERT_TRUE(arbol.remove(e.point, e.rid));
    ASSERT_EQ(arbol.check_invariants(), "") << "quedando " << vivos.size() << " puntos";
    ASSERT_EQ(arbol.size(), vivos.size());
    if (arbol.height() < altura_previa) ++alturas_bajadas;
    ASSERT_LE(arbol.height(), altura_previa);
    altura_previa = arbol.height();
    if (vivos.size() % 250 == 0) {
      ASSERT_EQ(claves(arbol.scan()), claves(vivos)) << "quedando " << vivos.size();
    }
  }
  EXPECT_EQ(arbol.height(), 0u);
  // Con orden 4 y 2000 puntos el arbol empieza con varios niveles: tiene que
  // haber bajado de altura mas de una vez hasta vaciarse.
  EXPECT_GT(alturas_bajadas, 1u);
}

TEST_F(RTreeEliminacionTest, InsertarYBorrarIntercaladosNoRompenNada) {
  RTree arbol(path_, kDefaultPageSize, kOrdenChico);
  std::mt19937 gen(33);
  std::uniform_real_distribution<double> d(-50.0, 50.0);
  std::vector<RTreeLeafEntry> vivos;
  std::size_t siguiente = 0;

  for (int paso = 0; paso < 4000; ++paso) {
    const bool insertar = vivos.empty() || gen() % 3 != 0;  // dos de cada tres
    if (insertar) {
      const RTreeLeafEntry e{Point{d(gen), d(gen)}, rid_de(siguiente++)};
      arbol.insert(e.point, e.rid);
      vivos.push_back(e);
    } else {
      const std::size_t i = gen() % vivos.size();
      ASSERT_TRUE(arbol.remove(vivos[i].point, vivos[i].rid));
      vivos[i] = vivos.back();
      vivos.pop_back();
    }
    ASSERT_EQ(arbol.check_invariants(), "") << "paso " << paso;
  }
  EXPECT_EQ(claves(arbol.scan()), claves(vivos));
}

TEST_F(RTreeEliminacionTest, BorrarUnPuntoLejanoEncogeElMbrYLaPodaLoNota) {
  RTree arbol(path_, kDefaultPageSize, kOrdenChico);
  for (const auto& e : puntos_al_azar(500, 4)) arbol.insert(e.point, e.rid);
  // Un punto en Arequipa estira el MBR de la raiz hasta alla.
  arbol.insert(Point{-71.5, -16.4}, RID{999, 0});
  arbol.remove(Point{-71.5, -16.4}, RID{999, 0});

  // Si los MBR no hubieran encogido, buscar en Arequipa bajaria por el arbol.
  arbol.reset_stats();
  EXPECT_TRUE(arbol.search(Rect{-71.6, -16.5, -71.4, -16.3}).empty());
  EXPECT_EQ(arbol.stats().pages_read, 1u);
}

// ---------------------------------------------------------------------------
// La free list: el archivo no crece
// ---------------------------------------------------------------------------

// La misma prueba que atrapo defectos reales en el B+ (#17) y en el hash
// extensible (#19): si las paginas liberadas no se reutilizan, cada ciclo de
// vaciar y rellenar deja el archivo mas grande.
//
// El orden de borrado es el mismo en las tres vueltas a proposito: asi cada
// vuelta pide exactamente las mismas paginas y el archivo tiene que quedarse
// clavado. Con ordenes distintos hay un matiz, que prueba el test siguiente.
TEST_F(RTreeEliminacionTest, VaciarYRellenarTresVecesNoHaceCrecerElArchivo) {
  RTree arbol(path_, kDefaultPageSize, kOrdenChico);
  const auto puntos = puntos_al_azar(3000, 77);
  auto orden = puntos;
  std::mt19937 gen(1);
  std::shuffle(orden.begin(), orden.end(), gen);

  PageId paginas = 0;
  for (int vuelta = 0; vuelta < 3; ++vuelta) {
    for (const auto& e : puntos) arbol.insert(e.point, e.rid);
    ASSERT_EQ(arbol.check_invariants(), "");
    if (vuelta == 0) paginas = arbol.page_count();
    EXPECT_EQ(arbol.page_count(), paginas) << "tras rellenar, vuelta " << vuelta;

    for (const auto& e : orden) ASSERT_TRUE(arbol.remove(e.point, e.rid));
    ASSERT_EQ(arbol.size(), 0u);
    EXPECT_EQ(arbol.page_count(), paginas) << "tras vaciar, vuelta " << vuelta;
    // Vacio, todas las paginas del archivo estan en la free list: ninguna se
    // perdio por el camino.
    ASSERT_EQ(arbol.free_pages(), arbol.page_count()) << "vuelta " << vuelta;
  }
  std::cout << "[ R-Tree ] 3 vueltas de 3000 puntos: el archivo se quedo en " << paginas
            << " paginas\n";
}

// Un borrado puede dejar MAS nodos de los que habia: libera el nodo que quedo
// en falta, pero reinsertar sus entradas puede partir otros en cascada. Es una
// propiedad del algoritmo de Guttman, no un defecto. Si pasa cuando la free
// list esta vacia -- al empezar a vaciar un arbol recien llenado --, ese pico
// sale de una pagina nueva.
//
// Lo que no puede pasar es que las paginas se pierdan: con ordenes de borrado
// distintos el archivo puede crecer lo que pida el pico, pero al vaciar todo
// tiene que estar en la free list, y el crecimiento tiene que ser acotado.
TEST_F(RTreeEliminacionTest, ConOrdenesDeBorradoDistintosNingunaPaginaSePierde) {
  RTree arbol(path_, kDefaultPageSize, kOrdenChico);
  const auto puntos = puntos_al_azar(3000, 77);
  std::mt19937 gen(1);

  PageId tras_primer_relleno = 0;
  for (int vuelta = 0; vuelta < 5; ++vuelta) {
    for (const auto& e : puntos) arbol.insert(e.point, e.rid);
    if (vuelta == 0) tras_primer_relleno = arbol.page_count();

    auto orden = puntos;
    std::shuffle(orden.begin(), orden.end(), gen);
    for (const auto& e : orden) ASSERT_TRUE(arbol.remove(e.point, e.rid));
    ASSERT_EQ(arbol.free_pages(), arbol.page_count()) << "vuelta " << vuelta;
  }
  // Acotado: unas pocas paginas de pico, no una por vuelta ni una por borrado.
  EXPECT_LE(arbol.page_count(), tras_primer_relleno + 8);
  std::cout << "[ R-Tree ] 5 vueltas con ordenes distintos: " << tras_primer_relleno
            << " paginas tras el primer relleno, " << arbol.page_count() << " al final\n";
}

TEST_F(RTreeEliminacionTest, LaFreeListSobreviveAlReabrir) {
  const auto puntos = puntos_al_azar(1000, 9);
  std::size_t libres = 0;
  PageId paginas = 0;
  {
    RTree arbol(path_, kDefaultPageSize, kOrdenChico);
    for (const auto& e : puntos) arbol.insert(e.point, e.rid);
    for (std::size_t i = 0; i < 600; ++i) arbol.remove(puntos[i].point, puntos[i].rid);
    libres = arbol.free_pages();
    paginas = arbol.page_count();
    ASSERT_GT(libres, 0u);
  }
  RTree reabierto(path_, kDefaultPageSize, kOrdenChico);
  EXPECT_EQ(reabierto.size(), 400u);
  EXPECT_EQ(reabierto.free_pages(), libres);
  EXPECT_EQ(reabierto.check_invariants(), "");

  // Reinsertar lo borrado reutiliza las paginas libres antes de crecer.
  for (std::size_t i = 0; i < 600; ++i) reabierto.insert(puntos[i].point, puntos[i].rid);
  EXPECT_EQ(reabierto.check_invariants(), "");
  EXPECT_LE(reabierto.page_count(), paginas + 1);
  EXPECT_EQ(claves(reabierto.scan()), claves(puntos));
}

}  // namespace
}  // namespace quipudb
