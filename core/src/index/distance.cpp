#include "quipudb/index/distance.hpp"

#include <cmath>
#include <numbers>
#include <string>

#include "quipudb/error.hpp"

namespace quipudb {
namespace {

std::string texto(Point p) {
  return "(" + std::to_string(p.x) + ", " + std::to_string(p.y) + ")";
}

void exigir_finitos(Point a, Point b, const char* metrica) {
  if (!std::isfinite(a.x) || !std::isfinite(a.y) || !std::isfinite(b.x) || !std::isfinite(b.y)) {
    throw InvalidRecord(std::string("coordenada no finita en ") + metrica + ": " + texto(a) +
                        " y " + texto(b));
  }
}

constexpr double kMaxLatitude = 90.0;

void exigir_latitud(Point p) {
  if (p.y < -kMaxLatitude || p.y > kMaxLatitude) {
    throw InvalidRecord("latitud fuera de [-90, 90] en haversine: " + texto(p));
  }
}

double radianes(double grados) noexcept { return grados * std::numbers::pi_v<double> / 180.0; }

}  // namespace

const char* name_of(Metric metric) noexcept {
  return metric == Metric::kHaversine ? "haversine" : "euclidean";
}

double euclidean(Point a, Point b) {
  exigir_finitos(a, b, "euclidean");
  return std::hypot(b.x - a.x, b.y - a.y);
}

double haversine(Point a, Point b) {
  exigir_finitos(a, b, "haversine");
  exigir_latitud(a);
  exigir_latitud(b);

  const double lat1 = radianes(a.y);
  const double lat2 = radianes(b.y);
  const double dlat = lat2 - lat1;
  const double dlon = radianes(b.x - a.x);

  // h = sen^2(dlat/2) + cos(lat1) cos(lat2) sen^2(dlon/2)
  //
  // Se usa esta forma y no el arcocoseno del producto escalar porque el
  // coseno es plano cerca de 0: para dos puntos a pocos metros, acos pierde
  // casi todos los digitos y devuelve 0 o un valor a saltos. El seno de la
  // mitad no tiene ese problema, que es justo por lo que existe la formula.
  const double sen_lat = std::sin(dlat / 2.0);
  const double sen_lon = std::sin(dlon / 2.0);
  const double h = sen_lat * sen_lat + std::cos(lat1) * std::cos(lat2) * sen_lon * sen_lon;

  // El redondeo puede dejar h un pelo por encima de 1 en puntos antipodales,
  // y entonces la raiz de 1 - h seria NaN.
  const double raiz = std::sqrt(h <= 1.0 ? h : 1.0);
  const double complemento = std::sqrt(h <= 1.0 ? 1.0 - h : 0.0);

  // atan2 y no asin: asin tambien pierde precision cuando el angulo se
  // acerca a 90 grados, y atan2 cubre el circulo entero sin ese problema.
  return 2.0 * kEarthRadiusMeters * std::atan2(raiz, complemento);
}

double distance(Point a, Point b, Metric metric) {
  return metric == Metric::kHaversine ? haversine(a, b) : euclidean(a, b);
}

}  // namespace quipudb
