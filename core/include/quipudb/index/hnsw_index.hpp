#pragma once

// Adaptador del HNSW al contrato comun de indices secundarios (issue #186).
//
// Igual que `IvfIndex` con el IVF: deja que el catalogo registre un HNSW, que
// `Database` lo cree y lo reabra, y que el executor lo mantenga al insertar y
// borrar filas. La busqueda por similitud del #188 llama a `k_nearest`.
//
// Un HNSW se crea con una metrica fija y con M y efConstruction, que
// `create_index` no recibe; por eso se crea con `Database::create_hnsw_index`.
// Al reabrir, todo eso se lee del archivo.
//
// La clave del contrato `Index` es el vector entero. `search(key)` devuelve
// las filas cuyo vector es exactamente igual; como el grafo no esta ordenado
// por clave, recorre todos los nodos. El executor no la usa para mantener el
// indice: borra con `remove(key, rid)`, que va directo al nodo por su RID.

#include <cstddef>
#include <filesystem>
#include <memory>
#include <span>
#include <string_view>
#include <utility>
#include <vector>

#include "quipudb/catalog/table.hpp"
#include "quipudb/index/hnsw.hpp"

namespace quipudb {

class HnswIndex final : public Index {
 public:
  /// Abre un HNSW existente sobre la columna VECTOR `column` de `data`.
  HnswIndex(std::filesystem::path path, Column column, TableFile& data,
            std::size_t page_size = kDefaultPageSize);

  /// Crea un HNSW nuevo con esta metrica y estos parametros. No lo llena: eso
  /// es `build`.
  HnswIndex(std::filesystem::path path, Column column, TableFile& data, std::size_t page_size,
            VectorMetric metric, HnswParams params);

  [[nodiscard]] std::string_view kind() const noexcept override { return kind::kHnsw; }
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

  /// Los `k` vectores mas parecidos a `query` con ef_search candidatos (#186).
  [[nodiscard]] std::vector<VectorMatch> k_nearest(std::span<const float> query, std::size_t k,
                                                   VectorMetric metric, std::size_t ef_search);

  [[nodiscard]] Hnsw& hnsw() noexcept { return *hnsw_; }
  void flush() { hnsw_->flush(); }

 private:
  void validar_columna(const Column& column, const TableFile& data);
  [[nodiscard]] const VectorValue& vector_key(const Key& key) const;

  Column column_;
  TableFile* data_;
  std::size_t key_index_ = 0;
  std::unique_ptr<Hnsw> hnsw_;
};

}  // namespace quipudb
