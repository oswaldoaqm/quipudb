#pragma once

// Idiomas del texto indexado (issue #160).
//
// Decide dos cosas del tokenizador: que lista de stopwords se descarta y que
// stemmer se aplica. Son los dos idiomas que pide el enunciado y los dos que
// tienen configuracion de texto en PostgreSQL con el mismo nombre, que es
// contra lo que se compara el indice en el 2.3.2.

#include <cstdint>

namespace quipudb {

enum class TextLanguage : std::uint8_t {
  kSpanish,
  kEnglish,
};

/// El nombre con el que el idioma aparece en el plan y en la configuracion de
/// texto de PostgreSQL ('spanish', 'english').
[[nodiscard]] constexpr const char* name_of(TextLanguage language) noexcept {
  return language == TextLanguage::kEnglish ? "english" : "spanish";
}

}  // namespace quipudb
