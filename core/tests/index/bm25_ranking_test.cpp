// Pruebas del ranking BM25 (issue #165).
//
// Los puntajes esperados se calcularon aparte, con la formula del header, sobre
// un corpus chico de longitudes muy distintas:
//
//   D1  "gato"                           |d| = 1
//   D2  "gato" + 9 veces "perro"          |d| = 10
//   D3  "perro"                          |d| = 1
//   D4  "pez"                            |d| = 1
//
//   N = 4, avgdl = 13 / 4 = 3.25
//
// Y para un corpus grande, contra un BM25 calculado por fuerza bruta desde el
// indice ingenuo de `texto_corpus.hpp`.

#include "quipudb/index/bm25_ranking.hpp"

#include <gtest/gtest.h>

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <map>
#include <string>
#include <vector>

#include "quipudb/error.hpp"
#include "quipudb/index/inverted_index.hpp"
#include "quipudb/index/spimi.hpp"
#include "quipudb/index/tfidf_ranking.hpp"
#include "texto_corpus.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

class RankingBm25 : public ::testing::Test {
 protected:
  void SetUp() override {
    dir_ = fs::temp_directory_path() /
           ("quipudb_bm25_test_" +
            std::string(::testing::UnitTest::GetInstance()->current_test_info()->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
  }
  void TearDown() override { fs::remove_all(dir_); }

  fs::path indexar(const std::vector<std::string>& docs, TokenizerOptions tok = {},
                   SpimiOptions opt = {}) {
    opt.dir = dir_;
    SpimiIndexer spimi(std::move(tok), opt);
    for (std::size_t i = 0; i < docs.size(); ++i) {
      spimi.add_document(static_cast<DocId>(i + 1), docs[i]);
    }
    spimi.finish();
    const fs::path ruta = dir_ / ("i" + std::to_string(n_++) + ".idx");
    InvertedIndex::build(spimi, ruta);
    return ruta;
  }

  fs::path dir_;
  int n_ = 0;
};

const std::vector<std::string> kLongitudes = {
    "gato",
    "gato perro perro perro perro perro perro perro perro perro",
    "perro",
    "pez",
};

constexpr double kTol = 1e-12;

TEST_F(RankingBm25, ElIdfEsSiemprePositivo) {
  // N = 4. La variante clasica daria 0.847, 0, -0.847 y -2.197.
  EXPECT_NEAR(bm25_idf(4, 1), 1.203972804326, kTol);
  EXPECT_NEAR(bm25_idf(4, 2), 0.693147180560, kTol);  // ln 2
  EXPECT_NEAR(bm25_idf(4, 3), 0.356674943939, kTol);
  EXPECT_NEAR(bm25_idf(4, 4), 0.105360515658, kTol);
  EXPECT_EQ(bm25_idf(4, 0), 0.0);
}

TEST_F(RankingBm25, ElDocumentoCortoGanaAlLargo) {
  // "gato" esta una vez en D1 (1 termino) y una vez en D2 (10 terminos): con
  // b = 0.75 el largo pesa, y D1 queda arriba.
  InvertedIndex indice(indexar(kLongitudes));
  Bm25Ranking bm25(indice);
  const auto r = bm25.search("gato", 10);
  ASSERT_EQ(r.size(), 2U);
  EXPECT_EQ(r[0].doc, 1U);
  EXPECT_NEAR(r[0].score, 0.967024847025, kTol);
  EXPECT_EQ(r[1].doc, 2U);
  EXPECT_NEAR(r[1].score, 0.374744978526, kTol);
}

TEST_F(RankingBm25, ElTfSatura) {
  // "perro" 9 veces en D2 contra 1 vez en D3: D2 gana, pero por poco, no por
  // 9 veces. El techo de cada termino es idf * (k1 + 1).
  InvertedIndex indice(indexar(kLongitudes));
  Bm25Ranking bm25(indice);
  const auto r = bm25.search("perro", 10);
  ASSERT_EQ(r.size(), 2U);
  EXPECT_EQ(r[0].doc, 2U);
  EXPECT_NEAR(r[0].score, 1.137132468299, kTol);
  EXPECT_EQ(r[1].doc, 3U);
  EXPECT_NEAR(r[1].score, 0.967024847025, kTol);
  EXPECT_LT(r[0].score, bm25_idf(4, 2) * (1.2 + 1.0));
}

TEST_F(RankingBm25, VariosTerminosSeSuman) {
  InvertedIndex indice(indexar(kLongitudes));
  Bm25Ranking bm25(indice);
  const auto r = bm25.search("gato perro", 10);
  ASSERT_EQ(r.size(), 3U);
  EXPECT_EQ(r[0].doc, 2U);
  EXPECT_NEAR(r[0].score, 0.374744978526 + 1.137132468299, kTol);
  // D1 y D3 empatan: cada uno tiene un termino una vez y mide 1. A igual
  // puntaje, por doc.
  EXPECT_EQ(r[1].doc, 1U);
  EXPECT_EQ(r[2].doc, 3U);
  EXPECT_EQ(r[1].score, r[2].score);
}

TEST_F(RankingBm25, LosParametrosSeEligen) {
  InvertedIndex indice(indexar(kLongitudes));

  // b = 0: la longitud no importa, D1 y D2 empatan en "gato".
  Bm25Ranking sin_largo(indice, Bm25Params{.b = 0.0});
  const auto r1 = sin_largo.search("gato", 10);
  ASSERT_EQ(r1.size(), 2U);
  EXPECT_NEAR(r1[0].score, 0.693147180560, kTol);
  EXPECT_EQ(r1[0].score, r1[1].score);

  // k1 = 0: solo importa si el termino esta; el puntaje es el idf.
  Bm25Ranking binario(indice, Bm25Params{.k1 = 0.0});
  const auto r2 = binario.search("perro", 10);
  ASSERT_EQ(r2.size(), 2U);
  EXPECT_NEAR(r2[0].score, 0.693147180560, kTol);
  EXPECT_EQ(r2[0].score, r2[1].score);

  // k1 = 2, b = 1: mas peso al largo.
  Bm25Ranking otro(indice, Bm25Params{.k1 = 2.0, .b = 1.0});
  const auto r3 = otro.search("gato", 10);
  ASSERT_EQ(r3.size(), 2U);
  EXPECT_NEAR(r3[0].score, 1.287273335326, kTol);
  EXPECT_NEAR(r3[1].score, 0.290674624106, kTol);
  EXPECT_EQ(otro.params().k1, 2.0);
}

TEST_F(RankingBm25, ParametrosFueraDeRango) {
  InvertedIndex indice(indexar(kLongitudes));
  EXPECT_THROW(Bm25Ranking(indice, Bm25Params{.k1 = -0.1}), SchemaError);
  EXPECT_THROW(Bm25Ranking(indice, Bm25Params{.b = 1.5}), SchemaError);
  EXPECT_THROW(Bm25Ranking(indice, Bm25Params{.b = -0.1}), SchemaError);
  EXPECT_THROW(Bm25Ranking(indice, Bm25Params{.k1 = NAN}), SchemaError);
  EXPECT_NO_THROW(Bm25Ranking(indice, Bm25Params{.k1 = 0.0, .b = 1.0}));
}

TEST_F(RankingBm25, UnTerminoComunTodaviaSuma) {
  // "casa" esta en 3 de 4 documentos. Con el idf clasico restaria; aqui suma
  // poco pero suma: el documento que tiene casa y sol supera al que solo tiene
  // sol.
  InvertedIndex indice(indexar({"casa sol", "casa", "casa luna", "sol"}));
  Bm25Ranking bm25(indice);
  const auto r = bm25.search("casa sol", 10);
  ASSERT_EQ(r.size(), 4U);
  for (const auto& e : r) EXPECT_GT(e.score, 0.0);
  EXPECT_EQ(r[0].doc, 1U);
  const auto sol_solo =
      std::find_if(r.begin(), r.end(), [](const auto& e) { return e.doc == 4U; });
  EXPECT_LT(sol_solo->score, r[0].score);

  // Y a diferencia de TF-IDF, un termino que esta en todos los documentos
  // todavia ordena.
  InvertedIndex todos(indexar({"casa", "casa casa", "casa grande y bonita"}));
  EXPECT_EQ(Bm25Ranking(todos).search("casa", 10).size(), 3U);
  EXPECT_TRUE(TfIdfRanking(todos).search("casa", 10).empty());
}

TEST_F(RankingBm25, MismaFirmaQueTfIdf) {
  // Los dos se usan a traves de TextRanking, que es lo que hara el parser.
  InvertedIndex indice(indexar(kLongitudes));
  Bm25Ranking bm25(indice);
  TfIdfRanking tfidf(indice);
  for (TextRanking* ranking : std::vector<TextRanking*>{&bm25, &tfidf}) {
    const auto r = ranking->search("gato", 1);
    ASSERT_EQ(r.size(), 1U) << ranking->name();
    EXPECT_EQ(r[0].doc, 1U) << ranking->name();
    EXPECT_EQ(ranking->stats().records_returned, 1U);
  }
  EXPECT_STREQ(bm25.name(), "bm25");
  EXPECT_STREQ(tfidf.name(), "tf_idf");
}

TEST_F(RankingBm25, BordesComoTfIdf) {
  InvertedIndex indice(indexar(kLongitudes));
  Bm25Ranking bm25(indice);
  EXPECT_TRUE(bm25.search("helicóptero", 10).empty());
  EXPECT_TRUE(bm25.search("", 10).empty());
  EXPECT_TRUE(bm25.search("el la de", 10).empty());
  EXPECT_TRUE(bm25.search("gato", 0).empty());
  EXPECT_EQ(bm25.search("gato perro pez", kAllResults).size(), 4U);
  EXPECT_EQ(bm25.search("GATOS", 10), bm25.search("gato", 10));  // tokenizada como los documentos
}

/// BM25 por fuerza bruta: recorre todos los documentos del oraculo.
std::map<DocId, double> fuerza_bruta(const std::vector<std::string>& docs,
                                     const std::string& consulta, Bm25Params p) {
  const auto indice = prueba::oraculo(docs, TokenizerOptions{});
  std::map<DocId, double> largo;
  double total = 0;
  for (const auto& [t, lista] : indice) {
    for (const Posting& q : lista) {
      largo[q.doc] += q.tf;
      total += q.tf;
    }
  }
  const double n = static_cast<double>(docs.size());
  const double avgdl = total / n;
  std::map<std::string, int> qtf;
  for (const auto& t : TextTokenizer().tokenize(consulta)) ++qtf[t];
  std::map<DocId, double> r;
  for (const auto& [t, veces] : qtf) {
    const auto it = indice.find(t);
    if (it == indice.end()) continue;
    const double df = static_cast<double>(it->second.size());
    const double i = std::log(1 + (n - df + 0.5) / (df + 0.5));
    for (const Posting& q : it->second) {
      const double tf = q.tf;
      r[q.doc] += veces * i * tf * (p.k1 + 1) /
                  (tf + p.k1 * (1 - p.b + p.b * largo[q.doc] / avgdl));
    }
  }
  return r;
}

TEST_F(RankingBm25, CoincideConBm25PorFuerzaBruta) {
  const auto docs = prueba::corpus(400);
  InvertedIndex indice(indexar(docs, TokenizerOptions{}, SpimiOptions{.memory_budget = 8192}));
  for (const Bm25Params p : {Bm25Params{}, Bm25Params{.k1 = 2.0, .b = 0.3},
                             Bm25Params{.k1 = 0.5, .b = 1.0}}) {
    Bm25Ranking bm25(indice, p);
    for (const char* consulta :
         {"w3", "w10 w50", "w0 w1 w250 w299", "w7 w7 w7 w100", "w0 w0 w5 w9 w12 w40 w77"}) {
      const auto esperado = fuerza_bruta(docs, consulta, p);
      const auto obtenido = bm25.search(consulta, kAllResults);
      ASSERT_EQ(obtenido.size(), esperado.size()) << consulta;
      for (const auto& o : obtenido) {
        ASSERT_TRUE(esperado.contains(o.doc)) << consulta;
        EXPECT_NEAR(o.score, esperado.at(o.doc), 1e-9) << consulta << " doc " << o.doc;
      }
      for (std::size_t i = 1; i < obtenido.size(); ++i) {
        EXPECT_GE(obtenido[i - 1].score, obtenido[i].score);
      }
      const auto top = bm25.search(consulta, 10);
      const std::size_t n = std::min<std::size_t>(10, obtenido.size());
      EXPECT_EQ(top, std::vector<ScoredDocument>(obtenido.begin(), obtenido.begin() + n));
    }
  }
}

TEST_F(RankingBm25, SoloRecorreLosPostingsDeLaConsulta) {
  const auto docs = prueba::corpus(400);
  InvertedIndex indice(indexar(docs));
  const auto oraculo = prueba::oraculo(docs, TokenizerOptions{});
  Bm25Ranking bm25(indice);
  const auto r = bm25.search("w10 w50 w123", 10);
  std::uint64_t postings = 0;
  for (const char* t : {"w10", "w50", "w123"}) postings += oraculo.at(t).size();
  EXPECT_EQ(bm25.stats().records_examined, postings);
  EXPECT_LT(postings, indice.postings());
  EXPECT_EQ(bm25.stats().records_returned, r.size());
}

}  // namespace
}  // namespace quipudb
