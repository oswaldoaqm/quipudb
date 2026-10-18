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
//   ts_lexize('spanish_stem', ...) de PostgreSQL 17.
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

#include <string>
#include <string_view>

namespace quipudb {

/// El stemmer de Snowball para espanol.
[[nodiscard]] std::string stem_spanish(std::string_view word);

}  // namespace quipudb
