// Pruebas de Database (issue #56): abrir tablas desde el catalogo.

#include <gtest/gtest.h>

#include <filesystem>
#include <string>

#include "quipudb/catalog/database.hpp"
#include "quipudb/error.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

Schema alumnos() {
  return Schema{
      .table_name = "alumnos",
      .columns = {{"codigo", DataType::Int}, {"nombre", DataType::Varchar, 16}},
      .key_column = 0,
  };
}

Schema cursos() {
  return Schema{
      .table_name = "cursos",
      .columns = {{"codigo", DataType::Varchar, 8}, {"creditos", DataType::Int}},
      .key_column = 0,
  };
}

class DatabaseTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_db_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
    path_ = dir_ / "catalogo.txt";
  }
  void TearDown() override { fs::remove_all(dir_); }

  fs::path dir_;
  fs::path path_;
};

TEST_F(DatabaseTest, CreaLaTablaYDevuelveUnHandleUsable) {
  Database db(path_);
  TableFile& t = db.create_table(alumnos(), kind::kHeap);
  EXPECT_EQ(t.kind(), kind::kHeap);
  t.insert(Record{1, std::string("ana")});
  t.insert(Record{2, std::string("beto")});
  EXPECT_EQ(t.size(), 2u);
  EXPECT_TRUE(fs::exists(dir_ / "alumnos.heap"));
  EXPECT_TRUE(db.is_open("alumnos"));
}

TEST_F(DatabaseTest, DevuelveSiempreElMismoObjetoParaUnaTabla) {
  // Dos handles sobre el mismo archivo tienen cada uno su estado en memoria,
  // se pisan al escribir y dejan contadores que no cuadran. Por eso hay uno
  // solo por tabla.
  Database db(path_);
  db.create_table(alumnos(), kind::kHeap);
  TableFile& a = db.table("alumnos");
  TableFile& b = db.table("alumnos");
  EXPECT_EQ(&a, &b);
  a.insert(Record{1, std::string("ana")});
  EXPECT_EQ(b.size(), 1u);
  EXPECT_EQ(db.open_tables(), (std::vector<std::string>{"alumnos"}));
}

TEST_F(DatabaseTest, AbreCadaOrganizacionSegunElCatalogo) {
  Database db(path_);
  db.create_table(alumnos(), kind::kHeap);
  db.create_table(cursos(), kind::kSequential);
  EXPECT_EQ(db.table("alumnos").kind(), kind::kHeap);
  EXPECT_EQ(db.table("cursos").kind(), kind::kSequential);
  EXPECT_TRUE(fs::exists(dir_ / "cursos.sequential"));
}

TEST_F(DatabaseTest, RespetaElTamanoDePaginaQueGuardoElCatalogo) {
  // Sin el page_size en el catalogo, una tabla creada con otro tamano no se
  // podia reabrir. Es lo que necesita variar la comparacion del 2.1.6.
  {
    Database db(path_);
    TableFile& t = db.create_table(alumnos(), kind::kHeap, 1024);
    for (std::int32_t i = 1; i <= 100; ++i) t.insert(Record{i, std::string("x")});
  }
  Database db(path_);
  EXPECT_EQ(db.catalog().table("alumnos").page_size, 1024u);
  TableFile& t = db.table("alumnos");  // antes lanzaba IoError por el tamano
  EXPECT_EQ(t.size(), 100u);
  EXPECT_EQ(t.search(Value{50}).size(), 1u);
}

TEST_F(DatabaseTest, LoEscritoSobreviveAlCerrarYReabrir) {
  {
    Database db(path_);
    TableFile& t = db.create_table(alumnos(), kind::kHeap);
    for (std::int32_t i = 1; i <= 50; ++i) t.insert(Record{i, std::string("n")});
    db.flush();
  }
  Database db(path_);
  EXPECT_FALSE(db.is_open("alumnos")) << "todavia no se abrio";
  EXPECT_EQ(db.table("alumnos").size(), 50u);
  EXPECT_TRUE(db.is_open("alumnos"));
}

TEST_F(DatabaseTest, DropCierraElHandleYBorraElArchivo) {
  Database db(path_);
  db.create_table(alumnos(), kind::kHeap);
  db.table("alumnos").insert(Record{1, std::string("ana")});
  db.drop_table("alumnos");
  EXPECT_FALSE(db.is_open("alumnos"));
  EXPECT_FALSE(fs::exists(dir_ / "alumnos.heap"));
  EXPECT_FALSE(db.catalog().has_table("alumnos"));
  EXPECT_THROW(db.table("alumnos"), SchemaError);
}

TEST_F(DatabaseTest, RechazaLoQueNoPuedeAbrir) {
  Database db(path_);
  EXPECT_THROW(db.table("noexiste"), SchemaError);
  EXPECT_THROW(db.create_table(alumnos(), "csv"), SchemaError);
  // El B+ agrupado ya existe (#15); lo que no, es una organizacion inventada.
  EXPECT_NO_THROW(db.create_table(cursos(), kind::kBPlusClustered));
}

TEST_F(DatabaseTest, ElCursorFuncionaAtravesDelHandle) {
  Database db(path_);
  TableFile& t = db.create_table(alumnos(), kind::kSequential);
  for (std::int32_t i = 1; i <= 100; ++i) t.insert(Record{i, std::string("n")});
  auto c = t.cursor();
  Record r;
  std::size_t n = 0;
  while (c->next(r)) ++n;
  EXPECT_EQ(n, 100u);
}

}  // namespace
}  // namespace quipudb
