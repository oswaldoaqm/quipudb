// Pruebas de los bloques de SPIMI (issue #161).
//
// El oraculo es un indice invertido construido de la forma mas ingenua
// posible: un std::map termino -> postings con todo el corpus en memoria. Lo
// que se verifica es que los bloques, leidos en orden y concatenados por
// termino, digan exactamente lo mismo.

#include "quipudb/index/spimi.hpp"

#include <gtest/gtest.h>

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

TEST_F(SpimiBloques, ElLectorPideElMismoTamanoDePagina) {
  SpimiIndexer spimi(sin_stemmer(), opciones(512));
  spimi.add_document(1, "sol");
  spimi.finish();
  EXPECT_THROW(SpimiBlockReader(spimi.blocks()[0], kDefaultPageSize), IoError);
  EXPECT_EQ(leer(spimi.blocks()[0], 512).size(), 1U);
}

}  // namespace
}  // namespace quipudb
