#pragma once

// Nombres de los archivos temporales que comparten los tres algoritmos
// externos. Es una cabecera privada de `core/src/external/`: no se instala ni
// se expone en `core/include/`, porque nadie fuera de estos tres la necesita.

#include <atomic>
#include <cstdint>
#include <random>
#include <string>

namespace quipudb::detail {

/// Etiqueta unica para nombrar un archivo temporal.
///
/// La direccion del objeto NO sirve, aunque sea lo que parece mas a mano: el
/// asignador la reutiliza en cuanto se libera una instancia, asi que dos
/// operadores consecutivos generaban nombres identicos. Si el archivo del
/// primero sobrevivio -- un borrado que fallo, un proceso que murio a medias --
/// el segundo lo abre y lee datos ajenos. Eso fue el #98: filas fantasma, sin
/// ningun error por el camino.
///
/// El contador por proceso no se reutiliza nunca. La sal aleatoria anade que
/// dos procesos distintos que compartan el directorio temporal tampoco
/// colisionen, cosa que un contador solo no evita porque los dos arrancan en
/// cero.
///
/// Vive en un solo sitio a proposito. Copiada en los tres archivos, bastaria
/// con que alguien tocara una para que las otras dos dejaran de coincidir sin
/// que nada lo denunciara.
inline std::string etiqueta_unica() {
  static const std::uint64_t sal = []() noexcept {
    std::random_device rd;
    return (static_cast<std::uint64_t>(rd()) << 32) ^ rd();
  }();
  static std::atomic<std::uint64_t> contador{0};
  return std::to_string(sal) + "_" +
         std::to_string(contador.fetch_add(1, std::memory_order_relaxed));
}

}  // namespace quipudb::detail
