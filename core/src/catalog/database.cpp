#include "quipudb/catalog/database.hpp"

#include "quipudb/error.hpp"
#include "quipudb/index/bplus_clustered_table.hpp"
#include "quipudb/index/bplus_unclustered_index.hpp"
#include "quipudb/index/extendible_hash_index.hpp"
#include "quipudb/storage/heap_file.hpp"
#include "quipudb/storage/sequential_file.hpp"

namespace quipudb {

Database::Database(std::filesystem::path catalog_path) : catalog_(std::move(catalog_path)) {}

Database::~Database() {
  // Los indices guardan un puntero a su tabla de datos: se sueltan primero.
  indices_.clear();
}

std::unique_ptr<TableFile> Database::abrir(const TableInfo& info) const {
  const auto ruta = catalog_.resolve(info.file);
  if (info.storage == kind::kHeap) {
    return std::make_unique<HeapFile>(ruta, info.schema, info.page_size);
  }
  if (info.storage == kind::kSequential) {
    return std::make_unique<SequentialFile>(ruta, info.schema, info.page_size);
  }
  if (info.storage == kind::kBPlusClustered) {
    return std::make_unique<BPlusClusteredTable>(ruta, info.schema, info.page_size);
  }
  throw Unsupported("la organizacion '" + info.storage + "' de la tabla " +
                    info.schema.table_name + " no esta implementada");
}

TableFile& Database::create_table(const Schema& schema, std::string_view storage,
                                  std::size_t page_size) {
  const TableInfo& info = catalog_.create_table(schema, storage, page_size);
  auto handle = abrir(info);
  auto [it, _] = abiertas_.emplace(schema.table_name, std::move(handle));
  return *it->second;
}

TableFile& Database::table(std::string_view name) {
  if (const auto it = abiertas_.find(name); it != abiertas_.end()) return *it->second;
  const TableInfo& info = catalog_.table(name);  // lanza si no existe
  auto [it, _] = abiertas_.emplace(std::string(name), abrir(info));
  return *it->second;
}

// ---------------------------------------------------------------------------
// Indices
// ---------------------------------------------------------------------------

std::string Database::clave_indice(std::string_view table, std::string_view index_name) {
  return std::string(table) + "." + std::string(index_name);
}

std::unique_ptr<Index> Database::abrir_indice(const TableInfo& tabla, const IndexInfo& info,
                                              TableFile& datos) const {
  const auto ruta = catalog_.resolve(info.file);
  const Column& col = tabla.schema.columns.at(info.column);
  if (info.kind == kind::kBPlusUnclustered) {
    return std::make_unique<BPlusUnclusteredIndex>(ruta, col, datos, tabla.page_size);
  }
  if (info.kind == kind::kExtendibleHash) {
    return std::make_unique<ExtendibleHashIndex>(ruta, col, datos, tabla.page_size);
  }
  throw Unsupported("el indice '" + info.name + "' de " + tabla.schema.table_name +
                    " es de tipo '" + info.kind + "', que no esta implementado");
}

Index& Database::create_index(std::string_view table, std::string_view index_name,
                              std::string_view column, std::string_view kind) {
  // El catalogo valida todo lo que puede fallar (tabla, columna, nombre
  // repetido, tipo de indice, organizacion de la tabla) antes de que aqui se
  // cree ningun archivo.
  const IndexInfo info = catalog_.create_index(table, index_name, column, kind);
  TableFile& datos = this->table(table);  // el MISMO handle que ve todo el mundo
  auto handle = abrir_indice(catalog_.table(table), info, datos);
  // Una tabla puede tener datos antes de que se indexe una columna, asi que el
  // indice se construye recorriendola.
  if (auto* b = dynamic_cast<BPlusUnclusteredIndex*>(handle.get())) {
    b->build();
  } else if (auto* h = dynamic_cast<ExtendibleHashIndex*>(handle.get())) {
    h->build();
  }
  auto [it, _] = indices_.emplace(clave_indice(table, index_name), std::move(handle));
  return *it->second;
}

Index& Database::index(std::string_view table, std::string_view index_name) {
  const auto clave = clave_indice(table, index_name);
  if (const auto it = indices_.find(clave); it != indices_.end()) return *it->second;

  const TableInfo& tabla = catalog_.table(table);  // lanza si no existe
  const IndexInfo* info = tabla.index(index_name);
  if (info == nullptr) {
    throw SchemaError("la tabla " + std::string(table) + " no tiene un indice llamado " +
                      std::string(index_name));
  }
  TableFile& datos = this->table(table);
  auto [it, _] = indices_.emplace(clave, abrir_indice(tabla, *info, datos));
  return *it->second;
}

void Database::drop_index(std::string_view table, std::string_view index_name) {
  const TableInfo& tabla = catalog_.table(table);
  const IndexInfo* info = tabla.index(index_name);
  if (info == nullptr) {
    throw SchemaError("la tabla " + std::string(table) + " no tiene un indice llamado " +
                      std::string(index_name));
  }
  const auto ruta = catalog_.resolve(info->file);
  if (const auto it = indices_.find(clave_indice(table, index_name)); it != indices_.end()) {
    indices_.erase(it);  // soltar el archivo antes de borrarlo
  }
  catalog_.drop_index(table, index_name);
  std::error_code ec;
  std::filesystem::remove(ruta, ec);
  if (ec) throw IoError("no se pudo borrar '" + ruta.string() + "': " + ec.message());
}

// ---------------------------------------------------------------------------

void Database::cerrar_indices_de(std::string_view table) {
  // Los indices se cierran ANTES que su tabla: guardan un puntero a ella y
  // vaciarlos despues seria usar un objeto ya destruido.
  const auto prefijo = std::string(table) + ".";
  for (auto it = indices_.begin(); it != indices_.end();) {
    if (it->first.compare(0, prefijo.size(), prefijo) == 0) {
      it = indices_.erase(it);
    } else {
      ++it;
    }
  }
}

void Database::close(std::string_view name) {
  cerrar_indices_de(name);
  if (const auto it = abiertas_.find(name); it != abiertas_.end()) abiertas_.erase(it);
}

void Database::drop_table(std::string_view name) {
  const TableInfo& info = catalog_.table(name);
  std::vector<std::filesystem::path> rutas{catalog_.resolve(info.file)};
  // Los indices de una tabla mueren con ella: sus RID no apuntan a nada.
  for (const auto& ix : info.indexes) rutas.push_back(catalog_.resolve(ix.file));

  close(name);  // hay que soltar los archivos antes de borrarlos
  catalog_.drop_table(name);
  for (const auto& ruta : rutas) {
    std::error_code ec;
    std::filesystem::remove(ruta, ec);
    if (ec) throw IoError("no se pudo borrar '" + ruta.string() + "': " + ec.message());
  }
}

void Database::flush() {
  // Primero los indices: si uno tuviera algo pendiente que dependa de la
  // tabla, la tabla todavia esta viva.
  for (auto& [nombre, handle] : indices_) {
    if (auto* b = dynamic_cast<BPlusUnclusteredIndex*>(handle.get())) {
      b->flush();
    } else if (auto* h = dynamic_cast<ExtendibleHashIndex*>(handle.get())) {
      h->flush();
    }
  }
  for (auto& [nombre, handle] : abiertas_) {
    if (auto* h = dynamic_cast<HeapFile*>(handle.get())) {
      h->flush();
    } else if (auto* s = dynamic_cast<SequentialFile*>(handle.get())) {
      s->flush();
    } else if (auto* b = dynamic_cast<BPlusClusteredTable*>(handle.get())) {
      b->flush();
    }
  }
}

std::vector<std::string> Database::open_indexes() const {
  std::vector<std::string> out;
  out.reserve(indices_.size());
  for (const auto& [nombre, _] : indices_) out.push_back(nombre);
  return out;
}

bool Database::is_open(std::string_view name) const noexcept {
  return abiertas_.find(name) != abiertas_.end();
}

std::vector<std::string> Database::open_tables() const {
  std::vector<std::string> out;
  out.reserve(abiertas_.size());
  for (const auto& [nombre, _] : abiertas_) out.push_back(nombre);
  return out;
}

}  // namespace quipudb
