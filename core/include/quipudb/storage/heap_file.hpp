#pragma once

// Heap File (issues #8 y #9).
//
// Guarda los registros en orden de llegada sobre paginas de disco. Es la
// organizacion mas simple del enunciado (2.1.1) y la linea base contra la que
// se compara todo lo demas en el 2.1.6: insercion barata, busqueda cara.
//
// Layout de una pagina de datos:
//
//   cabecera de Page (8 bytes)
//     record_count  registros vivos en esta pagina
//     free_space    bytes libres = slots libres * slot_size
//     next          siguiente pagina de la free list, o kInvalidPage
//   body: arreglo de slots, todos del mismo tamano
//     slot = [1 byte de estado][record_size bytes de registro]
//     estado 0 = libre, 1 = ocupado
//
// Los registros son de longitud fija (RecordCodec, #7), asi que el slot `i`
// empieza siempre en `i * slot_size` y un RID {pagina, slot} se resuelve sin
// recorrer nada. Esa es la razon de que un RID de heap file sea estable: el
// registro no se mueve nunca de donde se escribio.
//
// Reutilizacion de espacio (#9): FREE_LIST, no MOVE_THE_LAST.
//
//   Se encadenan las PAGINAS con al menos un slot libre, usando el campo
//   `next` de la cabecera de pagina y guardando la cabeza en el area meta.
//   Insertar toma la cabeza de la lista; si la pagina se llena, sale de la
//   lista. Eliminar libera el slot y, si la pagina estaba llena, la mete a la
//   lista. Ambas operaciones son O(1) y no recorren el archivo.
//
//   Se descarto MOVE_THE_LAST (traer el ultimo registro al hueco para que el
//   archivo quede siempre compacto) porque mueve un registro de sitio, y en
//   este motor un RID es una direccion estable: los indices no agrupados
//   (#16) guardan RIDs, y cada eliminacion obligaria a corregir el indice del
//   registro movido. FREE_LIST cuesta paginas con huecos hasta que se
//   reutilizan; MOVE_THE_LAST costaria correccion de indices en cada borrado.
//
//   La lista es LIFO: se reutiliza la pagina liberada mas recientemente. Es
//   O(1) y ademas suele seguir en la cache del sistema operativo. "Primer
//   hueco" significa la cabeza de la lista, no la pagina de menor numero.
//
// El area meta del DiskManager (pagina 0) guarda el estado del archivo entre
// sesiones: version de formato, tamano de registro, registros vivos y la
// cabeza de la free list. La lista persiste; al abrir se valida contra las
// paginas, pero no se reconstruye.
//
// Unicidad de la clave primaria: el contrato (#3) dice que insertar una clave
// repetida lanza DuplicateKey. Comprobarlo recorriendo el archivo haria que
// cargar N registros costara O(N^2), y el 2.1.6 pide cargar 100 000. Por eso
// HeapFile mantiene en memoria el conjunto de claves vivas, que reconstruye
// con un unico escaneo al abrir. Es memoria O(N) en claves, no en registros.
// `search` sigue siendo lineal sobre el disco, como pide el enunciado: el
// conjunto solo decide si una clave ya existe, nunca donde esta.

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <map>
#include <memory>
#include <optional>
#include <string_view>
#include <vector>

#include "quipudb/catalog/record_codec.hpp"
#include "quipudb/catalog/table.hpp"
#include "quipudb/storage/disk_manager.hpp"
#include "quipudb/storage/page.hpp"

namespace quipudb {

class HeapFile final : public TableFile {
 public:
  /// Estado de un slot dentro de la pagina.
  static constexpr std::byte kFree{0};
  static constexpr std::byte kUsed{1};

  /// Abre el archivo si existe o lo crea. `page_size` solo se usa al crear:
  /// al reabrir manda el que quedo grabado (lo valida DiskManager).
  HeapFile(std::filesystem::path path, Schema schema,
           std::size_t page_size = kDefaultPageSize);

  // --- TableFile ----------------------------------------------------------

  [[nodiscard]] const Schema& schema() const noexcept override { return codec_.schema(); }
  [[nodiscard]] std::string_view kind() const noexcept override { return kind::kHeap; }

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

  /// Bytes que ocupa un slot: estado mas registro.
  [[nodiscard]] std::size_t slot_size() const noexcept { return slot_size_; }

  /// Cuantos registros entran en una pagina.
  [[nodiscard]] std::size_t slots_per_page() const noexcept { return slots_per_page_; }

  /// Paginas de datos del archivo.
  [[nodiscard]] PageId page_count() const noexcept { return disk_.page_count(); }

  /// Tamano del archivo en bytes. Es el "espacio en disco" del 2.1.6.
  [[nodiscard]] std::uintmax_t file_size() const { return disk_.file_size(); }

  /// Paginas que tienen al menos un slot libre (largo de la free list).
  [[nodiscard]] std::uint32_t free_pages() const noexcept { return free_pages_; }

  /// Escribe a disco el estado pendiente (area meta).
  void flush();

 private:
  /// Version 1 (#8) guardaba una pagina sugerida en vez de la free list. Un
  /// archivo de esa version se rechaza al abrir con un mensaje explicito en
  /// vez de interpretarse mal: hay que recrearlo.
  static constexpr std::uint32_t kMetaVersion = 2;

  struct Meta {
    std::uint32_t version = kMetaVersion;
    std::uint32_t record_size = 0;
    std::uint64_t live = 0;
    std::uint32_t free_head = kInvalidPage;  // cabeza de la free list
    std::uint32_t free_pages = 0;            // cuantas paginas hay en la lista
  };
  static_assert(sizeof(Meta) <= DiskManager::kMetaSize);

  void load_meta();
  void save_meta();
  void rebuild_keys();
  /// Recorre la free list y comprueba que sea consistente con las paginas.
  void check_free_list();

  /// Lee la pagina `id` en `scratch_` contando la lectura en las estadisticas.
  void fetch(PageId id);
  void store(PageId id);

  [[nodiscard]] std::size_t slot_offset(std::size_t slot) const noexcept {
    return slot * slot_size_;
  }
  /// Bytes del registro dentro del slot (saltando el byte de estado).
  [[nodiscard]] std::span<const std::byte> slot_record(std::size_t slot) const {
    return scratch_.read_bytes(slot_offset(slot) + 1, codec_.size());
  }
  [[nodiscard]] bool slot_used(std::size_t slot) const {
    return scratch_.read_bytes(slot_offset(slot), 1)[0] == kUsed;
  }

  RecordCodec codec_;
  DiskManager disk_;
  Page scratch_;
  std::size_t slot_size_ = 0;
  std::size_t slots_per_page_ = 0;
  std::uint64_t live_ = 0;
  PageId free_head_ = kInvalidPage;
  std::uint32_t free_pages_ = 0;
  /// Claves vivas -> donde estan. Solo para detectar duplicados en insert.
  std::map<Key, RID, KeyLess> keys_;
  OpStats stats_;
};

}  // namespace quipudb
