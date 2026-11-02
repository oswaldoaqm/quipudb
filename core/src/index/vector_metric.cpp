#include "quipudb/index/vector_metric.hpp"

#include <cmath>
#include <cstddef>
#include <string>

#include "quipudb/error.hpp"

namespace quipudb {

namespace {

/// Las validaciones comunes a las tres metricas. Se hacen en una sola pasada
/// aparte y no dentro del bucle de cada metrica para que los mensajes sean
/// los mismos en las tres.
void validar(std::span<const float> a, std::span<const float> b, const char* donde) {
  if (a.size() != b.size()) {
    throw InvalidRecord(std::string(donde) + ": los vectores tienen dimension " +
                        std::to_string(a.size()) + " y " + std::to_string(b.size()));
  }
  if (a.empty()) {
    throw InvalidRecord(std::string(donde) + ": los vectores estan vacios");
  }
  for (std::size_t i = 0; i < a.size(); ++i) {
    if (!std::isfinite(a[i]) || !std::isfinite(b[i])) {
      throw InvalidRecord(std::string(donde) + ": el componente " + std::to_string(i) +
                          " no es finito");
    }
  }
}

}  // namespace

const char* name_of(VectorMetric metric) noexcept {
  switch (metric) {
    case VectorMetric::kEuclidean:
      return "EUCLIDEAN";
    case VectorMetric::kDotProduct:
      return "DOT_PRODUCT";
    case VectorMetric::kCosine:
      return "COSINE";
  }
  return "?";
}

bool higher_is_better(VectorMetric metric) noexcept {
  return metric != VectorMetric::kEuclidean;
}

double euclidean_distance(std::span<const float> a, std::span<const float> b) {
  validar(a, b, "euclidean_distance");
  double suma = 0.0;
  for (std::size_t i = 0; i < a.size(); ++i) {
    const double d = static_cast<double>(a[i]) - static_cast<double>(b[i]);
    suma += d * d;
  }
  return std::sqrt(suma);
}

double dot_product(std::span<const float> a, std::span<const float> b) {
  validar(a, b, "dot_product");
  double suma = 0.0;
  for (std::size_t i = 0; i < a.size(); ++i) {
    suma += static_cast<double>(a[i]) * static_cast<double>(b[i]);
  }
  return suma;
}

double cosine_similarity(std::span<const float> a, std::span<const float> b) {
  validar(a, b, "cosine_similarity");
  // Una sola pasada para las tres sumas.
  double punto = 0.0;
  double norma_a = 0.0;
  double norma_b = 0.0;
  for (std::size_t i = 0; i < a.size(); ++i) {
    const double x = a[i];
    const double y = b[i];
    punto += x * y;
    norma_a += x * x;
    norma_b += y * y;
  }
  // Norma cero: el coseno no esta definido (0/0). Se fija en 0, ni parecido
  // ni opuesto; ver la cabecera.
  if (norma_a == 0.0 || norma_b == 0.0) return 0.0;

  const double coseno = punto / (std::sqrt(norma_a) * std::sqrt(norma_b));
  // El redondeo puede pasarse un ulp de [-1, 1] con vectores paralelos.
  if (coseno > 1.0) return 1.0;
  if (coseno < -1.0) return -1.0;
  return coseno;
}

double vector_similarity(std::span<const float> a, std::span<const float> b,
                         VectorMetric metric) {
  switch (metric) {
    case VectorMetric::kEuclidean:
      return euclidean_distance(a, b);
    case VectorMetric::kDotProduct:
      return dot_product(a, b);
    case VectorMetric::kCosine:
      return cosine_similarity(a, b);
  }
  throw InvalidRecord("metrica vectorial desconocida");
}

double vector_distance(std::span<const float> a, std::span<const float> b,
                       VectorMetric metric) {
  switch (metric) {
    case VectorMetric::kEuclidean:
      return euclidean_distance(a, b);
    case VectorMetric::kDotProduct:
      return -dot_product(a, b);
    case VectorMetric::kCosine:
      return 1.0 - cosine_similarity(a, b);
  }
  throw InvalidRecord("metrica vectorial desconocida");
}

double from_distance(double distance, VectorMetric metric) noexcept {
  switch (metric) {
    case VectorMetric::kEuclidean:
      return distance;
    case VectorMetric::kDotProduct:
      return -distance;
    case VectorMetric::kCosine:
      return 1.0 - distance;
  }
  return distance;
}

}  // namespace quipudb
