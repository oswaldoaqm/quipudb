#include "quipudb/index/ivf_index.hpp"

#include <string>
#include <variant>

#include "quipudb/error.hpp"

namespace quipudb {

void IvfIndex::validar_columna(const Column& column, const TableFile& data) {
  if (data.kind() != kind::kHeap) {
    throw SchemaError("un indice IVF necesita un heap file como tabla de datos, y " +
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
    throw SchemaError("un indice IVF requiere una columna VECTOR; " + column.name + " es " +
                      std::string(to_string(column.type)));
  }
}

IvfIndex::IvfIndex(std::filesystem::path path, Column column, TableFile& data,
                   std::size_t page_size)
    : column_(std::move(column)), data_(&data) {
  validar_columna(column_, data);
  ivf_ = Ivf::open(path, page_size);
  if (ivf_->dim() != column_.length) {
    throw IoError("el IVF de " + column_.name + " es de dimension " +
                  std::to_string(ivf_->dim()) + " y la columna es VECTOR(" +
                  std::to_string(column_.length) + ")");
  }
}

IvfIndex::IvfIndex(std::filesystem::path path, Column column, TableFile& data,
                   std::size_t page_size, const std::vector<std::vector<float>>& centroids)
    : column_(std::move(column)), data_(&data) {
  validar_columna(column_, data);
  for (const auto& c : centroids) {
    if (c.size() != column_.length) {
      throw SchemaError("los centroides son de dimension " + std::to_string(c.size()) +
                        " y la columna " + column_.name + " es VECTOR(" +
                        std::to_string(column_.length) + ")");
    }
  }
  ivf_ = Ivf::create(path, centroids, page_size);
}

const VectorValue& IvfIndex::vector_key(const Key& key) const {
  if (!std::holds_alternative<VectorValue>(key)) {
    throw SchemaError("el indice IVF sobre " + column_.name + " esperaba VECTOR y recibio " +
                      std::string(to_string(type_of(key))));
  }
  return std::get<VectorValue>(key);
}

void IvfIndex::build() {
  std::vector<RID> rids;
  std::vector<float> plano;
  auto cursor = data_->cursor();
  Record record;
  while (cursor->next(record)) {
    const auto& v = std::get<VectorValue>(record[key_index_]);
    rids.push_back(cursor->rid());
    plano.insert(plano.end(), v.begin(), v.end());
  }
  ivf_->build(rids, plano);
}

void IvfIndex::insert(const Key& key, RID rid) { ivf_->insert(rid, vector_key(key)); }

std::size_t IvfIndex::remove(const Key& key) {
  std::size_t quitadas = 0;
  for (const RID rid : search(key)) {
    if (ivf_->remove(rid, vector_key(key))) ++quitadas;
  }
  return quitadas;
}

bool IvfIndex::remove(const Key& key, RID rid) { return ivf_->remove(rid, vector_key(key)); }

std::vector<RID> IvfIndex::search(const Key& key) {
  const VectorValue& v = vector_key(key);
  std::vector<RID> out;
  for (const auto& [rid, guardado] : ivf_->list_entries(ivf_->nearest_list(v))) {
    if (guardado == v) out.push_back(rid);
  }
  return out;
}

std::vector<RID> IvfIndex::range_search(const Key&, const Key&) {
  throw Unsupported("el IVF no ordena sus vectores: no hay range_search; use k_nearest");
}

std::vector<std::pair<Key, RID>> IvfIndex::scan() {
  std::vector<std::pair<Key, RID>> out;
  for (std::size_t list = 0; list < ivf_->nlist(); ++list) {
    for (auto& [rid, v] : ivf_->list_entries(list)) out.emplace_back(Key{std::move(v)}, rid);
  }
  return out;
}

std::size_t IvfIndex::size() const { return static_cast<std::size_t>(ivf_->size()); }

const OpStats& IvfIndex::stats() const noexcept { return ivf_->stats(); }

void IvfIndex::reset_stats() noexcept { ivf_->reset_stats(); }

std::vector<VectorMatch> IvfIndex::k_nearest(std::span<const float> query, std::size_t k,
                                             VectorMetric metric, std::size_t nprobe) {
  return ivf_->search(query, k, metric, nprobe);
}

}  // namespace quipudb
