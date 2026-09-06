#pragma once

// Gestor de archivos de paginas (issue #6).
//
// Un DiskManager es un archivo en disco visto como un arreglo de paginas del
// mismo tamano. Es lo unico del core que llama a read/write del sistema
// operativo: heap file, archivo secuencial e indices trabajan encima de el y
// nunca tocan un std::fstream.
//
// Layout del archivo:
//
//   pagina 0        cabecera del archivo (ver FileHeader) + area `meta`
//   paginas 1..N    paginas de datos; N = page_count()
//
// El tamano de pagina se decide al CREAR el archivo y queda grabado en la
// cabecera. Al reabrir se lee de ahi: si el codigo pide otro tamano, es un
// IoError, no una conversion silenciosa. Asi "cambiar el tamano de pagina"
// es cambiar un argumento del constructor, sin tocar ninguna estructura.
//
// El area `meta` (kMetaSize bytes de la pagina 0) es para el dueno del
// archivo: el heap file guarda ahi la cabeza de su free list, el B+ su raiz.
// Sin eso cada estructura tendria que inventar su propia pagina de metadatos.
//
// Contadores: `reads()` y `writes()` cuentan solo paginas de datos. Son la
// fuente de `OpStats::pages_read` / `pages_written` del contrato (#3).

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <span>

#include "quipudb/catalog/types.hpp"
#include "quipudb/storage/page.hpp"

namespace quipudb {

class DiskManager {
 public:
  static constexpr std::size_t kMetaSize = 64;

  /// Abre el archivo si existe (y valida que su tamano de pagina sea
  /// `page_size`) o lo crea vacio con ese tamano.
  explicit DiskManager(std::filesystem::path path, std::size_t page_size = kDefaultPageSize);
  ~DiskManager();

  DiskManager(const DiskManager&) = delete;
  DiskManager& operator=(const DiskManager&) = delete;

  [[nodiscard]] const std::filesystem::path& path() const noexcept { return path_; }
  [[nodiscard]] std::size_t page_size() const noexcept { return page_size_; }

  /// Cantidad de paginas de datos. Los PageId validos son 1..page_count().
  [[nodiscard]] PageId page_count() const noexcept { return page_count_; }

  /// Agrega una pagina en blanco al final y devuelve su id.
  PageId allocate_page();

  /// Lee la pagina `id` en `page`. Lanza IoError si `id` esta fuera de rango
  /// o si `page.size()` no coincide con el tamano del archivo.
  void read_page(PageId id, Page& page);

  /// Escribe `page` en la posicion `id`. Mismas condiciones que read_page.
  void write_page(PageId id, const Page& page);

  /// Area reservada al dueno del archivo en la pagina 0. `write_meta` acepta
  /// hasta kMetaSize bytes; `read_meta` llena `out` con los primeros
  /// `out.size()` bytes del area.
  void read_meta(std::span<std::byte> out);
  void write_meta(std::span<const std::byte> in);

  /// Fuerza la escritura al sistema operativo de todo lo pendiente.
  void flush();

  /// Deja el archivo con `new_count` paginas de datos, descartando las que
  /// sobran. Es lo que permite que una reorganizacion (#12) devuelva el
  /// espacio en vez de dejar paginas muertas al final. Lanza IoError si
  /// `new_count` es mayor que el actual: esto encoge, no crece.
  void truncate(PageId new_count);

  [[nodiscard]] std::uint64_t reads() const noexcept { return reads_; }
  [[nodiscard]] std::uint64_t writes() const noexcept { return writes_; }
  void reset_counters() noexcept { reads_ = writes_ = 0; }

  /// Tamano del archivo en bytes, incluida la pagina 0. Es lo que el 2.1.6
  /// reporta como "espacio en disco".
  [[nodiscard]] std::uintmax_t file_size() const;

 private:
  struct FileHeader {
    char magic[4] = {'Q', 'P', 'D', 'B'};
    std::uint16_t version = 1;
    std::uint16_t reserved = 0;
    std::uint32_t page_size = 0;
    std::uint32_t page_count = 0;
    // offset 16: area meta de kMetaSize bytes
  };
  static constexpr std::size_t kHeaderBytes = 16;
  static_assert(sizeof(FileHeader) == kHeaderBytes);
  static_assert(kHeaderBytes + kMetaSize <= Page::kMinSize,
                "la pagina 0 tiene que poder contener cabecera y area meta");

  void create(std::size_t page_size);
  void open_existing(std::size_t expected_page_size);
  void write_header();
  void seek_to(PageId id);
  [[nodiscard]] std::streamoff offset_of(PageId id) const noexcept {
    return static_cast<std::streamoff>(id) * static_cast<std::streamoff>(page_size_);
  }
  void check_id(PageId id) const;
  void check_page(const Page& page) const;

  std::filesystem::path path_;
  std::fstream file_;
  std::size_t page_size_ = 0;
  PageId page_count_ = 0;
  std::uint64_t reads_ = 0;
  std::uint64_t writes_ = 0;
};

}  // namespace quipudb
