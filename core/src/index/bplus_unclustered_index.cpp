#include "quipudb/index/bplus_unclustered_index.hpp"

#include <array>
#include <cstring>

#include "quipudb/error.hpp"

namespace quipudb {

namespace {

std::vector<std::byte> bytes_de(RID rid) {
  std::vector<std::byte> b(BPlusUnclusteredIndex::kRidSize);
  std::memcpy(b.data(), &rid.page, sizeof rid.page);
  std::memcpy(b.data() + sizeof rid.page, &rid.slot, sizeof rid.slot);
  return b;
}

RID rid_de(std::span<const std::byte> b) {
  RID rid;
  std::memcpy(&rid.page, b.data(), sizeof rid.page);
  std::memcpy(&rid.slot, b.data() + sizeof rid.page, sizeof rid.slot);
  return rid;
}

}  // namespace

BPlusUnclusteredIndex::BPlusUnclusteredIndex(std::filesystem::path path, Column column,
                                             TableFile& data, std::size_t page_size,
                                             std::size_t order)
    : column_(std::move(column)),
      data_(&data),
      tree_(std::move(path), column_, kRidSize, page_size, order, /*unique=*/false) {
  // Un RID guardado aqui tiene que seguir valiendo manana, y eso solo lo
  // garantiza el heap file: las demas organizaciones corren los registros de
  // sitio al insertar.
  if (data.kind() != kind::kHeap) {
    throw SchemaError("un indice no agrupado necesita un heap file como tabla de datos, y " +
                      data.schema().table_name + " se almacena como " + std::string(data.kind()) +
                      ", cuyos RID no son estables");
  }
  const auto pos = data.schema().find(column_.name);
  if (!pos) {
    throw SchemaError("la columna " + column_.name + " no existe en " +
                      data.schema().table_name);
  }
  key_index_ = *pos;
  const Column& real = data.schema().columns[key_index_];
  if (real.type != column_.type || real.length != column_.length) {
    throw SchemaError("la columna " + column_.name + " de " + data.schema().table_name +
                      " no coincide con la que se pidio indexar");
  }
}

// ---------------------------------------------------------------------------
// Mantenimiento
// ---------------------------------------------------------------------------

void BPlusUnclusteredIndex::insert(const Key& key, RID rid) {
  if (type_of(key) != column_.type) {
    throw SchemaError("la clave es " + std::string(to_string(type_of(key))) + " y la columna " +
                      column_.name + " es " + std::string(to_string(column_.type)));
  }
  tree_.insert(key, bytes_de(rid));  // admite repetidas: no lanza DuplicateKey
}

std::size_t BPlusUnclusteredIndex::remove(const Key& key) { return tree_.erase_all(key); }

bool BPlusUnclusteredIndex::remove(const Key& key, RID rid) {
  return tree_.erase_one(key, bytes_de(rid));
}

void BPlusUnclusteredIndex::build() {
  // Se recorre la tabla con un cursor, no con scan(): en 100 000 registros la
  // diferencia es materializarlos todos o ninguno. El cursor da el RID de
  // cada registro, que es justamente lo que hay que guardar.
  auto cur = data_->cursor();
  Record r;
  while (cur->next(r)) {
    tree_.insert(r[key_index_], bytes_de(cur->rid()));
  }
}

// ---------------------------------------------------------------------------
// Consulta
// ---------------------------------------------------------------------------

std::vector<RID> BPlusUnclusteredIndex::search(const Key& key) {
  std::vector<RID> out;
  if (type_of(key) != column_.type) {
    throw SchemaError("la clave es de otro tipo que la columna " + column_.name);
  }
  // Se baja una vez y se sigue la cadena de hojas mientras la clave repita.
  auto cur = tree_.entries_from(key);
  Key k;
  std::vector<std::byte> payload;
  while (cur->next(k, payload)) {
    if (compare(k, key) != 0) break;
    out.push_back(rid_de(payload));
  }
  return out;
}

std::vector<RID> BPlusUnclusteredIndex::range_search(const Key& lo, const Key& hi) {
  std::vector<RID> out;
  if (compare(lo, hi) > 0) return out;
  auto cur = tree_.entries_from(lo);
  Key k;
  std::vector<std::byte> payload;
  while (cur->next(k, payload)) {
    if (compare(k, hi) > 0) break;
    out.push_back(rid_de(payload));
  }
  return out;
}

std::vector<std::pair<Key, RID>> BPlusUnclusteredIndex::scan() {
  std::vector<std::pair<Key, RID>> out;
  out.reserve(tree_.size());
  auto cur = tree_.entries();
  Key k;
  std::vector<std::byte> payload;
  while (cur->next(k, payload)) out.emplace_back(k, rid_de(payload));
  return out;
}

std::vector<Record> BPlusUnclusteredIndex::resolver(const std::vector<RID>& rids) {
  std::vector<Record> out;
  out.reserve(rids.size());
  for (const RID rid : rids) {
    // Un puntero que no resuelve significa que la tabla y el indice se
    // desincronizaron: mejor decirlo que devolver de menos en silencio.
    auto r = data_->read(rid);
    if (!r) {
      throw IoError("el indice sobre " + column_.name + " apunta a un registro que ya no esta");
    }
    out.push_back(std::move(*r));
  }
  return out;
}

std::vector<Record> BPlusUnclusteredIndex::lookup(const Key& key) { return resolver(search(key)); }

std::vector<Record> BPlusUnclusteredIndex::lookup_range(const Key& lo, const Key& hi) {
  return resolver(range_search(lo, hi));
}

}  // namespace quipudb
