#include "quipudb/storage/heap_file.hpp"

#include <cstring>
#include <string>

#include "quipudb/error.hpp"

namespace quipudb {

HeapFile::HeapFile(std::filesystem::path path, Schema schema, std::size_t page_size)
    : codec_(std::move(schema)),
      disk_(std::move(path), page_size),
      scratch_(disk_.page_size()) {
  slot_size_ = codec_.size() + 1;  // 1 byte de estado
  slots_per_page_ = scratch_.body_size() / slot_size_;
  if (slots_per_page_ == 0) {
    throw SchemaError("un registro de " + std::to_string(codec_.size()) +
                      " bytes no entra en una pagina de " + std::to_string(disk_.page_size()));
  }
  load_meta();
  rebuild_keys();
  check_free_list();
}

// ---------------------------------------------------------------------------
// Metadatos
// ---------------------------------------------------------------------------

void HeapFile::load_meta() {
  Meta m;
  std::vector<std::byte> buf(sizeof(Meta));
  disk_.read_meta(buf);
  std::memcpy(&m, buf.data(), sizeof(Meta));

  if (m.version == 0 && m.record_size == 0 && disk_.page_count() == 0) {
    // Archivo recien creado: se inicializa con el esquema actual.
    live_ = 0;
    free_head_ = kInvalidPage;
    free_pages_ = 0;
    save_meta();
    return;
  }
  if (m.version != kMetaVersion) {
    throw IoError("'" + disk_.path().string() + "' usa el formato de heap file " +
                  std::to_string(m.version) + " y este core escribe el " +
                  std::to_string(kMetaVersion) + "; hay que recrear el archivo");
  }
  if (m.record_size != codec_.size()) {
    throw SchemaError("'" + disk_.path().string() + "' guarda registros de " +
                      std::to_string(m.record_size) + " bytes y el esquema pide " +
                      std::to_string(codec_.size()));
  }
  live_ = m.live;
  free_head_ = m.free_head;
  free_pages_ = m.free_pages;
}

void HeapFile::save_meta() {
  Meta m;
  m.version = kMetaVersion;
  m.record_size = static_cast<std::uint32_t>(codec_.size());
  m.live = live_;
  m.free_head = free_head_;
  m.free_pages = free_pages_;
  std::vector<std::byte> buf(sizeof(Meta));
  std::memcpy(buf.data(), &m, sizeof(Meta));
  disk_.write_meta(buf);
}

void HeapFile::check_free_list() {
  // La lista se persiste, no se reconstruye; aqui solo se comprueba que lo
  // que dice el area meta coincida con lo que hay en las paginas. Detecta un
  // archivo corrupto o un ciclo antes de que corrompa una insercion.
  std::uint32_t vistas = 0;
  PageId p = free_head_;
  while (p != kInvalidPage) {
    if (p == 0 || p > disk_.page_count()) {
      throw IoError("la free list de '" + disk_.path().string() + "' apunta a la pagina " +
                    std::to_string(p) + ", que no existe");
    }
    disk_.read_page(p, scratch_);
    if (scratch_.free_space() == 0) {
      throw IoError("la pagina " + std::to_string(p) + " esta en la free list de '" +
                    disk_.path().string() + "' pero no tiene espacio");
    }
    if (++vistas > disk_.page_count()) {
      throw IoError("la free list de '" + disk_.path().string() + "' tiene un ciclo");
    }
    p = scratch_.next();
  }
  if (vistas != free_pages_) {
    throw IoError("'" + disk_.path().string() + "' declara " + std::to_string(free_pages_) +
                  " paginas con espacio y la free list tiene " + std::to_string(vistas));
  }
}

void HeapFile::rebuild_keys() {
  keys_.clear();
  const std::size_t key_col = codec_.schema().key_column;
  for (PageId p = 1; p <= disk_.page_count(); ++p) {
    disk_.read_page(p, scratch_);  // no cuenta en OpStats: es trabajo de apertura
    for (std::size_t s = 0; s < slots_per_page_; ++s) {
      if (!slot_used(s)) continue;
      keys_.emplace(codec_.decode_column(slot_record(s), key_col), RID{p, static_cast<SlotId>(s)});
    }
  }
  if (keys_.size() != live_) {
    throw IoError("'" + disk_.path().string() + "' declara " + std::to_string(live_) +
                  " registros vivos y en las paginas hay " + std::to_string(keys_.size()));
  }
}

void HeapFile::flush() {
  save_meta();
  disk_.flush();
}

// ---------------------------------------------------------------------------
// Acceso a paginas
// ---------------------------------------------------------------------------

void HeapFile::fetch(PageId id) {
  disk_.read_page(id, scratch_);
  ++stats_.pages_read;
}

void HeapFile::store(PageId id) {
  disk_.write_page(id, scratch_);
  ++stats_.pages_written;
}

// ---------------------------------------------------------------------------
// Operaciones
// ---------------------------------------------------------------------------

RID HeapFile::insert(const Record& record) {
  codec_.schema().validate(record);
  const Key& key = codec_.schema().key_of(record);
  if (keys_.contains(key)) {
    throw DuplicateKey("la clave primaria ya existe en " + codec_.schema().table_name);
  }

  // Primero la free list: reutilizar un hueco antes de hacer crecer el
  // archivo es justamente lo que pide el enunciado (2.1.1). Si la lista esta
  // vacia, todas las paginas estan llenas y toca una nueva.
  PageId target = free_head_;
  if (target == kInvalidPage) {
    target = disk_.allocate_page();
    scratch_.clear();
    scratch_.set_free_space(static_cast<std::uint16_t>(slots_per_page_ * slot_size_));
    scratch_.set_next(kInvalidPage);
    free_head_ = target;
    ++free_pages_;
  } else {
    fetch(target);
  }

  // Primer slot libre de la pagina.
  std::size_t slot = slots_per_page_;
  for (std::size_t s = 0; s < slots_per_page_; ++s) {
    if (!slot_used(s)) {
      slot = s;
      break;
    }
  }
  if (slot == slots_per_page_) {
    throw IoError("la pagina " + std::to_string(target) + " esta en la free list y no tiene huecos");
  }

  std::vector<std::byte> bytes(slot_size_);
  bytes[0] = kUsed;
  codec_.encode(record, std::span<std::byte>(bytes).subspan(1));
  scratch_.write_bytes(slot_offset(slot), bytes);
  scratch_.set_record_count(static_cast<std::uint16_t>(scratch_.record_count() + 1));
  scratch_.set_free_space(static_cast<std::uint16_t>(scratch_.free_space() - slot_size_));

  // Si se lleno, sale de la free list. Como siempre se inserta en la cabeza,
  // desenlazarla es O(1).
  if (scratch_.free_space() == 0) {
    free_head_ = scratch_.next();
    scratch_.set_next(kInvalidPage);
    --free_pages_;
  }
  store(target);

  const RID rid{target, static_cast<SlotId>(slot)};
  keys_.emplace(key, rid);
  ++live_;
  save_meta();
  return rid;
}

std::size_t HeapFile::remove(const Key& key) {
  const auto it = keys_.find(key);
  if (it == keys_.end()) return 0;
  const RID rid = it->second;

  fetch(rid.page);
  const bool estaba_llena = scratch_.free_space() == 0;

  std::array<std::byte, 1> libre{kFree};
  scratch_.write_bytes(slot_offset(rid.slot), libre);
  scratch_.set_record_count(static_cast<std::uint16_t>(scratch_.record_count() - 1));
  scratch_.set_free_space(static_cast<std::uint16_t>(scratch_.free_space() + slot_size_));

  // Solo entra a la free list si estaba llena: si ya tenia espacio, ya estaba
  // en la lista y volver a encadenarla crearia un ciclo.
  if (estaba_llena) {
    scratch_.set_next(free_head_);
    free_head_ = rid.page;
    ++free_pages_;
  }
  store(rid.page);

  keys_.erase(it);
  --live_;
  save_meta();
  return 1;
}

std::size_t HeapFile::update(const Key& key, const Record& record) {
  codec_.schema().validate(record);
  if (compare(codec_.schema().key_of(record), key) != 0) {
    throw SchemaError("update no puede cambiar la clave primaria de " +
                      codec_.schema().table_name + ": eso es remove mas insert");
  }
  const auto it = keys_.find(key);
  if (it == keys_.end()) return 0;
  const RID rid = it->second;

  // Longitud fija: se reescribe el mismo slot y el RID se conserva.
  fetch(rid.page);
  std::vector<std::byte> bytes(slot_size_);
  bytes[0] = kUsed;
  codec_.encode(record, std::span<std::byte>(bytes).subspan(1));
  scratch_.write_bytes(slot_offset(rid.slot), bytes);
  store(rid.page);
  return 1;
}

// ---------------------------------------------------------------------------
// Cursor
// ---------------------------------------------------------------------------

class HeapFile::Cursor final : public RecordCursor {
 public:
  explicit Cursor(HeapFile& duenio) : duenio_(duenio), pagina_(duenio.disk_.page_size()) {}

  bool next(Record& out) override {
    while (true) {
      if (slot_ >= duenio_.slots_per_page_) {
        slot_ = 0;
        ++actual_;
        cargada_ = false;
      }
      if (actual_ > duenio_.disk_.page_count()) return false;
      if (!cargada_) {
        duenio_.disk_.read_page(actual_, pagina_);
        ++duenio_.stats_.pages_read;
        cargada_ = true;
      }
      const std::size_t offset = slot_ * duenio_.slot_size_;
      const bool vivo = pagina_.read_bytes(offset, 1)[0] == kUsed;
      ++slot_;
      ++duenio_.stats_.records_examined;
      if (vivo) {
        out = duenio_.codec_.decode(pagina_.read_bytes(offset + 1, duenio_.codec_.size()));
        ++duenio_.stats_.records_returned;
        return true;
      }
    }
  }

 private:
  HeapFile& duenio_;
  Page pagina_;              // buffer propio: no comparte scratch_ con el archivo
  PageId actual_ = 1;
  std::size_t slot_ = 0;
  bool cargada_ = false;
};

std::unique_ptr<RecordCursor> HeapFile::cursor() { return std::make_unique<Cursor>(*this); }

std::vector<Record> HeapFile::search(const Key& key) {
  // Busqueda lineal: es lo que define a un heap file y la linea base del 2.1.6.
  const std::size_t key_col = codec_.schema().key_column;
  std::vector<Record> out;
  for (PageId p = 1; p <= disk_.page_count(); ++p) {
    fetch(p);
    for (std::size_t s = 0; s < slots_per_page_; ++s) {
      if (!slot_used(s)) continue;
      ++stats_.records_examined;
      const auto bytes = slot_record(s);
      if (compare(codec_.decode_column(bytes, key_col), key) == 0) {
        out.push_back(codec_.decode(bytes));
        ++stats_.records_returned;
        return out;  // la clave primaria es unica
      }
    }
  }
  return out;
}

std::vector<Record> HeapFile::range_search(const Key& lo, const Key& hi) {
  const std::size_t key_col = codec_.schema().key_column;
  std::vector<Record> out;
  for (PageId p = 1; p <= disk_.page_count(); ++p) {
    fetch(p);
    for (std::size_t s = 0; s < slots_per_page_; ++s) {
      if (!slot_used(s)) continue;
      ++stats_.records_examined;
      const auto bytes = slot_record(s);
      const Value k = codec_.decode_column(bytes, key_col);
      if (compare(k, lo) >= 0 && compare(k, hi) <= 0) {
        out.push_back(codec_.decode(bytes));
        ++stats_.records_returned;
      }
    }
  }
  return out;  // en orden de llegada: el heap no tiene orden por clave
}

std::vector<Record> HeapFile::scan() {
  std::vector<Record> out;
  out.reserve(live_);
  for (PageId p = 1; p <= disk_.page_count(); ++p) {
    fetch(p);
    for (std::size_t s = 0; s < slots_per_page_; ++s) {
      if (!slot_used(s)) continue;
      ++stats_.records_examined;
      out.push_back(codec_.decode(slot_record(s)));
      ++stats_.records_returned;
    }
  }
  return out;
}

std::optional<Record> HeapFile::read(RID rid) {
  if (rid.page == kInvalidPage || rid.page == 0 || rid.page > disk_.page_count() ||
      rid.slot >= slots_per_page_) {
    return std::nullopt;
  }
  fetch(rid.page);
  ++stats_.records_examined;
  if (!slot_used(rid.slot)) return std::nullopt;
  ++stats_.records_returned;
  return codec_.decode(slot_record(rid.slot));
}

}  // namespace quipudb
