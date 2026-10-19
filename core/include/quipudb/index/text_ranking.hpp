#pragma once

// Ranking de documentos por relevancia (issues #164, #165).
//
// Lo comun a TF-IDF + coseno (#164) y BM25 (#165): reciben una consulta en
// texto libre y devuelven los k documentos mas relevantes con su puntaje. Que
// compartan la firma es lo que deja que el parser elija con `USING TF_IDF` o
// `USING BM25` (#168) sin ramificar el codigo, y que el benchmark del 2.3.2
// los intercambie con el mismo script.
//
// Las dos recorren SOLO las listas de postings de los terminos de la consulta
// (term-at-a-time): un documento que no tiene ninguno no se mira. Es lo que
// hace que el indice invertido sea mas rapido que el LIKE de la diapositiva,
// que recorre la tabla entera.

#include <cstddef>
#include <limits>
#include <string_view>
#include <vector>

#include "quipudb/catalog/table.hpp"
#include "quipudb/index/spimi.hpp"

namespace quipudb {

/// Un documento del resultado y su puntaje.
struct ScoredDocument {
  DocId doc = 0;
  double score = 0.0;

  bool operator==(const ScoredDocument&) const = default;
};

/// Pedir todos los resultados, sin cortar en k: la consulta sin LIMIT.
inline constexpr std::size_t kAllResults = std::numeric_limits<std::size_t>::max();

/// Deja en `resultado` los `k` de mayor puntaje, ordenados de mayor a menor
/// y, a igual puntaje, por doc creciente. Es el orden que prometen todos los
/// rankings; vive aqui para que no haya dos copias que se desalineen.
void keep_top_k(std::vector<ScoredDocument>& resultado, std::size_t k);

class TextRanking {
 public:
  virtual ~TextRanking() = default;

  /// Los `k` documentos mas relevantes para `query`, de mayor a menor
  /// puntaje; a igual puntaje, por doc creciente, para que el orden sea
  /// reproducible.
  ///
  /// `query` es texto libre: se tokeniza con las mismas opciones con las que
  /// se indexaron los documentos. Solo salen documentos con puntaje mayor que
  /// cero; si ninguno de los terminos de la consulta esta en el indice, el
  /// resultado es vacio. Si hay menos de k documentos relevantes, salen los
  /// que haya.
  [[nodiscard]] virtual std::vector<ScoredDocument> search(std::string_view query,
                                                           std::size_t k) = 0;

  /// El nombre con el que el ranking aparece en el plan y en el SQL.
  [[nodiscard]] virtual const char* name() const noexcept = 0;

  /// Lo que costo la ultima busqueda:
  ///   - `pages_read`: paginas del indice leidas;
  ///   - `records_examined`: postings recorridos;
  ///   - `records_returned`: documentos devueltos.
  [[nodiscard]] virtual const OpStats& stats() const noexcept = 0;
};

}  // namespace quipudb
