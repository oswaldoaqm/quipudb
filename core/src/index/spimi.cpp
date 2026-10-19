#include "quipudb/index/spimi.hpp"

#include <algorithm>
#include <span>
#include <string>
#include <system_error>
#include <utility>

#include "../external/temporales.hpp"
#include "quipudb/error.hpp"
#include "spimi_bloque.hpp"

namespace quipudb {

// ---------------------------------------------------------------------------
// SpimiIndexer
// ---------------------------------------------------------------------------

namespace {

/// Lo que cuesta, segun la cuenta del header, un termino nuevo con su lista
/// inicial.
constexpr std::size_t costo_termino_nuevo(std::size_t bytes_termino) noexcept {
  return kSpimiEntryOverhead + bytes_termino + kSpimiInitialPostings * sizeof(Posting);
}

}  // namespace

std::size_t SpimiIndexer::min_memory_budget(const TokenizerOptions& tokenizer) noexcept {
  return costo_termino_nuevo(tokenizer.max_term_bytes);
}

SpimiIndexer::SpimiIndexer(TokenizerOptions tokenizer, SpimiOptions options)
    : tokenizer_(std::move(tokenizer)), options_(std::move(options)) {
  const std::size_t minimo = min_memory_budget(tokenizer_.options());
  if (options_.memory_budget < minimo) {
    throw SchemaError("SPIMI: el presupuesto de memoria es de " +
                      std::to_string(options_.memory_budget) + " bytes; el minimo es " +
                      std::to_string(minimo) + ", lo que cuesta un termino del largo maximo");
  }
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
  ++stats_.records_examined;

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

  for (const auto& [termino, tf] : frecuencias) agregar(termino, Posting{doc, tf});
}

void SpimiIndexer::agregar(std::string_view termino, Posting posting) {
  // AddToDictionary / GetPostingsList, con la cuenta de memoria. Lo que cuesta
  // depende de si el termino es nuevo en ESTE bloque y de si su lista esta
  // llena.
  const auto costo = [&](const std::vector<Posting>* lista) -> std::size_t {
    if (lista == nullptr) return costo_termino_nuevo(termino.size());
    if (lista->size() == lista->capacity()) return lista->capacity() * sizeof(Posting);
    return 0;
  };

  auto it = diccionario_.find(std::string(termino));
  std::size_t necesita = costo(it == diccionario_.end() ? nullptr : &it->second);
  if (usado_ + necesita > options_.memory_budget) {
    // No cabe: el bloque se vuelca y el posting empieza el siguiente. Ahi el
    // termino ya es nuevo, y el minimo del constructor garantiza que cabe.
    volcar_bloque();
    it = diccionario_.end();
    necesita = costo(nullptr);
  }

  if (it == diccionario_.end()) {
    it = diccionario_.try_emplace(std::string(termino)).first;
    it->second.reserve(kSpimiInitialPostings);
  } else if (it->second.size() == it->second.capacity()) {
    // DoublePostingsList.
    it->second.reserve(it->second.capacity() * 2);
  }
  // Los documentos llegan en orden, asi que agregar al final deja la lista
  // ordenada por docID.
  it->second.push_back(posting);
  usado_ += necesita;
  pico_ = std::max(pico_, usado_);
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
  detail::EscritorDeBloque bloque(ruta, options_.page_size);
  for (const auto* entrada : orden) bloque.agregar(entrada->first, entrada->second);
  stats_ += bloque.cerrar();
  bloques_.push_back(ruta);
  diccionario_.clear();
  usado_ = 0;
}

// ---------------------------------------------------------------------------
// SpimiBlockReader
// ---------------------------------------------------------------------------

class SpimiBlockReader::Impl {
 public:
  Impl(const std::filesystem::path& path, std::size_t page_size, const detail::MetaBloque& meta)
      : disco(path, page_size), lector(disco, {meta.primera, 0}, meta.bytes) {}

  DiskManager disco;
  detail::LectorDePaginas lector;
};

SpimiBlockReader::SpimiBlockReader(const std::filesystem::path& path, std::size_t page_size) {
  const detail::MetaBloque meta = detail::leer_meta_bloque(path, page_size);
  entradas_ = meta.entradas;
  impl_ = std::make_unique<Impl>(path, page_size, meta);
}

SpimiBlockReader::~SpimiBlockReader() = default;

OpStats SpimiBlockReader::stats() const noexcept {
  OpStats s;
  s.pages_read = impl_->disco.reads();
  s.records_returned = leidas_;
  return s;
}

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
