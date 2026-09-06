#pragma once

// Contrato del core: operaciones de tabla e indice (issue #3).
//
// Este archivo es la frontera entre el core en C++ y el resto del sistema.
// Los cinco programamos contra estas dos interfaces:
//
//   TableFile  organizacion fisica de UNA tabla. La implementan heap file,
//              archivo secuencial paginado y B+ agrupado. Guarda registros
//              completos y los devuelve completos.
//
//   Index      estructura secundaria sobre una columna. La implementan B+ no
//              agrupado y extendible hashing. Guarda pares (clave, RID) y
//              devuelve RIDs; el registro se lee despues con
//              `TableFile::read(rid)`.
//
// Por que dos interfaces y no una: un indice agrupado ES la tabla (los
// registros viven en sus hojas), mientras que un indice no agrupado apunta a
// una tabla que vive en otro archivo. Meter ambos bajo la misma firma obliga
// a que `search` devuelva a veces registros y a veces punteros, y eso es
// justo la ambiguedad que este issue debia cerrar.
//
// Decisiones fijadas aqui (ver docs/arquitectura.md para el razonamiento):
//   - Una busqueda sobre la tabla devuelve el registro completo.
//   - Una busqueda sobre un indice devuelve RIDs.
//   - "No encontrado" no es un error: vector vacio / nullopt / 0.
//   - Los errores se reportan con excepciones (quipudb/error.hpp).
//   - `range_search(lo, hi)` es inclusivo en ambos extremos.
//   - La clave primaria es unica: insertar un duplicado lanza DuplicateKey.
//   - Toda operacion actualiza `stats()`, que es lo que el planner usa para
//     armar el plan de ejecucion (issue #4).

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string_view>
#include <utility>
#include <vector>

#include "quipudb/catalog/types.hpp"

namespace quipudb {

// ---------------------------------------------------------------------------
// Estadisticas de acceso
// ---------------------------------------------------------------------------

/// Contadores que cada estructura acumula por operacion. El tiempo NO se mide
/// aqui: lo mide quien llama (el planner), que es quien sabe donde empieza y
/// termina una consulta completa.
struct OpStats {
  std::uint64_t pages_read = 0;
  std::uint64_t pages_written = 0;
  std::uint64_t records_examined = 0;  // registros que se miraron
  std::uint64_t records_returned = 0;  // registros que se devolvieron

  constexpr void reset() noexcept { *this = OpStats{}; }

  constexpr OpStats& operator+=(const OpStats& o) noexcept {
    pages_read += o.pages_read;
    pages_written += o.pages_written;
    records_examined += o.records_examined;
    records_returned += o.records_returned;
    return *this;
  }
};

/// Nombre canonico de cada estructura, tal como aparece en el plan de
/// ejecucion y en las graficas de los benchmarks. Se fija aqui para que
/// planner, frontend y benchmarks usen exactamente las mismas cadenas.
namespace kind {
inline constexpr std::string_view kHeap = "heap";
inline constexpr std::string_view kSequential = "sequential";
inline constexpr std::string_view kBPlusClustered = "bplus_clustered";
inline constexpr std::string_view kBPlusUnclustered = "bplus_unclustered";
inline constexpr std::string_view kExtendibleHash = "extendible_hash";
}  // namespace kind

// ---------------------------------------------------------------------------
// Recorrido incremental
// ---------------------------------------------------------------------------

/// Recorre los registros de una tabla de a uno, sin materializarlos todos.
///
/// `scan()` devuelve el vector completo, y eso no escala: 100 000 registros
/// ocupan unos 4 MB en disco pero 15 MB en memoria, asi que un k-way merge de
/// k runs (el external sorting del #20) necesitaria k veces eso antes de
/// comparar la primera clave. Con un cursor la memoria queda acotada a una
/// pagina (heap file) o a un grupo (secuencial).
///
/// El orden es el mismo que el de `scan()`: de llegada en el heap file, de
/// clave en el secuencial.
///
/// Un cursor deja de valer en cuanto la tabla se modifica. No es un iterador
/// de la STL a proposito: leer del disco puede lanzar, y `operator++` no es
/// buen sitio para eso.
class RecordCursor {
 public:
  virtual ~RecordCursor() = default;

  /// Escribe el siguiente registro en `out` y devuelve true; false cuando ya
  /// no quedan.
  virtual bool next(Record& out) = 0;

  /// Donde estaba el registro que acaba de devolver `next`. Solo vale
  /// inmediatamente despues de un `next` que devolvio true.
  ///
  /// Existe porque construir un indice secundario sobre una tabla que ya
  /// tiene datos necesita recorrerla sabiendo la direccion de cada registro,
  /// y sin esto no habia forma: `insert` devuelve un RID y `read` lo resuelve,
  /// pero nada los enumeraba.
  [[nodiscard]] virtual RID rid() const = 0;
};

// ---------------------------------------------------------------------------
// TableFile: organizacion fisica de una tabla
// ---------------------------------------------------------------------------

class TableFile {
 public:
  virtual ~TableFile() = default;

  /// Esquema con el que se creo el archivo. Inmutable durante su vida.
  [[nodiscard]] virtual const Schema& schema() const noexcept = 0;

  /// Una de las constantes de `kind::`. Es lo que el plan de ejecucion muestra
  /// como "estructura usada".
  [[nodiscard]] virtual std::string_view kind() const noexcept = 0;

  /// Inserta un registro y devuelve donde quedo.
  /// Lanza InvalidRecord si no calza con el esquema y DuplicateKey si la
  /// clave primaria ya existe. En heap file la posicion es estable mientras
  /// el registro viva; en secuencial y B+ agrupado puede cambiar tras una
  /// reorganizacion o un split, asi que ahi el RID no debe persistirse.
  virtual RID insert(const Record& record) = 0;

  /// Elimina el registro con esa clave primaria. Devuelve cuantos se
  /// eliminaron (0 o 1, porque la clave es unica). La estrategia (free list,
  /// marca lazy, merge) es de cada implementacion.
  virtual std::size_t remove(const Key& key) = 0;

  /// Reemplaza el registro que tiene esa clave primaria. Devuelve cuantos se
  /// actualizaron (0 o 1). El registro nuevo tiene que traer la MISMA clave;
  /// cambiarla es `remove` mas `insert`, porque mueve el registro de sitio.
  /// Lanza SchemaError si la clave no coincide.
  ///
  /// Como los registros son de longitud fija, la actualizacion reescribe el
  /// mismo slot: el RID se conserva y los indices secundarios que lo apuntan
  /// siguen valiendo. Por eso es una operacion propia y no dos: un
  /// `remove` mas `insert` deja una ventana donde la fila no existe, cambia
  /// el RID, y en el secuencial puede disparar una reorganizacion.
  virtual std::size_t update(const Key& key, const Record& record) = 0;

  /// Registros cuya clave primaria es igual a `key`. Vacio si no hay.
  /// (Devuelve vector y no optional para que search y range_search tengan la
  /// misma forma y el planner los trate igual.)
  [[nodiscard]] virtual std::vector<Record> search(const Key& key) = 0;

  /// Registros con clave en [lo, hi], ambos inclusive, en orden ascendente de
  /// clave cuando la estructura lo permite (secuencial, B+); en heap file el
  /// orden es el de llegada.
  [[nodiscard]] virtual std::vector<Record> range_search(const Key& lo, const Key& hi) = 0;

  /// Todos los registros vivos, en el orden natural de la estructura.
  /// Materializa todo: para recorridos grandes usar `cursor()`.
  [[nodiscard]] virtual std::vector<Record> scan() = 0;

  /// Recorrido incremental, en el mismo orden que `scan()`.
  [[nodiscard]] virtual std::unique_ptr<RecordCursor> cursor() = 0;

  /// Lee un registro por su direccion fisica. Es la operacion que usan los
  /// indices no agrupados para resolver un RID. nullopt si el slot esta libre
  /// o fuera de rango.
  [[nodiscard]] virtual std::optional<Record> read(RID rid) = 0;

  /// Cantidad de registros vivos (no cuenta los eliminados lazy).
  [[nodiscard]] virtual std::size_t size() const = 0;

  /// Contadores acumulados desde el ultimo `reset_stats()`.
  [[nodiscard]] virtual const OpStats& stats() const noexcept = 0;
  virtual void reset_stats() noexcept = 0;
};

// ---------------------------------------------------------------------------
// Index: estructura secundaria sobre una columna
// ---------------------------------------------------------------------------

class Index {
 public:
  virtual ~Index() = default;

  /// Una de las constantes de `kind::`.
  [[nodiscard]] virtual std::string_view kind() const noexcept = 0;

  /// Tipo de la columna indexada. Toda `Key` que reciba debe ser de este tipo.
  [[nodiscard]] virtual DataType key_type() const noexcept = 0;

  /// true en B+, false en extendible hashing. El planner lo consulta antes de
  /// llamar a `range_search`; si lo ignora, recibe Unsupported.
  [[nodiscard]] virtual bool supports_range() const noexcept = 0;

  /// Registra que `key` vive en `rid`. Un indice secundario admite claves
  /// repetidas (varias filas con la misma edad, por ejemplo), asi que aqui no
  /// hay DuplicateKey: el par (key, rid) simplemente se agrega.
  virtual void insert(const Key& key, RID rid) = 0;

  /// Quita todas las entradas con esa clave. Devuelve cuantas quito.
  virtual std::size_t remove(const Key& key) = 0;


  /// Quita solo la entrada (key, rid). Devuelve si existia.
  virtual bool remove(const Key& key, RID rid) = 0;

  /// RIDs de todas las entradas con clave igual a `key`. Vacio si no hay.
  [[nodiscard]] virtual std::vector<RID> search(const Key& key) = 0;

  /// RIDs con clave en [lo, hi], inclusive, en orden ascendente de clave.
  /// Lanza Unsupported si `supports_range()` es false.
  [[nodiscard]] virtual std::vector<RID> range_search(const Key& lo, const Key& hi) = 0;

  /// Todas las entradas. En B+ salen ordenadas por clave (es lo que usa
  /// ORDER BY cuando hay indice); en hash salen en orden de bucket.
  [[nodiscard]] virtual std::vector<std::pair<Key, RID>> scan() = 0;

  /// Cantidad de entradas (key, rid) vivas.
  [[nodiscard]] virtual std::size_t size() const = 0;

  [[nodiscard]] virtual const OpStats& stats() const noexcept = 0;
  virtual void reset_stats() noexcept = 0;
};

}  // namespace quipudb
