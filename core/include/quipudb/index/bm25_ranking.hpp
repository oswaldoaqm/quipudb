#pragma once

// Ranking BM25 (issue #165).
//
// La formula
// ----------
//
//                            tf(t,d) * (k1 + 1)
//   score(q, d) = sum_t qtf * idf(t) * ---------------------------------------------
//                                      tf(t,d) + k1 * (1 - b + b * |d| / avgdl)
//
//   idf(t) = ln(1 + (N - df_t + 0.5) / (df_t + 0.5))
//
//   con la suma sobre los terminos de la consulta que estan en el documento,
//   y qtf las veces que el termino aparece en la consulta.
//
//   Frente a TF-IDF (#164), BM25 cambia dos cosas:
//
//     Saturacion del tf. El aporte de un termino crece con tf pero se acerca a
//     un techo, idf * (k1 + 1), en lugar de crecer sin limite: la decima
//     aparicion de "perro" aporta mucho menos que la primera. k1 regula que
//     tan rapido satura; con k1 = 0 solo importa si el termino esta o no.
//
//     Normalizacion por longitud. Un documento mas largo que el promedio
//     necesita mas apariciones para el mismo puntaje, porque es mas probable
//     que contenga el termino por casualidad. b regula cuanto: con b = 0 la
//     longitud no importa; con b = 1 se normaliza del todo.
//
//   No hay normas ni coseno: el puntaje no esta en [0, 1] y solo sirve para
//   ordenar documentos de la misma consulta.
//
// El idf, y por que no es el clasico
// ----------------------------------
//
//   El idf clasico de Robertson y Sparck Jones es ln((N - df + 0.5) / (df + 0.5)),
//   y es NEGATIVO cuando el termino esta en mas de la mitad de los documentos:
//   con N = 4, df = 2 da 0 y df = 3 da -0.85. Un documento que contiene ese
//   termino puntuaria PEOR que uno que no lo tiene, que no tiene sentido para
//   un buscador, y con una consulta de varios terminos un termino comun puede
//   restarle puntos al documento que mejor coincide.
//
//   Se usa la variante de Lucene, que suma 1 dentro del logaritmo: siempre es
//   positiva, sigue bajando a medida que el termino es mas comun, y para
//   terminos raros es practicamente la clasica. Es la que usan Elasticsearch
//   y MongoDB, que la diapositiva de MongoDB menciona como "una version
//   mejorada de TF-IDF".
//
//   Por eso, a diferencia de TF-IDF, un termino que esta en TODOS los
//   documentos todavia pesa un poco (idf = ln(1 + 0.5 / (N + 0.5)) > 0): la
//   consulta "casa" sobre documentos que todos dicen "casa" los devuelve,
//   ordenados por cuanto la dicen y por su longitud.

#include <cstddef>
#include <cstdint>
#include <string_view>
#include <vector>

#include "quipudb/index/inverted_index.hpp"
#include "quipudb/index/text_ranking.hpp"
#include "quipudb/index/text_tokenizer.hpp"

namespace quipudb {

struct Bm25Params {
  /// Saturacion del tf. Tiene que ser >= 0.
  double k1{1.2};
  /// Normalizacion por longitud. Tiene que estar en [0, 1].
  double b{0.75};
};

/// El idf de BM25 en la variante de Lucene, siempre positiva.
[[nodiscard]] double bm25_idf(std::uint64_t documentos, std::uint64_t df) noexcept;

class Bm25Ranking final : public TextRanking {
 public:
  /// `index` tiene que vivir mientras se use este objeto. La consulta se
  /// tokeniza con las opciones guardadas en el indice.
  ///
  /// Lanza SchemaError si k1 es negativo o no finito, o si b esta fuera de
  /// [0, 1].
  explicit Bm25Ranking(InvertedIndex& index, Bm25Params params = {});

  [[nodiscard]] std::vector<ScoredDocument> search(std::string_view query,
                                                   std::size_t k) override;

  [[nodiscard]] const char* name() const noexcept override { return "bm25"; }
  [[nodiscard]] const OpStats& stats() const noexcept override { return stats_; }

  [[nodiscard]] const Bm25Params& params() const noexcept { return params_; }

 private:
  InvertedIndex& index_;
  TextTokenizer tokenizer_;
  Bm25Params params_;
  OpStats stats_;
};

}  // namespace quipudb
