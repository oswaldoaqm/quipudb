#pragma once

// Errores del core (issue #3).
//
// Decision: el core reporta errores con excepciones, no con codigos de
// retorno. Razones:
//   1. pybind11 traduce las excepciones a Python automaticamente; un codigo de
//      retorno se pierde o se ignora en la capa Python.
//   2. "No encontrado" no es un error: `search` devuelve un vector vacio y
//      `read` devuelve nullopt. Las excepciones quedan para lo que rompe el
//      contrato (clave duplicada, registro que no calza, disco que falla).
//   3. Todas heredan de `quipudb::Error`, asi que quien no quiera distinguir
//      captura una sola.

#include <stdexcept>
#include <string>

namespace quipudb {

/// Raiz de todos los errores del core.
struct Error : std::runtime_error {
  using std::runtime_error::runtime_error;
};

/// Fallo al leer o escribir un archivo de paginas.
struct IoError : Error {
  using Error::Error;
};

/// El esquema no permite la operacion: tipos incompatibles, columna
/// inexistente, tabla que no existe.
struct SchemaError : Error {
  using Error::Error;
};

/// El registro no calza con el esquema de la tabla.
struct InvalidRecord : Error {
  using Error::Error;
};

/// Se intento insertar una clave primaria que ya existe en la tabla.
struct DuplicateKey : Error {
  using Error::Error;
};

/// La estructura no soporta la operacion (por ejemplo, `range_search` sobre
/// extendible hashing). El planner debe consultar `supports_range()` antes.
struct Unsupported : Error {
  using Error::Error;
};

}  // namespace quipudb
