#pragma once

// Piezas comunes a los stemmers de Snowball (issue #160).
//
// Snowball describe sus algoritmos con "busca el sufijo mas largo de esta
// lista y, si esta en tal region, haz tal cosa". Estas funciones son esa
// busqueda. Trabajan sobre puntos de codigo para que las regiones cuenten
// letras y no bytes.

#include <cstddef>
#include <initializer_list>
#include <string>
#include <string_view>

namespace quipudb::snowball {

using Palabra = std::u32string;

[[nodiscard]] inline bool termina_en(const Palabra& w, std::u32string_view sufijo) noexcept {
  return w.size() >= sufijo.size() &&
         std::u32string_view(w).substr(w.size() - sufijo.size()) == sufijo;
}

/// El sufijo mas largo de `lista` en que termina `w`, o vacio si ninguno.
///
/// `desde` restringe la busqueda a los sufijos que empiezan en esa posicion o
/// despues: es el `setlimit` de Snowball, que usa el espanol para buscar los
/// sufijos verbales solo dentro de RV.
///
/// Ojo con el orden de las operaciones, que Snowball respeta y estas
/// funciones tambien: primero se elige el sufijo MAS LARGO y despues se mira
/// la condicion de region. Si la condicion falla, el paso no hace nada; no se
/// prueba con un sufijo mas corto.
[[nodiscard]] inline std::u32string_view mas_largo(const Palabra& w,
                                                   std::initializer_list<std::u32string_view> lista,
                                                   std::size_t desde = 0) noexcept {
  std::u32string_view mejor;
  for (const std::u32string_view s : lista) {
    if (s.size() > mejor.size() && termina_en(w, s) && w.size() - s.size() >= desde) mejor = s;
  }
  return mejor;
}

/// Donde empieza el sufijo `s` en `w` (que termina en el).
[[nodiscard]] inline std::size_t inicio(const Palabra& w, std::u32string_view s) noexcept {
  return w.size() - s.size();
}

/// Cambia el sufijo `s` de `w` por `por`.
inline void reemplazar(Palabra& w, std::u32string_view s, std::u32string_view por) {
  w.resize(w.size() - s.size());
  w += por;
}

inline void quitar(Palabra& w, std::u32string_view s) { w.resize(w.size() - s.size()); }

/// La region que empieza despues de la primera no vocal que sigue a una
/// vocal, buscando desde `desde`. Es la definicion de R1 (desde 0) y de R2
/// (desde R1) en todos los algoritmos de Snowball. Si no existe, es el final
/// de la palabra: la region vacia.
template <typename EsVocal>
[[nodiscard]] std::size_t region_tras(const Palabra& w, std::size_t desde, EsVocal es_vocal) {
  std::size_t i = desde;
  while (i < w.size() && !es_vocal(w[i])) ++i;
  if (i >= w.size()) return w.size();
  ++i;
  while (i < w.size() && es_vocal(w[i])) ++i;
  if (i >= w.size()) return w.size();
  return i + 1;
}

}  // namespace quipudb::snowball
