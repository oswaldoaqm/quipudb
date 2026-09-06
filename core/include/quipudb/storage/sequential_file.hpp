#pragma once

// Archivo Secuencial Paginado (issue #10).
//
// Mantiene los registros ordenados por clave primaria sin reescribir el
// archivo entero en cada insercion. Es la contraparte del heap file en la
// comparacion del 2.1.6: insercion mas cara, busqueda mucho mas barata.
//
// Estructura: area principal + area de overflow
//
//   Las paginas del area principal forman una cadena en orden de clave (campo
//   `next` de la cabecera de pagina), y dentro de cada pagina los registros
//   estan fisicamente ordenados. Cada pagina principal tiene ademas su propia
//   cadena de paginas de overflow, cuya cabeza vive en los primeros 4 bytes
//   del body.
//
//   pagina principal 1        pagina principal 2        pagina principal 3
//   claves [10..40]  ------>  claves [50..90]  ------>  claves [95..]
//        |                         |
//        v                         v
//   overflow de la 1          overflow de la 2
//
// Criterio de cuando un registro va al area principal y cuando al overflow:
//
//   1. Se localiza por busqueda binaria la pagina principal a la que pertenece
//      la clave.
//   2. Si esa pagina tiene espacio fisico, el registro entra ahi, corriendo
//      los que le siguen para no romper el orden. Es el caso normal.
//   3. Si la pagina esta llena pero es la ultima y la clave es mayor que todas
//      las suyas, se agrega una pagina principal nueva al final de la cadena.
//      Asi una carga en orden ascendente (el caso del 2.1.6) no genera nada de
//      overflow.
//   4. En cualquier otro caso -- pagina llena y la clave cae en medio del
//      archivo -- el registro va al area de overflow de esa pagina.
//   5. La cadena de overflow de una pagina se limita a UNA pagina. Cuando esa
//      pagina tambien se llena, el grupo (principal + overflow) se parte: se
//      juntan sus registros, se ordenan y se reparten en varias paginas
//      principales llenas a la mitad, que se empalman en la cadena principal.
//      El grupo queda otra vez sin overflow.
//
//   El punto 5 no es un lujo. Sin el, el area principal deja de crecer en
//   cuanto la primera pagina se llena y todo termina apilado en cadenas de
//   overflow: medido con 100 000 inserciones aleatorias, 3 paginas
//   principales contra 714 de overflow y 93 segundos de carga. Con el limite
//   de una pagina, cada particion cuesta O(slots por pagina) y se amortiza
//   entre las inserciones que la provocaron.
//
//   Empalmar una pagina en medio de la cadena principal es O(1) porque la
//   cadena es una lista enlazada por el campo `next`: no hay que mover
//   ninguna pagina de sitio.
//
//   El overflow de una pagina no esta ordenado: se apila. El desorden queda
//   acotado al grupo de una pagina (sus claves caen entre la primera clave de
//   esta pagina y la de la siguiente), asi que `scan` ordena grupo por grupo y
//   el resultado global sale ordenado. La reorganizacion global del #12 sigue
//   haciendo falta para recuperar el espacio de los registros marcados.
//
// Layout de una pagina, principal o de overflow:
//
//   cabecera de Page (8 bytes)
//     next          siguiente pagina de la cadena (principal o de overflow)
//     record_count  registros VIVOS en la pagina
//     free_space    bytes libres al final = (slots - fisicos) * slot_size
//   body
//     [0, 4)        cabeza de la cadena de overflow (solo en paginas
//                   principales; kInvalidPage si no tiene)
//     [4, ...)      slots: [1 byte de estado][record_size bytes]
//                   estado 0 = libre, 1 = ocupado, 2 = borrado (#11)
//
//   Los slots ocupados y borrados son contiguos desde el inicio y estan
//   ordenados por clave; los libres quedan al final. Insertar en medio corre
//   la cola una posicion con un memmove dentro de la pagina.
//
// Un RID de este archivo NO es estable: correr registros dentro de una pagina
// cambia su posicion. El contrato (#3) ya lo dice, y por eso los indices no
// agrupados apuntan a heap files, no a este. `read(rid)` sirve dentro de la
// operacion que lo devolvio, no despues.
//
// Eliminacion lazy (#11): `remove` no mueve nada. Marca el slot con el estado
// kDeleted, que deja de contar como vivo pero sigue ocupando su lugar, asi
// que el archivo no cambia de tamano y los registros que le siguen conservan
// su posicion. Busquedas y escaneos saltan los marcados.
//
//   Espacio desperdiciado = el que ocupan los registros marcados.
//   `wasted_ratio()` es marcados / (vivos + marcados), o sea la fraccion de
//   los registros guardados que ya no sirve. Los slots libres al final de una
//   pagina NO cuentan como desperdicio: son sitio util para las proximas
//   inserciones, y contarlos haria que un archivo recien partido (paginas a
//   media carga) pareciera desperdiciar la mitad. Sobre esa razon se dispara
//   la reorganizacion del #12.
//
//   El unico momento en que el desperdicio baja solo es al partir un grupo:
//   ahi los marcados se quedan fuera y el contador se ajusta.
//
// Reorganizacion (#12): cuando la razon de desperdicio pasa del umbral (30%
// por defecto, configurable), `remove` dispara `reorganize()`, que reescribe
// el archivo entero: junta los registros vivos en orden, los reparte en
// paginas principales consecutivas sin huecos ni overflow, y devuelve al
// sistema las paginas que sobran. Despues de reorganizar el desperdicio es
// cero y el archivo vuelve a ser puramente secuencial.
//
//   Las paginas nuevas se llenan al kFillFactor (80%), no al 100%: empacar
//   del todo dejaria que la siguiente insercion de cada rango se fuera
//   derecho al overflow. El 20% de holgura se recupera igual en la proxima
//   reorganizacion.
//
//   La reorganizacion materializa los registros vivos en memoria antes de
//   reescribir. Es O(N) de memoria, aceptable para los 100 000 registros del
//   2.1.6, y evita el riesgo de pisar paginas que todavia no se leyeron al
//   reescribir sobre el mismo archivo. Una version que no cargue todo en
//   memoria tendria que apoyarse en el external sorting del #20.
//
// Lo que llega despues: busqueda binaria y por rango (#13). Aqui `search` y
// `range_search` son un recorrido lineal, correcto pero sin aprovechar el
// orden todavia.

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <optional>
#include <string_view>
#include <vector>

#include "quipudb/catalog/record_codec.hpp"
#include "quipudb/catalog/table.hpp"
#include "quipudb/storage/disk_manager.hpp"
#include "quipudb/storage/page.hpp"

namespace quipudb {

class SequentialFile final : public TableFile {
 public:
  static constexpr std::byte kFree{0};
  static constexpr std::byte kUsed{1};
  static constexpr std::byte kDeleted{2};

  /// Bytes del body reservados para la cabeza del overflow.
  static constexpr std::size_t kBodyHeader = 4;

  /// Razon de desperdicio a partir de la cual se reorganiza sola.
  static constexpr double kDefaultWasteThreshold = 0.30;
  /// Que tan llenas quedan las paginas al reorganizar.
  static constexpr double kFillFactor = 0.80;

  SequentialFile(std::filesystem::path path, Schema schema,
                 std::size_t page_size = kDefaultPageSize,
                 double waste_threshold = kDefaultWasteThreshold);

  // --- TableFile ----------------------------------------------------------

  [[nodiscard]] const Schema& schema() const noexcept override { return codec_.schema(); }
  [[nodiscard]] std::string_view kind() const noexcept override { return kind::kSequential; }

  RID insert(const Record& record) override;
  std::size_t remove(const Key& key) override;
  [[nodiscard]] std::vector<Record> search(const Key& key) override;
  [[nodiscard]] std::vector<Record> range_search(const Key& lo, const Key& hi) override;
  [[nodiscard]] std::vector<Record> scan() override;
  [[nodiscard]] std::optional<Record> read(RID rid) override;
  [[nodiscard]] std::size_t size() const override { return live_; }

  [[nodiscard]] const OpStats& stats() const noexcept override { return stats_; }
  void reset_stats() noexcept override { stats_.reset(); }

  // --- propios ------------------------------------------------------------

  [[nodiscard]] std::size_t slot_size() const noexcept { return slot_size_; }
  [[nodiscard]] std::size_t slots_per_page() const noexcept { return slots_per_page_; }

  /// Paginas del area principal.
  [[nodiscard]] std::size_t main_pages() const noexcept { return main_pages_.size(); }

  /// Paginas del area de overflow, sumando todas las cadenas.
  [[nodiscard]] std::size_t overflow_pages();

  /// Registros marcados como borrados que siguen ocupando su slot.
  [[nodiscard]] std::uint64_t deleted_records() const noexcept { return deleted_; }

  /// Bytes que ocupan esos registros marcados.
  [[nodiscard]] std::uint64_t wasted_bytes() const noexcept { return deleted_ * slot_size_; }

  /// Fraccion desperdiciada: marcados / (vivos + marcados). 0 si esta vacio.
  /// Es la razon que el #12 compara contra el umbral del 30%.
  [[nodiscard]] double wasted_ratio() const noexcept {
    const std::uint64_t total = live_ + deleted_;
    return total == 0 ? 0.0 : static_cast<double>(deleted_) / static_cast<double>(total);
  }

  [[nodiscard]] PageId page_count() const noexcept { return disk_.page_count(); }
  [[nodiscard]] std::uintmax_t file_size() const { return disk_.file_size(); }

  void flush();

  // --- reorganizacion (#12) -----------------------------------------------

  /// Reescribe el archivo sin huecos ni overflow. Devuelve cuanto tardo, en
  /// milisegundos. Se puede llamar a mano; ademas se dispara sola cuando
  /// `wasted_ratio()` supera el umbral.
  double reorganize();

  /// Umbral de desperdicio a partir del cual `remove` reorganiza.
  [[nodiscard]] double waste_threshold() const noexcept { return waste_threshold_; }
  void set_waste_threshold(double t) noexcept { waste_threshold_ = t; }

  /// Cuantas veces se reorganizo este archivo en esta sesion.
  [[nodiscard]] std::uint64_t reorganizations() const noexcept { return reorganizations_; }

  /// Milisegundos que tardo la ultima reorganizacion. Es una de las metricas
  /// que el 2.1.6 compara.
  [[nodiscard]] double last_reorganize_ms() const noexcept { return last_reorganize_ms_; }

 private:
  static constexpr std::uint32_t kMetaVersion = 1;

  struct Meta {
    std::uint32_t version = kMetaVersion;
    std::uint32_t record_size = 0;
    std::uint64_t live = 0;
    std::uint64_t deleted = 0;  // registros marcados; lo explota #11
    std::uint32_t main_head = kInvalidPage;
    std::uint32_t reserved = 0;
  };
  static_assert(sizeof(Meta) <= DiskManager::kMetaSize);

  void load_meta();
  void save_meta();
  /// Recorre la cadena principal (y los overflow) para armar el directorio en
  /// memoria y comprobar que los contadores del area meta cuadran con lo que
  /// hay en las paginas.
  void build_directory();
  /// Suma vivos y marcados de la pagina cargada en scratch_.
  void tally(std::uint64_t& vivos, std::uint64_t& marcados) const;

  void fetch(PageId id);
  void store(PageId id);
  PageId allocate_blank();

  // Acceso a la pagina cargada en scratch_.
  [[nodiscard]] std::size_t slot_offset(std::size_t slot) const noexcept {
    return kBodyHeader + slot * slot_size_;
  }
  [[nodiscard]] std::byte slot_state(std::size_t slot) const {
    return scratch_.read_bytes(slot_offset(slot), 1)[0];
  }
  [[nodiscard]] std::span<const std::byte> slot_record(std::size_t slot) const {
    return scratch_.read_bytes(slot_offset(slot) + 1, codec_.size());
  }
  [[nodiscard]] Key slot_key(std::size_t slot) const {
    return codec_.decode_column(slot_record(slot), codec_.schema().key_column);
  }
  /// Slots ocupados o borrados, siempre contiguos desde el 0.
  [[nodiscard]] std::size_t physical_slots() const noexcept {
    return slots_per_page_ - scratch_.free_space() / slot_size_;
  }
  [[nodiscard]] PageId overflow_head() const;
  void set_overflow_head(PageId p);

  /// Indice en main_pages_ del grupo al que pertenece `key`.
  [[nodiscard]] std::size_t group_of(const Key& key) const;
  /// Primera posicion de la pagina cargada cuya clave es >= `key`.
  [[nodiscard]] std::size_t lower_bound_in_page(const Key& key) const;
  /// Busca `key` en la pagina principal `mp` y su overflow. Devuelve el RID.
  [[nodiscard]] std::optional<RID> find_in_group(PageId mp, const Key& key);
  /// Registros vivos de la pagina cargada, en orden fisico.
  void collect_live(std::vector<Record>& out);
  /// Junta el grupo `g` (pagina principal y su overflow), lo ordena y lo
  /// reparte en paginas principales a media carga, empalmadas en la cadena.
  void split_group(std::size_t g);

  RecordCodec codec_;
  DiskManager disk_;
  Page scratch_;
  std::size_t slot_size_ = 0;
  std::size_t slots_per_page_ = 0;
  std::uint64_t live_ = 0;
  std::uint64_t deleted_ = 0;
  PageId main_head_ = kInvalidPage;
  /// Paginas principales en orden de clave, y la primera clave de cada una.
  std::vector<PageId> main_pages_;
  std::vector<Key> first_keys_;
  double waste_threshold_ = kDefaultWasteThreshold;
  std::uint64_t reorganizations_ = 0;
  double last_reorganize_ms_ = 0.0;
  OpStats stats_;
};

}  // namespace quipudb
