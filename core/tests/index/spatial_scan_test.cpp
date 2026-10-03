// Pruebas de la busqueda espacial secuencial (issue #131).
//
// La busqueda secuencial es la linea base del 2.2.4, asi que tiene que
// devolver EXACTAMENTE lo mismo que el R-Tree: si no, la comparacion de
// tiempos mediria dos consultas distintas. Por eso el oraculo principal es el
// propio indice, y uno independiente -- medir todo y ordenar -- confirma que
// los dos tienen razon y no que se equivocan igual.

#include <gtest/gtest.h>

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <limits>
#include <random>
#include <string>
#include <vector>

#include "quipudb/catalog/database.hpp"
#include "quipudb/error.hpp"
#include "quipudb/index/distance.hpp"
#include "quipudb/index/rtree_index.hpp"
#include "quipudb/index/spatial_scan.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

constexpr GeoPoint kLima{-12.0464, -77.0428};

Schema lugares() {
  return Schema{
      .table_name = "lugares",
      .columns = {{"id", DataType::Int}, {"nombre", DataType::Varchar, 8},
                  {"ubicacion", DataType::Point}},
      .key_column = 0,
  };
}

class SpatialScanTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_spatial_scan_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
    db_ = std::make_unique<Database>(dir_ / "catalogo.txt");
    tabla_ = &db_->create_table(lugares(), kind::kHeap);

    // Puntos alrededor de Lima, concentrados como en el dataset del 2.2.4, y
    // un punto repetido para que haya empates de distancia.
    std::mt19937 generador(20'260'906);
    std::normal_distribution<double> dlat(0.0, 0.08);
    std::normal_distribution<double> dlon(0.0, 0.08);
    for (std::int32_t id = 1; id <= 1'500; ++id) {
      const GeoPoint p = id % 300 == 0 ? kLima
                                       : GeoPoint{kLima.latitude + dlat(generador),
                                                  kLima.longitude + dlon(generador)};
      tabla_->insert(Record{id, std::string("p"), p});
      puntos_.push_back(p);
    }
    indice_ = dynamic_cast<RTreeIndex*>(
        &db_->create_index("lugares", "por_ubicacion", "ubicacion", kind::kRTree));
    ASSERT_NE(indice_, nullptr);
  }
  void TearDown() override {
    db_.reset();
    fs::remove_all(dir_);
  }

  /// Distancias de una lista de RIDs al centro, leyendo cada registro.
  std::vector<double> distancias(const std::vector<RID>& rids, const GeoPoint& c, Metric m) {
    std::vector<double> d;
    for (const RID rid : rids) {
      const auto p = std::get<GeoPoint>((*tabla_->read(rid))[2]);
      d.push_back(distance({c.longitude, c.latitude}, {p.longitude, p.latitude}, m));
    }
    return d;
  }

  fs::path dir_;
  std::unique_ptr<Database> db_;
  TableFile* tabla_ = nullptr;
  RTreeIndex* indice_ = nullptr;
  std::vector<GeoPoint> puntos_;
};

std::vector<RID> ordenados(std::vector<RID> v) {
  std::sort(v.begin(), v.end());
  return v;
}

TEST_F(SpatialScanTest, RadioDevuelveLoMismoQueElRTree) {
  for (const double radio : {0.0, 1'000.0, 5'000.0, 10'000.0, 50'000.0}) {
    const auto secuencial = scan_radius(*tabla_, "ubicacion", kLima, radio, Metric::kHaversine);
    EXPECT_EQ(ordenados(secuencial),
              ordenados(indice_->search_radius(kLima, radio, Metric::kHaversine)))
        << "radio " << radio;
  }
  for (const double radio : {0.01, 0.05, 0.1}) {
    EXPECT_EQ(ordenados(scan_radius(*tabla_, "ubicacion", kLima, radio, Metric::kEuclidean)),
              ordenados(indice_->search_radius(kLima, radio, Metric::kEuclidean)))
        << "radio " << radio;
  }
}

TEST_F(SpatialScanTest, RadioCoincideConElOraculoIndependiente) {
  const auto rids = scan_radius(*tabla_, "ubicacion", kLima, 5'000.0, Metric::kHaversine);
  std::size_t esperados = 0;
  for (const auto& p : puntos_) {
    if (haversine({kLima.longitude, kLima.latitude}, {p.longitude, p.latitude}) <= 5'000.0) {
      ++esperados;
    }
  }
  EXPECT_EQ(rids.size(), esperados);
  EXPECT_GT(esperados, 5u);  // Lima repetido y vecinos: la prueba no es trivial.
  for (const double d : distancias(rids, kLima, Metric::kHaversine)) EXPECT_LE(d, 5'000.0);
}

TEST_F(SpatialScanTest, KnnDevuelveLasMismasDistanciasQueElRTreeYOrdenadas) {
  const GeoPoint centro{-12.1, -77.0};
  for (const Metric m : {Metric::kHaversine, Metric::kEuclidean}) {
    for (const std::size_t k : {1u, 10u, 50u, 100u}) {
      const auto secuencial = scan_k_nearest(*tabla_, "ubicacion", centro, k, m);
      ASSERT_EQ(secuencial.size(), k);
      const auto d_seq = distancias(secuencial, centro, m);
      // Con empates cual entra es indistinto: se comparan distancias, no RIDs.
      EXPECT_EQ(d_seq, distancias(indice_->k_nearest(centro, k, m), centro, m)) << "k " << k;
      EXPECT_TRUE(std::is_sorted(d_seq.begin(), d_seq.end())) << "k " << k;
    }
  }
}

TEST_F(SpatialScanTest, KnnConEmpatesNoDejaFueraUnoMasCercano) {
  // Cinco puntos exactamente en Lima: con k = 3 entran tres de ellos, a 0 m.
  const auto rids = scan_k_nearest(*tabla_, "ubicacion", kLima, 3, Metric::kHaversine);
  EXPECT_EQ(distancias(rids, kLima, Metric::kHaversine), (std::vector<double>{0.0, 0.0, 0.0}));
}

TEST_F(SpatialScanTest, KnnConMasKQueRegistrosDevuelveTodos) {
  EXPECT_EQ(scan_k_nearest(*tabla_, "ubicacion", kLima, 5'000, Metric::kHaversine).size(),
            1'500u);
}

TEST_F(SpatialScanTest, RecorreLaTablaEnteraSinImportarElRadio) {
  // Es lo que la distingue del indice: paga todas las paginas siempre.
  tabla_->reset_stats();
  (void)scan_radius(*tabla_, "ubicacion", kLima, 1.0, Metric::kHaversine);
  const auto con_radio_chico = tabla_->stats().pages_read;
  tabla_->reset_stats();
  (void)scan_k_nearest(*tabla_, "ubicacion", kLima, 1, Metric::kHaversine);
  EXPECT_GT(con_radio_chico, 1u);
  EXPECT_EQ(tabla_->stats().pages_read, con_radio_chico);
}

TEST_F(SpatialScanTest, RadioNegativoYKCeroNoLeenNada) {
  tabla_->reset_stats();
  EXPECT_TRUE(scan_radius(*tabla_, "ubicacion", kLima, -1.0, Metric::kHaversine).empty());
  EXPECT_TRUE(scan_k_nearest(*tabla_, "ubicacion", kLima, 0, Metric::kHaversine).empty());
  EXPECT_EQ(tabla_->stats().pages_read, 0u);
}

TEST_F(SpatialScanTest, RechazaColumnasQueNoSonPoint) {
  EXPECT_THROW((void)scan_radius(*tabla_, "nombre", kLima, 1.0, Metric::kHaversine),
               SchemaError);
  EXPECT_THROW((void)scan_k_nearest(*tabla_, "no_existe", kLima, 1, Metric::kHaversine),
               SchemaError);
}

TEST_F(SpatialScanTest, RechazaCentroORadioNoFinitos) {
  const double nan = std::numeric_limits<double>::quiet_NaN();
  EXPECT_THROW((void)scan_radius(*tabla_, "ubicacion", {nan, 0.0}, 1.0, Metric::kHaversine),
               InvalidRecord);
  EXPECT_THROW((void)scan_radius(*tabla_, "ubicacion", kLima, nan, Metric::kHaversine),
               InvalidRecord);
  EXPECT_THROW((void)scan_k_nearest(*tabla_, "ubicacion", {nan, 0.0}, 1, Metric::kEuclidean),
               InvalidRecord);
}

}  // namespace
}  // namespace quipudb
