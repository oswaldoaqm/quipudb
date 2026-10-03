#include "quipudb/index/spatial_scan.hpp"

#include <algorithm>
#include <cmath>
#include <queue>
#include <string>
#include <utility>

#include "quipudb/error.hpp"

namespace quipudb {

namespace {

/// Posicion de la columna POINT en el esquema, o SchemaError.
std::size_t columna_point(const TableFile& table, std::string_view column) {
  const Schema& schema = table.schema();
  const auto position = schema.find(column);
  if (!position) {
    throw SchemaError("la columna " + std::string(column) + " no existe en " +
                      schema.table_name);
  }
  if (schema.columns[*position].type != DataType::Point) {
    throw SchemaError("una busqueda espacial requiere una columna POINT; " +
                      std::string(column) + " es " +
                      std::string(to_string(schema.columns[*position].type)));
  }
  return *position;
}

/// Misma conversion que el R-Tree: x es la longitud e y la latitud.
Point como_punto(const GeoPoint& point) noexcept { return Point{point.longitude, point.latitude}; }

}  // namespace

std::vector<RID> scan_radius(TableFile& table, std::string_view column, const GeoPoint& center,
                             double radius, Metric metric) {
  const std::size_t posicion = columna_point(table, column);
  const Point centro = como_punto(center);
  // Valida centro y radio con las mismas reglas que el R-Tree antes de leer.
  (void)bounding_box(centro, radius, metric);
  std::vector<RID> out;
  if (radius < 0.0) return out;

  auto cursor = table.cursor();
  Record record;
  while (cursor->next(record)) {
    const Point p = como_punto(std::get<GeoPoint>(record[posicion]));
    if (distance(centro, p, metric) <= radius) out.push_back(cursor->rid());
  }
  return out;
}

std::vector<RID> scan_k_nearest(TableFile& table, std::string_view column, const GeoPoint& center,
                                std::size_t k, Metric metric) {
  const std::size_t posicion = columna_point(table, column);
  const Point centro = como_punto(center);
  (void)distance(centro, centro, metric);
  std::vector<RID> out;
  if (k == 0) return out;

  // Un max-heap con los k mejores vistos hasta ahora: el de arriba es el peor,
  // y un punto nuevo solo entra si esta mas cerca que el. Asi la memoria es
  // O(k) y no O(N), que es lo que haria ordenar la tabla entera.
  std::priority_queue<std::pair<double, RID>> mejores;
  auto cursor = table.cursor();
  Record record;
  while (cursor->next(record)) {
    const double d = distance(centro, como_punto(std::get<GeoPoint>(record[posicion])), metric);
    if (mejores.size() < k) {
      mejores.emplace(d, cursor->rid());
    } else if (d < mejores.top().first) {
      mejores.pop();
      mejores.emplace(d, cursor->rid());
    }
  }

  out.resize(mejores.size());
  // El heap sale de mas lejos a mas cerca: se llena el vector desde el final.
  for (auto it = out.rbegin(); it != out.rend(); ++it) {
    *it = mejores.top().second;
    mejores.pop();
  }
  return out;
}

}  // namespace quipudb
