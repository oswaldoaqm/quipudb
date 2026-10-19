#pragma once

// Indice de texto sobre una columna de una tabla (issue #166).
//
// Que es aqui
// -----------
//
//   El adaptador que conecta el indice invertido de la Parte 3 -- SPIMI (#161,
//   #162), estadisticas (#163) y rankings (#164, #165) -- con el resto del
//   motor: el catalogo lo registra, `Database` lo crea y lo reabre, y el motor
//   Python lo consulta por los bindings. Es lo mismo que `RTreeIndex` hace con
//   el R-Tree.
//
// Donde vive el texto
// -------------------
//
//   En una columna VARCHAR de la tabla, como cualquier otro dato. El largo de
//   VARCHAR es fijo y el registro tiene que caber en una pagina, asi que un
//   documento largo pide una tabla con paginas mas grandes (hasta 64 KB). La
//   alternativa -- guardar en la tabla la ruta de un archivo con el texto --
//   queda para cuando un corpus no quepa; hoy no hace falta y obligaria a que
//   la tabla y los archivos se mantengan sincronizados a mano.
//
// Documentos y RIDs
// -----------------
//
//   El indice invertido trabaja con DocId densos; la tabla, con RIDs. Al
//   construir se recorre la tabla con su cursor y cada registro recibe el
//   siguiente DocId (1, 2, 3...). El mapeo DocId -> RID se guarda en un
//   archivo propio junto al indice, `<archivo>.rids`, y se carga al abrir.
//
// El contrato de `Index`, y lo que no aplica
// ------------------------------------------
//
//   Implementa `Index` para que el catalogo y `Database` lo manejen igual que
//   a los demas: crear, abrir, cerrar, borrar. La busqueda de verdad es
//   `search_text`. Las operaciones genericas que no tienen sentido lanzan
//   Unsupported con un mensaje que dice que usar:
//
//     insert, remove   el indice no se actualiza incrementalmente (#163): un
//                      documento nuevo cambia N, avgdl y df de todo. Se
//                      reconstruye con `build`.
//     search(key)      igualdad exacta sobre un texto no es lo que hace un
//                      indice de texto; es `search_text`.
//     range_search     no hay orden entre textos que sirva aqui.
//     scan             el indice no guarda pares (texto, RID).

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <optional>
#include <string_view>
#include <utility>
#include <vector>

#include "quipudb/catalog/table.hpp"
#include "quipudb/index/bm25_ranking.hpp"
#include "quipudb/index/inverted_index.hpp"
#include "quipudb/index/text_tokenizer.hpp"
#include "quipudb/index/text_weights.hpp"

namespace quipudb {

/// Con que ranking se busca. Es lo que el SQL elige con `USING` (#168).
enum class TextRankingKind : std::uint8_t {
  kTfIdf,
  kBm25,
};

[[nodiscard]] constexpr const char* name_of(TextRankingKind r) noexcept {
  return r == TextRankingKind::kBm25 ? "bm25" : "tf_idf";
}

class TextIndex final : public Index {
 public:
  /// Abre el indice si `path` existe; si no, queda vacio hasta `build()`.
  /// `column` tiene que ser VARCHAR.
  TextIndex(std::filesystem::path path, Column column, TableFile& data,
            std::size_t page_size = kDefaultPageSize);
  ~TextIndex() override;

  [[nodiscard]] std::string_view kind() const noexcept override {
    return kind::kInvertedIndex;
  }
  [[nodiscard]] DataType key_type() const noexcept override { return DataType::Varchar; }
  [[nodiscard]] bool supports_range() const noexcept override { return false; }

  void insert(const Key& key, RID rid) override;
  std::size_t remove(const Key& key) override;
  bool remove(const Key& key, RID rid) override;
  [[nodiscard]] std::vector<RID> search(const Key& key) override;
  [[nodiscard]] std::vector<RID> range_search(const Key& lo, const Key& hi) override;
  [[nodiscard]] std::vector<std::pair<Key, RID>> scan() override;

  /// Documentos indexados: los registros que tenia la tabla al construir.
  [[nodiscard]] std::size_t size() const override;
  [[nodiscard]] const OpStats& stats() const noexcept override;
  void reset_stats() noexcept override;

  /// Construye (o reconstruye) el indice recorriendo la tabla. Es lo que hay
  /// que llamar despues de cambiar la tabla, y tambien la forma de cambiar el
  /// idioma o las stopwords. Si algo falla, el indice anterior queda intacto.
  void build(const TokenizerOptions& tokenizer = {});

  /// Los `k` registros mas relevantes para `query`, con su puntaje, de mayor a
  /// menor. Mismo contrato que `TextRanking::search`. Lanza Unsupported si el
  /// indice nunca se construyo.
  [[nodiscard]] std::vector<std::pair<RID, double>> search_text(std::string_view query,
                                                                TextRankingKind ranking,
                                                                std::size_t k);

  /// Las opciones con que se tokenizo. La consulta usa las mismas.
  [[nodiscard]] const TokenizerOptions& tokenizer_options() const;

  /// Terminos distintos del indice; 0 si no se construyo.
  [[nodiscard]] std::uint64_t terms() const noexcept;

  /// El indice invertido de abajo, para quien necesite sus estadisticas (el
  /// benchmark del 2.3.2). Lanza Unsupported si nunca se construyo.
  [[nodiscard]] InvertedIndex& inverted_index();

  /// Ruta del archivo de RIDs que acompana a un indice de texto en `path`.
  [[nodiscard]] static std::filesystem::path rids_path(const std::filesystem::path& path);

  /// No hay nada pendiente en memoria: el indice se escribe entero al
  /// construir. Existe para que `Database::flush` lo trate como a los demas.
  void flush() noexcept {}

 private:
  void abrir();
  [[noreturn]] void no_aplica(const char* operacion) const;
  [[nodiscard]] TextRanking& ranking(TextRankingKind r);

  std::filesystem::path path_;
  Column column_;
  std::size_t key_index_ = 0;
  TableFile* data_;
  std::size_t page_size_;

  std::unique_ptr<InvertedIndex> indice_;
  /// RID del documento d en la posicion d - 1.
  std::vector<RID> rids_;
  std::unique_ptr<TextRanking> tfidf_;
  std::unique_ptr<TextRanking> bm25_;
  OpStats stats_;
};

}  // namespace quipudb
