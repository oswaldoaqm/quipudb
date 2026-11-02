#pragma once

// HNSW, grafo jerarquico de mundo pequeno navegable: capas, insercion y
// persistencia (#185), y busqueda k-NN con ef_search y borrado (#186).
//
// La idea, de Malkov y Yashunin (2018): cada vector es un nodo de un grafo
// donde los vecinos son vectores cercanos. Hay varias capas; la 0 tiene a
// todos los nodos y cada capa de arriba tiene a una fraccion cada vez menor.
// Las capas altas tienen pocos nodos y aristas largas, y sirven para llegar
// rapido cerca de la consulta; la capa 0 tiene a todos y sirve para afinar.
// Es la misma idea que una skip list, pero sobre un grafo.
//
// El nivel de cada nodo
// ---------------------
//
//   El nodo `id` llega hasta el nivel floor(-ln(u) * mL), con mL = 1 / ln(M) y
//   u uniforme en (0, 1]. Con eso P(nivel >= l) = M^-l: con M = 16, uno de
//   cada 16 nodos esta en la capa 1, uno de cada 256 en la 2, y el grafo tiene
//   unas log_M(N) capas. Es lo que hace que bajar por las capas cueste
//   O(log N) comparaciones.
//
//   u no sale de un generador con estado sino de mezclar la semilla con el id
//   del nodo (splitmix64). Asi el nivel de un nodo depende solo de la semilla y
//   de su posicion de llegada: construir dos veces con la misma semilla da el
//   mismo grafo, y reabrir el indice e insertar mas da lo mismo que no haberlo
//   cerrado. El nivel se corta en kMaxLevel.
//
// Insertar
// --------
//
//   Se baja desde el punto de entrada (el nodo de nivel mas alto) de forma
//   voraz hasta la capa del nivel del nodo nuevo. Desde ahi hasta la capa 0 se
//   hace una busqueda con efConstruction candidatos y se eligen hasta M
//   vecinos (2M en la capa 0, como en el paper) con la heuristica del
//   algoritmo 4: un candidato entra si esta mas cerca del nodo nuevo que de
//   los vecinos ya elegidos. Eso reparte los vecinos en direcciones distintas
//   en vez de juntarlos todos en un mismo grupo, y por eso un nodo suele
//   quedar con menos de M.
//
//   Las aristas van en los dos sentidos. Si un vecino pasa su grado maximo, se
//   recortan sus vecinos con la misma heuristica.
//
// Que el grafo quede conectado
// ----------------------------
//
//   El paper no lo garantiza: al recortar, un nodo puede perder la unica
//   arista que llegaba a el y quedar fuera de toda busqueda. Pasa sobre todo
//   con M chico o con vectores repetidos. Por eso, al terminar cada `insert`
//   y cada `build`, se recorre cada capa desde la entrada y, si un nodo no se
//   alcanza, se le agrega una arista desde el nodo alcanzado mas cercano que
//   todavia tenga espacio (lo mismo hace Lucene al cerrar un grafo).
//
//   Si todos los alcanzados estan llenos, uno cede una arista que no sea del
//   arbol del recorrido. Siempre hay una: si los |R| alcanzados tienen grado
//   maximo (al menos 2) hay 2|R| aristas o mas, y el arbol usa |R| - 1. Sin
//   esa arista todos siguen alcanzandose por el arbol, asi que cada vuelta
//   suma al menos un nodo y la pasada termina en a lo mas N vueltas.
//
//   Recorrer el grafo cuesta O(N * M) por capa: en un `build` se hace una vez,
//   y en un `insert` una vez por insercion.
//
//   La distancia es la de la metrica con la que se crea el indice, en la escala
//   de `vector_distance` (menor es mejor). Queda fija: un grafo armado con
//   coseno no sirve para buscar con producto punto.
//
//   Todo se resuelve con empates por id de nodo, para que el grafo no dependa
//   del orden de un heap.
//
// En disco
// --------
//
//   El grafo vive en memoria y las paginas son su copia en disco: al abrir se
//   lee entero, y cada insercion reescribe solo las paginas de los nodos que
//   cambiaron. `build` escribe cada pagina una sola vez al final.
//
//   pagina 0   cabecera del DiskManager; el area meta guarda la version, la
//              dimension, la metrica, M, efConstruction, la semilla, cuantos
//              nodos hay, el punto de entrada, el nivel maximo y donde empiezan
//              las dos cadenas.
//   nodos      cadena de paginas con un registro de tamano fijo por nodo, en
//              orden de id: [pagina u32][slot u16][nivel u8][reservado u8]
//              [primer registro de capas altas u32][vector: dim floats]
//              [vecinos en la capa 0 u16][reservado u16][2M ids u32].
//   capas      cadena de paginas con un registro por (nodo, capa >= 1):
//              [vecinos u16][reservado u16][M ids u32]. Los registros de un
//              nodo van seguidos, desde el que indica su registro de nodo.
//
//   El vector va en el registro del nodo, como en el IVF, para comparar sin
//   leer la tabla. Con dimension 256 y M = 16 un nodo ocupa 1 168 bytes y en
//   una pagina de 4 KB entran 3.
//
//   El byte que sigue al nivel marca si el nodo esta borrado (#186). En el
//   #185 se escribia siempre en 0, asi que el formato no cambia de version.
//
// Buscar (#186)
// -------------
//
//   El algoritmo 5 del paper: desde el punto de entrada se baja de forma
//   voraz, con un solo candidato, hasta la capa 1, y en la capa 0 se hace la
//   busqueda ampliada con ef_search candidatos. Se devuelven los k mejores.
//   ef_search menor que k se toma como k.
//
//   Bajar por las capas cuesta unas log_M(N) comparaciones por capa, y la
//   capa 0 cuesta del orden de ef_search * 2M: por eso las comparaciones
//   crecen como log N y no como N. ef_search es el equivalente de nprobe en
//   el IVF: con uno mas grande se miran mas nodos y el Recall@k sube. A
//   diferencia del IVF, que el recall no baje al subir ef_search no esta
//   garantizado consulta por consulta, porque la busqueda es voraz y puede
//   tomar otro camino; en promedio si sube. Con ef_search >= N la busqueda
//   no puede parar antes de ver todo lo alcanzable, y como el grafo esta
//   conectado (#185) el resultado es exactamente el de la busqueda exacta.
//
//   La metrica de la consulta tiene que ser la del grafo: los vecinos se
//   eligieron con esa distancia, y con otra el grafo no lleva a los mas
//   cercanos. Pedir otra es SchemaError.
//
// Borrar (#186)
// -------------
//
//   Sacar un nodo de verdad obliga a reconectar a sus vecinos. Aqui se marca
//   como borrado: la busqueda sigue pasando por el para navegar, pero no lo
//   devuelve. El nodo sigue ocupando lugar y contando en `size()`; los vivos
//   son `live_size()`.

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <map>
#include <memory>
#include <set>
#include <span>
#include <string>
#include <vector>

#include "quipudb/catalog/table.hpp"
#include "quipudb/catalog/types.hpp"
#include "quipudb/index/vector_metric.hpp"
#include "quipudb/index/vector_scan.hpp"
#include "quipudb/storage/disk_manager.hpp"
#include "quipudb/storage/page.hpp"

namespace quipudb {

struct HnswParams {
  /// Vecinos por nodo en las capas de arriba; en la capa 0 son 2M.
  std::size_t m = 16;
  /// Candidatos que se miran al buscar los vecinos de un nodo nuevo.
  std::size_t ef_construction = 200;
  /// Semilla de los niveles.
  std::uint64_t seed = 0;
};

class Hnsw {
 public:
  static constexpr std::uint32_t kMetaVersion = 1;
  /// Nivel mas alto que puede tener un nodo. Con M = 2, llegar a 15 tiene
  /// probabilidad 2^-15 por nodo; con M = 16, 16^-15.
  static constexpr std::size_t kMaxLevel = 15;
  /// Bytes fijos de un registro de nodo, sin el vector ni los vecinos.
  static constexpr std::size_t kNodeFixed = 12;
  /// Bytes del contador de vecinos de un registro (u16 mas u16 reservado).
  static constexpr std::size_t kLinkHeader = 4;
  static constexpr std::uint32_t kNoVisto = 0xFFFFFFFFU;

  /// Crea un indice vacio. Lanza SchemaError si dim es 0, si M es menor que 2
  /// o mayor que 1 000, si efConstruction es 0, si un nodo no cabe en la
  /// pagina o si el archivo ya tiene un indice.
  [[nodiscard]] static std::unique_ptr<Hnsw> create(const std::filesystem::path& path,
                                                    std::size_t dim, VectorMetric metric,
                                                    HnswParams params = {},
                                                    std::size_t page_size = kDefaultPageSize);

  /// Abre un indice existente y carga el grafo. IoError si el archivo no es un
  /// HNSW, es de otra version o esta danado.
  [[nodiscard]] static std::unique_ptr<Hnsw> open(const std::filesystem::path& path,
                                                  std::size_t page_size = kDefaultPageSize);

  /// Agrega un vector y lo conecta. Escribe solo las paginas que cambiaron.
  /// InvalidRecord si no tiene la dimension del indice o algun componente no
  /// es finito.
  void insert(RID rid, std::span<const float> vector);

  /// Inserta `rids.size()` vectores seguidos de `dim()` componentes, en ese
  /// orden, y escribe cada pagina una sola vez al final. Valida todo antes de
  /// insertar nada. Guarda el tiempo en `last_build_ms()`.
  void build(std::span<const RID> rids, std::span<const float> vectors);

  /// Nivel que le toca al nodo `id` con esta semilla y este M.
  [[nodiscard]] static std::size_t level_for(std::uint64_t seed, std::uint64_t id, std::size_t m);

  /// Recorre el grafo y devuelve "" si todo cuadra, o la primera falla: un
  /// nodo con mas vecinos que su grado maximo, una arista a si mismo, repetida
  /// o a un nodo que no llega a esa capa, un punto de entrada que no es de
  /// nivel maximo, o un nodo al que no se llega desde la entrada en alguna de
  /// sus capas.
  [[nodiscard]] std::string check_invariants() const;

  /// Los `k` vectores vivos mas cercanos a `query`, de mas cerca a mas lejos,
  /// con distancias de `vector_distance` y empates por RID, como
  /// `scan_k_nearest_vector`. SchemaError si `metric` no es la del grafo;
  /// InvalidRecord si ef_search es 0 o la consulta no tiene la dimension del
  /// indice.
  [[nodiscard]] std::vector<VectorMatch> search(std::span<const float> query, std::size_t k,
                                                VectorMetric metric, std::size_t ef_search);

  /// Marca como borrado el nodo vivo con este RID y este vector. Devuelve si
  /// existia.
  bool remove(RID rid, std::span<const float> vector);

  /// Nodos en el grafo, vivos y borrados.
  [[nodiscard]] std::size_t size() const noexcept { return rids_.size(); }
  /// Nodos que una busqueda puede devolver.
  [[nodiscard]] std::size_t live_size() const noexcept { return rids_.size() - borrados_; }
  [[nodiscard]] bool deleted(std::size_t node) const { return muerto_.at(node) != 0; }
  [[nodiscard]] bool empty() const noexcept { return rids_.empty(); }
  [[nodiscard]] std::size_t dim() const noexcept { return dim_; }
  [[nodiscard]] VectorMetric metric() const noexcept { return metric_; }
  [[nodiscard]] std::size_t m() const noexcept { return m_; }
  [[nodiscard]] std::size_t ef_construction() const noexcept { return ef_construction_; }
  [[nodiscard]] std::uint64_t seed() const noexcept { return seed_; }
  /// Grado maximo en una capa: 2M en la 0 y M en las demas.
  [[nodiscard]] std::size_t max_neighbors(std::size_t layer) const noexcept {
    return layer == 0 ? 2 * m_ : m_;
  }
  /// Nivel del punto de entrada; -1 con el indice vacio.
  [[nodiscard]] int max_level() const noexcept { return max_level_; }
  /// Nodo de entrada. Solo tiene sentido si el indice no esta vacio.
  [[nodiscard]] std::size_t entry_point() const noexcept { return entry_; }

  [[nodiscard]] std::size_t level(std::size_t node) const { return levels_.at(node); }
  [[nodiscard]] RID rid(std::size_t node) const { return rids_.at(node); }
  [[nodiscard]] std::span<const float> vector(std::size_t node) const;
  [[nodiscard]] std::span<const std::uint32_t> neighbors(std::size_t node,
                                                         std::size_t layer) const;

  [[nodiscard]] std::size_t nodes_per_page() const noexcept { return nodes_per_page_; }
  [[nodiscard]] PageId page_count() const noexcept { return disk_.page_count(); }
  [[nodiscard]] double last_build_ms() const noexcept { return last_build_ms_; }

  /// `records_examined` cuenta las distancias calculadas; `pages_read` y
  /// `pages_written`, las paginas.
  [[nodiscard]] const OpStats& stats() const noexcept { return stats_; }
  void reset_stats() noexcept { stats_.reset(); }
  void flush();

  Hnsw(const Hnsw&) = delete;
  Hnsw& operator=(const Hnsw&) = delete;

 private:
  struct Meta {
    std::uint32_t version = 0;
    std::uint32_t dim = 0;
    std::uint8_t metric = 0;
    std::uint8_t reservado = 0;
    std::uint16_t m = 0;
    std::uint32_t ef_construction = 0;
    std::uint64_t seed = 0;
    std::uint32_t count = 0;
    std::uint32_t entry = 0;
    std::int32_t max_level = -1;
    std::uint32_t upper_count = 0;
    PageId node_head = kInvalidPage;
    PageId upper_head = kInvalidPage;
  };
  static_assert(sizeof(Meta) <= DiskManager::kMetaSize);

  /// Un candidato: distancia al vector buscado y nodo. El orden es el de la
  /// distancia y, si empatan, el id.
  struct Candidato {
    double distance;
    std::uint32_t node;
    friend auto operator<=>(const Candidato&, const Candidato&) = default;
  };

  Hnsw(const std::filesystem::path& path, std::size_t page_size);

  [[nodiscard]] std::size_t node_record_size() const noexcept {
    return kNodeFixed + dim_ * sizeof(float) + kLinkHeader + 2 * m_ * sizeof(std::uint32_t);
  }
  [[nodiscard]] std::size_t upper_record_size() const noexcept {
    return kLinkHeader + m_ * sizeof(std::uint32_t);
  }

  void load();
  void save_meta();
  void check_vector(std::span<const float> vector, const char* where) const;

  /// Agrega el nodo al grafo en memoria y marca las paginas que cambian.
  void add_node(RID rid, std::span<const float> vector);
  /// Busqueda en una capa con `ef` candidatos, desde `entrada`. Devuelve los
  /// mejores de mas cerca a mas lejos. Con `solo_vivos` pasa por los nodos
  /// borrados pero no los devuelve.
  [[nodiscard]] std::vector<Candidato> search_layer(std::span<const float> query,
                                                    const std::vector<Candidato>& entrada,
                                                    std::size_t ef, std::size_t layer,
                                                    bool solo_vivos = false);
  /// Heuristica de seleccion de vecinos (algoritmo 4).
  [[nodiscard]] std::vector<std::uint32_t> select_neighbors(
      const std::vector<Candidato>& candidatos, std::size_t max);
  /// Recorta los vecinos de `node` en `layer` a su grado maximo.
  void shrink(std::uint32_t node, std::size_t layer);
  /// Recorre `layer` desde la entrada y devuelve, por nodo, desde que nodo se
  /// llego a el (la entrada se marca a si misma). Los que no se alcanzan
  /// quedan en kNoVisto.
  [[nodiscard]] std::vector<std::uint32_t> reachable(std::size_t layer) const;
  /// Conecta los nodos que no se alcanzan desde la entrada en alguna capa.
  void connect_components();
  [[nodiscard]] double distance(std::span<const float> a, std::span<const float> b);

  void mark_dirty(std::uint32_t node, std::size_t layer);
  void write_dirty();
  void write_node_page(std::size_t index);
  void write_upper_page(std::size_t index);

  DiskManager disk_;
  Page scratch_;
  std::size_t dim_ = 0;
  VectorMetric metric_ = VectorMetric::kEuclidean;
  std::size_t m_ = 0;
  std::size_t ef_construction_ = 0;
  std::uint64_t seed_ = 0;
  int max_level_ = -1;
  std::uint32_t entry_ = 0;
  std::size_t nodes_per_page_ = 0;
  std::size_t uppers_per_page_ = 0;

  std::vector<RID> rids_;
  std::vector<std::uint8_t> levels_;
  /// 1 si el nodo esta borrado.
  std::vector<std::uint8_t> muerto_;
  std::size_t borrados_ = 0;
  /// Nodos vivos de cada RID, para borrar sin recorrer todo el grafo.
  std::map<RID, std::vector<std::uint32_t>> por_rid_;
  std::vector<std::uint32_t> first_upper_;
  std::vector<float> vectors_;
  /// links_[nodo][capa] son los vecinos del nodo en esa capa.
  std::vector<std::vector<std::vector<std::uint32_t>>> links_;
  std::uint32_t upper_count_ = 0;
  /// upper_owner_[r] es el nodo dueno del registro de capa alta r.
  std::vector<std::uint32_t> upper_owner_;

  std::vector<PageId> node_pages_;
  std::vector<PageId> upper_pages_;
  std::set<std::size_t> dirty_nodes_;
  std::set<std::size_t> dirty_uppers_;

  /// Marcas de visitado de search_layer: un nodo esta visitado si su marca es
  /// igual a la vuelta actual, asi no hay que limpiar el arreglo cada vez.
  std::vector<std::uint32_t> visitado_;
  std::uint32_t vuelta_ = 0;

  OpStats stats_;
  double last_build_ms_ = 0.0;
};

}  // namespace quipudb
