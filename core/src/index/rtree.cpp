#include "quipudb/index/rtree.hpp"

#include <algorithm>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <utility>

#include "quipudb/error.hpp"

namespace quipudb {

// ---------------------------------------------------------------------------
// Nodo
// ---------------------------------------------------------------------------

Rect RTreeNode::mbr() const {
  if (size() == 0) {
    throw std::logic_error("un nodo vacio no tiene MBR");
  }
  if (leaf) {
    Rect r = Rect::of(points.front().point);
    for (const auto& e : points) r = r.united(Rect::of(e.point));
    return r;
  }
  Rect r = children.front().mbr;
  for (const auto& b : children) r = r.united(b.mbr);
  return r;
}

// ---------------------------------------------------------------------------
// Construccion y area meta
// ---------------------------------------------------------------------------

std::size_t RTree::max_order(std::size_t page_size) {
  const std::size_t body = page_size - Page::kHeaderSize;
  // Byte 0 del body es el tipo; el resto son entradas.
  const std::size_t cap_hoja = (body - 1) / kLeafEntrySize;
  const std::size_t cap_interno = (body - 1) / kBranchSize;
  return std::min(cap_hoja, cap_interno);
}

RTree::RTree(std::filesystem::path path, std::size_t page_size, std::size_t order)
    : disk_(std::move(path), page_size), scratch_(disk_.page_size()) {
  const std::size_t maximo = max_order(disk_.page_size());
  order_ = order == 0 ? maximo : order;
  if (order_ < kMinOrder) {
    throw SchemaError("el orden del R-Tree tiene que ser al menos " + std::to_string(kMinOrder) +
                      ": con menos, la ocupacion minima de 2 no deja partir un nodo en dos "
                      "mitades validas");
  }
  if (order_ > maximo) {
    throw SchemaError("orden " + std::to_string(order_) + " para el R-Tree: en una pagina de " +
                      std::to_string(disk_.page_size()) + " bytes entran " +
                      std::to_string(maximo));
  }
  // m = max(2, 40 % de M). Con M >= 4 se cumple m <= M/2, que es lo que
  // Guttman necesita para que un split deje los dos lados validos.
  min_fill_ = std::max<std::size_t>(2, order_ * 2 / 5);
  load_meta();
}

void RTree::load_meta() {
  Meta m;
  std::vector<std::byte> buf(sizeof(Meta));
  disk_.read_meta(buf);
  std::memcpy(&m, buf.data(), sizeof(Meta));

  if (m.version == 0 && disk_.page_count() == 0) {
    // Archivo recien creado.
    root_ = kInvalidPage;
    height_ = 0;
    count_ = 0;
    free_head_ = kInvalidPage;
    save_meta();
    return;
  }
  if (m.version != kMetaVersion) {
    throw IoError("'" + disk_.path().string() + "' usa el formato de R-Tree " +
                  std::to_string(m.version) + " y este core escribe el " +
                  std::to_string(kMetaVersion));
  }
  if (m.order != order_) {
    throw SchemaError("'" + disk_.path().string() + "' se creo con orden " +
                      std::to_string(m.order) + " y se pidio abrirlo con " +
                      std::to_string(order_));
  }
  root_ = m.root;
  height_ = m.height;
  count_ = m.count;
  free_head_ = m.free_head;
}

void RTree::save_meta() {
  Meta m;
  m.version = kMetaVersion;
  m.order = static_cast<std::uint32_t>(order_);
  m.root = root_;
  m.height = static_cast<std::uint32_t>(height_);
  m.free_head = free_head_;
  m.count = count_;
  std::vector<std::byte> buf(sizeof(Meta));
  std::memcpy(buf.data(), &m, sizeof(Meta));
  disk_.write_meta(buf);
}

void RTree::flush() {
  save_meta();
  disk_.flush();
}

// ---------------------------------------------------------------------------
// Serializacion
// ---------------------------------------------------------------------------

void RTree::encode(const RTreeNode& node, Page& page) {
  const std::size_t n = node.size();
  if (n > std::numeric_limits<std::uint16_t>::max()) {
    throw std::logic_error("un nodo de R-Tree no puede tener " + std::to_string(n) +
                           " entradas");
  }
  page.clear();
  page.set_next(kInvalidPage);
  page.set_record_count(static_cast<std::uint16_t>(n));
  page.write<std::byte>(0, node.leaf ? kLeaf : kInternal);

  // Campo por campo y no con memcpy del struct: asi el formato no depende del
  // relleno que el compilador meta en RID o en Rect. Un nodo que no cabe hace
  // que `write` lance std::out_of_range, porque es un error de logica.
  std::size_t off = 1;
  if (node.leaf) {
    for (const auto& e : node.points) {
      page.write<double>(off, e.point.x);
      page.write<double>(off + 8, e.point.y);
      page.write<PageId>(off + 16, e.rid.page);
      page.write<SlotId>(off + 20, e.rid.slot);
      off += kLeafEntrySize;
    }
  } else {
    for (const auto& b : node.children) {
      page.write<double>(off, b.mbr.min_x);
      page.write<double>(off + 8, b.mbr.min_y);
      page.write<double>(off + 16, b.mbr.max_x);
      page.write<double>(off + 24, b.mbr.max_y);
      page.write<PageId>(off + 32, b.child);
      off += kBranchSize;
    }
  }
}

RTreeNode RTree::decode(const Page& page) {
  const auto tipo = page.read<std::byte>(0);
  if (tipo != kLeaf && tipo != kInternal) {
    throw IoError("la pagina no es un nodo de R-Tree: tipo " +
                  std::to_string(static_cast<int>(tipo)));
  }
  RTreeNode node;
  node.leaf = tipo == kLeaf;
  const std::size_t n = page.record_count();
  const std::size_t ancho = node.leaf ? kLeafEntrySize : kBranchSize;
  if (1 + n * ancho > page.body_size()) {
    throw IoError("un nodo de R-Tree dice tener " + std::to_string(n) +
                  " entradas y no caben en su pagina");
  }

  std::size_t off = 1;
  if (node.leaf) {
    node.points.reserve(n);
    for (std::size_t i = 0; i < n; ++i, off += kLeafEntrySize) {
      RTreeLeafEntry e;
      e.point.x = page.read<double>(off);
      e.point.y = page.read<double>(off + 8);
      e.rid.page = page.read<PageId>(off + 16);
      e.rid.slot = page.read<SlotId>(off + 20);
      node.points.push_back(e);
    }
  } else {
    node.children.reserve(n);
    for (std::size_t i = 0; i < n; ++i, off += kBranchSize) {
      RTreeBranch b;
      b.mbr.min_x = page.read<double>(off);
      b.mbr.min_y = page.read<double>(off + 8);
      b.mbr.max_x = page.read<double>(off + 16);
      b.mbr.max_y = page.read<double>(off + 24);
      b.child = page.read<PageId>(off + 32);
      node.children.push_back(b);
    }
  }
  return node;
}

// ---------------------------------------------------------------------------
// Paginas
// ---------------------------------------------------------------------------

RTreeNode RTree::read_node(PageId id) {
  disk_.read_page(id, scratch_);
  ++stats_.pages_read;
  return decode(scratch_);
}

void RTree::write_node(PageId id, const RTreeNode& node) {
  encode(node, scratch_);
  disk_.write_page(id, scratch_);
  ++stats_.pages_written;
}

PageId RTree::allocate() {
  if (free_head_ == kInvalidPage) {
    return disk_.allocate_page();
  }
  // Se reutiliza una pagina que libero una eliminacion (#118). Hay que leer el
  // siguiente eslabon antes de que alguien escriba el nodo nuevo encima.
  disk_.read_page(free_head_, scratch_);
  ++stats_.pages_read;
  const PageId id = free_head_;
  free_head_ = scratch_.next();
  return id;
}

void RTree::free_page(PageId id) {
  scratch_.clear();
  scratch_.set_record_count(0);
  scratch_.set_next(free_head_);
  disk_.write_page(id, scratch_);
  ++stats_.pages_written;
  free_head_ = id;
}

std::size_t RTree::free_pages() {
  std::size_t n = 0;
  PageId id = free_head_;
  // El tope evita colgarse si una corrupcion hizo un ciclo en la lista.
  while (id != kInvalidPage && n <= disk_.page_count()) {
    disk_.read_page(id, scratch_);
    ++stats_.pages_read;
    id = scratch_.next();
    ++n;
  }
  return n;
}

// ---------------------------------------------------------------------------
// Invariantes
// ---------------------------------------------------------------------------

std::string RTree::check_invariants() {
  if (root_ == kInvalidPage) {
    if (height_ != 0 || count_ != 0) {
      return "arbol sin raiz con altura " + std::to_string(height_) + " y " +
             std::to_string(count_) + " puntos";
    }
    return "";
  }
  if (height_ == 0) return "hay raiz pero la altura es 0";

  Rect mbr;
  std::uint64_t puntos = 0;
  if (auto error = check_node(root_, 1, mbr, puntos); !error.empty()) return error;
  if (puntos != count_) {
    return "la meta dice " + std::to_string(count_) + " puntos y el arbol tiene " +
           std::to_string(puntos);
  }
  return "";
}

std::string RTree::check_node(PageId id, std::size_t nivel, Rect& mbr, std::uint64_t& puntos) {
  if (id == kInvalidPage || id == 0 || id > disk_.page_count()) {
    return "puntero a la pagina inexistente " + std::to_string(id);
  }
  // Cortar por profundidad tambien impide colgarse con un puntero que vuelve
  // hacia arriba.
  if (nivel > height_) {
    return "la pagina " + std::to_string(id) + " esta mas abajo que la altura " +
           std::to_string(height_);
  }
  const RTreeNode node = read_node(id);
  const std::string donde = "pagina " + std::to_string(id);

  if (node.leaf != (nivel == height_)) {
    return donde + (node.leaf ? " es una hoja" : " es un nodo interno") + " a profundidad " +
           std::to_string(nivel) + " y la altura es " + std::to_string(height_) +
           ": las hojas no estan todas al mismo nivel";
  }
  if (node.size() > order_) {
    return donde + " tiene " + std::to_string(node.size()) + " entradas y el orden es " +
           std::to_string(order_);
  }
  if (id == root_) {
    if (node.size() == 0) return "la raiz esta vacia";
    if (!node.leaf && node.size() < 2) return "la raiz interna tiene un solo hijo";
  } else if (node.size() < min_fill_) {
    return donde + " tiene " + std::to_string(node.size()) + " entradas y el minimo es " +
           std::to_string(min_fill_);
  }

  if (node.leaf) {
    puntos += node.points.size();
  } else {
    for (const auto& rama : node.children) {
      Rect real;
      if (auto error = check_node(rama.child, nivel + 1, real, puntos); !error.empty()) {
        return error;
      }
      // Igualdad exacta y no solo cobertura: un MBR que cubre de mas no rompe
      // las busquedas, pero las hace bajar por ramas que no tocan nada. Despues
      // de borrar (#118) los MBR tienen que haber encogido.
      if (!(rama.mbr == real)) {
        return donde + " guarda para el hijo " + std::to_string(rama.child) +
               " un MBR que no es exactamente la union de sus entradas";
      }
    }
  }
  mbr = node.mbr();
  return "";
}

}  // namespace quipudb
