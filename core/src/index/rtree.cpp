#include "quipudb/index/rtree.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>
#include <optional>
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
// Insercion (#116)
// ---------------------------------------------------------------------------

void RTree::insert(Point point, RID rid) {
  if (!std::isfinite(point.x) || !std::isfinite(point.y)) {
    throw InvalidRecord("coordenada no finita en el R-Tree: (" + std::to_string(point.x) + ", " +
                        std::to_string(point.y) + ")");
  }
  Item item;
  item.point = {point, rid};
  insert_item(item, 1);
  ++count_;
  save_meta();
}

void RTree::insert_item(const Item& item, std::size_t level) {
  if (root_ == kInvalidPage) {
    if (!item.is_point) {
      throw std::logic_error("un subarbol no se puede insertar en un R-Tree vacio");
    }
    RTreeNode hoja;
    hoja.points.push_back(item.point);
    root_ = allocate();
    write_node(root_, hoja);
    height_ = 1;
    return;
  }
  if (level == 0 || level > height_) {
    throw std::logic_error("nivel de insercion " + std::to_string(level) +
                           " fuera de un arbol de altura " + std::to_string(height_));
  }

  // Bajada: se guarda el camino con los nodos ya leidos para no volver a
  // leerlos al subir.
  struct Paso {
    PageId id;
    RTreeNode node;
    std::size_t hijo;
  };
  std::vector<Paso> camino;
  const Rect r = item.rect();
  PageId id = root_;
  RTreeNode node = read_node(id);
  for (std::size_t nivel = height_; nivel > level; --nivel) {
    const std::size_t i = choose_subtree(node, r);
    const PageId hijo = node.children[i].child;
    camino.push_back({id, std::move(node), i});
    id = hijo;
    node = read_node(id);
  }

  if (item.is_point) {
    node.points.push_back(item.point);
  } else {
    node.children.push_back(item.branch);
  }

  // Subida: partir lo que desborde y reajustar los MBR hasta donde cambien.
  while (true) {
    std::optional<RTreeBranch> hermano;
    if (node.size() > order_) {
      auto [izq, der] = split(node);
      write_node(id, izq);
      const PageId nuevo = allocate();
      write_node(nuevo, der);
      hermano = RTreeBranch{der.mbr(), nuevo};
      node = std::move(izq);
    } else {
      write_node(id, node);
    }

    if (camino.empty()) {
      if (hermano) {
        // Se partio la raiz: el arbol crece un nivel, y solo crece por arriba,
        // que es lo que mantiene todas las hojas a la misma altura.
        RTreeNode raiz;
        raiz.leaf = false;
        raiz.children = {RTreeBranch{node.mbr(), id}, *hermano};
        root_ = allocate();
        write_node(root_, raiz);
        ++height_;
      }
      return;
    }

    Paso padre = std::move(camino.back());
    camino.pop_back();
    const Rect mbr = node.mbr();
    if (!hermano && padre.node.children[padre.hijo].mbr == mbr) {
      // Nada cambio para los de arriba: no hace falta reescribirlos.
      return;
    }
    padre.node.children[padre.hijo].mbr = mbr;
    if (hermano) padre.node.children.push_back(*hermano);
    id = padre.id;
    node = std::move(padre.node);
  }
}

// Guttman: el hijo cuyo MBR menos crece en area. Con puntos alineados o
// repetidos las areas son todas 0 y el criterio no distingue nada, asi que se
// desempata por cuanto crece el semiperimetro y despues por el area menor.
std::size_t RTree::choose_subtree(const RTreeNode& node, const Rect& r) {
  std::size_t mejor = 0;
  double mejor_area = std::numeric_limits<double>::infinity();
  double mejor_margen = std::numeric_limits<double>::infinity();
  double mejor_tamano = std::numeric_limits<double>::infinity();
  for (std::size_t i = 0; i < node.children.size(); ++i) {
    const Rect& m = node.children[i].mbr;
    const double area = m.enlargement(r);
    const double margen = m.margin_enlargement(r);
    const double tamano = m.area();
    if (area < mejor_area || (area == mejor_area && margen < mejor_margen) ||
        (area == mejor_area && margen == mejor_margen && tamano < mejor_tamano)) {
      mejor = i;
      mejor_area = area;
      mejor_margen = margen;
      mejor_tamano = tamano;
    }
  }
  return mejor;
}

// Split cuadratico de Guttman.
//
// Se eligio el cuadratico y no el lineal (ver el contrato del #115): elige
// como semillas el par que mas area desperdiciaria juntas, y reparte el resto
// empezando por la entrada que mas "prefiere" un lado. Deja MBR que se solapan
// menos, y el solape es lo que obliga a una busqueda a bajar por varias ramas.
// Cuesta O(M^2) por split; con M = 113 son unas trece mil comparaciones, y los
// splits son raros frente a las busquedas. El 2.2.4 mide esa consecuencia.
//
// Igual que en `choose_subtree`, donde el area no distingue se desempata por
// semiperimetro: sin eso, con puntos alineados todas las semillas empatan en 0
// y se elige el primer par, que parte muy mal.
RTree::SplitGroups RTree::quadratic_split(std::span<const Rect> rects, std::size_t min_fill) {
  const std::size_t n = rects.size();
  if (n < 2 || 2 * min_fill > n) {
    throw std::logic_error("no se pueden repartir " + std::to_string(n) +
                           " entradas en dos grupos de al menos " + std::to_string(min_fill));
  }

  // PickSeeds.
  std::size_t s1 = 0;
  std::size_t s2 = 1;
  double peor = -std::numeric_limits<double>::infinity();
  double peor_margen = -std::numeric_limits<double>::infinity();
  for (std::size_t i = 0; i < n; ++i) {
    for (std::size_t j = i + 1; j < n; ++j) {
      const Rect juntos = rects[i].united(rects[j]);
      const double desperdicio = juntos.area() - rects[i].area() - rects[j].area();
      const double margen = juntos.margin();
      if (desperdicio > peor || (desperdicio == peor && margen > peor_margen)) {
        peor = desperdicio;
        peor_margen = margen;
        s1 = i;
        s2 = j;
      }
    }
  }

  SplitGroups g;
  g.first.push_back(s1);
  g.second.push_back(s2);
  Rect mbr1 = rects[s1];
  Rect mbr2 = rects[s2];
  std::vector<bool> asignado(n, false);
  asignado[s1] = asignado[s2] = true;
  std::size_t restantes = n - 2;

  while (restantes > 0) {
    // Si un grupo necesita todo lo que queda para llegar al minimo, se lo
    // lleva. Esto es lo que garantiza que el split termine con dos lados
    // validos, y funciona porque min_fill <= n/2.
    std::vector<std::size_t>* forzado = nullptr;
    if (g.first.size() + restantes == min_fill) forzado = &g.first;
    if (g.second.size() + restantes == min_fill) forzado = &g.second;
    if (forzado != nullptr) {
      for (std::size_t i = 0; i < n; ++i) {
        if (!asignado[i]) forzado->push_back(i);
      }
      break;
    }

    // PickNext: la entrada con mas diferencia entre ir a un lado o al otro.
    std::size_t elegida = n;
    double mayor = -1.0;
    double mayor_margen = -1.0;
    for (std::size_t i = 0; i < n; ++i) {
      if (asignado[i]) continue;
      const double dif = std::abs(mbr1.enlargement(rects[i]) - mbr2.enlargement(rects[i]));
      const double dif_margen =
          std::abs(mbr1.margin_enlargement(rects[i]) - mbr2.margin_enlargement(rects[i]));
      if (dif > mayor || (dif == mayor && dif_margen > mayor_margen)) {
        mayor = dif;
        mayor_margen = dif_margen;
        elegida = i;
      }
    }

    // Al lado que menos crece; despues al de menor semiperimetro ganado, al de
    // menor area y al que tiene menos entradas.
    const Rect& r = rects[elegida];
    const double a1 = mbr1.enlargement(r);
    const double a2 = mbr2.enlargement(r);
    const double m1 = mbr1.margin_enlargement(r);
    const double m2 = mbr2.margin_enlargement(r);
    bool al_primero = true;
    if (a1 != a2) {
      al_primero = a1 < a2;
    } else if (m1 != m2) {
      al_primero = m1 < m2;
    } else if (mbr1.area() != mbr2.area()) {
      al_primero = mbr1.area() < mbr2.area();
    } else {
      al_primero = g.first.size() <= g.second.size();
    }
    if (al_primero) {
      g.first.push_back(elegida);
      mbr1 = mbr1.united(r);
    } else {
      g.second.push_back(elegida);
      mbr2 = mbr2.united(r);
    }
    asignado[elegida] = true;
    --restantes;
  }
  return g;
}

std::pair<RTreeNode, RTreeNode> RTree::split(const RTreeNode& node) const {
  std::vector<Rect> rects;
  rects.reserve(node.size());
  if (node.leaf) {
    for (const auto& e : node.points) rects.push_back(Rect::of(e.point));
  } else {
    for (const auto& b : node.children) rects.push_back(b.mbr);
  }
  const SplitGroups g = quadratic_split(rects, min_fill_);

  RTreeNode a;
  RTreeNode b;
  a.leaf = b.leaf = node.leaf;
  const auto repartir = [&](const std::vector<std::size_t>& indices, RTreeNode& destino) {
    for (const std::size_t i : indices) {
      if (node.leaf) {
        destino.points.push_back(node.points[i]);
      } else {
        destino.children.push_back(node.children[i]);
      }
    }
  };
  repartir(g.first, a);
  repartir(g.second, b);
  return {std::move(a), std::move(b)};
}

// ---------------------------------------------------------------------------
// Busqueda por rectangulo (#117)
// ---------------------------------------------------------------------------

std::vector<RTreeLeafEntry> RTree::search(const Rect& region) {
  if (!std::isfinite(region.min_x) || !std::isfinite(region.min_y) ||
      !std::isfinite(region.max_x) || !std::isfinite(region.max_y)) {
    throw InvalidRecord("region de busqueda con coordenadas no finitas");
  }
  std::vector<RTreeLeafEntry> out;
  if (root_ == kInvalidPage || region.min_x > region.max_x || region.min_y > region.max_y) {
    return out;
  }
  search_node(root_, region, out);
  stats_.records_returned += out.size();
  return out;
}

void RTree::search_node(PageId id, const Rect& region, std::vector<RTreeLeafEntry>& out) {
  const RTreeNode node = read_node(id);
  if (node.leaf) {
    stats_.records_examined += node.points.size();
    for (const auto& e : node.points) {
      if (region.contains(e.point)) out.push_back(e);
    }
    return;
  }
  // La poda: un subarbol cuyo MBR no toca la region no puede tener nada
  // dentro, porque su MBR cubre todos sus puntos. Es exactamente lo que
  // check_invariants garantiza.
  for (const auto& b : node.children) {
    if (region.intersects(b.mbr)) search_node(b.child, region, out);
  }
}

std::vector<RTreeLeafEntry> RTree::scan() {
  std::vector<RTreeLeafEntry> out;
  out.reserve(count_);
  if (root_ != kInvalidPage) scan_node(root_, out);
  return out;
}

void RTree::scan_node(PageId id, std::vector<RTreeLeafEntry>& out) {
  const RTreeNode node = read_node(id);
  if (node.leaf) {
    out.insert(out.end(), node.points.begin(), node.points.end());
    return;
  }
  for (const auto& b : node.children) scan_node(b.child, out);
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
