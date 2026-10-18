// Stemmer de Snowball para ingles, "Porter2" (issue #160).
//
// Sigue https://snowballstem.org/algorithms/english/stemmer.html paso por
// paso. Cada funcion lleva el nombre del paso de esa pagina.

#include <cstddef>
#include <string>
#include <string_view>

#include "quipudb/index/text_stemmer.hpp"
#include "stemmer_snowball.hpp"
#include "texto_utf8.hpp"

namespace quipudb {
namespace {

using snowball::inicio;
using snowball::mas_largo;
using snowball::Palabra;
using snowball::quitar;
using snowball::reemplazar;
using snowball::termina_en;

/// La y es vocal salvo cuando hace de consonante; a esas el preludio las
/// marca como 'Y', que no es vocal.
constexpr bool es_vocal(char32_t c) noexcept {
  return c == U'a' || c == U'e' || c == U'i' || c == U'o' || c == U'u' || c == U'y';
}

struct Regiones {
  std::size_t r1;
  std::size_t r2;
};

Regiones marcar_regiones(const Palabra& w) {
  // Tres prefijos cuyo R1 se fija a mano: sin esto "general" y "generous"
  // compartirian R1 con "gene" y se juntarian en el indice.
  std::size_t r1 = w.size();
  bool fijado = false;
  for (const std::u32string_view prefijo : {U"gener", U"commun", U"arsen"}) {
    if (w.starts_with(prefijo)) {
      r1 = prefijo.size();
      fijado = true;
      break;
    }
  }
  if (!fijado) r1 = snowball::region_tras(w, 0, es_vocal);
  return {r1, snowball::region_tras(w, r1, es_vocal)};
}

/// Termina en silaba corta: consonante-vocal-consonante, con la ultima
/// consonante distinta de w, x e Y ("hop", "rap"); o vocal-consonante al
/// inicio de la palabra ("at", "on"). Se mira la palabra hasta `fin`.
bool silaba_corta(const Palabra& w, std::size_t fin) {
  if (fin >= 3) {
    const char32_t c = w[fin - 1];
    if (!es_vocal(c) && c != U'w' && c != U'x' && c != U'Y' && es_vocal(w[fin - 2]) &&
        !es_vocal(w[fin - 3])) {
      return true;
    }
  }
  return fin == 2 && es_vocal(w[0]) && !es_vocal(w[1]);
}

bool hay_vocal(const Palabra& w, std::size_t hasta) {
  for (std::size_t i = 0; i < hasta; ++i) {
    if (es_vocal(w[i])) return true;
  }
  return false;
}

/// Palabras que el algoritmo no reduce bien y Snowball resuelve con una
/// lista: o tienen raiz fija o no se tocan.
bool excepcion_inicial(Palabra& w) {
  static constexpr struct {
    std::u32string_view palabra;
    std::u32string_view raiz;
  } kEspeciales[] = {
      {U"skis", U"ski"},     {U"skies", U"sky"},   {U"dying", U"die"},   {U"lying", U"lie"},
      {U"tying", U"tie"},    {U"idly", U"idl"},    {U"gently", U"gentl"}, {U"ugly", U"ugli"},
      {U"early", U"earli"},  {U"only", U"onli"},   {U"singly", U"singl"}, {U"sky", U"sky"},
      {U"news", U"news"},    {U"howe", U"howe"},   {U"atlas", U"atlas"},  {U"cosmos", U"cosmos"},
      {U"bias", U"bias"},    {U"andes", U"andes"},
  };
  for (const auto& e : kEspeciales) {
    if (w == e.palabra) {
      w = e.raiz;
      return true;
    }
  }
  return false;
}

/// Despues del paso 1a estas palabras ya no cambian: parecen llevar
/// "-ing"/"-eed" pero es parte de la raiz.
bool excepcion_tras_1a(const Palabra& w) {
  for (const std::u32string_view p : {U"inning", U"outing", U"canning", U"herring", U"earring",
                                      U"proceed", U"exceed", U"succeed"}) {
    if (w == p) return true;
  }
  return false;
}

/// Preludio: la y inicial y la que sigue a una vocal hacen de consonante
/// ("youth", "boy"): se marcan como 'Y'.
bool preludio(Palabra& w) {
  bool hubo = false;
  for (std::size_t i = 0; i < w.size(); ++i) {
    if (w[i] == U'y' && (i == 0 || es_vocal(w[i - 1]))) {
      w[i] = U'Y';
      hubo = true;
    }
  }
  return hubo;
}

/// Paso 1a: plurales ("caresses", "ponies", "cats").
void paso_1a(Palabra& w) {
  const std::u32string_view s = mas_largo(w, {U"sses", U"ied", U"ies", U"s", U"us", U"ss"});
  if (s.empty() || s == U"us" || s == U"ss") return;
  const std::size_t i = inicio(w, s);
  if (s == U"sses") {
    reemplazar(w, s, U"ss");
  } else if (s == U"ied" || s == U"ies") {
    // "ties" -> "tie", pero "cries" -> "cri".
    reemplazar(w, s, i >= 2 ? U"i" : U"ie");
  } else if (i >= 1 && hay_vocal(w, i - 1)) {
    // La s se va si antes de la letra previa hay una vocal: "gaps" -> "gap",
    // pero "gas" y "this" no cambian.
    quitar(w, s);
  }
}

/// Paso 1b: "-eed", "-ed", "-ing" y sus formas en "-ly".
void paso_1b(Palabra& w, const Regiones& r) {
  const std::u32string_view s = mas_largo(w, {U"eed", U"eedly", U"ed", U"edly", U"ing", U"ingly"});
  if (s.empty()) return;
  const std::size_t i = inicio(w, s);
  if (s == U"eed" || s == U"eedly") {
    if (i >= r.r1) reemplazar(w, s, U"ee");
    return;
  }
  if (!hay_vocal(w, i)) return;
  quitar(w, s);
  // Reparar lo que quedo: "luxuriat" -> "luxuriate", "hopp" -> "hop",
  // "hop" -> "hope" (palabra corta).
  if (termina_en(w, U"at") || termina_en(w, U"bl") || termina_en(w, U"iz")) {
    w.push_back(U'e');
  } else if (const std::u32string_view doble =
                 mas_largo(w, {U"bb", U"dd", U"ff", U"gg", U"mm", U"nn", U"pp", U"rr", U"tt"});
             !doble.empty()) {
    w.pop_back();
  } else if (r.r1 == w.size() && silaba_corta(w, w.size())) {
    w.push_back(U'e');
  }
}

/// Paso 1c: la y final tras consonante pasa a i ("happy" -> "happi"), salvo
/// que esa consonante sea la primera letra ("by").
void paso_1c(Palabra& w) {
  const std::size_t n = w.size();
  if (n >= 3 && (w[n - 1] == U'y' || w[n - 1] == U'Y') && !es_vocal(w[n - 2])) w[n - 1] = U'i';
}

/// Paso 2: sufijos derivativos dobles, en R1.
void paso_2(Palabra& w, const Regiones& r) {
  const std::u32string_view s = mas_largo(
      w, {U"tional", U"enci",    U"anci",   U"abli",    U"entli",   U"izer",    U"ization",
          U"ational", U"ation",  U"ator",   U"alism",   U"aliti",   U"alli",    U"fulness",
          U"ousli",  U"ousness", U"iveness", U"iviti",  U"biliti",  U"bli",     U"ogi",
          U"fulli",  U"lessli",  U"li"});
  if (s.empty() || inicio(w, s) < r.r1) return;
  const std::size_t i = inicio(w, s);
  if (s == U"tional") {
    reemplazar(w, s, U"tion");
  } else if (s == U"enci") {
    reemplazar(w, s, U"ence");
  } else if (s == U"anci") {
    reemplazar(w, s, U"ance");
  } else if (s == U"abli") {
    reemplazar(w, s, U"able");
  } else if (s == U"entli") {
    reemplazar(w, s, U"ent");
  } else if (s == U"izer" || s == U"ization") {
    reemplazar(w, s, U"ize");
  } else if (s == U"ational" || s == U"ation" || s == U"ator") {
    reemplazar(w, s, U"ate");
  } else if (s == U"alism" || s == U"aliti" || s == U"alli") {
    reemplazar(w, s, U"al");
  } else if (s == U"fulness") {
    reemplazar(w, s, U"ful");
  } else if (s == U"ousli" || s == U"ousness") {
    reemplazar(w, s, U"ous");
  } else if (s == U"iveness" || s == U"iviti") {
    reemplazar(w, s, U"ive");
  } else if (s == U"biliti" || s == U"bli") {
    reemplazar(w, s, U"ble");
  } else if (s == U"ogi") {
    if (i >= 1 && w[i - 1] == U'l') reemplazar(w, s, U"og");
  } else if (s == U"fulli") {
    reemplazar(w, s, U"ful");
  } else if (s == U"lessli") {
    reemplazar(w, s, U"less");
  } else if (i >= 1) {
    // "li" solo tras una terminacion valida de adverbio.
    switch (w[i - 1]) {
      case U'c': case U'd': case U'e': case U'g': case U'h':
      case U'k': case U'm': case U'n': case U'r': case U't':
        quitar(w, s);
        break;
      default:
        break;
    }
  }
}

/// Paso 3: mas sufijos derivativos, en R1.
void paso_3(Palabra& w, const Regiones& r) {
  const std::u32string_view s = mas_largo(w, {U"tional", U"ational", U"alize", U"icate", U"iciti",
                                              U"ical", U"ful", U"ness", U"ative"});
  if (s.empty() || inicio(w, s) < r.r1) return;
  if (s == U"tional") {
    reemplazar(w, s, U"tion");
  } else if (s == U"ational") {
    reemplazar(w, s, U"ate");
  } else if (s == U"alize") {
    reemplazar(w, s, U"al");
  } else if (s == U"icate" || s == U"iciti" || s == U"ical") {
    reemplazar(w, s, U"ic");
  } else if (s == U"ful" || s == U"ness") {
    quitar(w, s);
  } else if (inicio(w, s) >= r.r2) {
    quitar(w, s);  // "ative", solo en R2
  }
}

/// Paso 4: sufijos en R2.
void paso_4(Palabra& w, const Regiones& r) {
  const std::u32string_view s =
      mas_largo(w, {U"al", U"ance", U"ence", U"er", U"ic", U"able", U"ible", U"ant", U"ement",
                    U"ment", U"ent", U"ism", U"ate", U"iti", U"ous", U"ive", U"ize", U"ion"});
  if (s.empty() || inicio(w, s) < r.r2) return;
  if (s == U"ion") {
    const std::size_t i = inicio(w, s);
    if (i == 0 || (w[i - 1] != U's' && w[i - 1] != U't')) return;
  }
  quitar(w, s);
}

/// Paso 5: la e final y la l doble.
void paso_5(Palabra& w, const Regiones& r) {
  if (w.empty()) return;
  const std::size_t i = w.size() - 1;
  if (w[i] == U'e') {
    if (i >= r.r2 || (i >= r.r1 && !silaba_corta(w, i))) w.pop_back();
  } else if (w[i] == U'l') {
    if (i >= r.r2 && i >= 1 && w[i - 1] == U'l') w.pop_back();
  }
}

}  // namespace

std::string stem_english(std::string_view word) {
  Palabra w = texto::decodificar(word);
  if (excepcion_inicial(w)) return texto::codificar(w);
  if (w.size() < 3) return texto::codificar(w);

  const bool hubo_y = preludio(w);
  const Regiones r = marcar_regiones(w);

  paso_1a(w);
  if (!excepcion_tras_1a(w)) {
    paso_1b(w, r);
    paso_1c(w);
    paso_2(w, r);
    paso_3(w, r);
    paso_4(w, r);
    paso_5(w, r);
  }

  if (hubo_y) {
    for (char32_t& c : w) {
      if (c == U'Y') c = U'y';
    }
  }
  return texto::codificar(w);
}

std::string stem(std::string_view word, TextLanguage language) {
  return language == TextLanguage::kEnglish ? stem_english(word) : stem_spanish(word);
}

}  // namespace quipudb
