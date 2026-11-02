// Pruebas de la construccion del IVF (issue #183).
//
// Los datos son vectores agrupados alrededor de centros conocidos y bien
// separados, y los centroides del indice son esos mismos centros. Asi se sabe
// de antemano en que lista tiene que quedar cada vector.

#include "quipudb/index/ivf.hpp"

#include <gtest/gtest.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <limits>
#include <random>
#include <set>
#include <string>
#include <vector>

#include "azar_portable.hpp"
#include "quipudb/error.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

using Vec = std::vector<float>;

class IvfTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_ivf_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
    path_ = dir_ / "indice.ivf";
  }
  void TearDown() override { fs::remove_all(dir_); }

  fs::path dir_;
  fs::path path_;
};

/// `k` centros en dimension `dim`: el centro i vale 10 en la coordenada i y 0
/// en las demas, asi que estan a distancia 10*sqrt(2) entre si.
std::vector<Vec> centros(std::size_t k, std::size_t dim) {
  std::vector<Vec> c(k, Vec(dim, 0.0F));
  for (std::size_t i = 0; i < k; ++i) c[i][i] = 10.0F;
  return c;
}

/// `n` vectores repartidos entre los centros, cada uno a menos de 1 por
/// coordenada de su centro. Devuelve los vectores seguidos y el grupo de cada
/// uno.
struct Datos {
  std::vector<RID> rids;
  std::vector<float> plano;
  std::vector<std::size_t> grupo;
};

Datos agrupados(const std::vector<Vec>& c, std::size_t n, unsigned semilla) {
  std::mt19937 gen(semilla);
  const prueba::RealUniforme ruido(-1.0, 1.0);
  Datos d;
  const std::size_t dim = c.front().size();
  for (std::size_t i = 0; i < n; ++i) {
    const std::size_t g = prueba::indice_uniforme(gen, c.size());
    for (std::size_t j = 0; j < dim; ++j) {
      d.plano.push_back(c[g][j] + static_cast<float>(ruido(gen)));
    }
    d.rids.push_back(RID{static_cast<PageId>(i / 100 + 1), static_cast<SlotId>(i % 100)});
    d.grupo.push_back(g);
  }
  return d;
}

std::span<const float> vector_de(const Datos& d, std::size_t i, std::size_t dim) {
  return std::span<const float>(d.plano).subspan(i * dim, dim);
}

std::multiset<std::pair<PageId, SlotId>> rids_de(Ivf& ivf, std::size_t list) {
  std::multiset<std::pair<PageId, SlotId>> s;
  for (const auto& [rid, v] : ivf.list_entries(list)) s.insert({rid.page, rid.slot});
  return s;
}

// ---------------------------------------------------------------------------
// Construccion
// ---------------------------------------------------------------------------

TEST_F(IvfTest, UnIndiceNuevoTieneSusListasVacias) {
  auto ivf = Ivf::create(path_, centros(4, 8));
  EXPECT_EQ(ivf->nlist(), 4u);
  EXPECT_EQ(ivf->dim(), 8u);
  EXPECT_EQ(ivf->size(), 0u);
  for (std::size_t i = 0; i < 4; ++i) EXPECT_EQ(ivf->list_size(i), 0u);
  EXPECT_EQ(ivf->check_invariants(), "");
}

TEST_F(IvfTest, CadaVectorQuedaEnLaListaDeSuCentroide) {
  const auto c = centros(5, 8);
  const Datos d = agrupados(c, 2'000, 183);
  auto ivf = Ivf::create(path_, c);
  ivf->build(d.rids, d.plano);

  EXPECT_EQ(ivf->size(), 2'000u);
  EXPECT_EQ(ivf->check_invariants(), "");
  for (std::size_t g = 0; g < 5; ++g) {
    std::multiset<std::pair<PageId, SlotId>> esperados;
    for (std::size_t i = 0; i < d.rids.size(); ++i) {
      if (d.grupo[i] == g) esperados.insert({d.rids[i].page, d.rids[i].slot});
    }
    EXPECT_EQ(rids_de(*ivf, g), esperados) << "lista " << g;
    EXPECT_GT(esperados.size(), 300u);  // la prueba no es trivial
  }
}

TEST_F(IvfTest, ElVectorSeGuardaTalCual) {
  auto ivf = Ivf::create(path_, centros(2, 3));
  const Vec v{9.5F, -0.25F, 0.125F};
  ivf->insert(RID{7, 3}, v);
  const auto entradas = ivf->list_entries(0);
  ASSERT_EQ(entradas.size(), 1u);
  EXPECT_EQ(entradas[0].first, (RID{7, 3}));
  EXPECT_EQ(entradas[0].second, v);
}

TEST_F(IvfTest, ConEmpateGanaElCentroideDeMenorIndice) {
  auto ivf = Ivf::create(path_, {{0, 0}, {2, 0}});
  EXPECT_EQ(ivf->nearest_list(Vec{1, 0}), 0u);
  EXPECT_EQ(ivf->nearest_list(Vec{1.01F, 0}), 1u);
}

TEST_F(IvfTest, InsertarUnoPorUnoDaLasMismasListasQueBuild) {
  const auto c = centros(4, 6);
  const Datos d = agrupados(c, 500, 9);
  auto por_lote = Ivf::create(dir_ / "lote.ivf", c);
  por_lote->build(d.rids, d.plano);
  auto uno_a_uno = Ivf::create(dir_ / "uno.ivf", c);
  for (std::size_t i = 0; i < d.rids.size(); ++i) uno_a_uno->insert(d.rids[i], vector_de(d, i, 6));

  EXPECT_EQ(uno_a_uno->check_invariants(), "");
  for (std::size_t g = 0; g < 4; ++g) EXPECT_EQ(rids_de(*por_lote, g), rids_de(*uno_a_uno, g));
  // Por lote escribe cada pagina de datos una vez; uno a uno, una por insercion.
  EXPECT_LT(por_lote->stats().pages_written, uno_a_uno->stats().pages_written);
}

TEST_F(IvfTest, NlistEsConfigurable) {
  const Datos d = agrupados(centros(4, 4), 1'000, 21);
  for (const std::size_t nlist : {1u, 2u, 7u, 32u}) {
    // Centroides tomados de los propios datos, como haria un K-Means sembrado.
    std::vector<Vec> c;
    for (std::size_t i = 0; i < nlist; ++i) {
      const auto v = vector_de(d, i * 31 % 1'000, 4);
      c.emplace_back(v.begin(), v.end());
    }
    auto ivf = Ivf::create(dir_ / ("n" + std::to_string(nlist) + ".ivf"), c);
    ivf->build(d.rids, d.plano);
    EXPECT_EQ(ivf->nlist(), nlist);
    std::uint64_t suma = 0;
    for (std::size_t i = 0; i < nlist; ++i) suma += ivf->list_size(i);
    EXPECT_EQ(suma, 1'000u);
    EXPECT_EQ(ivf->check_invariants(), "") << "nlist " << nlist;
  }
}

TEST_F(IvfTest, UnaListaLargaOcupaVariasPaginasEnOrden) {
  // Dimension 64: cada entrada mide 6 + 256 = 262 bytes y entran 15 por pagina.
  auto ivf = Ivf::create(path_, {Vec(64, 0.0F)});
  ASSERT_EQ(ivf->entries_per_page(), 15u);
  for (std::uint32_t i = 0; i < 1'000; ++i) {
    ivf->insert(RID{i + 1, 0}, Vec(64, static_cast<float>(i)));
  }
  const auto entradas = ivf->list_entries(0);
  ASSERT_EQ(entradas.size(), 1'000u);
  for (std::uint32_t i = 0; i < 1'000; ++i) {
    ASSERT_EQ(entradas[i].first.page, i + 1);
    ASSERT_EQ(entradas[i].second[63], static_cast<float>(i));
  }
  // 67 paginas de datos (ceil(1000 / 15)) y una de directorio.
  EXPECT_EQ(ivf->page_count(), 68u);
}

TEST_F(IvfTest, ReportaPaginasYTiempoDeConstruccion) {
  const auto c = centros(3, 16);
  const Datos d = agrupados(c, 3'000, 4);
  auto ivf = Ivf::create(path_, c);
  ivf->reset_stats();
  ivf->build(d.rids, d.plano);
  EXPECT_GT(ivf->stats().pages_written, 0u);
  EXPECT_GT(ivf->last_build_ms(), 0.0);
}

// ---------------------------------------------------------------------------
// Persistencia
// ---------------------------------------------------------------------------

TEST_F(IvfTest, SeReabreConSusCentroidesYListas) {
  const auto c = centros(4, 8);
  const Datos d = agrupados(c, 1'500, 77);
  std::vector<std::multiset<std::pair<PageId, SlotId>>> antes;
  {
    auto ivf = Ivf::create(path_, c, 8192);
    ivf->build(d.rids, d.plano);
    for (std::size_t g = 0; g < 4; ++g) antes.push_back(rids_de(*ivf, g));
    ivf->flush();
  }
  auto ivf = Ivf::open(path_, 8192);
  EXPECT_EQ(ivf->nlist(), 4u);
  EXPECT_EQ(ivf->dim(), 8u);
  EXPECT_EQ(ivf->size(), 1'500u);
  for (std::size_t g = 0; g < 4; ++g) {
    EXPECT_TRUE(std::equal(c[g].begin(), c[g].end(), ivf->centroid(g).begin()));
    EXPECT_EQ(rids_de(*ivf, g), antes[g]);
  }
  // Y sigue creciendo despues de reabrir.
  ivf->insert(RID{999, 1}, c[2]);
  EXPECT_EQ(ivf->list_size(2), antes[2].size() + 1);
  EXPECT_EQ(ivf->check_invariants(), "");
}

TEST_F(IvfTest, UnDirectorioDeVariasPaginasSobreviveAlReabrir) {
  // Dimension 300: cada entrada de directorio mide 1 212 bytes y entran 3 por
  // pagina, asi que 10 centroides ocupan 4 paginas de directorio.
  std::vector<Vec> c;
  for (int i = 0; i < 10; ++i) c.push_back(Vec(300, static_cast<float>(i)));
  { auto ivf = Ivf::create(path_, c); }
  auto ivf = Ivf::open(path_);
  ASSERT_EQ(ivf->nlist(), 10u);
  for (std::size_t i = 0; i < 10; ++i) EXPECT_EQ(ivf->centroid(i)[299], static_cast<float>(i));
}

TEST_F(IvfTest, AbrirUnArchivoVacioEsIoError) {
  EXPECT_THROW((void)Ivf::open(path_), IoError);
}

TEST_F(IvfTest, ReabrirOtraVersionDelFormatoEsIoError) {
  { auto ivf = Ivf::create(path_, centros(2, 2)); }
  {
    DiskManager disco(path_);
    std::vector<std::byte> meta(DiskManager::kMetaSize);
    disco.read_meta(meta);
    const std::uint32_t otra = 99;
    std::memcpy(meta.data(), &otra, sizeof otra);
    disco.write_meta(meta);
    disco.flush();
  }
  EXPECT_THROW((void)Ivf::open(path_), IoError);
}

TEST_F(IvfTest, CrearSobreUnIndiceExistenteEsSchemaError) {
  { auto ivf = Ivf::create(path_, centros(2, 2)); }
  EXPECT_THROW((void)Ivf::create(path_, centros(2, 2)), SchemaError);
}

// ---------------------------------------------------------------------------
// Errores
// ---------------------------------------------------------------------------

TEST_F(IvfTest, CentroidesInvalidos) {
  EXPECT_THROW((void)Ivf::create(dir_ / "a.ivf", {}), SchemaError);
  EXPECT_THROW((void)Ivf::create(dir_ / "b.ivf", {{1, 2}, {1, 2, 3}}), SchemaError);
  EXPECT_THROW((void)Ivf::create(dir_ / "c.ivf", {{1, std::nanf("")}}), InvalidRecord);
}

TEST_F(IvfTest, UnVectorQueNoCabeEnLaPaginaEsSchemaError) {
  // 2 000 floats son 8 000 bytes: no entran en una pagina de 4 KB.
  EXPECT_THROW((void)Ivf::create(dir_ / "chica.ivf", {Vec(2'000, 0.0F)}), SchemaError);
  auto ivf = Ivf::create(dir_ / "grande.ivf", {Vec(2'000, 0.0F)}, 16'384);
  EXPECT_EQ(ivf->entries_per_page(), 2u);
}

TEST_F(IvfTest, InsertarUnVectorDeOtraDimensionEsInvalidRecord) {
  auto ivf = Ivf::create(path_, centros(2, 3));
  EXPECT_THROW(ivf->insert(RID{1, 0}, Vec{1, 2}), InvalidRecord);
  EXPECT_THROW(ivf->insert(RID{1, 0}, Vec{1, 2, std::nanf("")}), InvalidRecord);
  EXPECT_EQ(ivf->size(), 0u);
}

TEST_F(IvfTest, UnBuildConUnVectorMaloNoEscribeNada) {
  const auto c = centros(2, 2);
  auto ivf = Ivf::create(path_, c);
  const std::vector<RID> rids{{1, 0}, {1, 1}, {1, 2}};
  const std::vector<float> plano{9, 0, 0, 9, 1, std::numeric_limits<float>::infinity()};
  EXPECT_THROW(ivf->build(rids, plano), InvalidRecord);
  EXPECT_EQ(ivf->size(), 0u);
  EXPECT_EQ(ivf->check_invariants(), "");
  // Y un tamano que no calza con la dimension tampoco.
  EXPECT_THROW(ivf->build(rids, std::vector<float>{1, 2, 3}), InvalidRecord);
}

}  // namespace
}  // namespace quipudb
