#pragma once

// Adaptador del IVF al contrato comun de indices secundarios (issue #184).
//
// Es lo que permite que el catalogo registre un IVF, que `Database` lo cree y
// lo reabra, y que el executor lo mantenga al insertar y borrar filas, igual
// que `RTreeIndex` con el R-Tree. La busqueda por similitud del #188 llama a
// `k_nearest`.
//
// A diferencia de los otros indices, un IVF no se puede crear solo con la
// columna: necesita los centroides, que salen del K-Means de Python (#178).
// Por eso se crea con `Database::create_ivf_index` y no con `create_index`.
// Al reabrir, los centroides se leen del archivo.
//
// La clave del contrato `Index` es el vector entero. `search(key)` devuelve
// las filas cuyo vector es exactamente igual, que es lo que el executor
// necesita para mantener el indice; la busqueda por parecido es `k_nearest`.

#include <cstddef>
#include <filesystem>
#include <memory>
#include <span>
#include <string_view>
#include <utility>
#include <vector>

#include "quipudb/catalog/table.hpp"
#include "quipudb/index/ivf.hpp"

namespace quipudb {

class IvfIndex final : public Index {
 public:
  /// Abre un IVF existente sobre la columna VECTOR `column` de `data`.
  IvfIndex(std::filesystem::path path, Column column, TableFile& data,
           std::size_t page_size = kDefaultPageSize);

  /// Crea un IVF nuevo con estos centroides. No lo llena: eso es `build`.
  IvfIndex(std::filesystem::path path, Column column, TableFile& data, std::size_t page_size,
           const std::vector<std::vector<float>>& centroids);

  [[nodiscard]] std::string_view kind() const noexcept override { return kind::kIvf; }
  [[nodiscard]] DataType key_type() const noexcept override { return DataType::Vector; }
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

  /// Recorre la tabla con su cursor y agrega todos sus vectores de una vez.
  void build();
  void flush() { ivf_->flush(); }

  /// Los `k` vectores mas parecidos a `query` mirando `nprobe` listas (#184).
  [[nodiscard]] std::vector<VectorMatch> k_nearest(std::span<const float> query, std::size_t k,
                                                   VectorMetric metric, std::size_t nprobe);

  [[nodiscard]] Ivf& ivf() noexcept { return *ivf_; }

 private:
  void validar_columna(const Column& column, const TableFile& data);
  [[nodiscard]] const VectorValue& vector_key(const Key& key) const;

  Column column_;
  TableFile* data_;
  std::size_t key_index_ = 0;
  std::unique_ptr<Ivf> ivf_;
};

}  // namespace quipudb
