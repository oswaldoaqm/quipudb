// Pruebas de las estadisticas del indice invertido (issue #163).
//
// Los valores esperados se calcularon a mano sobre corpus de pocos
// documentos: los cuatro de la diapositiva de Bag of Words y los tres del
// ejemplo de pesos TF-IDF, cuya tabla de df trae la propia diapositiva.

#include <gtest/gtest.h>

#include <cstdint>
#include <filesystem>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include "quipudb/index/inverted_index.hpp"
#include "quipudb/index/spimi.hpp"
#include "texto_corpus.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

class EstadisticasTexto : public ::testing::Test {
 protected:
  void SetUp() override {
    dir_ = fs::temp_directory_path() /
           ("quipudb_stats_test_" +
            std::string(::testing::UnitTest::GetInstance()->current_test_info()->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
  }
  void TearDown() override { fs::remove_all(dir_); }

  /// Indexa los documentos con los ids dados y devuelve la ruta del indice.
  fs::path indexar(const std::vector<std::pair<DocId, std::string>>& docs,
                   TokenizerOptions tok = {}, SpimiOptions opt = {}) {
    opt.dir = dir_;
    SpimiIndexer spimi(std::move(tok), opt);
    for (const auto& [id, texto] : docs) spimi.add_document(id, texto);
    spimi.finish();
    const fs::path ruta = dir_ / ("i" + std::to_string(n_++) + ".idx");
    InvertedIndex::build(spimi, ruta);
    return ruta;
  }

  fs::path dir_;
  int n_ = 0;
};

TokenizerOptions sin_stemmer() { return TokenizerOptions{.stemmer = std::nullopt}; }

TEST_F(EstadisticasTexto, LosDocumentosDeLaDiapositivaDeBagOfWords) {
  // Despues de quitar stopwords:
  //   1 "La casa es grande"            -> casa grande          2
  //   2 "El gato está en la casa"      -> gato casa            2
  //   3 "La casa es bonita y grande"   -> casa bonita grande   3
  //   4 "El sol brilla sobre la casa"  -> sol brilla casa      3
  InvertedIndex indice(indexar({{1, "La casa es grande"},
                                {2, "El gato está en la casa"},
                                {3, "La casa es bonita y grande"},
                                {4, "El sol brilla sobre la casa"}},
                               sin_stemmer()));
  EXPECT_EQ(indice.documents(), 4U);
  EXPECT_EQ(indice.total_length(), 10U);
  EXPECT_DOUBLE_EQ(indice.average_document_length(), 2.5);
  EXPECT_EQ(indice.document_length(1), 2U);
  EXPECT_EQ(indice.document_length(2), 2U);
  EXPECT_EQ(indice.document_length(3), 3U);
  EXPECT_EQ(indice.document_length(4), 3U);

  const std::map<std::string, std::uint32_t> df = {{"casa", 4}, {"grande", 2}, {"gato", 1},
                                                   {"bonita", 1}, {"sol", 1}, {"brilla", 1}};
  for (const auto& [termino, esperado] : df) {
    EXPECT_EQ(indice.document_frequency(termino), esperado) << termino;
  }
}

TEST_F(EstadisticasTexto, LaTablaDeDfDelEjemploTfIdf) {
  // D1 "Cargamento de oro dañado por el fuego"
  // D2 "La entrega de la plata llegó en el camión color plata"
  // D3 "El cargamento de oro llegó en un camión"
  // La columna df_t de la tabla de la diapositiva, y N = 3.
  InvertedIndex indice(indexar({{1, "Cargamento de oro dañado por el fuego"},
                                {2, "La entrega de la plata llegó en el camión color plata"},
                                {3, "El cargamento de oro llegó en un camión"}},
                               sin_stemmer()));
  EXPECT_EQ(indice.documents(), 3U);
  const std::map<std::string, std::uint32_t> df = {
      {"llego", 2}, {"dañado", 1}, {"entrega", 1},    {"fuego", 1}, {"oro", 2},
      {"plata", 1}, {"cargamento", 2}, {"camion", 2}, {"color", 1}};
  for (const auto& [termino, esperado] : df) {
    EXPECT_EQ(indice.document_frequency(termino), esperado) << termino;
  }
  EXPECT_EQ(indice.terms(), df.size());

  // Longitudes: D1 tiene 4 terminos; D2, 6 (plata dos veces); D3, 4.
  EXPECT_EQ(indice.document_length(1), 4U);
  EXPECT_EQ(indice.document_length(2), 6U);
  EXPECT_EQ(indice.document_length(3), 4U);
  EXPECT_DOUBLE_EQ(indice.average_document_length(), 14.0 / 3.0);
}

TEST_F(EstadisticasTexto, LosDocumentosSinTerminosCuentanEnN) {
  // Un documento vacio o de solo stopwords no esta en ninguna lista de
  // postings, pero existe: cuenta en N y en el promedio, con longitud 0.
  InvertedIndex indice(indexar({{5, "sol sol luna"}, {9, ""}, {12, "de la que"}, {20, "luna"}}));
  EXPECT_EQ(indice.documents(), 4U);
  EXPECT_EQ(indice.total_length(), 4U);
  EXPECT_DOUBLE_EQ(indice.average_document_length(), 1.0);
  EXPECT_EQ(indice.document_length(5), 3U);
  EXPECT_EQ(indice.document_length(9), 0U);
  EXPECT_EQ(indice.document_length(12), 0U);
  EXPECT_EQ(indice.document_length(20), 1U);
  // Ids que no estan, entre los que si y fuera del rango.
  for (const DocId ausente : {0U, 6U, 13U, 21U}) {
    EXPECT_EQ(indice.document_length(ausente), std::nullopt) << ausente;
  }
}

TEST_F(EstadisticasTexto, UnIndiceSinDocumentos) {
  InvertedIndex indice(indexar({}));
  EXPECT_EQ(indice.documents(), 0U);
  EXPECT_EQ(indice.total_length(), 0U);
  EXPECT_EQ(indice.average_document_length(), 0.0);
  EXPECT_EQ(indice.document_length(1), std::nullopt);
}

TEST_F(EstadisticasTexto, CoincidenConElCorpusEnteroAunqueHayaMuchosBloques) {
  const auto docs = prueba::corpus(300);
  std::vector<std::pair<DocId, std::string>> con_id;
  for (std::size_t i = 0; i < docs.size(); ++i) con_id.emplace_back(DocId(i + 1), docs[i]);
  InvertedIndex indice(indexar(con_id, TokenizerOptions{}, SpimiOptions{.memory_budget = 8192}));

  // Del oraculo: la longitud de cada documento es la suma de sus tf.
  std::map<DocId, std::uint32_t> longitud;
  std::uint64_t total = 0;
  for (const auto& [termino, lista] : prueba::oraculo(docs, TokenizerOptions{})) {
    EXPECT_EQ(indice.document_frequency(termino), lista.size()) << termino;
    for (const Posting& p : lista) {
      longitud[p.doc] += p.tf;
      total += p.tf;
    }
  }
  EXPECT_EQ(indice.documents(), docs.size());
  EXPECT_EQ(indice.total_length(), total);
  EXPECT_DOUBLE_EQ(indice.average_document_length(),
                   static_cast<double>(total) / static_cast<double>(docs.size()));
  for (DocId d = 1; d <= docs.size(); ++d) {
    EXPECT_EQ(indice.document_length(d), longitud[d]) << d;
  }
}

TEST_F(EstadisticasTexto, PersistenAlReabrir) {
  const auto ruta = indexar({{1, "sol y luna"}, {2, ""}, {3, "sol sol sol"}});
  {
    InvertedIndex indice(ruta);
    EXPECT_EQ(indice.documents(), 3U);
  }
  InvertedIndex indice(ruta);
  EXPECT_EQ(indice.documents(), 3U);
  EXPECT_EQ(indice.total_length(), 5U);
  EXPECT_EQ(indice.document_length(1), 2U);
  EXPECT_EQ(indice.document_length(2), 0U);
  EXPECT_EQ(indice.document_length(3), 3U);
  EXPECT_EQ(indice.document_frequency("sol"), 2U);
}

TEST_F(EstadisticasTexto, LasLongitudesSeCarganUnaSolaVez) {
  const auto docs = prueba::corpus(2000);
  std::vector<std::pair<DocId, std::string>> con_id;
  for (std::size_t i = 0; i < docs.size(); ++i) con_id.emplace_back(DocId(i + 1), docs[i]);
  InvertedIndex indice(indexar(con_id));

  ASSERT_TRUE(indice.document_length(1).has_value());
  // 2000 documentos de 8 bytes ocupan 4 paginas de 4 KB.
  EXPECT_EQ(indice.stats().pages_read, 4U);
  indice.reset_stats();
  for (DocId d = 1; d <= 2000; ++d) ASSERT_TRUE(indice.document_length(d).has_value());
  EXPECT_EQ(indice.stats().pages_read, 0U);
}

}  // namespace
}  // namespace quipudb
