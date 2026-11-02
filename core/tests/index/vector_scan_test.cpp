// Pruebas de la busqueda k-NN exacta sobre una columna VECTOR (issue #182).

#include "quipudb/index/vector_scan.hpp"

#include <gtest/gtest.h>

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <memory>
#include <numeric>
#include <random>
#include <string>
#include <vector>

#include "azar_portable.hpp"
#include "quipudb/catalog/database.hpp"
#include "quipudb/error.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

constexpr VectorMetric kTodas[] = {VectorMetric::kEuclidean, VectorMetric::kDotProduct,
                                   VectorMetric::kCosine};

Schema imagenes(std::uint16_t dim) {
  return Schema{
      .table_name = "imagenes",
      .columns = {{"id", DataType::Int}, {"nombre", DataType::Varchar, 8},
                  {"vec", DataType::Vector, dim}},
      .key_column = 0,
  };
}

class VectorScanTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_vector_scan_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
    db_ = std::make_unique<Database>(dir_ / "catalogo.txt");
  }
  void TearDown() override {
    db_.reset();
    fs::remove_all(dir_);
  }

  /// Una tabla con `n` vectores al azar de dimension `dim`, guardados tambien
  /// en `vectores_` en el orden de su RID.
  TableFile& tabla_al_azar(std::size_t n, std::uint16_t dim, unsigned semilla) {
    TableFile& t = db_->create_table(imagenes(dim), kind::kHeap);
    std::mt19937 gen(semilla);
    const prueba::RealUniforme u(-1.0, 1.0);
    for (std::size_t i = 0; i < n; ++i) {
      VectorValue v(dim);
      for (auto& x : v) x = static_cast<float>(u(gen));
      const RID rid = t.insert(Record{static_cast<std::int32_t>(i + 1), std::string("img"), v});
      vectores_.emplace_back(rid, v);
    }
    return t;
  }

  /// La respuesta calculada aparte: distancia a todos, ordenar, cortar.
  std::vector<VectorMatch> fuerza_bruta(const VectorValue& q, std::size_t k, VectorMetric m) {
    std::vector<VectorMatch> todos;
    for (const auto& [rid, v] : vectores_) todos.push_back({rid, vector_distance(q, v, m)});
    std::sort(todos.begin(), todos.end(), [](const VectorMatch& a, const VectorMatch& b) {
      return a.distance != b.distance ? a.distance < b.distance : a.rid < b.rid;
    });
    todos.resize(std::min(k, todos.size()));
    return todos;
  }

  fs::path dir_;
  std::unique_ptr<Database> db_;
  std::vector<std::pair<RID, VectorValue>> vectores_;
};

// ---------------------------------------------------------------------------
// Resultados conocidos
// ---------------------------------------------------------------------------

TEST_F(VectorScanTest, VectoresPequenosConResultadoConocido) {
  TableFile& t = db_->create_table(imagenes(2), kind::kHeap);
  const RID a = t.insert(Record{1, std::string("a"), VectorValue{1, 0}});
  const RID b = t.insert(Record{2, std::string("b"), VectorValue{0, 1}});
  const RID c = t.insert(Record{3, std::string("c"), VectorValue{3, 0}});
  const RID d = t.insert(Record{4, std::string("d"), VectorValue{-1, 0}});
  const VectorValue q{2, 0};

  // Euclidiana: c a 1, a a 1 (empate: va primero el RID menor), b a sqrt(5), d a 3.
  auto r = scan_k_nearest_vector(t, "vec", q, 4, VectorMetric::kEuclidean);
  ASSERT_EQ(r.size(), 4u);
  EXPECT_EQ(r[0].rid, a);
  EXPECT_EQ(r[1].rid, c);
  EXPECT_EQ(r[2].rid, b);
  EXPECT_EQ(r[3].rid, d);
  EXPECT_DOUBLE_EQ(r[0].distance, 1.0);
  EXPECT_DOUBLE_EQ(r[2].distance, std::sqrt(5.0));

  // Producto punto: c (6) le gana a a (2), aunque a esta igual de cerca. Es la
  // diferencia que documenta vector_metric.hpp.
  r = scan_k_nearest_vector(t, "vec", q, 2, VectorMetric::kDotProduct);
  EXPECT_EQ(r[0].rid, c);
  EXPECT_EQ(r[1].rid, a);
  EXPECT_DOUBLE_EQ(from_distance(r[0].distance, VectorMetric::kDotProduct), 6.0);

  // Coseno: a y c tienen la misma direccion que q (coseno 1, empate por RID).
  r = scan_k_nearest_vector(t, "vec", q, 3, VectorMetric::kCosine);
  EXPECT_EQ(r[0].rid, a);
  EXPECT_EQ(r[1].rid, c);
  EXPECT_EQ(r[2].rid, b);  // ortogonal: coseno 0, antes que d (coseno -1)
}

TEST_F(VectorScanTest, CoincideConLaFuerzaBrutaEnLasTresMetricas) {
  TableFile& t = tabla_al_azar(1'000, 16, 182);
  std::mt19937 gen(7);
  const prueba::RealUniforme u(-1.0, 1.0);
  for (int consulta = 0; consulta < 20; ++consulta) {
    VectorValue q(16);
    for (auto& x : q) x = static_cast<float>(u(gen));
    for (const VectorMetric m : kTodas) {
      for (const std::size_t k : {1u, 10u, 100u}) {
        EXPECT_EQ(scan_k_nearest_vector(t, "vec", q, k, m), fuerza_bruta(q, k, m))
            << name_of(m) << " k=" << k;
      }
    }
  }
}

TEST_F(VectorScanTest, SaleOrdenadoDeMasCercaAMasLejos) {
  TableFile& t = tabla_al_azar(500, 8, 3);
  const VectorValue q(8, 0.5F);
  for (const VectorMetric m : kTodas) {
    const auto r = scan_k_nearest_vector(t, "vec", q, 50, m);
    EXPECT_TRUE(std::is_sorted(r.begin(), r.end(), [](const auto& a, const auto& b) {
      return a.distance < b.distance;
    })) << name_of(m);
  }
}

TEST_F(VectorScanTest, ConMenosDeKVectoresDevuelveTodos) {
  TableFile& t = tabla_al_azar(7, 4, 1);
  const auto r = scan_k_nearest_vector(t, "vec", VectorValue{1, 2, 3, 4}, 10,
                                       VectorMetric::kEuclidean);
  EXPECT_EQ(r.size(), 7u);
}

TEST_F(VectorScanTest, UnaTablaVaciaDevuelveVacio) {
  TableFile& t = db_->create_table(imagenes(3), kind::kHeap);
  EXPECT_TRUE(scan_k_nearest_vector(t, "vec", VectorValue{1, 2, 3}, 5, VectorMetric::kCosine)
                  .empty());
}

TEST_F(VectorScanTest, UnVectorEnCerosNoRompeLaBusquedaPorCoseno) {
  TableFile& t = db_->create_table(imagenes(2), kind::kHeap);
  const RID cero = t.insert(Record{1, std::string("vacio"), VectorValue{0, 0}});
  const RID bueno = t.insert(Record{2, std::string("bueno"), VectorValue{1, 1}});
  const auto r = scan_k_nearest_vector(t, "vec", VectorValue{1, 1}, 2, VectorMetric::kCosine);
  ASSERT_EQ(r.size(), 2u);
  EXPECT_EQ(r[0].rid, bueno);
  EXPECT_EQ(r[1].rid, cero);
  EXPECT_DOUBLE_EQ(r[1].distance, 1.0);  // coseno 0, ver vector_metric.hpp
}

// ---------------------------------------------------------------------------
// Costo
// ---------------------------------------------------------------------------

TEST_F(VectorScanTest, ExaminaTodaLaTablaUnaVezPorConsulta) {
  TableFile& t = tabla_al_azar(2'000, 32, 11);
  t.reset_stats();
  (void)scan_k_nearest_vector(t, "vec", VectorValue(32, 0.1F), 10, VectorMetric::kEuclidean);
  // La busqueda exacta no se salta nada: es la linea base O(N). El cursor del
  // Heap cuenta como examinados tambien los slots libres de la ultima pagina,
  // asi que lo exacto es lo que entrega: los 2 000 vectores, cada uno una vez.
  EXPECT_EQ(t.stats().records_returned, 2'000u);
  EXPECT_GE(t.stats().records_examined, 2'000u);
  EXPECT_GT(t.stats().pages_read, 0u);
  EXPECT_EQ(t.stats().pages_written, 0u);
}

TEST_F(VectorScanTest, KCeroNoLeeLaTabla) {
  TableFile& t = tabla_al_azar(100, 4, 2);
  t.reset_stats();
  EXPECT_TRUE(
      scan_k_nearest_vector(t, "vec", VectorValue{1, 1, 1, 1}, 0, VectorMetric::kCosine).empty());
  EXPECT_EQ(t.stats().pages_read, 0u);
}

// ---------------------------------------------------------------------------
// Errores
// ---------------------------------------------------------------------------

TEST_F(VectorScanTest, UnaConsultaDeOtraDimensionEsInvalidRecord) {
  TableFile& t = tabla_al_azar(10, 4, 5);
  t.reset_stats();
  try {
    (void)scan_k_nearest_vector(t, "vec", VectorValue{1, 2, 3}, 5, VectorMetric::kEuclidean);
    FAIL() << "no lanzo";
  } catch (const InvalidRecord& e) {
    EXPECT_NE(std::string(e.what()).find("dimension 3"), std::string::npos) << e.what();
    EXPECT_NE(std::string(e.what()).find("VECTOR(4)"), std::string::npos) << e.what();
  }
  EXPECT_EQ(t.stats().pages_read, 0u);  // se valida antes de leer
}

TEST_F(VectorScanTest, UnaConsultaConNaNEsInvalidRecord) {
  TableFile& t = tabla_al_azar(10, 2, 5);
  EXPECT_THROW((void)scan_k_nearest_vector(t, "vec", VectorValue{1, std::nanf("")}, 5,
                                           VectorMetric::kCosine),
               InvalidRecord);
}

TEST_F(VectorScanTest, UnaColumnaQueNoEsVectorEsSchemaError) {
  TableFile& t = tabla_al_azar(10, 2, 5);
  EXPECT_THROW((void)scan_k_nearest_vector(t, "nombre", VectorValue{1, 1}, 5,
                                           VectorMetric::kCosine),
               SchemaError);
  EXPECT_THROW((void)scan_k_nearest_vector(t, "no_existe", VectorValue{1, 1}, 5,
                                           VectorMetric::kCosine),
               SchemaError);
}

}  // namespace
}  // namespace quipudb
