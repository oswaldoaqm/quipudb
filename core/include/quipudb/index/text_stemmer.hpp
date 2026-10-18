#pragma once

// Stemming de Snowball (issue #160).
//
// Que es aqui
// -----------
//
//   Reduce una palabra a su raiz para que las variantes de una misma palabra
//   coincidan en el indice: "amigas" y "amigos" dan "amig", "estudiosos" da
//   "estudi". Es la ultima etapa del preprocesamiento de la diapositiva de
//   Bag of Words (tokenizacion -> stopwords -> stemming).
//
//   La raiz no tiene que ser una palabra real. Lo unico que importa es que
//   las variantes den la MISMA raiz y que palabras distintas no se junten.
//
// Por que Snowball y no otro
// --------------------------
//
//   Es el stemmer que usa PostgreSQL en to_tsvector('spanish', ...) y
//   to_tsvector('english', ...), y por lo tanto el que tiene el indice GIN
//   contra el que se compara en el 2.3.2 (#174). Con el mismo stemmer, la
//   comparacion mide la estructura y el ranking, no dos preprocesamientos
//   distintos. Las pruebas verifican palabra por palabra contra
//   ts_lexize('spanish_stem', ...) y ts_lexize('english_stem', ...) de
//   PostgreSQL 17.
//
//   Los algoritmos estan descritos en https://snowballstem.org/algorithms/.
//   Las funciones siguen esa descripcion paso por paso, con los mismos nombres
//   de pasos, para que se puedan revisar contra ella.
//
// Entrada y salida
// ----------------
//
//   La palabra entra en UTF-8, en minusculas y CON sus tildes: las reglas de
//   Snowball las usan ("-ación", "-ándo"). Sale en UTF-8 tal como la deja
//   Snowball, que en espanol ya quita las tildes agudas pero conserva la
//   dieresis. El tokenizador quita lo que quede.
//
//   Las palabras de una o dos letras no cambian en ingles; en espanol casi
//   nunca, porque RV empieza como pronto en la tercera letra.

#include <string>
#include <string_view>

#include "quipudb/index/text_language.hpp"

namespace quipudb {

/// El stemmer de Snowball para espanol.
[[nodiscard]] std::string stem_spanish(std::string_view word);

/// El stemmer de Snowball para ingles, conocido como Porter2.
[[nodiscard]] std::string stem_english(std::string_view word);

/// El stemmer del idioma, elegido en tiempo de ejecucion.
[[nodiscard]] std::string stem(std::string_view word, TextLanguage language);

}  // namespace quipudb
