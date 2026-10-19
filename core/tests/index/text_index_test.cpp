// Pruebas del indice de texto en el catalogo y en Database (issue #166).
//
// Van por `Database` de punta a punta, como lo va a usar el motor: crear la
// tabla, insertar, crear el indice, buscar, cerrar y reabrir.

#include "quipudb/index/text_index.hpp"

#include <gtest/gtest.h>

#include <filesystem>
#include <memory>
#include <string>
#include <variant>
#include <vector>

#include "quipudb/catalog/database.hpp"
#include "quipudb/error.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

Schema documentos() {
  return Schema{
      .table_name = "documentos",
      .columns = {{"id", DataType::Int}, {"contenido", DataType::Varchar, 200}},
      .key_column = 0,
  };
}

/// Los documentos del ejemplo de pesos TF-IDF de la diapositiva.
const std::vector<std::string> kTextos = {
    "Cargamento de oro dañado por el fuego",
    "La entrega de la plata llegó en el camión color plata",
    "El cargamento de oro llegó en un camión",
};

class IndiceDeTexto : public ::testing::Test {
 protected:
  void SetUp() override {
    dir_ = fs::temp_directory_path() /
           ("quipudb_textindex_test_" +
            std::string(::testing::UnitTest::GetInstance()->current_test_info()->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
    abrir();
    TableFile& t = db_->create_table(documentos(), kind::kHeap);
    for (std::size_t i = 0; i < kTextos.size(); ++i) {
      t.insert(Record{static_cast<std::int32_t>(i + 1), kTextos[i]});
    }
  }
  void TearDown() override {
    db_.reset();
    fs::remove_all(dir_);
  }

  void abrir() { db_ = std::make_unique<Database>(dir_ / "catalogo.txt"); }

  TextIndex& crear() {
    return dynamic_cast<TextIndex&>(
        db_->create_index("documentos", "por_contenido", "contenido", kind::kInvertedIndex));
  }

  /// Los ids de los registros que devolvio una busqueda, en orden.
  std::vector<std::int32_t> ids(const std::vector<std::pair<RID, double>>& r) {
    std::vector<std::int32_t> out;
    for (const auto& [rid, score] : r) {
      out.push_back(std::get<std::int32_t>(db_->table("documentos").read(rid)->at(0)));
    }
    return out;
  }

  fs::path dir_;
  std::unique_ptr<Database> db_;
};

TEST_F(IndiceDeTexto, SeCreaSobreLosDatosQueYaTieneLaTabla) {
  TextIndex& ix = crear();
  EXPECT_EQ(ix.kind(), kind::kInvertedIndex);
  EXPECT_EQ(ix.size(), 3U);
  EXPECT_GT(ix.terms(), 0U);
  // El mismo orden que la diapositiva: D2, D3, D1.
  EXPECT_EQ(ids(ix.search_text("oro plata camión", TextRankingKind::kTfIdf, 10)),
            (std::vector<std::int32_t>{2, 3, 1}));
  EXPECT_EQ(ids(ix.search_text("oro plata camión", TextRankingKind::kBm25, 10)).front(), 2);
}

TEST_F(IndiceDeTexto, DevuelveRidsYPuntajes) {
  TextIndex& ix = crear();
  const auto r = ix.search_text("fuego", TextRankingKind::kBm25, 10);
  ASSERT_EQ(r.size(), 1U);
  EXPECT_GT(r[0].second, 0.0);
  const auto registro = db_->table("documentos").read(r[0].first);
  ASSERT_TRUE(registro.has_value());
  EXPECT_EQ(std::get<std::string>(registro->at(1)), kTextos[0]);
  EXPECT_EQ(ix.search_text("oro plata camión", TextRankingKind::kTfIdf, 1).size(), 1U);
  EXPECT_TRUE(ix.search_text("helicóptero", TextRankingKind::kTfIdf, 10).empty());
}

TEST_F(IndiceDeTexto, SeReabreDesdeElCatalogo) {
  const auto antes = ids(crear().search_text("oro plata camión", TextRankingKind::kTfIdf, 10));
  db_.reset();
  abrir();
  auto& ix = dynamic_cast<TextIndex&>(db_->index("documentos", "por_contenido"));
  EXPECT_EQ(ix.size(), 3U);
  EXPECT_EQ(ids(ix.search_text("oro plata camión", TextRankingKind::kTfIdf, 10)), antes);
  EXPECT_EQ(ix.tokenizer_options(), TokenizerOptions{});
}

TEST_F(IndiceDeTexto, SeReconstruyeConOtrasOpcionesYConDatosNuevos) {
  TextIndex& ix = crear();
  db_->table("documentos").insert(Record{std::int32_t{4}, std::string("Un fuego en la mina de oro")});
  // El indice no ve el documento nuevo hasta reconstruirse.
  EXPECT_EQ(ix.search_text("mina", TextRankingKind::kBm25, 10).size(), 0U);

  const TokenizerOptions sin_stemmer{.stemmer = std::nullopt};
  ix.build(sin_stemmer);
  EXPECT_EQ(ix.size(), 4U);
  EXPECT_EQ(ix.tokenizer_options(), sin_stemmer);
  EXPECT_EQ(ids(ix.search_text("mina", TextRankingKind::kBm25, 10)),
            (std::vector<std::int32_t>{4}));

  // Y la reconstruccion persiste.
  db_.reset();
  abrir();
  auto& reabierto = dynamic_cast<TextIndex&>(db_->index("documentos", "por_contenido"));
  EXPECT_EQ(reabierto.size(), 4U);
  EXPECT_EQ(reabierto.tokenizer_options(), sin_stemmer);
}

TEST_F(IndiceDeTexto, SoloSobreColumnasDeTexto) {
  EXPECT_THROW(db_->create_index("documentos", "mal", "id", kind::kInvertedIndex), SchemaError);
  EXPECT_FALSE(db_->catalog().table("documentos").index("mal"));
}

TEST_F(IndiceDeTexto, SoloSobreHeapFiles) {
  Schema s = documentos();
  s.table_name = "secuencial";
  db_->create_table(s, kind::kSequential);
  EXPECT_THROW(db_->create_index("secuencial", "ix", "contenido", kind::kInvertedIndex),
               SchemaError);
}

TEST_F(IndiceDeTexto, InsertarEnLaTablaLoDesactualizaYBuscarLoReconstruye) {
  TextIndex& ix = crear();
  EXPECT_FALSE(ix.stale());
  // Lo que hace el DML: insertar en la tabla y avisarle a cada indice.
  const Record nuevo{std::int32_t{4}, std::string("Un fuego en la mina de oro")};
  const RID rid = db_->table("documentos").insert(nuevo);
  ix.insert(nuevo.at(1), rid);
  EXPECT_TRUE(ix.stale());
  EXPECT_EQ(ix.size(), 3U);  // todavia no se reconstruyo

  // La busqueda reconstruye antes de buscar: el documento nuevo aparece.
  EXPECT_EQ(ids(ix.search_text("mina", TextRankingKind::kBm25, 10)),
            (std::vector<std::int32_t>{4}));
  EXPECT_FALSE(ix.stale());
  EXPECT_EQ(ix.size(), 4U);
}

TEST_F(IndiceDeTexto, BorrarDeLaTablaNoDejaRidsViejos) {
  TextIndex& ix = crear();
  auto& tabla = db_->table("documentos");
  // Borrar D2, el unico con "plata", como lo hace el DELETE.
  const auto antes = ix.search_text("plata", TextRankingKind::kTfIdf, 10);
  ASSERT_EQ(antes.size(), 1U);
  EXPECT_TRUE(ix.remove(Key{std::string(kTextos[1])}, antes[0].first));
  ASSERT_EQ(tabla.remove(Key{std::int32_t{2}}), 1U);

  EXPECT_TRUE(ix.search_text("plata", TextRankingKind::kTfIdf, 10).empty());
  EXPECT_EQ(ix.size(), 2U);
}

TEST_F(IndiceDeTexto, LaMarcaSobreviveAlProceso) {
  TextIndex& ix = crear();
  const RID rid = db_->table("documentos").insert(Record{std::int32_t{4}, std::string("mina")});
  ix.insert(Key{std::string("mina")}, rid);
  const fs::path archivo =
      db_->catalog().resolve(db_->catalog().table("documentos").index("por_contenido")->file);
  EXPECT_TRUE(fs::exists(TextIndex::stale_marker_path(archivo)));

  // Otro proceso abre la base: el indice sigue desactualizado y la busqueda
  // lo reconstruye igual.
  db_.reset();
  abrir();
  auto& reabierto = dynamic_cast<TextIndex&>(db_->index("documentos", "por_contenido"));
  EXPECT_TRUE(reabierto.stale());
  EXPECT_EQ(reabierto.search_text("mina", TextRankingKind::kBm25, 10).size(), 1U);
  EXPECT_FALSE(fs::exists(TextIndex::stale_marker_path(archivo)));
}

TEST_F(IndiceDeTexto, LoQueNoAplicaLoDiceClaro) {
  TextIndex& ix = crear();
  const Key texto = std::string("oro");
  EXPECT_THROW((void)ix.search(texto), Unsupported);
  EXPECT_THROW((void)ix.range_search(texto, texto), Unsupported);
  EXPECT_THROW((void)ix.scan(), Unsupported);
  EXPECT_FALSE(ix.supports_range());
}

TEST_F(IndiceDeTexto, BorrarElIndiceBorraTodosSusArchivos) {
  crear().insert(Key{std::string("x")}, RID{1, 0});  // deja la marca de desactualizado
  const fs::path archivo =
      db_->catalog().resolve(db_->catalog().table("documentos").index("por_contenido")->file);
  ASSERT_TRUE(fs::exists(archivo));
  ASSERT_TRUE(fs::exists(TextIndex::rids_path(archivo)));
  ASSERT_TRUE(fs::exists(TextIndex::stale_marker_path(archivo)));
  db_->drop_index("documentos", "por_contenido");
  EXPECT_FALSE(fs::exists(archivo));
  EXPECT_FALSE(fs::exists(TextIndex::rids_path(archivo)));
  EXPECT_FALSE(fs::exists(TextIndex::stale_marker_path(archivo)));
}

TEST_F(IndiceDeTexto, BorrarLaTablaBorraElIndice) {
  crear();
  db_->drop_table("documentos");
  // Solo queda el catalogo: ni la tabla, ni el indice, ni sus RIDs, ni restos
  // del SPIMI.
  std::vector<fs::path> archivos(fs::directory_iterator(dir_), {});
  ASSERT_EQ(archivos.size(), 1U);
  EXPECT_EQ(archivos[0].filename(), "catalogo.txt");
}

TEST_F(IndiceDeTexto, UnaTablaVaciaDaUnIndiceVacio) {
  Schema s = documentos();
  s.table_name = "vacia";
  db_->create_table(s, kind::kHeap);
  auto& ix =
      dynamic_cast<TextIndex&>(db_->create_index("vacia", "ix", "contenido", kind::kInvertedIndex));
  EXPECT_EQ(ix.size(), 0U);
  EXPECT_TRUE(ix.search_text("oro", TextRankingKind::kBm25, 10).empty());
}

TEST_F(IndiceDeTexto, CuentaLoQueCostoConstruirYBuscar) {
  TextIndex& ix = crear();
  EXPECT_GT(ix.stats().pages_written, 0U);  // la construccion
  ix.reset_stats();
  const auto r = ix.search_text("oro plata camión", TextRankingKind::kBm25, 10);
  EXPECT_GT(ix.stats().pages_read, 0U);
  EXPECT_EQ(ix.stats().records_returned, r.size());
}

}  // namespace
}  // namespace quipudb
