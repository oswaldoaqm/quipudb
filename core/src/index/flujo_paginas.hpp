#pragma once

// Un flujo de bytes de corrido sobre paginas encadenadas (issues #161, #162).
//
// Es interno del core: lo usan los bloques del SPIMI y el indice invertido,
// cuyas entradas -- un termino y su lista de postings -- no tienen tamano fijo
// y pueden ser mas grandes que una pagina. En lugar de partir cada entrada a
// mano, se escriben una detras de otra sobre el body de las paginas, y quien
// lee pide bytes sin saber donde termina cada pagina.
//
// Las paginas de un flujo se encadenan con `Page::next` y no tienen por que
// ser consecutivas en el archivo. Eso es lo que deja que el indice invertido
// (#162) escriba a la vez, en un solo archivo, las listas de postings y su
// diccionario: cada uno pide paginas cuando las necesita y las de uno quedan
// intercaladas con las del otro.
//
// Las dos clases tienen UNA sola pagina en memoria. Eso es lo que permite que
// el merge de bloques abra muchos bloques a la vez con un buffer de lectura
// cada uno, como pide la diapositiva de BSBI.

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

/// Un lugar dentro de un flujo: la pagina y el byte dentro de su body.
struct PosicionFlujo {
  PageId pagina = kInvalidPage;
  std::uint16_t offset = 0;

  bool operator==(const PosicionFlujo&) const = default;
};

/// Escribe bytes al final de un flujo, llenando el body de cada pagina antes
/// de pedir la siguiente.
class EscritorDePaginas {
 public:
  explicit EscritorDePaginas(DiskManager& disco) : disco_(disco), pagina_(disco.page_size()) {}

  void escribir(std::span<const std::byte> datos) {
    while (!datos.empty()) {
      asegurar_espacio();
      const std::size_t cabe = std::min(datos.size(), pagina_.body_size() - usado_);
      pagina_.write_bytes(usado_, datos.first(cabe));
      usado_ += cabe;
      total_ += cabe;
      datos = datos.subspan(cabe);
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

  /// Donde va a caer el proximo byte. Reserva la pagina si hace falta, asi que
  /// la posicion siempre se puede leer despues.
  [[nodiscard]] PosicionFlujo posicion() {
    asegurar_espacio();
    return {actual_, static_cast<std::uint16_t>(usado_)};
  }

  /// La primera pagina del flujo, o kInvalidPage si no se escribio nada.
  [[nodiscard]] PageId primera() const noexcept { return primera_; }

  /// Escribe la ultima pagina, aunque este a medio llenar, con `next` en
  /// kInvalidPage. Despues de esto el flujo esta completo en disco.
  void cerrar() {
    if (actual_ != kInvalidPage) disco_.write_page(actual_, pagina_);
    actual_ = kInvalidPage;
  }

  /// Bytes escritos. El lector los necesita para saber donde termina el flujo:
  /// la ultima pagina puede estar a medias.
  [[nodiscard]] std::uint64_t bytes() const noexcept { return total_; }

 private:
  /// Deja una pagina con espacio. La pagina llena se escribe recien cuando
  /// llega el primer byte de la siguiente: asi se sabe a cual apunta, y un
  /// flujo que termina justo en el borde no deja una pagina vacia al final.
  void asegurar_espacio() {
    if (actual_ == kInvalidPage) {
      actual_ = disco_.allocate_page();
      if (primera_ == kInvalidPage) primera_ = actual_;
      pagina_.clear();
      usado_ = 0;
    } else if (usado_ == pagina_.body_size()) {
      const PageId siguiente = disco_.allocate_page();
      pagina_.set_next(siguiente);
      disco_.write_page(actual_, pagina_);
      pagina_.clear();
      actual_ = siguiente;
      usado_ = 0;
    }
  }

  DiskManager& disco_;
  Page pagina_;
  PageId actual_ = kInvalidPage;
  PageId primera_ = kInvalidPage;
  std::size_t usado_ = 0;
  std::uint64_t total_ = 0;
};

/// Lee de corrido lo que escribio un `EscritorDePaginas`, desde una posicion,
/// siguiendo la cadena de paginas.
class LectorDePaginas {
 public:
  /// `bytes` es cuanto se va a leer desde `inicio`: todo el flujo, o una sola
  /// lista de postings.
  LectorDePaginas(DiskManager& disco, PosicionFlujo inicio, std::uint64_t bytes)
      : disco_(disco),
        pagina_(disco.page_size()),
        restantes_(bytes),
        siguiente_(inicio.pagina),
        pos_(inicio.offset) {}

  /// Llena `out` entero. Que el flujo se acabe antes es un archivo truncado o
  /// corrupto, no el final normal: quien lee sabe cuantos bytes pide.
  void leer(std::span<std::byte> out) {
    while (!out.empty()) {
      if (restantes_ == 0) throw truncado();
      if (!cargada_) {
        cargar(siguiente_);
      } else if (pos_ == pagina_.body_size()) {
        cargar(pagina_.next());
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
  void cargar(PageId id) {
    if (id == kInvalidPage || id > disco_.page_count()) throw truncado();
    disco_.read_page(id, pagina_);
    cargada_ = true;
  }

  [[nodiscard]] IoError truncado() const {
    return IoError("flujo de paginas truncado en " + disco_.path().string());
  }

  DiskManager& disco_;
  Page pagina_;
  std::uint64_t restantes_;
  PageId siguiente_;
  std::size_t pos_;
  bool cargada_ = false;
};

}  // namespace quipudb::detail
