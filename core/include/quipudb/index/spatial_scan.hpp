#pragma once

// Busqueda espacial secuencial (issue #131): la linea base del 2.2.4.
//
// Que es aqui
// -----------
//
//   Las mismas dos consultas que el R-Tree -- radio y k-NN -- resueltas sin
//   indice: se recorre la tabla entera con su cursor y se mide la distancia a
//   cada punto. Es lo que haria el motor sin un R-Tree, y es contra lo que el
//   2.2.4 compara al indice.
//
//   Va en C++ y no en Python a proposito. Si el recorrido se hiciera en Python
//   y el R-Tree en C++, la comparacion mediria el interprete y no el acceso a
//   disco: las dos tecnicas tienen que pagar el mismo costo por punto
//   examinado para que la diferencia sea la poda.
//
// Contrato
// --------
//
//   Identico al de `RTree::search_radius` y `RTree::k_nearest`, para que los
//   resultados se puedan comparar uno a uno:
//
//     - el radio va en la unidad de la metrica (metros con Haversine) y el
//       borde entra;
//     - un radio negativo o k = 0 devuelven vacio sin leer la tabla;
//     - el k-NN sale de mas cerca a mas lejos, y con empates cual entra es
//       indistinto.
//
//   Devuelve RID, como el indice, y no registros: asi las dos tecnicas
//   entregan lo mismo y ninguna paga una lectura extra que la otra no paga.
//   Las paginas leidas quedan en `table.stats()`, porque las cuenta el cursor.

#include <cstddef>
#include <string_view>
#include <vector>

#include "quipudb/catalog/table.hpp"
#include "quipudb/index/distance.hpp"

namespace quipudb {

/// RIDs de los registros cuya columna POINT `column` esta a `radius` o menos de
/// `center`. Lanza SchemaError si la columna no existe o no es POINT, e
/// InvalidRecord si el centro o el radio no son finitos.
[[nodiscard]] std::vector<RID> scan_radius(TableFile& table, std::string_view column,
                                           const GeoPoint& center, double radius,
                                           Metric metric);

/// RIDs de los `k` registros mas cercanos a `center` segun la columna POINT
/// `column`, de mas cerca a mas lejos. Con menos de `k` registros devuelve
/// todos. Mismos errores que `scan_radius`.
[[nodiscard]] std::vector<RID> scan_k_nearest(TableFile& table, std::string_view column,
                                              const GeoPoint& center, std::size_t k,
                                              Metric metric);

/// RIDs de los registros cuya columna POINT `column` cae dentro del poligono,
/// bordes incluidos, con el mismo criterio que `contains_point`. Lanza
/// SchemaError si la columna no existe o no es POINT, e InvalidRecord con menos
/// de tres vertices o alguno no finito.
[[nodiscard]] std::vector<RID> scan_polygon(TableFile& table, std::string_view column,
                                            const std::vector<GeoPoint>& vertices);

}  // namespace quipudb
