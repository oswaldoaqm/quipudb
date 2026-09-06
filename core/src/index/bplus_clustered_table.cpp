#include "quipudb/index/bplus_clustered_table.hpp"

#include "quipudb/error.hpp"

namespace quipudb {

BPlusClusteredTable::BPlusClusteredTable(std::filesystem::path path, Schema schema,
                                         std::size_t page_size, std::size_t order)
    : codec_(std::move(schema)),
      tree_(std::move(path), codec_.schema().key(), codec_.size(), page_size, order) {}

// ---------------------------------------------------------------------------
// Escritura
// ---------------------------------------------------------------------------

RID BPlusClusteredTable::insert(const Record& record) {
  codec_.schema().validate(record);
  const auto bytes = codec_.encode(record);
  const Key& key = codec_.schema().key_of(record);
  tree_.insert(key, bytes);  // lanza DuplicateKey si ya esta
  // Donde quedo: el arbol acaba de reacomodar, asi que hay que preguntarlo.
  const auto rid = tree_.locate(key);
  if (!rid) throw IoError("el registro recien insertado no aparece en el arbol");
  return *rid;
}

std::size_t BPlusClusteredTable::remove(const Key& key) { return tree_.erase(key) ? 1u : 0u; }

std::size_t BPlusClusteredTable::update(const Key& key, const Record& record) {
  codec_.schema().validate(record);
  if (compare(codec_.schema().key_of(record), key) != 0) {
    throw SchemaError("update no puede cambiar la clave primaria de " +
                      codec_.schema().table_name + ": eso es remove mas insert");
  }
  // La clave no cambia, asi que la entrada se queda en su hoja y solo se
  // reescribe el payload: la estructura del arbol no se toca.
  return tree_.set_payload(key, codec_.encode(record)) ? 1u : 0u;
}

// ---------------------------------------------------------------------------
// Lectura
// ---------------------------------------------------------------------------

std::vector<Record> BPlusClusteredTable::search(const Key& key) {
  std::vector<Record> out;
  if (auto p = tree_.find(key)) out.push_back(codec_.decode(*p));
  return out;
}

std::vector<Record> BPlusClusteredTable::range_search(const Key& lo, const Key& hi) {
  std::vector<Record> out;
  if (compare(lo, hi) > 0) return out;
  // Se baja una sola vez y despues se sigue la cadena de hojas.
  auto cur = tree_.entries_from(lo);
  Key k;
  std::vector<std::byte> payload;
  while (cur->next(k, payload)) {
    if (compare(k, hi) > 0) break;
    out.push_back(codec_.decode(payload));
  }
  return out;
}

std::vector<Record> BPlusClusteredTable::scan() {
  std::vector<Record> out;
  out.reserve(tree_.size());
  auto cur = tree_.entries();
  Key k;
  std::vector<std::byte> payload;
  while (cur->next(k, payload)) out.push_back(codec_.decode(payload));
  return out;
}

class BPlusClusteredTable::Cursor final : public RecordCursor {
 public:
  Cursor(const RecordCodec& codec, std::unique_ptr<EntryCursor> entradas)
      : codec_(codec), entradas_(std::move(entradas)) {}

  bool next(Record& out) override {
    Key k;
    if (!entradas_->next(k, payload_)) return false;
    out = codec_.decode(payload_);
    return true;
  }

  [[nodiscard]] RID rid() const override { return entradas_->position(); }

 private:
  const RecordCodec& codec_;
  std::unique_ptr<EntryCursor> entradas_;
  std::vector<std::byte> payload_;
};

std::unique_ptr<RecordCursor> BPlusClusteredTable::cursor() {
  return std::make_unique<Cursor>(codec_, tree_.entries());
}

std::optional<Record> BPlusClusteredTable::read(RID rid) {
  const auto p = tree_.payload_at_rid(rid);
  if (!p) return std::nullopt;
  return codec_.decode(*p);
}

}  // namespace quipudb
