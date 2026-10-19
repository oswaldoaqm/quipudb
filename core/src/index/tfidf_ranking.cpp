#include "quipudb/index/tfidf_ranking.hpp"

#include <cmath>
#include <cstdint>
#include <map>
#include <string>
#include <unordered_map>

#include "quipudb/error.hpp"

namespace quipudb {

TfIdfRanking::TfIdfRanking(InvertedIndex& index, TfWeight tf)
    : index_(index), tokenizer_(index.tokenizer_options()), tf_(tf) {}

std::vector<ScoredDocument> TfIdfRanking::search(std::string_view query, std::size_t k) {
  stats_.reset();
  const std::uint64_t paginas_antes = index_.stats().pages_read;

  // El tf de cada termino en la consulta. Un std::map y no un hash: los
  // productos se suman en el mismo orden en cada corrida, y los puntajes
  // salen identicos hasta el ultimo bit.
  std::map<std::string, std::uint32_t> consulta;
  for (const std::string& t : tokenizer_.tokenize(query)) ++consulta[t];

  const std::uint64_t n = index_.documents();
  std::unordered_map<DocId, double> producto;
  double norma2_consulta = 0.0;
  for (const auto& [termino, tf_q] : consulta) {
    const std::uint32_t df = index_.document_frequency(termino);
    const double idf_t = idf(n, df);
    // Fuera del indice, o en todos los documentos: no pesa en ninguno.
    if (idf_t == 0.0) continue;
    const double w_q = tf_weight(tf_q, tf_) * idf_t;
    norma2_consulta += w_q * w_q;
    for (const Posting& p : index_.postings(termino)) {
      producto[p.doc] += w_q * tf_weight(p.tf, tf_) * idf_t;
      ++stats_.records_examined;
    }
  }

  std::vector<ScoredDocument> resultado;
  if (norma2_consulta > 0.0 && k > 0) {
    const double norma_consulta = std::sqrt(norma2_consulta);
    resultado.reserve(producto.size());
    for (const auto& [doc, punto] : producto) {
      if (punto <= 0.0) continue;
      const auto norma_doc = index_.document_norm(doc, tf_);
      if (!norma_doc || *norma_doc <= 0.0) {
        // Un documento con un posting que pesa tiene norma positiva: si no,
        // el indice esta corrupto.
        throw IoError("TF-IDF: el documento " + std::to_string(doc) +
                      " tiene postings pero no norma en el indice");
      }
      resultado.push_back({doc, punto / (norma_consulta * *norma_doc)});
    }
    keep_top_k(resultado, k);
  }

  stats_.pages_read = index_.stats().pages_read - paginas_antes;
  stats_.records_returned = resultado.size();
  return resultado;
}

}  // namespace quipudb
