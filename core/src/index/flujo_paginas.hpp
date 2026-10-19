#pragma once

// Un flujo de bytes de corrido sobre las paginas de datos de un archivo
// (issue #161).
//
// Es interno del core: lo usan los bloques del SPIMI, cuyas entradas -- un
// termino y su lista de postings -- no tienen tamano fijo y pueden ser mas
// grandes que una pagina. En lugar de partir cada entrada a mano, se escriben
// una detras de otra sobre el body de paginas consecutivas, y quien lee pide
// bytes sin saber donde termina cada pagina.
//
// Las dos clases tienen UNA sola pagina en memoria. Eso es lo que permite que
// el merge de bloques (#162) abra muchos bloques a la vez con un buffer de
// lectura cada uno, como pide la diapositiva de BSBI.

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <span>
#include <string>
#include <type_traits>

#include "quipudb/error.hpp"
#include "quipudb/storage/disk_manager.hpp"
#include "quipudb/storage/page.hpp"

namespace quipudb::detail {

/// Escribe bytes al final del archivo, llenando el body de cada pagina antes
/// de pasar a la siguiente.
class EscritorDePaginas {
 public:
  explicit EscritorDePaginas(DiskManager& disco) : disco_(disco), pagina_(disco.page_size()) {}

  void escribir(std::span<const std::byte> datos) {
    while (!datos.empty()) {
      const std::size_t cabe = std::min(datos.size(), pagina_.body_size() - usado_);
      pagina_.write_bytes(usado_, datos.first(cabe));
      usado_ += cabe;
      total_ += cabe;
      datos = datos.subspan(cabe);
      if (usado_ == pagina_.body_size()) volcar();
    }
  }

  /// Un valor trivialmente copiable, como bytes crudos.
  ///
  /// La conversion a `span<const std::byte>` de extension dinamica es
  /// necesaria: `as_bytes` de un span de extension fija devuelve otro span de
  /// extension fija, que no es el tipo de la sobrecarga de arriba, y sin la
  /// conversion esta plantilla se llamaria a si misma.
  template <typename T>
  void escribir(const T& valor) {
    static_assert(std::is_trivially_copyable_v<T>);
    escribir(std::span<const std::byte>(std::as_bytes(std::span<const T, 1>(&valor, 1))));
  }

  /// Escribe la ultima pagina, aunque este a medio llenar. Despues de esto el
  /// flujo esta completo en disco.
  void cerrar() {
    if (usado_ > 0) volcar();
  }

  /// Bytes escritos. El lector los necesita para saber donde termina el flujo:
  /// la ultima pagina puede estar a medias.
  [[nodiscard]] std::uint64_t bytes() const noexcept { return total_; }

 private:
  void volcar() {
    disco_.write_page(disco_.allocate_page(), pagina_);
    pagina_.clear();
    usado_ = 0;
  }

  DiskManager& disco_;
  Page pagina_;
  std::size_t usado_ = 0;
  std::uint64_t total_ = 0;
};

/// Lee de corrido lo que escribio un `EscritorDePaginas`, desde la pagina 1.
class LectorDePaginas {
 public:
  /// `bytes` es lo que devolvio `EscritorDePaginas::bytes()`.
  LectorDePaginas(DiskManager& disco, std::uint64_t bytes)
      : disco_(disco), pagina_(disco.page_size()), restantes_(bytes) {}

  /// Llena `out` entero. Que el flujo se acabe antes es un archivo truncado o
  /// corrupto, no el final normal: quien lee sabe cuantos bytes pide.
  void leer(std::span<std::byte> out) {
    while (!out.empty()) {
      if (restantes_ == 0) {
        throw IoError("flujo de paginas truncado en " + disco_.path().string());
      }
      if (siguiente_ == 1 || pos_ == pagina_.body_size()) {
        disco_.read_page(siguiente_++, pagina_);
        pos_ = 0;
      }
      const std::size_t n = static_cast<std::size_t>(
          std::min<std::uint64_t>({out.size(), pagina_.body_size() - pos_, restantes_}));
      std::memcpy(out.data(), pagina_.body().data() + pos_, n);
      pos_ += n;
      restantes_ -= n;
      out = out.subspan(n);
    }
  }

  template <typename T>
  [[nodiscard]] T leer() {
    static_assert(std::is_trivially_copyable_v<T>);
    T valor;
    leer(std::span<std::byte>(std::as_writable_bytes(std::span<T, 1>(&valor, 1))));
    return valor;
  }

  [[nodiscard]] bool agotado() const noexcept { return restantes_ == 0; }

 private:
  DiskManager& disco_;
  Page pagina_;
  std::uint64_t restantes_;
  PageId siguiente_ = 1;
  std::size_t pos_ = 0;
};

}  // namespace quipudb::detail
