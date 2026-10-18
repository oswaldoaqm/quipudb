#include "quipudb/index/text_tokenizer.hpp"

#include <cstddef>
#include <string>
#include <utility>
#include <vector>

#include "quipudb/index/text_stopwords.hpp"
#include "texto_utf8.hpp"

namespace quipudb {
namespace {

using texto::kReemplazo;

/// Se ignoran sin cortar la palabra: las marcas diacriticas combinantes y el
/// guion suave. Ver la etapa 1 en el header.
constexpr bool se_ignora(char32_t c) noexcept {
  return (c >= 0x0300 && c <= 0x036F) || c == 0x00AD;
}

/// Si el caracter forma parte de una palabra.
///
/// ASCII y Latin-1 se deciden caracter por caracter. Mas alla de Latin-1 se
/// usa una lista de bloques de puntuacion y simbolos en lugar de una tabla
/// Unicode completa: lo que no es puntuacion conocida cuenta como letra. El
/// error posible es dejar un simbolo raro como termino, que no estorba; el
/// error contrario -- partir una palabra en otro alfabeto -- si estorbaria.
constexpr bool es_de_palabra(char32_t c) noexcept {
  if (c < 0x80) {
    return (c >= U'a' && c <= U'z') || (c >= U'A' && c <= U'Z') || texto::es_digito(c);
  }
  if (c < 0xC0) return false;                  // controles, NBSP, ¿, ¡, «, », ª, º
  if (c <= 0xFF) return c != 0xD7 && c != 0xF7;  // letras de Latin-1, menos × y ÷
  if (c == kReemplazo) return false;
  if (c >= 0x2000 && c <= 0x206F) return false;    // comillas tipograficas, rayas, ...
  if (c >= 0x20A0 && c <= 0x2BFF) return false;    // monedas, flechas, simbolos, dingbats
  if (c >= 0x2E00 && c <= 0x2E7F) return false;    // puntuacion suplementaria
  if (c >= 0x3000 && c <= 0x303F) return false;    // puntuacion CJK
  if (c >= 0xFE00 && c <= 0xFE0F) return false;    // selectores de variante
  if (c >= 0xFE30 && c <= 0xFE6F) return false;    // formas de puntuacion compatibles
  if (c >= 0xFF00 && c <= 0xFF0F) return false;    // puntuacion de ancho completo
  if (c == 0xFEFF) return false;                   // BOM
  if (c >= 0x1F000 && c <= 0x1FAFF) return false;  // emoji y pictogramas
  return true;
}

/// Una palabra tal como aparece, ya en minusculas, con el intervalo de
/// caracteres que ocupaba en el texto: las siglas se reconocen por lo que hay
/// ENTRE dos palabras.
struct Palabra {
  std::u32string letras;
  std::size_t inicio;
  std::size_t fin;
};

std::vector<Palabra> separar(const std::u32string& cps) {
  std::vector<Palabra> palabras;
  std::u32string actual;
  std::size_t inicio = 0;
  bool dentro = false;
  for (std::size_t i = 0; i < cps.size(); ++i) {
    const char32_t c = cps[i];
    if (se_ignora(c)) continue;
    if (es_de_palabra(c)) {
      if (!dentro) {
        inicio = i;
        dentro = true;
      }
      actual.push_back(texto::minuscula(c));
    } else if (dentro) {
      palabras.push_back({std::move(actual), inicio, i});
      actual.clear();
      dentro = false;
    }
  }
  if (dentro) palabras.push_back({std::move(actual), inicio, cps.size()});
  return palabras;
}

bool es_letra_suelta(const Palabra& p) noexcept {
  return p.letras.size() == 1 && !texto::es_digito(p.letras[0]);
}

/// "O.N.U." -> "onu". Junta corridas de dos o mas letras sueltas cuando entre
/// cada par hay exactamente un punto. Con digitos no: "3.14" sigue siendo
/// "3" y "14", porque pegarlos inventaria un numero que no esta en el texto.
std::vector<Palabra> juntar_siglas(std::vector<Palabra> palabras, const std::u32string& cps) {
  std::vector<Palabra> out;
  out.reserve(palabras.size());
  std::size_t i = 0;
  while (i < palabras.size()) {
    std::size_t j = i;
    while (j + 1 < palabras.size() && es_letra_suelta(palabras[j]) &&
           es_letra_suelta(palabras[j + 1]) && palabras[j + 1].inicio == palabras[j].fin + 1 &&
           cps[palabras[j].fin] == U'.') {
      ++j;
    }
    if (j > i) {
      Palabra sigla{{}, palabras[i].inicio, palabras[j].fin};
      for (std::size_t k = i; k <= j; ++k) sigla.letras += palabras[k].letras;
      out.push_back(std::move(sigla));
    } else {
      out.push_back(std::move(palabras[i]));
    }
    i = j + 1;
  }
  return out;
}

/// Minusculas y sin tildes: la forma en que se comparan las stopwords.
std::string normalizar(std::string_view palabra) {
  std::u32string cps = texto::decodificar(palabra);
  for (char32_t& c : cps) c = texto::sin_acento(texto::minuscula(c));
  return texto::codificar(cps);
}

}  // namespace

TextTokenizer::TextTokenizer(TokenizerOptions options) : options_(std::move(options)) {
  for (const TextLanguage idioma : options_.stopword_languages) {
    for (const std::string_view palabra : stopword_list(idioma)) {
      stopwords_.insert(normalizar(palabra));
    }
  }
  for (const std::string& palabra : options_.extra_stopwords) {
    stopwords_.insert(normalizar(palabra));
  }
}

bool TextTokenizer::is_stopword(std::string_view word) const {
  return stopwords_.contains(normalizar(word));
}

std::vector<std::string> TextTokenizer::tokenize(std::string_view text) const {
  const std::u32string cps = texto::decodificar(text);
  std::vector<std::string> terminos;
  for (const Palabra& p : juntar_siglas(separar(cps), cps)) {
    std::string termino = texto::codificar(texto::sin_acentos(p.letras));
    if (termino.size() > options_.max_term_bytes) continue;
    if (stopwords_.contains(termino)) continue;
    terminos.push_back(std::move(termino));
  }
  return terminos;
}

}  // namespace quipudb
