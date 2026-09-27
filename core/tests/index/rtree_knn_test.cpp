// Pruebas del k-NN del R-Tree (issue #121).
//
// El oraculo es el del criterio 4: ordenar TODOS los puntos por distancia y
// tomar los k primeros. Se compara por distancias y no por identidad porque
// con empates cual de los empatados entra es indistinto; lo que no puede
// pasar es que un punto de fuera este mas cerca que uno de dentro.

#include <gtest/gtest.h>

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <limits>
#include <random>
#include <string>
#include <vector>

#include "quipudb/error.hpp"
#include "quipudb/index/distance.hpp"
#include "quipudb/index/rtree.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

class RTreeKnnTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_rtree_knn_" + std::string(info->name()));
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

constexpr Point kLima{-77.0428, -12.0464};

std::vector<RTreeLeafEntry> alrededor_de_lima(std::size_t cuantos, unsigned semilla = 20'260'927) {
  std::mt19937 generador(semilla);
  std::uniform_real_distribution<double> lon(-77.3, -76.8);
  std::uniform_real_distribution<double> lat(-12.3, -11.8);

  std::vector<RTreeLeafEntry> puntos;
  puntos.reserve(cuantos);
  for (std::size_t i = 0; i < cuantos; ++i) {
    puntos.push_back({{lon(generador), lat(generador)}, rid_de(i)});
  }
  return puntos;
}

/// Las distancias de los k mas cercanos, ordenadas. El oraculo del criterio 4.
std::vector<double> k_mejores_distancias(const std::vector<RTreeLeafEntry>& todos, Point p,
                                         std::size_t k, Metric metrica) {
  std::vector<double> d;
  d.reserve(todos.size());
  for (const auto& e : todos) d.push_back(distance(p, e.point, metrica));
  std::sort(d.begin(), d.end());
  if (d.size() > k) d.resize(k);
  return d;
}

std::vector<double> distancias_de(const std::vector<RTreeLeafEntry>& entradas, Point p,
                                  Metric metrica) {
  std::vector<double> d;
  d.reserve(entradas.size());
  for (const auto& e : entradas) d.push_back(distance(p, e.point, metrica));
  return d;
}

// ---------------------------------------------------------------------------
// Criterio 1: punto, k y metrica; resultados ordenados
// ---------------------------------------------------------------------------

TEST_F(RTreeKnnTest, DevuelveKResultadosDeCercaALejos) {
  RTree arbol(path_, kDefaultPageSize, 4);
  // Puntos a distancias crecientes y conocidas, al norte de Lima.
  constexpr double kGradoLat = 111'195.0;
  for (std::size_t i = 1; i <= 10; ++i) {
    arbol.insert({kLima.x, kLima.y + (i * 1'000.0) / kGradoLat}, rid_de(i));
  }

  const auto cerca = arbol.k_nearest(kLima, 3, Metric::kHaversine);
  ASSERT_EQ(cerca.size(), 3u);
  EXPECT_EQ(cerca[0].rid, rid_de(1));
  EXPECT_EQ(cerca[1].rid, rid_de(2));
  EXPECT_EQ(cerca[2].rid, rid_de(3));

  const auto d = distancias_de(cerca, kLima, Metric::kHaversine);
  EXPECT_TRUE(std::is_sorted(d.begin(), d.end()));
}

TEST_F(RTreeKnnTest, SalenSiempreOrdenados) {
  RTree arbol(path_, kDefaultPageSize, 6);
  for (const auto& p : alrededor_de_lima(1'000)) arbol.insert(p.point, p.rid);

  for (const Metric metrica : {Metric::kEuclidean, Metric::kHaversine}) {
    for (const std::size_t k : {1u, 10u, 50u, 100u}) {
      const auto d = distancias_de(arbol.k_nearest(kLima, k, metrica), kLima, metrica);
      EXPECT_TRUE(std::is_sorted(d.begin(), d.end())) << name_of(metrica) << " k=" << k;
    }
  }
}

// ---------------------------------------------------------------------------
// Criterio 2: menos de k puntos
// ---------------------------------------------------------------------------

TEST_F(RTreeKnnTest, ConMenosDeKDevuelveTodosSinFallar) {
  RTree arbol(path_, kDefaultPageSize, 4);
  const auto puntos = alrededor_de_lima(7);
  for (const auto& p : puntos) arbol.insert(p.point, p.rid);

  EXPECT_EQ(arbol.k_nearest(kLima, 7, Metric::kHaversine).size(), 7u);
  EXPECT_EQ(arbol.k_nearest(kLima, 8, Metric::kHaversine).size(), 7u);
  EXPECT_EQ(arbol.k_nearest(kLima, 1'000, Metric::kHaversine).size(), 7u);
}

TEST_F(RTreeKnnTest, ArbolVacioYKCero) {
  RTree arbol(path_, kDefaultPageSize, 4);
  EXPECT_TRUE(arbol.k_nearest(kLima, 10, Metric::kHaversine).empty());

  arbol.insert(kLima, rid_de(0));
  EXPECT_TRUE(arbol.k_nearest(kLima, 0, Metric::kHaversine).empty());
}

// ---------------------------------------------------------------------------
// Criterio 3: OpStats
// ---------------------------------------------------------------------------

TEST_F(RTreeKnnTest, ReportaExaminadosYDevueltos) {
  RTree arbol(path_, kDefaultPageSize, 8);
  for (const auto& p : alrededor_de_lima(500)) arbol.insert(p.point, p.rid);

  arbol.reset_stats();
  const auto cerca = arbol.k_nearest(kLima, 20, Metric::kHaversine);
  const OpStats& s = arbol.stats();

  EXPECT_EQ(cerca.size(), 20u);
  EXPECT_EQ(s.records_returned, 20u);
  EXPECT_GT(s.pages_read, 0u);
  EXPECT_GE(s.records_examined, s.records_returned);
  EXPECT_EQ(s.pages_written, 0u);
}

// ---------------------------------------------------------------------------
// Criterio 4: coincide con ordenar todo y tomar los k primeros
// ---------------------------------------------------------------------------

TEST_F(RTreeKnnTest, CoincideConOrdenarTodo) {
  RTree arbol(path_, kDefaultPageSize, 6);
  const auto puntos = alrededor_de_lima(1'500);
  for (const auto& p : puntos) arbol.insert(p.point, p.rid);
  ASSERT_EQ(arbol.check_invariants(), "");

  const std::vector<Point> desde = {
      kLima, {-77.3, -12.3}, {-76.8, -11.8}, {-80.0, -12.0}, {-77.05, -12.05},
  };

  for (const Metric metrica : {Metric::kEuclidean, Metric::kHaversine}) {
    for (const Point p : desde) {
      for (const std::size_t k : {1u, 3u, 25u, 200u}) {
        const auto del_indice = distancias_de(arbol.k_nearest(p, k, metrica), p, metrica);
        const auto esperado = k_mejores_distancias(puntos, p, k, metrica);
        ASSERT_EQ(del_indice.size(), esperado.size())
            << name_of(metrica) << " (" << p.x << ", " << p.y << ") k=" << k;
        for (std::size_t i = 0; i < esperado.size(); ++i) {
          EXPECT_NEAR(del_indice[i], esperado[i], std::max(esperado[i], 1.0) * 1e-9)
              << name_of(metrica) << " k=" << k << " posicion " << i;
        }
      }
    }
  }
}

TEST_F(RTreeKnnTest, NingunPuntoDeFueraEstaMasCerca) {
  // La otra cara del criterio 4, dicha como propiedad: el peor del resultado
  // no puede ser peor que el mejor de los que quedaron fuera.
  RTree arbol(path_, kDefaultPageSize, 5);
  const auto puntos = alrededor_de_lima(800, 7);
  for (const auto& p : puntos) arbol.insert(p.point, p.rid);

  constexpr std::size_t kCuantos = 30;
  const auto cerca = arbol.k_nearest(kLima, kCuantos, Metric::kHaversine);
  ASSERT_EQ(cerca.size(), kCuantos);

  double peor_dentro = 0.0;
  for (const auto& e : cerca) {
    peor_dentro = std::max(peor_dentro, distance(kLima, e.point, Metric::kHaversine));
  }

  std::vector<double> todas;
  for (const auto& e : puntos) todas.push_back(distance(kLima, e.point, Metric::kHaversine));
  std::sort(todas.begin(), todas.end());
  // El primero que quedo fuera, con empates permitidos.
  EXPECT_GE(todas[kCuantos] * (1.0 + 1e-9), peor_dentro);
}

// ---------------------------------------------------------------------------
// Criterio 5: los k que mide el 2.2.4
// ---------------------------------------------------------------------------

TEST_F(RTreeKnnTest, KDeDiezCincuentaYCien) {
  RTree arbol(path_, kDefaultPageSize, 8);
  const auto puntos = alrededor_de_lima(3'000);
  for (const auto& p : puntos) arbol.insert(p.point, p.rid);

  for (const std::size_t k : {10u, 50u, 100u}) {
    const auto cerca = arbol.k_nearest(kLima, k, Metric::kHaversine);
    ASSERT_EQ(cerca.size(), k);

    const auto del_indice = distancias_de(cerca, kLima, Metric::kHaversine);
    const auto esperado = k_mejores_distancias(puntos, kLima, k, Metric::kHaversine);
    for (std::size_t i = 0; i < k; ++i) {
      EXPECT_NEAR(del_indice[i], esperado[i], std::max(esperado[i], 1.0) * 1e-9) << "k=" << k;
    }
  }
}

// ---------------------------------------------------------------------------
// Que de verdad use el indice, no un escaneo disfrazado
// ---------------------------------------------------------------------------

TEST_F(RTreeKnnTest, NoLeeElArbolEnteroParaUnKChico) {
  RTree arbol(path_, kDefaultPageSize, 8);
  for (const auto& p : alrededor_de_lima(3'000)) arbol.insert(p.point, p.rid);

  arbol.reset_stats();
  (void)arbol.scan();
  const std::uint64_t paginas_del_scan = arbol.stats().pages_read;

  arbol.reset_stats();
  (void)arbol.k_nearest(kLima, 10, Metric::kHaversine);
  const std::uint64_t paginas_del_knn = arbol.stats().pages_read;

  EXPECT_LT(paginas_del_knn, paginas_del_scan / 2)
      << "k=10 leyo " << paginas_del_knn << " paginas y el scan completo "
      << paginas_del_scan << ": el k-NN esta recorriendo el arbol entero";
}

TEST_F(RTreeKnnTest, UnKMayorNuncaLeeMenosPaginas) {
  RTree arbol(path_, kDefaultPageSize, 8);
  for (const auto& p : alrededor_de_lima(2'000)) arbol.insert(p.point, p.rid);

  std::uint64_t anterior = 0;
  for (const std::size_t k : {1u, 10u, 100u, 1'000u}) {
    arbol.reset_stats();
    (void)arbol.k_nearest(kLima, k, Metric::kHaversine);
    const std::uint64_t paginas = arbol.stats().pages_read;
    EXPECT_GE(paginas, anterior) << "k=" << k;
    anterior = paginas;
  }
}

// ---------------------------------------------------------------------------
// Bordes
// ---------------------------------------------------------------------------

TEST_F(RTreeKnnTest, PuntosRepetidosEnElMismoLugar) {
  RTree arbol(path_, kDefaultPageSize, 4);
  for (std::size_t i = 0; i < 12; ++i) arbol.insert(kLima, rid_de(i));
  arbol.insert({kLima.x + 1.0, kLima.y}, rid_de(99));

  const auto cerca = arbol.k_nearest(kLima, 12, Metric::kHaversine);
  ASSERT_EQ(cerca.size(), 12u);
  for (const auto& e : cerca) EXPECT_EQ(e.point, kLima);
}

TEST_F(RTreeKnnTest, LaConsultaCoincideConUnPuntoGuardado) {
  RTree arbol(path_, kDefaultPageSize, 4);
  const auto puntos = alrededor_de_lima(50);
  for (const auto& p : puntos) arbol.insert(p.point, p.rid);

  const Point exacto = puntos[17].point;
  const auto cerca = arbol.k_nearest(exacto, 1, Metric::kHaversine);
  ASSERT_EQ(cerca.size(), 1u);
  EXPECT_DOUBLE_EQ(distance(exacto, cerca[0].point, Metric::kHaversine), 0.0);
}

TEST_F(RTreeKnnTest, ConsultaInvalidaEsInvalidRecord) {
  RTree arbol(path_, kDefaultPageSize, 4);
  arbol.insert(kLima, rid_de(0));
  const double nan = std::numeric_limits<double>::quiet_NaN();

  EXPECT_THROW((void)arbol.k_nearest({nan, 0.0}, 5, Metric::kHaversine), InvalidRecord);
  EXPECT_THROW((void)arbol.k_nearest({0.0, 91.0}, 5, Metric::kHaversine), InvalidRecord);
}

TEST_F(RTreeKnnTest, CruzandoElAntimeridiano) {
  RTree arbol(path_, kDefaultPageSize, 4);
  const std::vector<Point> puntos = {
      {179.9, 0.0}, {-179.9, 0.0}, {179.0, 0.0}, {-179.0, 0.0}, {170.0, 0.0},
  };
  std::vector<RTreeLeafEntry> todos;
  for (std::size_t i = 0; i < puntos.size(); ++i) {
    arbol.insert(puntos[i], rid_de(i));
    todos.push_back({puntos[i], rid_de(i)});
  }

  const Point corte{180.0, 0.0};
  const auto del_indice = distancias_de(arbol.k_nearest(corte, 3, Metric::kHaversine), corte,
                                        Metric::kHaversine);
  const auto esperado = k_mejores_distancias(todos, corte, 3, Metric::kHaversine);
  ASSERT_EQ(del_indice.size(), esperado.size());
  for (std::size_t i = 0; i < esperado.size(); ++i) {
    EXPECT_NEAR(del_indice[i], esperado[i], std::max(esperado[i], 1.0) * 1e-9) << "posicion " << i;
  }
}

}  // namespace
}  // namespace quipudb
