// Pruebas del hash extensible: búsqueda, borrado, merge e índice (issue #19).

#include <gtest/gtest.h>

#include <algorithm>
#include <cstring>
#include <filesystem>
#include <map>
#include <numeric>
#include <random>
#include <set>
#include <string>
#include <unordered_map>
#include <vector>

#include "quipudb/error.hpp"
#include "quipudb/index/extendible_hash_index.hpp"
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

class HashIndexTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_ehi_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
  }
  void TearDown() override { fs::remove_all(dir_); }

  std::vector<std::int32_t> cargar(HeapFile& datos, int n, unsigned semilla) {
    std::vector<std::int32_t> cs(static_cast<std::size_t>(n));
    std::iota(cs.begin(), cs.end(), 1);
    std::shuffle(cs.begin(), cs.end(), std::mt19937{semilla});
    for (const auto c : cs) datos.insert(alumno(c));
    return cs;
  }

  fs::path dir_;
};

// ---------------------------------------------------------------------------
// Búsqueda
// ---------------------------------------------------------------------------

TEST_F(HashIndexTest, IndexaUnaColumnaQueNoEsLaClavePrimaria) {
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  cargar(datos, 400, 3);
  ExtendibleHashIndex ix(dir_ / "por_edad.hash", Column{"edad", DataType::Int}, datos, 512, 4);
  ix.build();

  EXPECT_EQ(ix.kind(), kind::kExtendibleHash);
  EXPECT_EQ(ix.size(), 400u);
  EXPECT_EQ(ix.check_invariants(), "");

  const auto rs = ix.lookup(Value{std::int32_t{20}});
  EXPECT_FALSE(rs.empty());
  for (const auto& r : rs) EXPECT_EQ(edad_de(r), 20);
}

TEST_F(HashIndexTest, CoincideConElEscaneoDeLaTabla) {
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  const auto cs = cargar(datos, 600, 5);
  ExtendibleHashIndex ix(dir_ / "por_edad.hash", Column{"edad", DataType::Int}, datos, 512, 4);
  ix.build();
  ASSERT_EQ(ix.check_invariants(), "");

  for (std::int32_t edad = 18; edad <= 27; ++edad) {
    std::multiset<std::int32_t> esperado;
    for (const auto c : cs) {
      if (edad_de(alumno(c)) == edad) esperado.insert(c);
    }
    std::multiset<std::int32_t> visto;
    for (const auto& r : ix.lookup(Value{edad})) visto.insert(codigo_de(r));
    EXPECT_EQ(visto, esperado) << "edad " << edad;
  }
}

TEST_F(HashIndexTest, UnaClaveQueNoEstaDevuelveVacioYNoEsUnError) {
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  cargar(datos, 100, 7);
  ExtendibleHashIndex ix(dir_ / "por_edad.hash", Column{"edad", DataType::Int}, datos, 512, 4);
  ix.build();
  EXPECT_TRUE(ix.search(Value{std::int32_t{999}}).empty());
  EXPECT_TRUE(ix.lookup(Value{std::int32_t{999}}).empty());
}

TEST_F(HashIndexTest, LaBusquedaLeeUnaSolaPaginaCuandoNoHayOverflow) {
  // El criterio del issue: un acceso al directorio (que vive en memoria) más
  // uno al bucket. Con claves únicas no hay cadena de overflow que recorrer.
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  cargar(datos, 500, 11);
  // Se indexa `codigo`, que es único: ninguna cadena de overflow.
  ExtendibleHashIndex ix(dir_ / "por_codigo.hash", Column{"codigo", DataType::Int}, datos, 512, 4);
  ix.build();
  ASSERT_EQ(ix.check_invariants(), "");
  ASSERT_EQ(ix.overflow_pages(), 0u);

  ix.reset_stats();
  const auto rs = ix.search(Value{std::int32_t{250}});
  ASSERT_EQ(rs.size(), 1u);
  EXPECT_EQ(ix.stats().pages_read, 1u)
      << "una búsqueda por igualdad debería costar exactamente una página de bucket";
}

// ---------------------------------------------------------------------------
// Rango: no se soporta, y se dice
// ---------------------------------------------------------------------------

TEST_F(HashIndexTest, NoSoportaRangoYLoDiceAntesDeQueSeLoPidan) {
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  cargar(datos, 50, 13);
  ExtendibleHashIndex ix(dir_ / "por_edad.hash", Column{"edad", DataType::Int}, datos, 512, 4);
  ix.build();

  // El planner consulta esto antes de elegir el índice.
  EXPECT_FALSE(ix.supports_range());
  // Y si lo ignora, recibe una excepción en vez de un resultado caro en
  // silencio: devolverlo igual costaría un scan completo disfrazado de índice.
  EXPECT_THROW(ix.range_search(Value{std::int32_t{20}}, Value{std::int32_t{25}}), Unsupported);
}

// ---------------------------------------------------------------------------
// Borrado
// ---------------------------------------------------------------------------

TEST_F(HashIndexTest, BorrarUnaClaveQuitaTodasSusEntradas) {
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  cargar(datos, 400, 17);
  ExtendibleHashIndex ix(dir_ / "por_edad.hash", Column{"edad", DataType::Int}, datos, 512, 4);
  ix.build();

  const std::size_t antes = ix.size();
  const std::size_t cuantas = ix.search(Value{std::int32_t{22}}).size();
  ASSERT_GT(cuantas, 0u);

  EXPECT_EQ(ix.remove(Value{std::int32_t{22}}), cuantas);
  EXPECT_TRUE(ix.search(Value{std::int32_t{22}}).empty());
  EXPECT_EQ(ix.size(), antes - cuantas);
  EXPECT_EQ(ix.check_invariants(), "");

  // Las demás claves no se tocaron.
  for (std::int32_t edad = 18; edad <= 27; ++edad) {
    if (edad == 22) continue;
    EXPECT_FALSE(ix.search(Value{edad}).empty()) << "edad " << edad;
  }
}

TEST_F(HashIndexTest, BorrarUnPunteroNoTocaLosQueComparteLaClave) {
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  cargar(datos, 300, 19);
  ExtendibleHashIndex ix(dir_ / "por_edad.hash", Column{"edad", DataType::Int}, datos, 512, 4);
  ix.build();

  const auto rids = ix.search(Value{std::int32_t{21}});
  ASSERT_GE(rids.size(), 2u);
  const RID victima = rids.front();

  EXPECT_TRUE(ix.remove(Value{std::int32_t{21}}, victima));
  const auto quedan = ix.search(Value{std::int32_t{21}});
  EXPECT_EQ(quedan.size(), rids.size() - 1);
  EXPECT_EQ(std::find(quedan.begin(), quedan.end(), victima), quedan.end());
  EXPECT_EQ(ix.check_invariants(), "");

  // Borrar el mismo puntero otra vez ya no encuentra nada, y eso no es error.
  EXPECT_FALSE(ix.remove(Value{std::int32_t{21}}, victima));
}

TEST_F(HashIndexTest, BorrarLoQueNoEstaDevuelveCeroYNoEsUnError) {
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  cargar(datos, 100, 23);
  ExtendibleHashIndex ix(dir_ / "por_edad.hash", Column{"edad", DataType::Int}, datos, 512, 4);
  ix.build();
  EXPECT_EQ(ix.remove(Value{std::int32_t{999}}), 0u);
  EXPECT_FALSE(ix.remove(Value{std::int32_t{999}}, RID{7, 0}));
  EXPECT_EQ(ix.check_invariants(), "");
}

// ---------------------------------------------------------------------------
// Merge y reducción del directorio
// ---------------------------------------------------------------------------

TEST_F(HashIndexTest, VaciarElIndiceLoDevuelveAUnSoloBucket) {
  // El criterio central del merge: si se borra todo, la estructura tiene que
  // volver a donde empezó. Sin merge, quedarían cientos de buckets vacíos.
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  const auto cs = cargar(datos, 800, 29);
  ExtendibleHashIndex ix(dir_ / "por_codigo.hash", Column{"codigo", DataType::Int}, datos, 512, 4);
  ix.build();

  ASSERT_GT(ix.global_depth(), 3u);
  ASSERT_GT(ix.bucket_count(), 100u);

  for (const auto c : cs) ASSERT_EQ(ix.remove(Value{c}), 1u) << "codigo " << c;

  EXPECT_EQ(ix.size(), 0u);
  EXPECT_EQ(ix.check_invariants(), "");
  EXPECT_EQ(ix.bucket_count(), 1u) << "los buckets vacíos no se fusionaron";
  EXPECT_EQ(ix.global_depth(), 0u) << "el directorio no se redujo";
  EXPECT_EQ(ix.directory_size(), 1u);
}

TEST_F(HashIndexTest, ElArchivoNoCreceAlBorrarYVolverAInsertar) {
  // Las páginas que libera un merge van a la free list y se reusan. Sin eso,
  // un ciclo de altas y bajas haría crecer el archivo indefinidamente.
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  const auto cs = cargar(datos, 600, 31);
  ExtendibleHashIndex ix(dir_ / "por_codigo.hash", Column{"codigo", DataType::Int}, datos, 512, 4);
  ix.build();
  ASSERT_EQ(ix.check_invariants(), "");
  const auto tam = ix.file_size();

  for (int vuelta = 0; vuelta < 3; ++vuelta) {
    for (const auto c : cs) ix.remove(Value{c});
    ASSERT_EQ(ix.size(), 0u) << "vuelta " << vuelta;
    for (const auto c : cs) ix.insert(Value{c}, RID{static_cast<PageId>(c), 0});
    ASSERT_EQ(ix.size(), cs.size()) << "vuelta " << vuelta;
  }
  EXPECT_EQ(ix.check_invariants(), "");
  EXPECT_LE(ix.file_size(), tam) << "el archivo creció pese a reusar las páginas liberadas";
}

TEST_F(HashIndexTest, LosBucketsConOverflowNoSeFusionan) {
  // Una cadena de overflow tiene más entradas que la capacidad, así que nunca
  // cumple "caben juntas en una página". Un bucket de claves repetidas no se
  // fusiona hasta que se borran esas claves.
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  for (std::int32_t c = 1; c <= 200; ++c) datos.insert(alumno(c));
  // `carrera` toma solo 4 valores distintos: 200 entradas en 4 claves.
  ExtendibleHashIndex ix(dir_ / "por_carrera.hash", Column{"carrera", DataType::Varchar, 12},
                         datos, 512, 4);
  ix.build();

  ASSERT_GT(ix.overflow_pages(), 0u) << "el caso que esta prueba cubre no llegó a darse";
  EXPECT_EQ(ix.size(), 200u);
  EXPECT_EQ(ix.check_invariants(), "");

  // Se borra una carrera entera: su cadena de overflow desaparece con ella.
  const std::size_t quitadas = ix.remove(Value{std::string{"quimica"}});
  EXPECT_GT(quitadas, 0u);
  EXPECT_EQ(ix.check_invariants(), "");
  EXPECT_TRUE(ix.search(Value{std::string{"quimica"}}).empty());
}

TEST_F(HashIndexTest, CoincideConUnMultimapTrasMilAltasYBajas) {
  // El régimen que el 2.1.6 mide: inserciones y eliminaciones frecuentes.
  // Oráculo diferencial contra unordered_multimap.
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  datos.insert(alumno(1));
  ExtendibleHashIndex ix(dir_ / "por_edad.hash", Column{"edad", DataType::Int}, datos, 512, 4);

  std::unordered_multimap<std::int32_t, RID> oraculo;
  auto igual = [](RID a, RID b) { return a.page == b.page && a.slot == b.slot; };

  std::mt19937 rng{37};
  std::uniform_int_distribution<std::int32_t> claves(1, 200);
  std::vector<std::pair<std::int32_t, RID>> vivas;

  for (int paso = 0; paso < 3000; ++paso) {
    const bool insertar = vivas.empty() || (rng() % 2) == 0;
    if (insertar) {
      const std::int32_t k = claves(rng);
      const RID rid{static_cast<PageId>(paso + 1), 0};
      ix.insert(Value{k}, rid);
      oraculo.emplace(k, rid);
      vivas.emplace_back(k, rid);
    } else {
      const std::size_t i = rng() % vivas.size();
      const auto [k, rid] = vivas[i];
      vivas.erase(vivas.begin() + static_cast<std::ptrdiff_t>(i));
      EXPECT_TRUE(ix.remove(Value{k}, rid)) << "paso " << paso;
      auto [ini, fin] = oraculo.equal_range(k);
      for (auto it = ini; it != fin; ++it) {
        if (igual(it->second, rid)) {
          oraculo.erase(it);
          break;
        }
      }
    }
  }

  ASSERT_EQ(ix.check_invariants(), "");
  EXPECT_EQ(ix.size(), oraculo.size());

  std::multiset<std::pair<std::int32_t, PageId>> esperado, visto;
  for (const auto& [k, rid] : oraculo) esperado.emplace(k, rid.page);
  for (const auto& [k, rid] : ix.scan()) visto.emplace(std::get<std::int32_t>(k), rid.page);
  EXPECT_EQ(visto, esperado);
}

// ---------------------------------------------------------------------------
// Contrato
// ---------------------------------------------------------------------------

TEST_F(HashIndexTest, SoloSePuedeMontarSobreUnHeapFile) {
  // Misma razón que el índice B+ no agrupado: en el secuencial los registros
  // se corren de sitio al insertar y el RID guardado dejaría de valer.
  SequentialFile seq(dir_ / "alumnos.seq", alumnos(), 512);
  for (std::int32_t c = 1; c <= 20; ++c) seq.insert(alumno(c));
  EXPECT_THROW(ExtendibleHashIndex(dir_ / "por_edad.hash", Column{"edad", DataType::Int}, seq,
                                   512, 4),
               SchemaError);
}

TEST_F(HashIndexTest, RechazaUnaColumnaQueNoExisteODeOtroTipo) {
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  datos.insert(alumno(1));
  EXPECT_THROW(ExtendibleHashIndex(dir_ / "a.hash", Column{"promedio", DataType::Double}, datos,
                                   512, 4),
               SchemaError);
  EXPECT_THROW(ExtendibleHashIndex(dir_ / "b.hash", Column{"edad", DataType::Double}, datos, 512,
                                   4),
               SchemaError);
}

TEST_F(HashIndexTest, SobreviveAlCerrarYReabrir) {
  std::multiset<std::pair<std::int32_t, PageId>> esperado;
  {
    HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
    cargar(datos, 500, 41);
    ExtendibleHashIndex ix(dir_ / "por_edad.hash", Column{"edad", DataType::Int}, datos, 512, 4);
    ix.build();
    ix.remove(Value{std::int32_t{18}});
    ix.flush();
    datos.flush();
    for (const auto& [k, rid] : ix.scan()) esperado.emplace(std::get<std::int32_t>(k), rid.page);
  }
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  ExtendibleHashIndex ix(dir_ / "por_edad.hash", Column{"edad", DataType::Int}, datos, 512, 4);
  EXPECT_EQ(ix.check_invariants(), "");
  EXPECT_TRUE(ix.search(Value{std::int32_t{18}}).empty());

  std::multiset<std::pair<std::int32_t, PageId>> visto;
  for (const auto& [k, rid] : ix.scan()) visto.emplace(std::get<std::int32_t>(k), rid.page);
  EXPECT_EQ(visto, esperado);

  const auto rs = ix.lookup(Value{std::int32_t{19}});
  EXPECT_FALSE(rs.empty());
  for (const auto& r : rs) EXPECT_EQ(edad_de(r), 19);
}

TEST_F(HashIndexTest, UnPunteroColgadoSeDenunciaEnVezDeDevolverDeMenos) {
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  for (std::int32_t c = 1; c <= 20; ++c) datos.insert(alumno(c));
  ExtendibleHashIndex ix(dir_ / "por_edad.hash", Column{"edad", DataType::Int}, datos, 512, 4);
  ix.build();
  // Se apunta a un registro que no existe: la tabla y el índice quedaron
  // desincronizados y eso hay que decirlo.
  ix.insert(Value{std::int32_t{18}}, RID{9999, 0});
  EXPECT_THROW(ix.lookup(Value{std::int32_t{18}}), IoError);
}

}  // namespace
}  // namespace quipudb
