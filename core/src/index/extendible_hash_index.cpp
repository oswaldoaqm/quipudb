#include "quipudb/index/extendible_hash_index.hpp"

#include <cstring>

#include "quipudb/error.hpp"

namespace quipudb {

namespace {

std::vector<std::byte> bytes_de(RID rid) {
  std::vector<std::byte> b(ExtendibleHashIndex::kRidSize);
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

ExtendibleHashIndex::ExtendibleHashIndex(std::filesystem::path path, Column column,
                                         TableFile& data, std::size_t page_size,
                                         std::size_t bucket_capacity)
    : column_(std::move(column)),
      data_(&data),
      hash_(std::move(path), column_, kRidSize, page_size, bucket_capacity) {
  // Un RID guardado aqui tiene que seguir valiendo manana, y eso solo lo
  // garantiza el heap file: las demas organizaciones corren los registros de
  // sitio al insertar. Lo mismo que comprueba el indice B+ no agrupado (#16).
  if (data.kind() != kind::kHeap) {
    throw SchemaError("un indice hash necesita un heap file como tabla de datos, y " +
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

void ExtendibleHashIndex::insert(const Key& key, RID rid) {
  if (type_of(key) != column_.type) {
    throw SchemaError("la clave es " + std::string(to_string(type_of(key))) + " y la columna " +
                      column_.name + " es " + std::string(to_string(column_.type)));
  }
  hash_.insert(key, bytes_de(rid));  // admite repetidas: no lanza DuplicateKey
}

std::size_t ExtendibleHashIndex::remove(const Key& key) { return hash_.erase_all(key); }

bool ExtendibleHashIndex::remove(const Key& key, RID rid) {
  return hash_.erase_one(key, bytes_de(rid));
}

void ExtendibleHashIndex::build() {
  // Con cursor y no con scan(): en 100 000 registros la diferencia es
  // materializarlos todos o ninguno. El cursor da ademas el RID de cada uno,
  // que es justamente lo que hay que guardar.
  auto cur = data_->cursor();
  Record r;
  while (cur->next(r)) {
    hash_.insert(r[key_index_], bytes_de(cur->rid()));
  }
}

// ---------------------------------------------------------------------------
// Consulta
// ---------------------------------------------------------------------------

std::vector<RID> ExtendibleHashIndex::search(const Key& key) {
  if (type_of(key) != column_.type) {
    throw SchemaError("la clave es de otro tipo que la columna " + column_.name);
  }
  std::vector<RID> out;
  for (const auto& payload : hash_.find(key)) out.push_back(rid_de(payload));
  return out;
}

std::vector<RID> ExtendibleHashIndex::range_search(const Key& lo, const Key& hi) {
  (void)lo;
  (void)hi;
  // No es una limitacion de esta implementacion: el hash esparce a proposito,
  // asi que claves contiguas caen en buckets sin relacion y no hay "bucket
  // siguiente" que recorrer. Devolver el resultado igual seria peor que
  // lanzar: una consulta que parece funcionar y cuesta un scan completo.
  throw Unsupported("el indice hash sobre " + column_.name +
                    " no soporta busquedas por rango: el hash no conserva el orden de las "
                    "claves. Consulta supports_range() y usa un B+ para rangos");
}

std::vector<std::pair<Key, RID>> ExtendibleHashIndex::scan() {
  std::vector<std::pair<Key, RID>> out;
  out.reserve(hash_.size());
  auto cur = hash_.entries();
  Key k;
  std::vector<std::byte> payload;
  while (cur->next(k, payload)) out.emplace_back(k, rid_de(payload));
  return out;
}

std::vector<Record> ExtendibleHashIndex::lookup(const Key& key) {
  const auto rids = search(key);
  std::vector<Record> out;
  out.reserve(rids.size());
  for (const RID rid : rids) {
    // Un puntero que no resuelve significa que la tabla y el indice se
    // desincronizaron: mejor decirlo que devolver de menos en silencio.
    auto r = data_->read(rid);
    if (!r) {
      throw IoError("el indice hash sobre " + column_.name +
                    " apunta a un registro que ya no esta");
    }
    out.push_back(std::move(*r));
  }
  return out;
}

}  // namespace quipudb
