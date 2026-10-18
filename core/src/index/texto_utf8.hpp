#pragma once

// Utilidades de caracteres para el texto del indice invertido (issue #160).
//
// Son internas del core: las comparten el tokenizador y los stemmers, que
// trabajan sobre puntos de codigo y no sobre bytes. Un stemmer que recorriera
// bytes UTF-8 veria "ó" como dos caracteres y contaria mal las regiones R1, R2
// y RV, que se definen por letras.

#include <cstddef>
#include <string>
#include <string_view>

namespace quipudb::texto {

/// Lo que se pone en lugar de una secuencia UTF-8 invalida. El tokenizador lo
/// trata como separador, asi que un byte suelto en Latin-1 corta la palabra en
/// vez de lanzar: un documento con un caracter roto se indexa igual.
inline constexpr char32_t kReemplazo = U'�';

/// Decodifica UTF-8. No lanza: lo invalido -- bytes de continuacion sueltos,
/// secuencias truncadas, formas sobrelargas, sustitutos UTF-16 y puntos mayores
/// que U+10FFFF -- se convierte en `kReemplazo`, un punto por byte descartado.
[[nodiscard]] inline std::u32string decodificar(std::string_view s) {
  std::u32string out;
  out.reserve(s.size());
  std::size_t i = 0;
  while (i < s.size()) {
    const auto b0 = static_cast<unsigned char>(s[i]);
    if (b0 < 0x80) {
      out.push_back(b0);
      ++i;
      continue;
    }
    std::size_t largo = 0;
    char32_t cp = 0;
    char32_t minimo = 0;
    if ((b0 & 0xE0) == 0xC0) {
      largo = 2;
      cp = b0 & 0x1F;
      minimo = 0x80;
    } else if ((b0 & 0xF0) == 0xE0) {
      largo = 3;
      cp = b0 & 0x0F;
      minimo = 0x800;
    } else if ((b0 & 0xF8) == 0xF0) {
      largo = 4;
      cp = b0 & 0x07;
      minimo = 0x10000;
    } else {
      out.push_back(kReemplazo);
      ++i;
      continue;
    }
    bool valido = i + largo <= s.size();
    for (std::size_t k = 1; valido && k < largo; ++k) {
      const auto b = static_cast<unsigned char>(s[i + k]);
      if ((b & 0xC0) != 0x80) {
        valido = false;
      } else {
        cp = (cp << 6) | (b & 0x3F);
      }
    }
    if (!valido || cp < minimo || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) {
      out.push_back(kReemplazo);
      ++i;
      continue;
    }
    out.push_back(cp);
    i += largo;
  }
  return out;
}

[[nodiscard]] inline std::string codificar(std::u32string_view s) {
  std::string out;
  out.reserve(s.size());
  for (const char32_t cp : s) {
    if (cp < 0x80) {
      out.push_back(static_cast<char>(cp));
    } else if (cp < 0x800) {
      out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
      out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else if (cp < 0x10000) {
      out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
      out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
      out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else {
      out.push_back(static_cast<char>(0xF0 | (cp >> 18)));
      out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
      out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
      out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    }
  }
  return out;
}

/// Minuscula de ASCII y de las mayusculas de Latin-1 (U+00C0 a U+00DE, menos
/// el signo de multiplicar). Fuera de eso devuelve el caracter tal cual: el
/// core no lleva tablas Unicode completas, y el corpus del proyecto es espanol
/// e ingles, que caben enteros en Latin-1.
[[nodiscard]] constexpr char32_t minuscula(char32_t c) noexcept {
  if (c >= U'A' && c <= U'Z') return c + 0x20;
  if (c >= 0xC0 && c <= 0xDE && c != 0xD7) return c + 0x20;
  return c;
}

/// La letra sin tilde, dieresis, circunflejo ni cedilla. La enie NO se toca:
/// "ano" y "año" son palabras distintas, y quitarle la virgulilla las
/// confundiria en el indice.
[[nodiscard]] constexpr char32_t sin_acento(char32_t c) noexcept {
  switch (c) {
    case U'à': case U'á': case U'â': case U'ã': case U'ä': case U'å': return U'a';
    case U'ç': return U'c';
    case U'è': case U'é': case U'ê': case U'ë': return U'e';
    case U'ì': case U'í': case U'î': case U'ï': return U'i';
    case U'ò': case U'ó': case U'ô': case U'õ': case U'ö': return U'o';
    case U'ù': case U'ú': case U'û': case U'ü': return U'u';
    case U'ý': case U'ÿ': return U'y';
    default: return c;
  }
}

[[nodiscard]] inline std::u32string sin_acentos(std::u32string_view s) {
  std::u32string out(s);
  for (char32_t& c : out) c = sin_acento(c);
  return out;
}

[[nodiscard]] constexpr bool es_digito(char32_t c) noexcept { return c >= U'0' && c <= U'9'; }

}  // namespace quipudb::texto
