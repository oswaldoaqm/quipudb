#include "quipudb/index/hnsw_index.hpp"

#include <algorithm>
#include <string>
#include <variant>

#include "quipudb/error.hpp"

namespace quipudb {

void HnswIndex::validar_columna(const Column& column, const TableFile& data) {
  if (data.kind() != kind::kHeap) {
    throw SchemaError("un indice HNSW necesita un heap file como tabla de datos, y " +
                      data.schema().table_name + " se almacena como " +
                      std::string(data.kind()) + ", cuyos RID no son estables");
  }
  const auto position = data.schema().find(column.name);
  if (!position) {
    throw SchemaError("la columna " + column.name + " no existe en " + data.schema().table_name);
  }
  key_index_ = *position;
  const Column& real = data.schema().columns[key_index_];
  if (real.type != column.type || real.length != column.length) {
    throw SchemaError("la columna " + column.name + " de " + data.schema().table_name +
                      " no coincide con la que se pidio indexar");
  }
  if (column.type != DataType::Vector) {
    throw SchemaError("un indice HNSW requiere una columna VECTOR; " + column.name + " es " +
                      std::string(to_string(column.type)));
  }
}

HnswIndex::HnswIndex(std::filesystem::path path, Column column, TableFile& data,
                     std::size_t page_size)
    : column_(std::move(column)), data_(&data) {
  validar_columna(column_, data);
  hnsw_ = Hnsw::open(path, page_size);
  if (hnsw_->dim() != column_.length) {
    throw IoError("el HNSW de " + column_.name + " es de dimension " +
                  std::to_string(hnsw_->dim()) + " y la columna es VECTOR(" +
                  std::to_string(column_.length) + ")");
  }
}

HnswIndex::HnswIndex(std::filesystem::path path, Column column, TableFile& data,
                     std::size_t page_size, VectorMetric metric, HnswParams params)
    : column_(std::move(column)), data_(&data) {
  validar_columna(column_, data);
  hnsw_ = Hnsw::create(path, column_.length, metric, params, page_size);
}

const VectorValue& HnswIndex::vector_key(const Key& key) const {
  if (!std::holds_alternative<VectorValue>(key)) {
    throw SchemaError("el indice HNSW sobre " + column_.name + " esperaba VECTOR y recibio " +
                      std::string(to_string(type_of(key))));
  }
  return std::get<VectorValue>(key);
}

void HnswIndex::build() {
  std::vector<RID> rids;
  std::vector<float> plano;
  auto cursor = data_->cursor();
  Record record;
  while (cursor->next(record)) {
    const auto& v = std::get<VectorValue>(record[key_index_]);
    rids.push_back(cursor->rid());
    plano.insert(plano.end(), v.begin(), v.end());
  }
  hnsw_->build(rids, plano);
}

void HnswIndex::insert(const Key& key, RID rid) { hnsw_->insert(rid, vector_key(key)); }

std::size_t HnswIndex::remove(const Key& key) {
  std::size_t quitadas = 0;
  for (const RID rid : search(key)) {
    if (hnsw_->remove(rid, vector_key(key))) ++quitadas;
  }
  return quitadas;
}

bool HnswIndex::remove(const Key& key, RID rid) { return hnsw_->remove(rid, vector_key(key)); }

std::vector<RID> HnswIndex::search(const Key& key) {
  const VectorValue& v = vector_key(key);
  std::vector<RID> out;
  for (std::size_t n = 0; n < hnsw_->size(); ++n) {
    if (hnsw_->deleted(n)) continue;
    const auto guardado = hnsw_->vector(n);
    if (std::equal(guardado.begin(), guardado.end(), v.begin(), v.end())) {
      out.push_back(hnsw_->rid(n));
    }
  }
  return out;
}

std::vector<RID> HnswIndex::range_search(const Key&, const Key&) {
  throw Unsupported("el HNSW no ordena sus vectores: no hay range_search; use k_nearest");
}

std::vector<std::pair<Key, RID>> HnswIndex::scan() {
  std::vector<std::pair<Key, RID>> out;
  for (std::size_t n = 0; n < hnsw_->size(); ++n) {
    if (hnsw_->deleted(n)) continue;
    const auto v = hnsw_->vector(n);
    out.emplace_back(Key{VectorValue(v.begin(), v.end())}, hnsw_->rid(n));
  }
  return out;
}

std::size_t HnswIndex::size() const { return hnsw_->live_size(); }

const OpStats& HnswIndex::stats() const noexcept { return hnsw_->stats(); }

void HnswIndex::reset_stats() noexcept { hnsw_->reset_stats(); }

std::vector<VectorMatch> HnswIndex::k_nearest(std::span<const float> query, std::size_t k,
                                              VectorMetric metric, std::size_t ef_search) {
  return hnsw_->search(query, k, metric, ef_search);
}

}  // namespace quipudb
