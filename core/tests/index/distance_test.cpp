// Pruebas de las metricas de distancia (issue #119).
//
// Las tolerancias van separadas a proposito, porque miden dos cosas
// distintas:
//
//   1. Contra valores de Haversine calculados aparte, con la misma formula y
//      el mismo radio, la tolerancia es 1e-9 RELATIVO. Ahi no hay margen de
//      modelo: si esta implementacion no reproduce esos numeros, esta mal.
//
//   2. Contra las distancias geodesicas reales sobre el elipsoide WGS84 la
//      tolerancia es 0,6 %. Ese margen no es holgura: es el error de suponer
//      que la Tierra es una esfera. Medido sobre estos mismos pares, el peor
//      caso es Lima-Quito con 0,54 %.
//
// Las referencias de los dos grupos se calcularon fuera con doble precision:
// las de Haversine con la formula sobre el radio R1 de la IUGG, y las de
// WGS84 con el metodo inverso de Vincenty.

#include "quipudb/index/distance.hpp"

#include <gtest/gtest.h>

#include <cmath>
#include <limits>
#include <numbers>

#include "quipudb/error.hpp"

namespace quipudb {
namespace {

// x = longitud, y = latitud, como en todo el R-Tree.
constexpr Point kLima{-77.0428, -12.0464};
constexpr Point kArequipa{-71.5375, -16.3989};
constexpr Point kCusco{-71.9675, -13.5319};
constexpr Point kTrujillo{-79.0300, -8.1100};
constexpr Point kIquitos{-73.2538, -3.7491};
constexpr Point kQuito{-78.4678, -0.1807};
constexpr Point kSantiago{-70.6693, -33.4489};
constexpr Point kMadrid{-3.7038, 40.4168};
constexpr Point kTokio{139.6917, 35.6895};

/// Un par de ciudades con sus dos distancias de referencia, en metros.
struct Referencia {
  const char* nombre;
  Point desde;
  Point hasta;
  /// Haversine sobre la esfera de radio `kEarthRadiusMeters`.
  double esfera;
  /// Geodesica exacta sobre WGS84, que es la distancia del mundo real.
  double wgs84;
};

constexpr Referencia kCiudades[] = {
    {"Lima-Arequipa", kLima, kArequipa, 765'595.626, 764'684.799},
    {"Lima-Cusco", kLima, kCusco, 574'576.143, 575'011.564},
    {"Lima-Trujillo", kLima, kTrujillo, 488'773.179, 486'826.081},
    {"Lima-Iquitos", kLima, kIquitos, 1'012'452.458, 1'008'138.128},
    {"Lima-Quito", kLima, kQuito, 1'328'746.317, 1'321'647.979},
    {"Lima-Santiago", kLima, kSantiago, 2'466'407.196, 2'457'487.323},
    {"Lima-Madrid", kLima, kMadrid, 9'509'049.435, 9'502'078.053},
    {"Lima-Tokio", kLima, kTokio, 15'491'335.255, 15'498'828.053},
};

/// Lo que se le exige a la IMPLEMENTACION: reproducir la formula.
constexpr double kToleranciaRelativa = 1e-9;
/// Lo que se le exige al MODELO: acercarse a la Tierra de verdad.
constexpr double kToleranciaModelo = 0.006;

// ---------------------------------------------------------------------------
// Criterio 2: la geodesica, en metros
// ---------------------------------------------------------------------------

TEST(DistanceTest, HaversineReproduceLaFormula) {
  for (const auto& caso : kCiudades) {
    const double medido = haversine(caso.desde, caso.hasta);
    EXPECT_NEAR(medido, caso.esfera, caso.esfera * kToleranciaRelativa) << caso.nombre;
  }
}

TEST(DistanceTest, HaversineSeAcercaALaDistanciaReal) {
  for (const auto& caso : kCiudades) {
    const double medido = haversine(caso.desde, caso.hasta);
    const double desvio = std::abs(medido - caso.wgs84) / caso.wgs84;
    EXPECT_LT(desvio, kToleranciaModelo)
        << caso.nombre << ": " << medido << " m contra " << caso.wgs84
        << " m reales, " << desvio * 100.0 << " % de desvio";
  }
}

TEST(DistanceTest, HaversineEsSimetrica) {
  for (const auto& caso : kCiudades) {
    EXPECT_DOUBLE_EQ(haversine(caso.desde, caso.hasta), haversine(caso.hasta, caso.desde))
        << caso.nombre;
  }
}

TEST(DistanceTest, HaversineDeUnPuntoConsigoMismoEsCero) {
  for (const auto& caso : kCiudades) {
    EXPECT_DOUBLE_EQ(haversine(caso.desde, caso.desde), 0.0) << caso.nombre;
  }
}

TEST(DistanceTest, HaversineCubreLaEsferaEntera) {
  // Media vuelta: la mitad de la circunferencia de la esfera.
  const double media_vuelta = std::numbers::pi_v<double> * kEarthRadiusMeters;
  EXPECT_NEAR(haversine({0.0, 90.0}, {0.0, -90.0}), media_vuelta,
              media_vuelta * kToleranciaRelativa);
  EXPECT_NEAR(haversine({0.0, 0.0}, {180.0, 0.0}), media_vuelta,
              media_vuelta * kToleranciaRelativa);
}

TEST(DistanceTest, HaversineCruzaElAntimeridianoSinSaltos) {
  // 0,2 grados de longitud en el ecuador, con el corte de -180/180 en medio.
  // Si la formula restara las longitudes sin mas, saldrian 359,8 grados.
  const double cruzando = haversine({179.9, 0.0}, {-179.9, 0.0});
  const double equivalente = haversine({-0.1, 0.0}, {0.1, 0.0});
  EXPECT_NEAR(cruzando, equivalente, equivalente * kToleranciaRelativa);
  EXPECT_LT(cruzando, 25'000.0);
}

TEST(DistanceTest, HaversineNoPierdePrecisionEnDistanciasCortas) {
  // Un metro en el ecuador son 1/111319,49 grados. Es el caso que el
  // arcocoseno no resuelve: con el, esta prueba daria 0.
  constexpr double kUnMetroEnGrados = 1.0 / 111'319.49;
  const double medido = haversine({0.0, 0.0}, {kUnMetroEnGrados, 0.0});
  EXPECT_NEAR(medido, 1.0, 0.01);
  EXPECT_GT(medido, 0.0);
}

// ---------------------------------------------------------------------------
// Criterio 1: la euclidiana
// ---------------------------------------------------------------------------

TEST(DistanceTest, EuclidianaEsElTeoremaDePitagoras) {
  EXPECT_DOUBLE_EQ(euclidean({0.0, 0.0}, {3.0, 4.0}), 5.0);
  EXPECT_DOUBLE_EQ(euclidean({1.0, 1.0}, {4.0, 5.0}), 5.0);
  EXPECT_DOUBLE_EQ(euclidean({-3.0, -4.0}, {0.0, 0.0}), 5.0);
}

TEST(DistanceTest, EuclidianaEsSimetricaYCeroConsigoMisma) {
  EXPECT_DOUBLE_EQ(euclidean(kLima, kCusco), euclidean(kCusco, kLima));
  EXPECT_DOUBLE_EQ(euclidean(kLima, kLima), 0.0);
}

TEST(DistanceTest, EuclidianaNoDesbordaConCoordenadasEnormes) {
  // Elevar 1e200 al cuadrado desborda el double; hypot no lo hace.
  const double grande = 1e200;
  EXPECT_TRUE(std::isfinite(euclidean({0.0, 0.0}, {grande, grande})));
  EXPECT_NEAR(euclidean({0.0, 0.0}, {grande, grande}), grande * std::numbers::sqrt2_v<double>,
              grande * 1e-12);
}

TEST(DistanceTest, EuclidianaCumpleLaDesigualdadTriangular) {
  const double directo = euclidean(kLima, kSantiago);
  const double con_escala = euclidean(kLima, kArequipa) + euclidean(kArequipa, kSantiago);
  EXPECT_LE(directo, con_escala);
}

// ---------------------------------------------------------------------------
// Criterio 5: en que se diferencian
// ---------------------------------------------------------------------------

TEST(DistanceTest, LaEuclidianaSobreGradosNoSonMetros) {
  // El mismo par medido de las dos formas da numeros de escalas distintas:
  // una cuenta grados y la otra metros. Confundirlas es el error que la
  // documentacion de distance.hpp advierte.
  EXPECT_NEAR(euclidean(kLima, kArequipa), 7.018, 0.001);
  EXPECT_NEAR(haversine(kLima, kArequipa), 765'595.6, 1.0);
}

TEST(DistanceTest, UnGradoDeLongitudEncogeConLaLatitud) {
  // La razon por la que las dos metricas no son intercambiables sobre
  // coordenadas geograficas: en grados los tres pares miden exactamente lo
  // mismo, y en metros no.
  const double en_el_ecuador = haversine({0.0, 0.0}, {1.0, 0.0});
  const double en_lima = haversine({0.0, -12.0}, {1.0, -12.0});
  const double lejos = haversine({0.0, 60.0}, {1.0, 60.0});

  EXPECT_DOUBLE_EQ(euclidean({0.0, 0.0}, {1.0, 0.0}), euclidean({0.0, 60.0}, {1.0, 60.0}));

  EXPECT_NEAR(en_el_ecuador, 111'195.0, 200.0);
  EXPECT_NEAR(en_lima, 108'768.0, 200.0);
  EXPECT_NEAR(lejos, 55'597.0, 200.0);
  EXPECT_LT(lejos, en_el_ecuador / 1.9);
}

TEST(DistanceTest, SobreUnMeridianoLasDosCoincidenAlEscalar) {
  // Norte-sur no hay distorsion: un grado de latitud mide igual en todas
  // partes sobre la esfera, asi que la euclidiana en grados por los metros
  // que mide un grado reproduce la geodesica.
  const double metros_por_grado = haversine({0.0, 0.0}, {0.0, 1.0});
  const Point a{-77.0, -12.0};
  const Point b{-77.0, -16.0};
  EXPECT_NEAR(euclidean(a, b) * metros_por_grado, haversine(a, b), 1e-6);
}

// ---------------------------------------------------------------------------
// Criterio 3: la metrica se elige en tiempo de ejecucion
// ---------------------------------------------------------------------------

TEST(DistanceTest, DistanceDespachaSegunLaMetrica) {
  // El valor de `metrica` sale de una variable, no de una constante del
  // codigo: es lo que hara el operador al leerlo del plan.
  for (const Metric metrica : {Metric::kEuclidean, Metric::kHaversine}) {
    const double esperado =
        metrica == Metric::kEuclidean ? euclidean(kLima, kCusco) : haversine(kLima, kCusco);
    EXPECT_DOUBLE_EQ(distance(kLima, kCusco, metrica), esperado) << name_of(metrica);
  }
}

TEST(DistanceTest, CadaMetricaTieneNombre) {
  EXPECT_STREQ(name_of(Metric::kEuclidean), "euclidean");
  EXPECT_STREQ(name_of(Metric::kHaversine), "haversine");
}

// ---------------------------------------------------------------------------
// Entradas que no se pueden medir
// ---------------------------------------------------------------------------

// Las tres funciones son [[nodiscard]] y aqui el valor no interesa, solo si
// lanzan: el cast a void es lo que le dice eso al compilador.
TEST(DistanceTest, CoordenadaNoFinitaEsInvalidRecord) {
  const double nan = std::numeric_limits<double>::quiet_NaN();
  const double inf = std::numeric_limits<double>::infinity();

  for (const Metric metrica : {Metric::kEuclidean, Metric::kHaversine}) {
    EXPECT_THROW((void)distance({nan, 0.0}, kLima, metrica), InvalidRecord) << name_of(metrica);
    EXPECT_THROW((void)distance({0.0, nan}, kLima, metrica), InvalidRecord) << name_of(metrica);
    EXPECT_THROW((void)distance(kLima, {inf, 0.0}, metrica), InvalidRecord) << name_of(metrica);
    EXPECT_THROW((void)distance(kLima, {0.0, -inf}, metrica), InvalidRecord) << name_of(metrica);
  }
}

TEST(DistanceTest, LatitudImposibleEsInvalidRecordSoloEnHaversine) {
  EXPECT_THROW((void)haversine({0.0, 90.1}, kLima), InvalidRecord);
  EXPECT_THROW((void)haversine(kLima, {0.0, -90.1}), InvalidRecord);
  EXPECT_NO_THROW((void)haversine({0.0, 90.0}, kLima));
  EXPECT_NO_THROW((void)haversine({0.0, -90.0}, kLima));

  // La euclidiana no interpreta las coordenadas como grados, asi que un
  // 90,1 ahi es un punto del plano como cualquier otro.
  EXPECT_NO_THROW((void)euclidean({0.0, 90.1}, kLima));
}

TEST(DistanceTest, LongitudFueraDeRangoNoEsError) {
  // 190 grados es lo mismo que -170: la formula es periodica y no hace falta
  // normalizar antes de llamarla.
  EXPECT_NEAR(haversine({190.0, 0.0}, {0.0, 0.0}), haversine({-170.0, 0.0}, {0.0, 0.0}), 1e-6);
}

}  // namespace
}  // namespace quipudb
