// Pruebas del JOIN externo: hash join por particiones e index nested loop
// (issue #22).
//
// El oraculo es `std::unordered_multimap`, igual que el `std::multimap` que
// encontro el bug de las ramas repetidas del #16. Se compara la salida del
// join contra la de un join en memoria escrito aparte, ordenando las dos: un
// join no promete orden, asi que compararlas tal cual seria probar el orden en
// vez de las coincidencias.

#include <gtest/gtest.h>

#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <numeric>
#include <random>
#include <string>
#include <unordered_map>
#include <vector>

#include "quipudb/catalog/database.hpp"
#include "quipudb/error.hpp"
#include "quipudb/external/external_join.hpp"
#include "quipudb/storage/heap_file.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

// ---------------------------------------------------------------------------
// Esquemas y datos
// ---------------------------------------------------------------------------

Schema alumnos() {
  return Schema{
      .table_name = "alumnos",
      .columns = {{"codigo", DataType::Int}, {"nombre", DataType::Varchar, 12}},
      .key_column = 0,
  };
}

Schema notas() {
  return Schema{
      .table_name = "notas",
      .columns = {{"id", DataType::Int}, {"codigo", DataType::Int}, {"nota", DataType::Int}},
      .key_column = 0,
  };
}

Record alumno(std::int32_t codigo) {
  return {codigo, "a" + std::to_string(codigo)};
}

/// `codigo` de la nota `i`, con `distintos` valores posibles: controla cuantas
/// notas casan con cada alumno.
Record nota(std::int32_t i, std::int32_t distintos) {
  return {i, i % distintos, (i % 21)};
}

std::vector<Record> drenar(RecordSource& s) {
  std::vector<Record> out;
  Record r;
  while (s.next(r)) out.push_back(r);
  return out;
}

/// El oraculo: el mismo join, en memoria, escrito de la forma mas aburrida
/// posible. Si el externo y este no coinciden, uno de los dos esta mal y el
/// aburrido es el que tiene menos sitios donde equivocarse.
std::vector<Record> join_en_memoria(const std::vector<Record>& izq, std::size_t col_izq,
                                    const std::vector<Record>& der, std::size_t col_der) {
  std::unordered_multimap<std::int32_t, const Record*> tabla;
  for (const auto& d : der) tabla.emplace(std::get<std::int32_t>(d[col_der]), &d);

  std::vector<Record> out;
  for (const auto& i : izq) {
    const auto rango = tabla.equal_range(std::get<std::int32_t>(i[col_izq]));
    for (auto it = rango.first; it != rango.second; ++it) {
      Record r = i;
      r.insert(r.end(), it->second->begin(), it->second->end());
      out.push_back(std::move(r));
    }
  }
  return out;
}

/// Un join no promete orden: para comparar dos resultados hay que ordenarlos
/// los dos. Se ordena por el registro entero para que dos filas distintas con
/// la misma clave no se confundan.
void ordenar(std::vector<Record>& rs) {
  std::sort(rs.begin(), rs.end(), [](const Record& a, const Record& b) {
    for (std::size_t i = 0; i < a.size() && i < b.size(); ++i) {
      const int c = compare(a[i], b[i]);
      if (c != 0) return c < 0;
    }
    return a.size() < b.size();
  });
}

void esperar_mismo_join(std::vector<Record> obtenido, std::vector<Record> esperado) {
  ordenar(obtenido);
  ordenar(esperado);
  ASSERT_EQ(obtenido.size(), esperado.size());
  for (std::size_t i = 0; i < obtenido.size(); ++i) {
    ASSERT_EQ(obtenido[i].size(), esperado[i].size()) << "fila " << i;
    for (std::size_t c = 0; c < obtenido[i].size(); ++c) {
      EXPECT_EQ(compare(obtenido[i][c], esperado[i][c]), 0) << "fila " << i << " columna " << c;
    }
  }
}

class ExternalJoinTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_join_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
  }
  void TearDown() override { fs::remove_all(dir_); }

  [[nodiscard]] std::size_t temporales_vivos() const {
    std::size_t n = 0;
    for (const auto& e : fs::directory_iterator(dir_)) {
      if (e.path().extension() == ".part") ++n;
    }
    return n;
  }

  fs::path dir_;
};

// ---------------------------------------------------------------------------
// Correccion contra el oraculo
// ---------------------------------------------------------------------------

TEST_F(ExternalJoinTest, HashJoinCoincideConElJoinEnMemoria) {
  std::vector<Record> izq;
  for (std::int32_t i = 0; i < 500; ++i) izq.push_back(alumno(i));
  std::vector<Record> der;
  for (std::int32_t i = 0; i < 1500; ++i) der.push_back(nota(i, 500));

  auto fi = source_of(izq);
  auto fd = source_of(der);
  ExternalJoin j(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kHash, 8, kDefaultPageSize,
                 dir_);
  auto salida = j.joined(*fi, *fd);

  esperar_mismo_join(drenar(*salida), join_en_memoria(izq, 0, der, 1));
  EXPECT_EQ(j.used(), ExternalJoin::Strategy::kHash);
  EXPECT_EQ(j.structure(), "external_hash");
  EXPECT_EQ(j.left_rows(), 500u);
  EXPECT_EQ(j.right_rows(), 1500u);
  EXPECT_EQ(j.output_rows(), 1500u);
}

// El caso que el criterio del issue manda probar: dos tablas de 10 000.
TEST_F(ExternalJoinTest, HashJoinConDiezMilPorLadoCoincideConElOraculo) {
  std::vector<Record> izq;
  for (std::int32_t i = 0; i < 10000; ++i) izq.push_back(alumno(i));
  std::vector<Record> der;
  for (std::int32_t i = 0; i < 10000; ++i) der.push_back(nota(i, 10000));

  auto fi = source_of(izq);
  auto fd = source_of(der);
  // Buffers bajos a proposito: con los 64 por omision las 20 000 filas caben
  // en memoria y no se probaria el particionado, que es lo que este issue
  // construye. Con 8 buffers hacen falta varias particiones y varios bloques.
  ExternalJoin j(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kHash, 8, kDefaultPageSize,
                 dir_);
  auto salida = j.joined(*fi, *fd);

  esperar_mismo_join(drenar(*salida), join_en_memoria(izq, 0, der, 1));
  EXPECT_EQ(j.output_rows(), 10000u);
  // Si esto fuera 0, no se habria escrito ninguna particion y el "external"
  // del nombre seria mentira.
  EXPECT_GT(j.stats().pages_written, 0u);
  EXPECT_GT(j.stats().pages_read, 0u);
}

TEST_F(ExternalJoinTest, ClavesRepetidasEnLosDosLadosDanElProductoPorClave) {
  // Donde un hash join se equivoca de verdad: si las dos partes de una clave
  // tienen varias filas, la salida es el PRODUCTO de las dos, no un
  // emparejamiento uno a uno. Con claves unicas de un lado esto pasa
  // desapercibido.
  std::vector<Record> izq;
  for (std::int32_t i = 0; i < 60; ++i) izq.push_back({i % 5, "x" + std::to_string(i)});
  std::vector<Record> der;
  for (std::int32_t i = 0; i < 40; ++i) der.push_back(nota(i, 5));

  auto fi = source_of(izq);
  auto fd = source_of(der);
  ExternalJoin j(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kHash, 4, kDefaultPageSize,
                 dir_);
  auto salida = j.joined(*fi, *fd);

  auto obtenido = drenar(*salida);
  // 12 filas por clave a la izquierda por 8 a la derecha, en 5 claves.
  EXPECT_EQ(obtenido.size(), 12u * 8u * 5u);
  esperar_mismo_join(std::move(obtenido), join_en_memoria(izq, 0, der, 1));
}

TEST_F(ExternalJoinTest, SinCoincidenciasDevuelveVacio) {
  std::vector<Record> izq;
  for (std::int32_t i = 0; i < 100; ++i) izq.push_back(alumno(i));
  std::vector<Record> der;
  for (std::int32_t i = 0; i < 100; ++i) der.push_back(Record{i, i + 10000, 15});

  auto fi = source_of(izq);
  auto fd = source_of(der);
  ExternalJoin j(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kHash, 4, kDefaultPageSize,
                 dir_);
  auto salida = j.joined(*fi, *fd);
  EXPECT_TRUE(drenar(*salida).empty());
  EXPECT_EQ(j.output_rows(), 0u);
}

TEST_F(ExternalJoinTest, UnLadoVacioDevuelveVacio) {
  std::vector<Record> izq;
  for (std::int32_t i = 0; i < 100; ++i) izq.push_back(alumno(i));

  auto fi = source_of(izq);
  auto fd = source_of(std::vector<Record>{});
  ExternalJoin j(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kHash, 4, kDefaultPageSize,
                 dir_);
  auto salida = j.joined(*fi, *fd);
  EXPECT_TRUE(drenar(*salida).empty());
}

// ---------------------------------------------------------------------------
// La particion que no cabe
// ---------------------------------------------------------------------------

TEST_F(ExternalJoinTest, UnaSolaClaveNoCabeYSeRecorrePorBloques) {
  // El caso degenerado: TODAS las filas comparten clave, asi que caen en la
  // misma particion y re-particionar no separaria nada (es el problema de las
  // claves repetidas del #18). El recorrido por bloques tiene que terminar
  // igual y dar el producto completo.
  std::vector<Record> izq;
  for (std::int32_t i = 0; i < 200; ++i) izq.push_back({7, "x" + std::to_string(i)});
  std::vector<Record> der;
  for (std::int32_t i = 0; i < 200; ++i) der.push_back(Record{i, 7, 15});

  auto fi = source_of(izq);
  auto fd = source_of(der);
  // Paginas de 256 bytes, como en las pruebas del sort (#20): con las de 4 KB
  // por omision estas 200 filas caben en un solo bloque y la prueba pasaria
  // sin haber ejercitado nunca el recorrido por bloques, que es lo que dice
  // probar. Es el mismo error que el `free_space` 504 del I6.
  ExternalJoin j(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kHash,
                 ExternalJoin::kMinBuffers, 256, dir_);
  auto salida = j.joined(*fi, *fd);

  auto obtenido = drenar(*salida);
  EXPECT_EQ(obtenido.size(), 200u * 200u);
  esperar_mismo_join(std::move(obtenido), join_en_memoria(izq, 0, der, 1));
  EXPECT_EQ(j.blocked_partitions(), 1u) << "la particion unica tuvo que recorrerse por bloques";
}

TEST_F(ExternalJoinTest, SinDesbordeNoSeCuentaNingunaParticionPorBloques) {
  // El contrapunto del anterior: `blocked_partitions()` tiene que ser 0 cuando
  // todo cabe, o el numero no distinguiria nada.
  std::vector<Record> izq;
  for (std::int32_t i = 0; i < 40; ++i) izq.push_back(alumno(i));
  std::vector<Record> der;
  for (std::int32_t i = 0; i < 40; ++i) der.push_back(nota(i, 40));

  auto fi = source_of(izq);
  auto fd = source_of(der);
  ExternalJoin j(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kHash, 16, kDefaultPageSize,
                 dir_);
  auto salida = j.joined(*fi, *fd);
  drenar(*salida);
  EXPECT_EQ(j.blocked_partitions(), 0u);
}

// ---------------------------------------------------------------------------
// Temporales
// ---------------------------------------------------------------------------

TEST_F(ExternalJoinTest, NoDejaParticionesNiSiquieraSiFalla) {
  // Igual que el #20: una excepcion a medio particionar no puede dejar 2p
  // archivos regados. Se lanza desde la fuente, que es la unica forma de
  // interrumpir el particionado por la mitad.
  class FuenteQueLanza final : public RecordSource {
   public:
    bool next(Record& out) override {
      if (++n_ > 50) throw IoError("fuente rota a proposito");
      out = alumno(static_cast<std::int32_t>(n_));
      return true;
    }

   private:
    std::size_t n_ = 0;
  };

  {
    FuenteQueLanza rota;
    auto fd = source_of(std::vector<Record>{});
    ExternalJoin j(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kHash, 4, kDefaultPageSize,
                   dir_);
    EXPECT_THROW(static_cast<void>(j.joined(rota, *fd)), IoError);
  }
  EXPECT_EQ(temporales_vivos(), 0u);
}

TEST_F(ExternalJoinTest, LasParticionesSeBorranAlDestruirElJoin) {
  std::vector<Record> izq;
  for (std::int32_t i = 0; i < 200; ++i) izq.push_back(alumno(i));
  std::vector<Record> der;
  for (std::int32_t i = 0; i < 200; ++i) der.push_back(nota(i, 200));

  {
    auto fi = source_of(izq);
    auto fd = source_of(der);
    ExternalJoin j(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kHash, 4, kDefaultPageSize,
                   dir_);
    auto salida = j.joined(*fi, *fd);
    drenar(*salida);
    EXPECT_GT(j.temp_bytes(), 0u);
    EXPECT_GT(temporales_vivos(), 0u);
  }
  EXPECT_EQ(temporales_vivos(), 0u);
}

// ---------------------------------------------------------------------------
// Esquema de salida
// ---------------------------------------------------------------------------

TEST_F(ExternalJoinTest, SoloSePrefijanLasColumnasQueColisionan) {
  ExternalJoin j(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kHash, 4, kDefaultPageSize,
                 dir_);
  const Schema& s = j.output_schema();
  ASSERT_EQ(s.columns.size(), 5u);
  // "codigo" esta en los dos lados: se prefija en ambos.
  EXPECT_EQ(s.columns[0].name, "alumnos.codigo");
  // "nombre", "id" y "nota" estan en uno solo: se quedan como estaban.
  EXPECT_EQ(s.columns[1].name, "nombre");
  EXPECT_EQ(s.columns[2].name, "id");
  EXPECT_EQ(s.columns[3].name, "notas.codigo");
  EXPECT_EQ(s.columns[4].name, "nota");
}

TEST_F(ExternalJoinTest, LaSalidaSeValidaContraSuPropioEsquema) {
  std::vector<Record> izq{alumno(1)};
  std::vector<Record> der{nota(1, 2)};
  auto fi = source_of(izq);
  auto fd = source_of(der);
  ExternalJoin j(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kHash, 4, kDefaultPageSize,
                 dir_);
  auto salida = j.joined(*fi, *fd);
  for (const auto& r : drenar(*salida)) {
    EXPECT_NO_THROW(j.output_schema().validate(r));
  }
}

// ---------------------------------------------------------------------------
// Validacion
// ---------------------------------------------------------------------------

TEST_F(ExternalJoinTest, RechazaColumnasDeTipoDistinto) {
  // Juntar un INT con un VARCHAR no da cero coincidencias: `compare` define un
  // orden entre tipos distintos, asi que daria un resultado silenciosamente
  // incorrecto. Tiene que fallar al construir.
  EXPECT_THROW(ExternalJoin(alumnos(), 1, notas(), 1, ExternalJoin::Strategy::kHash, 4,
                            kDefaultPageSize, dir_),
               SchemaError);
}

TEST_F(ExternalJoinTest, RechazaColumnaInexistente) {
  EXPECT_THROW(ExternalJoin(alumnos(), 9, notas(), 1, ExternalJoin::Strategy::kHash, 4,
                            kDefaultPageSize, dir_),
               SchemaError);
}

TEST_F(ExternalJoinTest, RechazaBuffersInsuficientes) {
  EXPECT_THROW(ExternalJoin(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kHash, 2,
                            kDefaultPageSize, dir_),
               SchemaError);
}

TEST_F(ExternalJoinTest, PedirIndexNestedSinSondaEsUnsupported) {
  std::vector<Record> izq{alumno(1)};
  std::vector<Record> der{nota(1, 2)};
  auto fi = source_of(izq);
  auto fd = source_of(der);
  ExternalJoin j(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kIndexNested, 4,
                 kDefaultPageSize, dir_);
  EXPECT_THROW(static_cast<void>(j.joined(*fi, *fd)), Unsupported);
}

// ---------------------------------------------------------------------------
// Index nested loop
// ---------------------------------------------------------------------------

/// Monta alumnos en un heap con un indice secundario sobre `codigo`, que es lo
/// que la regla del #3 permite: un indice secundario solo sobre heap file.
class JoinConIndiceTest : public ExternalJoinTest {
 protected:
  void montar(std::string_view kind_indice, std::int32_t n_notas, std::int32_t distintos) {
    db_ = std::make_unique<Database>(dir_ / "catalogo.txt");
    db_->create_table(notas(), kind::kHeap);
    TableFile& t = db_->table("notas");
    for (std::int32_t i = 0; i < n_notas; ++i) {
      der_.push_back(nota(i, distintos));
      t.insert(der_.back());
    }
    ix_ = &db_->create_index("notas", "por_codigo", "codigo", kind_indice);
    sonda_ = probe_of(*ix_, t);
  }

  std::unique_ptr<Database> db_;
  Index* ix_ = nullptr;
  std::unique_ptr<JoinProbe> sonda_;
  std::vector<Record> der_;
};

TEST_F(JoinConIndiceTest, IndexNestedLoopCoincideConElJoinEnMemoria) {
  montar(kind::kBPlusUnclustered, 800, 200);
  std::vector<Record> izq;
  for (std::int32_t i = 0; i < 30; ++i) izq.push_back(alumno(i));

  auto fi = source_of(izq);
  auto fd = source_of(der_);
  ExternalJoin j(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kIndexNested, 8,
                 kDefaultPageSize, dir_);
  auto salida = j.joined(*fi, *sonda_, *fd, izq.size());

  esperar_mismo_join(drenar(*salida), join_en_memoria(izq, 0, der_, 1));
  EXPECT_EQ(j.used(), ExternalJoin::Strategy::kIndexNested);
  EXPECT_EQ(j.structure(), kind::kBPlusUnclustered);
  // El INL no particiona: si escribio algo, no es un INL.
  EXPECT_EQ(j.stats().pages_written, 0u);
  EXPECT_EQ(j.partitions(), 0u);
  EXPECT_EQ(j.temp_bytes(), 0u);
}

TEST_F(JoinConIndiceTest, ElHashExtensibleTambienSirveComoSonda) {
  montar(kind::kExtendibleHash, 800, 200);
  std::vector<Record> izq;
  for (std::int32_t i = 0; i < 30; ++i) izq.push_back(alumno(i));

  auto fi = source_of(izq);
  auto fd = source_of(der_);
  ExternalJoin j(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kIndexNested, 8,
                 kDefaultPageSize, dir_);
  auto salida = j.joined(*fi, *sonda_, *fd, izq.size());

  esperar_mismo_join(drenar(*salida), join_en_memoria(izq, 0, der_, 1));
  EXPECT_EQ(j.structure(), kind::kExtendibleHash);
}

TEST_F(JoinConIndiceTest, LasDosEstrategiasDanExactamenteLoMismo) {
  // La prueba que de verdad importa de tener dos caminos: que no se puedan
  // contradecir. Si `used()` cambia el resultado, `used()` no es una nota al
  // pie del plan, es un bug.
  montar(kind::kBPlusUnclustered, 600, 150);
  std::vector<Record> izq;
  for (std::int32_t i = 0; i < 150; ++i) izq.push_back(alumno(i));

  std::vector<Record> por_indice;
  {
    auto fi = source_of(izq);
    auto fd = source_of(der_);
    ExternalJoin j(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kIndexNested, 8,
                   kDefaultPageSize, dir_);
    auto s = j.joined(*fi, *sonda_, *fd, izq.size());
    por_indice = drenar(*s);
  }

  std::vector<Record> por_hash;
  {
    auto fi = source_of(izq);
    auto fd = source_of(der_);
    ExternalJoin j(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kHash, 4, kDefaultPageSize,
                   dir_);
    auto s = j.joined(*fi, *fd);
    por_hash = drenar(*s);
  }

  esperar_mismo_join(por_indice, por_hash);
  esperar_mismo_join(std::move(por_hash), join_en_memoria(izq, 0, der_, 1));
}

TEST_F(JoinConIndiceTest, SondearUnHeapPorClavePrimariaEstaProhibido) {
  // `search` sobre un heap recorre la tabla entera: sondearlo por fila externa
  // seria un producto cartesiano disfrazado de "estrategia por indice".
  db_ = std::make_unique<Database>(dir_ / "catalogo.txt");
  db_->create_table(notas(), kind::kHeap);
  EXPECT_THROW(static_cast<void>(probe_of(db_->table("notas"))), SchemaError);
}

TEST_F(JoinConIndiceTest, SondarPorClavePrimariaFuncionaSobreElBPlusAgrupado) {
  // El caso que el criterio del issue dejaba fuera: la clave de join ES la
  // clave primaria del lado interno, asi que no hay ningun `Index` y quien
  // resuelve es la tabla. Es el join mas comun que existe.
  db_ = std::make_unique<Database>(dir_ / "catalogo.txt");
  db_->create_table(alumnos(), kind::kBPlusClustered);
  TableFile& t = db_->table("alumnos");
  std::vector<Record> internos;
  for (std::int32_t i = 0; i < 300; ++i) {
    internos.push_back(alumno(i));
    t.insert(internos.back());
  }
  auto sonda = probe_of(t);
  EXPECT_EQ(sonda->structure(), kind::kBPlusClustered);

  std::vector<Record> externos;
  for (std::int32_t i = 0; i < 20; ++i) externos.push_back(nota(i, 300));

  auto fi = source_of(externos);
  auto fd = source_of(internos);
  ExternalJoin j(notas(), 1, alumnos(), 0, ExternalJoin::Strategy::kIndexNested, 8,
                 kDefaultPageSize, dir_);
  auto salida = j.joined(*fi, *sonda, *fd, externos.size());

  esperar_mismo_join(drenar(*salida), join_en_memoria(externos, 1, internos, 0));
}

// ---------------------------------------------------------------------------
// La decision
// ---------------------------------------------------------------------------

TEST(EleccionDeEstrategia, ConDosTablasGrandesEligeHashAunqueHayaIndice) {
  // El criterio del issue decia "si hay indice, index nested loop". Medido
  // (docs/medir-condicion-join.cpp) con 10 000 por lado el INL cuesta 40 176
  // paginas y el hash join 474. Esta prueba fija esa conclusion para que nadie
  // "arregle" la regla de vuelta a la del issue sin volver a medir.
  const std::size_t filas = 10000;
  const std::uint64_t paginas = (filas + 80) / 81;
  EXPECT_FALSE(ExternalJoin::conviene_index_nested(filas, paginas, paginas, /*costo=*/4));
  EXPECT_FALSE(ExternalJoin::conviene_index_nested(filas, paginas, paginas, /*costo=*/2));
}

TEST(EleccionDeEstrategia, ConUnLadoExternoDiminutoEligeIndice) {
  // El otro lado de la moneda: si el INL no ganara NUNCA, tenerlo seria
  // codigo muerto y el criterio del issue no tendria nada de razon.
  const std::uint64_t paginas_internas = (10000 + 80) / 81;
  EXPECT_TRUE(ExternalJoin::conviene_index_nested(10, 1, paginas_internas, 4));
  EXPECT_TRUE(ExternalJoin::conviene_index_nested(50, 1, paginas_internas, 4));
}

TEST(EleccionDeEstrategia, ElCruceEstaDondeLaMedicionLoPuso) {
  // La medicion sobre el motor real puso el cruce del B+ no agrupado entre
  // |R|=50 (INL: 201 paginas contra 240) y |R|=100 (401 contra 240).
  const std::uint64_t internas = (10000 + 80) / 81;
  EXPECT_TRUE(ExternalJoin::conviene_index_nested(50, (50 + 80) / 81, internas, 4));
  EXPECT_FALSE(ExternalJoin::conviene_index_nested(100, (100 + 80) / 81, internas, 4));
}

TEST(EleccionDeEstrategia, SinFilasExternasNoSeEligeIndice) {
  EXPECT_FALSE(ExternalJoin::conviene_index_nested(0, 0, 100, 4));
}

TEST_F(JoinConIndiceTest, AutoEligeHashConDosLadosGrandes) {
  montar(kind::kBPlusUnclustered, 2000, 2000);
  std::vector<Record> izq;
  for (std::int32_t i = 0; i < 2000; ++i) izq.push_back(alumno(i));

  auto fi = source_of(izq);
  auto fd = source_of(der_);
  ExternalJoin j(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kAuto, 8, kDefaultPageSize,
                 dir_);
  auto salida = j.joined(*fi, *sonda_, *fd, izq.size());
  esperar_mismo_join(drenar(*salida), join_en_memoria(izq, 0, der_, 1));
  EXPECT_EQ(j.used(), ExternalJoin::Strategy::kHash);
  EXPECT_EQ(j.structure(), "external_hash");
}

TEST_F(JoinConIndiceTest, AutoEligeIndiceConUnLadoExternoDiminuto) {
  montar(kind::kBPlusUnclustered, 2000, 2000);
  std::vector<Record> izq;
  for (std::int32_t i = 0; i < 5; ++i) izq.push_back(alumno(i));

  auto fi = source_of(izq);
  auto fd = source_of(der_);
  ExternalJoin j(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kAuto, 8, kDefaultPageSize,
                 dir_);
  auto salida = j.joined(*fi, *sonda_, *fd, izq.size());
  esperar_mismo_join(drenar(*salida), join_en_memoria(izq, 0, der_, 1));
  EXPECT_EQ(j.used(), ExternalJoin::Strategy::kIndexNested);
}

TEST_F(JoinConIndiceTest, AutoSinSaberElTamanoExternoCaeAHash) {
  // 0 significa "no lo se". Elegir INL a ciegas es lo que cuesta ordenes de
  // magnitud, asi que la respuesta segura es hash.
  montar(kind::kBPlusUnclustered, 2000, 2000);
  std::vector<Record> izq;
  for (std::int32_t i = 0; i < 5; ++i) izq.push_back(alumno(i));

  auto fi = source_of(izq);
  auto fd = source_of(der_);
  ExternalJoin j(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kAuto, 8, kDefaultPageSize,
                 dir_);
  auto salida = j.joined(*fi, *sonda_, *fd, /*filas_izquierda=*/0);
  esperar_mismo_join(drenar(*salida), join_en_memoria(izq, 0, der_, 1));
  EXPECT_EQ(j.used(), ExternalJoin::Strategy::kHash);
}

// ---------------------------------------------------------------------------
// Costo
// ---------------------------------------------------------------------------

TEST_F(ExternalJoinTest, LasPaginasMedidasSeParecenALaFormula) {
  // El 2.1.6 contrasta la formula contra la medicion, igual que el #20 hace
  // con 2N(1+ceil(log)). Aqui la formula es 3(N_R+N_S) y solo vale si ninguna
  // particion desbordo, asi que se comprueba las dos cosas.
  std::vector<Record> izq;
  for (std::int32_t i = 0; i < 4000; ++i) izq.push_back(alumno(i));
  std::vector<Record> der;
  for (std::int32_t i = 0; i < 4000; ++i) der.push_back(nota(i, 4000));

  auto fi = source_of(izq);
  auto fd = source_of(der);
  ExternalJoin j(alumnos(), 0, notas(), 1, ExternalJoin::Strategy::kHash, 32, kDefaultPageSize,
                 dir_);
  auto salida = j.joined(*fi, *fd);
  drenar(*salida);

  ASSERT_EQ(j.blocked_partitions(), 0u) << "la formula solo vale sin desborde";
  const std::uint64_t n_izq =
      (4000 + j.left_records_per_page() - 1) / j.left_records_per_page();
  const std::uint64_t n_der =
      (4000 + j.right_records_per_page() - 1) / j.right_records_per_page();
  const std::uint64_t predicho = ExternalJoin::predicted_pages(n_izq, n_der);
  const std::uint64_t medido = j.stats().pages_read + j.stats().pages_written;

  // El particionado reparte en p archivos, y la ultima pagina de cada uno
  // queda a medias: se escriben hasta p paginas de mas por lado. La cota
  // recoge eso sin dejar de ser una cota.
  EXPECT_GE(medido, predicho);
  EXPECT_LE(medido, predicho + 6 * j.partitions());
}

}  // namespace
}  // namespace quipudb
