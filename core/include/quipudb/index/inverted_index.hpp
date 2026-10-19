#pragma once

// Indice invertido persistente: el merge de SPIMI (issue #162).
//
// Que es aqui
// -----------
//
//   La segunda mitad de SPIMI: mezclar los bloques que dejo el `SpimiIndexer`
//   (#161) en un solo indice en disco, y abrirlo despues para buscar. Es lo que
//   consultan los rankings TF-IDF (#164) y BM25 (#165).
//
// El merge: k-way, como el External Sorting
// -----------------------------------------
//
//   Es el MergeBlocks de la diapositiva de BSBI: se abren todos los bloques a
//   la vez, cada uno con UNA pagina en memoria, y una cola de prioridad va
//   sacando el termino mas chico entre los frentes. Las listas de ese termino
//   en los distintos bloques se concatenan en orden de bloque, y como los
//   documentos llegaron al SPIMI en orden creciente, la lista resultante ya
//   esta ordenada por docID: no hay que ordenar nada. El merge lo verifica de
//   todos modos y un bloque fuera de orden es un IoError.
//
//   Lo unico que se tiene en memoria a la vez es la entrada actual de cada
//   bloque y la lista del termino que se esta escribiendo.
//
// El archivo: postings, diccionario y un indice disperso
// ------------------------------------------------------
//
//   Un solo archivo de paginas con tres flujos encadenados que se escriben a
//   la vez durante el merge, por eso sus paginas quedan intercaladas:
//
//     postings     las listas, una detras de otra: (u32 doc, u32 tf) por
//                  posting, sin separadores. Una lista puede cruzar paginas.
//
//     diccionario  una entrada por termino, en orden:
//                  u16 largo | termino | u32 df | u32 pagina | u16 offset
//                  donde (pagina, offset) es donde empieza su lista. Las
//                  entradas NO cruzan paginas: cada pagina del diccionario se
//                  puede leer y recorrer sola.
//
//     disperso     el primer termino de cada pagina del diccionario, con su
//                  numero de pagina.
//
//   Es el "diccionario + buckets" de la diapositiva de SPIMI, con la
//   respuesta a su pregunta -- como localizar eficientemente el bloque de un
//   termino -- que propone la diapositiva siguiente: un nivel de indice
//   encima del diccionario, como el de un B+ Tree. Al abrir, el disperso se
//   carga en memoria (una entrada por pagina del diccionario, no por
//   termino); buscar un termino es una busqueda binaria ahi, UNA pagina del
//   diccionario, y las paginas de su lista.
//
//   Las opciones del tokenizador se guardan tambien, en un cuarto flujo: la
//   consulta tiene que tokenizarse igual que los documentos (#160), y el
//   indice es quien sabe como fueron tokenizados.
//
// Por que el resultado no depende de los bloques
// ----------------------------------------------
//
//   El archivo solo contiene el resultado de la mezcla, y la mezcla de los
//   bloques es la misma lista ordenada sin importar por donde se cortaron.
//   Construir el mismo corpus con 1 bloque o con 50 da el mismo archivo, byte
//   por byte; la prueba `ElArchivoNoDependeDeCuantosBloquesHubo` lo compara.

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <memory>
#include <string_view>
#include <vector>

#include "quipudb/catalog/table.hpp"
#include "quipudb/catalog/types.hpp"
#include "quipudb/index/spimi.hpp"
#include "quipudb/index/text_tokenizer.hpp"

namespace quipudb {

class InvertedIndex {
 public:
  /// Mezcla los bloques de `spimi` en un indice nuevo en `path`, con el tamano
  /// de pagina de los bloques. Devuelve lo que costo:
  ///   - `pages_read`: paginas de bloque leidas;
  ///   - `pages_written`: paginas escritas;
  ///   - `records_examined`: entradas de bloque leidas;
  ///   - `records_returned`: terminos del indice.
  ///
  /// El indice se escribe con otro nombre y se renombra al final: si algo
  /// falla a medias, en `path` no queda un indice a medio escribir.
  ///
  /// Lanza Unsupported si `spimi` no termino (`finish()`), IoError si `path`
  /// ya existe -- no se pisa un indice -- o si un bloque esta fuera de orden.
  static OpStats build(const SpimiIndexer& spimi, const std::filesystem::path& path);

  /// Abre un indice existente. Lanza IoError si no existe, si no es un indice
  /// invertido o si es de otra version del formato.
  explicit InvertedIndex(const std::filesystem::path& path,
                         std::size_t page_size = kDefaultPageSize);
  ~InvertedIndex();

  InvertedIndex(const InvertedIndex&) = delete;
  InvertedIndex& operator=(const InvertedIndex&) = delete;

  /// Terminos distintos.
  [[nodiscard]] std::uint64_t terms() const noexcept;

  /// Postings en total, sumando todas las listas.
  [[nodiscard]] std::uint64_t postings() const noexcept;

  /// Con que opciones se tokenizaron los documentos. La consulta tiene que
  /// usar las mismas.
  [[nodiscard]] const TokenizerOptions& tokenizer_options() const noexcept;

  /// En cuantos documentos aparece `term`; 0 si no esta. Lee una pagina del
  /// diccionario, o ninguna si el termino queda antes del primero.
  ///
  /// `term` es un termino ya tokenizado, no texto libre.
  [[nodiscard]] std::uint32_t document_frequency(std::string_view term);

  /// La lista de postings de `term`, ordenada por documento; vacia si no esta.
  [[nodiscard]] std::vector<Posting> postings(std::string_view term);

  /// Recorre todo el indice en orden de termino.
  void for_each(const std::function<void(const PostingEntry&)>& fn);

  /// Paginas leidas por las busquedas y postings devueltos
  /// (`records_returned`), desde que se abrio o desde el ultimo reset.
  [[nodiscard]] const OpStats& stats() const noexcept;
  void reset_stats() noexcept;

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace quipudb
