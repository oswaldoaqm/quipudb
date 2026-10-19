// Pruebas del ranking TF-IDF con similitud del coseno (issue #164).
//
// Dos oraculos:
//
//   1. El ejemplo resuelto de la diapositiva de pesos TF-IDF: tres documentos,
//      la consulta "oro plata camion" y sus tres similitudes. Con tf crudo se
//      reproducen las de la diapositiva. La de D1 aparece alli como 0,00801,
//      pero el valor es 0,0801: el producto escalar es 0,1761^2 = 0,0310, y
//      0,0310 / (0,5382 * 0,7192) = 0,0801. Es una errata del cero de mas.
//
//   2. Un coseno calculado por fuerza bruta: los vectores TF-IDF completos de
//      todos los documentos, armados desde el indice ingenuo de
//      `texto_corpus.hpp`, y el coseno contra cada uno. No usa ni las normas
//      guardadas ni las listas de postings, asi que confirma las dos cosas.

#include "quipudb/index/tfidf_ranking.hpp"

#include <gtest/gtest.h>

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <map>
#include <string>
#include <vector>

#include "quipudb/index/inverted_index.hpp"
#include "quipudb/index/spimi.hpp"
#include "texto_corpus.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

class RankingTfIdf : public ::testing::Test {
 protected:
  void SetUp() override {
    dir_ = fs::temp_directory_path() /
           ("quipudb_tfidf_test_" +
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

const std::vector<std::string> kDiapositiva = {
    "Cargamento de oro dañado por el fuego",
    "La entrega de la plata llegó en el camión color plata",
    "El cargamento de oro llegó en un camión",
};

TokenizerOptions sin_stemmer() { return TokenizerOptions{.stemmer = std::nullopt}; }

TEST_F(RankingTfIdf, ReproduceElEjemploDeLaDiapositiva) {
  InvertedIndex indice(indexar(kDiapositiva, sin_stemmer()));
  TfIdfRanking tfidf(indice, TfWeight::kRaw);
  const auto r = tfidf.search("oro plata camión", 10);

  // "Resultados en orden de relevancia: D2, D3, D1".
  ASSERT_EQ(r.size(), 3U);
  EXPECT_EQ(r[0].doc, 2U);
  EXPECT_EQ(r[1].doc, 3U);
  EXPECT_EQ(r[2].doc, 1U);
  // Con los cuatro decimales de la diapositiva...
  EXPECT_NEAR(r[0].score, 0.7561, 1e-4);
  EXPECT_NEAR(r[1].score, 0.3272, 1e-4);
  EXPECT_NEAR(r[2].score, 0.0801, 1e-4);
  // ...y con todos los decimales, calculados aparte.
  EXPECT_NEAR(r[0].score, 0.756154506695, 1e-12);
  EXPECT_NEAR(r[1].score, 0.327184574214, 1e-12);
  EXPECT_NEAR(r[2].score, 0.080104517540, 1e-12);
}

TEST_F(RankingTfIdf, ConTfLogaritmico) {
  // Lo unico que cambia es plata en D2: tf = 2 pesa 1 + log10(2) en vez de 2.
  InvertedIndex indice(indexar(kDiapositiva, sin_stemmer()));
  TfIdfRanking tfidf(indice);
  EXPECT_EQ(tfidf.tf(), TfWeight::kLog);
  const auto r = tfidf.search("oro plata camión", 10);
  ASSERT_EQ(r.size(), 3U);
  EXPECT_EQ(r[0], (ScoredDocument{2, r[0].score}));
  EXPECT_NEAR(r[0].score, 0.639862424655, 1e-12);
  EXPECT_NEAR(r[1].score, 0.327184574214, 1e-12);
  EXPECT_NEAR(r[2].score, 0.080104517540, 1e-12);
}

TEST_F(RankingTfIdf, CortaEnKODevuelveLosQueHaya) {
  InvertedIndex indice(indexar(kDiapositiva, sin_stemmer()));
  TfIdfRanking tfidf(indice);
  EXPECT_EQ(tfidf.search("oro plata camión", 1).size(), 1U);
  EXPECT_EQ(tfidf.search("oro plata camión", 1)[0].doc, 2U);
  EXPECT_EQ(tfidf.search("oro plata camión", 2).size(), 2U);
  EXPECT_EQ(tfidf.search("oro plata camión", 50).size(), 3U);
  EXPECT_EQ(tfidf.search("oro plata camión", kAllResults).size(), 3U);
  EXPECT_TRUE(tfidf.search("oro plata camión", 0).empty());
  // "fuego" solo esta en D1: hay un solo documento relevante.
  EXPECT_EQ(tfidf.search("fuego", 10).size(), 1U);
}

TEST_F(RankingTfIdf, SinTerminosDelIndiceDevuelveVacio) {
  InvertedIndex indice(indexar(kDiapositiva, sin_stemmer()));
  TfIdfRanking tfidf(indice);
  EXPECT_TRUE(tfidf.search("helicóptero submarino", 10).empty());
  EXPECT_TRUE(tfidf.search("", 10).empty());
  EXPECT_TRUE(tfidf.search("el de la por", 10).empty());  // solo stopwords
  EXPECT_TRUE(tfidf.search("  ¿?  ", 10).empty());
}

TEST_F(RankingTfIdf, UnTerminoQueEstaEnTodosNoPesa) {
  // "casa" esta en los tres: idf 0. La consulta "casa" no distingue a nadie,
  // y en "casa sol" solo cuenta sol.
  InvertedIndex indice(indexar({"casa sol", "casa", "casa luna"}, sin_stemmer()));
  TfIdfRanking tfidf(indice);
  EXPECT_TRUE(tfidf.search("casa", 10).empty());
  const auto r = tfidf.search("casa sol", 10);
  ASSERT_EQ(r.size(), 1U);
  EXPECT_EQ(r[0].doc, 1U);
  EXPECT_NEAR(r[0].score, 1.0, 1e-12);  // el unico termino que pesa coincide
  EXPECT_EQ(tfidf.stats().records_examined, 1U);  // la lista de casa ni se lee
}

TEST_F(RankingTfIdf, LaConsultaSeTokenizaComoLosDocumentos) {
  // Con el stemmer por defecto: "cargamentos" da la raiz de "cargamento", y
  // las mayusculas y tildes no importan.
  InvertedIndex indice(indexar(kDiapositiva));
  TfIdfRanking tfidf(indice);
  const auto plural = tfidf.search("CARGAMENTOS", 10);
  const auto singular = tfidf.search("cargamento", 10);
  ASSERT_EQ(plural.size(), 2U);
  EXPECT_EQ(plural, singular);
}

TEST_F(RankingTfIdf, AIgualPuntajeOrdenaPorDocumento) {
  InvertedIndex indice(indexar({"sol luna", "mar", "sol luna", "sol luna", "rio"}, sin_stemmer()));
  TfIdfRanking tfidf(indice);
  const auto r = tfidf.search("sol", 10);
  ASSERT_EQ(r.size(), 3U);
  EXPECT_EQ(r[0].doc, 1U);
  EXPECT_EQ(r[1].doc, 3U);
  EXPECT_EQ(r[2].doc, 4U);
  EXPECT_EQ(r[0].score, r[2].score);
}

/// El coseno por fuerza bruta: arma todos los vectores y compara con todos.
std::vector<ScoredDocument> fuerza_bruta(const std::vector<std::string>& docs,
                                         const std::string& consulta, TfWeight tf) {
  const auto indice = prueba::oraculo(docs, TokenizerOptions{});
  std::map<DocId, std::map<std::string, double>> vectores;
  for (const auto& [t, lista] : indice) {
    const double i = idf(docs.size(), lista.size());
    for (const Posting& p : lista) vectores[p.doc][t] = tf_weight(p.tf, tf) * i;
  }
  std::map<std::string, std::uint32_t> tf_q;
  for (const auto& t : TextTokenizer().tokenize(consulta)) ++tf_q[t];
  std::map<std::string, double> q;
  for (const auto& [t, f] : tf_q) {
    const auto it = indice.find(t);
    if (it != indice.end()) q[t] = tf_weight(f, tf) * idf(docs.size(), it->second.size());
  }
  const auto norma = [](const std::map<std::string, double>& v) {
    double s = 0;
    for (const auto& [t, w] : v) s += w * w;
    return std::sqrt(s);
  };
  std::vector<ScoredDocument> r;
  for (const auto& [doc, v] : vectores) {
    double punto = 0;
    for (const auto& [t, w] : q) {
      const auto it = v.find(t);
      if (it != v.end()) punto += w * it->second;
    }
    if (punto > 0) r.push_back({doc, punto / (norma(q) * norma(v))});
  }
  return r;
}

TEST_F(RankingTfIdf, CoincideConElCosenoPorFuerzaBruta) {
  const auto docs = prueba::corpus(400);
  InvertedIndex indice(indexar(docs, TokenizerOptions{}, SpimiOptions{.memory_budget = 8192}));
  for (const TfWeight tf : {TfWeight::kLog, TfWeight::kRaw}) {
    TfIdfRanking tfidf(indice, tf);
    for (const char* consulta : {"w3", "w10 w50", "w1 w2 w250 w299", "w7 w7 w7 w100 w180",
                                 "w0 w0 w5 w9 w12 w40 w77"}) {
      auto esperado = fuerza_bruta(docs, consulta, tf);
      const auto obtenido = tfidf.search(consulta, kAllResults);
      ASSERT_EQ(obtenido.size(), esperado.size()) << consulta;

      // Los mismos documentos con los mismos puntajes.
      std::map<DocId, double> por_doc;
      for (const auto& e : esperado) por_doc[e.doc] = e.score;
      for (const auto& o : obtenido) {
        ASSERT_TRUE(por_doc.contains(o.doc)) << consulta << " doc " << o.doc;
        EXPECT_NEAR(o.score, por_doc[o.doc], 1e-12) << consulta << " doc " << o.doc;
      }
      // Ordenados de mayor a menor.
      for (std::size_t i = 1; i < obtenido.size(); ++i) {
        EXPECT_GE(obtenido[i - 1].score, obtenido[i].score) << consulta;
      }
      // Y el top-10 es un prefijo del resultado completo.
      const auto top = tfidf.search(consulta, 10);
      const std::size_t n = std::min<std::size_t>(10, obtenido.size());
      EXPECT_EQ(top, std::vector<ScoredDocument>(obtenido.begin(), obtenido.begin() + n))
          << consulta;
    }
  }
}

TEST_F(RankingTfIdf, SoloRecorreLosPostingsDeLaConsulta) {
  const auto docs = prueba::corpus(400);
  InvertedIndex indice(indexar(docs));
  const auto oraculo = prueba::oraculo(docs, TokenizerOptions{});
  TfIdfRanking tfidf(indice);

  const auto r = tfidf.search("w10 w50 w123", 10);
  std::uint64_t postings = 0;
  for (const char* t : {"w10", "w50", "w123"}) postings += oraculo.at(t).size();
  EXPECT_EQ(tfidf.stats().records_examined, postings);
  EXPECT_EQ(tfidf.stats().records_returned, r.size());
  EXPECT_LT(postings, indice.postings());  // mucho menos que todo el indice
  // Una pagina de diccionario por termino, las de sus listas y las de las
  // normas, que se cargan una vez: nada cerca de leer el indice entero.
  EXPECT_GT(tfidf.stats().pages_read, 0U);

  EXPECT_EQ(tfidf.search("w10 w50 w123", 10), r);
  const std::uint64_t segunda = tfidf.stats().pages_read;
  EXPECT_LT(segunda, DiskManager(dir_ / "i0.idx").page_count());
}

TEST_F(RankingTfIdf, RepetirUnTerminoEnLaConsultaLoPesaMas) {
  InvertedIndex indice(indexar(kDiapositiva, sin_stemmer()));
  TfIdfRanking tfidf(indice, TfWeight::kRaw);
  // Con "oro oro plata", oro pesa el doble en la consulta: D3 (que tiene oro y
  // no plata) se acerca a D2.
  const auto una = tfidf.search("oro plata", 10);
  const auto dos = tfidf.search("oro oro plata", 10);
  ASSERT_EQ(una.size(), 3U);
  ASSERT_EQ(dos.size(), 3U);
  const auto puntaje = [](const std::vector<ScoredDocument>& r, DocId d) {
    return std::find_if(r.begin(), r.end(), [d](const auto& e) { return e.doc == d; })->score;
  };
  EXPECT_GT(puntaje(dos, 3), puntaje(una, 3));
  EXPECT_LT(puntaje(dos, 2), puntaje(una, 2));
}

}  // namespace
}  // namespace quipudb
