#pragma once

// Tipos basicos del contrato del core (issue #3).
//
// Todo lo que cruza la frontera entre el core y el resto del sistema se
// expresa con los tipos de este archivo: identificadores de pagina y registro,
// tipos de dato de una columna, el valor de una celda y el esquema de una
// tabla. Las estructuras de disco (paginas, buckets, nodos) NO aparecen aqui:
// son detalle de implementacion de cada modulo.

#include <compare>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <optional>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

#include "quipudb/error.hpp"

namespace quipudb {

// ---------------------------------------------------------------------------
// Direccionamiento en disco
// ---------------------------------------------------------------------------

/// Tamano de pagina por defecto. El tamano real lo fija cada archivo al
/// crearse (`DiskManager`) y queda grabado en su cabecera, asi que se puede
/// cambiar para un benchmark sin tocar el resto del codigo. La pagina es la
/// unidad que cuentan las estadisticas de acceso.
inline constexpr std::size_t kDefaultPageSize = 4096;

/// Numero de pagina dentro de un archivo. Es un indice, no un offset: el
/// offset en bytes es `page * page_size`.
using PageId = std::uint32_t;

/// Posicion de un registro dentro de su pagina.
using SlotId = std::uint16_t;

inline constexpr PageId kInvalidPage = std::numeric_limits<PageId>::max();

/// Record ID: direccion fisica de un registro. Es lo que guardan los indices
/// no agrupados y lo que devuelve `TableFile::insert`.
struct RID {
  PageId page = kInvalidPage;
  SlotId slot = 0;

  [[nodiscard]] constexpr bool valid() const noexcept { return page != kInvalidPage; }

  friend constexpr auto operator<=>(const RID&, const RID&) = default;
};

// ---------------------------------------------------------------------------
// Tipos de dato y valores
// ---------------------------------------------------------------------------

/// Tipos de columna soportados por el motor. Todos tienen tamano fijo en disco
/// (VARCHAR se rellena hasta `Column::length`), lo que permite registros de
/// longitud fija y slots calculables sin directorio de offsets.
enum class DataType : std::uint8_t {
  Int,      // int32, 4 bytes
  Double,   // IEEE 754, 8 bytes
  Varchar,  // texto de hasta `length` bytes, relleno con '\0'
  Bool,     // 1 byte
  Date      // dias desde 1970-01-01, int32, 4 bytes
};

[[nodiscard]] constexpr std::string_view to_string(DataType t) noexcept {
  switch (t) {
    case DataType::Int: return "INT";
    case DataType::Double: return "DOUBLE";
    case DataType::Varchar: return "VARCHAR";
    case DataType::Bool: return "BOOL";
    case DataType::Date: return "DATE";
  }
  return "?";
}

/// Fecha como dias desde 1970-01-01. Es un tipo propio (y no un int32 a secas)
/// para que un `Value` sepa que es una fecha sin consultar el esquema.
struct Date {
  std::int32_t days = 0;
  friend constexpr auto operator<=>(const Date&, const Date&) = default;
};

/// Valor de una celda. El indice del variant coincide con `DataType`, y
/// `type_of` lo hace explicito.
using Value = std::variant<std::int32_t, double, std::string, bool, Date>;

/// Clave de busqueda. Es un Value cualquiera; el esquema dice de que columna.
using Key = Value;

/// Registro logico: un Value por columna, en el orden del esquema. La
/// serializacion a bytes es responsabilidad del catalogo (issue #7), no de
/// quien consume el contrato.
using Record = std::vector<Value>;

[[nodiscard]] constexpr DataType type_of(const Value& v) noexcept {
  return static_cast<DataType>(v.index());
}

/// Orden total entre dos valores del mismo tipo: <0, 0 o >0.
/// Comparar tipos distintos es un error de esquema, no un resultado.
[[nodiscard]] inline int compare(const Value& a, const Value& b) {
  if (a.index() != b.index()) {
    throw SchemaError("no se pueden comparar " + std::string(to_string(type_of(a))) +
                      " con " + std::string(to_string(type_of(b))));
  }
  return std::visit(
      [&b](const auto& lhs) -> int {
        const auto& rhs = std::get<std::decay_t<decltype(lhs)>>(b);
        if (lhs < rhs) return -1;
        if (rhs < lhs) return 1;
        return 0;
      },
      a);
}

/// Orden estricto entre claves, para usar Key como clave de map o set.
/// Vive aqui (y no como lambda en cada modulo) para que el tipo del contenedor
/// tenga enlace externo: una lambda hace que la clase que la contiene quede con
/// enlace interno y GCC avisa con -Wsubobject-linkage.
struct KeyLess {
  bool operator()(const Value& a, const Value& b) const { return compare(a, b) < 0; }
};

// ---------------------------------------------------------------------------
// Esquema
// ---------------------------------------------------------------------------

struct Column {
  std::string name;
  DataType type = DataType::Int;
  /// Solo para Varchar: capacidad maxima en bytes. Ignorado en los demas tipos.
  std::uint16_t length = 0;

  /// Bytes que ocupa la columna dentro de un registro serializado.
  [[nodiscard]] constexpr std::size_t byte_size() const noexcept {
    switch (type) {
      case DataType::Int: return 4;
      case DataType::Double: return 8;
      case DataType::Varchar: return length;
      case DataType::Bool: return 1;
      case DataType::Date: return 4;
    }
    return 0;
  }
};

/// Definicion de una tabla. `key_column` es la columna por la que ordenan el
/// archivo secuencial y el B+ agrupado, y la que `TableFile::search` recibe.
struct Schema {
  std::string table_name;
  std::vector<Column> columns;
  std::size_t key_column = 0;

  [[nodiscard]] const Column& key() const { return columns.at(key_column); }

  /// Tamano fijo de un registro serializado, sin cabecera de slot.
  [[nodiscard]] std::size_t record_size() const noexcept {
    std::size_t total = 0;
    for (const auto& c : columns) total += c.byte_size();
    return total;
  }

  /// Posicion de una columna por nombre, o nullopt si no existe.
  [[nodiscard]] std::optional<std::size_t> find(std::string_view name) const noexcept {
    for (std::size_t i = 0; i < columns.size(); ++i) {
      if (columns[i].name == name) return i;
    }
    return std::nullopt;
  }

  /// Verifica que un registro logico calza con el esquema: misma cantidad de
  /// columnas, mismo tipo en cada una y VARCHAR dentro de su capacidad.
  void validate(const Record& r) const {
    if (r.size() != columns.size()) {
      throw InvalidRecord("el registro tiene " + std::to_string(r.size()) +
                          " columnas y el esquema de " + table_name + " tiene " +
                          std::to_string(columns.size()));
    }
    for (std::size_t i = 0; i < columns.size(); ++i) {
      if (type_of(r[i]) != columns[i].type) {
        throw InvalidRecord("columna " + columns[i].name + ": se esperaba " +
                            std::string(to_string(columns[i].type)) + " y llego " +
                            std::string(to_string(type_of(r[i]))));
      }
      if (columns[i].type == DataType::Varchar &&
          std::get<std::string>(r[i]).size() > columns[i].length) {
        throw InvalidRecord("columna " + columns[i].name + ": el texto supera VARCHAR(" +
                            std::to_string(columns[i].length) + ")");
      }
    }
  }

  /// Extrae la clave primaria de un registro ya validado.
  [[nodiscard]] const Key& key_of(const Record& r) const { return r.at(key_column); }
};

}  // namespace quipudb
