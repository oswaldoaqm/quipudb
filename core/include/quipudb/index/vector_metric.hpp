#pragma once

// Metricas de similitud entre vectores (issue #179): euclidiana, producto
// punto y coseno.
//
// Que es aqui
// -----------
//
//   Las tres formas de comparar dos vectores que pide el 2.4.2, como una pieza
//   que comparten la busqueda exacta (#182), IVF (#183, #184) y HNSW (#185,
//   #186). Es el equivalente de `distance.hpp` (#119) en la Parte 2: no toca
//   disco ni conoce ningun indice, recibe dos vectores y devuelve un numero.
//
//   Vive en el core y no en `engine/multimedia/` porque los indices que la
//   usan guardan sus vectores en paginas, como el resto del motor, y la
//   comparacion esta en su bucle mas interno: una busqueda exacta sobre
//   100 000 vectores la llama 100 000 veces. El K-Means de `kmeans.py` sigue
//   en Python con su propia euclidiana en numpy (ADR 0007); los centroides que
//   produce llegan al core ya calculados.
//
// Las tres NO son intercambiables
// -------------------------------
//
//   euclidiana      sqrt(sum (a_i - b_i)^2)    distancia: menor es mejor.
//                   Mide la diferencia de cada componente, asi que dos
//                   histogramas con la misma forma pero distinto tamano
//                   (una foto grande y la misma foto chica, con mas
//                   descriptores) quedan lejos.
//
//   producto punto  sum a_i * b_i               similitud: mayor es mejor.
//                   Crece con la norma de los vectores. Por si solo NO es una
//                   distancia: un vector puede tener mas producto punto con
//                   otro que consigo mismo. Con a = (1, 0) y b = (10, 0),
//                   a.b = 10 y a.a = 1, asi que la consulta `a` encontraria
//                   primero a `b` y no a si misma.
//
//   coseno          a.b / (|a| |b|)             similitud en [-1, 1].
//                   Solo mira la DIRECCION, no la norma: a y 3a dan 1. Es la
//                   que corresponde a histogramas de palabras, donde importa
//                   la proporcion de cada palabra y no cuantos descriptores
//                   salieron.
//
//   Cuando usar cada una:
//
//     - Histogramas de palabras, como los que produce ExtractFeatures (#181)
//       a partir de SIFT o MFCC -> coseno. Importa en que proporcion aparece
//       cada palabra, no cuantos descriptores salieron del objeto.
//     - Vectores que ya vienen normalizados -> producto punto. Ordena igual
//       que el coseno y se ahorra calcular las dos normas en cada comparacion.
//     - Vectores donde la magnitud significa algo (mediciones en las mismas
//       unidades) -> euclidiana.
//
//   Con vectores normalizados (norma 1) las tres ordenan igual:
//
//     a.b = cos(a, b)        y        |a - b|^2 = 2 - 2 cos(a, b)
//
//   asi que la euclidiana crece exactamente cuando el coseno baja. Es el
//   material que el informe necesita para la comparacion del #192.
//
// La convencion para ordenar, en un solo lugar
// --------------------------------------------
//
//   Dos de las tres son similitudes (mayor es mejor) y una es distancia (menor
//   es mejor). Si cada indice resolviera eso por su cuenta, tarde o temprano
//   uno ordenaria al reves. Por eso `vector_distance` las lleva todas a la
//   misma escala, donde MENOR SIEMPRE ES MEJOR:
//
//     euclidiana      d                       [0, inf)
//     producto punto  -(a.b)                  (-inf, inf)
//     coseno          1 - cos(a, b)           [0, 2]
//
//   Los indices comparan y ordenan solo con `vector_distance`. Para mostrarle
//   al usuario el valor natural de la metrica (el SIMILARITY_SCORE del #189)
//   esta `from_distance`, que deshace la transformacion.
//
// Casos limite
// ------------
//
//   - Vectores de distinta dimension, vacios o con un componente NaN o
//     infinito: InvalidRecord. Un NaN haria falsa cualquier comparacion
//     posterior y el vector quedaria en una posicion arbitraria del ranking
//     sin que nada lo denuncie.
//   - Coseno con un vector de norma cero: la similitud vale 0 (distancia 1),
//     ni parecido ni opuesto. La division daria 0/0 = NaN. Se eligio un valor
//     y no un error porque un solo vector vacio no debe tumbar una consulta
//     k-NN sobre toda la tabla.
//   - El coseno se acota a [-1, 1]: el redondeo puede dar 1.0000000000000002
//     para dos vectores paralelos, y entonces 1 - cos saldria negativo.
//
// Precision
// ---------
//
//   Los vectores son float (lo que devuelven SIFT, MFCC y el histograma), pero
//   todo se acumula en double. Con 128 o 256 componentes, sumar en float
//   pierde los ultimos digitos y dos vecinos casi empatados podrian cambiar de
//   orden segun en que orden se sumen. En double ademas no hay desborde:
//   el mayor float al cuadrado es ~1e77, lejos del limite del double.

#include <cstdint>
#include <span>

namespace quipudb {

/// Con que se comparan dos vectores. Se elige por consulta, no al compilar.
enum class VectorMetric : std::uint8_t {
  /// Distancia euclidiana: menor es mejor.
  kEuclidean,
  /// Producto punto: mayor es mejor. Depende de la norma.
  kDotProduct,
  /// Similitud del coseno: mayor es mejor. Solo mira la direccion.
  kCosine,
};

/// El nombre con el que la metrica viaja en el SQL y aparece en el plan.
[[nodiscard]] const char* name_of(VectorMetric metric) noexcept;

/// Si el valor natural de la metrica es una similitud (mayor es mejor).
[[nodiscard]] bool higher_is_better(VectorMetric metric) noexcept;

/// Distancia euclidiana. Lanza InvalidRecord si las dimensiones no coinciden,
/// si estan vacios o si algun componente no es finito.
[[nodiscard]] double euclidean_distance(std::span<const float> a, std::span<const float> b);

/// Producto punto, con las mismas validaciones.
[[nodiscard]] double dot_product(std::span<const float> a, std::span<const float> b);

/// Similitud del coseno en [-1, 1]. Si alguno tiene norma cero, vale 0.
[[nodiscard]] double cosine_similarity(std::span<const float> a, std::span<const float> b);

/// El valor natural de la metrica: la distancia euclidiana, el producto punto
/// o el coseno, tal como se le muestran al usuario.
[[nodiscard]] double vector_similarity(std::span<const float> a, std::span<const float> b,
                                       VectorMetric metric);

/// La metrica llevada a una escala donde MENOR ES MEJOR, la unica con la que
/// los indices ordenan. Ver la tabla de la cabecera.
[[nodiscard]] double vector_distance(std::span<const float> a, std::span<const float> b,
                                     VectorMetric metric);

/// Deshace `vector_distance`: de la escala comun al valor natural.
[[nodiscard]] double from_distance(double distance, VectorMetric metric) noexcept;

}  // namespace quipudb
