// Stemmer de Snowball para espanol (issue #160).
//
// Sigue https://snowballstem.org/algorithms/spanish/stemmer.html paso por
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

constexpr bool es_vocal(char32_t c) noexcept {
  switch (c) {
    case U'a': case U'e': case U'i': case U'o': case U'u':
    case U'á': case U'é': case U'í': case U'ó': case U'ú': case U'ü':
      return true;
    default:
      return false;
  }
}

/// Las tres regiones del algoritmo. Se calculan una vez, sobre la palabra
/// original, y no se recalculan cuando los pasos acortan la palabra: asi lo
/// hace Snowball.
struct Regiones {
  std::size_t rv;
  std::size_t r1;
  std::size_t r2;
};

/// RV depende de como empieza la palabra:
///   - si la segunda letra es consonante, RV empieza despues de la siguiente
///     vocal;
///   - si las dos primeras son vocales, despues de la siguiente consonante;
///   - si es consonante y vocal, despues de la tercera letra.
/// Si la posicion no existe, RV es el final de la palabra.
Regiones marcar_regiones(const Palabra& w) {
  const std::size_t n = w.size();
  std::size_t rv = n;
  if (n >= 2) {
    const auto pasar = [&](bool buscar_vocal) {
      for (std::size_t i = 2; i < n; ++i) {
        if (es_vocal(w[i]) == buscar_vocal) return i + 1;
      }
      return n;
    };
    if (!es_vocal(w[1])) {
      rv = pasar(true);
    } else if (es_vocal(w[0])) {
      rv = pasar(false);
    } else {
      rv = n >= 3 ? 3 : n;
    }
  }
  const std::size_t r1 = snowball::region_tras(w, 0, es_vocal);
  const std::size_t r2 = snowball::region_tras(w, r1, es_vocal);
  return {rv, r1, r2};
}

/// Paso 0: pronombres encliticos ("haciéndola", "dándoselos", "comerlo").
///
/// Se quita el pronombre si va despues de un gerundio o un infinitivo que
/// esta en RV. Si el verbo llevaba la tilde que obliga el pronombre
/// ("haciéndo-la"), se le quita al quitar el pronombre: "haciendo".
void pronombre_enclitico(Palabra& w, const Regiones& r) {
  const std::u32string_view pronombre = mas_largo(
      w, {U"me", U"se", U"sela", U"selo", U"selas", U"selos", U"la", U"le", U"lo", U"las", U"les",
          U"los", U"nos"});
  if (pronombre.empty()) return;

  Palabra verbo = w.substr(0, inicio(w, pronombre));
  const std::u32string_view terminacion =
      mas_largo(verbo, {U"iéndo", U"ándo", U"ár", U"ér", U"ír", U"ando", U"iendo", U"ar", U"er",
                        U"ir", U"yendo"});
  if (terminacion.empty() || inicio(verbo, terminacion) < r.rv) return;

  if (terminacion == U"iéndo" || terminacion == U"ándo" || terminacion == U"ár" ||
      terminacion == U"ér" || terminacion == U"ír") {
    reemplazar(verbo, terminacion, texto::sin_acentos(terminacion));
  } else if (terminacion == U"yendo") {
    // "oyéndolo" no llega aqui (lleva tilde); "construyendolo" si, y solo
    // cuenta si antes del "yendo" hay una u.
    const std::size_t i = inicio(verbo, terminacion);
    if (i == 0 || verbo[i - 1] != U'u') return;
  }
  w = std::move(verbo);
}

/// Paso 1: sufijos derivativos ("-amiento", "-ación", "-mente", "-idad"...).
///
/// Devuelve si quito algo. Si no, se prueban los sufijos verbales (paso 2):
/// una palabra no lleva los dos.
bool sufijo_estandar(Palabra& w, const Regiones& r) {
  const std::u32string_view s = mas_largo(
      w, {U"anza",    U"anzas",    U"ico",     U"ica",      U"icos",   U"icas",    U"ismo",
          U"ismos",   U"able",     U"ables",   U"ible",     U"ibles",  U"ista",    U"istas",
          U"oso",     U"osa",      U"osos",    U"osas",     U"amiento", U"amientos", U"imiento",
          U"imientos", U"adora",   U"ador",    U"ación",    U"adoras", U"adores",  U"aciones",
          U"ante",    U"antes",    U"ancia",   U"ancias",   U"logía",  U"logías",  U"ución",
          U"uciones", U"encia",    U"encias",  U"amente",   U"mente",  U"idad",    U"idades",
          U"iva",     U"ivo",      U"ivas",    U"ivos"});
  if (s.empty()) return false;
  const std::size_t i = inicio(w, s);
  const auto es = [&](std::initializer_list<std::u32string_view> grupo) {
    for (const auto g : grupo) {
      if (s == g) return true;
    }
    return false;
  };
  /// Quita el sufijo opcional `t` si la palabra termina en el y empieza en
  /// R2. Es el `try ([...] R2 delete)` de Snowball.
  const auto quitar_si_en_r2 = [&](std::u32string_view t) {
    if (termina_en(w, t) && inicio(w, t) >= r.r2) quitar(w, t);
  };

  if (es({U"anza", U"anzas", U"ico", U"ica", U"icos", U"icas", U"ismo", U"ismos", U"able",
          U"ables", U"ible", U"ibles", U"ista", U"istas", U"oso", U"osa", U"osos", U"osas",
          U"amiento", U"amientos", U"imiento", U"imientos"})) {
    if (i < r.r2) return false;
    quitar(w, s);
    return true;
  }
  if (es({U"adora", U"ador", U"ación", U"adoras", U"adores", U"aciones", U"ante", U"antes",
          U"ancia", U"ancias"})) {
    if (i < r.r2) return false;
    quitar(w, s);
    quitar_si_en_r2(U"ic");
    return true;
  }
  if (es({U"logía", U"logías"})) {
    if (i < r.r2) return false;
    reemplazar(w, s, U"log");
    return true;
  }
  if (es({U"ución", U"uciones"})) {
    if (i < r.r2) return false;
    reemplazar(w, s, U"u");
    return true;
  }
  if (es({U"encia", U"encias"})) {
    if (i < r.r2) return false;
    reemplazar(w, s, U"ente");
    return true;
  }
  if (s == U"amente") {
    if (i < r.r1) return false;
    quitar(w, s);
    const std::u32string_view t = mas_largo(w, {U"iv", U"os", U"ic", U"ad"});
    if (!t.empty() && inicio(w, t) >= r.r2) {
      quitar(w, t);
      if (t == U"iv") quitar_si_en_r2(U"at");
    }
    return true;
  }
  if (s == U"mente") {
    if (i < r.r2) return false;
    quitar(w, s);
    const std::u32string_view t = mas_largo(w, {U"ante", U"able", U"ible"});
    if (!t.empty() && inicio(w, t) >= r.r2) quitar(w, t);
    return true;
  }
  if (es({U"idad", U"idades"})) {
    if (i < r.r2) return false;
    quitar(w, s);
    const std::u32string_view t = mas_largo(w, {U"abil", U"ic", U"iv"});
    if (!t.empty() && inicio(w, t) >= r.r2) quitar(w, t);
    return true;
  }
  // "iva", "ivo", "ivas", "ivos".
  if (i < r.r2) return false;
  quitar(w, s);
  quitar_si_en_r2(U"at");
  return true;
}

/// Paso 2a: sufijos verbales que empiezan con y ("concluyó", "huyen"). Solo
/// cuentan dentro de RV y despues de una u, que puede estar fuera de RV.
bool sufijo_verbal_y(Palabra& w, const Regiones& r) {
  const std::u32string_view s =
      mas_largo(w,
                {U"ya", U"ye", U"yan", U"yen", U"yeron", U"yendo", U"yo", U"yó", U"yas", U"yes",
                 U"yais", U"yamos"},
                r.rv);
  if (s.empty()) return false;
  const std::size_t i = inicio(w, s);
  if (i == 0 || w[i - 1] != U'u') return false;
  quitar(w, s);
  return true;
}

/// Paso 2b: el resto de los sufijos verbales, solo dentro de RV.
void sufijo_verbal(Palabra& w, const Regiones& r) {
  const std::u32string_view s = mas_largo(
      w,
      {U"en",     U"es",      U"éis",     U"emos",    U"arían",   U"arías",   U"arán",
       U"arás",   U"aríais",  U"aría",    U"aréis",   U"aríamos", U"aremos",  U"ará",
       U"aré",    U"erían",   U"erías",   U"erán",    U"erás",    U"eríais",  U"ería",
       U"eréis",  U"eríamos", U"eremos",  U"erá",     U"eré",     U"irían",   U"irías",
       U"irán",   U"irás",    U"iríais",  U"iría",    U"iréis",   U"iríamos", U"iremos",
       U"irá",    U"iré",     U"aba",     U"ada",     U"ida",     U"ía",      U"ara",
       U"iera",   U"ad",      U"ed",      U"id",      U"ase",     U"iese",    U"aste",
       U"iste",   U"an",      U"aban",    U"ían",     U"aran",    U"ieran",   U"asen",
       U"iesen",  U"aron",    U"ieron",   U"ado",     U"ido",     U"ando",    U"iendo",
       U"ió",     U"ar",      U"er",      U"ir",      U"as",      U"abas",    U"adas",
       U"idas",   U"ías",     U"aras",    U"ieras",   U"ases",    U"ieses",   U"ís",
       U"áis",    U"abais",   U"íais",    U"arais",   U"ierais",  U"aseis",   U"ieseis",
       U"asteis", U"isteis",  U"ados",    U"idos",    U"amos",    U"ábamos",  U"áramos",
       U"iéramos", U"iésemos", U"ásemos", U"imos"},
      r.rv);
  if (s.empty()) return;
  const bool tras_gu = s == U"en" || s == U"es" || s == U"éis" || s == U"emos";
  quitar(w, s);
  // "averigüen" no, pero "persiguen": tras "gu" la u tambien se va, este o no
  // en RV, para que "persiguen" y "persigo" den la misma raiz.
  if (tras_gu && termina_en(w, U"gu")) w.pop_back();
}

/// Paso 3: la vocal final que queda ("-os", "-a", "-o", "-e"...), en RV.
void sufijo_residual(Palabra& w, const Regiones& r) {
  const std::u32string_view s =
      mas_largo(w, {U"os", U"a", U"o", U"á", U"í", U"ó", U"e", U"é"});
  if (s.empty() || inicio(w, s) < r.rv) return;
  quitar(w, s);
  if ((s == U"e" || s == U"é") && termina_en(w, U"gu") && w.size() - 1 >= r.rv) {
    w.pop_back();
  }
}

}  // namespace

std::string stem_spanish(std::string_view word) {
  Palabra w = texto::decodificar(word);
  const Regiones r = marcar_regiones(w);

  pronombre_enclitico(w, r);
  if (!sufijo_estandar(w, r) && !sufijo_verbal_y(w, r)) sufijo_verbal(w, r);
  sufijo_residual(w, r);

  // Postludio: quitar las tildes agudas que queden. La dieresis se queda.
  for (char32_t& c : w) {
    switch (c) {
      case U'á': c = U'a'; break;
      case U'é': c = U'e'; break;
      case U'í': c = U'i'; break;
      case U'ó': c = U'o'; break;
      case U'ú': c = U'u'; break;
      default: break;
    }
  }
  return texto::codificar(w);
}

}  // namespace quipudb
