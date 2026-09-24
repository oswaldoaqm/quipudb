#pragma once

// R-Tree (issues #115-#118): indice secundario de puntos en dos dimensiones.
//
// Que es aqui
// -----------
//
//   El mismo papel que el B+ no agrupado (#16), pero para puntos: las hojas
//   guardan (punto, RID) y el registro se lee despues de la tabla con
//   `TableFile::read(rid)`. Por eso solo se puede montar sobre un heap file,
//   que es la unica organizacion donde un RID sigue apuntando al mismo
//   registro despues de insertar.
//
//   Todavia NO implementa `Index`: `Index::insert(const Key&, RID)` recibe una
//   sola columna y un punto son dos. Esa integracion, junto con la extension
//   del SQL, es de 2.2.2 y 2.2.3.
//
// Geometria
// ---------
//
//   `x` es la longitud e `y` la latitud, en grados. Los MBR son PLANOS: sirven
//   para podar aunque la distancia final sea Haversine, porque una consulta
//   geodesica (2.2.1.B) primero convierte su region a una caja en grados y
//   despues afina.
//
// Formato en disco
// ----------------
//
//   Pagina 0: cabecera del DiskManager + area meta (ver `Meta`).
//   Cada nodo es una pagina. Byte 0 del body: tipo. El numero de entradas va
//   en `record_count` de la cabecera de la pagina, como en el B+.
//
//     hoja      [tipo][x 8][y 8][pagina 4][slot 2] ... 22 bytes por entrada
//     interno   [tipo][min_x 8][min_y 8][max_x 8][max_y 8][hijo 4] ... 36 bytes
//
//   Las paginas libres van a una free list encadenada por el campo `next` de
//   la cabecera, con la cabeza en el area meta, igual que el heap file (#8) y
//   el B+ (#17).
//
// Orden y ocupacion
// -----------------
//
//   `order` es el maximo de entradas por nodo; 0 significa el maximo que cabe
//   en la pagina (con 4 KB son 113, que es lo que admite un nodo interno). Las
//   pruebas lo bajan a 4 para tener arboles altos con pocos puntos, como hizo
//   el #14.
//
//   La ocupacion minima es m = max(2, 40 % de M). Guttman exige m <= M/2 para
//   que un split pueda dejar los dos lados validos; el 40 % es el compromiso
//   que recomienda el propio articulo. De ahi que el orden minimo sea 4.

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <span>
#include <string>
#include <utility>
#include <vector>

#include "quipudb/catalog/table.hpp"
#include "quipudb/catalog/types.hpp"
#include "quipudb/storage/disk_manager.hpp"
#include "quipudb/storage/page.hpp"

namespace quipudb {

/// Un punto del plano. `x` = longitud, `y` = latitud.
struct Point {
  double x = 0.0;
  double y = 0.0;

  friend constexpr bool operator==(const Point&, const Point&) = default;
};

/// Rectangulo alineado a los ejes: el MBR de un nodo o de una busqueda.
struct Rect {
  double min_x = 0.0;
  double min_y = 0.0;
  double max_x = 0.0;
  double max_y = 0.0;

  /// El rectangulo degenerado que ocupa exactamente un punto.
  [[nodiscard]] static constexpr Rect of(Point p) noexcept { return {p.x, p.y, p.x, p.y}; }

  [[nodiscard]] constexpr bool contains(Point p) const noexcept {
    return min_x <= p.x && p.x <= max_x && min_y <= p.y && p.y <= max_y;
  }
  [[nodiscard]] constexpr bool contains(const Rect& r) const noexcept {
    return min_x <= r.min_x && r.max_x <= max_x && min_y <= r.min_y && r.max_y <= max_y;
  }
  /// Comparten al menos un punto. Tocarse en un borde cuenta: una busqueda
  /// cuyo limite coincide con el de un nodo tiene que bajar por el.
  [[nodiscard]] constexpr bool intersects(const Rect& r) const noexcept {
    return min_x <= r.max_x && r.min_x <= max_x && min_y <= r.max_y && r.min_y <= max_y;
  }
  [[nodiscard]] constexpr double area() const noexcept {
    return (max_x - min_x) * (max_y - min_y);
  }
  [[nodiscard]] constexpr Rect united(const Rect& r) const noexcept {
    return {min_x < r.min_x ? min_x : r.min_x, min_y < r.min_y ? min_y : r.min_y,
            max_x > r.max_x ? max_x : r.max_x, max_y > r.max_y ? max_y : r.max_y};
  }
  /// Cuanto crece el area si este rectangulo se amplia para incluir `r`. Es
  /// el criterio con el que la insercion elige subarbol (#116).
  [[nodiscard]] constexpr double enlargement(const Rect& r) const noexcept {
    return united(r).area() - area();
  }
  /// Semiperimetro. Desempata cuando las areas no dicen nada: con puntos
  /// alineados o repetidos todos los MBR tienen area 0, y entonces el unico
  /// criterio que distingue un rectangulo largo de uno corto es este.
  [[nodiscard]] constexpr double margin() const noexcept {
    return (max_x - min_x) + (max_y - min_y);
  }
  [[nodiscard]] constexpr double margin_enlargement(const Rect& r) const noexcept {
    return united(r).margin() - margin();
  }

  friend constexpr bool operator==(const Rect&, const Rect&) = default;
};

/// Entrada de una hoja: el punto y donde vive su registro.
struct RTreeLeafEntry {
  Point point;
  RID rid;

  friend constexpr bool operator==(const RTreeLeafEntry&, const RTreeLeafEntry&) = default;
};

/// Entrada de un nodo interno: el MBR de un hijo y su pagina.
struct RTreeBranch {
  Rect mbr;
  PageId child = kInvalidPage;

  friend constexpr bool operator==(const RTreeBranch&, const RTreeBranch&) = default;
};

/// Un nodo ya leido de disco. Exactamente una de las dos listas tiene datos,
/// segun `leaf`.
struct RTreeNode {
  bool leaf = true;
  std::vector<RTreeLeafEntry> points;
  std::vector<RTreeBranch> children;

  [[nodiscard]] std::size_t size() const noexcept {
    return leaf ? points.size() : children.size();
  }
  /// El MBR que cubre todas las entradas. Un nodo vacio no tiene MBR: pedirlo
  /// es un error de logica y lanza std::logic_error.
  [[nodiscard]] Rect mbr() const;

  friend bool operator==(const RTreeNode&, const RTreeNode&) = default;
};

class RTree {
 public:
  static constexpr std::byte kLeaf{0};
  static constexpr std::byte kInternal{1};

  static constexpr std::size_t kLeafEntrySize = 2 * sizeof(double) + sizeof(PageId) + sizeof(SlotId);
  static constexpr std::size_t kBranchSize = 4 * sizeof(double) + sizeof(PageId);
  static constexpr std::size_t kMinOrder = 4;

  /// Abre el archivo o lo crea vacio. `order = 0` usa el maximo que cabe en
  /// la pagina. Reabrir con otro orden es un SchemaError, y con otra version
  /// del formato un IoError.
  explicit RTree(std::filesystem::path path, std::size_t page_size = kDefaultPageSize,
                 std::size_t order = 0);

  /// Entradas maximas por nodo que caben en una pagina de `page_size` bytes.
  [[nodiscard]] static std::size_t max_order(std::size_t page_size);

  [[nodiscard]] std::size_t order() const noexcept { return order_; }
  [[nodiscard]] std::size_t min_fill() const noexcept { return min_fill_; }
  /// Puntos guardados.
  [[nodiscard]] std::uint64_t size() const noexcept { return count_; }
  /// 0 con el arbol vacio; 1 cuando la raiz es una hoja.
  [[nodiscard]] std::size_t height() const noexcept { return height_; }
  [[nodiscard]] PageId root() const noexcept { return root_; }
  /// Paginas en la free list, listas para reutilizarse.
  [[nodiscard]] std::size_t free_pages();
  /// Paginas de datos del archivo, contando las libres.
  [[nodiscard]] PageId page_count() const noexcept { return disk_.page_count(); }

  /// Inserta un punto (#116). Coordenadas no finitas -- NaN o infinito -- son
  /// un InvalidRecord: un NaN hace falsa cualquier comparacion y rompe los MBR
  /// de todo el camino sin que nada lo denuncie.
  void insert(Point point, RID rid);

  /// Los puntos que caen dentro de `region`, bordes incluidos (#117).
  ///
  /// Solo baja por los hijos cuyo MBR se solapa con la region: es la primitiva
  /// sobre la que se construyen el radio, el k-NN y el poligono (2.2.1.B), que
  /// empiezan todos podando por rectangulo. Reporta en `stats()` las paginas
  /// leidas, las entradas de hoja examinadas y las devueltas.
  ///
  /// Una region con coordenadas no finitas es InvalidRecord. Una region
  /// invertida (min > max) no contiene nada y devuelve vacio sin leer, igual
  /// que un BETWEEN con los limites al reves.
  [[nodiscard]] std::vector<RTreeLeafEntry> search(const Rect& region);

  /// Borra el punto con ese RID (#118). Tienen que coincidir los dos: varios
  /// registros pueden estar en el mismo lugar. Devuelve false si no estaba.
  ///
  /// Los MBR afectados encogen hasta la raiz. Un nodo que queda por debajo
  /// del minimo se quita y sus entradas se reinsertan desde la raiz -- las de
  /// un nodo interno a su misma altura, como subarboles --, y su pagina va a
  /// la free list. Si la raiz queda con un solo hijo, el arbol baja un nivel.
  bool remove(Point point, RID rid);

  /// Todas las entradas de las hojas, sin podar. Para pruebas y para
  /// reconstruir; las consultas usan la busqueda por rectangulo (#117).
  [[nodiscard]] std::vector<RTreeLeafEntry> scan();

  /// Reparto del split cuadratico de Guttman, como funcion pura: recibe los
  /// M+1 rectangulos de un nodo desbordado y devuelve que indices van a cada
  /// lado, con al menos `min_fill` en cada uno. Ver el comentario en rtree.cpp.
  struct SplitGroups {
    std::vector<std::size_t> first;
    std::vector<std::size_t> second;
  };
  [[nodiscard]] static SplitGroups quadratic_split(std::span<const Rect> rects,
                                                   std::size_t min_fill);

  /// Recorre el arbol entero y devuelve "" si todo cuadra, o la primera
  /// violacion encontrada: un MBR que no es exactamente la union de sus
  /// hijos, hojas a distinta altura, un nodo fuera de [m, M] (salvo la raiz),
  /// una raiz interna con menos de dos hijos, o un conteo de puntos que no
  /// coincide con el de la meta.
  [[nodiscard]] std::string check_invariants();

  [[nodiscard]] const OpStats& stats() const noexcept { return stats_; }
  void reset_stats() noexcept { stats_.reset(); }

  void flush();

  /// Serializacion de un nodo a una pagina y de vuelta. Son funciones puras:
  /// no tocan el archivo, y por eso se pueden probar solas.
  static void encode(const RTreeNode& node, Page& page);
  [[nodiscard]] static RTreeNode decode(const Page& page);

 private:
  // Version 1: primer formato del R-Tree.
  static constexpr std::uint32_t kMetaVersion = 1;

  struct Meta {
    std::uint32_t version = 0;
    std::uint32_t order = 0;
    std::uint32_t root = kInvalidPage;
    std::uint32_t height = 0;
    std::uint32_t free_head = kInvalidPage;
    std::uint32_t reserved = 0;
    std::uint64_t count = 0;
  };
  static_assert(sizeof(Meta) <= DiskManager::kMetaSize);

  void load_meta();
  void save_meta();

  [[nodiscard]] RTreeNode read_node(PageId id);
  void write_node(PageId id, const RTreeNode& node);
  /// Una pagina para un nodo nuevo: de la free list si hay, si no crece el
  /// archivo.
  [[nodiscard]] PageId allocate();
  /// Manda la pagina a la free list. No encoge el archivo.
  void free_page(PageId id);

  /// Un paso del camino desde la raiz: el nodo ya leido y por que hijo se
  /// siguio. Lo usan la insercion para subir y la eliminacion para condensar.
  struct PathStep {
    PageId id = kInvalidPage;
    RTreeNode node;
    std::size_t child = 0;
  };

  /// Lo que se inserta en un nodo: un punto en una hoja, o un subarbol en un
  /// nodo interno. La eliminacion (#118) reinserta de las dos clases.
  struct Item {
    bool is_point = true;
    RTreeLeafEntry point;
    RTreeBranch branch;

    [[nodiscard]] Rect rect() const noexcept {
      return is_point ? Rect::of(point.point) : branch.mbr;
    }
  };

  /// Inserta `item` en un nodo del nivel `level`, contando desde las hojas
  /// (hoja = 1, raiz = height_). Un punto va al nivel 1; un subarbol cuyas
  /// hojas estan k niveles abajo va al nivel k + 1.
  void insert_item(const Item& item, std::size_t level);

  /// El hijo que menos hay que ampliar para meter `r`.
  [[nodiscard]] static std::size_t choose_subtree(const RTreeNode& node, const Rect& r);

  /// Parte un nodo desbordado en dos con `quadratic_split`.
  [[nodiscard]] std::pair<RTreeNode, RTreeNode> split(const RTreeNode& node) const;

  void scan_node(PageId id, std::vector<RTreeLeafEntry>& out);

  /// Busca la hoja que tiene exactamente (point, rid), bajando por todos los
  /// hijos cuyo MBR contiene el punto: con solape puede haber mas de uno.
  /// Deja en `path` el camino hasta el padre de la hoja.
  bool find_leaf(PageId id, Point point, RID rid, std::vector<PathStep>& path, PathStep& leaf);

  /// Mientras la raiz sea interna con un solo hijo, ese hijo pasa a ser la
  /// raiz y el arbol baja un nivel.
  void shrink_root();
  void search_node(PageId id, const Rect& region, std::vector<RTreeLeafEntry>& out);

  /// Verifica el subarbol con raiz en `id`, que esta a profundidad `nivel`
  /// (1 = raiz). Devuelve el MBR del nodo en `mbr` y los puntos que contiene
  /// en `puntos`.
  [[nodiscard]] std::string check_node(PageId id, std::size_t nivel, Rect& mbr,
                                       std::uint64_t& puntos);

  DiskManager disk_;
  Page scratch_;
  std::size_t order_ = 0;
  std::size_t min_fill_ = 0;
  PageId root_ = kInvalidPage;
  std::size_t height_ = 0;
  std::uint64_t count_ = 0;
  PageId free_head_ = kInvalidPage;
  OpStats stats_;
};

}  // namespace quipudb
