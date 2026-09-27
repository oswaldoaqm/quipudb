// Pruebas de la interseccion con poligonos del R-Tree (issue #122).
//
// El oraculo del criterio 6 es el mismo de siempre: comprobar todos los
// puntos uno por uno. Si el indice y la fuerza bruta no coinciden, el indice
// esta mal.

#include <gtest/gtest.h>

#include <algorithm>
#include <cmath>
#include <filesystem>
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

class RTreePoligonoTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_rtree_poly_" + std::string(info->name()));
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

std::vector<RTreeLeafEntry> a_fuerza_bruta(const std::vector<RTreeLeafEntry>& todos,
                                           std::span<const Point> poligono) {
  std::vector<RTreeLeafEntry> out;
  for (const auto& e : todos) {
    if (contains_point(poligono, e.point)) out.push_back(e);
  }
  return out;
}

/// Un cuadrado de lado 1 con la esquina en el origen.
const std::vector<Point> kCuadrado = {{0.0, 0.0}, {1.0, 0.0}, {1.0, 1.0}, {0.0, 1.0}};

/// Una L: el cuadrado de 3x3 sin el cuadrante superior derecho. NO es convexo.
const std::vector<Point> kEle = {{0.0, 0.0}, {3.0, 0.0}, {3.0, 1.0},
                                 {1.0, 1.0}, {1.0, 3.0}, {0.0, 3.0}};

/// Una U: dos brazos y un hueco en medio que llega hasta arriba.
const std::vector<Point> kUve = {{0.0, 0.0}, {3.0, 0.0}, {3.0, 3.0}, {2.0, 3.0},
                                 {2.0, 1.0}, {1.0, 1.0}, {1.0, 3.0}, {0.0, 3.0}};

std::vector<RTreeLeafEntry> rejilla(double desde, double hasta, int pasos) {
  std::vector<RTreeLeafEntry> puntos;
  std::size_t i = 0;
  for (int a = 0; a <= pasos; ++a) {
    for (int b = 0; b <= pasos; ++b) {
      puntos.push_back({{desde + (hasta - desde) * a / pasos,
                         desde + (hasta - desde) * b / pasos},
                        rid_de(i++)});
    }
  }
  return puntos;
}

// ---------------------------------------------------------------------------
// contains_point, que es donde esta la logica
// ---------------------------------------------------------------------------

TEST(ContainsPointTest, CuadradoSimple) {
  EXPECT_TRUE(contains_point(kCuadrado, {0.5, 0.5}));
  EXPECT_TRUE(contains_point(kCuadrado, {0.01, 0.99}));
  EXPECT_FALSE(contains_point(kCuadrado, {1.5, 0.5}));
  EXPECT_FALSE(contains_point(kCuadrado, {0.5, -0.5}));
  EXPECT_FALSE(contains_point(kCuadrado, {-0.001, 0.5}));
}

TEST(ContainsPointTest, ElBordeEntra) {
  // Criterio 4: documentado y consistente. El borde cuenta como dentro.
  EXPECT_TRUE(contains_point(kCuadrado, {0.0, 0.5}));  // arista izquierda
  EXPECT_TRUE(contains_point(kCuadrado, {1.0, 0.5}));  // arista derecha
  EXPECT_TRUE(contains_point(kCuadrado, {0.5, 0.0}));  // arista inferior
  EXPECT_TRUE(contains_point(kCuadrado, {0.5, 1.0}));  // arista superior
  EXPECT_TRUE(contains_point(kCuadrado, {0.0, 0.0}));  // vertice
  EXPECT_TRUE(contains_point(kCuadrado, {1.0, 1.0}));  // vertice
}

TEST(ContainsPointTest, ElBordeDaLoMismoConLosVerticesAlReves) {
  // Consistente quiere decir esto: la respuesta no puede depender de como se
  // escribio el poligono.
  std::vector<Point> alreves(kCuadrado.rbegin(), kCuadrado.rend());
  const std::vector<Point> muestras = {{0.0, 0.5}, {0.5, 0.0}, {1.0, 1.0},
                                       {0.5, 0.5}, {1.5, 0.5}};
  for (const Point p : muestras) {
    EXPECT_EQ(contains_point(kCuadrado, p), contains_point(alreves, p))
        << "(" << p.x << ", " << p.y << ")";
  }
}

TEST(ContainsPointTest, PoligonoNoConvexoEnEle) {
  // Criterio 3. El hueco de la L tiene que quedar fuera.
  EXPECT_TRUE(contains_point(kEle, {0.5, 0.5}));   // brazo de abajo
  EXPECT_TRUE(contains_point(kEle, {2.5, 0.5}));   // brazo de abajo, a la derecha
  EXPECT_TRUE(contains_point(kEle, {0.5, 2.5}));   // brazo de arriba
  EXPECT_FALSE(contains_point(kEle, {2.0, 2.0}));  // el hueco
  EXPECT_FALSE(contains_point(kEle, {2.5, 2.5}));  // el hueco
}

TEST(ContainsPointTest, PoligonoNoConvexoEnUve) {
  // El hueco de la U se abre por arriba: un rayo desde ahi cruza el borde un
  // numero par de veces.
  EXPECT_TRUE(contains_point(kUve, {0.5, 2.0}));   // brazo izquierdo
  EXPECT_TRUE(contains_point(kUve, {2.5, 2.0}));   // brazo derecho
  EXPECT_TRUE(contains_point(kUve, {1.5, 0.5}));   // la base
  EXPECT_FALSE(contains_point(kUve, {1.5, 2.0}));  // el hueco
  EXPECT_FALSE(contains_point(kUve, {1.5, 2.9}));  // el hueco, casi arriba
}

TEST(ContainsPointTest, UnVerticeALaAlturaDelRayoNoSeCuentaDosVeces) {
  // El caso clasico que rompe una implementacion ingenua: un punto a la
  // misma altura que un vertice. Si ese vertice contara dos cruces, la
  // paridad se invertiria y el punto saldria fuera.
  const std::vector<Point> diamante = {{0.0, 1.0}, {1.0, 0.0}, {2.0, 1.0}, {1.0, 2.0}};
  EXPECT_TRUE(contains_point(diamante, {1.0, 1.0}));   // centro, a la altura de dos vertices
  EXPECT_FALSE(contains_point(diamante, {2.5, 1.0}));  // fuera, misma altura
  EXPECT_FALSE(contains_point(diamante, {-0.5, 1.0}));
}

TEST(ContainsPointTest, PoligonoDegenerado) {
  // Tres vertices en una recta no encierran nada: solo la propia recta.
  const std::vector<Point> recta = {{0.0, 0.0}, {1.0, 0.0}, {2.0, 0.0}};
  EXPECT_TRUE(contains_point(recta, {1.0, 0.0}));
  EXPECT_TRUE(contains_point(recta, {0.5, 0.0}));
  EXPECT_FALSE(contains_point(recta, {1.0, 0.5}));
  EXPECT_FALSE(contains_point(recta, {3.0, 0.0}));
}

TEST(ContainsPointTest, EntradaInvalida) {
  const std::vector<Point> dos = {{0.0, 0.0}, {1.0, 1.0}};
  EXPECT_THROW((void)contains_point(dos, {0.5, 0.5}), InvalidRecord);

  const double nan = std::numeric_limits<double>::quiet_NaN();
  const std::vector<Point> roto = {{0.0, 0.0}, {nan, 0.0}, {1.0, 1.0}};
  EXPECT_THROW((void)contains_point(roto, {0.5, 0.5}), InvalidRecord);
  EXPECT_THROW((void)contains_point(kCuadrado, {nan, 0.5}), InvalidRecord);
}

TEST(ContainsPointTest, FuncionaEnEscalaDeCoordenadasGeograficas) {
  // La tolerancia del borde es relativa, asi que tiene que valer igual con
  // numeros de otra magnitud.
  const std::vector<Point> lima = {
      {-77.2, -12.3}, {-76.8, -12.3}, {-76.8, -11.8}, {-77.2, -11.8}};
  EXPECT_TRUE(contains_point(lima, {-77.0428, -12.0464}));
  EXPECT_TRUE(contains_point(lima, {-77.2, -12.0}));  // borde
  EXPECT_FALSE(contains_point(lima, {-76.0, -12.0}));
}

TEST(BoundingBoxOfTest, EnvuelveTodosLosVertices) {
  const Rect caja = bounding_box_of(kEle);
  EXPECT_DOUBLE_EQ(caja.min_x, 0.0);
  EXPECT_DOUBLE_EQ(caja.min_y, 0.0);
  EXPECT_DOUBLE_EQ(caja.max_x, 3.0);
  EXPECT_DOUBLE_EQ(caja.max_y, 3.0);
  for (const Point v : kEle) EXPECT_TRUE(caja.contains(v));
}

// ---------------------------------------------------------------------------
// La busqueda sobre el arbol
// ---------------------------------------------------------------------------

TEST_F(RTreePoligonoTest, DevuelveLosInteriores) {
  RTree arbol(path_, kDefaultPageSize, 4);
  const auto puntos = rejilla(0.0, 3.0, 12);
  for (const auto& p : puntos) arbol.insert(p.point, p.rid);

  EXPECT_EQ(claves(arbol.search_polygon(kEle)), claves(a_fuerza_bruta(puntos, kEle)));
}

TEST_F(RTreePoligonoTest, DescartaLoQueEstaEnLaCajaPeroNoEnElPoligono) {
  // El corazon del criterio 2 y de la Nota del issue: la caja del poligono
  // deja pasar el hueco de la L, y el segundo filtro tiene que tirarlo.
  RTree arbol(path_, kDefaultPageSize, 4);
  arbol.insert({0.5, 0.5}, rid_de(0));  // dentro
  arbol.insert({2.5, 2.5}, rid_de(1));  // en la caja, en el hueco

  const Rect caja = bounding_box_of(kEle);
  EXPECT_EQ(arbol.search(caja).size(), 2u) << "la caja deberia dejar pasar los dos";

  const auto dentro = arbol.search_polygon(kEle);
  ASSERT_EQ(dentro.size(), 1u);
  EXPECT_EQ(dentro[0].rid, rid_de(0));
}

TEST_F(RTreePoligonoTest, CoincideConComprobarUnoPorUno) {
  RTree arbol(path_, kDefaultPageSize, 6);
  std::mt19937 generador(20'260'927);
  std::uniform_real_distribution<double> coord(-0.5, 3.5);

  std::vector<RTreeLeafEntry> puntos;
  for (std::size_t i = 0; i < 2'000; ++i) {
    puntos.push_back({{coord(generador), coord(generador)}, rid_de(i)});
  }
  for (const auto& p : puntos) arbol.insert(p.point, p.rid);
  ASSERT_EQ(arbol.check_invariants(), "");

  const std::vector<std::pair<const char*, std::vector<Point>>> formas = {
      {"cuadrado", {{0.0, 0.0}, {3.0, 0.0}, {3.0, 3.0}, {0.0, 3.0}}},
      {"ele", kEle},
      {"uve", kUve},
      {"triangulo", {{0.0, 0.0}, {3.0, 0.0}, {1.5, 3.0}}},
      {"estrella",
       {{1.5, 3.0}, {1.9, 1.9}, {3.0, 1.5}, {1.9, 1.1}, {1.5, 0.0}, {1.1, 1.1}, {0.0, 1.5},
        {1.1, 1.9}}},
      {"fuera de todo", {{10.0, 10.0}, {11.0, 10.0}, {11.0, 11.0}, {10.0, 11.0}}},
  };

  for (const auto& [nombre, forma] : formas) {
    EXPECT_EQ(claves(arbol.search_polygon(forma)), claves(a_fuerza_bruta(puntos, forma)))
        << nombre;
  }
}

TEST_F(RTreePoligonoTest, LaPodaEvitaLeerElArbolEntero) {
  RTree arbol(path_, kDefaultPageSize, 8);
  for (const auto& p : rejilla(0.0, 100.0, 50)) arbol.insert(p.point, p.rid);

  arbol.reset_stats();
  (void)arbol.scan();
  const std::uint64_t paginas_del_scan = arbol.stats().pages_read;

  // Un poligono chiquito en una esquina.
  const std::vector<Point> esquina = {{0.0, 0.0}, {5.0, 0.0}, {5.0, 5.0}, {0.0, 5.0}};
  arbol.reset_stats();
  const auto dentro = arbol.search_polygon(esquina);
  const std::uint64_t paginas_del_poligono = arbol.stats().pages_read;

  EXPECT_GT(dentro.size(), 0u);
  EXPECT_LT(paginas_del_poligono, paginas_del_scan)
      << "leyo " << paginas_del_poligono << " paginas y el scan " << paginas_del_scan;
}

TEST_F(RTreePoligonoTest, ReportaExaminadosYDevueltos) {
  RTree arbol(path_, kDefaultPageSize, 8);
  for (const auto& p : rejilla(0.0, 3.0, 30)) arbol.insert(p.point, p.rid);

  arbol.reset_stats();
  const auto dentro = arbol.search_polygon(kEle);
  const OpStats& s = arbol.stats();

  EXPECT_EQ(s.records_returned, dentro.size());
  EXPECT_GT(s.pages_read, 0u);
  // El hueco de la L asegura que se examinaron mas de los devueltos.
  EXPECT_GT(s.records_examined, s.records_returned);
  EXPECT_EQ(s.pages_written, 0u);
}

TEST_F(RTreePoligonoTest, ArbolVacioYPoligonoInvalido) {
  RTree arbol(path_, kDefaultPageSize, 4);
  EXPECT_TRUE(arbol.search_polygon(kCuadrado).empty());

  arbol.insert({0.5, 0.5}, rid_de(0));
  const std::vector<Point> dos = {{0.0, 0.0}, {1.0, 1.0}};
  EXPECT_THROW((void)arbol.search_polygon(dos), InvalidRecord);
}

TEST_F(RTreePoligonoTest, PuntosJustoSobreElBordeEntranDesdeElArbol) {
  RTree arbol(path_, kDefaultPageSize, 4);
  const std::vector<Point> sobre_el_borde = {
      {0.0, 0.0}, {1.0, 0.0}, {1.0, 1.0}, {0.0, 1.0}, {0.5, 0.0}, {0.0, 0.5},
  };
  std::vector<RTreeLeafEntry> todos;
  for (std::size_t i = 0; i < sobre_el_borde.size(); ++i) {
    arbol.insert(sobre_el_borde[i], rid_de(i));
    todos.push_back({sobre_el_borde[i], rid_de(i)});
  }
  arbol.insert({2.0, 2.0}, rid_de(99));  // lejos

  const auto dentro = arbol.search_polygon(kCuadrado);
  EXPECT_EQ(dentro.size(), sobre_el_borde.size())
      << "todos los del borde tienen que entrar";
  EXPECT_EQ(claves(dentro), claves(a_fuerza_bruta(todos, kCuadrado)));
}

}  // namespace
}  // namespace quipudb
