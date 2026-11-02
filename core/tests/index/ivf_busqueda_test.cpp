// Pruebas de la busqueda con nprobe, el borrado y la integracion del IVF con
// el catalogo (issue #184).
//
// La busqueda exacta del #182 es el oraculo: el IVF tiene que devolver lo
// mismo con nprobe = nlist, y con menos listas tiene que acercarse a eso como
// dice la teoria (el Recall@10 nunca baja al subir nprobe).

#include <gtest/gtest.h>

#include <algorithm>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <memory>
#include <set>
#include <string>
#include <vector>

#include "azar_portable.hpp"
#include "quipudb/catalog/database.hpp"
#include "quipudb/error.hpp"
#include "quipudb/index/ivf_index.hpp"
#include "quipudb/index/vector_scan.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

constexpr VectorMetric kTodas[] = {VectorMetric::kEuclidean, VectorMetric::kDotProduct,
                                   VectorMetric::kCosine};

Schema imagenes(std::uint16_t dim) {
  return Schema{
      .table_name = "imagenes",
      .columns = {{"id", DataType::Int}, {"nombre", DataType::Varchar, 8},
                  {"vec", DataType::Vector, dim}},
      .key_column = 0,
  };
}

VectorValue al_azar(std::mt19937& gen, std::size_t dim) {
  const prueba::RealUniforme u(-1.0, 1.0);
  VectorValue v(dim);
  for (auto& x : v) x = static_cast<float>(u(gen));
  return v;
}

class IvfBusquedaTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_ivf_bus_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
    db_ = std::make_unique<Database>(dir_ / "catalogo.txt");
  }
  void TearDown() override {
    db_.reset();
    fs::remove_all(dir_);
  }

  /// Tabla con `n` vectores al azar y un IVF de `nlist` listas cuyos
  /// centroides son los primeros `nlist` vectores (una siembra al azar, como
  /// la de un K-Means sin iterar).
  IvfIndex& tabla_con_ivf(std::size_t n, std::uint16_t dim, std::size_t nlist,
                          unsigned semilla) {
    tabla_ = &db_->create_table(imagenes(dim), kind::kHeap);
    std::mt19937 gen(semilla);
    std::vector<std::vector<float>> centroides;
    for (std::size_t i = 0; i < n; ++i) {
      const VectorValue v = al_azar(gen, dim);
      tabla_->insert(Record{static_cast<std::int32_t>(i + 1), std::string("img"), v});
      if (centroides.size() < nlist) centroides.push_back(v);
    }
    auto* ivf = dynamic_cast<IvfIndex*>(
        &db_->create_ivf_index("imagenes", "por_vec", "vec", centroides));
    EXPECT_NE(ivf, nullptr);
    return *ivf;
  }

  fs::path dir_;
  std::unique_ptr<Database> db_;
  TableFile* tabla_ = nullptr;
};

double recall(const std::vector<VectorMatch>& obtenido, const std::vector<VectorMatch>& verdad) {
  std::set<RID> v;
  for (const auto& m : verdad) v.insert(m.rid);
  std::size_t aciertos = 0;
  for (const auto& m : obtenido) aciertos += v.count(m.rid);
  return verdad.empty() ? 1.0 : static_cast<double>(aciertos) / static_cast<double>(verdad.size());
}

// ---------------------------------------------------------------------------
// Lo que dice la teoria
// ---------------------------------------------------------------------------

TEST_F(IvfBusquedaTest, ConNprobeIgualANlistEsLaBusquedaExacta) {
  IvfIndex& ivf = tabla_con_ivf(2'000, 16, 20, 184);
  std::mt19937 gen(5);
  for (int consulta = 0; consulta < 15; ++consulta) {
    const VectorValue q = al_azar(gen, 16);
    for (const VectorMetric m : kTodas) {
      for (const std::size_t k : {1u, 10u, 50u}) {
        EXPECT_EQ(ivf.k_nearest(q, k, m, 20), scan_k_nearest_vector(*tabla_, "vec", q, k, m))
            << name_of(m) << " k=" << k;
      }
    }
  }
}

TEST_F(IvfBusquedaTest, ElRecallNuncaBajaAlSubirNprobeYComparaMenosQueLaExacta) {
  constexpr std::size_t kN = 4'000;
  constexpr std::size_t kNlist = 32;
  IvfIndex& ivf = tabla_con_ivf(kN, 16, kNlist, 7);
  std::mt19937 gen(99);
  std::vector<VectorValue> consultas;
  for (int i = 0; i < 40; ++i) consultas.push_back(al_azar(gen, 16));

  const std::size_t nprobes[] = {1, 2, 4, 8, 16, 32};
  double recall_previo = 0.0;
  std::uint64_t comparados_previo = 0;
  std::cout << "[ IVF ] N=" << kN << " nlist=" << kNlist << " k=10, euclidiana\n";
  for (const std::size_t nprobe : nprobes) {
    double suma = 0.0;
    ivf.reset_stats();
    for (const auto& q : consultas) {
      const auto verdad = scan_k_nearest_vector(*tabla_, "vec", q, 10, VectorMetric::kEuclidean);
      suma += recall(ivf.k_nearest(q, 10, VectorMetric::kEuclidean, nprobe), verdad);
    }
    const double r = suma / static_cast<double>(consultas.size());
    const std::uint64_t comparados = ivf.stats().records_examined / consultas.size();
    std::cout << "[ IVF ]   nprobe=" << std::setw(2) << nprobe << "  Recall@10=" << std::fixed
              << std::setprecision(3) << r << "  vectores comparados por consulta=" << comparados
              << "\n";
    EXPECT_GE(r, recall_previo) << "nprobe " << nprobe;
    EXPECT_GT(comparados, comparados_previo) << "nprobe " << nprobe;
    recall_previo = r;
    comparados_previo = comparados;
    if (nprobe == 1) {
      EXPECT_LT(r, 1.0);  // con una sola lista se pierden vecinos: la prueba no es trivial
      EXPECT_LT(comparados, kN / 4);
    }
  }
  EXPECT_DOUBLE_EQ(recall_previo, 1.0);
  EXPECT_EQ(comparados_previo, kN);  // nprobe = nlist compara todo
}

TEST_F(IvfBusquedaTest, ComparaExactamenteLosVectoresDeLasListasVisitadas) {
  IvfIndex& ivf = tabla_con_ivf(1'500, 8, 12, 3);
  std::mt19937 gen(1);
  const VectorValue q = al_azar(gen, 8);
  const auto orden = ivf.ivf().probe_order(q);
  for (const std::size_t nprobe : {1u, 3u, 12u}) {
    std::uint64_t esperados = 0;
    for (std::size_t i = 0; i < nprobe; ++i) esperados += ivf.ivf().list_size(orden[i]);
    ivf.reset_stats();
    (void)ivf.k_nearest(q, 10, VectorMetric::kCosine, nprobe);
    EXPECT_EQ(ivf.stats().records_examined, esperados) << "nprobe " << nprobe;
  }
}

TEST_F(IvfBusquedaTest, VisitaPrimeroLaListaDelCentroideMasCercano) {
  IvfIndex& ivf = tabla_con_ivf(500, 4, 6, 11);
  std::mt19937 gen(2);
  for (int i = 0; i < 20; ++i) {
    const VectorValue q = al_azar(gen, 4);
    EXPECT_EQ(ivf.ivf().probe_order(q).front(), ivf.ivf().nearest_list(q));
  }
}

// ---------------------------------------------------------------------------
// Contrato de la busqueda
// ---------------------------------------------------------------------------

TEST_F(IvfBusquedaTest, ConEmpateDeDistanciaGanaElMenorRidAunqueEstenEnListasDistintas) {
  // Todos los vectores estan a distancia 2 de la consulta, repartidos en las
  // dos listas e insertados del RID mas alto al mas bajo. El corte en k tiene
  // que quedarse con los RID menores, como hace la busqueda exacta.
  auto ivf = Ivf::create(dir_ / "empate.ivf", {{-1.0F, 0.0F}, {1.0F, 0.0F}});
  const std::vector<std::vector<float>> puntos = {
      {2.0F, 0.0F}, {-2.0F, 0.0F}, {0.0F, 2.0F}, {0.0F, -2.0F}};
  for (SlotId s = 8; s-- > 0;) ivf->insert(RID{1, s}, puntos[s % puntos.size()]);
  ASSERT_GT(ivf->list_size(0), 0U);
  ASSERT_GT(ivf->list_size(1), 0U);

  const std::vector<float> q = {0.0F, 0.0F};
  const auto r = ivf->search(q, 3, VectorMetric::kEuclidean, 2);
  ASSERT_EQ(r.size(), 3U);
  EXPECT_EQ(r[0].rid, (RID{1, 0}));
  EXPECT_EQ(r[1].rid, (RID{1, 1}));
  EXPECT_EQ(r[2].rid, (RID{1, 2}));
}

TEST_F(IvfBusquedaTest, CasosBorde) {
  IvfIndex& ivf = tabla_con_ivf(30, 4, 3, 1);
  const VectorValue q{0.1F, 0.2F, 0.3F, 0.4F};
  ivf.reset_stats();
  EXPECT_TRUE(ivf.k_nearest(q, 0, VectorMetric::kCosine, 3).empty());
  EXPECT_EQ(ivf.stats().pages_read, 0u);  // k = 0 no lee nada
  EXPECT_EQ(ivf.k_nearest(q, 100, VectorMetric::kCosine, 3).size(), 30u);  // menos de k
  // Un nprobe mayor que nlist se toma como nlist.
  EXPECT_EQ(ivf.k_nearest(q, 5, VectorMetric::kEuclidean, 99),
            ivf.k_nearest(q, 5, VectorMetric::kEuclidean, 3));
  EXPECT_THROW((void)ivf.k_nearest(q, 5, VectorMetric::kCosine, 0), InvalidRecord);
  EXPECT_THROW((void)ivf.k_nearest(VectorValue{1, 2}, 5, VectorMetric::kCosine, 1),
               InvalidRecord);
}

// ---------------------------------------------------------------------------
// Borrado
// ---------------------------------------------------------------------------

TEST_F(IvfBusquedaTest, BorrarQuitaSoloEsaEntradaYLasListasSiguenCuadrando) {
  IvfIndex& ivf = tabla_con_ivf(800, 8, 5, 21);
  auto todas = ivf.scan();
  ASSERT_EQ(todas.size(), 800u);
  std::mt19937 gen(3);
  prueba::barajar(todas, gen);
  for (std::size_t i = 0; i < 500; ++i) {
    ASSERT_TRUE(ivf.remove(todas[i].first, todas[i].second));
    ASSERT_FALSE(ivf.remove(todas[i].first, todas[i].second));  // ya no esta
    if (i % 50 == 0) ASSERT_EQ(ivf.ivf().check_invariants(), "") << "borrados " << i;
  }
  EXPECT_EQ(ivf.size(), 300u);
  EXPECT_EQ(ivf.ivf().check_invariants(), "");
  std::multiset<RID> esperados;
  for (std::size_t i = 500; i < 800; ++i) esperados.insert(todas[i].second);
  std::multiset<RID> quedan;
  for (const auto& [k, rid] : ivf.scan()) quedan.insert(rid);
  EXPECT_EQ(quedan, esperados);
}

TEST_F(IvfBusquedaTest, VaciarYRellenarReutilizaLasPaginas) {
  IvfIndex& ivf = tabla_con_ivf(1'000, 32, 4, 8);
  const auto todas = ivf.scan();
  const PageId paginas = ivf.ivf().page_count();
  for (int vuelta = 0; vuelta < 3; ++vuelta) {
    for (const auto& [k, rid] : todas) ASSERT_TRUE(ivf.remove(k, rid));
    ASSERT_EQ(ivf.size(), 0u);
    ASSERT_EQ(ivf.ivf().check_invariants(), "");
    for (const auto& [k, rid] : todas) ivf.insert(k, rid);
    ASSERT_EQ(ivf.ivf().check_invariants(), "");
  }
  // Las paginas liberadas vuelven a usarse: el archivo no crece.
  EXPECT_EQ(ivf.ivf().page_count(), paginas);
}

TEST_F(IvfBusquedaTest, RemoveDeUnaClaveQuitaTodasSusCopias) {
  IvfIndex& ivf = tabla_con_ivf(50, 3, 2, 4);
  const VectorValue repetido{0.5F, 0.5F, 0.5F};
  ivf.insert(repetido, RID{900, 1});
  ivf.insert(repetido, RID{900, 2});
  EXPECT_EQ(ivf.search(repetido).size(), 2u);
  EXPECT_EQ(ivf.remove(repetido), 2u);
  EXPECT_TRUE(ivf.search(repetido).empty());
  EXPECT_EQ(ivf.size(), 50u);
}

// ---------------------------------------------------------------------------
// Catalogo
// ---------------------------------------------------------------------------

TEST_F(IvfBusquedaTest, SeConstruyeSobreLaTablaYSobreviveAlReabrirLaBase) {
  const VectorValue q{0.3F, -0.2F, 0.9F, 0.1F};
  std::vector<VectorMatch> antes;
  {
    IvfIndex& ivf = tabla_con_ivf(600, 4, 6, 15);
    EXPECT_EQ(ivf.size(), 600u);  // construido con lo que la tabla ya tenia
    antes = ivf.k_nearest(q, 10, VectorMetric::kCosine, 2);
    db_->flush();
  }
  db_ = std::make_unique<Database>(dir_ / "catalogo.txt");
  auto* ivf = dynamic_cast<IvfIndex*>(&db_->index("imagenes", "por_vec"));
  ASSERT_NE(ivf, nullptr);
  EXPECT_EQ(ivf->kind(), kind::kIvf);
  EXPECT_EQ(ivf->size(), 600u);
  EXPECT_EQ(ivf->k_nearest(q, 10, VectorMetric::kCosine, 2), antes);
}

TEST_F(IvfBusquedaTest, CreateIndexSinCentroidesExplicaQueUsar) {
  db_->create_table(imagenes(4), kind::kHeap);
  try {
    (void)db_->create_index("imagenes", "por_vec", "vec", kind::kIvf);
    FAIL() << "no lanzo";
  } catch (const SchemaError& e) {
    EXPECT_NE(std::string(e.what()).find("create_ivf_index"), std::string::npos);
  }
  EXPECT_EQ(db_->catalog().table("imagenes").index("por_vec"), nullptr);
}

TEST_F(IvfBusquedaTest, ColumnasYCentroidesEquivocados) {
  db_->create_table(imagenes(4), kind::kHeap);
  // IVF sobre algo que no es VECTOR.
  EXPECT_THROW((void)db_->create_ivf_index("imagenes", "a", "nombre", {{1, 2, 3, 4}}),
               SchemaError);
  // Otro indice sobre una columna VECTOR.
  EXPECT_THROW((void)db_->create_index("imagenes", "b", "vec", kind::kBPlusUnclustered),
               SchemaError);
  // Centroides de otra dimension: el indice no queda registrado ni en disco.
  EXPECT_THROW((void)db_->create_ivf_index("imagenes", "c", "vec", {{1, 2, 3}}), SchemaError);
  EXPECT_EQ(db_->catalog().table("imagenes").index("c"), nullptr);
  EXPECT_FALSE(fs::exists(dir_ / "imagenes.c.ivf"));
}

TEST_F(IvfBusquedaTest, DropIndexBorraElArchivo) {
  tabla_con_ivf(20, 4, 2, 1);
  ASSERT_TRUE(fs::exists(dir_ / "imagenes.por_vec.ivf"));
  db_->drop_index("imagenes", "por_vec");
  EXPECT_FALSE(fs::exists(dir_ / "imagenes.por_vec.ivf"));
}

}  // namespace
}  // namespace quipudb
