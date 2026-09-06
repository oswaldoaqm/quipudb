#pragma once

// B+ Tree sobre paginas de disco (issue #14).
//
// Es la maquinaria comun de los dos indices B+ que pide el enunciado (2.1.2):
// el agrupado (#15), donde las hojas guardan el registro completo, y el no
// agrupado (#16), donde guardan un RID. Por eso el arbol no sabe que hay
// dentro de una entrada: guarda `payload_size` bytes opacos por clave y deja
// que cada indice los interprete.
//
//   BPlusTree(archivo, columna_clave, payload_size)
//     #15  payload = registro serializado (RecordCodec::size())
//     #16  payload = un RID (6 bytes)
//
// Estructura
// ----------
//
//   Cada nodo es una pagina. La pagina 0 es la cabecera del archivo
//   (DiskManager) y su area meta guarda la raiz, la altura y el orden.
//
//   Nodo hoja                          Nodo interno
//   [1 byte tipo = 0]                  [1 byte tipo = 1]
//   [clave][payload]                   [PageId hijo izquierdo]
//   [clave][payload]                   [clave][PageId hijo]
//   ...                                [clave][PageId hijo]
//   cabecera de Page:                  ...
//     record_count = claves            n claves, n+1 hijos
//     next = siguiente hoja
//
//   Las hojas estan encadenadas por el campo `next` de la cabecera de pagina,
//   asi que recorrerlas en orden no cuesta bajar por el arbol: es lo que usan
//   el ORDER BY del #20 y el `range_search` del #15.
//
//   Los nodos internos NO guardan payload: solo claves separadoras y punteros.
//   Una clave separadora se copia hacia arriba en un split de hoja (la clave
//   sigue viviendo en la hoja) y se mueve hacia arriba en un split de nodo
//   interno (deja de estar abajo). Confundir esos dos casos es el error
//   clasico al implementar un B+.
//
// Orden
// -----
//
//   El orden es la cantidad maxima de claves por nodo. Por omision se calcula
//   el maximo que entra en una pagina; se puede fijar a mano, que es lo que
//   hacen las pruebas para forzar arboles altos con pocas claves.
//
//   Un nodo que se pasa del orden se parte por la mitad. La raiz es el unico
//   nodo al que se le permite tener menos de la mitad.
//
// Claves
// ------
//
//   La clave se serializa con el mismo codec que los registros (#7), asi que
//   ocupa `Column::byte_size()` bytes fijos: un INT 4, un VARCHAR(n) n. Eso
//   permite direccionar la entrada `i` sin recorrer las anteriores.
//
// Claves repetidas
// ----------------
//
//   Un arbol puede ser unico (la clave primaria de una tabla) o admitir
//   repetidas (un indice secundario sobre una columna cualquiera). Se elige al
//   crearlo y queda grabado.
//
//   La diferencia esta en como se baja. Con claves unicas, una separadora
//   igual a la buscada significa que la clave vive a la derecha, asi que se
//   baja por ahi. Con repetidas eso no vale: un split puede partir una corrida
//   de claves iguales, y entonces quedan a los dos lados de la separadora. Con
//   repetidas se baja por la IZQUIERDA en caso de empate -- la primera hoja
//   que podria contenerla -- y desde ahi se sigue la cadena de hojas mientras
//   las claves sigan siendo iguales.
//
//   Por eso las busquedas con repetidas usan `entries_from`, que es
//   justamente bajar una vez y seguir la cadena.

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <utility>
#include <vector>

#include "quipudb/catalog/record_codec.hpp"
#include "quipudb/catalog/table.hpp"
#include "quipudb/storage/disk_manager.hpp"
#include "quipudb/storage/page.hpp"

namespace quipudb {

/// Recorre las entradas del arbol de a una, siguiendo la cadena de hojas.
/// Deja de valer en cuanto el arbol se modifica.
class EntryCursor {
 public:
  virtual ~EntryCursor() = default;
  virtual bool next(Key& key, std::vector<std::byte>& payload) = 0;

  /// Hoja y posicion de la entrada que acaba de devolver `next`. Solo vale
  /// inmediatamente despues de un `next` que devolvio true.
  [[nodiscard]] virtual RID position() const = 0;
};

class BPlusTree {
 public:
  static constexpr std::byte kLeaf{0};
  static constexpr std::byte kInternal{1};

  /// Abre el arbol si el archivo existe, o lo crea vacio.
  ///
  /// `order` es la cantidad maxima de claves por nodo; 0 significa la maxima
  /// que entre en una pagina. Lanza SchemaError si el orden pedido no entra o
  /// es menor que 2 (con menos de 2 claves por nodo un split no termina).
  BPlusTree(std::filesystem::path path, Column key_column, std::size_t payload_size,
            std::size_t page_size = kDefaultPageSize, std::size_t order = 0, bool unique = true);

  /// Si el arbol rechaza claves repetidas.
  [[nodiscard]] bool unique() const noexcept { return unique_; }

  [[nodiscard]] const Column& key_column() const noexcept { return key_column_; }
  [[nodiscard]] std::size_t key_size() const noexcept { return key_size_; }
  [[nodiscard]] std::size_t payload_size() const noexcept { return payload_size_; }

  /// Claves maximas por nodo.
  [[nodiscard]] std::size_t order() const noexcept { return order_; }

  /// Altura: 0 si esta vacio, 1 si la raiz es una hoja.
  [[nodiscard]] std::size_t height() const noexcept { return height_; }

  /// Claves guardadas.
  [[nodiscard]] std::size_t size() const noexcept { return count_; }

  [[nodiscard]] PageId root() const noexcept { return root_; }
  [[nodiscard]] PageId first_leaf() const noexcept { return first_leaf_; }
  [[nodiscard]] PageId page_count() const noexcept { return disk_.page_count(); }
  [[nodiscard]] std::uintmax_t file_size() const { return disk_.file_size(); }

  /// Inserta la clave con su payload. En un arbol unico, lanza DuplicateKey si
  /// la clave ya esta; en uno con repetidas, la agrega despues de las iguales.
  /// Lanza SchemaError si la clave no es del tipo de la columna o el payload
  /// no mide `payload_size`.
  void insert(const Key& key, std::span<const std::byte> payload);

  /// Payload de esa clave, o nullopt si no esta.
  [[nodiscard]] std::optional<std::vector<std::byte>> find(const Key& key);

  /// Reemplaza el payload de una clave existente. Devuelve false si no esta.
  bool set_payload(const Key& key, std::span<const std::byte> payload);

  /// Quita la clave. Devuelve false si no estaba.
  ///
  /// No rebalancea: la hoja puede quedar por debajo de la mitad, e incluso
  /// vacia, y sigue en el arbol y en la cadena. El arbol se mantiene
  /// correcto -- ordenado, balanceado en altura y con la cadena completa --
  /// pero desperdicia espacio. La fusion y la redistribucion son el #17.
  bool erase(const Key& key);

  /// Quita todas las entradas con esa clave. Devuelve cuantas quito.
  std::size_t erase_all(const Key& key);

  /// Quita la entrada que tiene esa clave Y ese payload. Devuelve si existia.
  /// Es lo que necesita un indice secundario para borrar un puntero concreto
  /// sin tocar los demas registros que comparten la clave.
  bool erase_one(const Key& key, std::span<const std::byte> payload);

  /// Donde vive una clave: la hoja y su posicion dentro de ella. Es lo que
  /// usa el indice agrupado (#15) para devolver un RID. Ojo: la posicion
  /// cambia si el nodo se parte, asi que no se persiste.
  [[nodiscard]] std::optional<RID> locate(const Key& key);

  /// Payload que hay en esa posicion, o nullopt si el RID no apunta a una
  /// entrada viva.
  [[nodiscard]] std::optional<std::vector<std::byte>> payload_at_rid(RID rid);

  /// Todas las entradas en orden de clave, recorriendo la cadena de hojas sin
  /// volver a bajar por el arbol. Materializa todo: para recorridos grandes,
  /// `entries()`.
  [[nodiscard]] std::vector<std::pair<Key, std::vector<std::byte>>> scan();

  /// Recorrido incremental de la cadena de hojas, con memoria acotada a una
  /// hoja. `entries_from` empieza en la primera clave >= `lo`.
  [[nodiscard]] std::unique_ptr<EntryCursor> entries();
  [[nodiscard]] std::unique_ptr<EntryCursor> entries_from(const Key& lo);

  /// Entradas con clave en [lo, hi], ambos inclusive.
  [[nodiscard]] std::vector<std::pair<Key, std::vector<std::byte>>> range(const Key& lo,
                                                                         const Key& hi);

  /// Comprueba las invariantes del arbol y devuelve la primera que falle, o
  /// una cadena vacia si esta todo bien. Lo usan las pruebas y sirve para
  /// depurar un archivo sospechoso.
  [[nodiscard]] std::string check_invariants();

  [[nodiscard]] const OpStats& stats() const noexcept { return stats_; }
  void reset_stats() noexcept { stats_.reset(); }

  void flush();

 private:
  class Cursor;

  // La version 1 (#14, #15) no guardaba si el arbol admite repetidas.
  static constexpr std::uint32_t kMetaVersion = 2;

  struct Meta {
    std::uint32_t version = kMetaVersion;
    std::uint32_t unique = 1;
    std::uint32_t key_type = 0;
    std::uint32_t key_size = 0;
    std::uint32_t payload_size = 0;
    std::uint32_t order = 0;
    std::uint32_t root = kInvalidPage;
    std::uint32_t first_leaf = kInvalidPage;
    std::uint32_t height = 0;
    std::uint64_t count = 0;
  };
  static_assert(sizeof(Meta) <= DiskManager::kMetaSize);

  /// Una entrada de nodo interno: la clave separadora y el hijo a su derecha.
  struct Branch {
    Key key;
    PageId child = kInvalidPage;
  };

  void load_meta();
  void save_meta();

  void fetch(PageId id);
  void store(PageId id);
  PageId allocate(std::byte tipo);

  // --- acceso al nodo cargado en scratch_ -----------------------------------
  [[nodiscard]] std::byte node_type() const { return scratch_.read_bytes(0, 1)[0]; }
  [[nodiscard]] bool is_leaf() const { return node_type() == kLeaf; }
  [[nodiscard]] std::size_t key_count() const { return scratch_.record_count(); }

  [[nodiscard]] std::size_t leaf_offset(std::size_t i) const {
    return 1 + i * (key_size_ + payload_size_);
  }
  [[nodiscard]] std::size_t branch_offset(std::size_t i) const {
    return 1 + sizeof(PageId) + i * (key_size_ + sizeof(PageId));
  }

  [[nodiscard]] Key key_at(std::size_t i) const;
  [[nodiscard]] std::vector<std::byte> payload_at(std::size_t i) const;
  [[nodiscard]] PageId child_at(std::size_t i) const;  // 0..n, el 0 es el izquierdo

  /// Primera posicion del nodo cargado con clave >= `key`.
  [[nodiscard]] std::size_t lower_bound(const Key& key) const;
  /// Primera posicion del nodo cargado con clave > `key`.
  [[nodiscard]] std::size_t upper_bound(const Key& key) const;
  /// Quita de las hojas las entradas con esa clave que cumplan el filtro.
  std::size_t erase_matching(const Key& key, const std::vector<std::byte>* payload);

  /// Baja desde la raiz hasta la hoja donde deberia estar `key`, dejando en
  /// `camino` los nodos internos recorridos.
  ///
  /// `por_la_derecha` decide que hacer cuando un separador es igual a la
  /// clave. Con claves unicas siempre se va a la derecha, porque ahi vive.
  /// Con repetidas depende: al INSERTAR se va a la derecha, para que la nueva
  /// quede despues de las iguales y la corrida conserve el orden de
  /// insercion; al BUSCAR se va a la izquierda, a la primera hoja que podria
  /// contenerla, y desde ahi se sigue la cadena.
  PageId descend(const Key& key, std::vector<PageId>* camino, bool por_la_derecha = true);

  void write_leaf(PageId id, const std::vector<std::pair<Key, std::vector<std::byte>>>& entradas,
                  PageId siguiente);
  void write_internal(PageId id, PageId hijo_izq, const std::vector<Branch>& ramas);

  /// Mete una rama en el nodo interno `id`, partiendolo si hace falta y
  /// propagando hacia arriba por `camino`.
  void insert_in_parent(std::vector<PageId>& camino, const Key& separador, PageId derecho,
                        PageId izquierdo);

  [[nodiscard]] std::string check_node(PageId id, std::size_t profundidad,
                                       const Key* lo, const Key* hi,
                                       std::size_t& hojas_en, std::size_t& contadas);

  Column key_column_;
  std::size_t key_size_ = 0;
  std::size_t payload_size_ = 0;
  std::size_t order_ = 0;
  bool unique_ = true;
  DiskManager disk_;
  Page scratch_;
  PageId root_ = kInvalidPage;
  PageId first_leaf_ = kInvalidPage;
  std::size_t height_ = 0;
  std::uint64_t count_ = 0;
  OpStats stats_;
};

}  // namespace quipudb
