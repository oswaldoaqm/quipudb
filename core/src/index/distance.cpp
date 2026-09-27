#include "quipudb/index/distance.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
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

namespace {

/// Cuanto se ensancha cada arista, RELATIVO a su propia magnitud.
///
/// Tiene que ser relativo a la coordenada y no al radio: la arista sale de
/// `centro + delta`, y ahi el redondeo es del tamano de un ulp de la
/// coordenada. A latitud 40 eso son ~7e-15 grados, mucho mas que un 1e-12 del
/// radio cuando el radio son 100 metros. Aplicado al radio, la holgura se
/// perdia y la caja podia quedar un ulp corta.
///
/// 1e-12 de 90 grados son 4 mm en el suelo: lo bastante para tapar el
/// redondeo y demasiado poco para que la poda pierda valor.
constexpr double kHolgura = 1e-12;
/// Para las aristas cerca de 0, donde lo relativo no alcanza.
constexpr double kHolguraMinima = 1e-12;

/// Empuja cada arista hacia afuera. El rectangulo solo poda: pasarse cuesta
/// unas pocas distancias de mas, quedarse corto pierde resultados.
Rect ensanchar(Rect r) {
  const auto margen = [](double v) { return std::abs(v) * kHolgura + kHolguraMinima; };
  return {r.min_x - margen(r.min_x), r.min_y - margen(r.min_y), r.max_x + margen(r.max_x),
          r.max_y + margen(r.max_y)};
}

/// Ensancha y despues recorta al dominio geografico.
///
/// El recorte solo vale aqui: en la metrica euclidiana `y` no es una latitud
/// y un punto puede estar perfectamente en y = 1000. En la geodesica, en
/// cambio, `exigir_latitud` ya garantiza que ningun punto pase de +-90, asi
/// que recortar no puede perder nada y deja la caja dentro del dominio.
Rect ensanchar_geografica(Rect r) {
  Rect ancha = ensanchar(r);
  ancha.min_y = std::max(ancha.min_y, -90.0);
  ancha.max_y = std::min(ancha.max_y, 90.0);
  return ancha;
}

constexpr Rect kRectVacio{1.0, 1.0, -1.0, -1.0};

/// Todas las longitudes. La usan los dos casos donde acotar el ancho no
/// tendria sentido: el circulo toca un polo, o cruza el antimeridiano.
Rect franja_completa(double lat_min, double lat_max) {
  return {-180.0, std::max(lat_min, -90.0), 180.0, std::min(lat_max, 90.0)};
}

}  // namespace

namespace {

double acotar(double v, double bajo, double alto) noexcept {
  return v < bajo ? bajo : (v > alto ? alto : v);
}

double grados(double rad) noexcept { return rad * 180.0 / std::numbers::pi_v<double>; }

/// La diferencia de longitud por el lado corto, en [-180, 180].
double diferencia_de_longitud(double desde, double hasta) noexcept {
  double d = std::fmod(hasta - desde, 360.0);
  if (d > 180.0) d -= 360.0;
  if (d < -180.0) d += 360.0;
  return d;
}

/// La distancia de `p` al borde meridiano de `region` en la longitud `lon`.
///
/// Sobre ese borde, el coseno de la distancia angular vale
/// sen(lat_p) sen(lat) + cos(lat_p) cos(lat) cos(dlon), que es una sola
/// sinusoide en `lat`: tiene como mucho un punto critico. De ahi que baste
/// con mirar tres latitudes y quedarse con la mejor.
///
/// El punto critico es el pie de la perpendicular al circulo maximo,
/// atan2(sen lat_p, cos lat_p cos dlon), y SOLO existe dentro de esta mitad
/// del circulo cuando cos(lat_p) cos(dlon) > 0. Cuando no, la distancia es
/// monotona sobre el borde y el minimo cae en un extremo del rango.
///
/// Los extremos se prueban siempre: agregar candidatos nunca puede dejar el
/// resultado por debajo del minimo real -- todos son puntos de la region --,
/// y olvidarse de uno si podria dejarlo por encima, que es lo que romperia
/// la poda del k-NN.
double minimo_en_el_meridiano(Point p, double lon, const Rect& region) {
  double mejor = std::min(haversine(p, {lon, region.min_y}), haversine(p, {lon, region.max_y}));

  const double dlon = radianes(diferencia_de_longitud(lon, p.x));
  const double hacia_el_meridiano = std::cos(radianes(p.y)) * std::cos(dlon);
  if (hacia_el_meridiano > 0.0) {
    const double pie = grados(std::atan2(std::sin(radianes(p.y)), hacia_el_meridiano));
    if (pie > region.min_y && pie < region.max_y) {
      mejor = std::min(mejor, haversine(p, {lon, pie}));
    }
  }
  return mejor;
}

}  // namespace

double min_distance(Point p, const Rect& region, Metric metric) {
  exigir_finitos(p, p, "min_distance");
  if (!std::isfinite(region.min_x) || !std::isfinite(region.min_y) ||
      !std::isfinite(region.max_x) || !std::isfinite(region.max_y)) {
    throw InvalidRecord("region con coordenadas no finitas en min_distance");
  }
  // Una region invertida no contiene ningun punto: nada puede acercarsele.
  if (region.min_x > region.max_x || region.min_y > region.max_y) {
    return std::numeric_limits<double>::infinity();
  }
  if (region.contains(p)) return 0.0;

  if (metric == Metric::kEuclidean) {
    const Point cerca{acotar(p.x, region.min_x, region.max_x),
                      acotar(p.y, region.min_y, region.max_y)};
    return euclidean(p, cerca);
  }

  exigir_latitud(p);

  // Con la longitud dentro del rango, el punto mas cercano esta justo al
  // norte o al sur, sobre el mismo meridiano que `p`.
  if (region.min_x <= p.x && p.x <= region.max_x) {
    return haversine(p, {p.x, acotar(p.y, region.min_y, region.max_y)});
  }

  // Fuera del rango hay que mirar los dos bordes meridianos y quedarse con
  // el mejor: cual de los dos gana depende de por donde se de la vuelta.
  //
  // Los bordes en paralelo no se miran: sobre un paralelo la distancia crece
  // con |dlon|, asi que su minimo cae siempre en una esquina, y las cuatro
  // esquinas ya son extremos de los meridianos.
  return std::min(minimo_en_el_meridiano(p, region.min_x, region),
                  minimo_en_el_meridiano(p, region.max_x, region));
}

Rect bounding_box(Point center, double radius, Metric metric) {
  exigir_finitos(center, center, "bounding_box");
  if (!std::isfinite(radius)) {
    throw InvalidRecord("radio no finito en bounding_box: " + std::to_string(radius));
  }
  if (radius < 0.0) return kRectVacio;

  const double r = radius;

  if (metric == Metric::kEuclidean) {
    return ensanchar({center.x - r, center.y - r, center.x + r, center.y + r});
  }

  exigir_latitud(center);

  // Radio angular: que fraccion de la esfera abarca el circulo.
  const double delta = r / kEarthRadiusMeters;
  const double media_vuelta = std::numbers::pi_v<double>;
  if (delta >= media_vuelta) return franja_completa(-90.0, 90.0);

  const double delta_grados = delta * 180.0 / media_vuelta;
  const double lat_min = center.y - delta_grados;
  const double lat_max = center.y + delta_grados;

  // El circulo toca un polo: ahi se juntan todas las longitudes, asi que
  // acotar el ancho no tendria sentido.
  if (lat_min <= -90.0 || lat_max >= 90.0) {
    return ensanchar_geografica(franja_completa(lat_min, lat_max));
  }

  const double lat = radianes(center.y);
  // Con el polo ya descartado, |lat| + delta < 90 grados, y entonces
  // cos(lat) > sen(delta): el cociente nunca llega a 1. El clamp es contra el
  // redondeo, no contra el caso real.
  const double razon = std::sin(delta) / std::cos(lat);
  const double semiancho = std::asin(razon >= 1.0 ? 1.0 : razon) * 180.0 / media_vuelta;

  const double lon_min = center.x - semiancho;
  const double lon_max = center.x + semiancho;
  // Un Rect no sabe envolverse por el antimeridiano. Abrir la longitud entera
  // mantiene el resultado correcto y solo cuesta poda.
  if (lon_min < -180.0 || lon_max > 180.0) {
    return ensanchar_geografica(franja_completa(lat_min, lat_max));
  }

  return ensanchar_geografica({lon_min, lat_min, lon_max, lat_max});
}

}  // namespace quipudb
