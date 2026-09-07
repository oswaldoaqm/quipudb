#pragma once

// Indice secundario por hash extensible (issue #19).
//
// Es la tercera vineta de 2.1.2 -- "Indice Hash Dinamico (Extendible Hashing)"
// -- y cierra con esta clase, no con el #18: aquel dejo la maquinaria
// (`ExtendibleHash`, que guarda bytes opacos) y aqui esta el indice que el
// planner puede usar. Misma relacion que hay entre `BPlusTree` (#14) y
// `BPlusUnclusteredIndex` (#16).
//
//   B+ no agrupado (#16)   hoja = [clave][RID]     ordenado, sirve para rango
//   hash extensible (#19)  bucket = [clave][RID]   sin orden, igualdad en O(1)
//
// Por que no sirve para busquedas por rango
// -----------------------------------------
//
//   Es la pregunta que el analisis comparativo (2.1.6) tiene que responder, y
//   la respuesta esta en el hash mismo, no en como esta implementado.
//
//   El directorio se indexa con los bits bajos del hash, y un hash bueno
//   ESPARCE: su trabajo es que claves parecidas caigan lejos. Las claves 100 y
//   101 terminan en buckets sin relacion, y entre ellas puede haber cualquier
//   otra clave del archivo. No existe "el bucket siguiente": el orden de
//   bucket no tiene nada que ver con el orden de clave.
//
//   Asi que responder `WHERE edad BETWEEN 20 AND 30` con este indice obliga a
//   una de dos cosas, y las dos son peores que no tenerlo:
//
//     - recorrer TODOS los buckets y filtrar, que es un scan completo con
//       peor localidad que el del heap file, porque los buckets estan
//       repartidos por el archivo; o
//     - generar todas las claves del intervalo y buscarlas una por una, lo que
//       solo es posible si el tipo es discreto y acotado, y cuesta una
//       busqueda por valor posible aunque no exista ninguno.
//
//   El B+ no tiene ese problema porque sus hojas estan encadenadas EN ORDEN DE
//   CLAVE: se baja una vez al extremo del intervalo y se sigue la cadena. Esa
//   es la diferencia que el 2.1.6 mide, y es estructural.
//
//   Por eso `supports_range()` devuelve false y `range_search` lanza
//   `Unsupported` en vez de devolver un resultado caro en silencio. El planner
//   consulta `supports_range()` antes de elegir el indice; si lo ignora,
//   recibe la excepcion. Devolver el resultado igual seria peor: una consulta
//   que parece funcionar y que en 100 000 registros tarda lo que un scan.
//
//   Lo que este indice SI hace mejor que el B+ es la igualdad exacta: un
//   acceso al directorio (que vive en memoria) mas uno al bucket, sin importar
//   cuantas claves haya. El B+ paga la altura del arbol.
//
// Sobre que tablas se puede montar
// --------------------------------
//
//   Solo sobre un heap file, por la misma razon que el indice no agrupado
//   (#16): un RID guardado aqui tiene que seguir apuntando al mismo registro
//   manana, y en el archivo secuencial y en el B+ agrupado los registros se
//   corren de sitio al insertar.
//
// Claves repetidas
// ----------------
//
//   Es el caso normal en un indice secundario. La maquinaria del #18 las
//   resuelve con cadenas de overflow, porque dos claves iguales tienen el
//   mismo hash y ningun split las separa. El costo de esa decision se paga
//   aqui: `search` sobre una clave muy repetida recorre su cadena. Es la
//   degradacion conocida de la estructura y `overflow_pages()` esta para
//   poder reportarla en vez de suponerla.

#include <cstddef>
#include <filesystem>
#include <string_view>
#include <utility>
#include <vector>

#include "quipudb/catalog/table.hpp"
#include "quipudb/index/extendible_hash.hpp"

namespace quipudb {

class ExtendibleHashIndex final : public Index {
 public:
  /// Bytes que ocupa un RID serializado dentro de un bucket.
  static constexpr std::size_t kRidSize = sizeof(PageId) + sizeof(SlotId);

  /// `column` es la columna indexada de la tabla de datos. `data` es la tabla
  /// a la que apuntan los RID: tiene que ser un heap file.
  ExtendibleHashIndex(std::filesystem::path path, Column column, TableFile& data,
                      std::size_t page_size = kDefaultPageSize,
                      std::size_t bucket_capacity = 0);

  // --- Index --------------------------------------------------------------

  [[nodiscard]] std::string_view kind() const noexcept override {
    return kind::kExtendibleHash;
  }
  [[nodiscard]] DataType key_type() const noexcept override { return column_.type; }

  /// false, y no es una limitacion de esta implementacion sino de la
  /// estructura. Ver la cabecera.
  [[nodiscard]] bool supports_range() const noexcept override { return false; }

  void insert(const Key& key, RID rid) override;
  std::size_t remove(const Key& key) override;
  bool remove(const Key& key, RID rid) override;
  [[nodiscard]] std::vector<RID> search(const Key& key) override;

  /// Lanza siempre `Unsupported`. El planner debe consultar
  /// `supports_range()` antes.
  [[nodiscard]] std::vector<RID> range_search(const Key& lo, const Key& hi) override;

  /// Todas las entradas, en orden de bucket. No hay orden de clave que
  /// ofrecer, y el contrato de `Index::scan` ya lo dice.
  [[nodiscard]] std::vector<std::pair<Key, RID>> scan() override;
  [[nodiscard]] std::size_t size() const override { return hash_.size(); }

  [[nodiscard]] const OpStats& stats() const noexcept override { return hash_.stats(); }
  void reset_stats() noexcept override { hash_.reset_stats(); }

  // --- propios ------------------------------------------------------------

  [[nodiscard]] const Column& column() const noexcept { return column_; }

  /// Registros que tienen ese valor en la columna indexada: busca en el hash y
  /// resuelve cada puntero contra la tabla de datos.
  [[nodiscard]] std::vector<Record> lookup(const Key& key);

  /// Recorre la tabla entera y arma el indice desde cero. Es lo que se hace al
  /// crear un indice sobre una tabla que ya tiene datos.
  void build();

  [[nodiscard]] std::size_t global_depth() const noexcept { return hash_.global_depth(); }
  [[nodiscard]] std::size_t directory_size() const noexcept { return hash_.directory_size(); }
  [[nodiscard]] std::size_t bucket_count() const { return hash_.bucket_count(); }
  [[nodiscard]] std::size_t overflow_pages() { return hash_.overflow_pages(); }
  [[nodiscard]] std::uintmax_t file_size() const { return hash_.file_size(); }
  [[nodiscard]] std::string check_invariants() { return hash_.check_invariants(); }

  void flush() { hash_.flush(); }

 private:
  Column column_;
  std::size_t key_index_ = 0;  // posicion de la columna en el esquema de la tabla
  TableFile* data_;
  ExtendibleHash hash_;
};

}  // namespace quipudb
