#pragma once

// Busqueda k-NN exacta sobre una columna VECTOR (issue #182): la linea base
// de la Parte 4.
//
// Recorre la tabla entera con su cursor, compara la consulta con cada vector
// y se queda con los k mas cercanos. Es la respuesta correcta contra la que se
// mide el Recall@10 de IVF (#184) y HNSW (#186), y lo que usa el motor cuando
// una consulta no tiene indice. Es el equivalente de `scan_k_nearest` de
// `spatial_scan.hpp` (#131).
//
// Va en C++ por la misma razon que aquel: si la busqueda exacta corriera en
// Python y los indices en C++, el benchmark mediria el interprete y no lo que
// cada tecnica se ahorra de comparar.
//
// Contrato
// --------
//
//   - Devuelve `VectorMatch` (RID y distancia) y no registros, igual que IVF y
//     HNSW, para que los resultados se comparen uno a uno.
//   - La distancia es la de `vector_distance` (#179): menor es mejor en las
//     tres metricas. `from_distance` da el valor que ve el usuario.
//   - Salen de mas cerca a mas lejos. Con empates de distancia se ordena por
//     RID, para que dos corridas den exactamente la misma lista y el Recall no
//     dependa del orden en que se recorrio la tabla.
//   - k = 0 devuelve vacio sin leer la tabla; con menos de k vectores devuelve
//     todos.
//   - Las paginas leidas y los registros examinados quedan en `table.stats()`,
//     porque los cuenta el cursor.

#include <cstddef>
#include <span>
#include <string_view>
#include <vector>

#include "quipudb/catalog/table.hpp"
#include "quipudb/index/vector_metric.hpp"

namespace quipudb {

/// Un resultado de una busqueda vectorial: donde esta el registro y a que
/// distancia de la consulta, en la escala de `vector_distance`.
struct VectorMatch {
  RID rid;
  double distance = 0.0;

  friend constexpr bool operator==(const VectorMatch&, const VectorMatch&) = default;
};

/// Los `k` registros cuya columna VECTOR `column` esta mas cerca de `query`,
/// de mas cerca a mas lejos. Lanza SchemaError si la columna no existe o no es
/// VECTOR, e InvalidRecord si la consulta no tiene la dimension de la columna o
/// trae componentes no finitos.
[[nodiscard]] std::vector<VectorMatch> scan_k_nearest_vector(TableFile& table,
                                                             std::string_view column,
                                                             std::span<const float> query,
                                                             std::size_t k, VectorMetric metric);

}  // namespace quipudb
