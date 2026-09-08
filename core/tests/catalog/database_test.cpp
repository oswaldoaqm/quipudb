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

// ---------------------------------------------------------------------------
// Indices abiertos desde el catalogo
// ---------------------------------------------------------------------------

/// Esquema con una columna que se repite: es lo que se indexa de verdad con un
/// indice secundario.
Schema empleados() {
  return Schema{
      .table_name = "empleados",
      .columns = {{"codigo", DataType::Int},
                  {"area", DataType::Varchar, 12},
                  {"edad", DataType::Int}},
      .key_column = 0,
  };
}

const std::vector<std::string> kAreas{"ventas", "soporte", "legal"};

Record empleado(std::int32_t c) {
  return {c, kAreas[static_cast<std::size_t>(c) % kAreas.size()], 20 + (c % 15)};
}

TEST_F(DatabaseTest, AbreCadaTipoDeIndiceSegunElCatalogo) {
  Database db(path_);
  TableFile& t = db.create_table(empleados(), kind::kHeap);
  for (std::int32_t c = 1; c <= 200; ++c) t.insert(empleado(c));

  Index& bp = db.create_index("empleados", "por_edad", "edad", kind::kBPlusUnclustered);
  Index& hs = db.create_index("empleados", "por_area", "area", kind::kExtendibleHash);

  EXPECT_EQ(bp.kind(), kind::kBPlusUnclustered);
  EXPECT_EQ(hs.kind(), kind::kExtendibleHash);
  EXPECT_TRUE(bp.supports_range());
  EXPECT_FALSE(hs.supports_range());
  // Se construyeron sobre los datos que la tabla YA tenia.
  EXPECT_EQ(bp.size(), 200u);
  EXPECT_EQ(hs.size(), 200u);
}

TEST_F(DatabaseTest, DevuelveSiempreElMismoObjetoParaUnIndice) {
  Database db(path_);
  TableFile& t = db.create_table(empleados(), kind::kHeap);
  for (std::int32_t c = 1; c <= 50; ++c) t.insert(empleado(c));
  Index& a = db.create_index("empleados", "por_edad", "edad", kind::kExtendibleHash);
  Index& b = db.index("empleados", "por_edad");
  EXPECT_EQ(&a, &b);
}

TEST_F(DatabaseTest, ElIndiceSeMontaSobreElMismoHandleQueDevuelveTable) {
  // Es la razon de ser de esta clase. Si el indice se montara sobre un handle
  // propio, los RID que guarda apuntarian a un estado que el handle de
  // `table()` no conoce, y `lookup` devolveria basura o lanzaria.
  Database db(path_);
  TableFile& t = db.create_table(empleados(), kind::kHeap);
  for (std::int32_t c = 1; c <= 100; ++c) t.insert(empleado(c));
  db.create_index("empleados", "por_edad", "edad", kind::kExtendibleHash);

  // Se inserta por el handle de la tabla y se avisa al indice, que es lo que
  // hara el planner (2.1.3).
  const RID rid = db.table("empleados").insert(empleado(101));
  db.index("empleados", "por_edad").insert(Value{20 + (101 % 15)}, rid);

  const auto rids = db.index("empleados", "por_edad").search(Value{20 + (101 % 15)});
  ASSERT_FALSE(rids.empty());
  bool encontrado = false;
  for (const RID r : rids) {
    const auto rec = db.table("empleados").read(r);
    ASSERT_TRUE(rec.has_value()) << "el indice apunta a un registro que la tabla no ve";
    if (std::get<std::int32_t>((*rec)[0]) == 101) encontrado = true;
  }
  EXPECT_TRUE(encontrado);
}

TEST_F(DatabaseTest, LosIndicesSobrevivenAlCerrarYReabrir) {
  {
    Database db(path_);
    TableFile& t = db.create_table(empleados(), kind::kHeap);
    for (std::int32_t c = 1; c <= 300; ++c) t.insert(empleado(c));
    db.create_index("empleados", "por_area", "area", kind::kExtendibleHash);
    db.flush();
  }
  Database db(path_);
  Index& ix = db.index("empleados", "por_area");
  EXPECT_EQ(ix.kind(), kind::kExtendibleHash);
  EXPECT_EQ(ix.size(), 300u);
  const auto rids = ix.search(Value{std::string("ventas")});
  EXPECT_FALSE(rids.empty());
  for (const RID r : rids) {
    const auto rec = db.table("empleados").read(r);
    ASSERT_TRUE(rec.has_value());
    EXPECT_EQ(std::get<std::string>((*rec)[1]), "ventas");
  }
}

TEST_F(DatabaseTest, DropIndexCierraElHandleYBorraElArchivoSinTocarLaTabla) {
  Database db(path_);
  TableFile& t = db.create_table(empleados(), kind::kHeap);
  for (std::int32_t c = 1; c <= 40; ++c) t.insert(empleado(c));
  db.create_index("empleados", "por_edad", "edad", kind::kExtendibleHash);

  const auto archivo = db.catalog().resolve(
      db.catalog().table("empleados").index("por_edad")->file);
  ASSERT_TRUE(fs::exists(archivo));

  db.drop_index("empleados", "por_edad");
  EXPECT_FALSE(fs::exists(archivo));
  EXPECT_EQ(db.catalog().table("empleados").index("por_edad"), nullptr);
  EXPECT_THROW(db.index("empleados", "por_edad"), SchemaError);
  // La tabla sigue entera.
  EXPECT_EQ(db.table("empleados").size(), 40u);
}

TEST_F(DatabaseTest, DropTableSeLlevaLosArchivosDeSusIndices) {
  // Los RID de un indice no apuntan a nada si su tabla desaparece: dejar el
  // archivo seria dejar basura que el catalogo ya no menciona.
  Database db(path_);
  TableFile& t = db.create_table(empleados(), kind::kHeap);
  for (std::int32_t c = 1; c <= 40; ++c) t.insert(empleado(c));
  db.create_index("empleados", "por_edad", "edad", kind::kExtendibleHash);
  db.create_index("empleados", "por_area", "area", kind::kBPlusUnclustered);

  const auto a1 = db.catalog().resolve(db.catalog().table("empleados").index("por_edad")->file);
  const auto a2 = db.catalog().resolve(db.catalog().table("empleados").index("por_area")->file);
  ASSERT_TRUE(fs::exists(a1));
  ASSERT_TRUE(fs::exists(a2));

  db.drop_table("empleados");
  EXPECT_FALSE(fs::exists(a1));
  EXPECT_FALSE(fs::exists(a2));
  EXPECT_TRUE(db.open_indexes().empty());
}

TEST_F(DatabaseTest, CerrarUnaTablaCierraAntesSusIndices) {
  // Un indice guarda un puntero a su tabla: si la tabla se cerrara primero, el
  // indice quedaria colgado. Con ASan esta prueba es la que lo detecta.
  Database db(path_);
  TableFile& t = db.create_table(empleados(), kind::kHeap);
  for (std::int32_t c = 1; c <= 60; ++c) t.insert(empleado(c));
  db.create_index("empleados", "por_edad", "edad", kind::kExtendibleHash);
  ASSERT_EQ(db.open_indexes().size(), 1u);

  db.close("empleados");
  EXPECT_TRUE(db.open_indexes().empty());
  EXPECT_FALSE(db.is_open("empleados"));

  // Y se puede volver a abrir sin rastro del anterior.
  Index& ix = db.index("empleados", "por_edad");
  EXPECT_EQ(ix.size(), 60u);
  EXPECT_EQ(db.open_indexes().size(), 1u);
}

TEST_F(DatabaseTest, RechazaIndicesQueNoPuedeAbrir) {
  Database db(path_);
  db.create_table(empleados(), kind::kHeap);
  EXPECT_THROW(db.index("empleados", "noexiste"), SchemaError);
  EXPECT_THROW(db.index("noexiste", "da_igual"), SchemaError);
  EXPECT_THROW(db.create_index("empleados", "x", "noexiste", kind::kExtendibleHash), SchemaError);
  EXPECT_THROW(db.create_index("empleados", "x", "edad", "arbol_magico"), SchemaError);
  // Solo sobre heap: en las demas organizaciones los RID se mueven.
  db.create_table(cursos(), kind::kSequential);
  EXPECT_THROW(db.create_index("cursos", "por_creditos", "creditos", kind::kExtendibleHash),
               SchemaError);
}

TEST_F(DatabaseTest, ElFlushVaciaTambienLosIndices) {
  Database db(path_);
  TableFile& t = db.create_table(empleados(), kind::kHeap);
  for (std::int32_t c = 1; c <= 120; ++c) t.insert(empleado(c));
  db.create_index("empleados", "por_area", "area", kind::kExtendibleHash);
  db.flush();

  // Otro proceso abriria el archivo y veria las 120 entradas sin que nadie
  // haya cerrado el handle.
  Database otra(path_);
  EXPECT_EQ(otra.index("empleados", "por_area").size(), 120u);
}

}  // namespace
}  // namespace quipudb
