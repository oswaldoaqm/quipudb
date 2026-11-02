#include "quipudb/index/vector_scan.hpp"

#include <algorithm>
#include <queue>
#include <string>
#include <utility>

#include "quipudb/error.hpp"

namespace quipudb {

namespace {

/// Posicion de la columna VECTOR en el esquema, o SchemaError.
std::size_t columna_vector(const TableFile& table, std::string_view column) {
  const Schema& schema = table.schema();
  const auto position = schema.find(column);
  if (!position) {
    throw SchemaError("la columna " + std::string(column) + " no existe en " +
                      schema.table_name);
  }
  if (schema.columns[*position].type != DataType::Vector) {
    throw SchemaError("una busqueda por similitud requiere una columna VECTOR; " +
                      std::string(column) + " es " +
                      std::string(to_string(schema.columns[*position].type)));
  }
  return *position;
}

/// Orden total de los resultados: distancia y, si empatan, RID.
bool antes(const VectorMatch& a, const VectorMatch& b) noexcept {
  if (a.distance != b.distance) return a.distance < b.distance;
  return a.rid < b.rid;
}

}  // namespace

std::vector<VectorMatch> scan_k_nearest_vector(TableFile& table, std::string_view column,
                                               std::span<const float> query, std::size_t k,
                                               VectorMetric metric) {
  const std::size_t posicion = columna_vector(table, column);
  const std::size_t dim = table.schema().columns[posicion].length;
  // La consulta se valida antes de leer la tabla: dimension y componentes
  // finitos, con las mismas reglas que las metricas.
  if (query.size() != dim) {
    throw InvalidRecord("la consulta tiene dimension " + std::to_string(query.size()) +
                        " y la columna " + std::string(column) + " es VECTOR(" +
                        std::to_string(dim) + ")");
  }
  (void)vector_distance(query, query, metric);

  std::vector<VectorMatch> out;
  if (k == 0) return out;

  // Un max-heap con los k mejores vistos hasta ahora: arriba queda el peor, y
  // un vector nuevo solo entra si va antes que el. La memoria es O(k) y no
  // O(N), que es lo que costaria ordenar la tabla entera.
  auto peor_arriba = [](const VectorMatch& a, const VectorMatch& b) { return antes(a, b); };
  std::priority_queue<VectorMatch, std::vector<VectorMatch>, decltype(peor_arriba)> mejores(
      peor_arriba);

  auto cursor = table.cursor();
  Record record;
  while (cursor->next(record)) {
    const auto& v = std::get<VectorValue>(record[posicion]);
    const VectorMatch candidato{cursor->rid(), vector_distance(query, v, metric)};
    if (mejores.size() < k) {
      mejores.push(candidato);
    } else if (antes(candidato, mejores.top())) {
      mejores.pop();
      mejores.push(candidato);
    }
  }

  out.resize(mejores.size());
  // El heap sale del peor al mejor: se llena desde el final.
  for (auto it = out.rbegin(); it != out.rend(); ++it) {
    *it = mejores.top();
    mejores.pop();
  }
  return out;
}

}  // namespace quipudb
