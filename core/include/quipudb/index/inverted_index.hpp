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
//   Abrir todos los bloques tiene un limite: cada uno es un archivo abierto y
//   una pagina de memoria. Con un presupuesto chico y un corpus grande salen
//   cientos de bloques, mas de los que el sistema deja abrir. Por eso se
//   mezclan a lo sumo `fan_in` a la vez: si hay mas, se mezclan por grupos en
//   bloques intermedios -- los MergeBlocks (L1), (L2) de la diapositiva -- y
//   se repite hasta que quedan `fan_in` o menos, que van al indice. Es la
//   misma cuenta de pasadas del External Sorting (#20): ceil(log_k(bloques)).
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

/// Cuantos bloques se mezclan a la vez por omision: 64 archivos abiertos y 64
/// paginas de lectura, 256 KB con paginas de 4 KB. Es el mismo numero de
/// buffers que usa el External Sorting por omision.
inline constexpr std::size_t kSpimiDefaultFanIn = 64;

/// Lo que costo construir un indice.
struct InvertedIndexBuild {
  /// - `pages_read`: paginas de bloque leidas, intermedios incluidos;
  /// - `pages_written`: paginas escritas, intermedios incluidos;
  /// - `records_examined`: entradas de bloque leidas;
  /// - `records_returned`: terminos del indice.
  OpStats stats;
  /// Bloques que dejo el SPIMI.
  std::size_t blocks = 0;
  /// Pasadas de mezcla: 1 si los bloques cabian en una sola, mas los niveles
  /// intermedios si no.
  std::size_t passes = 0;
};

class InvertedIndex {
 public:
  /// Mezcla los bloques de `spimi` en un indice nuevo en `path`, con el tamano
  /// de pagina de los bloques, abriendo a lo sumo `fan_in` bloques a la vez.
  ///
  /// El indice se escribe con otro nombre y se renombra al final: si algo
  /// falla a medias, en `path` no queda un indice a medio escribir. Los
  /// bloques intermedios se borran al terminar, falle o no.
  ///
  /// Lanza SchemaError si `fan_in` es menor que 2, Unsupported si `spimi` no
  /// termino (`finish()`), IoError si `path` ya existe -- no se pisa un
  /// indice -- o si un bloque esta fuera de orden.
  static InvertedIndexBuild build(const SpimiIndexer& spimi, const std::filesystem::path& path,
                                  std::size_t fan_in = kSpimiDefaultFanIn);

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
