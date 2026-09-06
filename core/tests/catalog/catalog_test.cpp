// Pruebas de Catalog (issue #7): el catalogo persiste en disco y se recarga
// igual al abrir la base.

#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>
#include <string>

#include "quipudb/catalog/catalog.hpp"
#include "quipudb/catalog/table.hpp"
#include "quipudb/error.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

Schema alumnos() {
  return Schema{
      .table_name = "alumnos",
      .columns = {{"codigo", DataType::Int},
                  {"nombre", DataType::Varchar, 20},
                  {"promedio", DataType::Double},
                  {"activo", DataType::Bool},
                  {"ingreso", DataType::Date}},
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

class CatalogTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_catalog_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
    path_ = dir_ / "catalogo.txt";
  }
  void TearDown() override { fs::remove_all(dir_); }

  fs::path dir_;
  fs::path path_;
};

TEST_F(CatalogTest, EmpiezaVacioYNoCreaArchivoHastaElPrimerCambio) {
  Catalog c(path_);
  EXPECT_EQ(c.size(), 0u);
  EXPECT_FALSE(fs::exists(path_));
  c.create_table(alumnos(), kind::kHeap);
  EXPECT_TRUE(fs::exists(path_));
  EXPECT_FALSE(fs::exists(path_.string() + ".tmp")) << "el guardado atomico no deja temporales";
}

TEST_F(CatalogTest, CreateTableRegistraEsquemaStorageYArchivo) {
  Catalog c(path_);
  const auto& t = c.create_table(alumnos(), kind::kSequential);
  EXPECT_EQ(t.schema.table_name, "alumnos");
  EXPECT_EQ(t.schema.columns.size(), 5u);
  EXPECT_EQ(t.storage, kind::kSequential);
  EXPECT_EQ(t.file, "alumnos.sequential");
  EXPECT_EQ(c.resolve(t.file), dir_ / "alumnos.sequential");
  EXPECT_TRUE(c.has_table("alumnos"));
  EXPECT_FALSE(c.has_table("otra"));
  EXPECT_EQ(c.table("alumnos").schema.key().name, "codigo");
  EXPECT_THROW(static_cast<void>(c.table("otra")), SchemaError);
}

TEST_F(CatalogTest, PersisteYSeRecargaIgual) {
  {
    Catalog c(path_);
    c.create_table(alumnos(), kind::kHeap);
    c.create_table(cursos(), kind::kBPlusClustered);
    c.create_index("alumnos", "por_promedio", "promedio", kind::kBPlusUnclustered);
    c.create_index("alumnos", "por_nombre", "nombre", kind::kExtendibleHash);
  }
  Catalog c(path_);
  ASSERT_EQ(c.size(), 2u);
  EXPECT_EQ(c.table_names(), (std::vector<std::string>{"alumnos", "cursos"}));

  const auto& a = c.table("alumnos");
  EXPECT_EQ(a.storage, kind::kHeap);
  EXPECT_EQ(a.file, "alumnos.heap");
  ASSERT_EQ(a.schema.columns.size(), 5u);
  EXPECT_EQ(a.schema.columns[1].type, DataType::Varchar);
  EXPECT_EQ(a.schema.columns[1].length, 20);
  EXPECT_EQ(a.schema.columns[2].type, DataType::Double);
  EXPECT_EQ(a.schema.columns[3].type, DataType::Bool);
  EXPECT_EQ(a.schema.columns[4].type, DataType::Date);
  EXPECT_EQ(a.schema.key_column, 0u);
  EXPECT_EQ(a.schema.record_size(), 37u);
  ASSERT_EQ(a.indexes.size(), 2u);
  EXPECT_EQ(a.indexes[0].name, "por_promedio");
  EXPECT_EQ(a.indexes[0].column, 2u);
  EXPECT_EQ(a.indexes[0].kind, kind::kBPlusUnclustered);
  EXPECT_EQ(a.indexes[0].file, "alumnos.por_promedio.bplus_unclustered")
      << "el archivo se nombra por el indice, no por la columna";
  EXPECT_EQ(a.index_on(1)->name, "por_nombre");
  EXPECT_EQ(a.index_on(0), nullptr);
  EXPECT_EQ(a.index("por_nombre")->kind, kind::kExtendibleHash);

  const auto& k = c.table("cursos");
  EXPECT_EQ(k.storage, kind::kBPlusClustered);
  EXPECT_EQ(k.schema.columns[0].length, 8);
  EXPECT_TRUE(k.indexes.empty());
}

TEST_F(CatalogTest, ElArchivoEsLegible) {
  Catalog c(path_);
  c.create_table(cursos(), kind::kHeap);
  std::ifstream f(path_);
  std::string l1, l2, l3, l4;
  std::getline(f, l1);
  std::getline(f, l2);
  std::getline(f, l3);
  std::getline(f, l4);
  EXPECT_EQ(l1, "quipudb-catalog 2");
  EXPECT_EQ(l2, "table cursos heap cursos.heap 0 4096 2");
  EXPECT_EQ(l3, "column codigo VARCHAR 8");
  EXPECT_EQ(l4, "column creditos INT");
}

TEST_F(CatalogTest, DropTableYDropIndex) {
  Catalog c(path_);
  c.create_table(alumnos(), kind::kHeap);
  c.create_index("alumnos", "ix", "promedio", kind::kBPlusUnclustered);
  c.drop_index("alumnos", "ix");
  EXPECT_TRUE(c.table("alumnos").indexes.empty());
  EXPECT_THROW(c.drop_index("alumnos", "ix"), SchemaError);
  c.drop_table("alumnos");
  EXPECT_EQ(c.size(), 0u);
  EXPECT_THROW(c.drop_table("alumnos"), SchemaError);
  Catalog reabierto(path_);
  EXPECT_EQ(reabierto.size(), 0u);
}

TEST_F(CatalogTest, RechazaLoQueNoDebeEntrar) {
  Catalog c(path_);
  c.create_table(alumnos(), kind::kHeap);
  EXPECT_THROW(c.create_table(alumnos(), kind::kHeap), SchemaError);  // repetida
  EXPECT_THROW(c.create_table(cursos(), "csv"), SchemaError);  // storage desconocido
  EXPECT_THROW(c.create_table(cursos(), kind::kBPlusUnclustered),
               SchemaError);  // un indice secundario no es una tabla

  EXPECT_THROW(c.create_index("nadie", "ix", "promedio", kind::kBPlusUnclustered), SchemaError);
  EXPECT_THROW(c.create_index("alumnos", "ix", "edad", kind::kBPlusUnclustered), SchemaError);
  EXPECT_THROW(c.create_index("alumnos", "ix", "promedio", kind::kHeap), SchemaError);
  EXPECT_THROW(c.create_index("alumnos", "mal nombre", "promedio", kind::kBPlusUnclustered),
               SchemaError);
  c.create_index("alumnos", "ix", "promedio", kind::kBPlusUnclustered);
  EXPECT_THROW(c.create_index("alumnos", "ix", "nombre", kind::kBPlusUnclustered), SchemaError);
}

TEST_F(CatalogTest, ValidaElEsquema) {
  auto s = alumnos();
  EXPECT_NO_THROW(Catalog::validate(s));

  s.table_name = "1abc";
  EXPECT_THROW(Catalog::validate(s), SchemaError);
  s.table_name = "con espacio";
  EXPECT_THROW(Catalog::validate(s), SchemaError);

  s = alumnos();
  s.columns.clear();
  EXPECT_THROW(Catalog::validate(s), SchemaError);

  s = alumnos();
  s.columns[1].name = "codigo";  // repetida
  EXPECT_THROW(Catalog::validate(s), SchemaError);

  s = alumnos();
  s.columns[1].length = 0;  // VARCHAR sin longitud
  EXPECT_THROW(Catalog::validate(s), SchemaError);

  s = alumnos();
  s.columns[0].length = 4;  // INT con longitud
  EXPECT_THROW(Catalog::validate(s), SchemaError);

  s = alumnos();
  s.key_column = 5;
  EXPECT_THROW(Catalog::validate(s), SchemaError);

  EXPECT_TRUE(Catalog::is_identifier("_x1"));
  EXPECT_FALSE(Catalog::is_identifier(""));
  EXPECT_FALSE(Catalog::is_identifier("a-b"));
  EXPECT_FALSE(Catalog::is_identifier(std::string(65, 'a')));
}

TEST_F(CatalogTest, ArchivoCorruptoLanzaAlAbrir) {
  const auto escribir = [&](const std::string& contenido) {
    std::ofstream f(path_, std::ios::binary | std::ios::trunc);
    f << contenido;
  };
  escribir("");
  EXPECT_THROW(Catalog{path_}, IoError);
  escribir("otro-formato 1\n");
  EXPECT_THROW(Catalog{path_}, IoError);
  escribir("quipudb-catalog 3\n");
  EXPECT_THROW(Catalog{path_}, IoError);
  escribir("quipudb-catalog 2\ntable t heap t.heap 0 4096 2\ncolumn a INT\n");  // falta una
  EXPECT_THROW(Catalog{path_}, IoError);
  escribir("quipudb-catalog 2\ntable t heap t.heap 0 4096 1\ncolumn a TEXTO\n");
  EXPECT_THROW(Catalog{path_}, IoError);
  escribir("quipudb-catalog 2\ntable t heap t.heap 3 4096 1\ncolumn a INT\n");  // clave mala
  EXPECT_THROW(Catalog{path_}, IoError);
  escribir("quipudb-catalog 2\nindex ix t a bplus_unclustered f\n");  // tabla inexistente
  EXPECT_THROW(Catalog{path_}, IoError);
  escribir("quipudb-catalog 2\nbasura\n");
  EXPECT_THROW(Catalog{path_}, IoError);
  escribir("quipudb-catalog 2\ntable t heap t.heap 0 99 1\ncolumn a INT\n");  // page_size malo
  EXPECT_THROW(Catalog{path_}, IoError);
  escribir("quipudb-catalog 2\ntable t heap t.heap 0 4096 1\ncolumn a INT\n\n");  // valido
  EXPECT_NO_THROW(Catalog{path_});
}


// --- regresiones de la auditoria de 2.1.1 (#55) ---

TEST_F(CatalogTest, DosIndicesSobreLaMismaColumnaNoCompartenArchivo) {
  // I5: el archivo se nombraba por la columna, que no es unica dentro de la
  // tabla, asi que dos indices se pisaban en disco.
  Catalog c(path_);
  c.create_table(alumnos(), kind::kHeap);
  const auto a = c.create_index("alumnos", "por_promedio_bmas", "promedio", kind::kBPlusUnclustered);
  const auto b = c.create_index("alumnos", "por_promedio_hash", "promedio", kind::kExtendibleHash);
  EXPECT_NE(a.file, b.file);
  EXPECT_EQ(a.file, "alumnos.por_promedio_bmas.bplus_unclustered");
  EXPECT_EQ(b.file, "alumnos.por_promedio_hash.extendible_hash");
}

TEST_F(CatalogTest, SoloSePuedeIndexarUnHeapFile) {
  // Un indice secundario guarda RIDs; en el secuencial y en el B+ agrupado
  // los registros se mueven de sitio y el indice apuntaria al vecino sin
  // lanzar nada, que es el peor modo de fallo para el 2.1.6.
  Catalog c(path_);
  c.create_table(alumnos(), kind::kHeap);
  auto s = cursos();
  s.table_name = "cursos_seq";
  c.create_table(s, kind::kSequential);
  auto b = cursos();
  b.table_name = "cursos_bmas";
  c.create_table(b, kind::kBPlusClustered);

  EXPECT_NO_THROW(c.create_index("alumnos", "ix", "promedio", kind::kBPlusUnclustered));
  EXPECT_THROW(c.create_index("cursos_seq", "ix", "creditos", kind::kBPlusUnclustered),
               SchemaError);
  EXPECT_THROW(c.create_index("cursos_bmas", "ix", "creditos", kind::kExtendibleHash),
               SchemaError);
  EXPECT_TRUE(c.table("cursos_seq").indexes.empty());
}

}  // namespace
}  // namespace quipudb
