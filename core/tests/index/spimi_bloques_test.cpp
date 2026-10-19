// Pruebas de los bloques de SPIMI (issue #161).
//
// El oraculo es un indice invertido construido de la forma mas ingenua
// posible: un std::map termino -> postings con todo el corpus en memoria. Lo
// que se verifica es que los bloques, leidos en orden y concatenados por
// termino, digan exactamente lo mismo.

#include "quipudb/index/spimi.hpp"

#include <gtest/gtest.h>

#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <map>
#include <string>
#include <vector>

#include "quipudb/error.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

/// Un directorio propio por prueba, para contar los archivos que quedan.
class SpimiBloques : public ::testing::Test {
 protected:
  void SetUp() override {
    dir_ = fs::temp_directory_path() /
           ("quipudb_spimi_test_" +
            std::string(::testing::UnitTest::GetInstance()->current_test_info()->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
  }
  void TearDown() override { fs::remove_all(dir_); }

  SpimiOptions opciones(std::size_t page_size = kDefaultPageSize) const {
    return SpimiOptions{.page_size = page_size, .dir = dir_};
  }

  std::size_t archivos() const {
    return static_cast<std::size_t>(std::distance(fs::directory_iterator(dir_), {}));
  }

  fs::path dir_;
};

/// Sin stemmer, para que los terminos esperados se lean tal cual.
TokenizerOptions sin_stemmer() { return TokenizerOptions{.stemmer = std::nullopt}; }

std::vector<PostingEntry> leer(const fs::path& bloque, std::size_t page_size = kDefaultPageSize) {
  SpimiBlockReader lector(bloque, page_size);
  std::vector<PostingEntry> entradas;
  PostingEntry e;
  while (lector.next(e)) entradas.push_back(e);
  EXPECT_EQ(entradas.size(), lector.entries());
  return entradas;
}

TEST_F(SpimiBloques, LosDocumentosDeLaDiapositivaEnUnBloque) {
  // Los cuatro documentos de la diapositiva de Bag of Words.
  SpimiIndexer spimi(sin_stemmer(), opciones());
  spimi.add_document(1, "La casa es grande");
  spimi.add_document(2, "El gato está en la casa");
  spimi.add_document(3, "La casa es bonita y grande");
  spimi.add_document(4, "El sol brilla sobre la casa");
  spimi.finish();

  ASSERT_EQ(spimi.blocks().size(), 1U);
  EXPECT_EQ(spimi.documents(), 4U);
  const std::vector<PostingEntry> esperado = {
      {"bonita", {{3, 1}}},
      {"brilla", {{4, 1}}},
      {"casa", {{1, 1}, {2, 1}, {3, 1}, {4, 1}}},
      {"gato", {{2, 1}}},
      {"grande", {{1, 1}, {3, 1}}},
      {"sol", {{4, 1}}},
  };
  EXPECT_EQ(leer(spimi.blocks()[0]), esperado);
}

TEST_F(SpimiBloques, CuentaLaFrecuenciaDeCadaTerminoEnElDocumento) {
  SpimiIndexer spimi(sin_stemmer(), opciones());
  spimi.add_document(7, "plata plata oro PLATA");
  spimi.add_document(9, "oro");
  spimi.finish();
  const std::vector<PostingEntry> esperado = {
      {"oro", {{7, 1}, {9, 1}}},
      {"plata", {{7, 3}}},
  };
  EXPECT_EQ(leer(spimi.blocks()[0]), esperado);
}

TEST_F(SpimiBloques, UsaElTokenizadorQueSeLePasa) {
  // Con el stemmer por defecto, amigas y amigos son el mismo termino.
  SpimiIndexer spimi(TokenizerOptions{}, opciones());
  spimi.add_document(1, "Mis amigas y amigos peruanos son estudiosos.");
  spimi.finish();
  const std::vector<PostingEntry> esperado = {
      {"amig", {{1, 2}}},
      {"estudi", {{1, 1}}},
      {"peruan", {{1, 1}}},
  };
  EXPECT_EQ(leer(spimi.blocks()[0]), esperado);
}

TEST_F(SpimiBloques, LosDocumentosVaciosCuentanPeroNoAgreganPostings) {
  SpimiIndexer spimi(sin_stemmer(), opciones());
  spimi.add_document(1, "");
  spimi.add_document(2, "el la de que");  // solo stopwords
  spimi.add_document(3, "sol");
  spimi.add_document(4, "   ");
  spimi.finish();
  EXPECT_EQ(spimi.documents(), 4U);
  ASSERT_EQ(spimi.blocks().size(), 1U);
  EXPECT_EQ(leer(spimi.blocks()[0]), (std::vector<PostingEntry>{{"sol", {{3, 1}}}}));
}

TEST_F(SpimiBloques, SinTerminosNoSeEscribeNingunBloque) {
  SpimiIndexer spimi(sin_stemmer(), opciones());
  spimi.add_document(1, "");
  spimi.add_document(2, "de la");
  spimi.finish();
  EXPECT_EQ(spimi.documents(), 2U);
  EXPECT_TRUE(spimi.blocks().empty());
  EXPECT_EQ(archivos(), 0U);
}

TEST_F(SpimiBloques, LosDocumentosTienenQueLlegarEnOrden) {
  SpimiIndexer spimi(sin_stemmer(), opciones());
  spimi.add_document(5, "sol");
  EXPECT_THROW(spimi.add_document(5, "luna"), InvalidRecord);
  EXPECT_THROW(spimi.add_document(3, "luna"), InvalidRecord);
  spimi.add_document(6, "luna");
  EXPECT_EQ(spimi.documents(), 2U);
}

TEST_F(SpimiBloques, DespuesDeFinishNoSeAgregaNada) {
  SpimiIndexer spimi(sin_stemmer(), opciones());
  spimi.add_document(1, "sol");
  spimi.finish();
  spimi.finish();  // dos veces no escribe un segundo bloque
  EXPECT_EQ(spimi.blocks().size(), 1U);
  EXPECT_THROW(spimi.add_document(2, "luna"), Unsupported);
}

TEST_F(SpimiBloques, UnaListaDePostingsPuedeCruzarPaginas) {
  // Con paginas de 128 bytes caben 15 postings por pagina: la lista de "sol"
  // en 500 documentos ocupa unas 34 paginas.
  constexpr std::size_t kPagina = 128;
  SpimiIndexer spimi(sin_stemmer(), opciones(kPagina));
  for (DocId d = 1; d <= 500; ++d) spimi.add_document(d, d % 7 == 0 ? "sol luna sol" : "sol");
  spimi.finish();

  const std::vector<PostingEntry> entradas = leer(spimi.blocks()[0], kPagina);
  ASSERT_EQ(entradas.size(), 2U);
  EXPECT_EQ(entradas[0].term, "luna");
  EXPECT_EQ(entradas[0].postings.size(), 500U / 7);
  EXPECT_EQ(entradas[1].term, "sol");
  ASSERT_EQ(entradas[1].postings.size(), 500U);
  for (DocId d = 1; d <= 500; ++d) {
    EXPECT_EQ(entradas[1].postings[d - 1], (Posting{d, d % 7 == 0 ? 2U : 1U}));
  }
}

TEST_F(SpimiBloques, LosBloquesSeBorranConElIndexador) {
  {
    SpimiIndexer spimi(sin_stemmer(), opciones());
    spimi.add_document(1, "sol");
    spimi.finish();
    EXPECT_EQ(archivos(), 1U);
  }
  EXPECT_EQ(archivos(), 0U);
}

TEST_F(SpimiBloques, ElLectorRechazaLoQueNoEsUnBloque) {
  EXPECT_THROW(SpimiBlockReader(dir_ / "no_existe.blk"), IoError);

  // Un archivo de paginas valido, pero de otra estructura.
  const fs::path ajeno = dir_ / "ajeno.pages";
  { DiskManager disco(ajeno); }
  EXPECT_THROW(SpimiBlockReader{ajeno}, IoError);
}

// ---------------------------------------------------------------------------
// Presupuesto de memoria
// ---------------------------------------------------------------------------

/// Un corpus sintetico y reproducible: documentos de 5 a 40 palabras tomadas
/// de un vocabulario de 300, con frecuencias sesgadas -- unas pocas palabras
/// aparecen en casi todos los documentos y la mayoria en muy pocos, como en un
/// texto real. Las palabras llevan digitos, asi que el stemmer no las toca.
std::vector<std::string> corpus(std::size_t documentos) {
  std::uint64_t estado = 160;
  const auto siguiente = [&estado]() {
    estado = estado * 6364136223846793005ULL + 1442695040888963407ULL;
    return static_cast<std::uint32_t>(estado >> 33);
  };
  std::vector<std::string> docs;
  for (std::size_t d = 0; d < documentos; ++d) {
    std::string texto;
    const std::uint32_t palabras = 5 + siguiente() % 36;
    for (std::uint32_t i = 0; i < palabras; ++i) {
      // El minimo de dos uniformes sesga hacia los indices chicos.
      const std::uint32_t a = siguiente() % 300;
      const std::uint32_t b = siguiente() % 300;
      texto += "w" + std::to_string(std::min(a, b)) + " ";
    }
    docs.push_back(std::move(texto));
  }
  return docs;
}

/// El indice invertido hecho a mano, con todo en memoria.
std::map<std::string, std::vector<Posting>> oraculo(const std::vector<std::string>& docs,
                                                    const TokenizerOptions& opciones) {
  const TextTokenizer tokenizador(opciones);
  std::map<std::string, std::vector<Posting>> indice;
  for (std::size_t i = 0; i < docs.size(); ++i) {
    std::map<std::string, std::uint32_t> tf;
    for (const std::string& t : tokenizador.tokenize(docs[i])) ++tf[t];
    for (const auto& [t, n] : tf) indice[t].push_back(Posting{static_cast<DocId>(i + 1), n});
  }
  return indice;
}

/// Construye el indice por bloques y devuelve cada bloque leido.
std::vector<std::vector<PostingEntry>> construir(SpimiIndexer& spimi,
                                                 const std::vector<std::string>& docs) {
  for (std::size_t i = 0; i < docs.size(); ++i) {
    spimi.add_document(static_cast<DocId>(i + 1), docs[i]);
  }
  spimi.finish();
  std::vector<std::vector<PostingEntry>> bloques;
  for (const auto& b : spimi.blocks()) bloques.push_back(leer(b));
  return bloques;
}

/// Concatena los bloques por termino, en orden de bloque, que es lo que hara
/// el merge de #162.
std::map<std::string, std::vector<Posting>> concatenar(
    const std::vector<std::vector<PostingEntry>>& bloques) {
  std::map<std::string, std::vector<Posting>> indice;
  for (const auto& bloque : bloques) {
    for (const auto& e : bloque) {
      auto& lista = indice[e.term];
      lista.insert(lista.end(), e.postings.begin(), e.postings.end());
    }
  }
  return indice;
}

TEST_F(SpimiBloques, ConPocoPresupuestoSalenMuchosBloquesYDicenLoMismo) {
  const auto docs = corpus(150);
  SpimiIndexer spimi(TokenizerOptions{}, SpimiOptions{.memory_budget = 4096, .dir = dir_});
  const auto bloques = construir(spimi, docs);

  EXPECT_GT(bloques.size(), 10U);
  EXPECT_LE(spimi.peak_memory(), 4096U);
  EXPECT_EQ(concatenar(bloques), oraculo(docs, TokenizerOptions{}));

  for (const auto& bloque : bloques) {
    ASSERT_FALSE(bloque.empty());
    for (std::size_t i = 0; i < bloque.size(); ++i) {
      // Ordenado por termino, sin repetidos dentro del bloque.
      if (i > 0) EXPECT_LT(bloque[i - 1].term, bloque[i].term);
      // Cada lista, por documento estrictamente creciente.
      for (std::size_t j = 1; j < bloque[i].postings.size(); ++j) {
        EXPECT_LT(bloque[i].postings[j - 1].doc, bloque[i].postings[j].doc);
      }
    }
  }
}

TEST_F(SpimiBloques, NoHayDiccionarioGlobal) {
  // Cada bloque arma su diccionario desde cero, asi que un termino frecuente
  // reaparece en varios bloques: la suma de entradas de todos los bloques es
  // mayor que el vocabulario. Con un diccionario global, cada termino estaria
  // una sola vez.
  const auto docs = corpus(150);
  SpimiIndexer spimi(TokenizerOptions{}, SpimiOptions{.memory_budget = 4096, .dir = dir_});
  const auto bloques = construir(spimi, docs);
  ASSERT_GT(bloques.size(), 1U);

  std::size_t entradas = 0;
  std::map<std::string, std::size_t> bloques_por_termino;
  for (const auto& bloque : bloques) {
    entradas += bloque.size();
    for (const auto& e : bloque) ++bloques_por_termino[e.term];
  }
  EXPECT_GT(entradas, bloques_por_termino.size());

  // El termino con mas postings esta en varios bloques.
  const auto indice = oraculo(docs, TokenizerOptions{});
  const auto mas_frecuente =
      std::max_element(indice.begin(), indice.end(), [](const auto& x, const auto& y) {
        return x.second.size() < y.second.size();
      });
  EXPECT_GT(bloques_por_termino[mas_frecuente->first], 1U);
}

TEST_F(SpimiBloques, ConPresupuestoGrandeSaleUnSoloBloque) {
  const auto docs = corpus(400);
  SpimiIndexer spimi(TokenizerOptions{}, SpimiOptions{.dir = dir_});
  const auto bloques = construir(spimi, docs);
  ASSERT_EQ(bloques.size(), 1U);
  EXPECT_EQ(concatenar(bloques), oraculo(docs, TokenizerOptions{}));
}

TEST_F(SpimiBloques, MenosMemoriaEsMasBloques) {
  const auto docs = corpus(80);
  std::size_t anteriores = 0;
  for (const std::size_t presupuesto : {65536U, 16384U, 8192U, 4096U}) {
    SpimiIndexer spimi(TokenizerOptions{},
                       SpimiOptions{.memory_budget = presupuesto, .dir = dir_});
    const auto bloques = construir(spimi, docs);
    EXPECT_GT(bloques.size(), anteriores) << presupuesto;
    EXPECT_LE(spimi.peak_memory(), presupuesto);
    EXPECT_EQ(concatenar(bloques), oraculo(docs, TokenizerOptions{})) << presupuesto;
    anteriores = bloques.size();
  }
}

TEST_F(SpimiBloques, ElPresupuestoMinimoAlcanzaParaUnTermino) {
  const std::size_t minimo = SpimiIndexer::min_memory_budget(TokenizerOptions{});
  EXPECT_EQ(minimo, kSpimiEntryOverhead + kMaxTermBytes + kSpimiInitialPostings * sizeof(Posting));
  EXPECT_THROW(SpimiIndexer(TokenizerOptions{}, SpimiOptions{.memory_budget = minimo - 1}),
               SchemaError);

  // Justo el minimo: casi un bloque por posting, pero el indice sale bien.
  const auto docs = corpus(5);
  SpimiIndexer spimi(TokenizerOptions{}, SpimiOptions{.memory_budget = minimo, .dir = dir_});
  const auto bloques = construir(spimi, docs);
  EXPECT_LE(spimi.peak_memory(), minimo);
  EXPECT_EQ(concatenar(bloques), oraculo(docs, TokenizerOptions{}));
}

TEST_F(SpimiBloques, LasListasDuplicanSuCapacidad) {
  // Un solo termino en 9 documentos: la lista nace con 4, pasa a 8 y a 16.
  SpimiIndexer spimi(sin_stemmer(), opciones());
  for (DocId d = 1; d <= 9; ++d) spimi.add_document(d, "sol");
  EXPECT_EQ(spimi.peak_memory(), kSpimiEntryOverhead + 3 + 16 * sizeof(Posting));
}

TEST_F(SpimiBloques, ReportaLoQueCostoLaConstruccion) {
  const auto docs = corpus(150);
  SpimiIndexer spimi(TokenizerOptions{}, SpimiOptions{.memory_budget = 4096, .dir = dir_});
  for (std::size_t i = 0; i < docs.size(); ++i) {
    spimi.add_document(static_cast<DocId>(i + 1), docs[i]);
  }
  spimi.finish();

  std::uint64_t paginas = 0;
  for (const auto& b : spimi.blocks()) paginas += DiskManager(b).page_count();

  std::uint64_t postings = 0;
  for (const auto& [termino, lista] : oraculo(docs, TokenizerOptions{})) postings += lista.size();

  EXPECT_EQ(spimi.blocks_written(), spimi.blocks().size());
  EXPECT_GT(spimi.blocks_written(), 1U);
  EXPECT_EQ(spimi.stats().pages_written, paginas);
  EXPECT_EQ(spimi.stats().pages_read, 0U);  // una sola pasada: no relee nada
  EXPECT_EQ(spimi.stats().records_examined, docs.size());
  EXPECT_EQ(spimi.stats().records_returned, postings);
}

TEST_F(SpimiBloques, ElLectorLeeCadaPaginaUnaSolaVez) {
  // Paginas chicas para que el bloque ocupe varias.
  constexpr std::size_t kPagina = 256;
  const auto docs = corpus(60);
  SpimiIndexer spimi(TokenizerOptions{}, SpimiOptions{.page_size = kPagina, .dir = dir_});
  for (std::size_t i = 0; i < docs.size(); ++i) {
    spimi.add_document(static_cast<DocId>(i + 1), docs[i]);
  }
  spimi.finish();
  ASSERT_EQ(spimi.blocks().size(), 1U);
  const PageId paginas = DiskManager(spimi.blocks()[0], kPagina).page_count();
  ASSERT_GT(paginas, 10U);

  SpimiBlockReader lector(spimi.blocks()[0], kPagina);
  PostingEntry e;
  while (lector.next(e)) {
  }
  EXPECT_EQ(lector.stats().pages_read, paginas);
  EXPECT_EQ(lector.stats().records_returned, lector.entries());
}

TEST_F(SpimiBloques, ElLectorPideElMismoTamanoDePagina) {
  SpimiIndexer spimi(sin_stemmer(), opciones(512));
  spimi.add_document(1, "sol");
  spimi.finish();
  EXPECT_THROW(SpimiBlockReader(spimi.blocks()[0], kDefaultPageSize), IoError);
  EXPECT_EQ(leer(spimi.blocks()[0], 512).size(), 1U);
}

}  // namespace
}  // namespace quipudb
