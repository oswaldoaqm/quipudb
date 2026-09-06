#include "quipudb/index/bplus_tree.hpp"

#include <algorithm>
#include <array>
#include <cstring>

#include "quipudb/error.hpp"

namespace quipudb {

namespace {

PageId leer_pagina(std::span<const std::byte> bytes) {
  PageId p = kInvalidPage;
  std::memcpy(&p, bytes.data(), sizeof p);
  return p;
}

std::array<std::byte, sizeof(PageId)> bytes_de(PageId p) {
  std::array<std::byte, sizeof(PageId)> buf{};
  std::memcpy(buf.data(), &p, sizeof p);
  return buf;
}

}  // namespace

// ---------------------------------------------------------------------------
// Construccion
// ---------------------------------------------------------------------------

BPlusTree::BPlusTree(std::filesystem::path path, Column key_column, std::size_t payload_size,
                     std::size_t page_size, std::size_t order)
    : key_column_(std::move(key_column)),
      key_size_(key_column_.byte_size()),
      payload_size_(payload_size),
      disk_(std::move(path), page_size),
      scratch_(disk_.page_size()) {
  if (key_size_ == 0) {
    throw SchemaError("la columna " + key_column_.name + " no tiene tamano de clave");
  }
  const std::size_t body = scratch_.body_size();
  // Hoja: 1 byte de tipo + n * (clave + payload).
  const std::size_t cap_hoja = (body - 1) / (key_size_ + payload_size_);
  // Interno: 1 byte de tipo + hijo izquierdo + n * (clave + hijo).
  const std::size_t cap_interno = (body - 1 - sizeof(PageId)) / (key_size_ + sizeof(PageId));
  const std::size_t maximo = std::min(cap_hoja, cap_interno);

  order_ = order == 0 ? maximo : order;
  if (order_ < 2) {
    throw SchemaError("el orden del B+ tiene que ser al menos 2; con menos, un split no termina");
  }
  if (order_ > maximo) {
    throw SchemaError("orden " + std::to_string(order_) + " para claves de " +
                      std::to_string(key_size_) + " bytes y payload de " +
                      std::to_string(payload_size_) + ": en una pagina de " +
                      std::to_string(disk_.page_size()) + " entran " + std::to_string(maximo));
  }
  load_meta();
}

void BPlusTree::load_meta() {
  Meta m;
  std::vector<std::byte> buf(sizeof(Meta));
  disk_.read_meta(buf);
  std::memcpy(&m, buf.data(), sizeof(Meta));

  if (m.version == 0 && m.key_size == 0 && disk_.page_count() == 0) {
    root_ = kInvalidPage;
    first_leaf_ = kInvalidPage;
    height_ = 0;
    count_ = 0;
    save_meta();
    return;
  }
  if (m.version != kMetaVersion) {
    throw IoError("'" + disk_.path().string() + "' usa el formato de B+ " +
                  std::to_string(m.version) + " y este core escribe el " +
                  std::to_string(kMetaVersion));
  }
  if (m.key_type != static_cast<std::uint32_t>(key_column_.type) || m.key_size != key_size_ ||
      m.payload_size != payload_size_) {
    throw SchemaError("'" + disk_.path().string() + "' guarda claves " +
                      std::string(to_string(static_cast<DataType>(m.key_type))) + " de " +
                      std::to_string(m.key_size) + " bytes con payload de " +
                      std::to_string(m.payload_size) + ", y se pidio abrirlo con " +
                      std::string(to_string(key_column_.type)) + " de " +
                      std::to_string(key_size_) + " y payload de " +
                      std::to_string(payload_size_));
  }
  if (m.order != order_) {
    throw SchemaError("'" + disk_.path().string() + "' se creo con orden " +
                      std::to_string(m.order) + " y se pidio abrirlo con " +
                      std::to_string(order_));
  }
  root_ = m.root;
  first_leaf_ = m.first_leaf;
  height_ = m.height;
  count_ = m.count;
}

void BPlusTree::save_meta() {
  Meta m;
  m.key_type = static_cast<std::uint32_t>(key_column_.type);
  m.key_size = static_cast<std::uint32_t>(key_size_);
  m.payload_size = static_cast<std::uint32_t>(payload_size_);
  m.order = static_cast<std::uint32_t>(order_);
  m.root = root_;
  m.first_leaf = first_leaf_;
  m.height = static_cast<std::uint32_t>(height_);
  m.count = count_;
  std::vector<std::byte> buf(sizeof(Meta));
  std::memcpy(buf.data(), &m, sizeof(Meta));
  disk_.write_meta(buf);
}

void BPlusTree::flush() {
  save_meta();
  disk_.flush();
}

// ---------------------------------------------------------------------------
// Acceso a paginas y a las entradas del nodo cargado
// ---------------------------------------------------------------------------

void BPlusTree::fetch(PageId id) {
  disk_.read_page(id, scratch_);
  ++stats_.pages_read;
}

void BPlusTree::store(PageId id) {
  disk_.write_page(id, scratch_);
  ++stats_.pages_written;
}

PageId BPlusTree::allocate(std::byte tipo) {
  const PageId id = disk_.allocate_page();
  scratch_.clear();
  scratch_.set_next(kInvalidPage);
  scratch_.set_record_count(0);
  const std::array<std::byte, 1> t{tipo};
  scratch_.write_bytes(0, t);
  return id;
}

Key BPlusTree::key_at(std::size_t i) const {
  const std::size_t off = is_leaf() ? leaf_offset(i) : branch_offset(i);
  return RecordCodec::decode_value(key_column_, scratch_.read_bytes(off, key_size_));
}

std::vector<std::byte> BPlusTree::payload_at(std::size_t i) const {
  const auto bytes = scratch_.read_bytes(leaf_offset(i) + key_size_, payload_size_);
  return {bytes.begin(), bytes.end()};
}

PageId BPlusTree::child_at(std::size_t i) const {
  // El hijo 0 es el izquierdo, que va antes de la primera clave.
  if (i == 0) return leer_pagina(scratch_.read_bytes(1, sizeof(PageId)));
  return leer_pagina(scratch_.read_bytes(branch_offset(i - 1) + key_size_, sizeof(PageId)));
}

std::size_t BPlusTree::lower_bound(const Key& key) const {
  std::size_t lo = 0;
  std::size_t hi = key_count();
  while (lo < hi) {
    const std::size_t mid = lo + (hi - lo) / 2;
    if (compare(key_at(mid), key) < 0) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  return lo;
}

// ---------------------------------------------------------------------------
// Escritura de nodos
// ---------------------------------------------------------------------------

void BPlusTree::write_leaf(PageId id,
                           const std::vector<std::pair<Key, std::vector<std::byte>>>& entradas,
                           PageId siguiente) {
  scratch_.clear();
  const std::array<std::byte, 1> t{kLeaf};
  scratch_.write_bytes(0, t);
  scratch_.set_next(siguiente);
  scratch_.set_record_count(static_cast<std::uint16_t>(entradas.size()));
  std::vector<std::byte> clave(key_size_);
  for (std::size_t i = 0; i < entradas.size(); ++i) {
    RecordCodec::encode_value(key_column_, entradas[i].first, clave);
    scratch_.write_bytes(leaf_offset(i), clave);
    scratch_.write_bytes(leaf_offset(i) + key_size_, entradas[i].second);
  }
  store(id);
}

void BPlusTree::write_internal(PageId id, PageId hijo_izq, const std::vector<Branch>& ramas) {
  scratch_.clear();
  const std::array<std::byte, 1> t{kInternal};
  scratch_.write_bytes(0, t);
  scratch_.set_next(kInvalidPage);
  scratch_.set_record_count(static_cast<std::uint16_t>(ramas.size()));
  scratch_.write_bytes(1, bytes_de(hijo_izq));
  std::vector<std::byte> clave(key_size_);
  for (std::size_t i = 0; i < ramas.size(); ++i) {
    RecordCodec::encode_value(key_column_, ramas[i].key, clave);
    scratch_.write_bytes(branch_offset(i), clave);
    scratch_.write_bytes(branch_offset(i) + key_size_, bytes_de(ramas[i].child));
  }
  store(id);
}

// ---------------------------------------------------------------------------
// Descenso
// ---------------------------------------------------------------------------

PageId BPlusTree::descend(const Key& key, std::vector<PageId>* camino) {
  PageId actual = root_;
  while (true) {
    fetch(actual);
    if (is_leaf()) return actual;
    if (camino != nullptr) camino->push_back(actual);
    // El hijo i cuelga a la izquierda de la clave i. Se baja por el primer
    // separador mayor que la clave buscada.
    const std::size_t n = key_count();
    std::size_t i = 0;
    while (i < n && compare(key_at(i), key) <= 0) ++i;
    actual = child_at(i);
  }
}

// ---------------------------------------------------------------------------
// Insercion
// ---------------------------------------------------------------------------

void BPlusTree::insert(const Key& key, std::span<const std::byte> payload) {
  if (type_of(key) != key_column_.type) {
    throw SchemaError("la clave es " + std::string(to_string(type_of(key))) + " y la columna " +
                      key_column_.name + " es " + std::string(to_string(key_column_.type)));
  }
  if (payload.size() != payload_size_) {
    throw SchemaError("el payload mide " + std::to_string(payload.size()) +
                      " bytes y el arbol guarda " + std::to_string(payload_size_));
  }

  // Arbol vacio: la raiz es una hoja.
  if (root_ == kInvalidPage) {
    const PageId hoja = allocate(kLeaf);
    write_leaf(hoja, {{key, {payload.begin(), payload.end()}}}, kInvalidPage);
    root_ = hoja;
    first_leaf_ = hoja;
    height_ = 1;
    count_ = 1;
    save_meta();
    return;
  }

  std::vector<PageId> camino;
  const PageId hoja = descend(key, &camino);
  // scratch_ tiene la hoja cargada.
  const std::size_t n = key_count();
  const std::size_t pos = lower_bound(key);
  if (pos < n && compare(key_at(pos), key) == 0) {
    throw DuplicateKey("la clave ya esta en el indice");
  }

  // Se arma la hoja completa en memoria: como mucho order+1 entradas.
  std::vector<std::pair<Key, std::vector<std::byte>>> entradas;
  entradas.reserve(n + 1);
  for (std::size_t i = 0; i < pos; ++i) entradas.emplace_back(key_at(i), payload_at(i));
  entradas.emplace_back(key, std::vector<std::byte>(payload.begin(), payload.end()));
  for (std::size_t i = pos; i < n; ++i) entradas.emplace_back(key_at(i), payload_at(i));
  const PageId siguiente = scratch_.next();

  if (entradas.size() <= order_) {
    write_leaf(hoja, entradas, siguiente);
    ++count_;
    save_meta();
    return;
  }

  // Split de hoja: la mitad izquierda se queda, la derecha se va a una hoja
  // nueva, y la primera clave de la derecha SE COPIA hacia arriba (sigue
  // viviendo en la hoja, porque las hojas guardan todas las claves).
  const std::size_t mitad = (entradas.size() + 1) / 2;
  const PageId nueva = allocate(kLeaf);
  const std::vector<std::pair<Key, std::vector<std::byte>>> izq(entradas.begin(),
                                                                entradas.begin() + mitad);
  const std::vector<std::pair<Key, std::vector<std::byte>>> der(entradas.begin() + mitad,
                                                                entradas.end());
  write_leaf(nueva, der, siguiente);
  write_leaf(hoja, izq, nueva);  // la cadena de hojas se mantiene
  ++count_;

  insert_in_parent(camino, der.front().first, nueva, hoja);
  save_meta();
}

void BPlusTree::insert_in_parent(std::vector<PageId>& camino, const Key& separador, PageId derecho,
                                 PageId izquierdo) {
  Key sep = separador;
  PageId der = derecho;
  PageId izq = izquierdo;

  while (true) {
    if (camino.empty()) {
      // Se partio la raiz: nace una raiz nueva y el arbol crece de altura.
      const PageId raiz = allocate(kInternal);
      write_internal(raiz, izq, {Branch{sep, der}});
      root_ = raiz;
      ++height_;
      return;
    }

    const PageId padre = camino.back();
    camino.pop_back();
    fetch(padre);

    const std::size_t n = key_count();
    const PageId hijo_izq = child_at(0);
    std::vector<Branch> ramas;
    ramas.reserve(n + 1);
    for (std::size_t i = 0; i < n; ++i) ramas.push_back(Branch{key_at(i), child_at(i + 1)});

    // La rama nueva va justo despues del hijo que se partio.
    const std::size_t pos = static_cast<std::size_t>(
        std::lower_bound(ramas.begin(), ramas.end(), sep,
                         [](const Branch& b, const Key& k) { return compare(b.key, k) < 0; }) -
        ramas.begin());
    ramas.insert(ramas.begin() + static_cast<std::ptrdiff_t>(pos), Branch{sep, der});

    if (ramas.size() <= order_) {
      write_internal(padre, hijo_izq, ramas);
      return;
    }

    // Split de nodo interno: la clave del medio SE MUEVE hacia arriba, no se
    // copia. Abajo deja de estar, porque los internos solo guian la busqueda.
    const std::size_t mitad = ramas.size() / 2;
    const Key sube = ramas[mitad].key;
    const std::vector<Branch> izq_ramas(ramas.begin(), ramas.begin() + mitad);
    const std::vector<Branch> der_ramas(ramas.begin() + mitad + 1, ramas.end());
    const PageId der_hijo_izq = ramas[mitad].child;

    const PageId nuevo = allocate(kInternal);
    write_internal(nuevo, der_hijo_izq, der_ramas);
    write_internal(padre, hijo_izq, izq_ramas);

    sep = sube;
    izq = padre;
    der = nuevo;
  }
}

// ---------------------------------------------------------------------------
// Lectura
// ---------------------------------------------------------------------------

std::optional<std::vector<std::byte>> BPlusTree::find(const Key& key) {
  if (root_ == kInvalidPage) return std::nullopt;
  if (type_of(key) != key_column_.type) {
    throw SchemaError("la clave es de otro tipo que la columna " + key_column_.name);
  }
  descend(key, nullptr);
  const std::size_t pos = lower_bound(key);
  ++stats_.records_examined;
  if (pos >= key_count() || compare(key_at(pos), key) != 0) return std::nullopt;
  ++stats_.records_returned;
  return payload_at(pos);
}

bool BPlusTree::set_payload(const Key& key, std::span<const std::byte> payload) {
  if (payload.size() != payload_size_) {
    throw SchemaError("el payload mide " + std::to_string(payload.size()) +
                      " bytes y el arbol guarda " + std::to_string(payload_size_));
  }
  if (root_ == kInvalidPage) return false;
  const PageId hoja = descend(key, nullptr);
  const std::size_t pos = lower_bound(key);
  if (pos >= key_count() || compare(key_at(pos), key) != 0) return false;
  scratch_.write_bytes(leaf_offset(pos) + key_size_, payload);
  store(hoja);
  return true;
}

bool BPlusTree::erase(const Key& key) {
  if (root_ == kInvalidPage) return false;
  const PageId hoja = descend(key, nullptr);
  const std::size_t n = key_count();
  const std::size_t pos = lower_bound(key);
  if (pos >= n || compare(key_at(pos), key) != 0) return false;

  std::vector<std::pair<Key, std::vector<std::byte>>> entradas;
  entradas.reserve(n - 1);
  for (std::size_t i = 0; i < n; ++i) {
    if (i != pos) entradas.emplace_back(key_at(i), payload_at(i));
  }
  const PageId siguiente = scratch_.next();
  write_leaf(hoja, entradas, siguiente);
  --count_;

  // Si la raiz era la unica hoja y quedo vacia, el arbol vuelve a estar
  // vacio. En cualquier otro caso la hoja se queda como esta, aunque quede
  // por debajo de la mitad: fusionarla o redistribuirla es el #17.
  if (entradas.empty() && hoja == root_) {
    root_ = kInvalidPage;
    first_leaf_ = kInvalidPage;
    height_ = 0;
  }
  save_meta();
  return true;
}

std::optional<RID> BPlusTree::locate(const Key& key) {
  if (root_ == kInvalidPage) return std::nullopt;
  const PageId hoja = descend(key, nullptr);
  const std::size_t pos = lower_bound(key);
  if (pos >= key_count() || compare(key_at(pos), key) != 0) return std::nullopt;
  return RID{hoja, static_cast<SlotId>(pos)};
}

std::optional<std::vector<std::byte>> BPlusTree::payload_at_rid(RID rid) {
  if (rid.page == kInvalidPage || rid.page == 0 || rid.page > disk_.page_count()) {
    return std::nullopt;
  }
  fetch(rid.page);
  if (!is_leaf() || rid.slot >= key_count()) return std::nullopt;
  ++stats_.records_returned;
  return payload_at(rid.slot);
}

// ---------------------------------------------------------------------------
// Recorrido incremental
// ---------------------------------------------------------------------------

class BPlusTree::Cursor final : public EntryCursor {
 public:
  Cursor(BPlusTree& duenio, PageId hoja, std::size_t desde)
      : duenio_(duenio), pagina_(duenio.disk_.page_size()), actual_(hoja), i_(desde) {}

  bool next(Key& key, std::vector<std::byte>& payload) override {
    while (actual_ != kInvalidPage) {
      if (!cargada_) {
        duenio_.disk_.read_page(actual_, pagina_);
        ++duenio_.stats_.pages_read;
        cargada_ = true;
        if (++leidas_ > duenio_.disk_.page_count()) {
          throw IoError("la cadena de hojas de '" + duenio_.disk_.path().string() +
                        "' tiene un ciclo");
        }
      }
      const std::size_t n = pagina_.record_count();
      if (i_ < n) {
        const std::size_t off = 1 + i_ * (duenio_.key_size_ + duenio_.payload_size_);
        key = RecordCodec::decode_value(duenio_.key_column_,
                                        pagina_.read_bytes(off, duenio_.key_size_));
        const auto bytes =
            pagina_.read_bytes(off + duenio_.key_size_, duenio_.payload_size_);
        payload.assign(bytes.begin(), bytes.end());
        ++i_;
        ++duenio_.stats_.records_returned;
        return true;
      }
      actual_ = pagina_.next();
      i_ = 0;
      cargada_ = false;
    }
    return false;
  }

 private:
  BPlusTree& duenio_;
  Page pagina_;  // buffer propio: no comparte scratch_ con el arbol
  PageId actual_ = kInvalidPage;
  std::size_t i_ = 0;
  std::size_t leidas_ = 0;
  bool cargada_ = false;
};

std::unique_ptr<EntryCursor> BPlusTree::entries() {
  return std::make_unique<Cursor>(*this, first_leaf_, 0);
}

std::unique_ptr<EntryCursor> BPlusTree::entries_from(const Key& lo) {
  if (root_ == kInvalidPage) return std::make_unique<Cursor>(*this, kInvalidPage, 0);
  const PageId hoja = descend(lo, nullptr);
  return std::make_unique<Cursor>(*this, hoja, lower_bound(lo));
}

std::vector<std::pair<Key, std::vector<std::byte>>> BPlusTree::scan() {
  std::vector<std::pair<Key, std::vector<std::byte>>> out;
  out.reserve(count_);
  PageId p = first_leaf_;
  std::size_t leidas = 0;
  while (p != kInvalidPage) {
    if (++leidas > disk_.page_count()) {
      throw IoError("la cadena de hojas de '" + disk_.path().string() + "' tiene un ciclo");
    }
    fetch(p);
    const std::size_t n = key_count();
    for (std::size_t i = 0; i < n; ++i) {
      out.emplace_back(key_at(i), payload_at(i));
      ++stats_.records_returned;
    }
    stats_.records_examined += n;
    p = scratch_.next();
  }
  return out;
}

std::vector<std::pair<Key, std::vector<std::byte>>> BPlusTree::range(const Key& lo,
                                                                    const Key& hi) {
  std::vector<std::pair<Key, std::vector<std::byte>>> out;
  if (root_ == kInvalidPage || compare(lo, hi) > 0) return out;

  // Se baja una sola vez y despues se sigue la cadena de hojas.
  PageId p = descend(lo, nullptr);
  std::size_t desde = lower_bound(lo);
  std::size_t leidas = 0;
  while (p != kInvalidPage) {
    if (++leidas > disk_.page_count()) {
      throw IoError("la cadena de hojas de '" + disk_.path().string() + "' tiene un ciclo");
    }
    const std::size_t n = key_count();
    for (std::size_t i = desde; i < n; ++i) {
      ++stats_.records_examined;
      const Key k = key_at(i);
      if (compare(k, hi) > 0) return out;
      out.emplace_back(k, payload_at(i));
      ++stats_.records_returned;
    }
    p = scratch_.next();
    desde = 0;
    if (p != kInvalidPage) fetch(p);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Invariantes
// ---------------------------------------------------------------------------

std::string BPlusTree::check_node(PageId id, std::size_t profundidad, const Key* lo, const Key* hi,
                                  std::size_t& hojas_en, std::size_t& contadas) {
  if (id == 0 || id > disk_.page_count()) {
    return "el nodo " + std::to_string(id) + " no existe";
  }
  disk_.read_page(id, scratch_);
  const bool hoja = is_leaf();
  const std::size_t n = key_count();

  if (n > order_) {
    return "el nodo " + std::to_string(id) + " tiene " + std::to_string(n) +
           " claves y el orden es " + std::to_string(order_);
  }
  // Un nodo interno sin claves no tiene como guiar la busqueda. Una hoja si
  // puede quedar vacia: `erase` no rebalancea, eso llega en el #17.
  if (!hoja && n < 1) {
    return "el nodo interno " + std::to_string(id) + " no tiene claves";
  }
  // Las claves de un nodo estan ordenadas y caen dentro del rango que le
  // asignan sus separadores.
  for (std::size_t i = 0; i < n; ++i) {
    const Key k = key_at(i);
    if (i > 0 && compare(key_at(i - 1), k) >= 0) {
      return "las claves del nodo " + std::to_string(id) + " no estan ordenadas";
    }
    if (lo != nullptr && compare(k, *lo) < 0) {
      return "el nodo " + std::to_string(id) + " tiene una clave menor que su separador izquierdo";
    }
    if (hi != nullptr && compare(k, *hi) >= 0) {
      return "el nodo " + std::to_string(id) + " tiene una clave que le toca a su hermano derecho";
    }
  }

  if (hoja) {
    if (hojas_en == 0) {
      hojas_en = profundidad;
    } else if (hojas_en != profundidad) {
      return "el arbol no esta balanceado: hay hojas a profundidad " + std::to_string(hojas_en) +
             " y a " + std::to_string(profundidad);
    }
    contadas += n;
    return "";
  }

  // Un interno con n claves tiene n+1 hijos; cada uno acotado por sus vecinos.
  std::vector<PageId> hijos;
  std::vector<Key> claves;
  hijos.reserve(n + 1);
  claves.reserve(n);
  for (std::size_t i = 0; i <= n; ++i) hijos.push_back(child_at(i));
  for (std::size_t i = 0; i < n; ++i) claves.push_back(key_at(i));

  for (std::size_t i = 0; i <= n; ++i) {
    const Key* sub_lo = i == 0 ? lo : &claves[i - 1];
    const Key* sub_hi = i == n ? hi : &claves[i];
    const auto err = check_node(hijos[i], profundidad + 1, sub_lo, sub_hi, hojas_en, contadas);
    if (!err.empty()) return err;
    disk_.read_page(id, scratch_);  // el hijo dejo otra pagina en scratch_
  }
  return "";
}

std::string BPlusTree::check_invariants() {
  if (root_ == kInvalidPage) {
    if (count_ != 0) return "el arbol dice tener " + std::to_string(count_) + " claves sin raiz";
    if (height_ != 0) return "el arbol vacio dice tener altura " + std::to_string(height_);
    return "";
  }
  std::size_t hojas_en = 0;
  std::size_t contadas = 0;
  const auto err = check_node(root_, 1, nullptr, nullptr, hojas_en, contadas);
  if (!err.empty()) return err;
  if (hojas_en != height_) {
    return "la altura declarada es " + std::to_string(height_) + " y las hojas estan a " +
           std::to_string(hojas_en);
  }
  if (contadas != count_) {
    return "el arbol dice tener " + std::to_string(count_) + " claves y en las hojas hay " +
           std::to_string(contadas);
  }

  // La cadena de hojas tiene que recorrer las mismas claves, en orden.
  std::size_t por_la_cadena = 0;
  PageId p = first_leaf_;
  std::optional<Key> anterior;
  std::size_t leidas = 0;
  while (p != kInvalidPage) {
    if (++leidas > disk_.page_count()) return "la cadena de hojas tiene un ciclo";
    disk_.read_page(p, scratch_);
    if (!is_leaf()) return "la cadena de hojas pasa por el nodo interno " + std::to_string(p);
    const std::size_t n = key_count();
    for (std::size_t i = 0; i < n; ++i) {
      const Key k = key_at(i);
      if (anterior && compare(*anterior, k) >= 0) {
        return "la cadena de hojas no sale ordenada";
      }
      anterior = k;
    }
    por_la_cadena += n;
    p = scratch_.next();
  }
  if (por_la_cadena != count_) {
    return "la cadena de hojas recorre " + std::to_string(por_la_cadena) + " claves de " +
           std::to_string(count_);
  }
  return "";
}

}  // namespace quipudb
