#include "quipudb/index/text_index.hpp"

#include <cstring>
#include <fstream>
#include <span>
#include <string>
#include <system_error>
#include <utility>
#include <variant>

#include "flujo_paginas.hpp"
#include "quipudb/error.hpp"
#include "quipudb/index/spimi.hpp"
#include "quipudb/index/tfidf_ranking.hpp"
#include "quipudb/storage/disk_manager.hpp"

namespace quipudb {
namespace {

/// El area meta del archivo de RIDs.
struct MetaRids {
  char magic[4] = {'Q', 'P', 'T', 'R'};
  std::uint16_t version = 1;
  std::uint16_t reservado = 0;
  std::uint32_t documentos = 0;
  PageId primera = kInvalidPage;
  std::uint64_t bytes = 0;
};
static_assert(sizeof(MetaRids) <= DiskManager::kMetaSize);

constexpr char kMagicRids[4] = {'Q', 'P', 'T', 'R'};

/// Cada RID se escribe como (u32 pagina, u16 slot), campo por campo: el
/// struct RID tiene relleno y escribirlo crudo dejaria bytes indeterminados.
void escribir_rids(const std::filesystem::path& ruta, const std::vector<RID>& rids,
                   std::size_t page_size) {
  DiskManager disco(ruta, page_size);
  detail::EscritorDePaginas escritor(disco);
  for (const RID& r : rids) {
    escritor.escribir(r.page);
    escritor.escribir(r.slot);
  }
  escritor.cerrar();
  MetaRids meta;
  meta.documentos = static_cast<std::uint32_t>(rids.size());
  meta.primera = escritor.primera();
  meta.bytes = escritor.bytes();
  disco.write_meta(std::as_bytes(std::span<const MetaRids, 1>(&meta, 1)));
  disco.flush();
}

std::vector<RID> leer_rids(const std::filesystem::path& ruta, std::size_t page_size) {
  if (!std::filesystem::exists(ruta)) {
    throw IoError("indice de texto: falta el archivo de RIDs " + ruta.string());
  }
  DiskManager disco(ruta, page_size);
  MetaRids meta;
  disco.read_meta(std::as_writable_bytes(std::span<MetaRids, 1>(&meta, 1)));
  if (std::memcmp(meta.magic, kMagicRids, sizeof kMagicRids) != 0 || meta.version != 1) {
    throw IoError("indice de texto: " + ruta.string() + " no es un archivo de RIDs valido");
  }
  std::vector<RID> rids(meta.documentos);
  detail::LectorDePaginas lector(disco, {meta.primera, 0}, meta.bytes);
  for (RID& r : rids) {
    r.page = lector.leer<PageId>();
    r.slot = lector.leer<SlotId>();
  }
  return rids;
}

void borrar(const std::filesystem::path& ruta) noexcept {
  std::error_code ec;
  std::filesystem::remove(ruta, ec);
}

}  // namespace

TextIndex::TextIndex(std::filesystem::path path, Column column, TableFile& data,
                     std::size_t page_size)
    : path_(std::move(path)), column_(std::move(column)), data_(&data), page_size_(page_size) {
  if (data.kind() != kind::kHeap) {
    throw SchemaError("un indice de texto necesita un heap file como tabla de datos, y " +
                      data.schema().table_name + " se almacena como " +
                      std::string(data.kind()) + ", cuyos RID no son estables");
  }
  const auto position = data.schema().find(column_.name);
  if (!position) {
    throw SchemaError("la columna " + column_.name + " no existe en " +
                      data.schema().table_name);
  }
  key_index_ = *position;
  const Column& real = data.schema().columns[key_index_];
  if (real.type != column_.type || real.length != column_.length) {
    throw SchemaError("la columna " + column_.name + " de " + data.schema().table_name +
                      " no coincide con la que se pidio indexar");
  }
  if (column_.type != DataType::Varchar) {
    throw SchemaError("un indice de texto requiere una columna VARCHAR; " + column_.name +
                      " es " + std::string(to_string(column_.type)));
  }
  abrir();
}

TextIndex::~TextIndex() = default;

std::filesystem::path TextIndex::rids_path(const std::filesystem::path& path) {
  return std::filesystem::path(path.string() + ".rids");
}

std::filesystem::path TextIndex::stale_marker_path(const std::filesystem::path& path) {
  return std::filesystem::path(path.string() + ".desactualizado");
}

void TextIndex::marcar_desactualizado() {
  if (desactualizado_) return;  // un archivo por cambio de estado, no por fila
  const auto marca = stale_marker_path(path_);
  std::ofstream(marca).flush();
  if (!std::filesystem::exists(marca)) {
    throw IoError("indice de texto: no se pudo dejar la marca " + marca.string());
  }
  desactualizado_ = true;
}

void TextIndex::abrir() {
  tfidf_.reset();
  bm25_.reset();
  indice_.reset();
  rids_.clear();
  desactualizado_ = std::filesystem::exists(stale_marker_path(path_));
  if (!std::filesystem::exists(path_)) return;
  indice_ = std::make_unique<InvertedIndex>(path_, page_size_);
  rids_ = leer_rids(rids_path(path_), page_size_);
  if (rids_.size() != indice_->documents()) {
    throw IoError("indice de texto: " + path_.string() + " tiene " +
                  std::to_string(indice_->documents()) + " documentos y su archivo de RIDs " +
                  std::to_string(rids_.size()));
  }
}

void TextIndex::build(const TokenizerOptions& tokenizer) {
  // Se construye al lado, con otro nombre, y se reemplaza al final: si algo
  // falla a medias, el indice anterior sigue sirviendo.
  const std::filesystem::path nuevo(path_.string() + ".nuevo");
  const auto rids_nuevo = rids_path(nuevo);
  borrar(nuevo);
  borrar(rids_nuevo);

  try {
    SpimiIndexer spimi(tokenizer, SpimiOptions{.page_size = page_size_, .dir = path_.parent_path()});
    std::vector<RID> rids;
    auto cursor = data_->cursor();
    Record registro;
    while (cursor->next(registro)) {
      rids.push_back(cursor->rid());
      spimi.add_document(static_cast<DocId>(rids.size()),
                         std::get<std::string>(registro.at(key_index_)));
    }
    spimi.finish();
    stats_ += spimi.stats();
    stats_ += InvertedIndex::build(spimi, nuevo).stats;
    escribir_rids(rids_nuevo, rids, page_size_);
  } catch (...) {
    borrar(nuevo);
    borrar(rids_nuevo);
    throw;
  }

  // Soltar los archivos viejos antes de reemplazarlos: en Windows uno abierto
  // no se borra.
  indice_.reset();
  borrar(path_);
  borrar(rids_path(path_));
  std::filesystem::rename(nuevo, path_);
  std::filesystem::rename(rids_nuevo, rids_path(path_));
  // La marca se quita al final: si algo de arriba fallo, el indice sigue
  // desactualizado y la proxima busqueda lo vuelve a intentar.
  borrar(stale_marker_path(path_));
  abrir();
}

TextRanking& TextIndex::ranking(TextRankingKind r) {
  if (r == TextRankingKind::kBm25) {
    if (!bm25_) bm25_ = std::make_unique<Bm25Ranking>(*indice_);
    return *bm25_;
  }
  if (!tfidf_) tfidf_ = std::make_unique<TfIdfRanking>(*indice_);
  return *tfidf_;
}

std::vector<std::pair<RID, double>> TextIndex::search_text(std::string_view query,
                                                           TextRankingKind ranking_kind,
                                                           std::size_t k) {
  // La tabla cambio desde la ultima construccion: se reconstruye ahora, con
  // las mismas opciones. Ver "Cuando la tabla cambia" en el header.
  if (desactualizado_) build(indice_ ? indice_->tokenizer_options() : TokenizerOptions{});
  (void)inverted_index();  // lanza si nunca se construyo
  TextRanking& r = ranking(ranking_kind);
  const auto documentos = r.search(query, k);
  stats_ += r.stats();
  std::vector<std::pair<RID, double>> out;
  out.reserve(documentos.size());
  for (const ScoredDocument& d : documentos) out.emplace_back(rids_.at(d.doc - 1), d.score);
  return out;
}

InvertedIndex& TextIndex::inverted_index() {
  if (!indice_) {
    throw Unsupported("el indice de texto sobre " + column_.name +
                      " nunca se construyo: hay que llamar a build()");
  }
  return *indice_;
}

const TokenizerOptions& TextIndex::tokenizer_options() const {
  if (!indice_) {
    throw Unsupported("el indice de texto sobre " + column_.name + " nunca se construyo");
  }
  return indice_->tokenizer_options();
}

std::uint64_t TextIndex::terms() const noexcept { return indice_ ? indice_->terms() : 0; }

std::size_t TextIndex::size() const { return rids_.size(); }
const OpStats& TextIndex::stats() const noexcept { return stats_; }
void TextIndex::reset_stats() noexcept { stats_.reset(); }

// --- lo que no aplica a un indice de texto ---------------------------------
//
// insert y remove si aplican: marcan el indice como desactualizado (ver el
// header). Lo que sigue no tiene sentido para texto.

void TextIndex::no_aplica(const char* operacion) const {
  throw Unsupported(std::string("el indice de texto sobre ") + column_.name + " no implementa " +
                    operacion);
}

void TextIndex::insert(const Key&, RID) { marcar_desactualizado(); }

std::size_t TextIndex::remove(const Key&) {
  marcar_desactualizado();
  return 0;
}

bool TextIndex::remove(const Key&, RID) {
  marcar_desactualizado();
  return true;
}
std::vector<RID> TextIndex::search(const Key&) {
  no_aplica("search por igualdad; use search_text");
}
std::vector<RID> TextIndex::range_search(const Key&, const Key&) {
  no_aplica("range_search; use search_text");
}
std::vector<std::pair<Key, RID>> TextIndex::scan() {
  no_aplica("scan: no guarda pares (texto, RID)");
}

}  // namespace quipudb
