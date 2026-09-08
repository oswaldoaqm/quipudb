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
//   db.create_index("alumnos", "por_edad", "edad", kind::kExtendibleHash);
//   Index& ix = db.index("alumnos", "por_edad");
//   for (RID rid : ix.search(Value{20})) { ... }
//
// Devuelve SIEMPRE el mismo objeto para una tabla dada, y eso no es un
// detalle: dos handles abiertos sobre el mismo archivo tienen cada uno su
// copia de los contadores y del estado en memoria, se pisan al escribir y
// dejan el archivo con contadores que no cuadran. La unica forma segura de
// trabajar con una tabla es que haya un solo objeto por archivo, y esta clase
// es la que lo garantiza.
//
// Indices
// -------
//
// El catalogo (#7) ya registraba indices -- nombre, columna, tipo y archivo --
// pero nadie sabia convertir ese registro en un objeto usable, asi que cada
// consumidor habria tenido que escribir su propio
// `if (kind == "bplus_unclustered") new BPlusUnclusteredIndex(...)`. Es
// exactamente el problema que el #56 resolvio para las tablas, con los mismos
// cinco consumidores: planner, parser, transacciones, benchmarks y bindings.
//
// La regla del objeto unico vale doble aqui. Un indice y su tabla de datos
// TIENEN que ser los mismos objetos que ve todo el mundo: si el planner
// inserta por un handle de la tabla y el indice apunta a otro, los RID que
// guarda el indice se refieren a un estado que el otro handle no conoce.
// Por eso `index()` monta el indice sobre el handle que devuelve `table()`, y
// cerrar una tabla cierra antes sus indices.
//
// Lo que esta clase NO hace es mantener los indices al dia cuando la tabla
// cambia: quien inserta, borra o actualiza un registro tiene que avisarle al
// indice. Coordinar eso es del planner (2.1.3). Aqui la responsabilidad
// termina en que haya un solo objeto por archivo y en saber abrirlo.
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

  /// Cierra los indices antes que las tablas. Un indice guarda un puntero a su
  /// tabla de datos, asi que destruirla primero lo dejaria colgado. El orden
  /// de los miembros ya lo garantiza (se destruyen al reves de como se
  /// declaran, y `indices_` va despues de `abiertas_`), pero eso es una
  /// propiedad fragil que un reordenamiento inocente rompe en silencio: aqui
  /// queda explicito.
  ~Database();

  Database(const Database&) = delete;
  Database& operator=(const Database&) = delete;

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

  /// Si esa tabla esta abierta, la cierra (vaciando lo pendiente) junto con
  /// sus indices. Los handles que alguien haya guardado dejan de valer.
  void close(std::string_view name);

  /// Quita la tabla del catalogo, cierra su handle y el de sus indices, y
  /// borra los archivos de todos.
  void drop_table(std::string_view name);

  // --- indices ------------------------------------------------------------

  /// Registra el indice en el catalogo, crea su archivo y devuelve el handle,
  /// ya construido sobre los datos que la tabla tenga.
  ///
  /// Lanza SchemaError por lo mismo que `Catalog::create_index`: tabla o
  /// columna inexistente, nombre repetido, `kind` que no es un indice
  /// secundario, o una tabla que no se almacena como heap file.
  Index& create_index(std::string_view table, std::string_view index_name,
                      std::string_view column, std::string_view kind);

  /// Handle de un indice ya registrado, abriendolo la primera vez. Siempre el
  /// mismo objeto, montado sobre el mismo handle de tabla que devuelve
  /// `table()`. Lanza SchemaError si la tabla o el indice no estan en el
  /// catalogo.
  Index& index(std::string_view table, std::string_view index_name);

  /// Quita el indice del catalogo, cierra su handle y borra su archivo. La
  /// tabla no se toca.
  void drop_index(std::string_view table, std::string_view index_name);

  /// Vacia a disco todas las tablas y todos los indices abiertos.
  void flush();

  [[nodiscard]] bool is_open(std::string_view name) const noexcept;
  [[nodiscard]] std::vector<std::string> open_tables() const;

  /// Indices abiertos, como "tabla.indice".
  [[nodiscard]] std::vector<std::string> open_indexes() const;

 private:
  [[nodiscard]] std::unique_ptr<TableFile> abrir(const TableInfo& info) const;

  /// Construye el indice de `info` sobre `datos`. Es el unico sitio donde vive
  /// el switch de `IndexInfo::kind`: agregar un tipo de indice es tocar esta
  /// funcion y nada mas.
  [[nodiscard]] std::unique_ptr<Index> abrir_indice(const TableInfo& tabla,
                                                    const IndexInfo& info,
                                                    TableFile& datos) const;

  /// Clave con la que se guarda un indice abierto: "tabla.indice".
  [[nodiscard]] static std::string clave_indice(std::string_view table,
                                                std::string_view index_name);

  /// Cierra los indices abiertos de esa tabla, vaciando lo pendiente.
  void cerrar_indices_de(std::string_view table);

  Catalog catalog_;
  std::map<std::string, std::unique_ptr<TableFile>, std::less<>> abiertas_;
  std::map<std::string, std::unique_ptr<Index>, std::less<>> indices_;
};

}  // namespace quipudb
