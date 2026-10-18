#pragma once

// Listas de stopwords (issue #160).
//
// Las palabras tan comunes que aparecen en casi todos los documentos y no
// distinguen a ninguno: "de", "la", "que", "the", "of". Su idf es casi cero,
// asi que pesan poco en el ranking, pero ocupan las listas de postings mas
// largas del indice. Descartarlas achica el indice y acelera la consulta sin
// cambiar que documentos salen arriba.

#include <span>
#include <string_view>

#include "quipudb/index/text_language.hpp"

namespace quipudb {

/// La lista de Snowball del idioma: 313 palabras en espanol y 127 en ingles,
/// en minusculas y con sus tildes.
[[nodiscard]] std::span<const std::string_view> stopword_list(TextLanguage language) noexcept;

}  // namespace quipudb
