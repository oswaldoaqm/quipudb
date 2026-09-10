// Pruebas del indice B+ no agrupado (issue #16).

#include <gtest/gtest.h>

#include <algorithm>
#include <filesystem>
#include <map>
#include <numeric>
#include <random>
#include <set>
#include <string>
#include <vector>

#include "quipudb/error.hpp"
#include "quipudb/index/bplus_unclustered_index.hpp"
#include "quipudb/storage/heap_file.hpp"
#include "quipudb/storage/sequential_file.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

/// La columna indexada NO es la clave primaria: `codigo` es la clave y se
/// indexa `carrera`, que se repite mucho, o `edad`, que se repite bastante.
Schema alumnos() {
  return Schema{
      .table_name = "alumnos",
      .columns = {{"codigo", DataType::Int},
                  {"carrera", DataType::Varchar, 12},
                  {"edad", DataType::Int}},
      .key_column = 0,
  };
}

const std::vector<std::string> kCarreras{"ciencia", "software", "mecatro", "quimica"};

Record alumno(std::int32_t codigo) {
  return {codigo, kCarreras[static_cast<std::size_t>(codigo) % kCarreras.size()],
          18 + (codigo % 10)};
}

std::int32_t codigo_de(const Record& r) { return std::get<std::int32_t>(r[0]); }
std::int32_t edad_de(const Record& r) { return std::get<std::int32_t>(r[2]); }

class UnclusteredTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_bpu_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
  }
  void TearDown() override { fs::remove_all(dir_); }

  /// Carga la tabla y devuelve el indice sobre `edad`, ya construido.
  std::vector<std::int32_t> cargar(HeapFile& datos, int n, unsigned semilla) {
    std::vector<std::int32_t> cs(static_cast<std::size_t>(n));
    std::iota(cs.begin(), cs.end(), 1);
    std::shuffle(cs.begin(), cs.end(), std::mt19937{semilla});
    for (const auto c : cs) datos.insert(alumno(c));
    return cs;
  }

  fs::path dir_;
};

TEST_F(UnclusteredTest, IndexaUnaColumnaQueNoEsLaClavePrimaria) {
  // El criterio central del issue.
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  cargar(datos, 500, 3);
  BPlusUnclusteredIndex ix(dir_ / "por_edad.bplus", Column{"edad", DataType::Int}, datos, 512, 4);
  ix.build();

  EXPECT_EQ(ix.kind(), kind::kBPlusUnclustered);
  EXPECT_EQ(ix.key_type(), DataType::Int);
  EXPECT_TRUE(ix.supports_range());
  EXPECT_EQ(ix.size(), 500u) << "una entrada por registro, no una por valor distinto";
  EXPECT_EQ(ix.check_invariants(), "");

  // La edad se repite: 500 registros reparten 10 edades.
  const auto rids = ix.search(Value{20});
  EXPECT_EQ(rids.size(), 50u) << "50 alumnos tienen 20 anios";

  // Y resolviendo los punteros salen los registros de verdad.
  const auto encontrados = ix.lookup(Value{20});
  ASSERT_EQ(encontrados.size(), 50u);
  for (const auto& r : encontrados) EXPECT_EQ(edad_de(r), 20);
}

TEST_F(UnclusteredTest, CoincideConElEscaneoDeLaTabla) {
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  cargar(datos, 800, 9);
  BPlusUnclusteredIndex ix(dir_ / "por_edad.bplus", Column{"edad", DataType::Int}, datos, 512, 4);
  ix.build();

  // Referencia: filtrar la tabla entera a mano.
  const auto todos = datos.scan();
  const auto por_edad = [&](std::int32_t desde, std::int32_t hasta) {
    std::multiset<std::int32_t> out;
    for (const auto& r : todos) {
      if (edad_de(r) >= desde && edad_de(r) <= hasta) out.insert(codigo_de(r));
    }
    return out;
  };
  const auto codigos_de = [](const std::vector<Record>& rs) {
    std::multiset<std::int32_t> out;
    for (const auto& r : rs) out.insert(codigo_de(r));
    return out;
  };

  for (std::int32_t e = 17; e <= 29; ++e) {
    EXPECT_EQ(codigos_de(ix.lookup(Value{e})), por_edad(e, e)) << "edad " << e;
  }
  const std::vector<std::pair<std::int32_t, std::int32_t>> rangos{
      {18, 27}, {20, 22}, {18, 18}, {27, 27}, {0, 100}, {30, 40}, {25, 20},
  };
  for (const auto& [lo, hi] : rangos) {
    EXPECT_EQ(codigos_de(ix.lookup_range(Value{lo}, Value{hi})), por_edad(lo, hi))
        << "rango [" << lo << ", " << hi << "]";
  }
}

TEST_F(UnclusteredTest, ElScanDelIndiceSaleOrdenadoPorLaColumnaIndexada) {
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  cargar(datos, 300, 4);
  BPlusUnclusteredIndex ix(dir_ / "por_edad.bplus", Column{"edad", DataType::Int}, datos, 512, 4);
  ix.build();

  const auto pares = ix.scan();
  ASSERT_EQ(pares.size(), 300u);
  EXPECT_TRUE(std::is_sorted(pares.begin(), pares.end(), [](const auto& a, const auto& b) {
    return compare(a.first, b.first) < 0;
  }));
  // Y cada puntero resuelve a un registro cuya columna vale esa clave.
  for (const auto& [k, rid] : pares) {
    const auto r = datos.read(rid);
    ASSERT_TRUE(r.has_value());
    EXPECT_EQ(edad_de(*r), std::get<std::int32_t>(k));
  }
}

TEST_F(UnclusteredTest, BorrarUnPunteroNoToccaLosQueComparteLaClave) {
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  std::vector<RID> rids;
  for (std::int32_t c = 1; c <= 40; ++c) rids.push_back(datos.insert(alumno(c)));
  BPlusUnclusteredIndex ix(dir_ / "por_edad.bplus", Column{"edad", DataType::Int}, datos, 512, 4);
  ix.build();

  const auto antes = ix.search(Value{20}).size();
  ASSERT_GT(antes, 1u) << "tiene que haber varios con la misma edad";

  // Quitar un puntero concreto deja los demas de esa clave.
  const RID victima = ix.search(Value{20}).front();
  EXPECT_TRUE(ix.remove(Value{20}, victima));
  EXPECT_EQ(ix.search(Value{20}).size(), antes - 1);
  EXPECT_FALSE(ix.remove(Value{20}, victima)) << "quitarlo dos veces no cuenta dos veces";

  // Quitar la clave entera se lleva el resto.
  EXPECT_EQ(ix.remove(Value{20}), antes - 1);
  EXPECT_TRUE(ix.search(Value{20}).empty());
  EXPECT_EQ(ix.check_invariants(), "");
  // Los de otras edades siguen ahi.
  EXPECT_FALSE(ix.search(Value{21}).empty());
}

TEST_F(UnclusteredTest, SoloSePuedeMontarSobreUnHeapFile) {
  // Los RID del secuencial y del B+ agrupado se mueven de sitio, asi que un
  // indice que los guarde apuntaria al vecino sin lanzar nada.
  SequentialFile secuencial(dir_ / "alumnos.seq", alumnos(), 512);
  secuencial.insert(alumno(1));
  EXPECT_THROW(BPlusUnclusteredIndex(dir_ / "ix.bplus", Column{"edad", DataType::Int}, secuencial,
                                     512, 4),
               SchemaError);

  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  EXPECT_THROW(BPlusUnclusteredIndex(dir_ / "ix2.bplus", Column{"noexiste", DataType::Int}, datos,
                                     512, 4),
               SchemaError);
  EXPECT_THROW(BPlusUnclusteredIndex(dir_ / "ix3.bplus", Column{"edad", DataType::Double}, datos,
                                     512, 4),
               SchemaError)
      << "el tipo tiene que coincidir con el de la tabla";
}

TEST_F(UnclusteredTest, IndexaTambienUnaColumnaDeTexto) {
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  cargar(datos, 400, 6);
  BPlusUnclusteredIndex ix(dir_ / "por_carrera.bplus", Column{"carrera", DataType::Varchar, 12},
                           datos, 512, 4);
  ix.build();
  EXPECT_EQ(ix.size(), 400u);

  // Cuatro carreras repartidas entre 400 alumnos.
  std::size_t total = 0;
  for (const auto& c : kCarreras) {
    const auto rs = ix.lookup(Value{c});
    EXPECT_GT(rs.size(), 50u) << c;
    for (const auto& r : rs) EXPECT_EQ(std::get<std::string>(r[1]), c);
    total += rs.size();
  }
  EXPECT_EQ(total, 400u);
  EXPECT_TRUE(ix.lookup(Value{std::string("noexiste")}).empty());
  EXPECT_EQ(ix.check_invariants(), "");
}

TEST_F(UnclusteredTest, LaBusquedaLeeLaAlturaMasUnaPaginaPorRegistro) {
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 4096);
  cargar(datos, 5000, 8);
  BPlusUnclusteredIndex ix(dir_ / "por_edad.bplus", Column{"edad", DataType::Int}, datos, 4096);
  ix.build();

  ix.reset_stats();
  const auto rids = ix.search(Value{22});
  EXPECT_GT(rids.size(), 100u);
  // Bajar cuesta la altura; despues es seguir la cadena de hojas.
  EXPECT_LE(ix.stats().pages_read, ix.height() + rids.size() / 10 + 5);

  // Resolver los punteros si cuesta una lectura por registro: es la desventaja
  // del indice no agrupado frente al agrupado, y es lo que compara el 2.1.6.
  datos.reset_stats();
  const auto rs = ix.lookup(Value{22});
  EXPECT_EQ(rs.size(), rids.size());
  EXPECT_EQ(datos.stats().pages_read, rids.size());
}

TEST_F(UnclusteredTest, SobreviveAlCerrarYReabrir) {
  {
    HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
    cargar(datos, 600, 12);
    BPlusUnclusteredIndex ix(dir_ / "por_edad.bplus", Column{"edad", DataType::Int}, datos, 512,
                             4);
    ix.build();
    ix.remove(Value{18});
    ix.flush();
    datos.flush();
  }
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  BPlusUnclusteredIndex ix(dir_ / "por_edad.bplus", Column{"edad", DataType::Int}, datos, 512, 4);
  EXPECT_EQ(ix.check_invariants(), "");
  EXPECT_TRUE(ix.search(Value{18}).empty());
  const auto rs = ix.lookup(Value{19});
  EXPECT_FALSE(rs.empty());
  for (const auto& r : rs) EXPECT_EQ(edad_de(r), 19);
}

TEST_F(UnclusteredTest, UnPunteroColgadoSeDenunciaEnVezDeDevolverDeMenos) {
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  for (std::int32_t c = 1; c <= 20; ++c) datos.insert(alumno(c));
  BPlusUnclusteredIndex ix(dir_ / "por_edad.bplus", Column{"edad", DataType::Int}, datos, 512, 4);
  ix.build();

  // Se borra un registro de la tabla sin avisarle al indice: el puntero queda
  // colgado. Mantener las dos cosas en sincronia es del planner (2.1.3).
  const std::int32_t edad = edad_de(alumno(5));
  ASSERT_EQ(datos.remove(Value{5}), 1u);
  EXPECT_THROW(static_cast<void>(ix.lookup(Value{edad})), IoError);
  // El indice por si solo sigue respondiendo: es la tabla la que cambio.
  EXPECT_FALSE(ix.search(Value{edad}).empty());
}

}  // namespace
}  // namespace quipudb
