#include "quipudb/storage/sequential_file.hpp"

#include <algorithm>
#include <array>
#include <cstring>
#include <string>

#include "quipudb/error.hpp"

namespace quipudb {

SequentialFile::SequentialFile(std::filesystem::path path, Schema schema, std::size_t page_size)
    : codec_(std::move(schema)), disk_(std::move(path), page_size), scratch_(disk_.page_size()) {
  slot_size_ = codec_.size() + 1;
  if (scratch_.body_size() <= kBodyHeader) {
    throw SchemaError("la pagina es demasiado chica para el area secuencial");
  }
  slots_per_page_ = (scratch_.body_size() - kBodyHeader) / slot_size_;
  if (slots_per_page_ < 2) {
    throw SchemaError("un registro de " + std::to_string(codec_.size()) +
                      " bytes no deja al menos dos slots en una pagina de " +
                      std::to_string(disk_.page_size()));
  }
  load_meta();
  build_directory();
}

// ---------------------------------------------------------------------------
// Metadatos y directorio
// ---------------------------------------------------------------------------

void SequentialFile::load_meta() {
  Meta m;
  std::vector<std::byte> buf(sizeof(Meta));
  disk_.read_meta(buf);
  std::memcpy(&m, buf.data(), sizeof(Meta));

  if (m.version == 0 && m.record_size == 0 && disk_.page_count() == 0) {
    live_ = 0;
    deleted_ = 0;
    main_head_ = kInvalidPage;
    save_meta();
    return;
  }
  if (m.version != kMetaVersion) {
    throw IoError("'" + disk_.path().string() + "' usa el formato secuencial " +
                  std::to_string(m.version) + " y este core escribe el " +
                  std::to_string(kMetaVersion));
  }
  if (m.record_size != codec_.size()) {
    throw SchemaError("'" + disk_.path().string() + "' guarda registros de " +
                      std::to_string(m.record_size) + " bytes y el esquema pide " +
                      std::to_string(codec_.size()));
  }
  live_ = m.live;
  deleted_ = m.deleted;
  main_head_ = m.main_head;
}

void SequentialFile::save_meta() {
  Meta m;
  m.version = kMetaVersion;
  m.record_size = static_cast<std::uint32_t>(codec_.size());
  m.live = live_;
  m.deleted = deleted_;
  m.main_head = main_head_;
  std::vector<std::byte> buf(sizeof(Meta));
  std::memcpy(buf.data(), &m, sizeof(Meta));
  disk_.write_meta(buf);
}

void SequentialFile::build_directory() {
  // El directorio (que paginas principales hay y con que clave empieza cada
  // una) se reconstruye al abrir recorriendo la cadena: son tantas lecturas
  // como paginas principales, y evita persistir una estructura aparte.
  main_pages_.clear();
  first_keys_.clear();
  PageId p = main_head_;
  while (p != kInvalidPage) {
    if (p == 0 || p > disk_.page_count()) {
      throw IoError("la cadena principal de '" + disk_.path().string() + "' apunta a la pagina " +
                    std::to_string(p) + ", que no existe");
    }
    if (main_pages_.size() > disk_.page_count()) {
      throw IoError("la cadena principal de '" + disk_.path().string() + "' tiene un ciclo");
    }
    disk_.read_page(p, scratch_);
    main_pages_.push_back(p);
    if (physical_slots() == 0) {
      throw IoError("la pagina principal " + std::to_string(p) + " de '" +
                    disk_.path().string() + "' esta vacia");
    }
    first_keys_.push_back(slot_key(0));
    p = scratch_.next();
  }
  for (std::size_t i = 1; i < first_keys_.size(); ++i) {
    if (compare(first_keys_[i - 1], first_keys_[i]) >= 0) {
      throw IoError("las paginas principales de '" + disk_.path().string() +
                    "' no estan en orden de clave");
    }
  }
}

void SequentialFile::flush() {
  save_meta();
  disk_.flush();
}

// ---------------------------------------------------------------------------
// Acceso a paginas
// ---------------------------------------------------------------------------

void SequentialFile::fetch(PageId id) {
  disk_.read_page(id, scratch_);
  ++stats_.pages_read;
}

void SequentialFile::store(PageId id) {
  disk_.write_page(id, scratch_);
  ++stats_.pages_written;
}

PageId SequentialFile::allocate_blank() {
  const PageId id = disk_.allocate_page();
  scratch_.clear();
  scratch_.set_next(kInvalidPage);
  scratch_.set_free_space(static_cast<std::uint16_t>(slots_per_page_ * slot_size_));
  set_overflow_head(kInvalidPage);
  return id;
}

PageId SequentialFile::overflow_head() const {
  PageId p = kInvalidPage;
  const auto bytes = scratch_.read_bytes(0, sizeof(PageId));
  std::memcpy(&p, bytes.data(), sizeof(PageId));
  return p;
}

void SequentialFile::set_overflow_head(PageId p) {
  std::array<std::byte, sizeof(PageId)> buf{};
  std::memcpy(buf.data(), &p, sizeof(PageId));
  scratch_.write_bytes(0, buf);
}

// ---------------------------------------------------------------------------
// Localizacion
// ---------------------------------------------------------------------------

std::size_t SequentialFile::group_of(const Key& key) const {
  // Ultima pagina cuya primera clave es <= key; si key es menor que todas,
  // el grupo es el primero.
  const auto it = std::upper_bound(first_keys_.begin(), first_keys_.end(), key,
                                   [](const Key& k, const Key& f) { return compare(k, f) < 0; });
  if (it == first_keys_.begin()) return 0;
  return static_cast<std::size_t>(std::distance(first_keys_.begin(), it) - 1);
}

std::size_t SequentialFile::lower_bound_in_page(const Key& key) const {
  std::size_t lo = 0;
  std::size_t hi = physical_slots();
  while (lo < hi) {
    const std::size_t mid = lo + (hi - lo) / 2;
    if (compare(slot_key(mid), key) < 0) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  return lo;
}

std::optional<RID> SequentialFile::find_in_group(PageId mp, const Key& key) {
  fetch(mp);
  const std::size_t n = physical_slots();
  const std::size_t pos = lower_bound_in_page(key);
  if (pos < n && compare(slot_key(pos), key) == 0 && slot_state(pos) == kUsed) {
    return RID{mp, static_cast<SlotId>(pos)};
  }
  stats_.records_examined += n;

  PageId ovf = overflow_head();
  while (ovf != kInvalidPage) {
    fetch(ovf);
    const std::size_t m = physical_slots();
    stats_.records_examined += m;
    for (std::size_t s = 0; s < m; ++s) {
      if (slot_state(s) == kUsed && compare(slot_key(s), key) == 0) {
        return RID{ovf, static_cast<SlotId>(s)};
      }
    }
    ovf = scratch_.next();
  }
  return std::nullopt;
}

// ---------------------------------------------------------------------------
// Particion de un grupo
// ---------------------------------------------------------------------------

void SequentialFile::split_group(std::size_t g) {
  const std::size_t key_col = codec_.schema().key_column;
  const PageId mp = main_pages_[g];

  // 1. Juntar los registros vivos del grupo, sin decodificarlos: se mueven
  //    los bytes del slot tal cual.
  std::vector<std::pair<Key, std::vector<std::byte>>> registros;
  std::vector<PageId> reutilizables{mp};

  fetch(mp);
  const PageId siguiente_main = scratch_.next();
  PageId p = mp;
  PageId ovf = overflow_head();
  while (true) {
    const std::size_t n = physical_slots();
    for (std::size_t i = 0; i < n; ++i) {
      if (slot_state(i) != kUsed) continue;
      const auto bytes = slot_record(i);
      registros.emplace_back(codec_.decode_column(bytes, key_col),
                             std::vector<std::byte>(bytes.begin(), bytes.end()));
    }
    if (ovf == kInvalidPage) break;
    p = ovf;
    reutilizables.push_back(p);
    fetch(p);
    ovf = scratch_.next();
  }

  std::sort(registros.begin(), registros.end(),
            [](const auto& a, const auto& b) { return compare(a.first, b.first) < 0; });

  // 2. Repartir a media carga, para que las proximas inserciones de este
  //    rango vuelvan a caber en el area principal.
  const std::size_t por_pagina = std::max<std::size_t>(1, slots_per_page_ / 2);
  const std::size_t destino = (registros.size() + por_pagina - 1) / por_pagina;

  std::vector<PageId> paginas = reutilizables;
  while (paginas.size() < destino) paginas.push_back(disk_.allocate_page());
  paginas.resize(std::max<std::size_t>(destino, 1));

  // 3. Escribirlas encadenadas; la ultima retoma el resto de la cadena.
  std::size_t escritos = 0;
  for (std::size_t i = 0; i < paginas.size(); ++i) {
    const std::size_t cuantos =
        std::min(por_pagina, registros.size() - escritos);
    scratch_.clear();
    set_overflow_head(kInvalidPage);
    scratch_.set_next(i + 1 < paginas.size() ? paginas[i + 1] : siguiente_main);
    scratch_.set_record_count(static_cast<std::uint16_t>(cuantos));
    scratch_.set_free_space(
        static_cast<std::uint16_t>((slots_per_page_ - cuantos) * slot_size_));
    for (std::size_t j = 0; j < cuantos; ++j) {
      std::vector<std::byte> slot(slot_size_);
      slot[0] = kUsed;
      std::copy(registros[escritos + j].second.begin(), registros[escritos + j].second.end(),
                slot.begin() + 1);
      scratch_.write_bytes(slot_offset(j), slot);
    }
    store(paginas[i]);
    escritos += cuantos;
  }

  // 4. Rehacer el directorio en memoria: el grupo g pasa a ser varias paginas.
  //    La primera es la misma de antes, asi que quien la apuntaba sigue bien.
  std::vector<Key> claves;
  claves.reserve(paginas.size());
  for (std::size_t i = 0; i < paginas.size(); ++i) {
    claves.push_back(registros[i * por_pagina].first);
  }
  main_pages_.erase(main_pages_.begin() + static_cast<std::ptrdiff_t>(g));
  main_pages_.insert(main_pages_.begin() + static_cast<std::ptrdiff_t>(g), paginas.begin(),
                     paginas.end());
  first_keys_.erase(first_keys_.begin() + static_cast<std::ptrdiff_t>(g));
  first_keys_.insert(first_keys_.begin() + static_cast<std::ptrdiff_t>(g), claves.begin(),
                     claves.end());
}

// ---------------------------------------------------------------------------
// Insercion
// ---------------------------------------------------------------------------

RID SequentialFile::insert(const Record& record) {
  codec_.schema().validate(record);
  const Key& key = codec_.schema().key_of(record);

  std::vector<std::byte> slot_bytes(slot_size_);
  slot_bytes[0] = kUsed;
  codec_.encode(record, std::span<std::byte>(slot_bytes).subspan(1));

  // Archivo vacio: la primera pagina principal.
  if (main_pages_.empty()) {
    const PageId p = allocate_blank();
    scratch_.write_bytes(slot_offset(0), slot_bytes);
    scratch_.set_record_count(1);
    scratch_.set_free_space(static_cast<std::uint16_t>(scratch_.free_space() - slot_size_));
    store(p);
    main_head_ = p;
    main_pages_.push_back(p);
    first_keys_.push_back(key);
    ++live_;
    save_meta();
    return RID{p, 0};
  }

  const std::size_t g = group_of(key);
  const PageId mp = main_pages_[g];
  if (find_in_group(mp, key)) {
    throw DuplicateKey("la clave primaria ya existe en " + codec_.schema().table_name);
  }

  fetch(mp);
  const std::size_t n = physical_slots();

  // Caso normal: cabe en su pagina principal.
  if (n < slots_per_page_) {
    const std::size_t pos = lower_bound_in_page(key);
    if (pos < n) {
      // Corre la cola una posicion para no romper el orden.
      const auto cola = scratch_.read_bytes(slot_offset(pos), (n - pos) * slot_size_);
      std::vector<std::byte> tmp(cola.begin(), cola.end());
      scratch_.write_bytes(slot_offset(pos + 1), tmp);
    }
    scratch_.write_bytes(slot_offset(pos), slot_bytes);
    scratch_.set_record_count(static_cast<std::uint16_t>(scratch_.record_count() + 1));
    scratch_.set_free_space(static_cast<std::uint16_t>(scratch_.free_space() - slot_size_));
    store(mp);
    if (pos == 0) first_keys_[g] = key;
    ++live_;
    save_meta();
    return RID{mp, static_cast<SlotId>(pos)};
  }

  // Pagina llena y es la ultima, con una clave mayor que todas: se agrega una
  // pagina principal al final. Asi una carga ordenada no genera overflow.
  const bool es_ultima = g + 1 == main_pages_.size();
  if (es_ultima && compare(key, slot_key(n - 1)) > 0) {
    const PageId nueva = allocate_blank();
    scratch_.write_bytes(slot_offset(0), slot_bytes);
    scratch_.set_record_count(1);
    scratch_.set_free_space(static_cast<std::uint16_t>(scratch_.free_space() - slot_size_));
    store(nueva);
    fetch(mp);
    scratch_.set_next(nueva);
    store(mp);
    main_pages_.push_back(nueva);
    first_keys_.push_back(key);
    ++live_;
    save_meta();
    return RID{nueva, 0};
  }

  // Overflow: una sola pagina por grupo.
  const PageId cabeza = overflow_head();
  PageId destino = kInvalidPage;
  if (cabeza != kInvalidPage) {
    fetch(cabeza);
    if (physical_slots() < slots_per_page_) {
      destino = cabeza;
    } else {
      // La pagina de overflow tambien esta llena: se parte el grupo y se
      // reintenta. Despues de partir hay sitio en el area principal, asi que
      // la llamada no se vuelve a anidar.
      split_group(g);
      return insert(record);
    }
  }
  if (destino == kInvalidPage) {
    destino = allocate_blank();
    scratch_.set_next(kInvalidPage);
    store(destino);
    fetch(mp);
    set_overflow_head(destino);
    store(mp);
    fetch(destino);
  }
  const std::size_t pos = physical_slots();
  scratch_.write_bytes(slot_offset(pos), slot_bytes);
  scratch_.set_record_count(static_cast<std::uint16_t>(scratch_.record_count() + 1));
  scratch_.set_free_space(static_cast<std::uint16_t>(scratch_.free_space() - slot_size_));
  store(destino);
  ++live_;
  save_meta();
  return RID{destino, static_cast<SlotId>(pos)};
}

// ---------------------------------------------------------------------------
// Eliminacion (marcado; la cuenta del desperdicio es #11)
// ---------------------------------------------------------------------------

std::size_t SequentialFile::remove(const Key& key) {
  if (main_pages_.empty()) return 0;
  const std::size_t g = group_of(key);
  const auto rid = find_in_group(main_pages_[g], key);
  if (!rid) return 0;

  fetch(rid->page);
  std::array<std::byte, 1> marca{kDeleted};
  scratch_.write_bytes(slot_offset(rid->slot), marca);
  scratch_.set_record_count(static_cast<std::uint16_t>(scratch_.record_count() - 1));
  store(rid->page);

  --live_;
  ++deleted_;
  save_meta();
  return 1;
}

// ---------------------------------------------------------------------------
// Lectura
// ---------------------------------------------------------------------------

void SequentialFile::collect_live(std::vector<Record>& out) {
  const std::size_t n = physical_slots();
  for (std::size_t s = 0; s < n; ++s) {
    ++stats_.records_examined;
    if (slot_state(s) != kUsed) continue;
    out.push_back(codec_.decode(slot_record(s)));
    ++stats_.records_returned;
  }
}

std::vector<Record> SequentialFile::scan() {
  const std::size_t key_col = codec_.schema().key_column;
  std::vector<Record> out;
  out.reserve(live_);

  for (const PageId mp : main_pages_) {
    // Grupo = pagina principal + su cadena de overflow. Las claves del grupo
    // caen todas entre la primera clave de esta pagina y la de la siguiente,
    // asi que ordenar el grupo alcanza para que el total salga ordenado.
    std::vector<Record> grupo;
    fetch(mp);
    const PageId ovf = overflow_head();
    collect_live(grupo);
    PageId p = ovf;
    while (p != kInvalidPage) {
      fetch(p);
      collect_live(grupo);
      p = scratch_.next();
    }
    if (ovf != kInvalidPage) {
      std::sort(grupo.begin(), grupo.end(), [&](const Record& a, const Record& b) {
        return compare(a[key_col], b[key_col]) < 0;
      });
    }
    out.insert(out.end(), std::make_move_iterator(grupo.begin()),
               std::make_move_iterator(grupo.end()));
  }
  return out;
}

std::vector<Record> SequentialFile::search(const Key& key) {
  // Recorrido lineal a proposito: aprovechar el orden con busqueda binaria es
  // el issue #13, que ademas compara su resultado contra esta version.
  std::vector<Record> out;
  const std::size_t key_col = codec_.schema().key_column;
  for (const Record& r : scan()) {
    if (compare(r[key_col], key) == 0) {
      out.push_back(r);
      break;
    }
  }
  stats_.records_returned = out.size();
  return out;
}

std::vector<Record> SequentialFile::range_search(const Key& lo, const Key& hi) {
  std::vector<Record> out;
  const std::size_t key_col = codec_.schema().key_column;
  for (Record& r : scan()) {
    if (compare(r[key_col], lo) >= 0 && compare(r[key_col], hi) <= 0) {
      out.push_back(std::move(r));
    }
  }
  stats_.records_returned = out.size();
  return out;
}

std::optional<Record> SequentialFile::read(RID rid) {
  if (rid.page == kInvalidPage || rid.page == 0 || rid.page > disk_.page_count() ||
      rid.slot >= slots_per_page_) {
    return std::nullopt;
  }
  fetch(rid.page);
  if (rid.slot >= physical_slots()) return std::nullopt;
  ++stats_.records_examined;
  if (slot_state(rid.slot) != kUsed) return std::nullopt;
  ++stats_.records_returned;
  return codec_.decode(slot_record(rid.slot));
}

std::size_t SequentialFile::overflow_pages() {
  std::size_t total = 0;
  for (const PageId mp : main_pages_) {
    fetch(mp);
    PageId p = overflow_head();
    while (p != kInvalidPage) {
      ++total;
      fetch(p);
      p = scratch_.next();
    }
  }
  return total;
}

}  // namespace quipudb
