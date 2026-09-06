#include "quipudb/catalog/database.hpp"

#include "quipudb/error.hpp"
#include "quipudb/index/bplus_clustered_table.hpp"
#include "quipudb/storage/heap_file.hpp"
#include "quipudb/storage/sequential_file.hpp"

namespace quipudb {

Database::Database(std::filesystem::path catalog_path) : catalog_(std::move(catalog_path)) {}

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

void Database::close(std::string_view name) {
  if (const auto it = abiertas_.find(name); it != abiertas_.end()) abiertas_.erase(it);
}

void Database::drop_table(std::string_view name) {
  const auto ruta = catalog_.resolve(catalog_.table(name).file);
  close(name);  // hay que soltar el archivo antes de borrarlo
  catalog_.drop_table(name);
  std::error_code ec;
  std::filesystem::remove(ruta, ec);
  if (ec) throw IoError("no se pudo borrar '" + ruta.string() + "': " + ec.message());
}

void Database::flush() {
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
