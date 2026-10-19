#pragma once

// Ranking TF-IDF con similitud del coseno (issue #164).
//
// La formula
// ----------
//
//   La consulta y cada documento son vectores de pesos TF-IDF sobre el
//   vocabulario, y el puntaje es el coseno del angulo entre los dos:
//
//                  sum_t  w(t, q) * w(t, d)
//     cos(q, d) = --------------------------          w(t, x) = tf_w(tf) * idf(t)
//                       ||q|| * ||d||                 idf(t)  = log10(N / df_t)
//
//   con tf_w la variante elegida (`text_weights.hpp`): 1 + log10(tf) por
//   omision, o el tf crudo. La consulta se pesa con la misma formula que los
//   documentos, con el tf de cada termino en la consulta. Es el esquema de la
//   diapositiva de "Ranked Retrieval: resumen": consulta y documentos como
//   vectores TF-IDF, coseno, top-k.
//
//   Por que el coseno y no la distancia euclidiana: la diapositiva de
//   proximidad lo explica con un documento pegado a si mismo, que tiene el
//   mismo contenido pero un vector el doble de largo. El coseno no cambia; la
//   distancia, si. Dividir por las normas hace comparables documentos largos
//   y cortos, y deja el puntaje en [0, 1].
//
// Como se calcula sin recorrer el corpus
// --------------------------------------
//
//   El numerador solo tiene terminos de la consulta: para cada uno se lee su
//   lista de postings y se acumula w(t, q) * w(t, d) en el documento. Los
//   documentos que no tienen ningun termino de la consulta no se tocan.
//
//   El denominador necesita ||d||, que depende de TODOS los terminos del
//   documento, no solo de los de la consulta. Por eso no se calcula aqui: el
//   indice la guarda desde la construccion (`InvertedIndex::document_norm`).
//   ||q|| se calcula sobre los terminos de la consulta que estan en el
//   indice; uno que no esta no pesa en ningun documento.
//
//   Un termino que esta en todos los documentos tiene idf 0: no aporta al
//   puntaje, y su lista ni se lee.

#include <cstddef>
#include <string_view>
#include <vector>

#include "quipudb/index/inverted_index.hpp"
#include "quipudb/index/text_ranking.hpp"
#include "quipudb/index/text_tokenizer.hpp"
#include "quipudb/index/text_weights.hpp"

namespace quipudb {

class TfIdfRanking final : public TextRanking {
 public:
  /// `index` tiene que vivir mientras se use este objeto. La consulta se
  /// tokeniza con las opciones guardadas en el indice.
  explicit TfIdfRanking(InvertedIndex& index, TfWeight tf = TfWeight::kLog);

  [[nodiscard]] std::vector<ScoredDocument> search(std::string_view query,
                                                   std::size_t k) override;

  [[nodiscard]] const char* name() const noexcept override { return "tf_idf"; }
  [[nodiscard]] const OpStats& stats() const noexcept override { return stats_; }

  [[nodiscard]] TfWeight tf() const noexcept { return tf_; }

 private:
  InvertedIndex& index_;
  TextTokenizer tokenizer_;
  TfWeight tf_;
  OpStats stats_;
};

}  // namespace quipudb
