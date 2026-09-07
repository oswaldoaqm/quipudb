// Pruebas del hash extensible: directorio, buckets y split (issue #18).

#include <gtest/gtest.h>

#include <algorithm>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <numeric>
#include <random>
#include <set>
#include <string>
#include <unordered_map>
#include <vector>

#include "quipudb/error.hpp"
#include "quipudb/index/extendible_hash.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

const Column kCodigo{"codigo", DataType::Int};
constexpr std::size_t kPayload = sizeof(std::uint32_t);

std::vector<std::byte> carga(std::uint32_t v) {
  std::vector<std::byte> b(kPayload);
  std::memcpy(b.data(), &v, sizeof v);
  return b;
}

std::uint32_t carga_de(std::span<const std::byte> b) {
  std::uint32_t v = 0;
  std::memcpy(&v, b.data(), sizeof v);
  return v;
}

/// Par (clave, payload) en una forma ordenable, para comparar el contenido del
/// indice contra el oraculo sin depender del orden de bucket.
using Par = std::pair<std::int32_t, std::uint32_t>;

std::multiset<Par> contenido(ExtendibleHash& h) {
  std::multiset<Par> out;
  for (const auto& [k, p] : h.scan()) out.emplace(std::get<std::int32_t>(k), carga_de(p));
  return out;
}

class HashTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_eh_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
  }
  void TearDown() override { fs::remove_all(dir_); }

  [[nodiscard]] fs::path archivo() const { return dir_ / "codigo.hash"; }

  fs::path dir_;
};

// ---------------------------------------------------------------------------
// Directorio y buckets
// ---------------------------------------------------------------------------

TEST_F(HashTest, ArrancaConUnBucketYUnaEntradaDeDirectorio) {
  ExtendibleHash h(archivo(), kCodigo, kPayload, 256, 4);
  EXPECT_EQ(h.global_depth(), 0u);
  EXPECT_EQ(h.directory_size(), 1u);
  EXPECT_EQ(h.bucket_count(), 1u);
  EXPECT_EQ(h.local_depth(0), 0u);
  EXPECT_EQ(h.size(), 0u);
  EXPECT_EQ(h.overflow_pages(), 0u);
  EXPECT_EQ(h.check_invariants(), "");
}

TEST_F(HashTest, LlenarElUnicoBucketNoLoParte) {
  ExtendibleHash h(archivo(), kCodigo, kPayload, 256, 4);
  for (std::int32_t c = 1; c <= 4; ++c) h.insert(Value{c}, carga(static_cast<std::uint32_t>(c)));
  EXPECT_EQ(h.size(), 4u);
  EXPECT_EQ(h.global_depth(), 0u);
  EXPECT_EQ(h.bucket_count(), 1u);
  EXPECT_EQ(h.check_invariants(), "");
}

TEST_F(HashTest, PasarseDeLaCapacidadDuplicaElDirectorioYParteElBucket) {
  ExtendibleHash h(archivo(), kCodigo, kPayload, 256, 4);
  for (std::int32_t c = 1; c <= 5; ++c) h.insert(Value{c}, carga(static_cast<std::uint32_t>(c)));

  // El bucket estaba en profundidad local 0, igual que la global: para poder
  // distinguir a sus dos mitades hace falta un bit mas de directorio.
  EXPECT_EQ(h.global_depth(), 1u);
  EXPECT_EQ(h.directory_size(), 2u);
  EXPECT_EQ(h.bucket_count(), 2u);
  EXPECT_EQ(h.local_depth(0), 1u);
  EXPECT_EQ(h.local_depth(1), 1u);
  EXPECT_NE(h.bucket_at(0), h.bucket_at(1));
  EXPECT_EQ(h.size(), 5u);
  EXPECT_EQ(h.check_invariants(), "");
}

TEST_F(HashTest, UnBucketConProfundidadLocalMenorLoComparteMediaTablaDeDirectorio) {
  // Con profundidad global 2 y un bucket de local 1, ese bucket tiene que
  // aparecer en dos entradas del directorio, y en las dos que comparten su bit
  // bajo. Es la invariante que hace que buscar por el directorio funcione.
  ExtendibleHash h(archivo(), kCodigo, kPayload, 256, 2);
  for (std::int32_t c = 1; c <= 40; ++c) h.insert(Value{c}, carga(static_cast<std::uint32_t>(c)));
  ASSERT_GE(h.global_depth(), 2u);
  ASSERT_EQ(h.check_invariants(), "");

  const std::size_t g = h.global_depth();
  for (std::size_t j = 0; j < h.directory_size(); ++j) {
    const std::size_t local = h.local_depth(j);
    ASSERT_LE(local, g);
    const std::size_t mascara = (std::size_t{1} << local) - 1;
    std::size_t apuntan = 0;
    for (std::size_t k = 0; k < h.directory_size(); ++k) {
      if (h.bucket_at(k) != h.bucket_at(j)) continue;
      ++apuntan;
      EXPECT_EQ(k & mascara, j & mascara) << "entrada " << k << " comparte bucket con " << j
                                          << " sin compartir sus bits bajos";
    }
    EXPECT_EQ(apuntan, std::size_t{1} << (g - local));
  }
}

// ---------------------------------------------------------------------------
// El criterio del issue
// ---------------------------------------------------------------------------

TEST_F(HashTest, DiezMilClavesYNingunaSePierdeTrasLosSplits) {
  ExtendibleHash h(archivo(), kCodigo, kPayload, 512, 8);

  std::vector<std::int32_t> cs(10000);
  std::iota(cs.begin(), cs.end(), 1);
  std::shuffle(cs.begin(), cs.end(), std::mt19937{7});
  for (const auto c : cs) h.insert(Value{c}, carga(static_cast<std::uint32_t>(c)));

  EXPECT_EQ(h.size(), 10000u);
  EXPECT_EQ(h.check_invariants(), "");

  std::multiset<Par> esperado;
  for (const auto c : cs) esperado.emplace(c, static_cast<std::uint32_t>(c));
  EXPECT_EQ(contenido(h), esperado);

  // Y de paso: el directorio creció, que es lo que se está probando.
  EXPECT_GT(h.global_depth(), 0u);
  EXPECT_GT(h.bucket_count(), 1u);
}

TEST_F(HashTest, CoincideConUnUnorderedMultimapComoOraculo) {
  ExtendibleHash h(archivo(), kCodigo, kPayload, 256, 3);
  std::unordered_multimap<std::int32_t, std::uint32_t> oraculo;

  std::mt19937 rng{31};
  std::uniform_int_distribution<std::int32_t> claves(1, 300);  // se repiten a proposito
  for (std::uint32_t i = 0; i < 3000; ++i) {
    const std::int32_t k = claves(rng);
    h.insert(Value{k}, carga(i));
    oraculo.emplace(k, i);
  }

  EXPECT_EQ(h.check_invariants(), "");
  EXPECT_EQ(h.size(), oraculo.size());

  std::multiset<Par> esperado;
  for (const auto& [k, v] : oraculo) esperado.emplace(k, v);
  EXPECT_EQ(contenido(h), esperado);
}

// ---------------------------------------------------------------------------
// Claves repetidas: overflow en vez de directorio infinito
// ---------------------------------------------------------------------------

TEST_F(HashTest, LasClavesRepetidasVanAOverflowYNoDuplicanElDirectorio) {
  // Todas las entradas tienen la MISMA clave, asi que tienen el mismo hash y
  // ningun bit las separa. Si el split no comprobara eso antes de partir, el
  // directorio se duplicaria una vez por insercion hasta reventar.
  ExtendibleHash h(archivo(), kCodigo, kPayload, 256, 4);
  for (std::uint32_t i = 0; i < 50; ++i) h.insert(Value{std::int32_t{7}}, carga(i));

  EXPECT_EQ(h.global_depth(), 0u);
  EXPECT_EQ(h.directory_size(), 1u);
  EXPECT_EQ(h.bucket_count(), 1u);
  EXPECT_GT(h.overflow_pages(), 0u);
  EXPECT_EQ(h.size(), 50u);
  EXPECT_EQ(h.check_invariants(), "");

  std::multiset<Par> esperado;
  for (std::uint32_t i = 0; i < 50; ++i) esperado.emplace(7, i);
  EXPECT_EQ(contenido(h), esperado);
}

// ---------------------------------------------------------------------------
// El hash
// ---------------------------------------------------------------------------

TEST_F(HashTest, ElHashRepartelasClavesConUnPasoQueEsPotenciaDeDos) {
  // El directorio se indexa con los bits BAJOS, asi que una clave usada tal
  // cual como hash concentra en un solo bucket todo lo que tenga un paso que
  // sea potencia de dos: 512, 1024, 2048... mandan las 2 000 claves al bucket
  // 0 y el indice degenera en una lista encadenada de overflow.
  //
  // Es el patron que justifica que haya un hash y no una identidad, y no es
  // rebuscado: identificadores alineados, tamanos en bytes, milisegundos
  // redondeados. Con claves correlativas (1..n) casi cualquier funcion
  // reparte, por eso la prueba no usa esas.
  ExtendibleHash h(archivo(), kCodigo, kPayload, 256, 4);
  for (std::int32_t i = 1; i <= 2000; ++i) {
    h.insert(Value{i * 512}, carga(static_cast<std::uint32_t>(i)));
  }

  ASSERT_EQ(h.check_invariants(), "");
  EXPECT_EQ(h.size(), 2000u);
  EXPECT_EQ(h.overflow_pages(), 0u) << "hubo colisiones que ningun split pudo separar";

  // Con 2 000 claves y buckets de 4 hacen falta al menos 500 buckets. Un
  // reparto razonable no deberia pasar del doble.
  EXPECT_GE(h.bucket_count(), 500u);
  EXPECT_LE(h.bucket_count(), 1000u);

  // Y sobre todo: el DIRECTORIO no puede dispararse. Un hash que amontona en
  // los bits bajos igual termina repartiendo, porque el split sigue bajando
  // hasta el bit donde las claves difieren, pero para llegar ahi duplica el
  // directorio una vez por bit: con paso 512 son nueve duplicaciones de mas,
  // y el directorio pasa de miles de entradas a cientos de miles para el
  // mismo numero de buckets. Contar buckets no lo ve; esta cota si.
  EXPECT_LE(h.directory_size(), 4 * h.bucket_count())
      << "el directorio tiene " << h.directory_size() << " entradas para solo "
      << h.bucket_count() << " buckets";
}

TEST_F(HashTest, ClavesAlAzarNoGeneranOverflow) {
  // Con claves distintas siempre hay un bit que las separa, asi que el split
  // tiene que alcanzar y las cadenas de overflow deben quedar en cero.
  //
  // Van al azar a proposito: los patrones regulares hacen que el bit que toca
  // partir separe casi siempre por construccion, y esta prueba existe para
  // cubrir el caso contrario -- cuando ese bit NO separa y hay que seguir
  // bajando en vez de rendirse y encadenar overflow.
  ExtendibleHash h(archivo(), kCodigo, kPayload, 256, 4);
  std::mt19937 rng{2026};
  std::uniform_int_distribution<std::int32_t> dist(1, 1000000000);
  std::set<std::int32_t> claves;
  while (claves.size() < 2000) claves.insert(dist(rng));
  std::uint32_t i = 0;
  for (const auto c : claves) h.insert(Value{c}, carga(i++));

  ASSERT_EQ(h.check_invariants(), "");
  EXPECT_EQ(h.size(), 2000u);
  EXPECT_EQ(h.overflow_pages(), 0u)
      << "claves distintas terminaron en overflow: algun split se rindio antes de tiempo";
}

TEST_F(HashTest, CeroYMenosCeroSonLaMismaClaveYVanAlMismoBucket) {
  // `compare(0.0, -0.0)` es 0, asi que son la misma clave; sus bytes difieren
  // en el bit de signo. Si el hash mirara los bytes crudos, terminarian en
  // buckets distintos y una busqueda por 0.0 no encontraria al -0.0.
  const Column precio{"precio", DataType::Double};
  EXPECT_EQ(ExtendibleHash::hash_of(precio, Value{0.0}),
            ExtendibleHash::hash_of(precio, Value{-0.0}));

  ExtendibleHash h(dir_ / "precio.hash", precio, kPayload, 256, 4);
  h.insert(Value{0.0}, carga(1));
  h.insert(Value{-0.0}, carga(2));
  EXPECT_EQ(h.check_invariants(), "");
  EXPECT_EQ(h.bucket_at(0), h.bucket_at(0));
  EXPECT_EQ(h.size(), 2u);
}

TEST_F(HashTest, IndexaTambienUnaColumnaDeTexto) {
  const Column carrera{"carrera", DataType::Varchar, 12};
  ExtendibleHash h(dir_ / "carrera.hash", carrera, kPayload, 512, 4);
  const std::vector<std::string> nombres{"ciencia", "software", "mecatro", "quimica", "civil"};
  for (std::uint32_t i = 0; i < 100; ++i) {
    h.insert(Value{nombres[i % nombres.size()]}, carga(i));
  }
  EXPECT_EQ(h.size(), 100u);
  EXPECT_EQ(h.check_invariants(), "");

  std::multiset<std::pair<std::string, std::uint32_t>> visto;
  for (const auto& [k, p] : h.scan()) visto.emplace(std::get<std::string>(k), carga_de(p));
  EXPECT_EQ(visto.size(), 100u);
  for (const auto& [nombre, _] : visto) {
    EXPECT_NE(std::find(nombres.begin(), nombres.end(), nombre), nombres.end());
  }
}

// ---------------------------------------------------------------------------
// Recorrido
// ---------------------------------------------------------------------------

TEST_F(HashTest, ElRecorridoDaCadaEntradaUnaSolaVezAunqueElBucketSeComparta) {
  // Un bucket de profundidad local menor que la global aparece en varias
  // entradas del directorio. Recorrerlo por cada una duplicaria sus entradas.
  //
  // Para que ese caso se de hacen falta claves DESPAREJAS: con un rango
  // correlativo el hash reparte tan parejo que todos los buckets terminan con
  // la misma profundidad y ninguno se comparte. Aqui van dos grupos alejados,
  // que es lo que pasa con identificadores de dos altas distintas.
  ExtendibleHash h(archivo(), kCodigo, kPayload, 256, 4);
  std::uint32_t id = 0;
  for (std::int32_t c = 1; c <= 300; ++c) h.insert(Value{c}, carga(id++));
  for (std::int32_t c = 1; c <= 200; ++c) h.insert(Value{1000000 + c * 7}, carga(id++));
  ASSERT_EQ(h.check_invariants(), "");

  std::size_t compartidos = 0;
  for (std::size_t j = 0; j < h.directory_size(); ++j) {
    if (h.local_depth(j) < h.global_depth()) ++compartidos;
  }
  ASSERT_GT(compartidos, 0u) << "el caso que esta prueba cubre no llego a darse";

  const auto todo = h.scan();
  EXPECT_EQ(todo.size(), 500u);
  std::set<std::int32_t> distintas;
  std::set<std::uint32_t> cargas;
  for (const auto& [k, p] : todo) {
    distintas.insert(std::get<std::int32_t>(k));
    cargas.insert(carga_de(p));
  }
  EXPECT_EQ(distintas.size(), 500u);
  EXPECT_EQ(cargas.size(), 500u) << "alguna entrada salio repetida";
}

TEST_F(HashTest, LaPosicionDelCursorEsDistintaParaCadaEntrada) {
  ExtendibleHash h(archivo(), kCodigo, kPayload, 256, 4);
  for (std::int32_t c = 1; c <= 300; ++c) h.insert(Value{c}, carga(static_cast<std::uint32_t>(c)));

  auto cur = h.entries();
  Key k;
  std::vector<std::byte> p;
  std::set<std::pair<PageId, SlotId>> posiciones;
  while (cur->next(k, p)) {
    const RID rid = cur->position();
    EXPECT_TRUE(rid.valid());
    EXPECT_TRUE(posiciones.emplace(rid.page, rid.slot).second)
        << "la posicion " << rid.page << ":" << rid.slot << " salio dos veces";
  }
  EXPECT_EQ(posiciones.size(), 300u);
}

// ---------------------------------------------------------------------------
// Persistencia
// ---------------------------------------------------------------------------

TEST_F(HashTest, SobreviveAlCerrarYReabrir) {
  std::multiset<Par> esperado;
  {
    ExtendibleHash h(archivo(), kCodigo, kPayload, 256, 4);
    for (std::int32_t c = 1; c <= 400; ++c) {
      h.insert(Value{c}, carga(static_cast<std::uint32_t>(c)));
      esperado.emplace(c, static_cast<std::uint32_t>(c));
    }
    h.flush();
  }
  ExtendibleHash h(archivo(), kCodigo, kPayload, 256, 4);
  EXPECT_EQ(h.size(), 400u);
  EXPECT_GT(h.global_depth(), 0u);
  EXPECT_EQ(h.check_invariants(), "");
  EXPECT_EQ(contenido(h), esperado);
}

TEST_F(HashTest, RechazaUnArchivoDeOtraVersionDeFormato) {
  {
    ExtendibleHash h(archivo(), kCodigo, kPayload, 256, 4);
    h.insert(Value{std::int32_t{1}}, carga(1));
    h.flush();
  }
  // El area meta empieza tras los 16 bytes de cabecera de DiskManager, y su
  // primer uint32 es la version del formato.
  {
    std::fstream f(archivo(), std::ios::in | std::ios::out | std::ios::binary);
    ASSERT_TRUE(f.is_open());
    const std::uint32_t otra = ExtendibleHash::kMetaVersion + 1;
    f.seekp(16);
    f.write(reinterpret_cast<const char*>(&otra), sizeof otra);
  }
  EXPECT_THROW(ExtendibleHash(archivo(), kCodigo, kPayload, 256, 4), IoError);
}

TEST_F(HashTest, RechazaReabrirloConOtraFormaDeEntrada) {
  {
    ExtendibleHash h(archivo(), kCodigo, kPayload, 256, 4);
    h.insert(Value{std::int32_t{1}}, carga(1));
    h.flush();
  }
  // Otra capacidad de bucket, otro payload y otro tipo de clave son tres
  // formas de leer mal el mismo archivo.
  EXPECT_THROW(ExtendibleHash(archivo(), kCodigo, kPayload, 256, 5), SchemaError);
  EXPECT_THROW(ExtendibleHash(archivo(), kCodigo, kPayload + 4, 256, 4), SchemaError);
  EXPECT_THROW(ExtendibleHash(archivo(), Column{"codigo", DataType::Double}, kPayload, 256, 4),
               SchemaError);
  // Y el tamano de pagina lo valida el DiskManager.
  EXPECT_THROW(ExtendibleHash(archivo(), kCodigo, kPayload, 512, 4), IoError);
}

// ---------------------------------------------------------------------------
// Validaciones
// ---------------------------------------------------------------------------

TEST_F(HashTest, RechazaUnaCapacidadQueNoDejaPartir) {
  // Con una sola entrada por bucket, partir uno lleno nunca deja sitio.
  EXPECT_THROW(ExtendibleHash(archivo(), kCodigo, kPayload, 256, 1), SchemaError);
  // Y una capacidad que no entra en la pagina tampoco.
  EXPECT_THROW(ExtendibleHash(archivo(), kCodigo, kPayload, 256, 10000), SchemaError);
}

TEST_F(HashTest, RechazaUnaClaveDeOtroTipoOUnPayloadDeOtroTamano) {
  ExtendibleHash h(archivo(), kCodigo, kPayload, 256, 4);
  EXPECT_THROW(h.insert(Value{3.5}, carga(1)), SchemaError);
  EXPECT_THROW(h.insert(Value{std::int32_t{1}}, std::vector<std::byte>(kPayload + 1)),
               SchemaError);
  EXPECT_EQ(h.size(), 0u);
}

TEST_F(HashTest, ElPayloadVuelveIntacto) {
  ExtendibleHash h(archivo(), kCodigo, kPayload, 256, 4);
  for (std::int32_t c = 1; c <= 100; ++c) {
    h.insert(Value{c}, carga(static_cast<std::uint32_t>(c) * 1000u));
  }
  for (const auto& [k, p] : h.scan()) {
    EXPECT_EQ(carga_de(p), static_cast<std::uint32_t>(std::get<std::int32_t>(k)) * 1000u);
  }
}

}  // namespace
}  // namespace quipudb
