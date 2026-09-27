// Pruebas de la busqueda por radio del R-Tree (issue #120).
//
// El oraculo de casi todas es el mismo: recorrer los puntos uno por uno y
// medirlos contra el radio. Si el indice y la fuerza bruta no coinciden, el
// indice esta mal, porque la fuerza bruta no puede equivocarse (criterio 4).

#include <gtest/gtest.h>

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <random>
#include <set>
#include <string>
#include <tuple>
#include <vector>

#include "quipudb/error.hpp"
#include "quipudb/index/distance.hpp"
#include "quipudb/index/rtree.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

class RTreeRadioTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_rtree_radio_" + std::string(info->name()));
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

/// El oraculo: mide todos los puntos, sin indice ni poda.
std::vector<RTreeLeafEntry> a_fuerza_bruta(const std::vector<RTreeLeafEntry>& todos, Point centro,
                                           double radio, Metric metrica) {
  std::vector<RTreeLeafEntry> out;
  for (const auto& e : todos) {
    if (distance(centro, e.point, metrica) <= radio) out.push_back(e);
  }
  return out;
}

constexpr Point kLima{-77.0428, -12.0464};

/// Puntos repartidos alrededor de Lima, deterministas.
std::vector<RTreeLeafEntry> alrededor_de_lima(std::size_t cuantos) {
  std::mt19937 generador(20'260'927);
  std::uniform_real_distribution<double> lon(-77.3, -76.8);
  std::uniform_real_distribution<double> lat(-12.3, -11.8);

  std::vector<RTreeLeafEntry> puntos;
  puntos.reserve(cuantos);
  for (std::size_t i = 0; i < cuantos; ++i) {
    puntos.push_back({{lon(generador), lat(generador)}, rid_de(i)});
  }
  return puntos;
}

// ---------------------------------------------------------------------------
// Criterio 1: recibe centro, radio y metrica
// ---------------------------------------------------------------------------

TEST_F(RTreeRadioTest, DevuelveLoQueEstaDentroYNadaMas) {
  RTree arbol(path_, kDefaultPageSize, 4);
  // Cuatro puntos a distancias conocidas al norte de Lima, sobre el mismo
  // meridiano: un grado de latitud son ~111,2 km en cualquier parte.
  constexpr double kGradoLat = 111'195.0;
  const std::vector<double> metros = {500.0, 2'000.0, 7'000.0, 20'000.0};
  for (std::size_t i = 0; i < metros.size(); ++i) {
    arbol.insert({kLima.x, kLima.y + metros[i] / kGradoLat}, rid_de(i));
  }

  EXPECT_EQ(arbol.search_radius(kLima, 1'000.0, Metric::kHaversine).size(), 1u);
  EXPECT_EQ(arbol.search_radius(kLima, 5'000.0, Metric::kHaversine).size(), 2u);
  EXPECT_EQ(arbol.search_radius(kLima, 10'000.0, Metric::kHaversine).size(), 3u);
  EXPECT_EQ(arbol.search_radius(kLima, 30'000.0, Metric::kHaversine).size(), 4u);
}

TEST_F(RTreeRadioTest, LaMetricaCambiaElResultado) {
  RTree arbol(path_, kDefaultPageSize, 8);
  const auto puntos = alrededor_de_lima(300);
  for (const auto& p : puntos) arbol.insert(p.point, p.rid);

  // El mismo numero con las dos metricas significa cosas distintas: 5000
  // metros contra 5000 grados. La euclidiana con ese radio se lleva todo.
  const auto geodesica = arbol.search_radius(kLima, 5'000.0, Metric::kHaversine);
  const auto plana = arbol.search_radius(kLima, 5'000.0, Metric::kEuclidean);

  EXPECT_LT(geodesica.size(), puntos.size());
  EXPECT_EQ(plana.size(), puntos.size());
}

// ---------------------------------------------------------------------------
// Criterio 2: poda por rectangulo, y despues el radio de verdad
// ---------------------------------------------------------------------------

TEST_F(RTreeRadioTest, DescartaLasEsquinasDelRectangulo) {
  RTree arbol(path_, kDefaultPageSize, 4);

  // Dos puntos a la misma distancia en grados del centro: uno al norte y
  // otro en diagonal. El rectangulo que circunscribe el circulo contiene a
  // los dos, pero el de la diagonal esta a sqrt(2) veces la distancia y
  // queda fuera del circulo. Es el caso que la Nota del issue describe.
  constexpr double kDelta = 0.05;
  arbol.insert({kLima.x, kLima.y + kDelta}, rid_de(0));           // al norte
  arbol.insert({kLima.x + kDelta, kLima.y + kDelta}, rid_de(1));  // la esquina

  const Rect caja = bounding_box(kLima, kDelta * 1.001, Metric::kEuclidean);
  // `Point{...}` explicito: con las llaves solas, `contains` es ambiguo entre
  // la version de punto y la de rectangulo.
  EXPECT_TRUE(caja.contains(Point{kLima.x, kLima.y + kDelta}));
  EXPECT_TRUE(caja.contains(Point{kLima.x + kDelta, kLima.y + kDelta}));
  EXPECT_EQ(arbol.search(caja).size(), 2u);

  const auto dentro = arbol.search_radius(kLima, kDelta * 1.001, Metric::kEuclidean);
  ASSERT_EQ(dentro.size(), 1u);
  EXPECT_EQ(dentro[0].rid, rid_de(0));
}

TEST_F(RTreeRadioTest, LaPodaEvitaLeerElArbolEntero) {
  RTree arbol(path_, kDefaultPageSize, 8);
  for (const auto& p : alrededor_de_lima(2'000)) arbol.insert(p.point, p.rid);

  arbol.reset_stats();
  const auto todo = arbol.scan();
  const std::uint64_t paginas_del_scan = arbol.stats().pages_read;

  arbol.reset_stats();
  const auto cerca = arbol.search_radius(kLima, 1'000.0, Metric::kHaversine);
  const std::uint64_t paginas_del_radio = arbol.stats().pages_read;

  EXPECT_LT(cerca.size(), todo.size());
  EXPECT_LT(paginas_del_radio, paginas_del_scan)
      << "un radio chico leyo " << paginas_del_radio << " paginas y el scan "
      << paginas_del_scan << ": el indice no esta podando";
}

// ---------------------------------------------------------------------------
// Criterio 3: OpStats
// ---------------------------------------------------------------------------

TEST_F(RTreeRadioTest, ReportaExaminadosYDevueltos) {
  RTree arbol(path_, kDefaultPageSize, 8);
  for (const auto& p : alrededor_de_lima(500)) arbol.insert(p.point, p.rid);

  arbol.reset_stats();
  const auto dentro = arbol.search_radius(kLima, 3'000.0, Metric::kHaversine);
  const OpStats& s = arbol.stats();

  EXPECT_EQ(s.records_returned, dentro.size());
  EXPECT_GT(s.pages_read, 0u);
  // Se miraron al menos los que se devolvieron, y en general mas: los que el
  // rectangulo dejo pasar y el radio descarto.
  EXPECT_GE(s.records_examined, s.records_returned);
  EXPECT_EQ(s.pages_written, 0u);
}

// ---------------------------------------------------------------------------
// Criterio 4: coincide con la busqueda secuencial
// ---------------------------------------------------------------------------

TEST_F(RTreeRadioTest, CoincideConLaFuerzaBrutaEnMuchosRadios) {
  RTree arbol(path_, kDefaultPageSize, 6);
  const auto puntos = alrededor_de_lima(1'500);
  for (const auto& p : puntos) arbol.insert(p.point, p.rid);
  ASSERT_EQ(arbol.check_invariants(), "");

  // Centros dentro y fuera de la nube, para cubrir los dos lados.
  const std::vector<Point> centros = {
      kLima, {-77.3, -12.3}, {-76.8, -11.8}, {-77.05, -12.05}, {-76.0, -12.0},
  };
  const std::vector<double> radios = {0.0,      100.0,    1'000.0,  5'000.0,
                                      10'000.0, 30'000.0, 200'000.0};

  for (const Point centro : centros) {
    for (const double radio : radios) {
      const auto del_indice = arbol.search_radius(centro, radio, Metric::kHaversine);
      const auto esperado = a_fuerza_bruta(puntos, centro, radio, Metric::kHaversine);
      EXPECT_EQ(claves(del_indice), claves(esperado))
          << "centro (" << centro.x << ", " << centro.y << ") radio " << radio;
    }
  }
}

TEST_F(RTreeRadioTest, CoincideConLaFuerzaBrutaTambienConLaEuclidiana) {
  RTree arbol(path_, kDefaultPageSize, 6);
  const auto puntos = alrededor_de_lima(800);
  for (const auto& p : puntos) arbol.insert(p.point, p.rid);

  for (const double radio : {0.0, 0.01, 0.05, 0.2, 1.0}) {
    const auto del_indice = arbol.search_radius(kLima, radio, Metric::kEuclidean);
    const auto esperado = a_fuerza_bruta(puntos, kLima, radio, Metric::kEuclidean);
    EXPECT_EQ(claves(del_indice), claves(esperado)) << "radio " << radio;
  }
}

// ---------------------------------------------------------------------------
// Criterio 5: los radios que mide el 2.2.4
// ---------------------------------------------------------------------------

TEST_F(RTreeRadioTest, RadiosDeUnoCincoYDiezKilometros) {
  RTree arbol(path_, kDefaultPageSize, 8);
  const auto puntos = alrededor_de_lima(3'000);
  for (const auto& p : puntos) arbol.insert(p.point, p.rid);

  std::size_t anterior = 0;
  for (const double km : {1.0, 5.0, 10.0}) {
    const double radio = km * 1'000.0;
    const auto del_indice = arbol.search_radius(kLima, radio, Metric::kHaversine);
    const auto esperado = a_fuerza_bruta(puntos, kLima, radio, Metric::kHaversine);

    EXPECT_EQ(claves(del_indice), claves(esperado)) << km << " km";
    EXPECT_GT(del_indice.size(), 0u) << km << " km: el caso de prueba no mide nada";
    // Un radio mas grande no puede devolver menos.
    EXPECT_GE(del_indice.size(), anterior) << km << " km";
    anterior = del_indice.size();

    // Ninguno se cuela por encima del radio pedido.
    for (const auto& e : del_indice) {
      EXPECT_LE(distance(kLima, e.point, Metric::kHaversine), radio) << km << " km";
    }
  }
}

// ---------------------------------------------------------------------------
// Bordes
// ---------------------------------------------------------------------------

TEST_F(RTreeRadioTest, ElBordeEntra) {
  RTree arbol(path_, kDefaultPageSize, 4);
  const Point vecino{kLima.x, kLima.y + 0.01};
  arbol.insert(vecino, rid_de(0));

  const double exacto = distance(kLima, vecino, Metric::kHaversine);
  EXPECT_EQ(arbol.search_radius(kLima, exacto, Metric::kHaversine).size(), 1u)
      << "un punto a exactamente el radio tiene que entrar";
  EXPECT_EQ(arbol.search_radius(kLima, std::nextafter(exacto, 0.0), Metric::kHaversine).size(), 0u);
}

TEST_F(RTreeRadioTest, RadioCeroYRadioNegativo) {
  RTree arbol(path_, kDefaultPageSize, 4);
  arbol.insert(kLima, rid_de(0));
  arbol.insert(kLima, rid_de(1));  // dos registros en el mismo lugar
  arbol.insert({kLima.x + 0.1, kLima.y}, rid_de(2));

  EXPECT_EQ(arbol.search_radius(kLima, 0.0, Metric::kHaversine).size(), 2u);
  EXPECT_TRUE(arbol.search_radius(kLima, -1.0, Metric::kHaversine).empty());
}

TEST_F(RTreeRadioTest, ArbolVacio) {
  RTree arbol(path_, kDefaultPageSize, 4);
  EXPECT_TRUE(arbol.search_radius(kLima, 5'000.0, Metric::kHaversine).empty());
}

TEST_F(RTreeRadioTest, CentroInvalidoEsInvalidRecord) {
  RTree arbol(path_, kDefaultPageSize, 4);
  arbol.insert(kLima, rid_de(0));
  const double nan = std::numeric_limits<double>::quiet_NaN();

  EXPECT_THROW((void)arbol.search_radius({nan, 0.0}, 100.0, Metric::kHaversine), InvalidRecord);
  EXPECT_THROW((void)arbol.search_radius(kLima, nan, Metric::kHaversine), InvalidRecord);
  EXPECT_THROW((void)arbol.search_radius({0.0, 91.0}, 100.0, Metric::kHaversine), InvalidRecord);
}

TEST_F(RTreeRadioTest, CercaDelPoloYCruzandoElAntimeridiano) {
  RTree arbol(path_, kDefaultPageSize, 4);
  const std::vector<Point> puntos = {
      {179.95, 0.0}, {-179.95, 0.0}, {179.0, 0.0},   // a los lados del corte
      {0.0, 89.9},   {180.0, 89.9},  {90.0, 89.95},  // alrededor del polo
  };
  std::vector<RTreeLeafEntry> todos;
  for (std::size_t i = 0; i < puntos.size(); ++i) {
    arbol.insert(puntos[i], rid_de(i));
    todos.push_back({puntos[i], rid_de(i)});
  }

  // Un circulo de 20 km centrado en el antimeridiano tiene que agarrar los
  // dos puntos que lo rodean, aunque sus longitudes esten en extremos
  // opuestos del rango.
  const Point corte{180.0, 0.0};
  EXPECT_EQ(claves(arbol.search_radius(corte, 20'000.0, Metric::kHaversine)),
            claves(a_fuerza_bruta(todos, corte, 20'000.0, Metric::kHaversine)));

  // Y uno centrado en el polo, donde todas las longitudes convergen.
  const Point polo{0.0, 90.0};
  for (const double radio : {5'000.0, 20'000.0, 100'000.0}) {
    EXPECT_EQ(claves(arbol.search_radius(polo, radio, Metric::kHaversine)),
              claves(a_fuerza_bruta(todos, polo, radio, Metric::kHaversine)))
        << "radio " << radio;
  }
}

TEST_F(RTreeRadioTest, UnRadioEnormeDevuelveTodo) {
  RTree arbol(path_, kDefaultPageSize, 4);
  const auto puntos = alrededor_de_lima(200);
  for (const auto& p : puntos) arbol.insert(p.point, p.rid);

  // Media vuelta al planeta: no puede quedar nada afuera.
  const auto todo = arbol.search_radius(kLima, 25'000'000.0, Metric::kHaversine);
  EXPECT_EQ(todo.size(), puntos.size());
}

}  // namespace
}  // namespace quipudb
