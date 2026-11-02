// Pruebas de la construccion del HNSW (issue #185).
//
// Lo que se comprueba primero es lo que dice el paper: P(nivel >= l) = M^-l,
// el grado nunca pasa de M (2M en la capa 0), cada nodo se alcanza desde la
// entrada en todas sus capas, y el costo de insertar crece como log N y no
// como N. Despues, que el grafo es determinista y que sobrevive al reabrir.

#include <gtest/gtest.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <random>
#include <span>
#include <string>
#include <vector>

#include "azar_portable.hpp"
#include "quipudb/error.hpp"
#include "quipudb/index/hnsw.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

std::vector<float> al_azar(std::size_t n, std::size_t dim, unsigned semilla) {
  std::mt19937 gen(semilla);
  const prueba::RealUniforme u(-1.0, 1.0);
  std::vector<float> v(n * dim);
  for (auto& x : v) x = static_cast<float>(u(gen));
  return v;
}

std::vector<RID> rids_de(std::size_t n) {
  std::vector<RID> r;
  r.reserve(n);
  for (std::size_t i = 0; i < n; ++i) {
    r.push_back(RID{static_cast<PageId>(i / 100 + 1), static_cast<SlotId>(i % 100)});
  }
  return r;
}

/// Todas las aristas de todos los nodos, para comparar dos grafos.
std::vector<std::vector<std::vector<std::uint32_t>>> aristas(const Hnsw& h) {
  std::vector<std::vector<std::vector<std::uint32_t>>> out(h.size());
  for (std::size_t id = 0; id < h.size(); ++id) {
    for (std::size_t l = 0; l <= h.level(id); ++l) {
      const auto v = h.neighbors(id, l);
      out[id].emplace_back(v.begin(), v.end());
    }
  }
  return out;
}

class HnswTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_hnsw_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
  }
  void TearDown() override { fs::remove_all(dir_); }

  std::unique_ptr<Hnsw> construido(const std::string& nombre, std::size_t n, std::size_t dim,
                                   HnswParams p, VectorMetric metrica = VectorMetric::kEuclidean,
                                   unsigned semilla = 185) {
    auto h = Hnsw::create(dir_ / nombre, dim, metrica, p);
    h->build(rids_de(n), al_azar(n, dim, semilla));
    return h;
  }

  fs::path dir_;
};

// ---------------------------------------------------------------------------
// Lo que dice la teoria
// ---------------------------------------------------------------------------

TEST_F(HnswTest, LosNivelesSiguenLaDistribucionDelPaper) {
  // P(nivel >= l) = M^-l. Con 200 000 nodos la fraccion observada tiene que
  // quedar a menos de 4 desviaciones de la esperada.
  constexpr std::size_t kN = 200'000;
  for (const std::size_t m : {2u, 4u, 16u}) {
    std::vector<std::size_t> al_menos(Hnsw::kMaxLevel + 2, 0);
    for (std::size_t id = 0; id < kN; ++id) {
      const std::size_t nivel = Hnsw::level_for(7, id, m);
      for (std::size_t l = 0; l <= nivel; ++l) ++al_menos[l];
    }
    EXPECT_EQ(al_menos[0], kN);
    for (std::size_t l = 1; l <= 3; ++l) {
      const double p = std::pow(static_cast<double>(m), -static_cast<double>(l));
      const double sigma = std::sqrt(static_cast<double>(kN) * p * (1 - p));
      EXPECT_NEAR(static_cast<double>(al_menos[l]), static_cast<double>(kN) * p, 4 * sigma)
          << "M=" << m << " nivel>=" << l;
    }
  }
}

TEST_F(HnswTest, ElNivelDependeSoloDeLaSemillaYElId) {
  EXPECT_EQ(Hnsw::level_for(1, 12345, 4), Hnsw::level_for(1, 12345, 4));
  std::size_t distintos = 0;
  for (std::size_t id = 0; id < 1000; ++id) {
    distintos += Hnsw::level_for(1, id, 2) != Hnsw::level_for(2, id, 2);
  }
  EXPECT_GT(distintos, 100U);
  for (std::size_t id = 0; id < 100'000; ++id) {
    ASSERT_LE(Hnsw::level_for(3, id, 2), Hnsw::kMaxLevel);
  }
}

TEST_F(HnswTest, ElGrafoCumpleSusInvariantesConVariosParametros) {
  struct Caso {
    std::size_t n, dim, m, ef;
    VectorMetric metrica;
  };
  const Caso casos[] = {
      {1'500, 8, 4, 32, VectorMetric::kEuclidean}, {1'500, 8, 8, 64, VectorMetric::kCosine},
      {1'000, 16, 2, 16, VectorMetric::kEuclidean}, {800, 4, 16, 100, VectorMetric::kDotProduct},
      {2'000, 2, 6, 40, VectorMetric::kEuclidean},
  };
  unsigned semilla = 1;
  for (const Caso& c : casos) {
    const auto h = construido("g" + std::to_string(semilla) + ".hnsw", c.n, c.dim,
                              {.m = c.m, .ef_construction = c.ef, .seed = semilla}, c.metrica,
                              semilla);
    ++semilla;
    EXPECT_EQ(h->size(), c.n);
    EXPECT_EQ(h->check_invariants(), "") << "M=" << c.m << " " << name_of(c.metrica);
  }
}

TEST_F(HnswTest, QuedaConectadoAunEnLosCasosQueRompenAlPaper) {
  // M = 2 con efConstruction chico y muchos vectores repetidos: sin la pasada
  // que conecta, varios nodos se quedan sin ninguna arista que llegue a ellos.
  for (unsigned semilla = 1; semilla <= 8; ++semilla) {
    auto h = Hnsw::create(dir_ / ("rep" + std::to_string(semilla) + ".hnsw"), 3,
                          VectorMetric::kEuclidean,
                          {.m = 2, .ef_construction = 4, .seed = semilla});
    auto datos = al_azar(600, 3, semilla);
    // Un tercio de los vectores repite uno de los 5 primeros.
    for (std::size_t i = 0; i < 600; i += 3) {
      for (std::size_t d = 0; d < 3; ++d) datos[i * 3 + d] = datos[(i % 5) * 3 + d];
    }
    h->build(rids_de(600), datos);
    ASSERT_EQ(h->check_invariants(), "") << "semilla " << semilla;
    // Y tambien insertando de a uno.
    for (SlotId s = 0; s < 30; ++s) {
      h->insert(RID{900, s}, std::span(datos).subspan((s % 5) * 3, 3));
    }
    ASSERT_EQ(h->check_invariants(), "") << "semilla " << semilla;
  }
}

TEST_F(HnswTest, ElGradoNuncaPasaDelMaximo) {
  // Con M = 2 el grado maximo es 4 en la capa 0 y 2 arriba, y en 2 000 nodos
  // se recorta muchas veces. Ningun nodo queda sin vecinos.
  const auto h = construido("recorte.hnsw", 2'000, 4, {.m = 2, .ef_construction = 30});
  std::size_t llenos = 0;
  for (std::size_t id = 0; id < h->size(); ++id) {
    ASSERT_LE(h->neighbors(id, 0).size(), 4U);
    ASSERT_GE(h->neighbors(id, 0).size(), 1U);
    llenos += h->neighbors(id, 0).size() == 4;
    for (std::size_t l = 1; l <= h->level(id); ++l) ASSERT_LE(h->neighbors(id, l).size(), 2U);
  }
  EXPECT_GT(llenos, 0U);
  EXPECT_EQ(h->check_invariants(), "");
}

TEST_F(HnswTest, LaHeuristicaEligeVecinosEnDireccionesDistintas) {
  // Puntos en una recta en 0, 1, ..., 299, y al final uno en 150.5. Sus dos
  // mas cercanos son 150 y 151, uno a cada lado. Cualquier otro punto esta
  // mas cerca de uno de esos dos que del nuevo, asi que la heuristica no lo
  // elige aunque M sea 8: el nodo nuevo queda con esos dos y nada mas.
  auto h = Hnsw::create(dir_ / "recta.hnsw", 2, VectorMetric::kEuclidean,
                        {.m = 8, .ef_construction = 64});
  std::vector<float> datos;
  for (std::size_t k = 0; k < 300; ++k) {
    // Un orden mezclado (7 y 300 son coprimos), para que no se inserten en fila.
    datos.push_back(static_cast<float>((k * 7) % 300));
    datos.push_back(0.0F);
  }
  h->build(rids_de(300), datos);
  const std::vector<float> medio = {150.5F, 0.0F};
  h->insert(RID{50, 0}, medio);

  std::vector<float> x;
  for (const std::uint32_t v : h->neighbors(300, 0)) x.push_back(h->vector(v)[0]);
  std::sort(x.begin(), x.end());
  EXPECT_EQ(x, (std::vector<float>{150.0F, 151.0F}));
  EXPECT_EQ(h->check_invariants(), "");
}

TEST_F(HnswTest, ConEmpateLaHeuristicaDejaEntrarAlCandidato) {
  // C esta en la mediatriz entre el nodo nuevo (0, 0) y A (1, 0): queda a la
  // misma distancia de los dos. La heuristica descarta solo si el candidato
  // esta estrictamente mas cerca de un vecino elegido, como hnswlib, asi que
  // C entra. Las dos distancias salen identicas en float: 0.5^2 = (-0.5)^2.
  auto h = Hnsw::create(dir_ / "empate.hnsw", 2, VectorMetric::kEuclidean, {.m = 8});
  h->insert(RID{1, 0}, std::vector<float>{1.0F, 0.0F});
  h->insert(RID{1, 1}, std::vector<float>{0.5F, 2.0F});
  h->insert(RID{1, 2}, std::vector<float>{0.0F, 0.0F});
  const auto v = h->neighbors(2, 0);
  EXPECT_EQ(std::vector<std::uint32_t>(v.begin(), v.end()), (std::vector<std::uint32_t>{0, 1}));
}

TEST_F(HnswTest, LasCapasSeAchicanYLaEntradaEstaArriba) {
  const auto h = construido("capas.hnsw", 4'000, 8, {.m = 4, .ef_construction = 32});
  std::vector<std::size_t> por_capa(static_cast<std::size_t>(h->max_level()) + 1, 0);
  for (std::size_t id = 0; id < h->size(); ++id) {
    for (std::size_t l = 0; l <= h->level(id); ++l) ++por_capa[l];
  }
  for (std::size_t l = 1; l < por_capa.size(); ++l) EXPECT_LT(por_capa[l], por_capa[l - 1]);
  EXPECT_EQ(static_cast<int>(h->level(h->entry_point())), h->max_level());
  // log_4(4 000) es 6: el grafo no deberia tener muchas mas capas.
  EXPECT_LE(h->max_level(), 9);
  EXPECT_GE(h->max_level(), 3);
}

TEST_F(HnswTest, InsertarCuestaComoLogNYNoComoN) {
  // Se mide cuantas distancias calcula insertar los ultimos 200 nodos en
  // grafos de 1 000 y de 8 000. Si el costo fuera lineal, el segundo costaria
  // 8 veces mas; con log N, poco mas.
  const HnswParams p{.m = 8, .ef_construction = 40};
  const std::size_t dim = 8;
  std::vector<double> por_insercion;
  for (const std::size_t n : {1'000u, 8'000u}) {
    auto h = Hnsw::create(dir_ / ("log" + std::to_string(n) + ".hnsw"), dim,
                          VectorMetric::kEuclidean, p);
    const auto datos = al_azar(n + 200, dim, 9);
    const auto rids = rids_de(n + 200);
    h->build(std::span(rids).first(n), std::span(datos).first(n * dim));
    h->reset_stats();
    h->build(std::span(rids).subspan(n), std::span(datos).subspan(n * dim));
    por_insercion.push_back(static_cast<double>(h->stats().records_examined) / 200.0);
    std::cout << "[ HNSW ] N=" << std::setw(5) << n << "  distancias por insercion="
              << std::fixed << std::setprecision(0) << por_insercion.back() << "\n";
  }
  EXPECT_LT(por_insercion[1] / por_insercion[0], 2.0);
}

// ---------------------------------------------------------------------------
// Determinismo y persistencia
// ---------------------------------------------------------------------------

TEST_F(HnswTest, ConLaMismaSemillaSaleElMismoGrafo) {
  const HnswParams p{.m = 5, .ef_construction = 20, .seed = 42};
  const auto a = construido("a.hnsw", 700, 6, p);
  const auto b = construido("b.hnsw", 700, 6, p);
  EXPECT_EQ(aristas(*a), aristas(*b));
  EXPECT_EQ(a->entry_point(), b->entry_point());
}

TEST_F(HnswTest, InsertarUnoPorUnoTambienDejaUnGrafoValido) {
  const HnswParams p{.m = 4, .ef_construction = 16};
  const auto datos = al_azar(300, 5, 3);
  const auto rids = rids_de(300);
  auto a = Hnsw::create(dir_ / "a.hnsw", 5, VectorMetric::kEuclidean, p);
  a->build(rids, datos);
  auto b = Hnsw::create(dir_ / "b.hnsw", 5, VectorMetric::kEuclidean, p);
  for (std::size_t i = 0; i < 300; ++i) b->insert(rids[i], std::span(datos).subspan(i * 5, 5));
  // Los niveles son los mismos; las aristas pueden cambiar porque la pasada
  // que conecta el grafo corre una vez en build y una vez por insercion.
  for (std::size_t i = 0; i < 300; ++i) ASSERT_EQ(a->level(i), b->level(i));
  EXPECT_EQ(a->entry_point(), b->entry_point());
  EXPECT_EQ(a->check_invariants(), "");
  EXPECT_EQ(b->check_invariants(), "");
  // build escribe cada pagina una vez; insertar uno por uno, muchas veces.
  EXPECT_EQ(a->stats().pages_written, a->page_count());
  EXPECT_GT(b->stats().pages_written, a->stats().pages_written);
}

TEST_F(HnswTest, SeReabreConElMismoGrafoYParametros) {
  const fs::path ruta = dir_ / "reabrir.hnsw";
  std::vector<std::vector<std::vector<std::uint32_t>>> antes;
  {
    auto h =
        Hnsw::create(ruta, 6, VectorMetric::kCosine, {.m = 3, .ef_construction = 12, .seed = 9});
    h->build(rids_de(1'200), al_azar(1'200, 6, 4));
    antes = aristas(*h);
    h->flush();
  }
  auto h = Hnsw::open(ruta);
  EXPECT_EQ(h->size(), 1'200U);
  EXPECT_EQ(h->dim(), 6U);
  EXPECT_EQ(h->metric(), VectorMetric::kCosine);
  EXPECT_EQ(h->m(), 3U);
  EXPECT_EQ(h->ef_construction(), 12U);
  EXPECT_EQ(h->seed(), 9U);
  EXPECT_EQ(aristas(*h), antes);
  EXPECT_EQ(h->check_invariants(), "");
  const auto datos = al_azar(1'200, 6, 4);
  for (std::size_t i = 0; i < 1'200; i += 97) {
    EXPECT_EQ(h->rid(i), rids_de(1'200)[i]);
    const auto v = h->vector(i);
    EXPECT_EQ(std::vector<float>(v.begin(), v.end()),
              std::vector<float>(datos.begin() + static_cast<std::ptrdiff_t>(i * 6),
                                 datos.begin() + static_cast<std::ptrdiff_t>(i * 6 + 6)));
  }
  EXPECT_GT(h->stats().pages_read, 0U);
}

TEST_F(HnswTest, ReabrirEInsertarDaLoMismoQueNoCerrar) {
  const HnswParams p{.m = 4, .ef_construction = 24, .seed = 5};
  const auto datos = al_azar(900, 4, 8);
  const auto rids = rids_de(900);
  // El mismo par de builds, sin cerrar entre los dos.
  auto seguido = Hnsw::create(dir_ / "seguido.hnsw", 4, VectorMetric::kEuclidean, p);
  seguido->build(std::span(rids).first(400), std::span(datos).first(400 * 4));
  seguido->build(std::span(rids).subspan(400), std::span(datos).subspan(400 * 4));

  const fs::path ruta = dir_ / "cortado.hnsw";
  {
    auto h = Hnsw::create(ruta, 4, VectorMetric::kEuclidean, p);
    h->build(std::span(rids).first(400), std::span(datos).first(400 * 4));
  }
  auto h = Hnsw::open(ruta);
  h->build(std::span(rids).subspan(400), std::span(datos).subspan(400 * 4));
  EXPECT_EQ(aristas(*h), aristas(*seguido));
  h.reset();
  EXPECT_EQ(aristas(*Hnsw::open(ruta)), aristas(*seguido));
}

TEST_F(HnswTest, InsertarDeAUnoEnlazaBienLasPaginas) {
  // Con dimension 1 000 entra un nodo por pagina, asi que cada insercion
  // agrega una pagina y la anterior tiene que reescribirse con el nuevo
  // `next` aunque el nodo nuevo no sea vecino de ella.
  const fs::path ruta = dir_ / "una_por_pagina.hnsw";
  std::vector<std::vector<std::vector<std::uint32_t>>> antes;
  {
    auto h = Hnsw::create(ruta, 1'000, VectorMetric::kEuclidean, {.m = 2, .ef_construction = 8});
    ASSERT_EQ(h->nodes_per_page(), 1U);
    const auto datos = al_azar(40, 1'000, 6);
    for (std::size_t i = 0; i < 40; ++i) {
      h->insert(RID{1, static_cast<SlotId>(i)}, std::span(datos).subspan(i * 1'000, 1'000));
    }
    antes = aristas(*h);
  }
  const auto h = Hnsw::open(ruta);
  EXPECT_EQ(aristas(*h), antes);
}

TEST_F(HnswTest, UnaInsercionEscribePocasPaginas) {
  auto h = construido("pocas.hnsw", 2'000, 8, {.m = 4, .ef_construction = 20});
  const PageId total = h->page_count();
  h->reset_stats();
  const auto v = al_azar(1, 8, 77);
  h->insert(RID{999, 0}, v);
  // El nodo nuevo y sus vecinos (a lo mas 2M en la capa 0, mas los de las
  // capas de arriba): muchas menos que todo el archivo.
  EXPECT_GE(h->stats().pages_written, 1U);
  EXPECT_LE(h->stats().pages_written, 4U * 2U + 2U);
  EXPECT_LT(h->stats().pages_written, total / 4);
  EXPECT_EQ(h->stats().pages_read, 0U);
}

TEST_F(HnswTest, ReportaTiempoYDistancias) {
  const auto h = construido("tiempo.hnsw", 1'000, 8, {.m = 6, .ef_construction = 30});
  EXPECT_GT(h->last_build_ms(), 0.0);
  EXPECT_GT(h->stats().records_examined, 1'000U);
  EXPECT_EQ(h->stats().pages_written, h->page_count());
}

// ---------------------------------------------------------------------------
// Casos borde y errores
// ---------------------------------------------------------------------------

TEST_F(HnswTest, CasosBorde) {
  auto h = Hnsw::create(dir_ / "borde.hnsw", 2, VectorMetric::kEuclidean, {.m = 2});
  EXPECT_TRUE(h->empty());
  EXPECT_EQ(h->max_level(), -1);
  EXPECT_EQ(h->check_invariants(), "");
  const std::vector<float> a = {0.0F, 0.0F};
  const std::vector<float> b = {1.0F, 0.0F};
  h->insert(RID{1, 0}, a);
  EXPECT_EQ(h->entry_point(), 0U);
  EXPECT_TRUE(h->neighbors(0, 0).empty());
  h->insert(RID{1, 1}, b);
  ASSERT_EQ(h->neighbors(0, 0).size(), 1U);
  EXPECT_EQ(h->neighbors(0, 0)[0], 1U);
  EXPECT_EQ(h->neighbors(1, 0)[0], 0U);
  // Vectores repetidos tambien quedan conectados.
  for (SlotId s = 2; s < 40; ++s) h->insert(RID{1, s}, a);
  EXPECT_EQ(h->check_invariants(), "");
  // Una capa que el nodo no tiene no tiene vecinos.
  EXPECT_TRUE(h->neighbors(0, Hnsw::kMaxLevel).empty());
  EXPECT_THROW((void)h->neighbors(40, 0), InvalidRecord);
}

TEST_F(HnswTest, ParametrosInvalidos) {
  const auto ruta = [&](const char* n) { return dir_ / n; };
  EXPECT_THROW((void)Hnsw::create(ruta("a"), 0, VectorMetric::kEuclidean), SchemaError);
  EXPECT_THROW((void)Hnsw::create(ruta("b"), 4, VectorMetric::kEuclidean, {.m = 1}),
               SchemaError);
  EXPECT_THROW((void)Hnsw::create(ruta("c"), 4, VectorMetric::kEuclidean, {.m = 1'001}),
               SchemaError);
  EXPECT_THROW((void)Hnsw::create(ruta("d"), 4, VectorMetric::kEuclidean,
                                  {.m = 4, .ef_construction = 0}),
               SchemaError);
  // dim 2 000 ocupa 8 000 bytes y no entra en 4 KB.
  EXPECT_THROW((void)Hnsw::create(ruta("e"), 2'000, VectorMetric::kEuclidean), SchemaError);
  EXPECT_NO_THROW((void)Hnsw::create(ruta("f"), 2'000, VectorMetric::kEuclidean, {}, 16'384));
}

TEST_F(HnswTest, ArchivosQueNoSonUnHnsw) {
  EXPECT_THROW((void)Hnsw::open(dir_ / "vacio.hnsw"), IoError);
  const fs::path ruta = dir_ / "existe.hnsw";
  (void)Hnsw::create(ruta, 3, VectorMetric::kEuclidean);
  EXPECT_THROW((void)Hnsw::create(ruta, 3, VectorMetric::kEuclidean), SchemaError);
  {
    DiskManager d(ruta);
    std::vector<std::byte> meta(sizeof(std::uint32_t));
    const std::uint32_t otra = Hnsw::kMetaVersion + 1;
    std::memcpy(meta.data(), &otra, sizeof otra);
    d.write_meta(meta);
  }
  EXPECT_THROW((void)Hnsw::open(ruta), IoError);
}

TEST_F(HnswTest, VectoresInvalidos) {
  auto h = Hnsw::create(dir_ / "malos.hnsw", 3, VectorMetric::kEuclidean);
  const std::vector<float> corto = {1.0F, 2.0F};
  EXPECT_THROW(h->insert(RID{1, 0}, corto), InvalidRecord);
  const std::vector<float> nan = {1.0F, std::nanf(""), 0.0F};
  EXPECT_THROW(h->insert(RID{1, 0}, nan), InvalidRecord);

  // Un build con un vector malo al final no inserta nada.
  auto datos = al_azar(50, 3, 1);
  datos[49 * 3 + 2] = std::numeric_limits<float>::infinity();
  EXPECT_THROW(h->build(rids_de(50), datos), InvalidRecord);
  EXPECT_TRUE(h->empty());
  EXPECT_EQ(h->page_count(), 0U);
  EXPECT_THROW(h->build(rids_de(50), std::span(datos).first(10)), InvalidRecord);
}

}  // namespace
}  // namespace quipudb
