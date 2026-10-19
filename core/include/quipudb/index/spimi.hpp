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
// Cuando se llena la memoria
// --------------------------
//
//   El "while (free memory available)" de la diapositiva necesita una medida
//   de memoria. Medir la del proceso no sirve: depende del asignador y del
//   sistema, y las pruebas no podrian fijar cuantos bloques salen. En su lugar
//   el diccionario lleva la cuenta de lo que reserva, de forma determinista:
//
//     termino nuevo        kSpimiEntryOverhead + sus bytes
//                          + la lista inicial de kSpimiInitialPostings postings
//     lista llena          se duplica (DoublePostingsList): cuesta lo que ya
//                          tenia, otra vez
//
//   Las listas se reservan exactamente asi, asi que la cuenta no es una
//   aproximacion de lo que hacen: es lo que hacen. La sobrecarga por entrada
//   si es una estimacion -- el nodo del hash, el std::string y el std::vector
//   de libstdc++ en 64 bits --, y es la misma en todas las corridas.
//
//   Antes de agregar un posting se mira si cabe. Si no, el bloque se vuelca
//   ANTES de agregarlo y el posting va al bloque siguiente: ningun bloque
//   supera el presupuesto. Eso puede partir un documento entre dos bloques
//   -- unos terminos en uno, otros en el siguiente --, y no estorba: cada
//   posting (termino, documento) queda entero en un solo bloque, y por
//   termino los documentos siguen creciendo de un bloque al siguiente.
//
// La longitud de cada documento (#163)
// ------------------------------------
//
//   BM25 necesita cuanto mide cada documento, y la unica vez que se ven los
//   documentos es esta pasada. La longitud es la cantidad de terminos que deja
//   el tokenizador, con repeticiones: sin stopwords y ya stemmeados, que es lo
//   que el ranking compara. Es igual a la suma de los tf de sus postings.
//
//   No se acumula en memoria: con 100 000 documentos seria una lista que
//   crece sin limite, por fuera del presupuesto. Se escribe de corrido a un
//   archivo propio, (u32 doc, u32 longitud) por documento, y como los
//   documentos llegan en orden queda ordenado por doc sin hacer nada. El
//   merge (#162) lo copia al indice. Su costo de memoria es una pagina,
//   fija, ademas del presupuesto del diccionario.
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

#include "quipudb/catalog/table.hpp"
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

/// Lo que cuesta una entrada del diccionario aparte de su termino y su lista:
/// el nodo del hash con su hash cacheado (16), el std::string (32) y el
/// std::vector (24), en libstdc++ de 64 bits.
inline constexpr std::size_t kSpimiEntryOverhead = 72;

/// Capacidad con la que nace una lista de postings. Al llenarse se duplica.
inline constexpr std::size_t kSpimiInitialPostings = 4;

/// Presupuesto por omision: 1 MiB. Con el corpus de 100 000 documentos del
/// 2.3.2 eso da varias decenas de bloques, que es justo lo que el merge de
/// #162 tiene que saber manejar.
inline constexpr std::size_t kSpimiDefaultMemory = std::size_t{1} << 20;

struct SpimiOptions {
  /// Bytes que puede ocupar el diccionario del bloque en construccion, segun
  /// la cuenta descrita arriba. Al no caber el siguiente posting, se vuelca el
  /// bloque.
  ///
  /// Bajarlo es lo que permite que las pruebas generen muchos bloques con
  /// pocos documentos, igual que el orden configurable del B+ Tree (#14). Lo
  /// minimo es lo que cuesta un termino nuevo del largo maximo que deja pasar
  /// el tokenizador: con menos, ese termino no cabria en ningun bloque.
  std::size_t memory_budget{kSpimiDefaultMemory};

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
  ///
  /// Lanza SchemaError si `options.memory_budget` es menor que
  /// `min_memory_budget(tokenizer)`.
  explicit SpimiIndexer(TokenizerOptions tokenizer = {}, SpimiOptions options = {});

  /// El presupuesto minimo con estas opciones de tokenizacion: un termino nuevo
  /// del largo maximo, con su lista inicial.
  [[nodiscard]] static std::size_t min_memory_budget(const TokenizerOptions& tokenizer) noexcept;
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

  /// Suma de las longitudes de todos los documentos: terminos que dejo el
  /// tokenizador, con repeticiones.
  [[nodiscard]] std::uint64_t total_length() const noexcept { return longitud_total_; }

  /// El archivo con la longitud de cada documento. Completo recien despues de
  /// `finish()`; el indexador lo borra al destruirse, igual que los bloques.
  [[nodiscard]] const std::filesystem::path& lengths_file() const noexcept {
    return longitudes_ruta_;
  }

  /// Si ya se llamo a `finish()`: recien ahi los bloques estan completos.
  [[nodiscard]] bool finished() const noexcept { return terminado_; }
  [[nodiscard]] const TextTokenizer& tokenizer() const noexcept { return tokenizer_; }
  [[nodiscard]] const SpimiOptions& options() const noexcept { return options_; }

  /// Lo maximo que llego a ocupar el diccionario, segun la cuenta. Nunca pasa
  /// de `options().memory_budget`.
  [[nodiscard]] std::size_t peak_memory() const noexcept { return pico_; }

  /// Bloques escritos. `OpStats` es el contrato comun de todas las
  /// estructuras y no tiene ese campo, asi que va aparte.
  [[nodiscard]] std::size_t blocks_written() const noexcept { return bloques_.size(); }

  /// Lo que costo la construccion:
  ///   - `pages_written`: paginas de datos de todos los bloques y del archivo
  ///     de longitudes;
  ///   - `pages_read`: 0, porque SPIMI no relee nada -- es una sola pasada,
  ///     de ahi el nombre. Lo que se lee de los bloques lo cuenta el merge;
  ///   - `records_examined`: documentos procesados;
  ///   - `records_returned`: postings escritos.
  [[nodiscard]] const OpStats& stats() const noexcept { return stats_; }

 private:
  /// Agrega un posting, volcando el bloque antes si no cabe.
  void agregar(std::string_view termino, Posting posting);
  void volcar_bloque();

  TextTokenizer tokenizer_;
  SpimiOptions options_;

  /// El diccionario LOCAL del bloque en construccion. Se vacia en cada
  /// volcado.
  std::unordered_map<std::string, std::vector<Posting>> diccionario_;
  /// Lo que ocupa `diccionario_`, segun la cuenta.
  std::size_t usado_ = 0;
  std::size_t pico_ = 0;
  OpStats stats_;

  std::vector<std::filesystem::path> bloques_;

  /// El archivo de longitudes y su escritor, abiertos hasta `finish()`. Van
  /// detras de un puntero porque el escritor es interno del core.
  struct Longitudes;
  std::filesystem::path longitudes_ruta_;
  std::unique_ptr<Longitudes> longitudes_;
  std::uint64_t longitud_total_ = 0;

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

  /// Paginas leidas hasta ahora y entradas devueltas (`records_returned`).
  /// Leer el bloque entero lee cada pagina exactamente una vez.
  [[nodiscard]] OpStats stats() const noexcept;

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
  std::uint64_t entradas_ = 0;
  std::uint64_t leidas_ = 0;
};

}  // namespace quipudb
