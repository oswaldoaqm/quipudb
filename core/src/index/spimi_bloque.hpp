#pragma once

// El formato en disco de un bloque de SPIMI (issues #161, #162).
//
// Lo escriben dos: el `SpimiIndexer`, al volcar el diccionario de un bloque,
// y el merge, cuando hay mas bloques de los que se pueden abrir a la vez y
// tiene que mezclarlos por niveles en bloques intermedios. Por eso el escritor
// vive aqui y no dentro de uno de los dos.

#include <cstdint>
#include <cstring>
#include <filesystem>
#include <span>
#include <string>
#include <string_view>

#include "flujo_paginas.hpp"
#include "quipudb/catalog/table.hpp"
#include "quipudb/error.hpp"
#include "quipudb/index/spimi.hpp"
#include "quipudb/storage/disk_manager.hpp"

namespace quipudb::detail {

/// Lo que va en el area meta de un bloque. Sin la cantidad de bytes, el
/// lector no sabria donde termina la ultima pagina, que casi siempre queda a
/// medias.
struct MetaBloque {
  char magic[4] = {'S', 'P', 'M', 'B'};
  std::uint16_t version = 2;
  std::uint16_t reservado = 0;
  std::uint64_t entradas = 0;
  std::uint64_t postings = 0;
  std::uint64_t bytes = 0;
  PageId primera = kInvalidPage;
};
static_assert(sizeof(MetaBloque) <= DiskManager::kMetaSize);
static_assert(sizeof(Posting) == 8, "los postings se escriben como bytes crudos");

/// La version 2 encadena las paginas del flujo (#162); la 1 las suponia
/// consecutivas desde la pagina 1.
inline constexpr std::uint16_t kVersionBloque = 2;

/// Escribe un bloque entrada por entrada. Las entradas tienen que llegar
/// ordenadas por termino: el escritor no las ordena.
class EscritorDeBloque {
 public:
  EscritorDeBloque(const std::filesystem::path& ruta, std::size_t page_size)
      : disco_(ruta, page_size), escritor_(disco_) {}

  void agregar(std::string_view termino, std::span<const Posting> postings) {
    escritor_.escribir(static_cast<std::uint16_t>(termino.size()));
    escritor_.escribir(std::as_bytes(std::span(termino.data(), termino.size())));
    escritor_.escribir(static_cast<std::uint32_t>(postings.size()));
    escritor_.escribir(std::as_bytes(postings));
    ++meta_.entradas;
    meta_.postings += postings.size();
  }

  /// Termina el bloque y devuelve lo que costo: paginas escritas y postings
  /// (`records_returned`).
  OpStats cerrar() {
    escritor_.cerrar();
    meta_.bytes = escritor_.bytes();
    meta_.primera = escritor_.primera();
    disco_.write_meta(std::as_bytes(std::span<const MetaBloque, 1>(&meta_, 1)));
    disco_.flush();
    OpStats s;
    s.pages_written = disco_.writes();
    s.records_returned = meta_.postings;
    return s;
  }

 private:
  DiskManager disco_;
  EscritorDePaginas escritor_;
  MetaBloque meta_;
};

/// Lee y valida el area meta de un bloque.
inline MetaBloque leer_meta_bloque(const std::filesystem::path& ruta, std::size_t page_size) {
  // DiskManager CREA el archivo si no existe; un bloque que falta es un error,
  // no un bloque vacio.
  if (!std::filesystem::exists(ruta)) {
    throw IoError("SPIMI: no existe el bloque " + ruta.string());
  }
  MetaBloque meta;
  {
    DiskManager disco(ruta, page_size);
    disco.read_meta(std::as_writable_bytes(std::span<MetaBloque, 1>(&meta, 1)));
  }
  constexpr char kMagic[4] = {'S', 'P', 'M', 'B'};
  if (std::memcmp(meta.magic, kMagic, sizeof kMagic) != 0) {
    throw IoError("SPIMI: " + ruta.string() + " no es un bloque de SPIMI");
  }
  if (meta.version != kVersionBloque) {
    throw IoError("SPIMI: el bloque " + ruta.string() + " es de la version " +
                  std::to_string(meta.version) + " del formato; se esperaba la " +
                  std::to_string(kVersionBloque));
  }
  return meta;
}

}  // namespace quipudb::detail
