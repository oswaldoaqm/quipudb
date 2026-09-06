#pragma once

// Apertura de tablas desde el catalogo (issue #56).
//
// El catalogo (#7) sabe que tablas hay, con que esquema, con que organizacion
// y en que archivo, pero no abre nada: hasta ahora cada consumidor -- planner,
// parser, transacciones, benchmarks, bindings -- tenia que escribir su propio
// `if (storage == "heap") new HeapFile(...) else if (...)`. Son cinco copias
// del mismo switch, y cada organizacion nueva obliga a tocar las cinco.
//
// `Database` junta el catalogo con los archivos abiertos:
//
//   Database db("datos/catalogo.txt");
//   db.create_table(esquema, kind::kHeap);
//   TableFile& t = db.table("alumnos");
//   t.insert(...);
//
// Devuelve SIEMPRE el mismo objeto para una tabla dada, y eso no es un
// detalle: dos handles abiertos sobre el mismo archivo tienen cada uno su
// copia de los contadores y del estado en memoria, se pisan al escribir y
// dejan el archivo con contadores que no cuadran. La unica forma segura de
// trabajar con una tabla es que haya un solo objeto por archivo, y esta clase
// es la que lo garantiza.
//
// No es segura para hilos: eso lo resuelve el lock manager de 2.1.4, que se
// apoyara en esta clase para saber sobre que objeto sincronizar.

#include <filesystem>
#include <map>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

#include "quipudb/catalog/catalog.hpp"
#include "quipudb/catalog/table.hpp"

namespace quipudb {

class Database {
 public:
  /// Abre (o crea) el catalogo en esa ruta. Los archivos de datos viven en su
  /// mismo directorio.
  explicit Database(std::filesystem::path catalog_path);

  [[nodiscard]] Catalog& catalog() noexcept { return catalog_; }
  [[nodiscard]] const Catalog& catalog() const noexcept { return catalog_; }

  /// Registra la tabla en el catalogo, crea su archivo y devuelve el handle.
  /// Lanza SchemaError si el esquema o la organizacion no valen, o si la
  /// tabla ya existe.
  TableFile& create_table(const Schema& schema, std::string_view storage,
                          std::size_t page_size = kDefaultPageSize);

  /// Handle de una tabla ya registrada, abriendola la primera vez. Siempre el
  /// mismo objeto. Lanza SchemaError si la tabla no esta en el catalogo.
  TableFile& table(std::string_view name);

  /// Si esa tabla esta abierta, la cierra (vaciando lo pendiente). Los
  /// handles que alguien haya guardado dejan de valer.
  void close(std::string_view name);

  /// Quita la tabla del catalogo, cierra su handle y borra su archivo.
  void drop_table(std::string_view name);

  /// Vacia a disco todas las tablas abiertas.
  void flush();

  [[nodiscard]] bool is_open(std::string_view name) const noexcept;
  [[nodiscard]] std::vector<std::string> open_tables() const;

 private:
  [[nodiscard]] std::unique_ptr<TableFile> abrir(const TableInfo& info) const;

  Catalog catalog_;
  std::map<std::string, std::unique_ptr<TableFile>, std::less<>> abiertas_;
};

}  // namespace quipudb
