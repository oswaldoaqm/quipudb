// Pruebas del borrado con redistribucion y fusion (issue #17).
//
// El criterio del issue es que borrar no degrade el arbol. Casi todas estas
// pruebas terminan en `check_invariants()`, que desde este issue tambien
// comprueba la ocupacion minima de cada nodo y que ninguna pagina quede
// perdida entre el arbol y la free list.

#include <gtest/gtest.h>

#include <algorithm>
#include <cstring>
#include <filesystem>
#include <map>
#include <numeric>
#include <random>
#include <set>
#include <string>
#include <vector>

#include "quipudb/error.hpp"
#include "quipudb/index/bplus_tree.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

std::vector<std::byte> carga(std::int32_t v) {
  std::vector<std::byte> b(4);
  std::memcpy(b.data(), &v, sizeof v);
  return b;
}
std::int32_t valor(std::span<const std::byte> b) {
  std::int32_t v = 0;
  std::memcpy(&v, b.data(), sizeof v);
  return v;
}

const Column kClaveInt{"k", DataType::Int};

class BPlusDeleteTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_bpdel_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
    path_ = dir_ / "indice.bplus";
  }
  void TearDown() override { fs::remove_all(dir_); }

  std::vector<std::int32_t> barajadas(int n, unsigned semilla) {
    std::vector<std::int32_t> cs(static_cast<std::size_t>(n));
    std::iota(cs.begin(), cs.end(), 1);
    std::shuffle(cs.begin(), cs.end(), std::mt19937{semilla});
    return cs;
  }

  fs::path dir_;
  fs::path path_;
};

// ---------------------------------------------------------------------------
// Casos chicos
// ---------------------------------------------------------------------------

TEST_F(BPlusDeleteTest, LosMinimosSalenDeLoQueDejaUnSplit) {
  // Un nodo recien partido no puede nacer ya en falta, asi que el minimo es
  // exactamente el lado chico del split. Y una fusion tiene que entrar en una
  // pagina: hoja 2*min-1 <= orden, interno 2*min <= orden.
  for (std::size_t orden = 2; orden <= 12; ++orden) {
    BPlusTree t(dir_ / ("o" + std::to_string(orden) + ".bplus"), kClaveInt, 4, 512, orden);
    const std::size_t hoja = t.min_leaf_keys();
    const std::size_t interno = t.min_internal_keys();
    EXPECT_EQ(hoja, (orden + 1) / 2) << "orden " << orden;
    EXPECT_EQ(interno, orden / 2) << "orden " << orden;
    EXPECT_GE(hoja, 1u) << "orden " << orden;
    EXPECT_GE(interno, 1u) << "orden " << orden;
    EXPECT_LE(2 * hoja - 1, orden) << "una fusion de hojas no entraria en la pagina";
    EXPECT_LE(2 * interno, orden) << "una fusion de internos no entraria en la pagina";
  }
}

TEST_F(BPlusDeleteTest, BorrarLaUnicaClaveDejaElArbolVacio) {
  BPlusTree t(path_, kClaveInt, 4, 512, 4);
  t.insert(Value{7}, carga(7));
  EXPECT_TRUE(t.erase(Value{7}));
  EXPECT_EQ(t.size(), 0u);
  EXPECT_EQ(t.height(), 0u);
  EXPECT_EQ(t.root(), kInvalidPage);
  EXPECT_FALSE(t.find(Value{7}).has_value());
  EXPECT_TRUE(t.scan().empty());
  EXPECT_EQ(t.check_invariants(), "");

  // Y se puede volver a usar: la pagina que quedo libre se reusa.
  EXPECT_EQ(t.free_pages(), 1u);
  const auto paginas = t.page_count();
  t.insert(Value{8}, carga(8));
  EXPECT_EQ(t.page_count(), paginas) << "el archivo no crece: reusa la pagina liberada";
  EXPECT_EQ(t.free_pages(), 0u);
  EXPECT_EQ(t.check_invariants(), "");
}

TEST_F(BPlusDeleteTest, BorrarLoQueNoEstaNoCambiaNada) {
  BPlusTree t(path_, kClaveInt, 4, 512, 4);
  for (std::int32_t i = 1; i <= 50; ++i) t.insert(Value{i}, carga(i));
  const auto altura = t.height();
  const auto paginas = t.page_count();

  EXPECT_FALSE(t.erase(Value{999}));
  EXPECT_FALSE(t.erase(Value{0}));
  EXPECT_FALSE(t.erase(Value{-1}));
  EXPECT_EQ(t.size(), 50u);
  EXPECT_EQ(t.height(), altura);
  EXPECT_EQ(t.page_count(), paginas);
  EXPECT_EQ(t.free_pages(), 0u);
  EXPECT_EQ(t.check_invariants(), "");
}

TEST_F(BPlusDeleteTest, HayPrestamosYHayFusiones) {
  // No se puede fijar de antemano cual toca en cada borrado sin clavar la
  // forma exacta del arbol, pero si que en una corrida larga pasen las dos
  // cosas: si nunca se fusionara, el rebalanceo no estaria haciendo nada.
  BPlusTree t(path_, kClaveInt, 4, 512, 4);
  for (std::int32_t i = 1; i <= 200; ++i) t.insert(Value{i}, carga(i));

  std::size_t prestamos = 0;
  std::size_t fusiones = 0;
  std::size_t libres = t.free_pages();
  const auto paginas = t.page_count();

  for (const auto c : barajadas(200, 7)) {
    ASSERT_TRUE(t.erase(Value{c})) << c;
    const std::size_t ahora = t.free_pages();
    if (ahora > libres) {
      ++fusiones;
    } else {
      ++prestamos;  // no libero paginas: o presto, o el nodo no estaba en falta
    }
    libres = ahora;
    ASSERT_EQ(t.check_invariants(), "") << "despues de borrar " << c;
  }
  EXPECT_GT(fusiones, 0u) << "nunca se fusiono: el arbol no se estaria compactando";
  EXPECT_GT(prestamos, 0u) << "todos los borrados fusionaron: no se intento prestar";
  EXPECT_EQ(t.size(), 0u);
  EXPECT_EQ(t.page_count(), paginas) << "borrar no agranda el archivo";
  EXPECT_EQ(t.free_pages(), paginas) << "vacio el arbol, todas las paginas estan libres";
}

TEST_F(BPlusDeleteTest, LaAlturaBajaAMedidaQueSeVacia) {
  BPlusTree t(path_, kClaveInt, 4, 512, 3);
  for (const auto c : barajadas(2000, 13)) t.insert(Value{c}, carga(c));
  const auto altura_llena = t.height();
  ASSERT_GE(altura_llena, 6u) << "con orden 3 el arbol tiene que quedar alto";

  for (std::int32_t c = 1; c <= 1990; ++c) ASSERT_TRUE(t.erase(Value{c})) << c;
  EXPECT_EQ(t.size(), 10u);
  EXPECT_LT(t.height(), altura_llena) << "10 claves no pueden seguir a la misma profundidad";
  EXPECT_EQ(t.check_invariants(), "");

  for (std::int32_t c = 1991; c <= 2000; ++c) ASSERT_TRUE(t.erase(Value{c})) << c;
  EXPECT_EQ(t.height(), 0u);
  EXPECT_EQ(t.root(), kInvalidPage);
  EXPECT_EQ(t.check_invariants(), "");
}

// ---------------------------------------------------------------------------
// El criterio del issue
// ---------------------------------------------------------------------------

TEST_F(BPlusDeleteTest, DiezMilInsertadasCincoMilBorradasAlAzar) {
  BPlusTree t(path_, kClaveInt, 4, 512, 4);
  for (const auto c : barajadas(10000, 3)) t.insert(Value{c}, carga(c));
  ASSERT_EQ(t.size(), 10000u);
  ASSERT_EQ(t.check_invariants(), "");
  const auto altura_llena = t.height();
  const auto paginas_llenas = t.page_count();

  auto orden_de_borrado = barajadas(10000, 91);
  orden_de_borrado.resize(5000);
  std::set<std::int32_t> quedan;
  for (std::int32_t c = 1; c <= 10000; ++c) quedan.insert(c);
  for (const auto c : orden_de_borrado) {
    ASSERT_TRUE(t.erase(Value{c})) << c;
    quedan.erase(c);
  }

  EXPECT_EQ(t.size(), 5000u);
  EXPECT_EQ(t.check_invariants(), "") << "el arbol sigue valido despues de 5000 borrados";
  EXPECT_LE(t.height(), altura_llena);
  EXPECT_EQ(t.page_count(), paginas_llenas) << "borrar no agranda el archivo";
  EXPECT_GT(t.free_pages(), 0u) << "fusionar tiene que haber liberado paginas";

  // Contenido: exactamente los que quedan, en orden y con su payload.
  const auto todo = t.scan();
  ASSERT_EQ(todo.size(), quedan.size());
  auto it = quedan.begin();
  for (const auto& [k, p] : todo) {
    EXPECT_EQ(std::get<std::int32_t>(k), *it);
    EXPECT_EQ(valor(p), *it);
    ++it;
  }
  // Y las busquedas puntuales coinciden con el conjunto.
  for (std::int32_t c = 1; c <= 10000; c += 7) {
    EXPECT_EQ(t.find(Value{c}).has_value(), quedan.count(c) == 1) << c;
  }
}

TEST_F(BPlusDeleteTest, ElArchivoNoCreceAlBorrarYVolverAInsertar) {
  BPlusTree t(path_, kClaveInt, 4, 512, 4);
  for (std::int32_t c = 1; c <= 3000; ++c) t.insert(Value{c}, carga(c));
  const auto paginas = t.page_count();

  for (int vuelta = 0; vuelta < 3; ++vuelta) {
    for (std::int32_t c = 1; c <= 3000; ++c) ASSERT_TRUE(t.erase(Value{c})) << c;
    ASSERT_EQ(t.size(), 0u);
    for (std::int32_t c = 1; c <= 3000; ++c) t.insert(Value{c}, carga(c));
    ASSERT_EQ(t.size(), 3000u);
    ASSERT_EQ(t.check_invariants(), "") << "vuelta " << vuelta;
    // Sin free list, cada vuelta agregaria un arbol entero de paginas nuevas.
    EXPECT_LE(t.page_count(), paginas) << "vuelta " << vuelta;
  }
}

TEST_F(BPlusDeleteTest, SobreviveAlCerrarYReabrirConPaginasLibres) {
  PageId paginas = 0;
  std::size_t libres = 0;
  {
    BPlusTree t(path_, kClaveInt, 4, 512, 4);
    for (const auto c : barajadas(1000, 21)) t.insert(Value{c}, carga(c));
    for (std::int32_t c = 1; c <= 1000; c += 2) ASSERT_TRUE(t.erase(Value{c})) << c;
    paginas = t.page_count();
    libres = t.free_pages();
    ASSERT_GT(libres, 0u);
    t.flush();
  }
  BPlusTree t(path_, kClaveInt, 4, 512, 4);
  EXPECT_EQ(t.size(), 500u);
  EXPECT_EQ(t.page_count(), paginas);
  EXPECT_EQ(t.free_pages(), libres) << "la free list se guarda en el area meta";
  EXPECT_EQ(t.check_invariants(), "");
  // Y las paginas libres se siguen reusando despues de reabrir.
  for (std::int32_t c = 1; c <= 1000; c += 2) t.insert(Value{c}, carga(c));
  EXPECT_EQ(t.size(), 1000u);
  EXPECT_LE(t.page_count(), paginas);
  EXPECT_EQ(t.check_invariants(), "");
}

// ---------------------------------------------------------------------------
// Claves repetidas
// ---------------------------------------------------------------------------

TEST_F(BPlusDeleteTest, BorrarClavesRepetidasTambienRebalancea) {
  BPlusTree t(path_, kClaveInt, 4, 512, 4, /*unique=*/false);
  // 40 claves distintas, 25 copias de cada una: corridas de iguales que
  // abarcan varias hojas, que es donde el descenso se complica.
  for (std::int32_t c = 1; c <= 40; ++c) {
    for (std::int32_t rep = 0; rep < 25; ++rep) t.insert(Value{c}, carga(c * 100 + rep));
  }
  ASSERT_EQ(t.size(), 1000u);
  ASSERT_EQ(t.check_invariants(), "");

  // erase_one quita un puntero concreto y deja los demas.
  EXPECT_TRUE(t.erase_one(Value{20}, carga(20 * 100 + 7)));
  EXPECT_FALSE(t.erase_one(Value{20}, carga(20 * 100 + 7))) << "ya no esta";
  EXPECT_EQ(t.size(), 999u);
  EXPECT_EQ(t.check_invariants(), "");

  // erase_all se lleva la corrida entera.
  EXPECT_EQ(t.erase_all(Value{20}), 24u);
  EXPECT_EQ(t.size(), 975u);
  EXPECT_EQ(t.range(Value{20}, Value{20}).size(), 0u);
  EXPECT_EQ(t.check_invariants(), "");

  for (std::int32_t c = 1; c <= 40; ++c) {
    if (c == 20) continue;
    ASSERT_EQ(t.erase_all(Value{c}), 25u) << c;
    ASSERT_EQ(t.check_invariants(), "") << "despues de borrar la corrida " << c;
  }
  EXPECT_EQ(t.size(), 0u);
  EXPECT_EQ(t.height(), 0u);
}

// ---------------------------------------------------------------------------
// Comparacion diferencial
// ---------------------------------------------------------------------------

TEST_F(BPlusDeleteTest, DiferencialContraStdMap) {
  // Mezcla insertar, borrar y consultar contra un std::map que hace de
  // oraculo. Varios ordenes, porque el minimo cambia con la paridad.
  for (const std::size_t orden : {3u, 4u, 5u, 8u}) {
    for (const unsigned semilla : {1u, 2u, 3u}) {
      const auto archivo = dir_ / ("d" + std::to_string(orden) + "_" +
                                   std::to_string(semilla) + ".bplus");
      fs::remove(archivo);
      BPlusTree t(archivo, kClaveInt, 4, 512, orden);
      std::map<std::int32_t, std::int32_t> oraculo;
      std::mt19937 rng{semilla * 977 + static_cast<unsigned>(orden)};
      std::uniform_int_distribution<std::int32_t> clave{1, 120};
      std::uniform_int_distribution<int> dado{0, 9};

      for (int paso = 0; paso < 1200; ++paso) {
        const std::int32_t k = clave(rng);
        const int op = dado(rng);
        if (op < 5) {
          const std::int32_t v = paso;
          if (oraculo.count(k) == 1) {
            EXPECT_THROW(t.insert(Value{k}, carga(v)), DuplicateKey) << k;
          } else {
            t.insert(Value{k}, carga(v));
            oraculo[k] = v;
          }
        } else if (op < 9) {
          EXPECT_EQ(t.erase(Value{k}), oraculo.erase(k) == 1) << k;
        } else {
          const auto encontrado = t.find(Value{k});
          const auto esperado = oraculo.find(k);
          ASSERT_EQ(encontrado.has_value(), esperado != oraculo.end()) << k;
          // Con llaves: EXPECT_EQ expande a un if/else y sin ellas el `else`
          // interno queda ambiguo (-Wdangling-else).
          if (encontrado) {
            EXPECT_EQ(valor(*encontrado), esperado->second) << k;
          }
        }
        ASSERT_EQ(t.size(), oraculo.size()) << "orden " << orden << " paso " << paso;
        if (paso % 25 == 0) {
          ASSERT_EQ(t.check_invariants(), "") << "orden " << orden << " paso " << paso;
        }
      }

      ASSERT_EQ(t.check_invariants(), "") << "orden " << orden;
      const auto todo = t.scan();
      ASSERT_EQ(todo.size(), oraculo.size()) << "orden " << orden;
      auto it = oraculo.begin();
      for (const auto& [k, p] : todo) {
        EXPECT_EQ(std::get<std::int32_t>(k), it->first);
        EXPECT_EQ(valor(p), it->second);
        ++it;
      }
      // Rangos, que es lo que recorre la cadena de hojas.
      for (std::int32_t lo = 1; lo <= 120; lo += 17) {
        const std::int32_t hi = lo + 30;
        const auto obtenido = t.range(Value{lo}, Value{hi});
        std::size_t esperados = 0;
        for (auto i = oraculo.lower_bound(lo); i != oraculo.end() && i->first <= hi; ++i) {
          ++esperados;
        }
        EXPECT_EQ(obtenido.size(), esperados) << "orden " << orden << " rango " << lo;
      }
    }
  }
}

TEST_F(BPlusDeleteTest, DiferencialConRepetidasContraStdMultimap) {
  for (const std::size_t orden : {3u, 4u, 6u}) {
    for (const unsigned semilla : {1u, 2u}) {
      const auto archivo = dir_ / ("m" + std::to_string(orden) + "_" +
                                   std::to_string(semilla) + ".bplus");
      fs::remove(archivo);
      BPlusTree t(archivo, kClaveInt, 4, 512, orden, /*unique=*/false);
      std::multimap<std::int32_t, std::int32_t> oraculo;
      std::mt19937 rng{semilla * 613 + static_cast<unsigned>(orden)};
      // Pocas claves distintas a proposito: corridas largas de iguales.
      std::uniform_int_distribution<std::int32_t> clave{1, 6};
      std::uniform_int_distribution<int> dado{0, 9};
      std::int32_t siguiente_payload = 0;

      for (int paso = 0; paso < 900; ++paso) {
        const std::int32_t k = clave(rng);
        const int op = dado(rng);
        if (op < 5) {
          const std::int32_t v = ++siguiente_payload;
          t.insert(Value{k}, carga(v));
          oraculo.emplace(k, v);
        } else if (op < 7) {
          // Borrar un puntero concreto: el primero que el oraculo tenga.
          const auto i = oraculo.find(k);
          if (i == oraculo.end()) {
            EXPECT_FALSE(t.erase_one(Value{k}, carga(-1))) << k;
          } else {
            EXPECT_TRUE(t.erase_one(Value{k}, carga(i->second))) << k;
            oraculo.erase(i);
          }
        } else if (op < 9) {
          EXPECT_EQ(t.erase_all(Value{k}), oraculo.erase(k)) << k;
        } else {
          EXPECT_EQ(t.range(Value{k}, Value{k}).size(), oraculo.count(k)) << k;
        }
        ASSERT_EQ(t.size(), oraculo.size()) << "orden " << orden << " paso " << paso;
        if (paso % 20 == 0) {
          ASSERT_EQ(t.check_invariants(), "") << "orden " << orden << " paso " << paso;
        }
      }

      ASSERT_EQ(t.check_invariants(), "") << "orden " << orden;
      const auto todo = t.scan();
      ASSERT_EQ(todo.size(), oraculo.size());
      // Mismas claves, en el mismo orden; los payload de una corrida pueden
      // venir en cualquier orden, asi que se comparan como conjunto.
      auto it = oraculo.begin();
      for (const auto& [k, p] : todo) {
        EXPECT_EQ(std::get<std::int32_t>(k), it->first);
        ++it;
      }
      for (std::int32_t k = 1; k <= 6; ++k) {
        std::multiset<std::int32_t> del_arbol;
        for (const auto& [clave_i, p] : t.range(Value{k}, Value{k})) {
          static_cast<void>(clave_i);
          del_arbol.insert(valor(p));
        }
        std::multiset<std::int32_t> del_oraculo;
        for (auto i = oraculo.lower_bound(k); i != oraculo.upper_bound(k); ++i) {
          del_oraculo.insert(i->second);
        }
        EXPECT_EQ(del_arbol, del_oraculo) << "clave " << k << ", orden " << orden;
      }
    }
  }
}

}  // namespace
}  // namespace quipudb
