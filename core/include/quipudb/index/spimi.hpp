#pragma once

// SPIMI: construccion del indice invertido por bloques (issue #161).
//
// Que es aqui
// -----------
//
//   La primera mitad del Single-Pass In-Memory Indexing del 2.3.1: recorrer
//   los documentos UNA vez, acumulando en memoria un diccionario
//   termino -> lista de postings, y escribirlo a disco como un bloque ordenado
//   por termino. La segunda mitad, mezclar los bloques en el indice final, es
//   el #162.
//
//   Es el SPIMI-INVERT de la diapositiva:
//
//     dictionary <- NewHash()
//     while (free memory available)
//       token <- next(token_stream)
//       postings_list <- AddToDictionary o GetPostingsList
//       AddToPostingsList(postings_list, docID(token))
//     sorted_terms <- SortTerms(dictionary)
//     WriteBlockToDisk(sorted_terms, dictionary, output_file)
//
// Por que SPIMI y no BSBI
// -----------------------
//
//   BSBI necesita un diccionario GLOBAL termino -> termID que viva en memoria
//   durante toda la construccion, y ordena pares (termID, docID). SPIMI no:
//   cada bloque arma su propio diccionario con los terminos tal cual, lo
//   ordena una sola vez al volcarlo y lo descarta. Por eso un mismo termino
//   aparece en varios bloques, y por eso la memoria queda acotada por el
//   tamano del bloque y no por el vocabulario de toda la coleccion.
//
// Los documentos llegan en orden
// ------------------------------
//
//   Los ids de documento tienen que ser estrictamente crecientes. Con eso las
//   listas de postings salen ordenadas por docID sin ordenar nada: cada
//   documento nuevo se agrega al final. Es la propiedad que la diapositiva del
//   indice invertido marca como crucial -- intersecciones en O(n + m) -- y la
//   que hace que mezclar bloques sea concatenar listas en vez de ordenarlas.
//
// El formato de un bloque
// -----------------------
//
//   Un bloque es un archivo de paginas (`DiskManager`). Su area meta guarda
//   cuantas entradas y bytes tiene; las paginas de datos son un flujo de
//   entradas de corrido, ordenadas por termino:
//
//     u16  largo del termino en bytes
//     ...  el termino, en UTF-8
//     u32  cantidad de postings
//     ...  los postings: (u32 doc, u32 tf) cada uno, por doc creciente
//
//   Una entrada puede cruzar paginas: la lista de "casa" en 100 000
//   documentos no cabe en una sola. Quien lee necesita una sola pagina en
//   memoria por bloque, que es lo que el merge de #162 usa para abrir todos
//   los bloques a la vez.

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

#include "quipudb/catalog/types.hpp"
#include "quipudb/index/text_tokenizer.hpp"
#include "quipudb/storage/disk_manager.hpp"

namespace quipudb {

/// Identificador de un documento dentro del indice de texto. Es un numero
/// denso asignado en orden de llegada, no un RID: con numeros densos las
/// longitudes de documento (#163) caben en un arreglo.
using DocId = std::uint32_t;

/// Una aparicion de un termino: en que documento y cuantas veces.
struct Posting {
  DocId doc = 0;
  std::uint32_t tf = 0;

  bool operator==(const Posting&) const = default;
};

/// Un termino y su lista de postings, ordenada por documento.
struct PostingEntry {
  std::string term;
  std::vector<Posting> postings;

  bool operator==(const PostingEntry&) const = default;
};

struct SpimiOptions {
  /// Tamano de pagina de los archivos de bloque.
  std::size_t page_size{kDefaultPageSize};

  /// Donde se escriben los bloques. Vacio es el directorio temporal del
  /// sistema.
  std::filesystem::path dir{};
};

/// Recorre los documentos y produce los bloques de SPIMI.
///
/// Es de un solo uso: se agregan los documentos, se llama a `finish()` y se
/// leen los bloques. Es el dueno de los archivos de bloque y los borra al
/// destruirse: son intermedios, lo que queda es el indice que arma el #162.
class SpimiIndexer {
 public:
  /// `tokenizer` decide que terminos salen de cada documento. Tiene que ser el
  /// mismo con el que despues se tokenicen las consultas.
  explicit SpimiIndexer(TokenizerOptions tokenizer = {}, SpimiOptions options = {});
  ~SpimiIndexer();

  SpimiIndexer(const SpimiIndexer&) = delete;
  SpimiIndexer& operator=(const SpimiIndexer&) = delete;

  /// Tokeniza `text` y agrega sus terminos, con la frecuencia de cada uno.
  ///
  /// Un documento sin terminos -- vacio, o todo stopwords -- cuenta como
  /// documento pero no agrega postings: el ranking necesita saber que existe
  /// para el total N.
  ///
  /// Lanza InvalidRecord si `doc` no es mayor que el ultimo agregado, y
  /// Unsupported si ya se llamo a `finish()`.
  void add_document(DocId doc, std::string_view text);

  /// Vuelca lo que quede en memoria como ultimo bloque. Despues de esto no se
  /// agregan mas documentos.
  void finish();

  /// Los archivos de bloque escritos, en orden. Cada uno tiene documentos
  /// mayores que el anterior.
  [[nodiscard]] const std::vector<std::filesystem::path>& blocks() const noexcept {
    return bloques_;
  }

  [[nodiscard]] std::size_t documents() const noexcept { return documentos_; }
  [[nodiscard]] const TextTokenizer& tokenizer() const noexcept { return tokenizer_; }
  [[nodiscard]] const SpimiOptions& options() const noexcept { return options_; }

 private:
  void volcar_bloque();

  TextTokenizer tokenizer_;
  SpimiOptions options_;

  /// El diccionario LOCAL del bloque en construccion. Se vacia en cada
  /// volcado.
  std::unordered_map<std::string, std::vector<Posting>> diccionario_;

  std::vector<std::filesystem::path> bloques_;
  std::size_t documentos_ = 0;
  bool hay_documento_ = false;
  DocId ultimo_ = 0;
  bool terminado_ = false;
};

/// Lee un bloque de SPIMI entrada por entrada, con una pagina en memoria.
class SpimiBlockReader {
 public:
  /// Lanza IoError si el archivo no es un bloque de SPIMI o es de otra
  /// version del formato.
  explicit SpimiBlockReader(const std::filesystem::path& path,
                            std::size_t page_size = kDefaultPageSize);
  ~SpimiBlockReader();

  SpimiBlockReader(const SpimiBlockReader&) = delete;
  SpimiBlockReader& operator=(const SpimiBlockReader&) = delete;

  /// La siguiente entrada, en orden de termino. false cuando no quedan.
  bool next(PostingEntry& out);

  /// Entradas del bloque, segun su area meta.
  [[nodiscard]] std::uint64_t entries() const noexcept { return entradas_; }

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
  std::uint64_t entradas_ = 0;
  std::uint64_t leidas_ = 0;
};

}  // namespace quipudb
