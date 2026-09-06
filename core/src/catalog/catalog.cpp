#include "quipudb/catalog/catalog.hpp"

#include <cctype>
#include <fstream>
#include <optional>
#include <set>
#include <sstream>

#include "quipudb/catalog/table.hpp"
#include "quipudb/storage/page.hpp"
#include "quipudb/error.hpp"

namespace quipudb {

namespace {

constexpr std::string_view kMagic = "quipudb-catalog";
constexpr int kVersion = 2;

bool is_table_storage(std::string_view s) noexcept {
  return s == kind::kHeap || s == kind::kSequential || s == kind::kBPlusClustered;
}

bool is_index_kind(std::string_view s) noexcept {
  return s == kind::kBPlusUnclustered || s == kind::kExtendibleHash;
}

std::optional<DataType> parse_type(std::string_view s) noexcept {
  for (const auto t : {DataType::Int, DataType::Double, DataType::Varchar, DataType::Bool,
                       DataType::Date}) {
    if (to_string(t) == s) return t;
  }
  return std::nullopt;
}

std::string quoted(const std::filesystem::path& p) { return "'" + p.string() + "'"; }

}  // namespace

// ---------------------------------------------------------------------------
// TableInfo
// ---------------------------------------------------------------------------

const IndexInfo* TableInfo::index_on(std::size_t column) const noexcept {
  for (const auto& ix : indexes) {
    if (ix.column == column) return &ix;
  }
  return nullptr;
}

const IndexInfo* TableInfo::index(std::string_view name) const noexcept {
  for (const auto& ix : indexes) {
    if (ix.name == name) return &ix;
  }
  return nullptr;
}

// ---------------------------------------------------------------------------
// Validacion
// ---------------------------------------------------------------------------

bool Catalog::is_identifier(std::string_view s) noexcept {
  if (s.empty() || s.size() > 64) return false;
  const auto first = static_cast<unsigned char>(s[0]);
  if (!(std::isalpha(first) || first == '_')) return false;
  for (const char ch : s) {
    const auto c = static_cast<unsigned char>(ch);
    if (!(std::isalnum(c) || c == '_')) return false;
  }
  return true;
}

void Catalog::validate(const Schema& schema) {
  if (!is_identifier(schema.table_name)) {
    throw SchemaError("nombre de tabla invalido: '" + schema.table_name + "'");
  }
  if (schema.columns.empty()) {
    throw SchemaError("la tabla " + schema.table_name + " no tiene columnas");
  }
  std::set<std::string> seen;
  for (const auto& c : schema.columns) {
    if (!is_identifier(c.name)) {
      throw SchemaError("nombre de columna invalido en " + schema.table_name + ": '" + c.name +
                        "'");
    }
    if (!seen.insert(c.name).second) {
      throw SchemaError("columna repetida en " + schema.table_name + ": " + c.name);
    }
    if (c.type == DataType::Varchar && c.length == 0) {
      throw SchemaError("columna " + c.name + " de " + schema.table_name +
                        ": VARCHAR necesita una longitud mayor que 0");
    }
    if (c.type != DataType::Varchar && c.length != 0) {
      throw SchemaError("columna " + c.name + " de " + schema.table_name +
                        ": solo VARCHAR lleva longitud");
    }
  }
  if (schema.key_column >= schema.columns.size()) {
    throw SchemaError("la clave de " + schema.table_name + " apunta a la columna " +
                      std::to_string(schema.key_column) + " y solo hay " +
                      std::to_string(schema.columns.size()));
  }
}

// ---------------------------------------------------------------------------
// Ciclo de vida
// ---------------------------------------------------------------------------

Catalog::Catalog(std::filesystem::path path) : path_(std::move(path)) {
  if (std::filesystem::exists(path_)) load();
}

const TableInfo& Catalog::create_table(const Schema& schema, std::string_view storage,
                                       std::size_t page_size) {
  validate(schema);
  if (!is_table_storage(storage)) {
    throw SchemaError("'" + std::string(storage) + "' no es una organizacion de tabla (" +
                      std::string(kind::kHeap) + ", " + std::string(kind::kSequential) + ", " +
                      std::string(kind::kBPlusClustered) + ")");
  }
  if (tables_.contains(schema.table_name)) {
    throw SchemaError("la tabla " + schema.table_name + " ya existe");
  }
  if (page_size < Page::kMinSize || page_size > Page::kMaxSize) {
    throw SchemaError("tamano de pagina invalido para " + schema.table_name + ": " +
                      std::to_string(page_size));
  }
  TableInfo info;
  info.schema = schema;
  info.storage = std::string(storage);
  info.file = schema.table_name + "." + std::string(storage);
  info.page_size = page_size;
  auto [it, _] = tables_.emplace(schema.table_name, std::move(info));
  save();
  return it->second;
}

void Catalog::drop_table(std::string_view name) {
  const auto it = tables_.find(std::string(name));
  if (it == tables_.end()) throw SchemaError("la tabla " + std::string(name) + " no existe");
  tables_.erase(it);
  save();
}

bool Catalog::has_table(std::string_view name) const noexcept {
  return tables_.find(std::string(name)) != tables_.end();
}

const TableInfo& Catalog::table(std::string_view name) const {
  const auto it = tables_.find(std::string(name));
  if (it == tables_.end()) throw SchemaError("la tabla " + std::string(name) + " no existe");
  return it->second;
}

std::vector<std::string> Catalog::table_names() const {
  std::vector<std::string> out;
  out.reserve(tables_.size());
  for (const auto& [name, _] : tables_) out.push_back(name);
  return out;
}

IndexInfo Catalog::create_index(std::string_view table, std::string_view index_name,
                                std::string_view column, std::string_view kind) {
  const auto it = tables_.find(std::string(table));
  if (it == tables_.end()) throw SchemaError("la tabla " + std::string(table) + " no existe");
  TableInfo& info = it->second;
  if (!is_identifier(index_name)) {
    throw SchemaError("nombre de indice invalido: '" + std::string(index_name) + "'");
  }
  if (info.index(index_name) != nullptr) {
    throw SchemaError("el indice " + std::string(index_name) + " ya existe en " +
                      std::string(table));
  }
  const auto col = info.schema.find(column);
  if (!col) {
    throw SchemaError("la columna " + std::string(column) + " no existe en " + std::string(table));
  }
  if (!is_index_kind(kind)) {
    throw SchemaError("'" + std::string(kind) + "' no es un indice secundario (" +
                      std::string(kind::kBPlusUnclustered) + ", " +
                      std::string(kind::kExtendibleHash) + ")");
  }
  // Un indice secundario guarda RIDs y los resuelve con TableFile::read, asi
  // que solo sirve sobre una organizacion cuyos RIDs no se muevan. Hoy eso es
  // el heap file: en el secuencial y en el B+ agrupado, insertar corre los
  // registros de sitio y el indice apuntaria al vecino sin lanzar nada.
  if (info.storage != kind::kHeap) {
    throw SchemaError("no se puede indexar " + std::string(table) + ", que se almacena como " +
                      info.storage + ": los RID de esa organizacion no son estables");
  }
  IndexInfo ix;
  ix.name = std::string(index_name);
  ix.column = *col;
  ix.kind = std::string(kind);
  // El nombre del indice es lo unico unico dentro de la tabla: si el archivo
  // se nombrara por la columna, dos indices sobre la misma columna se
  // pisarian en disco.
  ix.file = std::string(table) + "." + std::string(index_name) + "." + std::string(kind);
  info.indexes.push_back(std::move(ix));
  save();
  return info.indexes.back();
}

void Catalog::drop_index(std::string_view table, std::string_view index_name) {
  const auto it = tables_.find(std::string(table));
  if (it == tables_.end()) throw SchemaError("la tabla " + std::string(table) + " no existe");
  auto& ixs = it->second.indexes;
  for (auto ix = ixs.begin(); ix != ixs.end(); ++ix) {
    if (ix->name == index_name) {
      ixs.erase(ix);
      save();
      return;
    }
  }
  throw SchemaError("el indice " + std::string(index_name) + " no existe en " +
                    std::string(table));
}

// ---------------------------------------------------------------------------
// Persistencia
// ---------------------------------------------------------------------------

void Catalog::save() const {
  std::ostringstream out;
  out << kMagic << ' ' << kVersion << '\n';
  for (const auto& [name, info] : tables_) {
    out << "table " << name << ' ' << info.storage << ' ' << info.file << ' '
        << info.schema.key_column << ' ' << info.page_size << ' '
        << info.schema.columns.size() << '\n';
    for (const auto& c : info.schema.columns) {
      out << "column " << c.name << ' ' << to_string(c.type);
      if (c.type == DataType::Varchar) out << ' ' << c.length;
      out << '\n';
    }
    for (const auto& ix : info.indexes) {
      out << "index " << ix.name << ' ' << name << ' ' << info.schema.columns[ix.column].name
          << ' ' << ix.kind << ' ' << ix.file << '\n';
    }
  }

  const auto tmp = std::filesystem::path(path_.string() + ".tmp");
  {
    std::ofstream f(tmp, std::ios::binary | std::ios::trunc);
    if (!f) throw IoError("no se pudo escribir " + quoted(tmp));
    f << out.str();
    f.flush();
    if (!f) throw IoError("fallo al escribir " + quoted(tmp));
  }
  std::error_code ec;
  std::filesystem::rename(tmp, path_, ec);
  if (ec) {
    std::filesystem::remove(tmp);
    throw IoError("no se pudo reemplazar " + quoted(path_) + ": " + ec.message());
  }
}

void Catalog::load() {
  std::ifstream f(path_, std::ios::binary);
  if (!f) throw IoError("no se pudo abrir " + quoted(path_));

  const auto bad = [&](int line, const std::string& why) {
    return IoError(quoted(path_) + " linea " + std::to_string(line) + ": " + why);
  };

  std::string line;
  int n = 0;

  if (!std::getline(f, line)) throw bad(1, "archivo vacio");
  ++n;
  {
    std::istringstream head(line);
    std::string magic;
    int version = 0;
    head >> magic >> version;
    if (magic != kMagic) throw bad(n, "no es un catalogo de QuipuDB");
    if (version != kVersion) {
      throw bad(n, "version de formato " + std::to_string(version) + ", se entiende la " +
                       std::to_string(kVersion));
    }
  }

  std::map<std::string, TableInfo> tables;
  TableInfo* current = nullptr;
  std::size_t pending_columns = 0;

  while (std::getline(f, line)) {
    ++n;
    if (line.empty()) continue;
    std::istringstream in(line);
    std::string tag;
    in >> tag;

    if (tag == "table") {
      if (pending_columns != 0) throw bad(n, "faltan columnas de la tabla anterior");
      TableInfo info;
      std::size_t ncols = 0;
      in >> info.schema.table_name >> info.storage >> info.file >> info.schema.key_column >>
          info.page_size >> ncols;
      if (!in) throw bad(n, "linea 'table' incompleta");
      if (info.page_size < Page::kMinSize || info.page_size > Page::kMaxSize) {
        throw bad(n, "tamano de pagina invalido: " + std::to_string(info.page_size));
      }
      if (!is_identifier(info.schema.table_name)) throw bad(n, "nombre de tabla invalido");
      if (!is_table_storage(info.storage)) throw bad(n, "storage desconocido: " + info.storage);
      if (ncols == 0) throw bad(n, "tabla sin columnas");
      if (tables.contains(info.schema.table_name)) throw bad(n, "tabla repetida");
      const auto name = info.schema.table_name;
      current = &tables.emplace(name, std::move(info)).first->second;
      pending_columns = ncols;

    } else if (tag == "column") {
      if (current == nullptr || pending_columns == 0) throw bad(n, "'column' fuera de una tabla");
      Column c;
      std::string type;
      in >> c.name >> type;
      if (!in) throw bad(n, "linea 'column' incompleta");
      const auto t = parse_type(type);
      if (!t) throw bad(n, "tipo desconocido: " + type);
      c.type = *t;
      if (c.type == DataType::Varchar) {
        unsigned len = 0;
        in >> len;
        if (!in || len == 0 || len > 65535) throw bad(n, "VARCHAR sin longitud valida");
        c.length = static_cast<std::uint16_t>(len);
      }
      current->schema.columns.push_back(std::move(c));
      if (--pending_columns == 0) {
        try {
          validate(current->schema);
        } catch (const SchemaError& e) {
          throw bad(n, e.what());
        }
      }

    } else if (tag == "index") {
      if (pending_columns != 0) throw bad(n, "faltan columnas de la tabla anterior");
      IndexInfo ix;
      std::string table, column;
      in >> ix.name >> table >> column >> ix.kind >> ix.file;
      if (!in) throw bad(n, "linea 'index' incompleta");
      const auto it = tables.find(table);
      if (it == tables.end()) throw bad(n, "indice sobre tabla desconocida: " + table);
      const auto col = it->second.schema.find(column);
      if (!col) throw bad(n, "indice sobre columna desconocida: " + column);
      if (!is_index_kind(ix.kind)) throw bad(n, "tipo de indice desconocido: " + ix.kind);
      if (it->second.index(ix.name) != nullptr) throw bad(n, "indice repetido: " + ix.name);
      ix.column = *col;
      it->second.indexes.push_back(std::move(ix));

    } else {
      throw bad(n, "linea desconocida: " + tag);
    }
  }
  if (pending_columns != 0) throw bad(n, "faltan columnas de la ultima tabla");

  tables_ = std::move(tables);
}

}  // namespace quipudb
