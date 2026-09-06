#pragma once

// Heap File (issue #8).
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
//     next          sin usar en #8; lo usa la free list en #9
//   body: arreglo de slots, todos del mismo tamano
//     slot = [1 byte de estado][record_size bytes de registro]
//     estado 0 = libre, 1 = ocupado
//
// Los registros son de longitud fija (RecordCodec, #7), asi que el slot `i`
// empieza siempre en `i * slot_size` y un RID {pagina, slot} se resuelve sin
// recorrer nada. Esa es la razon de que un RID de heap file sea estable: el
// registro no se mueve nunca de donde se escribio.
//
// El area meta del DiskManager (pagina 0) guarda el estado del archivo entre
// sesiones: tamano de registro con el que se creo, cuantos registros vivos
// hay, y por que pagina conviene empezar a buscar espacio.
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

  /// Escribe a disco el estado pendiente (area meta).
  void flush();

 private:
  struct Meta {
    std::uint32_t record_size = 0;
    std::uint32_t insert_hint = kInvalidPage;  // primera pagina donde probar
    std::uint64_t live = 0;
    std::uint32_t free_head = kInvalidPage;  // reservado para la free list (#9)
    std::uint32_t reserved = 0;
  };
  static_assert(sizeof(Meta) <= DiskManager::kMetaSize);

  void load_meta();
  void save_meta();
  void rebuild_keys();

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
  PageId insert_hint_ = kInvalidPage;
  PageId free_head_ = kInvalidPage;
  /// Claves vivas -> donde estan. Solo para detectar duplicados en insert.
  std::map<Key, RID, KeyLess> keys_;
  OpStats stats_;
};

}  // namespace quipudb
