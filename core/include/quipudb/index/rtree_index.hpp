#pragma once

// Adaptador del R-Tree espacial al contrato comun de indices secundarios.

#include <cstddef>
#include <filesystem>
#include <string_view>
#include <utility>
#include <vector>

#include "quipudb/catalog/table.hpp"
#include "quipudb/index/distance.hpp"
#include "quipudb/index/rtree.hpp"

namespace quipudb {

class RTreeIndex final : public Index {
 public:
  RTreeIndex(std::filesystem::path path, Column column, TableFile& data,
             std::size_t page_size = kDefaultPageSize);

  [[nodiscard]] std::string_view kind() const noexcept override { return kind::kRTree; }
  [[nodiscard]] DataType key_type() const noexcept override { return DataType::Point; }
  [[nodiscard]] bool supports_range() const noexcept override { return false; }

  void insert(const Key& key, RID rid) override;
  std::size_t remove(const Key& key) override;
  bool remove(const Key& key, RID rid) override;
  [[nodiscard]] std::vector<RID> search(const Key& key) override;
  [[nodiscard]] std::vector<RID> range_search(const Key& lo, const Key& hi) override;
  [[nodiscard]] std::vector<std::pair<Key, RID>> scan() override;
  [[nodiscard]] std::size_t size() const override;
  [[nodiscard]] const OpStats& stats() const noexcept override;
  void reset_stats() noexcept override;

  [[nodiscard]] std::vector<RID> search_radius(const GeoPoint& center, double radius,
                                               Metric metric);
  void build();
  void flush();

 private:
  [[nodiscard]] static Point to_point(const GeoPoint& point) noexcept;
  [[nodiscard]] static GeoPoint to_geo_point(Point point) noexcept;
  [[nodiscard]] const GeoPoint& point_key(const Key& key) const;

  Column column_;
  std::size_t key_index_ = 0;
  TableFile* data_;
  RTree tree_;
};

}  // namespace quipudb
