// Pruebas de la busqueda con ef_search, el borrado y la integracion del HNSW
// con el catalogo (issue #186).
//
// La busqueda exacta del #182 es el oraculo: con ef_search >= N el HNSW tiene
// que devolver exactamente lo mismo, y con menos candidatos tiene que
// acercarse como dice la teoria: el Recall@10 sube con ef_search y las
// comparaciones crecen como log N, no como N.

#include <gtest/gtest.h>

#include <cmath>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <memory>
#include <random>
#include <set>
#include <string>
#include <vector>

#include "azar_portable.hpp"
#include "quipudb/catalog/database.hpp"
#include "quipudb/error.hpp"
#include "quipudb/index/hnsw_index.hpp"
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

double recall(const std::vector<VectorMatch>& obtenido, const std::vector<VectorMatch>& verdad) {
  std::set<RID> v;
  for (const auto& m : verdad) v.insert(m.rid);
  std::size_t aciertos = 0;
  for (const auto& m : obtenido) aciertos += v.count(m.rid);
  return verdad.empty() ? 1.0 : static_cast<double>(aciertos) / static_cast<double>(verdad.size());
}

class HnswBusquedaTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_hnsw_bus_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
    db_ = std::make_unique<Database>(dir_ / "catalogo.txt");
  }
  void TearDown() override {
    db_.reset();
    fs::remove_all(dir_);
  }

  /// Tabla con `n` vectores al azar y un HNSW con esta metrica sobre ellos.
  HnswIndex& tabla_con_hnsw(std::size_t n, std::uint16_t dim, VectorMetric metrica,
                            HnswParams p, unsigned semilla,
                            const std::string& nombre = "por_vec") {
    if (tabla_ == nullptr) {
      tabla_ = &db_->create_table(imagenes(dim), kind::kHeap);
      std::mt19937 gen(semilla);
      for (std::size_t i = 0; i < n; ++i) {
        tabla_->insert(
            Record{static_cast<std::int32_t>(i + 1), std::string("img"), al_azar(gen, dim)});
      }
    }
    auto* h = dynamic_cast<HnswIndex*>(
        &db_->create_hnsw_index("imagenes", nombre, "vec", metrica, p));
    EXPECT_NE(h, nullptr);
    return *h;
  }

  fs::path dir_;
  std::unique_ptr<Database> db_;
  TableFile* tabla_ = nullptr;
};

// ---------------------------------------------------------------------------
// Lo que dice la teoria
// ---------------------------------------------------------------------------

TEST_F(HnswBusquedaTest, ConEfSearchGrandeEsLaBusquedaExacta) {
  // Con ef_search >= N la busqueda no puede parar antes de ver todo lo que se
  // alcanza, y el grafo esta conectado: el resultado es el exacto, con los
  // mismos empates.
  constexpr std::size_t kN = 1'500;
  for (const VectorMetric m : kTodas) {
    HnswIndex& h = tabla_con_hnsw(kN, 12, m, {.m = 6, .ef_construction = 40}, 186,
                                  "por_" + std::string(name_of(m)));
    std::mt19937 gen(3);
    for (int consulta = 0; consulta < 10; ++consulta) {
      const VectorValue q = al_azar(gen, 12);
      for (const std::size_t k : {1u, 10u, 50u}) {
        EXPECT_EQ(h.k_nearest(q, k, m, kN), scan_k_nearest_vector(*tabla_, "vec", q, k, m))
            << name_of(m) << " k=" << k;
      }
    }
  }
}

TEST_F(HnswBusquedaTest, ElRecallSubeConEfSearchYComparaMenosQueLaExacta) {
  constexpr std::size_t kN = 5'000;
  constexpr std::uint16_t kDim = 16;
  HnswIndex& h =
      tabla_con_hnsw(kN, kDim, VectorMetric::kEuclidean, {.m = 8, .ef_construction = 64}, 7);
  std::mt19937 gen(99);
  std::vector<VectorValue> consultas;
  for (int i = 0; i < 50; ++i) consultas.push_back(al_azar(gen, kDim));
  std::vector<std::vector<VectorMatch>> verdad;
  for (const auto& q : consultas) {
    verdad.push_back(scan_k_nearest_vector(*tabla_, "vec", q, 10, VectorMetric::kEuclidean));
  }

  std::vector<double> recalls;
  std::vector<std::uint64_t> comparados;
  std::cout << "[ HNSW ] N=" << kN << " M=8 efConstruction=64 k=10, euclidiana\n";
  for (const std::size_t ef : {10u, 20u, 40u, 80u, 160u, 320u}) {
    double suma = 0.0;
    h.reset_stats();
    for (std::size_t i = 0; i < consultas.size(); ++i) {
      suma += recall(h.k_nearest(consultas[i], 10, VectorMetric::kEuclidean, ef), verdad[i]);
    }
    recalls.push_back(suma / static_cast<double>(consultas.size()));
    comparados.push_back(h.stats().records_examined / consultas.size());
    std::cout << "[ HNSW ]   ef_search=" << std::setw(3) << ef << "  Recall@10=" << std::fixed
              << std::setprecision(3) << recalls.back()
              << "  vectores comparados por consulta=" << comparados.back() << "\n";
    EXPECT_EQ(h.stats().pages_read, 0u);  // el grafo esta en memoria
  }
  for (std::size_t i = 1; i < recalls.size(); ++i) {
    // En promedio sube; consulta por consulta no esta garantizado (ver hnsw.hpp).
    EXPECT_GE(recalls[i], recalls[i - 1] - 0.01) << "paso " << i;
    EXPECT_GT(comparados[i], comparados[i - 1]) << "paso " << i;
  }
  EXPECT_GE(recalls.back(), 0.99);
  // Aun con el ef_search mas grande se compara una fraccion de la tabla.
  EXPECT_LT(comparados.back(), kN / 2);
  EXPECT_LT(comparados.front(), kN / 10);
}

TEST_F(HnswBusquedaTest, LasComparacionesCrecenComoLogN) {
  // Puntos en el plano y ef_search chico, el caso donde las capas importan.
  // En dos dimensiones las aristas de la capa 0 son cortas: sin bajar por las
  // capas, una busqueda que empieza lejos tiene que caminar unos sqrt(N)
  // saltos, y con 16 veces mas puntos compararia unas 4 veces mas. Bajando
  // desde arriba llega cerca en unas log N comparaciones y el costo casi no
  // cambia.
  const HnswParams p{.m = 8, .ef_construction = 48};
  std::vector<double> por_consulta;
  for (const std::size_t n : {1'000u, 16'000u}) {
    auto h = Hnsw::create(dir_ / ("log" + std::to_string(n) + ".hnsw"), 2,
                          VectorMetric::kEuclidean, p);
    std::mt19937 gen(5);
    std::vector<RID> rids;
    std::vector<float> plano;
    for (std::size_t i = 0; i < n; ++i) {
      rids.push_back(RID{static_cast<PageId>(i / 100 + 1), static_cast<SlotId>(i % 100)});
      const auto v = al_azar(gen, 2);
      plano.insert(plano.end(), v.begin(), v.end());
    }
    h->build(rids, plano);
    h->reset_stats();
    std::mt19937 gq(17);
    for (int i = 0; i < 200; ++i) {
      (void)h->search(al_azar(gq, 2), 1, VectorMetric::kEuclidean, 4);
    }
    por_consulta.push_back(static_cast<double>(h->stats().records_examined) / 200.0);
    std::cout << "[ HNSW ] N=" << std::setw(5) << n << "  dim=2 ef_search=4  comparados por "
              << "consulta=" << std::fixed << std::setprecision(0) << por_consulta.back() << "\n";
  }
  EXPECT_LT(por_consulta[1] / por_consulta[0], 2.0);
}

// ---------------------------------------------------------------------------
// Contrato de la busqueda
// ---------------------------------------------------------------------------

TEST_F(HnswBusquedaTest, CasosBorde) {
  HnswIndex& h = tabla_con_hnsw(30, 3, VectorMetric::kCosine, {.m = 4}, 2);
  const VectorValue q{1.0F, 0.0F, 0.0F};
  EXPECT_TRUE(h.k_nearest(q, 0, VectorMetric::kCosine, 10).empty());
  // k mayor que la tabla devuelve todo, ordenado.
  const auto todo = h.k_nearest(q, 100, VectorMetric::kCosine, 100);
  EXPECT_EQ(todo, scan_k_nearest_vector(*tabla_, "vec", q, 100, VectorMetric::kCosine));
  EXPECT_EQ(todo.size(), 30u);
  // ef_search menor que k se toma como k.
  EXPECT_EQ(h.k_nearest(q, 30, VectorMetric::kCosine, 1).size(), 30u);
  EXPECT_THROW((void)h.k_nearest(q, 5, VectorMetric::kCosine, 0), InvalidRecord);
  const VectorValue corto{1.0F, 0.0F};
  EXPECT_THROW((void)h.k_nearest(corto, 5, VectorMetric::kCosine, 10), InvalidRecord);
  // Un indice vacio no devuelve nada.
  auto vacio = Hnsw::create(dir_ / "vacio.hnsw", 3, VectorMetric::kEuclidean);
  EXPECT_TRUE(vacio->search(q, 5, VectorMetric::kEuclidean, 10).empty());
}

TEST_F(HnswBusquedaTest, ConEmpateGanaElMenorRidComoEnLaExacta) {
  // Cinco copias del mismo vector, insertadas del RID mas alto al mas bajo:
  // el orden de los nodos es el inverso al de los RID. El corte en k tiene que
  // quedarse con los RID menores, como `scan_k_nearest_vector`.
  auto h = Hnsw::create(dir_ / "empate.hnsw", 2, VectorMetric::kEuclidean, {.m = 4});
  const std::vector<float> v = {1.0F, 1.0F};
  for (SlotId s = 5; s-- > 0;) h->insert(RID{1, s}, v);
  const auto r = h->search(v, 2, VectorMetric::kEuclidean, 10);
  ASSERT_EQ(r.size(), 2u);
  EXPECT_EQ(r[0].rid, (RID{1, 0}));
  EXPECT_EQ(r[1].rid, (RID{1, 1}));
}

TEST_F(HnswBusquedaTest, LaMetricaTieneQueSerLaDelGrafo) {
  HnswIndex& h = tabla_con_hnsw(50, 4, VectorMetric::kCosine, {.m = 4}, 3);
  const VectorValue q{1.0F, 2.0F, 3.0F, 4.0F};
  try {
    (void)h.k_nearest(q, 5, VectorMetric::kDotProduct, 10);
    FAIL() << "no lanzo";
  } catch (const SchemaError& e) {
    const std::string msg = e.what();
    EXPECT_NE(msg.find("COSINE"), std::string::npos) << msg;
  }
  EXPECT_THROW((void)h.k_nearest(q, 5, VectorMetric::kEuclidean, 10), SchemaError);
}

TEST_F(HnswBusquedaTest, ReportaComparacionesYDevueltos) {
  HnswIndex& h = tabla_con_hnsw(400, 6, VectorMetric::kEuclidean, {.m = 4}, 4);
  h.reset_stats();
  std::mt19937 gen(1);
  (void)h.k_nearest(al_azar(gen, 6), 7, VectorMetric::kEuclidean, 20);
  EXPECT_EQ(h.stats().records_returned, 7u);
  EXPECT_GT(h.stats().records_examined, 7u);
  EXPECT_LT(h.stats().records_examined, 400u);
  EXPECT_EQ(h.stats().pages_written, 0u);
}

// ---------------------------------------------------------------------------
// Borrado
// ---------------------------------------------------------------------------

TEST_F(HnswBusquedaTest, BorrarSacaLasFilasDeLosResultados) {
  constexpr std::size_t kN = 800;
  HnswIndex& h = tabla_con_hnsw(kN, 8, VectorMetric::kEuclidean, {.m = 4}, 5);
  // Se borra una de cada tres filas de la tabla y del indice, como el executor.
  struct Fila {
    RID rid;
    Key id;
    VectorValue vec;
  };
  std::vector<Fila> filas;
  {
    auto cursor = tabla_->cursor();
    Record r;
    while (cursor->next(r)) filas.push_back({cursor->rid(), r[0], std::get<VectorValue>(r[2])});
  }
  std::set<RID> borradas;
  for (std::size_t i = 0; i < filas.size(); i += 3) {
    ASSERT_TRUE(h.remove(filas[i].vec, filas[i].rid));
    ASSERT_EQ(tabla_->remove(filas[i].id), 1u);
    borradas.insert(filas[i].rid);
  }
  EXPECT_FALSE(h.remove(filas[0].vec, filas[0].rid));
  EXPECT_EQ(h.size(), kN - borradas.size());
  EXPECT_EQ(h.hnsw().size(), kN);  // siguen en el grafo para navegar
  EXPECT_EQ(h.hnsw().check_invariants(), "");

  std::mt19937 gen(8);
  for (int i = 0; i < 10; ++i) {
    const VectorValue q = al_azar(gen, 8);
    const auto r = h.k_nearest(q, 20, VectorMetric::kEuclidean, kN);
    for (const auto& m : r) EXPECT_EQ(borradas.count(m.rid), 0u);
    EXPECT_EQ(r, scan_k_nearest_vector(*tabla_, "vec", q, 20, VectorMetric::kEuclidean));
  }
}

TEST_F(HnswBusquedaTest, ElBorradoSobreviveAlReabrirLaBase) {
  const VectorValue q{0.2F, 0.1F, -0.3F, 0.5F};
  RID quitada{};
  std::vector<VectorMatch> antes;
  {
    HnswIndex& h = tabla_con_hnsw(300, 4, VectorMetric::kCosine, {.m = 4}, 6);
    const auto mejor = h.k_nearest(q, 1, VectorMetric::kCosine, 300).front();
    quitada = mejor.rid;
    const auto fila = tabla_->read(quitada);
    ASSERT_TRUE(fila.has_value());
    ASSERT_TRUE(h.remove((*fila)[2], quitada));
    antes = h.k_nearest(q, 10, VectorMetric::kCosine, 50);
    db_->flush();
  }
  db_ = std::make_unique<Database>(dir_ / "catalogo.txt");
  auto* h = dynamic_cast<HnswIndex*>(&db_->index("imagenes", "por_vec"));
  ASSERT_NE(h, nullptr);
  EXPECT_EQ(h->kind(), kind::kHnsw);
  EXPECT_EQ(h->size(), 299u);
  EXPECT_EQ(h->hnsw().metric(), VectorMetric::kCosine);
  EXPECT_EQ(h->k_nearest(q, 10, VectorMetric::kCosine, 50), antes);
  for (const auto& m : antes) EXPECT_NE(m.rid, quitada);
}

TEST_F(HnswBusquedaTest, RemoveDeUnaClaveQuitaTodasSusCopias) {
  HnswIndex& h = tabla_con_hnsw(50, 3, VectorMetric::kEuclidean, {.m = 4}, 4);
  const VectorValue repetido{0.5F, 0.5F, 0.5F};
  h.insert(repetido, RID{900, 1});
  h.insert(repetido, RID{900, 2});
  EXPECT_EQ(h.search(repetido).size(), 2u);
  EXPECT_EQ(h.remove(repetido), 2u);
  EXPECT_TRUE(h.search(repetido).empty());
  EXPECT_EQ(h.size(), 50u);
  EXPECT_EQ(h.scan().size(), 50u);
  EXPECT_THROW((void)h.range_search(repetido, repetido), Unsupported);
}

// ---------------------------------------------------------------------------
// Catalogo
// ---------------------------------------------------------------------------

TEST_F(HnswBusquedaTest, CreateIndexSinMetricaExplicaQueUsar) {
  db_->create_table(imagenes(4), kind::kHeap);
  try {
    (void)db_->create_index("imagenes", "por_vec", "vec", kind::kHnsw);
    FAIL() << "no lanzo";
  } catch (const SchemaError& e) {
    EXPECT_NE(std::string(e.what()).find("create_hnsw_index"), std::string::npos);
  }
  EXPECT_EQ(db_->catalog().table("imagenes").index("por_vec"), nullptr);
}

TEST_F(HnswBusquedaTest, ColumnasYParametrosEquivocados) {
  db_->create_table(imagenes(4), kind::kHeap);
  EXPECT_THROW(
      (void)db_->create_hnsw_index("imagenes", "a", "nombre", VectorMetric::kCosine),
      SchemaError);
  // M invalido: el indice no queda registrado ni en disco.
  EXPECT_THROW((void)db_->create_hnsw_index("imagenes", "b", "vec", VectorMetric::kCosine,
                                            {.m = 1}),
               SchemaError);
  EXPECT_EQ(db_->catalog().table("imagenes").index("b"), nullptr);
  EXPECT_FALSE(fs::exists(dir_ / "imagenes.b.hnsw"));
}

TEST_F(HnswBusquedaTest, DropIndexBorraElArchivo) {
  tabla_con_hnsw(20, 4, VectorMetric::kEuclidean, {.m = 4}, 1);
  ASSERT_TRUE(fs::exists(dir_ / "imagenes.por_vec.hnsw"));
  db_->drop_index("imagenes", "por_vec");
  EXPECT_FALSE(fs::exists(dir_ / "imagenes.por_vec.hnsw"));
}

TEST_F(HnswBusquedaTest, ElExecutorLoMantieneAlInsertarYBorrarFilas) {
  // Lo que hace el executor: insertar en la tabla y en cada indice, y borrar
  // de los dos con el RID.
  HnswIndex& h = tabla_con_hnsw(100, 4, VectorMetric::kEuclidean, {.m = 4}, 9);
  const VectorValue v{9.0F, 9.0F, 9.0F, 9.0F};
  const RID rid = tabla_->insert(Record{std::int32_t{1000}, std::string("nueva"), v});
  h.insert(v, rid);
  EXPECT_EQ(h.k_nearest(v, 1, VectorMetric::kEuclidean, 10).front().rid, rid);
  ASSERT_TRUE(h.remove(v, rid));
  ASSERT_EQ(tabla_->remove(Key{std::int32_t{1000}}), 1u);
  EXPECT_NE(h.k_nearest(v, 1, VectorMetric::kEuclidean, 10).front().rid, rid);
}

}  // namespace
}  // namespace quipudb
