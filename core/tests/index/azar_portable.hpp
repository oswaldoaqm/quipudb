#pragma once

// Numeros "al azar" que dan lo mismo con cualquier compilador (#204).
//
// `std::mt19937` esta definido por el estandar bit a bit: con la misma semilla
// da la misma secuencia en GCC, Clang y MSVC. Lo que NO esta definido es como
// las distribuciones (`std::uniform_real_distribution`, ...) y `std::shuffle`
// convierten esa secuencia en valores: cada biblioteca estandar lo hace a su
// manera, y puede cambiar entre versiones. Con GCC 15 la misma semilla dio
// otros puntos que con GCC 13 y 14, y una prueba del R-Tree que comparaba
// contra un numero de paginas empezo a fallar.
//
// Estas funciones usan solo la salida cruda de `mt19937` y aritmetica de
// `double`, asi que una prueba que las usa ve los mismos datos en cualquier
// plataforma.

#include <cstddef>
#include <cstdint>
#include <random>
#include <utility>
#include <vector>

namespace quipudb::prueba {

/// Un `double` en [lo, hi), con los 32 bits de una salida del generador.
inline double real_uniforme(std::mt19937& gen, double lo, double hi) {
  const double u = static_cast<double>(gen()) / 4294967296.0;  // 2^32: u en [0, 1)
  return lo + (hi - lo) * u;
}

/// Reemplazo de `std::uniform_real_distribution<double>` con la misma forma de
/// uso, `RealUniforme lon(-77.2, -76.8); lon(gen)`, pero portable.
class RealUniforme {
 public:
  RealUniforme(double lo, double hi) : lo_(lo), hi_(hi) {}
  double operator()(std::mt19937& gen) const { return real_uniforme(gen, lo_, hi_); }

 private:
  double lo_;
  double hi_;
};

/// Un entero en [0, n), sin sesgo: descarta las salidas del ultimo tramo
/// incompleto, como hacen las distribuciones de la biblioteca.
inline std::size_t indice_uniforme(std::mt19937& gen, std::size_t n) {
  const std::uint64_t rango = std::uint64_t{1} << 32;
  const std::uint64_t limite = rango - rango % n;
  std::uint64_t x;
  do {
    x = gen();
  } while (x >= limite);
  return static_cast<std::size_t>(x % n);
}

/// Fisher-Yates con `indice_uniforme`: el mismo orden en cualquier plataforma,
/// a diferencia de `std::shuffle`.
template <typename T>
void barajar(std::vector<T>& v, std::mt19937& gen) {
  for (std::size_t i = v.size(); i > 1; --i) {
    std::swap(v[i - 1], v[indice_uniforme(gen, i)]);
  }
}

}  // namespace quipudb::prueba
