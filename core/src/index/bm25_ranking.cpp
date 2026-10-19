#include "quipudb/index/bm25_ranking.hpp"

#include <cmath>
#include <cstdint>
#include <map>
#include <string>
#include <unordered_map>

#include "quipudb/error.hpp"

namespace quipudb {

double bm25_idf(std::uint64_t documentos, std::uint64_t df) noexcept {
  if (df == 0 || df > documentos) return 0.0;
  const double n = static_cast<double>(documentos);
  const double d = static_cast<double>(df);
  return std::log(1.0 + (n - d + 0.5) / (d + 0.5));
}

Bm25Ranking::Bm25Ranking(InvertedIndex& index, Bm25Params params)
    : index_(index), tokenizer_(index.tokenizer_options()), params_(params) {
  if (!std::isfinite(params_.k1) || params_.k1 < 0.0) {
    throw SchemaError("BM25: k1 tiene que ser un numero >= 0; se dio " +
                      std::to_string(params_.k1));
  }
  if (!std::isfinite(params_.b) || params_.b < 0.0 || params_.b > 1.0) {
    throw SchemaError("BM25: b tiene que estar en [0, 1]; se dio " + std::to_string(params_.b));
  }
}

std::vector<ScoredDocument> Bm25Ranking::search(std::string_view query, std::size_t k) {
  stats_.reset();
  const std::uint64_t paginas_antes = index_.stats().pages_read;

  // Como en TF-IDF: un std::map para sumar siempre en el mismo orden.
  std::map<std::string, std::uint32_t> consulta;
  for (const std::string& t : tokenizer_.tokenize(query)) ++consulta[t];

  const std::uint64_t n = index_.documents();
  const double avgdl = index_.average_document_length();
  const double k1 = params_.k1;
  const double b = params_.b;

  std::unordered_map<DocId, double> puntaje;
  for (const auto& [termino, qtf] : consulta) {
    const std::uint32_t df = index_.document_frequency(termino);
    if (df == 0) continue;
    const double peso = static_cast<double>(qtf) * bm25_idf(n, df);
    for (const Posting& p : index_.postings(termino)) {
      const auto largo = index_.document_length(p.doc);
      if (!largo || *largo == 0) {
        // Un documento con postings tiene al menos un termino: si no, el
        // indice esta corrupto.
        throw IoError("BM25: el documento " + std::to_string(p.doc) +
                      " tiene postings pero no longitud en el indice");
      }
      const double tf = static_cast<double>(p.tf);
      // avgdl > 0: hay al menos un documento con terminos, este.
      const double normalizacion = k1 * (1.0 - b + b * static_cast<double>(*largo) / avgdl);
      puntaje[p.doc] += peso * tf * (k1 + 1.0) / (tf + normalizacion);
      ++stats_.records_examined;
    }
  }

  std::vector<ScoredDocument> resultado;
  if (k > 0) {
    resultado.reserve(puntaje.size());
    for (const auto& [doc, s] : puntaje) {
      if (s > 0.0) resultado.push_back({doc, s});
    }
    keep_top_k(resultado, k);
  }

  stats_.pages_read = index_.stats().pages_read - paginas_antes;
  stats_.records_returned = resultado.size();
  return resultado;
}

}  // namespace quipudb
