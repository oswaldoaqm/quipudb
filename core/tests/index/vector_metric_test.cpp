// Pruebas de las metricas de similitud entre vectores (issue #179).
//
// Los valores esperados estan calculados a mano en el comentario de cada
// prueba. La tolerancia es 1e-12 relativa: los vectores son float, pero todos
// los que se usan aqui son exactos en float y las cuentas van en double.

#include "quipudb/index/vector_metric.hpp"

#include <gtest/gtest.h>

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <limits>
#include <numeric>
#include <random>
#include <string>
#include <vector>

#include "azar_portable.hpp"
#include "quipudb/error.hpp"

namespace quipudb {
namespace {

using Vec = std::vector<float>;

constexpr VectorMetric kTodas[] = {VectorMetric::kEuclidean, VectorMetric::kDotProduct,
                                   VectorMetric::kCosine};

void cerca(double obtenido, double esperado) {
  EXPECT_NEAR(obtenido, esperado, 1e-12 * std::max(1.0, std::abs(esperado)));
}

// ---------------------------------------------------------------------------
// Valores calculados a mano
// ---------------------------------------------------------------------------

TEST(VectorMetricTest, LosTresValoresDeUnParConocido) {
  const Vec a{1, 2, 3};
  const Vec b{4, 5, 6};
  // (4-1)^2 + (5-2)^2 + (6-3)^2 = 27
  cerca(euclidean_distance(a, b), std::sqrt(27.0));
  // 4 + 10 + 18 = 32
  cerca(dot_product(a, b), 32.0);
  // 32 / (sqrt(14) * sqrt(77))
  cerca(cosine_similarity(a, b), 32.0 / (std::sqrt(14.0) * std::sqrt(77.0)));
}

TEST(VectorMetricTest, VectoresOrtogonales) {
  const Vec a{1, 0};
  const Vec b{0, 1};
  cerca(euclidean_distance(a, b), std::sqrt(2.0));
  cerca(dot_product(a, b), 0.0);
  cerca(cosine_similarity(a, b), 0.0);
}

TEST(VectorMetricTest, VectoresOpuestos) {
  const Vec a{1, 1};
  const Vec b{-1, -1};
  cerca(euclidean_distance(a, b), std::sqrt(8.0));
  cerca(dot_product(a, b), -2.0);
  cerca(cosine_similarity(a, b), -1.0);
  cerca(vector_distance(a, b, VectorMetric::kCosine), 2.0);  // el maximo
}

TEST(VectorMetricTest, UnVectorConsigoMismo) {
  const Vec a{3, -4, 12};
  cerca(euclidean_distance(a, a), 0.0);
  cerca(dot_product(a, a), 169.0);  // 9 + 16 + 144 = 13^2
  cerca(cosine_similarity(a, a), 1.0);
  cerca(vector_distance(a, a, VectorMetric::kCosine), 0.0);
}

TEST(VectorMetricTest, SonSimetricas) {
  const Vec a{0.5F, -2, 7, 1.25F};
  const Vec b{3, 1, -0.75F, 2};
  for (const VectorMetric m : kTodas) {
    EXPECT_EQ(vector_similarity(a, b, m), vector_similarity(b, a, m)) << name_of(m);
  }
}

// ---------------------------------------------------------------------------
// En que se diferencian
// ---------------------------------------------------------------------------

TEST(VectorMetricTest, ElCosenoIgnoraLaNormaYLosDemasNo) {
  const Vec a{1, 2, 3};
  const Vec triple{3, 6, 9};
  cerca(cosine_similarity(a, triple), 1.0);    // misma direccion
  cerca(dot_product(a, triple), 3 * 14.0);     // crece con la norma
  cerca(euclidean_distance(a, triple), 2 * std::sqrt(14.0));  // |2a|
}

TEST(VectorMetricTest, ElProductoPuntoNoEsUnaDistancia) {
  // Un vector puede tener mas producto punto con otro que consigo mismo: la
  // consulta `a` encontraria primero a `b`. Es la razon de documentarlo.
  const Vec a{1, 0};
  const Vec b{10, 0};
  EXPECT_GT(dot_product(a, b), dot_product(a, a));
  EXPECT_LT(vector_distance(a, b, VectorMetric::kDotProduct),
            vector_distance(a, a, VectorMetric::kDotProduct));
  // El coseno y la euclidiana si ponen primero a `a`.
  EXPECT_LE(vector_distance(a, a, VectorMetric::kCosine),
            vector_distance(a, b, VectorMetric::kCosine));
  EXPECT_LT(vector_distance(a, a, VectorMetric::kEuclidean),
            vector_distance(a, b, VectorMetric::kEuclidean));
}

TEST(VectorMetricTest, ConVectoresNormalizadosLasTresOrdenanIgual) {
  // |a| = |b| = 1  =>  a.b = cos  y  |a - b|^2 = 2 - 2 cos.
  // Generador portable (#204): los mismos vectores en GCC, Clang y MSVC.
  std::mt19937 gen(179);
  const prueba::RealUniforme u(-1.0, 1.0);
  auto unitario = [&] {
    Vec v(32);
    for (auto& x : v) x = static_cast<float>(u(gen));
    const double n = std::sqrt(std::inner_product(v.begin(), v.end(), v.begin(), 0.0));
    for (auto& x : v) x = static_cast<float>(x / n);
    return v;
  };

  const Vec consulta = unitario();
  std::vector<Vec> base;
  for (int i = 0; i < 200; ++i) base.push_back(unitario());

  for (const Vec& v : base) {
    const double c = cosine_similarity(consulta, v);
    EXPECT_NEAR(dot_product(consulta, v), c, 1e-6);  // la norma float no es 1 exacta
    const double e = euclidean_distance(consulta, v);
    EXPECT_NEAR(e * e, 2.0 - 2.0 * c, 1e-5);
  }

  auto orden = [&](VectorMetric m) {
    std::vector<std::size_t> idx(base.size());
    std::iota(idx.begin(), idx.end(), 0);
    std::sort(idx.begin(), idx.end(), [&](std::size_t i, std::size_t j) {
      return vector_distance(consulta, base[i], m) < vector_distance(consulta, base[j], m);
    });
    return idx;
  };
  // Los 10 primeros, que es lo que mide el Recall@10 del benchmark.
  const auto por_coseno = orden(VectorMetric::kCosine);
  const auto por_euclidiana = orden(VectorMetric::kEuclidean);
  const auto por_punto = orden(VectorMetric::kDotProduct);
  EXPECT_TRUE(std::equal(por_coseno.begin(), por_coseno.begin() + 10, por_euclidiana.begin()));
  EXPECT_TRUE(std::equal(por_coseno.begin(), por_coseno.begin() + 10, por_punto.begin()));
}

// ---------------------------------------------------------------------------
// La convencion: menor siempre es mejor
// ---------------------------------------------------------------------------

TEST(VectorMetricTest, VectorDistanceOrdenaElMasParecidoPrimeroEnLasTres) {
  const Vec consulta{1, 1, 0};
  const Vec casi_igual{1, 0.9F, 0};
  const Vec lejos{-1, 0, 1};
  for (const VectorMetric m : kTodas) {
    EXPECT_LT(vector_distance(consulta, casi_igual, m), vector_distance(consulta, lejos, m))
        << name_of(m);
  }
}

TEST(VectorMetricTest, FromDistanceDevuelveElValorNatural) {
  const Vec a{1, 2, 3};
  const Vec b{-2, 0.5F, 4};
  for (const VectorMetric m : kTodas) {
    cerca(from_distance(vector_distance(a, b, m), m), vector_similarity(a, b, m));
  }
}

TEST(VectorMetricTest, SentidoYNombreDeCadaMetrica) {
  EXPECT_FALSE(higher_is_better(VectorMetric::kEuclidean));
  EXPECT_TRUE(higher_is_better(VectorMetric::kDotProduct));
  EXPECT_TRUE(higher_is_better(VectorMetric::kCosine));
  EXPECT_STREQ(name_of(VectorMetric::kEuclidean), "EUCLIDEAN");
  EXPECT_STREQ(name_of(VectorMetric::kDotProduct), "DOT_PRODUCT");
  EXPECT_STREQ(name_of(VectorMetric::kCosine), "COSINE");
}

TEST(VectorMetricTest, LaDistanciaDelCosenoQuedaEntreCeroYDos) {
  std::mt19937 gen(7);
  const prueba::RealUniforme u(-5.0, 5.0);
  for (int i = 0; i < 1000; ++i) {
    Vec a(8);
    Vec b(8);
    for (auto& x : a) x = static_cast<float>(u(gen));
    for (auto& x : b) x = static_cast<float>(u(gen));
    const double d = vector_distance(a, b, VectorMetric::kCosine);
    ASSERT_GE(d, 0.0);
    ASSERT_LE(d, 2.0);
  }
  // Paralelos con componentes que no son exactos en binario. Sin el acotado,
  // este par da un coseno de 1.0000000000000002 (encontrado buscando, no
  // supuesto, con GCC en x86-64) y la distancia del coseno saldria negativa.
  // Se compara con <= y no con == porque otro compilador puede redondear
  // hacia abajo, y eso tambien es correcto.
  const Vec a{0.1F, 0.1F, 0.3F};
  const Vec b{0.2F, 0.2F, 0.6F};
  EXPECT_LE(cosine_similarity(a, b), 1.0);
  EXPECT_GE(vector_distance(a, b, VectorMetric::kCosine), 0.0);
  EXPECT_NEAR(cosine_similarity(a, b), 1.0, 1e-15);
}

// ---------------------------------------------------------------------------
// Casos limite
// ---------------------------------------------------------------------------

TEST(VectorMetricTest, ElCosenoConNormaCeroValeCero) {
  const Vec cero{0, 0, 0};
  const Vec v{1, 2, 3};
  EXPECT_EQ(cosine_similarity(cero, v), 0.0);
  EXPECT_EQ(cosine_similarity(v, cero), 0.0);
  EXPECT_EQ(cosine_similarity(cero, cero), 0.0);
  EXPECT_EQ(vector_distance(cero, v, VectorMetric::kCosine), 1.0);
  // Las otras dos si estan definidas.
  cerca(euclidean_distance(cero, v), std::sqrt(14.0));
  cerca(dot_product(cero, v), 0.0);
}

TEST(VectorMetricTest, DimensionesDistintasSonInvalidRecord) {
  const Vec a{1, 2, 3};
  const Vec b{1, 2};
  for (const VectorMetric m : kTodas) {
    EXPECT_THROW((void)vector_distance(a, b, m), InvalidRecord) << name_of(m);
  }
  try {
    (void)euclidean_distance(a, b);
    FAIL() << "no lanzo";
  } catch (const InvalidRecord& e) {
    EXPECT_NE(std::string(e.what()).find("dimension 3 y 2"), std::string::npos) << e.what();
  }
}

TEST(VectorMetricTest, VectoresVaciosSonInvalidRecord) {
  const Vec vacio;
  for (const VectorMetric m : kTodas) {
    EXPECT_THROW((void)vector_distance(vacio, vacio, m), InvalidRecord) << name_of(m);
  }
}

TEST(VectorMetricTest, ComponentesNoFinitosSonInvalidRecord) {
  const float nan = std::numeric_limits<float>::quiet_NaN();
  const float inf = std::numeric_limits<float>::infinity();
  const Vec bueno{1, 2, 3};
  for (const VectorMetric m : kTodas) {
    EXPECT_THROW((void)vector_distance(Vec{1, nan, 3}, bueno, m), InvalidRecord);
    EXPECT_THROW((void)vector_distance(bueno, Vec{1, 2, -inf}, m), InvalidRecord);
  }
}

TEST(VectorMetricTest, ComponentesEnormesNoDesbordan) {
  // El mayor float al cuadrado desbordaria si se acumulara en float.
  const float max = std::numeric_limits<float>::max();
  const Vec a{max, max};
  const Vec b{-max, -max};
  EXPECT_TRUE(std::isfinite(euclidean_distance(a, b)));
  EXPECT_TRUE(std::isfinite(dot_product(a, b)));
  cerca(cosine_similarity(a, b), -1.0);
}

}  // namespace
}  // namespace quipudb
