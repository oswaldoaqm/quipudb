#pragma once

// IVF, indice de archivo invertido para vectores (issue #183): construccion,
// listas en disco y persistencia. La busqueda con nprobe es el #184.
//
// La idea: se reparte el espacio en `nlist` celdas, una por centroide, y cada
// vector se guarda en la lista de su centroide mas cercano. Al buscar se miran
// solo las listas de los centroides mas proximos a la consulta, en vez de la
// tabla entera.
//
// Los centroides no se calculan aqui. Salen del K-Means de
// `engine/multimedia/kmeans.py` (#178), entrenado sobre los vectores de la
// base, y llegan al crear el indice. Es el mismo algoritmo que arma el
// codebook de SIFT y MFCC, con otra entrada (ADR 0007).
//
// A que lista va cada vector
// --------------------------
//
//   Al centroide mas cercano en distancia euclidiana, sea cual sea la metrica
//   con la que despues se busque. Es la distancia que minimiza el K-Means, asi
//   que las listas son exactamente sus celdas. La metrica de la consulta se
//   aplica al comparar contra los vectores de cada lista (#184). Como nuestros
//   vectores salen normalizados de ExtractFeatures, la celda euclidiana y la
//   del coseno coinciden; con vectores sin normalizar puede no ser la mejor
//   celda para el producto punto, y por eso nprobe = nlist es lo unico que
//   garantiza el resultado exacto.
//
//   Si dos centroides empatan, gana el de menor indice, para que construir dos
//   veces con los mismos datos de exactamente el mismo indice.
//
// En disco
// --------
//
//   pagina 0     cabecera del DiskManager; el area meta guarda la version, la
//                dimension, nlist, cuantos vectores hay y la primera pagina
//                del directorio.
//   directorio   una cadena de paginas con una entrada por lista:
//                [centroide: dim floats][cabeza u32][cola u32][tamano u32].
//                Se carga entera al abrir: con nlist ~ sqrt(N) son unos 316
//                centroides para 100 000 vectores.
//   listas       cada lista es una cadena de paginas enlazada por `next`, con
//                entradas [pagina u32][slot u16][vector: dim floats]. Se
//                agrega siempre en la cola, asi que insertar lee y escribe una
//                pagina mas la del directorio.
//
//   El vector se copia en la lista a proposito: al buscar se compara ahi
//   mismo, sin leer la tabla por cada candidato, igual que el R-Tree guarda el
//   punto en la hoja. Cuesta espacio: con dimension 256 una entrada ocupa 1 030
//   bytes y en una pagina de 4 KB entran 3, asi que conviene paginas mas
//   grandes (el tamano se elige al crear, hasta 64 KB).

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <span>
#include <string>
#include <utility>
#include <vector>

#include "quipudb/catalog/table.hpp"
#include "quipudb/catalog/types.hpp"
#include "quipudb/storage/disk_manager.hpp"
#include "quipudb/storage/page.hpp"

namespace quipudb {

class Ivf {
 public:
  /// Version del formato. Si cambia el layout sube, y un archivo viejo se
  /// rechaza con IoError en vez de leerse mal.
  static constexpr std::uint32_t kMetaVersion = 1;
  /// Bytes de un RID dentro de una entrada de lista.
  static constexpr std::size_t kRidSize = sizeof(PageId) + sizeof(SlotId);
  /// Bytes fijos de una entrada de directorio, ademas del centroide.
  static constexpr std::size_t kDirFixed = 3 * sizeof(std::uint32_t);

  /// Crea un indice vacio con estos centroides, uno por fila, todos de la
  /// misma dimension. Lanza SchemaError si no hay centroides, si sus
  /// dimensiones no coinciden, si una entrada no cabe en la pagina o si el
  /// archivo ya tiene un indice; InvalidRecord si algun componente no es
  /// finito.
  [[nodiscard]] static std::unique_ptr<Ivf> create(
      const std::filesystem::path& path, const std::vector<std::vector<float>>& centroids,
      std::size_t page_size = kDefaultPageSize);

  /// Abre un indice existente. IoError si el archivo no es un IVF o es de otra
  /// version.
  [[nodiscard]] static std::unique_ptr<Ivf> open(const std::filesystem::path& path,
                                                 std::size_t page_size = kDefaultPageSize);

  /// Agrega los vectores de una vez: `vectors` trae `rids.size()` vectores
  /// seguidos de `dim()` componentes. Agrupa por lista antes de escribir, asi
  /// que cada pagina se escribe una sola vez. Guarda el tiempo que tardo en
  /// `last_build_ms()`.
  void build(std::span<const RID> rids, std::span<const float> vectors);

  /// Agrega un vector a la lista de su centroide mas cercano.
  void insert(RID rid, std::span<const float> vector);

  /// Indice de la lista a la que va `vector`: la de su centroide mas cercano en
  /// distancia euclidiana, con empates para el de menor indice.
  [[nodiscard]] std::size_t nearest_list(std::span<const float> vector) const;

  /// Lo que guarda una lista, leyendo sus paginas.
  [[nodiscard]] std::vector<std::pair<RID, std::vector<float>>> list_entries(std::size_t list);

  /// Recorre todas las listas y devuelve "" si todo cuadra, o la primera
  /// inconsistencia: un vector en una lista que no es la de su centroide, un
  /// tamano de lista que no coincide con sus paginas, o un total distinto.
  [[nodiscard]] std::string check_invariants();

  [[nodiscard]] std::size_t dim() const noexcept { return dim_; }
  [[nodiscard]] std::size_t nlist() const noexcept { return lists_.size(); }
  [[nodiscard]] std::uint64_t size() const noexcept { return count_; }
  [[nodiscard]] std::size_t list_size(std::size_t list) const { return lists_.at(list).size; }
  [[nodiscard]] std::span<const float> centroid(std::size_t list) const {
    return lists_.at(list).centroid;
  }
  /// Cuantas entradas de lista caben en una pagina.
  [[nodiscard]] std::size_t entries_per_page() const noexcept { return entries_per_page_; }
  [[nodiscard]] PageId page_count() const noexcept { return disk_.page_count(); }
  [[nodiscard]] double last_build_ms() const noexcept { return last_build_ms_; }

  [[nodiscard]] const OpStats& stats() const noexcept { return stats_; }
  void reset_stats() noexcept { stats_.reset(); }
  void flush();

  Ivf(const Ivf&) = delete;
  Ivf& operator=(const Ivf&) = delete;

 private:
  struct Meta {
    std::uint32_t version = 0;
    std::uint32_t dim = 0;
    std::uint32_t nlist = 0;
    PageId dir_head = kInvalidPage;
    std::uint64_t count = 0;
  };

  struct List {
    std::vector<float> centroid;
    PageId head = kInvalidPage;
    PageId tail = kInvalidPage;
    std::uint32_t size = 0;
  };

  Ivf(const std::filesystem::path& path, std::size_t page_size);

  [[nodiscard]] std::size_t entry_size() const noexcept { return kRidSize + dim_ * sizeof(float); }
  [[nodiscard]] std::size_t dir_entry_size() const noexcept {
    return dim_ * sizeof(float) + kDirFixed;
  }

  void load();
  void save_meta();
  void write_directory_entry(std::size_t list);
  void check_vector(std::span<const float> vector, const char* where) const;
  /// Agrega entradas al final de una lista, llenando la cola y encadenando
  /// paginas nuevas cuando hace falta.
  void append(std::size_t list, std::span<const RID> rids,
              std::span<const std::span<const float>> vectors);

  DiskManager disk_;
  Page scratch_;
  std::size_t dim_ = 0;
  std::size_t entries_per_page_ = 0;
  std::size_t dir_per_page_ = 0;
  std::uint64_t count_ = 0;
  PageId dir_head_ = kInvalidPage;
  std::vector<PageId> dir_pages_;
  std::vector<List> lists_;
  OpStats stats_;
  double last_build_ms_ = 0.0;
};

}  // namespace quipudb
