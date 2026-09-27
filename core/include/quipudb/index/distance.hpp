#pragma once

// Metricas de distancia (issue #119): euclidiana y geodesica.
//
// Que es aqui
// -----------
//
//   Las dos formas de medir la distancia entre dos `Point` del R-Tree. Van en
//   su propio archivo porque las consumen las tres consultas espaciales de
//   2.2.1.B -- radio, k-NN y poligono -- y tambien los benchmarks del 2.2.4,
//   que comparan las dos metricas entre si.
//
//   No tocan disco ni conocen el arbol: reciben dos puntos y devuelven un
//   numero. Por eso se prueban solas.
//
// Las dos NO son intercambiables
// ------------------------------
//
//   `euclidean` mide en el plano y devuelve el resultado en las MISMAS
//   unidades de las coordenadas. Sobre coordenadas geograficas eso son
//   grados, no metros, y un grado no mide lo mismo en todas partes: un grado
//   de longitud son 111 km en el ecuador, 109 km en Lima y 56 km a latitud
//   60. Tratar los grados como si fueran una distancia estira las medidas
//   este-oeste cerca del ecuador y las encoge lejos de el.
//
//   `haversine` mide sobre la esfera y devuelve METROS, asi que es la que
//   corresponde cuando los puntos son coordenadas geograficas y el resultado
//   se va a comparar con una distancia del mundo real ("a menos de 5 km").
//
//   Cuando usar cada una:
//
//     - Los puntos son coordenadas geograficas y el radio viene en metros
//       -> `haversine`.
//     - Los puntos son un plano cualquiera (pixeles, un plano proyectado, un
//       espacio de caracteristicas) -> `euclidean`.
//     - Solo hace falta ORDENAR por cercania dentro de una zona pequena y no
//       reportar la distancia -> las dos dan casi el mismo orden y la
//       euclidiana es mas barata; con zonas grandes o muy al norte o al sur,
//       ya no.
//
// Que tan exacta es la geodesica
// ------------------------------
//
//   Haversine supone que la Tierra es una esfera, y no lo es: esta achatada
//   en los polos. Frente a la geodesica exacta sobre el elipsoide WGS84, el
//   error medido sobre los pares de ciudades de `distance_test.cpp` llega al
//   0,54 % (Lima-Quito). Es el error del MODELO, no de la implementacion, y
//   por eso las pruebas separan las dos cosas: contra valores de Haversine
//   calculados aparte la tolerancia es de 1e-9 relativo, y contra distancias
//   reales es del 0,6 %.
//
//   Se acepta ese error a cambio de una formula corta, estable y sin
//   iteraciones, que es lo que pide el 2.2.1. Si algun dia hiciera falta mas
//   precision, el reemplazo es Vincenty o Karney, que iteran.

#include <cstdint>

#include "quipudb/index/rtree.hpp"

namespace quipudb {

/// Radio medio de la Tierra en metros: el R1 de la IUGG, (2a + b) / 3 sobre
/// WGS84. Es el radio que menos error deja en promedio para una esfera, y el
/// que usan las distancias de referencia de las pruebas.
inline constexpr double kEarthRadiusMeters = 6'371'008.8;

/// Con que se mide. Se elige por consulta y no en tiempo de compilacion:
/// viaja en el plan hasta el operador, que llama a `distance` (criterio 3 del
/// issue #119).
enum class Metric : std::uint8_t {
  /// Distancia en el plano, en las unidades de las coordenadas.
  kEuclidean,
  /// Distancia sobre la esfera, en metros.
  kHaversine,
};

/// El nombre con el que la metrica viaja en el SQL y aparece en el plan.
[[nodiscard]] const char* name_of(Metric metric) noexcept;

/// Distancia euclidiana entre dos puntos del plano.
///
/// El resultado va en las unidades de las coordenadas: si son grados, son
/// grados. Coordenadas no finitas son InvalidRecord, igual que en el R-Tree:
/// un NaN haria falsa cualquier comparacion posterior sin que nada lo
/// denuncie.
///
/// Usa `std::hypot` y no la raiz de la suma de cuadrados: con coordenadas muy
/// grandes, elevar al cuadrado desborda el double antes de llegar a la raiz.
[[nodiscard]] double euclidean(Point a, Point b);

/// Distancia sobre la superficie de la esfera, en metros.
///
/// `x` es la longitud e `y` la latitud, en grados, como en todo el R-Tree.
/// La longitud no necesita estar normalizada: la formula usa el seno de la
/// mitad de la diferencia, que es periodico, asi que cruzar el antimeridiano
/// sale bien sin sumar ni restar 360.
///
/// Es InvalidRecord una coordenada no finita, o una latitud fuera de
/// [-90, 90]: mas alla el coseno cambia de signo y la formula devolveria un
/// numero sin significado en vez de avisar.
[[nodiscard]] double haversine(Point a, Point b);

/// La metrica elegida, resuelta en tiempo de ejecucion.
[[nodiscard]] double distance(Point a, Point b, Metric metric);

/// El rectangulo mas chico que contiene el circulo de radio `radius` alrededor
/// de `center`, en las coordenadas del indice (issue #120).
///
/// Es la pieza que traduce entre las dos unidades: el radio viene en la unidad
/// de la metrica -- metros con `kHaversine` -- y los MBR del R-Tree estan en
/// grados. Sin esta conversion no se pueden comparar.
///
/// Con `kEuclidean` es el cuadrado de lado 2r y no hay nada que convertir.
///
/// Con `kHaversine` la altura es constante -- un grado de latitud mide igual
/// en todas partes -- pero el ancho NO: cuanto mas lejos del ecuador, mas
/// grados de longitud abarca el mismo circulo. El semiancho exacto es
/// asin(sen d / cos lat), con d el radio angular; la aproximacion d / cos lat
/// se queda corta y perderia puntos del borde.
///
/// Dos casos degeneran al rango entero de longitud, [-180, 180]:
///   - el circulo toca un polo, donde todas las longitudes se juntan;
///   - el circulo cruza el antimeridiano, porque un `Rect` no sabe envolverse.
/// En los dos el rectangulo sigue siendo correcto -- contiene el circulo --,
/// solo poda menos.
///
/// El rectangulo se ensancha un pelo (1e-12 relativo) a proposito: su trabajo
/// es PODAR, nunca decidir. Si el redondeo lo dejara un ulp corto, un punto
/// que cae justo en el borde del circulo se perderia antes de que el filtro
/// exacto llegara a mirarlo.
///
/// Un radio negativo devuelve un rectangulo invertido, que no contiene nada,
/// igual que un BETWEEN con los limites al reves. Coordenadas o radio no
/// finitos son InvalidRecord.
[[nodiscard]] Rect bounding_box(Point center, double radius, Metric metric);

/// Lo mas cerca que `p` puede estar de un punto de `region` (issue #121).
///
/// Es la cota con la que el k-NN decide por que rama bajar y cuando parar:
/// un subarbol cuyo MBR esta a mas distancia que el k-esimo vecino ya
/// encontrado no puede mejorarlo, y se descarta sin leerlo.
///
/// Por eso NO puede quedarse por encima del minimo real: si lo hiciera,
/// podaria un subarbol que si tenia un vecino mas cercano y el resultado
/// saldria mal. Quedarse por debajo solo cuesta leer paginas de mas. Esta
/// devuelve el minimo exacto, que es lo que mas poda.
///
/// Con `p` dentro de `region` es 0. Con una region invertida es infinito:
/// no contiene ningun punto, asi que nada puede estar cerca de ella.
///
/// Con `kEuclidean` el punto mas cercano se obtiene recortando cada
/// coordenada al rango del rectangulo, y eso es exacto en el plano.
///
/// Con `kHaversine` no alcanza con recortar: sobre la esfera, el punto mas
/// cercano de un borde meridiano no esta en general a la latitud recortada,
/// porque un paralelo no es un circulo maximo. Se resuelve con la latitud del
/// pie de la perpendicular al meridiano, atan2(sen lat, cos lat cos dlon),
/// acotada al rango del rectangulo. Los bordes en paralelo no hace falta
/// mirarlos: sobre un paralelo la distancia crece con |dlon|, asi que su
/// minimo cae siempre en una esquina, que ya es parte de un meridiano.
[[nodiscard]] double min_distance(Point p, const Rect& region, Metric metric);

}  // namespace quipudb
