#pragma once

// Catalogo de esquemas (issue #7).
//
// El catalogo es la lista de tablas de una base: para cada una, su Schema, con
// que estructura se almacena (`kind::kHeap`, `kSequential`, `kBPlusClustered`),
// en que archivo, y que indices secundarios tiene. Es lo que permite que
// CREATE TABLE cree cualquier tabla en vez de una clase escrita a mano.
//
// Persiste en un archivo de texto con una linea por entidad:
//
//   quipudb-catalog 2
//   table <nombre> <storage> <archivo> <indice_columna_clave> <page_size> <n_columnas>
//   column <nombre> <TIPO> [longitud]        (n_columnas veces)
//   index <nombre> <tabla> <columna> <kind> <archivo>
//
// Texto y no paginas porque el catalogo es diminuto (decenas de lineas), se
// lee entero al abrir y se reescribe entero al cambiar, y poder abrirlo con
// `cat` cuando algo falla vale mas que ahorrar bytes. Los identificadores no
// llevan espacios (se valida), asi que el formato no necesita comillas.
//
// Cada mutacion guarda de inmediato y de forma atomica (archivo temporal +
// rename): o queda el catalogo anterior completo o el nuevo completo.

#include <filesystem>
#include <map>
#include <string>
#include <string_view>
#include <vector>

#include "quipudb/catalog/types.hpp"

namespace quipudb {

struct IndexInfo {
  std::string name;
  std::size_t column = 0;  // posicion en Schema::columns
  std::string kind;        // kind::kBPlusUnclustered o kind::kExtendibleHash
  std::string file;        // relativo al directorio del catalogo
};

struct TableInfo {
  Schema schema;
  std::string storage;  // kind::kHeap, kSequential o kBPlusClustered
  std::string file;     // relativo al directorio del catalogo
  /// Tamano de pagina con el que se creo el archivo. Sin esto no se puede
  /// reabrir una tabla creada con un tamano distinto del que este por
  /// defecto, que es lo que necesita variar la comparacion del 2.1.6.
  std::size_t page_size = kDefaultPageSize;
  std::vector<IndexInfo> indexes;

  /// Primer indice secundario sobre esa columna, o nullptr.
  [[nodiscard]] const IndexInfo* index_on(std::size_t column) const noexcept;
  [[nodiscard]] const IndexInfo* index(std::string_view name) const noexcept;
};

class Catalog {
 public:
  /// Carga el catalogo si el archivo existe; si no, empieza vacio y lo crea
  /// en el primer cambio.
  explicit Catalog(std::filesystem::path path);

  [[nodiscard]] const std::filesystem::path& path() const noexcept { return path_; }

  /// Directorio donde viven los archivos de datos de las tablas e indices.
  [[nodiscard]] std::filesystem::path directory() const { return path_.parent_path(); }

  /// Ruta absoluta del archivo de una tabla o indice.
  [[nodiscard]] std::filesystem::path resolve(std::string_view file) const {
    return directory() / file;
  }

  // --- tablas -----------------------------------------------------------

  /// Registra una tabla. Lanza SchemaError si ya existe, si el esquema es
  /// invalido o si `storage` no es una organizacion de tabla conocida.
  const TableInfo& create_table(const Schema& schema, std::string_view storage,
                                std::size_t page_size = kDefaultPageSize);

  /// Quita la tabla y sus indices del catalogo. No borra los archivos de
  /// datos: eso lo decide quien llama, que sabe si los tiene abiertos.
  void drop_table(std::string_view name);

  [[nodiscard]] bool has_table(std::string_view name) const noexcept;

  /// Lanza SchemaError si no existe.
  [[nodiscard]] const TableInfo& table(std::string_view name) const;

  [[nodiscard]] std::vector<std::string> table_names() const;
  [[nodiscard]] std::size_t size() const noexcept { return tables_.size(); }

  // --- indices ----------------------------------------------------------

  /// Registra un indice secundario sobre una columna de la tabla, y devuelve
  /// una copia de lo que quedo registrado. Lanza SchemaError si la tabla o la
  /// columna no existen, si el nombre ya esta usado en esa tabla, si `kind` no
  /// es un indice secundario conocido, o si la tabla no se almacena como heap
  /// file (las demas organizaciones mueven sus registros de sitio y los RID
  /// guardados en el indice dejarian de valer).
  ///
  /// Devuelve por valor y no por referencia a proposito: los indices viven en
  /// un vector dentro de la tabla, asi que una segunda llamada lo reubica y
  /// dejaria colgada cualquier referencia que alguien hubiera guardado.
  IndexInfo create_index(std::string_view table, std::string_view index_name,
                         std::string_view column, std::string_view kind);

  void drop_index(std::string_view table, std::string_view index_name);

  // --- validacion -------------------------------------------------------

  /// Identificador SQL simple: [A-Za-z_][A-Za-z0-9_]*, maximo 64 caracteres.
  [[nodiscard]] static bool is_identifier(std::string_view s) noexcept;

  /// Lanza SchemaError si el esquema no es utilizable: nombre o columnas
  /// invalidos, columnas repetidas, clave fuera de rango, VARCHAR sin longitud.
  static void validate(const Schema& schema);

  /// Reescribe el archivo completo, de forma atomica.
  void save() const;

 private:
  void load();

  std::filesystem::path path_;
  std::map<std::string, TableInfo> tables_;
};

}  // namespace quipudb
