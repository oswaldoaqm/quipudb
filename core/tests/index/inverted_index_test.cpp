// Pruebas del indice invertido: el merge de SPIMI (issue #162).
//
// Dos oraculos. El indice ingenuo de `texto_corpus.hpp`, con todo el corpus en
// memoria, dice que tiene que haber en el indice. Y el propio indice
// construido con un solo bloque dice como tiene que quedar el ARCHIVO: con
// cualquier otra cantidad de bloques tiene que salir igual, byte por byte.

#include "quipudb/index/inverted_index.hpp"

#include <gtest/gtest.h>

#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <map>
#include <string>
#include <vector>

#include "quipudb/error.hpp"
#include "quipudb/storage/disk_manager.hpp"
#include "texto_corpus.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;
using prueba::corpus;
using prueba::oraculo;

class IndiceInvertido : public ::testing::Test {
 protected:
  void SetUp() override {
    dir_ = fs::temp_directory_path() /
           ("quipudb_ii_test_" +
            std::string(::testing::UnitTest::GetInstance()->current_test_info()->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
  }
  void TearDown() override { fs::remove_all(dir_); }

  /// Construye el indice de `docs` en `nombre` y devuelve lo que costo.
  InvertedIndexBuild construir(const std::vector<std::string>& docs, const std::string& nombre,
                               TokenizerOptions tok = {}, SpimiOptions spimi_opt = {},
                               std::size_t fan_in = kSpimiDefaultFanIn) {
    spimi_opt.dir = dir_;
    SpimiIndexer spimi(std::move(tok), spimi_opt);
    for (std::size_t i = 0; i < docs.size(); ++i) {
      spimi.add_document(static_cast<DocId>(i + 1), docs[i]);
    }
    spimi.finish();
    bloques_ = spimi.blocks().size();
    return InvertedIndex::build(spimi, dir_ / nombre, fan_in);
  }

  /// Todo el indice, leido con for_each.
  static std::map<std::string, std::vector<Posting>> todo(InvertedIndex& indice) {
    std::map<std::string, std::vector<Posting>> out;
    indice.for_each([&](const PostingEntry& e) { out[e.term] = e.postings; });
    return out;
  }

  static std::vector<char> bytes(const fs::path& p) {
    std::ifstream in(p, std::ios::binary);
    return {std::istreambuf_iterator<char>(in), {}};
  }

  fs::path dir_;
  std::size_t bloques_ = 0;
};

TokenizerOptions sin_stemmer() { return TokenizerOptions{.stemmer = std::nullopt}; }

TEST_F(IndiceInvertido, LosDocumentosDeLaDiapositiva) {
  construir({"La casa es grande", "El gato está en la casa", "La casa es bonita y grande",
             "El sol brilla sobre la casa"},
            "slide.idx", sin_stemmer());
  InvertedIndex indice(dir_ / "slide.idx");

  EXPECT_EQ(indice.terms(), 6U);
  EXPECT_EQ(indice.postings(), 10U);
  EXPECT_EQ(indice.postings("casa"), (std::vector<Posting>{{1, 1}, {2, 1}, {3, 1}, {4, 1}}));
  EXPECT_EQ(indice.postings("grande"), (std::vector<Posting>{{1, 1}, {3, 1}}));
  EXPECT_EQ(indice.document_frequency("casa"), 4U);
  EXPECT_EQ(indice.document_frequency("gato"), 1U);
  // Terminos que no estan: antes del primero, entre dos y despues del ultimo.
  for (const char* ausente : {"aaa", "casas", "perro", "zzz"}) {
    EXPECT_EQ(indice.document_frequency(ausente), 0U) << ausente;
    EXPECT_TRUE(indice.postings(ausente).empty()) << ausente;
  }
}

TEST_F(IndiceInvertido, CoincideConElIndiceHechoAMano) {
  const auto docs = corpus(300);
  construir(docs, "corpus.idx", TokenizerOptions{},
            SpimiOptions{.memory_budget = 8192});
  ASSERT_GT(bloques_, 5U);

  InvertedIndex indice(dir_ / "corpus.idx");
  const auto esperado = oraculo(docs, TokenizerOptions{});
  EXPECT_EQ(todo(indice), esperado);
  EXPECT_EQ(indice.terms(), esperado.size());
  for (const auto& [termino, lista] : esperado) {
    EXPECT_EQ(indice.document_frequency(termino), lista.size()) << termino;
    EXPECT_EQ(indice.postings(termino), lista) << termino;
  }
}

TEST_F(IndiceInvertido, ElArchivoNoDependeDeCuantosBloquesHubo) {
  // La prueba que sugiere el issue: el mismo corpus con 1 bloque y con muchos
  // tiene que dar el mismo archivo.
  const auto docs = corpus(300);
  construir(docs, "uno.idx");
  ASSERT_EQ(bloques_, 1U);
  const auto referencia = bytes(dir_ / "uno.idx");

  for (const std::size_t presupuesto : {65536U, 16384U, 8192U}) {
    const std::string nombre = "p" + std::to_string(presupuesto) + ".idx";
    construir(docs, nombre, TokenizerOptions{}, SpimiOptions{.memory_budget = presupuesto});
    EXPECT_GT(bloques_, 1U) << presupuesto;
    EXPECT_EQ(bytes(dir_ / nombre), referencia)
        << "con " << bloques_ << " bloques el archivo es distinto";
  }
}

TEST_F(IndiceInvertido, ListasYDiccionarioEnMuchasPaginas) {
  // Paginas de 256 bytes: el diccionario ocupa varias paginas y las listas de
  // los terminos frecuentes cruzan varias.
  const auto docs = corpus(300);
  construir(docs, "chico.idx", TokenizerOptions{},
            SpimiOptions{.memory_budget = 16384, .page_size = 256});
  InvertedIndex indice(dir_ / "chico.idx", 256);
  const auto esperado = oraculo(docs, TokenizerOptions{});
  for (const auto& [termino, lista] : esperado) {
    EXPECT_EQ(indice.postings(termino), lista) << termino;
  }
  EXPECT_EQ(todo(indice), esperado);
  EXPECT_TRUE(indice.postings("w").empty());
  EXPECT_TRUE(indice.postings("w1000").empty());
}

TEST_F(IndiceInvertido, CerrarYReabrirConservaTodo) {
  const auto docs = corpus(100);
  const TokenizerOptions opciones{.stopword_languages = {TextLanguage::kEnglish},
                                  .extra_stopwords = {"w3", "Artículo"},
                                  .stemmer = TextLanguage::kEnglish,
                                  .unaccent_before_stem = true,
                                  .max_term_bytes = 40};
  construir(docs, "reabrir.idx", opciones);

  std::map<std::string, std::vector<Posting>> primera;
  {
    InvertedIndex indice(dir_ / "reabrir.idx");
    primera = todo(indice);
  }
  InvertedIndex indice(dir_ / "reabrir.idx");
  EXPECT_EQ(todo(indice), primera);
  EXPECT_EQ(primera, oraculo(docs, opciones));
  EXPECT_EQ(indice.tokenizer_options(), opciones);
}

TEST_F(IndiceInvertido, SinTerminosQuedaUnIndiceVacio) {
  construir({"", "el la de"}, "vacio.idx");
  InvertedIndex indice(dir_ / "vacio.idx");
  EXPECT_EQ(indice.terms(), 0U);
  EXPECT_EQ(indice.postings(), 0U);
  EXPECT_EQ(indice.document_frequency("casa"), 0U);
  EXPECT_TRUE(todo(indice).empty());
  EXPECT_EQ(indice.tokenizer_options(), TokenizerOptions{});
}

TEST_F(IndiceInvertido, OtraVersionDelFormatoEsUnIoError) {
  construir({"sol y luna"}, "version.idx");
  {
    DiskManager disco(dir_ / "version.idx");
    std::byte meta[DiskManager::kMetaSize];
    disco.read_meta(meta);
    const std::uint16_t otra = 99;
    std::memcpy(meta + 4, &otra, sizeof otra);  // el campo version va tras el magic
    disco.write_meta(meta);
  }
  EXPECT_THROW(InvertedIndex{dir_ / "version.idx"}, IoError);
}

TEST_F(IndiceInvertido, SoloAbreIndicesInvertidos) {
  EXPECT_THROW(InvertedIndex{dir_ / "no_existe.idx"}, IoError);
  { DiskManager ajeno(dir_ / "ajeno.pages"); }
  EXPECT_THROW(InvertedIndex{dir_ / "ajeno.pages"}, IoError);
}

TEST_F(IndiceInvertido, ElMergeExigeElSpimiTerminado) {
  SpimiIndexer spimi(TokenizerOptions{}, SpimiOptions{.dir = dir_});
  spimi.add_document(1, "sol");
  EXPECT_THROW(InvertedIndex::build(spimi, dir_ / "x.idx"), Unsupported);
  EXPECT_FALSE(fs::exists(dir_ / "x.idx"));
}

TEST_F(IndiceInvertido, NoPisaUnIndiceExistente) {
  construir({"sol"}, "existe.idx");
  const auto antes = bytes(dir_ / "existe.idx");
  EXPECT_THROW(construir({"luna"}, "existe.idx"), IoError);
  EXPECT_EQ(bytes(dir_ / "existe.idx"), antes);
}

TEST_F(IndiceInvertido, NoQuedanArchivosDeMas) {
  // Solo el indice: ni los bloques -- son del SPIMI, que ya murio -- ni el
  // temporal del merge.
  construir(corpus(100), "solo.idx", TokenizerOptions{}, SpimiOptions{.memory_budget = 8192});
  ASSERT_GT(bloques_, 1U);
  std::vector<fs::path> archivos(fs::directory_iterator(dir_), {});
  ASSERT_EQ(archivos.size(), 1U);
  EXPECT_EQ(archivos[0].filename(), "solo.idx");
}

TEST_F(IndiceInvertido, ReportaLoQueCostoElMerge) {
  const auto docs = corpus(200);
  SpimiIndexer spimi(TokenizerOptions{}, SpimiOptions{.memory_budget = 8192, .dir = dir_});
  for (std::size_t i = 0; i < docs.size(); ++i) {
    spimi.add_document(static_cast<DocId>(i + 1), docs[i]);
  }
  spimi.finish();
  // Lo que se lee: cada pagina de bloque y del archivo de longitudes (#163),
  // una vez.
  std::uint64_t paginas_bloques = DiskManager(spimi.lengths_file()).page_count();
  std::uint64_t entradas_bloques = 0;
  for (const auto& b : spimi.blocks()) {
    paginas_bloques += DiskManager(b).page_count();
    entradas_bloques += SpimiBlockReader(b).entries();
  }

  const InvertedIndexBuild r = InvertedIndex::build(spimi, dir_ / "stats.idx");
  const OpStats& s = r.stats;
  EXPECT_EQ(r.blocks, spimi.blocks().size());
  EXPECT_EQ(r.passes, 1U);
  InvertedIndex indice(dir_ / "stats.idx");
  EXPECT_EQ(s.pages_read, paginas_bloques);
  EXPECT_EQ(s.pages_written, DiskManager(dir_ / "stats.idx").page_count());
  EXPECT_EQ(s.records_examined, entradas_bloques);
  EXPECT_EQ(s.records_returned, indice.terms());
}

/// Pasadas que predice la teoria: niveles intermedios hasta que quedan
/// `fan_in` bloques o menos, mas la pasada final.
std::size_t pasadas_esperadas(std::size_t bloques, std::size_t fan_in) {
  std::size_t pasadas = 1;
  while (bloques > fan_in) {
    bloques = (bloques + fan_in - 1) / fan_in;
    ++pasadas;
  }
  return pasadas;
}

TEST_F(IndiceInvertido, MezclarPorNivelesDaElMismoArchivo) {
  const auto docs = corpus(300);
  construir(docs, "uno.idx");
  ASSERT_EQ(bloques_, 1U);
  const auto referencia = bytes(dir_ / "uno.idx");

  // fan-in 2 es la mezcla binaria de la diapositiva de BSBI: un arbol de
  // log2(bloques) niveles.
  for (const std::size_t fan_in : {2U, 3U, 7U}) {
    const std::string nombre = "k" + std::to_string(fan_in) + ".idx";
    const auto r = construir(docs, nombre, TokenizerOptions{},
                             SpimiOptions{.memory_budget = 8192}, fan_in);
    ASSERT_GT(r.blocks, fan_in);
    EXPECT_EQ(r.passes, pasadas_esperadas(r.blocks, fan_in)) << "fan-in " << fan_in;
    EXPECT_GT(r.passes, 1U);
    EXPECT_EQ(bytes(dir_ / nombre), referencia) << "fan-in " << fan_in;
  }
}

TEST_F(IndiceInvertido, LosBloquesIntermediosSeBorran) {
  const auto r = construir(corpus(150), "niveles.idx", TokenizerOptions{},
                           SpimiOptions{.memory_budget = 8192}, 2);
  ASSERT_GT(r.passes, 2U);
  std::vector<fs::path> archivos(fs::directory_iterator(dir_), {});
  ASSERT_EQ(archivos.size(), 1U);
  EXPECT_EQ(archivos[0].filename(), "niveles.idx");
}

TEST_F(IndiceInvertido, LosNivelesReleenLosIntermedios) {
  // Con niveles, cada pasada vuelve a leer todo: se leen mas paginas que las
  // de los bloques originales, y se escriben mas que las del indice.
  const auto docs = corpus(200);
  const auto una = construir(docs, "una.idx", TokenizerOptions{},
                             SpimiOptions{.memory_budget = 8192});
  const auto varias = construir(docs, "varias.idx", TokenizerOptions{},
                                SpimiOptions{.memory_budget = 8192}, 2);
  ASSERT_EQ(una.passes, 1U);
  ASSERT_GT(varias.passes, 1U);
  EXPECT_GT(varias.stats.pages_read, una.stats.pages_read);
  EXPECT_GT(varias.stats.pages_written, una.stats.pages_written);
  EXPECT_GT(varias.stats.records_examined, una.stats.records_examined);
  EXPECT_EQ(varias.stats.records_returned, una.stats.records_returned);
}

TEST_F(IndiceInvertido, ElFanInEsAlMenosDos) {
  SpimiIndexer spimi(TokenizerOptions{}, SpimiOptions{.dir = dir_});
  spimi.add_document(1, "sol");
  spimi.finish();
  EXPECT_THROW(InvertedIndex::build(spimi, dir_ / "x.idx", 1), SchemaError);
  EXPECT_FALSE(fs::exists(dir_ / "x.idx"));
}

TEST_F(IndiceInvertido, BuscarLeeUnaPaginaDelDiccionario) {
  construir(corpus(200), "busca.idx");
  InvertedIndex indice(dir_ / "busca.idx");
  EXPECT_EQ(indice.stats().pages_read, 0U);  // abrir no cuenta

  ASSERT_GT(indice.document_frequency("w0"), 0U);
  EXPECT_EQ(indice.stats().pages_read, 1U);

  indice.reset_stats();
  EXPECT_EQ(indice.document_frequency("zzz"), 0U);
  EXPECT_EQ(indice.stats().pages_read, 1U);

  indice.reset_stats();
  EXPECT_EQ(indice.document_frequency("a"), 0U);  // antes del primer termino
  EXPECT_EQ(indice.stats().pages_read, 0U);

  indice.reset_stats();
  const auto lista = indice.postings("w0");
  EXPECT_GE(indice.stats().pages_read, 2U);  // el diccionario y al menos una de la lista
  EXPECT_EQ(indice.stats().records_returned, lista.size());
}

}  // namespace
}  // namespace quipudb
