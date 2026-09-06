#pragma once

// Indice B+ agrupado (issue #15).
//
// Agrupado quiere decir que el indice ES la tabla: los registros completos
// viven en las hojas del arbol, ordenados por clave primaria. No hay un
// archivo de datos aparte al que apuntar, y por eso esta clase implementa
// `TableFile` y no `Index` -- es una tercera organizacion de tabla, junto al
// heap file y al archivo secuencial paginado.
//
//   heap file            registros en orden de llegada
//   secuencial paginado  registros ordenados, con area de overflow
//   B+ agrupado          registros ordenados en las hojas de un arbol
//
// Comparado con el archivo secuencial, que tambien manda registros ordenados:
// el secuencial mantiene el orden con un area de overflow y particiones de
// grupo, y su busqueda hace dos busquedas binarias (directorio en memoria y
// dentro de la pagina). El B+ agrupado paga paginas de nodos internos, pero a
// cambio la busqueda baja siempre la misma cantidad de niveles y no hay
// overflow que revisar ni reorganizacion que disparar.
//
// El payload de cada entrada del arbol es el registro serializado con el
// mismo codec que las demas organizaciones (#7), asi que el arbol no sabe que
// esta guardando: eso es lo que permite que el #16 use el mismo `BPlusTree`
// con RIDs como payload.
//
// Los RID de esta organizacion NO son estables: un split mueve las entradas
// de sitio. El contrato (#3) ya lo dice, y `Catalog::create_index` rechaza
// crear un indice secundario sobre una tabla asi.
//
// Falta el rebalanceo al borrar (#17): `remove` quita la entrada de la hoja
// sin fusionar ni redistribuir, asi que el arbol queda correcto pero con
// hojas por debajo de la mitad.

#include <cstddef>
#include <filesystem>
#include <memory>
#include <optional>
#include <string_view>
#include <vector>

#include "quipudb/catalog/record_codec.hpp"
#include "quipudb/catalog/table.hpp"
#include "quipudb/index/bplus_tree.hpp"

namespace quipudb {

class BPlusClusteredTable final : public TableFile {
 public:
  /// Abre la tabla si el archivo existe, o la crea. `order` es el del arbol:
  /// 0 significa el maximo que entre en una pagina.
  BPlusClusteredTable(std::filesystem::path path, Schema schema,
                      std::size_t page_size = kDefaultPageSize, std::size_t order = 0);

  // --- TableFile ----------------------------------------------------------

  [[nodiscard]] const Schema& schema() const noexcept override { return codec_.schema(); }
  [[nodiscard]] std::string_view kind() const noexcept override { return kind::kBPlusClustered; }

  RID insert(const Record& record) override;
  std::size_t remove(const Key& key) override;
  std::size_t update(const Key& key, const Record& record) override;
  [[nodiscard]] std::vector<Record> search(const Key& key) override;
  [[nodiscard]] std::vector<Record> range_search(const Key& lo, const Key& hi) override;
  [[nodiscard]] std::vector<Record> scan() override;
  [[nodiscard]] std::unique_ptr<RecordCursor> cursor() override;
  [[nodiscard]] std::optional<Record> read(RID rid) override;
  [[nodiscard]] std::size_t size() const override { return tree_.size(); }

  [[nodiscard]] const OpStats& stats() const noexcept override { return tree_.stats(); }
  void reset_stats() noexcept override { tree_.reset_stats(); }

  // --- propios ------------------------------------------------------------

  /// Altura del arbol: cuantas paginas se leen en una busqueda puntual.
  [[nodiscard]] std::size_t height() const noexcept { return tree_.height(); }
  [[nodiscard]] std::size_t order() const noexcept { return tree_.order(); }
  [[nodiscard]] PageId page_count() const noexcept { return tree_.page_count(); }
  [[nodiscard]] std::uintmax_t file_size() const { return tree_.file_size(); }

  /// Comprueba las invariantes del arbol; cadena vacia si esta todo bien.
  [[nodiscard]] std::string check_invariants() { return tree_.check_invariants(); }

  void flush() { tree_.flush(); }

 private:
  class Cursor;

  RecordCodec codec_;
  BPlusTree tree_;
};

}  // namespace quipudb
