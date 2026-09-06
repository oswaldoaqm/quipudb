#pragma once

// Indice B+ no agrupado (issue #16).
//
// No agrupado quiere decir que los datos NO viven en el indice: las hojas
// guardan pares (clave, RID), y el registro se lee despues del archivo de
// datos con `TableFile::read(rid)`. Por eso implementa `Index` y no
// `TableFile`: es un indice secundario que se cuelga de una tabla.
//
//   B+ agrupado (#15)     hoja = [clave][registro completo]   ES la tabla
//   B+ no agrupado (#16)  hoja = [clave][RID]                 apunta a la tabla
//
// Sobre que tablas se puede montar
// -------------------------------
//
//   Solo sobre un heap file. Un RID guardado aqui tiene que seguir apuntando
//   al mismo registro manana, y eso solo lo garantiza el heap file: en el
//   archivo secuencial y en el B+ agrupado los registros se corren de sitio al
//   insertar. `Catalog::create_index` ya lo rechaza (#55), y esta clase lo
//   vuelve a comprobar porque tambien se puede construir sin pasar por el
//   catalogo.
//
// Claves repetidas
// ----------------
//
//   Es lo normal aqui: se indexa una columna que no es la clave primaria, asi
//   que muchos registros comparten valor. El arbol se crea admitiendo
//   repetidas (#14 lo soporta desde este issue), y las busquedas bajan una vez
//   y siguen la cadena de hojas mientras las claves sigan siendo iguales.
//
//   `remove(clave)` quita todas las entradas de esa clave; `remove(clave, rid)`
//   quita solo ese puntero, que es lo que hace falta cuando se borra un
//   registro y otros siguen compartiendo su valor.
//
// El indice no se entera solo de lo que pasa en la tabla: quien inserta,
// borra o actualiza un registro tiene que avisarle. Coordinar eso es del
// planner (2.1.3); aqui la responsabilidad termina en mantener los pares.

#include <cstddef>
#include <filesystem>
#include <string_view>
#include <utility>
#include <vector>

#include "quipudb/catalog/table.hpp"
#include "quipudb/index/bplus_tree.hpp"

namespace quipudb {

class BPlusUnclusteredIndex final : public Index {
 public:
  /// Bytes que ocupa un RID serializado en una hoja.
  static constexpr std::size_t kRidSize = sizeof(PageId) + sizeof(SlotId);

  /// `column` es la columna indexada de la tabla de datos. `data` es la tabla
  /// a la que apuntan los RID: tiene que ser un heap file.
  BPlusUnclusteredIndex(std::filesystem::path path, Column column, TableFile& data,
                        std::size_t page_size = kDefaultPageSize, std::size_t order = 0);

  // --- Index --------------------------------------------------------------

  [[nodiscard]] std::string_view kind() const noexcept override {
    return kind::kBPlusUnclustered;
  }
  [[nodiscard]] DataType key_type() const noexcept override { return column_.type; }
  [[nodiscard]] bool supports_range() const noexcept override { return true; }

  void insert(const Key& key, RID rid) override;
  std::size_t remove(const Key& key) override;
  bool remove(const Key& key, RID rid) override;
  [[nodiscard]] std::vector<RID> search(const Key& key) override;
  [[nodiscard]] std::vector<RID> range_search(const Key& lo, const Key& hi) override;
  [[nodiscard]] std::vector<std::pair<Key, RID>> scan() override;
  [[nodiscard]] std::size_t size() const override { return tree_.size(); }

  [[nodiscard]] const OpStats& stats() const noexcept override { return tree_.stats(); }
  void reset_stats() noexcept override { tree_.reset_stats(); }

  // --- propios ------------------------------------------------------------

  [[nodiscard]] const Column& column() const noexcept { return column_; }

  /// Registros que tienen ese valor en la columna indexada: busca en el arbol
  /// y resuelve cada puntero contra la tabla de datos. Es la operacion que un
  /// indice secundario existe para hacer.
  [[nodiscard]] std::vector<Record> lookup(const Key& key);

  /// Lo mismo para un intervalo [lo, hi], inclusive.
  [[nodiscard]] std::vector<Record> lookup_range(const Key& lo, const Key& hi);

  /// Recorre la tabla entera y arma el indice desde cero. Es lo que se hace al
  /// crear un indice sobre una tabla que ya tiene datos.
  void build();

  [[nodiscard]] std::size_t height() const noexcept { return tree_.height(); }
  [[nodiscard]] std::size_t order() const noexcept { return tree_.order(); }
  [[nodiscard]] std::uintmax_t file_size() const { return tree_.file_size(); }
  [[nodiscard]] std::string check_invariants() { return tree_.check_invariants(); }

  void flush() { tree_.flush(); }

 private:
  [[nodiscard]] std::vector<Record> resolver(const std::vector<RID>& rids);

  Column column_;
  std::size_t key_index_ = 0;  // posicion de la columna en el esquema de la tabla
  TableFile* data_;
  BPlusTree tree_;
};

}  // namespace quipudb
