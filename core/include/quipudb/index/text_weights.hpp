#pragma once

// Pesos TF-IDF (issue #164).
//
// Las formulas, en un solo lugar: las usa el indice invertido para calcular
// la norma de cada documento al construirse, y el ranking para pesar la
// consulta y los postings. Si cada uno tuviera su copia, bastaria con que una
// cambiara para que el coseno dejara de estar en [0, 1] sin que nada lo
// denunciara.
//
//   w(t, d) = tf_w(tf_{t,d}) * idf(t)          idf(t) = log10(N / df_t)
//
// El idf es el de la diapositiva: base 10, sin suavizado. Un termino que esta
// en todos los documentos tiene idf 0 y no pesa: no distingue a ninguno.
//
// El tf tiene dos variantes, y las dos aparecen en las diapositivas:
//
//   kLog   1 + log10(tf)     la "log-frequency weighting": 10 apariciones no
//                            hacen a un documento 10 veces mas relevante que
//                            una. Es la que usa el ejemplo de las novelas.
//   kRaw   tf                la del ejemplo resuelto de pesos TF-IDF (oro,
//                            plata, camion), cuyas similitudes 0.7561 y 0.3272
//                            se reproducen exactamente con ella.
//
// Por omision se usa kLog: la diapositiva de "Term frequency" explica por que
// el tf crudo no es lo que se quiere.

#include <cmath>
#include <cstdint>

namespace quipudb {

enum class TfWeight : std::uint8_t {
  kLog,
  kRaw,
};

/// El nombre con el que la variante aparece en el plan.
[[nodiscard]] constexpr const char* name_of(TfWeight tf) noexcept {
  return tf == TfWeight::kRaw ? "raw" : "log";
}

/// El peso del tf. 0 si el termino no aparece.
[[nodiscard]] inline double tf_weight(std::uint32_t tf, TfWeight variante) noexcept {
  if (tf == 0) return 0.0;
  if (variante == TfWeight::kRaw) return static_cast<double>(tf);
  return 1.0 + std::log10(static_cast<double>(tf));
}

/// log10(N / df). 0 si el termino no esta en ningun documento: no hay nada
/// que pesar, y asi la division no explota.
[[nodiscard]] inline double idf(std::uint64_t documentos, std::uint64_t df) noexcept {
  if (df == 0 || documentos == 0) return 0.0;
  return std::log10(static_cast<double>(documentos) / static_cast<double>(df));
}

}  // namespace quipudb
