#pragma once

// Pagina de disco (issue #6).
//
// Una Page es un bloque de bytes de tamano fijo que se lee y escribe completo.
// Los primeros `kHeaderSize` bytes son la cabecera comun a todas las
// estructuras; el resto (`body()`) lo interpreta cada una a su manera: el heap
// file guarda slots de registros, el B+ guarda claves y punteros, etc.
//
// Layout:
//
//   offset  tamano  campo
//   0       4       next          PageId de la siguiente pagina de la cadena,
//                                 o kInvalidPage. Enlaza paginas de un mismo
//                                 archivo (overflow, free list, hojas del B+).
//   4       2       record_count  cuantos registros/entradas vivos hay en el body
//   6       2       free_space    bytes libres en el body
//   8       ...     body          page_size - kHeaderSize bytes
//
// Los enteros se guardan en el orden de bytes de la maquina (memcpy). Todo el
// equipo trabaja en x86-64, y un archivo de paginas no viaja entre
// arquitecturas; si algun dia importa, el punto unico de cambio es
// `read<T>` / `write<T>`.
//
// `free_space` y `record_count` son uint16, asi que el tamano maximo de pagina
// es 65536 bytes. Es un limite generoso: los benchmarks del 2.1.6 se mueven
// entre 512 y 16384.

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <span>
#include <type_traits>
#include <vector>

#include "quipudb/catalog/types.hpp"

namespace quipudb {

class Page {
 public:
  static constexpr std::size_t kHeaderSize = 8;
  static constexpr std::size_t kMinSize = 128;
  static constexpr std::size_t kMaxSize = 65536;

  struct Header {
    PageId next = kInvalidPage;
    std::uint16_t record_count = 0;
    std::uint16_t free_space = 0;
  };

  /// Pagina en blanco: cabecera con `free_space = body_size()` y body en ceros.
  explicit Page(std::size_t page_size = kDefaultPageSize) : data_(check_size(page_size)) {
    clear();
  }

  [[nodiscard]] std::size_t size() const noexcept { return data_.size(); }
  [[nodiscard]] std::size_t body_size() const noexcept { return data_.size() - kHeaderSize; }

  /// Vuelve a dejar la pagina en blanco sin cambiar su tamano.
  void clear() noexcept {
    std::fill(data_.begin(), data_.end(), std::byte{0});
    set_header(Header{kInvalidPage, 0, static_cast<std::uint16_t>(body_size())});
  }

  // --- cabecera -----------------------------------------------------------

  [[nodiscard]] Header header() const noexcept {
    Header h;
    std::memcpy(&h.next, data_.data() + 0, sizeof h.next);
    std::memcpy(&h.record_count, data_.data() + 4, sizeof h.record_count);
    std::memcpy(&h.free_space, data_.data() + 6, sizeof h.free_space);
    return h;
  }

  void set_header(const Header& h) noexcept {
    std::memcpy(data_.data() + 0, &h.next, sizeof h.next);
    std::memcpy(data_.data() + 4, &h.record_count, sizeof h.record_count);
    std::memcpy(data_.data() + 6, &h.free_space, sizeof h.free_space);
  }

  [[nodiscard]] PageId next() const noexcept { return header().next; }
  [[nodiscard]] std::uint16_t record_count() const noexcept { return header().record_count; }
  [[nodiscard]] std::uint16_t free_space() const noexcept { return header().free_space; }

  void set_next(PageId p) noexcept { std::memcpy(data_.data() + 0, &p, sizeof p); }
  void set_record_count(std::uint16_t n) noexcept { std::memcpy(data_.data() + 4, &n, sizeof n); }
  void set_free_space(std::uint16_t n) noexcept { std::memcpy(data_.data() + 6, &n, sizeof n); }

  // --- acceso a bytes -----------------------------------------------------

  /// Toda la pagina, cabecera incluida. Es lo que DiskManager lee y escribe.
  [[nodiscard]] std::span<std::byte> bytes() noexcept { return data_; }
  [[nodiscard]] std::span<const std::byte> bytes() const noexcept { return data_; }

  /// Solo el body, que es lo que cada estructura interpreta.
  [[nodiscard]] std::span<std::byte> body() noexcept {
    return std::span<std::byte>(data_).subspan(kHeaderSize);
  }
  [[nodiscard]] std::span<const std::byte> body() const noexcept {
    return std::span<const std::byte>(data_).subspan(kHeaderSize);
  }

  /// Lee un valor trivialmente copiable en `offset` bytes desde el inicio del
  /// body. Lanza std::out_of_range si no cabe: un offset mal calculado es un
  /// bug, no una condicion de disco.
  template <typename T>
  [[nodiscard]] T read(std::size_t offset) const {
    static_assert(std::is_trivially_copyable_v<T>);
    check_range(offset, sizeof(T));
    T value;
    std::memcpy(&value, body().data() + offset, sizeof(T));
    return value;
  }

  template <typename T>
  void write(std::size_t offset, const T& value) {
    static_assert(std::is_trivially_copyable_v<T>);
    check_range(offset, sizeof(T));
    std::memcpy(body().data() + offset, &value, sizeof(T));
  }

  /// Copia `src` al body en `offset`. Mismas reglas que `write<T>`.
  void write_bytes(std::size_t offset, std::span<const std::byte> src) {
    check_range(offset, src.size());
    std::memcpy(body().data() + offset, src.data(), src.size());
  }

  [[nodiscard]] std::span<const std::byte> read_bytes(std::size_t offset,
                                                      std::size_t length) const {
    check_range(offset, length);
    return body().subspan(offset, length);
  }

  friend bool operator==(const Page& a, const Page& b) noexcept { return a.data_ == b.data_; }

 private:
  // Fuera de linea a proposito (page.cpp): con -O2 GCC no ve que el throw
  // protege al memcpy y emite -Wstringop-overflow falsos en las pruebas.
  static std::size_t check_size(std::size_t page_size);
  void check_range(std::size_t offset, std::size_t length) const;

  std::vector<std::byte> data_;
};

}  // namespace quipudb
