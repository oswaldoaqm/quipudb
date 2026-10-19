#include "quipudb/index/spimi.hpp"

#include <algorithm>
#include <cstring>
#include <span>
#include <string>
#include <system_error>
#include <utility>

#include "../external/temporales.hpp"
#include "flujo_paginas.hpp"
#include "quipudb/error.hpp"

namespace quipudb {
namespace {

/// Lo que va en el area meta de un bloque. Sin la cantidad de bytes, el
/// lector no sabria donde termina la ultima pagina, que casi siempre queda a
/// medias.
struct MetaBloque {
  char magic[4] = {'S', 'P', 'M', 'B'};
  std::uint16_t version = 1;
  std::uint16_t reservado = 0;
  std::uint64_t entradas = 0;
  std::uint64_t postings = 0;
  std::uint64_t bytes = 0;
};
static_assert(sizeof(MetaBloque) <= DiskManager::kMetaSize);
static_assert(sizeof(Posting) == 8, "los postings se escriben como bytes crudos");

constexpr char kMagic[4] = {'S', 'P', 'M', 'B'};
constexpr std::uint16_t kVersion = 1;

}  // namespace

// ---------------------------------------------------------------------------
// SpimiIndexer
// ---------------------------------------------------------------------------

SpimiIndexer::SpimiIndexer(TokenizerOptions tokenizer, SpimiOptions options)
    : tokenizer_(std::move(tokenizer)), options_(std::move(options)) {
  if (options_.dir.empty()) options_.dir = std::filesystem::temp_directory_path();
}

SpimiIndexer::~SpimiIndexer() {
  // El destructor no puede lanzar: un bloque que no se pudo borrar queda en
  // el directorio temporal, que es un mal menor frente a abortar.
  for (const auto& bloque : bloques_) {
    std::error_code ec;
    std::filesystem::remove(bloque, ec);
  }
}

void SpimiIndexer::add_document(DocId doc, std::string_view text) {
  if (terminado_) throw Unsupported("SPIMI: no se agregan documentos despues de finish()");
  if (hay_documento_ && doc <= ultimo_) {
    throw InvalidRecord("SPIMI: los documentos tienen que llegar en orden creciente; el " +
                        std::to_string(doc) + " llego despues del " + std::to_string(ultimo_));
  }
  hay_documento_ = true;
  ultimo_ = doc;
  ++documentos_;

  // Frecuencia de cada termino en ESTE documento, en el orden en que aparecen
  // por primera vez. El orden no cambia el resultado -- el bloque se ordena
  // al volcarse --, pero hace la construccion reproducible.
  const std::vector<std::string> terminos = tokenizer_.tokenize(text);
  std::unordered_map<std::string_view, std::size_t> posicion;
  std::vector<std::pair<std::string_view, std::uint32_t>> frecuencias;
  for (const std::string& t : terminos) {
    const auto [it, nuevo] = posicion.try_emplace(t, frecuencias.size());
    if (nuevo) {
      frecuencias.emplace_back(t, 1);
    } else {
      ++frecuencias[it->second].second;
    }
  }

  // Los documentos llegan en orden, asi que agregar al final deja cada lista
  // ordenada por docID.
  for (const auto& [termino, tf] : frecuencias) {
    diccionario_[std::string(termino)].push_back(Posting{doc, tf});
  }
}

void SpimiIndexer::finish() {
  if (terminado_) return;
  volcar_bloque();
  terminado_ = true;
}

void SpimiIndexer::volcar_bloque() {
  if (diccionario_.empty()) return;

  // SortTerms(dictionary): se ordena una sola vez, al volcar. Por bytes: en
  // UTF-8 eso es el orden de los puntos de codigo, y es el que el merge de
  // #162 va a usar para comparar los frentes de cada bloque.
  std::vector<const std::pair<const std::string, std::vector<Posting>>*> orden;
  orden.reserve(diccionario_.size());
  for (const auto& entrada : diccionario_) orden.push_back(&entrada);
  std::sort(orden.begin(), orden.end(),
            [](const auto* a, const auto* b) { return a->first < b->first; });

  const auto ruta = options_.dir / ("quipudb_spimi_" + detail::etiqueta_unica() + "_" +
                                    std::to_string(bloques_.size()) + ".blk");
  {
    DiskManager disco(ruta, options_.page_size);
    detail::EscritorDePaginas escritor(disco);
    MetaBloque meta;
    for (const auto* entrada : orden) {
      const std::string& termino = entrada->first;
      const std::vector<Posting>& postings = entrada->second;
      escritor.escribir(static_cast<std::uint16_t>(termino.size()));
      escritor.escribir(std::as_bytes(std::span(termino.data(), termino.size())));
      escritor.escribir(static_cast<std::uint32_t>(postings.size()));
      escritor.escribir(std::as_bytes(std::span(postings)));
      ++meta.entradas;
      meta.postings += postings.size();
    }
    escritor.cerrar();
    meta.bytes = escritor.bytes();
    disco.write_meta(std::as_bytes(std::span<const MetaBloque, 1>(&meta, 1)));
    disco.flush();
  }
  bloques_.push_back(ruta);
  diccionario_.clear();
}

// ---------------------------------------------------------------------------
// SpimiBlockReader
// ---------------------------------------------------------------------------

class SpimiBlockReader::Impl {
 public:
  Impl(const std::filesystem::path& path, std::size_t page_size, const MetaBloque& meta)
      : disco(path, page_size), lector(disco, meta.bytes) {}

  DiskManager disco;
  detail::LectorDePaginas lector;
};

SpimiBlockReader::SpimiBlockReader(const std::filesystem::path& path, std::size_t page_size) {
  // DiskManager CREA el archivo si no existe; un bloque que falta es un error,
  // no un bloque vacio.
  if (!std::filesystem::exists(path)) {
    throw IoError("SPIMI: no existe el bloque " + path.string());
  }
  MetaBloque meta;
  {
    DiskManager disco(path, page_size);
    disco.read_meta(std::as_writable_bytes(std::span<MetaBloque, 1>(&meta, 1)));
  }
  if (std::memcmp(meta.magic, kMagic, sizeof kMagic) != 0) {
    throw IoError("SPIMI: " + path.string() + " no es un bloque de SPIMI");
  }
  if (meta.version != kVersion) {
    throw IoError("SPIMI: el bloque " + path.string() + " es de la version " +
                  std::to_string(meta.version) + " del formato; se esperaba la " +
                  std::to_string(kVersion));
  }
  entradas_ = meta.entradas;
  impl_ = std::make_unique<Impl>(path, page_size, meta);
}

SpimiBlockReader::~SpimiBlockReader() = default;

bool SpimiBlockReader::next(PostingEntry& out) {
  if (leidas_ == entradas_) return false;
  auto& lector = impl_->lector;
  out.term.resize(lector.leer<std::uint16_t>());
  lector.leer(std::as_writable_bytes(std::span(out.term.data(), out.term.size())));
  out.postings.resize(lector.leer<std::uint32_t>());
  lector.leer(std::as_writable_bytes(std::span(out.postings)));
  ++leidas_;
  return true;
}

}  // namespace quipudb
