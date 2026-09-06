#pragma once

// Serializacion de registros de longitud fija (issue #7).
//
// Un RecordCodec esta atado a un Schema y convierte un Record logico
// (vector<Value>) en exactamente `schema.record_size()` bytes, y de vuelta.
// El layout es la concatenacion de las columnas en el orden del esquema, cada
// una con el tamano fijo de `Column::byte_size()`:
//
//   INT      4 bytes   int32 en el orden de bytes de la maquina
//   DOUBLE   8 bytes   IEEE 754
//   VARCHAR  n bytes   texto seguido de '\0' hasta completar n
//   BOOL     1 byte    0 o 1
//   DATE     4 bytes   int32, dias desde 1970-01-01
//
// No hay cabecera ni separadores: el esquema es el que sabe donde empieza
// cada columna. Eso es lo que hace posible que un slot de pagina sea
// `record_size()` bytes exactos y se pueda direccionar por indice (#8, #10).

#include <cstddef>
#include <span>
#include <vector>

#include "quipudb/catalog/types.hpp"

namespace quipudb {

class RecordCodec {
 public:
  explicit RecordCodec(Schema schema);

  [[nodiscard]] const Schema& schema() const noexcept { return schema_; }

  /// Bytes que ocupa un registro serializado.
  [[nodiscard]] std::size_t size() const noexcept { return size_; }

  /// Valida el registro contra el esquema y lo escribe en `out`, que debe
  /// tener al menos `size()` bytes. Lanza InvalidRecord si no calza.
  void encode(const Record& record, std::span<std::byte> out) const;

  /// Version comoda que devuelve el buffer.
  [[nodiscard]] std::vector<std::byte> encode(const Record& record) const;

  /// Reconstruye el registro desde `in`, que debe tener al menos `size()`
  /// bytes. Lanza InvalidRecord si es mas corto.
  [[nodiscard]] Record decode(std::span<const std::byte> in) const;

  /// Offset en bytes de la columna `i` dentro del registro.
  [[nodiscard]] std::size_t offset_of(std::size_t column) const { return offsets_.at(column); }

  /// Lee solo una columna del registro serializado, sin decodificar el resto.
  /// Es lo que usa un indice para sacar la clave sin materializar la fila.
  [[nodiscard]] Value decode_column(std::span<const std::byte> in, std::size_t column) const;

  // --- una columna suelta ---------------------------------------------------
  // Un indice guarda claves sueltas (no registros), asi que necesita
  // serializar un Value solo. Mismo layout que dentro de un registro.

  static void encode_value(const Column& col, const Value& v, std::span<std::byte> out);
  [[nodiscard]] static Value decode_value(const Column& col, std::span<const std::byte> in);

 private:
  Schema schema_;
  std::vector<std::size_t> offsets_;
  std::size_t size_ = 0;
};

}  // namespace quipudb
