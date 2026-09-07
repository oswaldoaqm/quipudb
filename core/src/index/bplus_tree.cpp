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
                     std::size_t page_size, std::size_t order, bool unique)
    : key_column_(std::move(key_column)),
      key_size_(key_column_.byte_size()),
      payload_size_(payload_size),
      unique_(unique),
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
  // Los minimos son lo que deja un split, para que un nodo recien partido no
  // nazca ya en falta. La hoja se parte en (n+1)/2 y n+1-(n+1)/2 con
  // n = orden+1, asi que el lado chico tiene ceil(orden/2); el interno pierde
  // ademas la clave que sube, y le queda floor(orden/2).
  min_leaf_ = (order_ + 1) / 2;
  min_internal_ = order_ / 2;
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
    free_head_ = kInvalidPage;
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
  if ((m.unique != 0) != unique_) {
    throw SchemaError("'" + disk_.path().string() + "' se creo " +
                      (m.unique != 0 ? "con claves unicas" : "admitiendo claves repetidas") +
                      " y se pidio abrirlo al reves");
  }
  if (m.order != order_) {
    throw SchemaError("'" + disk_.path().string() + "' se creo con orden " +
                      std::to_string(m.order) + " y se pidio abrirlo con " +
                      std::to_string(order_));
  }
  root_ = m.root;
  first_leaf_ = m.first_leaf;
  free_head_ = m.free_head;
  height_ = m.height;
  count_ = m.count;
}

void BPlusTree::save_meta() {
  Meta m;
  m.key_type = static_cast<std::uint32_t>(key_column_.type);
  m.key_size = static_cast<std::uint32_t>(key_size_);
  m.payload_size = static_cast<std::uint32_t>(payload_size_);
  m.order = static_cast<std::uint32_t>(order_);
  m.unique = unique_ ? 1u : 0u;
  m.root = root_;
  m.first_leaf = first_leaf_;
  m.free_head = free_head_;
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
  PageId id = kInvalidPage;
  if (free_head_ != kInvalidPage) {
    // Se reusa una pagina que dejo libre una fusion. Ojo con el orden: hay que
    // leer el siguiente eslabon ANTES de pisar scratch_.
    fetch(free_head_);
    id = free_head_;
    free_head_ = scratch_.next();
  } else {
    id = disk_.allocate_page();
  }
  scratch_.clear();
  scratch_.set_next(kInvalidPage);
  scratch_.set_record_count(0);
  const std::array<std::byte, 1> t{tipo};
  scratch_.write_bytes(0, t);
  return id;
}

void BPlusTree::free_page(PageId id) {
  scratch_.clear();
  scratch_.set_record_count(0);
  scratch_.set_next(free_head_);
  store(id);
  free_head_ = id;
}

std::size_t BPlusTree::free_pages() {
  std::size_t n = 0;
  PageId p = free_head_;
  while (p != kInvalidPage) {
    if (++n > disk_.page_count()) {
      throw IoError("la free list de '" + disk_.path().string() + "' tiene un ciclo");
    }
    fetch(p);
    p = scratch_.next();
  }
  return n;
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

std::size_t BPlusTree::upper_bound(const Key& key) const {
  std::size_t lo = 0;
  std::size_t hi = key_count();
  while (lo < hi) {
    const std::size_t mid = lo + (hi - lo) / 2;
    if (compare(key_at(mid), key) <= 0) {
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

std::vector<std::pair<Key, std::vector<std::byte>>> BPlusTree::read_leaf(PageId id,
                                                                        PageId& siguiente) {
  fetch(id);
  const std::size_t n = key_count();
  siguiente = scratch_.next();
  std::vector<std::pair<Key, std::vector<std::byte>>> entradas;
  entradas.reserve(n);
  for (std::size_t i = 0; i < n; ++i) entradas.emplace_back(key_at(i), payload_at(i));
  return entradas;
}

std::vector<BPlusTree::Branch> BPlusTree::read_internal(PageId id, PageId& hijo_izq) {
  fetch(id);
  const std::size_t n = key_count();
  hijo_izq = child_at(0);
  std::vector<Branch> ramas;
  ramas.reserve(n);
  for (std::size_t i = 0; i < n; ++i) ramas.push_back(Branch{key_at(i), child_at(i + 1)});
  return ramas;
}

// ---------------------------------------------------------------------------
// Descenso
// ---------------------------------------------------------------------------

PageId BPlusTree::descend(const Key& key, std::vector<PageId>* camino, bool por_la_derecha) {
  PageId actual = root_;
  while (true) {
    fetch(actual);
    if (is_leaf()) return actual;
    if (camino != nullptr) camino->push_back(actual);
    // El hijo i cuelga a la izquierda de la clave i. En el empate con un
    // separador se sigue de largo (<= 0) o se para (< 0) segun `por_la_derecha`.
    const int limite = (unique_ || por_la_derecha) ? 0 : -1;
    const std::size_t n = key_count();
    std::size_t i = 0;
    while (i < n && compare(key_at(i), key) <= limite) ++i;
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
  if (unique_) {
    const std::size_t existente = lower_bound(key);
    if (existente < n && compare(key_at(existente), key) == 0) {
      throw DuplicateKey("la clave ya esta en el indice");
    }
  }
  // Con repetidas la nueva va despues de las iguales, para que el orden de
  // insercion se conserve dentro de la corrida.
  const std::size_t pos = unique_ ? lower_bound(key) : upper_bound(key);

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

    // La rama nueva va justo despues del hijo que se partio, y esa posicion se
    // deduce del hijo, no de la clave: con separadores repetidos (un indice
    // con claves repetidas) buscar por clave la pondria antes de sus hermanas
    // iguales y dejaria los hijos desordenados.
    std::size_t pos = 0;
    if (izq != hijo_izq) {
      pos = ramas.size() + 1;  // centinela: tiene que aparecer
      for (std::size_t i = 0; i < ramas.size(); ++i) {
        if (ramas[i].child == izq) {
          pos = i + 1;
          break;
        }
      }
      if (pos > ramas.size()) {
        throw IoError("el nodo " + std::to_string(padre) + " no reconoce al hijo " +
                      std::to_string(izq) + " que se acaba de partir");
      }
    }
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
  descend(key, nullptr, /*por_la_derecha=*/false);
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
  const PageId hoja = descend(key, nullptr, /*por_la_derecha=*/false);
  const std::size_t pos = lower_bound(key);
  if (pos >= key_count() || compare(key_at(pos), key) != 0) return false;
  scratch_.write_bytes(leaf_offset(pos) + key_size_, payload);
  store(hoja);
  return true;
}

bool BPlusTree::erase(const Key& key) {
  return erase_matching(key, nullptr, /*todas=*/false) > 0;
}

std::size_t BPlusTree::erase_all(const Key& key) {
  return erase_matching(key, nullptr, /*todas=*/true);
}

bool BPlusTree::erase_one(const Key& key, std::span<const std::byte> payload) {
  const std::vector<std::byte> buscado(payload.begin(), payload.end());
  return erase_matching(key, &buscado, /*todas=*/false) > 0;
}

std::size_t BPlusTree::erase_matching(const Key& key, const std::vector<std::byte>* payload,
                                      bool todas) {
  if (root_ == kInvalidPage) return 0;

  // Se borra de a una y se rebalancea despues de cada una. Con claves
  // repetidas cuesta un descenso por entrada en vez de un recorrido de la
  // cadena, pero es la unica forma de que el arreglo del underflow tenga a
  // mano el padre de la hoja que se toco.
  std::size_t quitadas = 0;
  while (root_ != kInvalidPage && erase_one_from(root_, key, payload)) {
    ++quitadas;
    --count_;
    shrink_root();
    if (!todas) break;
  }
  if (quitadas > 0) save_meta();
  return quitadas;
}

bool BPlusTree::erase_one_from(PageId id, const Key& key,
                               const std::vector<std::byte>* payload) {
  fetch(id);

  if (is_leaf()) {
    const std::size_t n = key_count();
    const PageId siguiente = scratch_.next();
    // Entre las iguales se busca la que ademas tenga ese payload (erase_one).
    std::size_t pos = lower_bound(key);
    while (pos < n && compare(key_at(pos), key) == 0) {
      if (payload == nullptr || payload_at(pos) == *payload) break;
      ++pos;
    }
    if (pos >= n || compare(key_at(pos), key) != 0) return false;

    std::vector<std::pair<Key, std::vector<std::byte>>> quedan;
    quedan.reserve(n - 1);
    for (std::size_t i = 0; i < n; ++i) {
      if (i != pos) quedan.emplace_back(key_at(i), payload_at(i));
    }
    write_leaf(id, quedan, siguiente);
    return true;
  }

  // Primer hijo cuyo rango puede contener la clave.
  std::size_t i = 0;
  while (i < key_count() && compare(key_at(i), key) < 0) ++i;
  while (true) {
    const PageId hijo = child_at(i);
    if (erase_one_from(hijo, key, payload)) {
      fix_underflow(id, i);
      return true;
    }
    fetch(id);  // el hijo dejo otra pagina en scratch_
    // Solo tiene sentido seguir al hermano de la derecha si el separador que
    // los divide es la clave buscada: entonces la corrida de iguales sigue
    // ahi. Si es mayor, del otro lado ya no puede estar.
    if (i >= key_count() || compare(key_at(i), key) != 0) return false;
    ++i;
  }
}

// ---------------------------------------------------------------------------
// Rebalanceo
// ---------------------------------------------------------------------------

void BPlusTree::fix_underflow(PageId padre, std::size_t i) {
  fetch(padre);
  const std::size_t n = key_count();
  const PageId hijo = child_at(i);

  fetch(hijo);
  const std::size_t minimo = is_leaf() ? min_leaf_ : min_internal_;
  if (key_count() >= minimo) return;
  // Un padre sin claves tiene un solo hijo y no hay con quien rebalancear.
  // Solo la raiz puede estar asi, y de ella se ocupa shrink_root.
  if (n == 0) return;

  // Prestar es mas barato que fusionar (no libera paginas ni cambia la
  // estructura del padre), asi que se intenta primero con los dos hermanos.
  if (i > 0) {
    fetch(padre);
    fetch(child_at(i - 1));
    if (key_count() > minimo) {
      borrow_from_left(padre, i);
      return;
    }
  }
  if (i < n) {
    fetch(padre);
    fetch(child_at(i + 1));
    if (key_count() > minimo) {
      borrow_from_right(padre, i);
      return;
    }
  }
  // Nadie tiene de sobra: se fusiona. Siempre hacia la izquierda, para que la
  // pagina que desaparece nunca sea la primera hoja del archivo.
  merge_children(padre, i > 0 ? i - 1 : i);
}

void BPlusTree::borrow_from_left(PageId padre, std::size_t i) {
  fetch(padre);
  const PageId izq = child_at(i - 1);
  const PageId der = child_at(i);
  const Key separador = key_at(i - 1);

  fetch(der);
  const bool hoja = is_leaf();
  Key nuevo_separador;

  if (hoja) {
    PageId sig_izq = kInvalidPage;
    PageId sig_der = kInvalidPage;
    auto a = read_leaf(izq, sig_izq);
    auto b = read_leaf(der, sig_der);
    b.insert(b.begin(), std::move(a.back()));
    a.pop_back();
    // La hoja guarda todas sus claves, asi que el separador es una COPIA de
    // la primera de la derecha.
    nuevo_separador = b.front().first;
    write_leaf(izq, a, sig_izq);
    write_leaf(der, b, sig_der);
  } else {
    PageId izq_hijo_izq = kInvalidPage;
    PageId der_hijo_izq = kInvalidPage;
    auto a = read_internal(izq, izq_hijo_izq);
    auto b = read_internal(der, der_hijo_izq);
    // Rotacion: el ultimo hijo de la izquierda pasa a ser el hijo izquierdo
    // de la derecha, el separador del padre BAJA a la derecha y la clave que
    // lo acompanaba SUBE al padre. Los internos no guardan la clave dos
    // veces, por eso aqui se mueve y en la hoja se copia.
    const Branch ultima = a.back();
    a.pop_back();
    b.insert(b.begin(), Branch{separador, der_hijo_izq});
    nuevo_separador = ultima.key;
    write_internal(izq, izq_hijo_izq, a);
    write_internal(der, ultima.child, b);
  }

  fetch(padre);
  std::vector<std::byte> clave(key_size_);
  RecordCodec::encode_value(key_column_, nuevo_separador, clave);
  scratch_.write_bytes(branch_offset(i - 1), clave);
  store(padre);
}

void BPlusTree::borrow_from_right(PageId padre, std::size_t i) {
  fetch(padre);
  const PageId izq = child_at(i);
  const PageId der = child_at(i + 1);
  const Key separador = key_at(i);

  fetch(izq);
  const bool hoja = is_leaf();
  Key nuevo_separador;

  if (hoja) {
    PageId sig_izq = kInvalidPage;
    PageId sig_der = kInvalidPage;
    auto a = read_leaf(izq, sig_izq);
    auto b = read_leaf(der, sig_der);
    a.push_back(std::move(b.front()));
    b.erase(b.begin());
    nuevo_separador = b.front().first;
    write_leaf(izq, a, sig_izq);
    write_leaf(der, b, sig_der);
  } else {
    PageId izq_hijo_izq = kInvalidPage;
    PageId der_hijo_izq = kInvalidPage;
    auto a = read_internal(izq, izq_hijo_izq);
    auto b = read_internal(der, der_hijo_izq);
    a.push_back(Branch{separador, der_hijo_izq});
    nuevo_separador = b.front().key;
    const PageId nuevo_hijo_izq = b.front().child;
    b.erase(b.begin());
    write_internal(izq, izq_hijo_izq, a);
    write_internal(der, nuevo_hijo_izq, b);
  }

  fetch(padre);
  std::vector<std::byte> clave(key_size_);
  RecordCodec::encode_value(key_column_, nuevo_separador, clave);
  scratch_.write_bytes(branch_offset(i), clave);
  store(padre);
}

void BPlusTree::merge_children(PageId padre, std::size_t j) {
  fetch(padre);
  PageId padre_hijo_izq = kInvalidPage;
  auto ramas = read_internal(padre, padre_hijo_izq);
  const PageId izq = j == 0 ? padre_hijo_izq : ramas[j - 1].child;
  const PageId der = ramas[j].child;
  const Key separador = ramas[j].key;

  fetch(izq);
  const bool hoja = is_leaf();

  if (hoja) {
    PageId sig_izq = kInvalidPage;
    PageId sig_der = kInvalidPage;
    auto a = read_leaf(izq, sig_izq);
    auto b = read_leaf(der, sig_der);
    if (a.size() + b.size() > order_) {
      throw IoError("fusionar las hojas " + std::to_string(izq) + " y " + std::to_string(der) +
                    " daria " + std::to_string(a.size() + b.size()) + " claves y el orden es " +
                    std::to_string(order_));
    }
    for (auto& e : b) a.push_back(std::move(e));
    // La derecha desaparece, asi que la cadena de hojas la saltea.
    write_leaf(izq, a, sig_der);
  } else {
    PageId izq_hijo_izq = kInvalidPage;
    PageId der_hijo_izq = kInvalidPage;
    auto a = read_internal(izq, izq_hijo_izq);
    auto b = read_internal(der, der_hijo_izq);
    if (a.size() + 1 + b.size() > order_) {
      throw IoError("fusionar los nodos " + std::to_string(izq) + " y " + std::to_string(der) +
                    " daria " + std::to_string(a.size() + 1 + b.size()) +
                    " claves y el orden es " + std::to_string(order_));
    }
    // El separador BAJA del padre: es la clave que separaba los dos bloques
    // de hijos y sin ella el nodo fusionado no sabria por donde partir.
    a.push_back(Branch{separador, der_hijo_izq});
    for (auto& r : b) a.push_back(r);
    write_internal(izq, izq_hijo_izq, a);
  }

  free_page(der);
  ramas.erase(ramas.begin() + static_cast<std::ptrdiff_t>(j));
  write_internal(padre, padre_hijo_izq, ramas);
}

void BPlusTree::shrink_root() {
  while (root_ != kInvalidPage) {
    fetch(root_);
    if (is_leaf()) {
      if (key_count() > 0) return;
      // La raiz era la unica hoja y se vacio: el arbol vuelve a estar vacio.
      free_page(root_);
      root_ = kInvalidPage;
      first_leaf_ = kInvalidPage;
      height_ = 0;
      return;
    }
    if (key_count() > 0) return;
    // Una raiz interna sin claves tiene un solo hijo: ese hijo pasa a ser la
    // raiz y el arbol pierde un nivel. Es la unica forma de que un B+ baje de
    // altura.
    const PageId unico = child_at(0);
    free_page(root_);
    root_ = unico;
    --height_;
  }
}

std::optional<RID> BPlusTree::locate(const Key& key) {
  if (root_ == kInvalidPage) return std::nullopt;
  const PageId hoja = descend(key, nullptr, /*por_la_derecha=*/false);
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
        posicion_ = RID{actual_, static_cast<SlotId>(i_)};
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

  [[nodiscard]] RID position() const override { return posicion_; }

 private:
  BPlusTree& duenio_;
  Page pagina_;  // buffer propio: no comparte scratch_ con el arbol
  RID posicion_;
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
  const PageId hoja = descend(lo, nullptr, /*por_la_derecha=*/false);
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
  PageId p = descend(lo, nullptr, /*por_la_derecha=*/false);
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
                                  std::size_t& hojas_en, std::size_t& contadas,
                                  std::vector<bool>& vivas) {
  if (id == 0 || id > disk_.page_count()) {
    return "el nodo " + std::to_string(id) + " no existe";
  }
  if (vivas[id]) return "la pagina " + std::to_string(id) + " cuelga del arbol dos veces";
  vivas[id] = true;
  disk_.read_page(id, scratch_);
  const bool hoja = is_leaf();
  const std::size_t n = key_count();

  if (n > order_) {
    return "el nodo " + std::to_string(id) + " tiene " + std::to_string(n) +
           " claves y el orden es " + std::to_string(order_);
  }
  // Un nodo interno sin claves no tiene como guiar la busqueda.
  if (!hoja && n < 1) {
    return "el nodo interno " + std::to_string(id) + " no tiene claves";
  }
  // Ocupacion minima: es la invariante que el rebalanceo del #17 sostiene.
  // La raiz esta exenta -- un arbol de tres claves tiene que poder existir.
  const std::size_t minimo = hoja ? min_leaf_ : min_internal_;
  if (profundidad > 1 && n < minimo) {
    return "el nodo " + std::to_string(id) + " tiene " + std::to_string(n) +
           " claves y el minimo de " + (hoja ? "una hoja" : "un interno") + " es " +
           std::to_string(minimo);
  }
  // Las claves de un nodo estan ordenadas y caen dentro del rango que le
  // asignan sus separadores.
  for (std::size_t i = 0; i < n; ++i) {
    const Key k = key_at(i);
    // Con claves unicas las de un nodo crecen estrictamente; con repetidas
    // pueden repetirse, tanto en una hoja como entre separadores cuando una
    // corrida de iguales abarca mas de dos hojas.
    if (i > 0 && compare(key_at(i - 1), k) > (unique_ ? -1 : 0)) {
      return "las claves del nodo " + std::to_string(id) + " no estan ordenadas";
    }
    if (lo != nullptr && compare(k, *lo) < 0) {
      return "el nodo " + std::to_string(id) + " tiene una clave menor que su separador izquierdo";
    }
    // Con claves unicas el rango de un hijo es [lo, hi); con repetidas es
    // [lo, hi], porque un split puede partir una corrida de iguales.
    if (hi != nullptr && compare(k, *hi) > (unique_ ? -1 : 0)) {
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
    const auto err =
        check_node(hijos[i], profundidad + 1, sub_lo, sub_hi, hojas_en, contadas, vivas);
    if (!err.empty()) return err;
    disk_.read_page(id, scratch_);  // el hijo dejo otra pagina en scratch_
  }
  return "";
}

std::string BPlusTree::check_invariants() {
  // Las paginas libres se recorren siempre: un arbol vacio despues de borrar
  // todo tiene free list y no tiene raiz.
  std::vector<bool> vivas(disk_.page_count() + 1, false);
  std::vector<bool> libres(disk_.page_count() + 1, false);
  {
    PageId p = free_head_;
    std::size_t vueltas = 0;
    while (p != kInvalidPage) {
      if (p == 0 || p > disk_.page_count()) {
        return "la free list pasa por la pagina " + std::to_string(p) + ", que no existe";
      }
      if (++vueltas > disk_.page_count()) return "la free list tiene un ciclo";
      if (libres[p]) return "la pagina " + std::to_string(p) + " esta dos veces en la free list";
      libres[p] = true;
      disk_.read_page(p, scratch_);
      p = scratch_.next();
    }
  }

  // Toda pagina del archivo esta o en el arbol o en la free list, y nunca en
  // las dos: si estuviera en las dos, el proximo allocate la entregaria por
  // debajo de un nodo que todavia la usa.
  const auto todas_ubicadas = [&]() -> std::string {
    for (PageId p = 1; p <= disk_.page_count(); ++p) {
      if (vivas[p] && libres[p]) {
        return "la pagina " + std::to_string(p) + " esta en el arbol y en la free list";
      }
      if (!vivas[p] && !libres[p]) {
        return "la pagina " + std::to_string(p) + " no esta ni en el arbol ni en la free list";
      }
    }
    return "";
  };

  if (root_ == kInvalidPage) {
    if (count_ != 0) return "el arbol dice tener " + std::to_string(count_) + " claves sin raiz";
    if (height_ != 0) return "el arbol vacio dice tener altura " + std::to_string(height_);
    return todas_ubicadas();
  }
  std::size_t hojas_en = 0;
  std::size_t contadas = 0;
  const auto err = check_node(root_, 1, nullptr, nullptr, hojas_en, contadas, vivas);
  if (!err.empty()) return err;
  if (const auto e = todas_ubicadas(); !e.empty()) return e;
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
      if (anterior && compare(*anterior, k) > (unique_ ? -1 : 0)) {
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
