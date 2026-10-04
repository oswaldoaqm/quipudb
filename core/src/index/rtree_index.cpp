#include "quipudb/index/rtree_index.hpp"

#include <string>
#include <utility>

#include "quipudb/error.hpp"

namespace quipudb {

RTreeIndex::RTreeIndex(std::filesystem::path path, Column column, TableFile& data,
                       std::size_t page_size)
    : column_(std::move(column)), data_(&data), tree_(std::move(path), page_size) {
  if (data.kind() != kind::kHeap) {
    throw SchemaError("un indice R-Tree necesita un heap file como tabla de datos, y " +
                      data.schema().table_name + " se almacena como " +
                      std::string(data.kind()) + ", cuyos RID no son estables");
  }
  const auto position = data.schema().find(column_.name);
  if (!position) {
    throw SchemaError("la columna " + column_.name + " no existe en " +
                      data.schema().table_name);
  }
  key_index_ = *position;
  const Column& real = data.schema().columns[key_index_];
  if (real.type != column_.type || real.length != column_.length) {
    throw SchemaError("la columna " + column_.name + " de " + data.schema().table_name +
                      " no coincide con la que se pidio indexar");
  }
  if (column_.type != DataType::Point) {
    throw SchemaError("un indice R-Tree requiere una columna POINT; " + column_.name + " es " +
                      std::string(to_string(column_.type)));
  }
}

Point RTreeIndex::to_point(const GeoPoint& point) noexcept {
  return Point{point.longitude, point.latitude};
}

GeoPoint RTreeIndex::to_geo_point(Point point) noexcept {
  return GeoPoint{point.y, point.x};
}

const GeoPoint& RTreeIndex::point_key(const Key& key) const {
  if (!std::holds_alternative<GeoPoint>(key)) {
    throw SchemaError("el indice R-Tree sobre " + column_.name + " esperaba POINT y recibio " +
                      std::string(to_string(type_of(key))));
  }
  return std::get<GeoPoint>(key);
}

void RTreeIndex::insert(const Key& key, RID rid) { tree_.insert(to_point(point_key(key)), rid); }

std::size_t RTreeIndex::remove(const Key& key) {
  const Point point = to_point(point_key(key));
  const auto entries = tree_.search(Rect::of(point));
  std::size_t removed = 0;
  for (const auto& entry : entries) {
    if (entry.point == point && tree_.remove(point, entry.rid)) ++removed;
  }
  return removed;
}

bool RTreeIndex::remove(const Key& key, RID rid) {
  return tree_.remove(to_point(point_key(key)), rid);
}

std::vector<RID> RTreeIndex::search(const Key& key) {
  const Point point = to_point(point_key(key));
  const auto entries = tree_.search(Rect::of(point));
  std::vector<RID> out;
  out.reserve(entries.size());
  for (const auto& entry : entries) {
    if (entry.point == point) out.push_back(entry.rid);
  }
  return out;
}

std::vector<RID> RTreeIndex::range_search(const Key&, const Key&) {
  throw Unsupported("el R-Tree no implementa range_search escalar; use search_radius");
}

std::vector<std::pair<Key, RID>> RTreeIndex::scan() {
  const auto entries = tree_.scan();
  std::vector<std::pair<Key, RID>> out;
  out.reserve(entries.size());
  for (const auto& entry : entries) {
    out.emplace_back(Key{to_geo_point(entry.point)}, entry.rid);
  }
  return out;
}

std::size_t RTreeIndex::size() const { return static_cast<std::size_t>(tree_.size()); }

const OpStats& RTreeIndex::stats() const noexcept { return tree_.stats(); }

void RTreeIndex::reset_stats() noexcept { tree_.reset_stats(); }

std::vector<RID> RTreeIndex::search_radius(const GeoPoint& center, double radius,
                                           Metric metric) {
  const auto entries = tree_.search_radius(to_point(center), radius, metric);
  std::vector<RID> out;
  out.reserve(entries.size());
  for (const auto& entry : entries) out.push_back(entry.rid);
  return out;
}

std::vector<RID> RTreeIndex::k_nearest(const GeoPoint& center, std::size_t k, Metric metric) {
  const auto entries = tree_.k_nearest(to_point(center), k, metric);
  std::vector<RID> out;
  out.reserve(entries.size());
  for (const auto& entry : entries) out.push_back(entry.rid);
  return out;
}

std::vector<RID> RTreeIndex::search_polygon(const std::vector<GeoPoint>& vertices) {
  std::vector<Point> poligono;
  poligono.reserve(vertices.size());
  for (const auto& vertex : vertices) poligono.push_back(to_point(vertex));
  const auto entries = tree_.search_polygon(poligono);
  std::vector<RID> out;
  out.reserve(entries.size());
  for (const auto& entry : entries) out.push_back(entry.rid);
  return out;
}

void RTreeIndex::build() {
  auto cursor = data_->cursor();
  Record record;
  while (cursor->next(record)) insert(record[key_index_], cursor->rid());
}

void RTreeIndex::flush() { tree_.flush(); }

}  // namespace quipudb
