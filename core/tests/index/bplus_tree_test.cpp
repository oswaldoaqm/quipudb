// Pruebas del B+ Tree (issue #14).

#include <gtest/gtest.h>

#include <algorithm>
#include <cstring>
#include <filesystem>
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

/// Payload de juguete: un entero de 4 bytes, para no arrastrar registros.
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

class BPlusTreeTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_bplus_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
    path_ = dir_ / "indice.bplus";
  }
  void TearDown() override { fs::remove_all(dir_); }

  fs::path dir_;
  fs::path path_;
};

TEST_F(BPlusTreeTest, EmpiezaVacio) {
  BPlusTree t(path_, kClaveInt, 4, 512);
  EXPECT_EQ(t.size(), 0u);
  EXPECT_EQ(t.height(), 0u);
  EXPECT_EQ(t.root(), kInvalidPage);
  EXPECT_TRUE(t.scan().empty());
  EXPECT_FALSE(t.find(Value{1}).has_value());
  EXPECT_TRUE(t.range(Value{1}, Value{100}).empty());
  EXPECT_EQ(t.check_invariants(), "");
}

TEST_F(BPlusTreeTest, ElOrdenEsConfigurableYSeValida) {
  // Por omision, el maximo que entra en la pagina.
  BPlusTree porDefecto(path_, kClaveInt, 4, 512);
  EXPECT_GT(porDefecto.order(), 10u);

  // A mano, para forzar arboles altos con pocas claves.
  {
    BPlusTree chico(dir_ / "chico.bplus", kClaveInt, 4, 512, 3);
    EXPECT_EQ(chico.order(), 3u);
    chico.insert(Value{1}, carga(1));
  }  // se cierra antes de reabrirlo: lo escrito sigue en el buffer hasta que
     // el archivo se vacia, y dos objetos sobre el mismo archivo no se ven

  EXPECT_THROW(BPlusTree(dir_ / "a.bplus", kClaveInt, 4, 512, 1), SchemaError)
      << "con orden 1 un split no termina";
  EXPECT_THROW(BPlusTree(dir_ / "b.bplus", kClaveInt, 4, 512, 10000), SchemaError)
      << "no entra en la pagina";
  // Reabrir con otro orden es un error, no una conversion silenciosa.
  EXPECT_THROW(BPlusTree(dir_ / "chico.bplus", kClaveInt, 4, 512, 5), SchemaError);
}

TEST_F(BPlusTreeTest, UnaHojaSolaAntesDelPrimerSplit) {
  BPlusTree t(path_, kClaveInt, 4, 512, 4);
  for (std::int32_t i = 1; i <= 4; ++i) t.insert(Value{i}, carga(i * 10));
  EXPECT_EQ(t.height(), 1u) << "la raiz sigue siendo una hoja";
  EXPECT_EQ(t.size(), 4u);
  EXPECT_EQ(t.root(), t.first_leaf());
  EXPECT_EQ(t.check_invariants(), "");
  for (std::int32_t i = 1; i <= 4; ++i) {
    const auto p = t.find(Value{i});
    ASSERT_TRUE(p.has_value()) << i;
    EXPECT_EQ(valor(*p), i * 10);
  }
}

TEST_F(BPlusTreeTest, ElSplitDeLaRaizHaceCrecerElArbol) {
  BPlusTree t(path_, kClaveInt, 4, 512, 4);
  for (std::int32_t i = 1; i <= 4; ++i) t.insert(Value{i}, carga(i));
  ASSERT_EQ(t.height(), 1u);
  t.insert(Value{5}, carga(5));  // se pasa del orden: parte
  EXPECT_EQ(t.height(), 2u) << "nacio una raiz interna";
  EXPECT_NE(t.root(), t.first_leaf());
  EXPECT_EQ(t.size(), 5u);
  EXPECT_EQ(t.check_invariants(), "");
}

TEST_F(BPlusTreeTest, DiezMilClavesQuedanBalanceadasYOrdenadas) {
  // La prueba que pide el issue.
  BPlusTree t(path_, kClaveInt, 4, 4096);
  std::vector<std::int32_t> cs(10000);
  std::iota(cs.begin(), cs.end(), 1);
  std::shuffle(cs.begin(), cs.end(), std::mt19937{20260906});
  for (const auto c : cs) t.insert(Value{c}, carga(c * 2));

  EXPECT_EQ(t.size(), 10000u);
  EXPECT_EQ(t.check_invariants(), "") << "balanceado, ordenado y con la cadena de hojas completa";
  EXPECT_GE(t.height(), 2u);

  const auto todo = t.scan();
  ASSERT_EQ(todo.size(), 10000u);
  for (std::size_t i = 0; i < todo.size(); ++i) {
    EXPECT_EQ(std::get<std::int32_t>(todo[i].first), static_cast<std::int32_t>(i) + 1) << i;
    EXPECT_EQ(valor(todo[i].second), (static_cast<std::int32_t>(i) + 1) * 2) << i;
  }
  // Y cada clave se encuentra por el arbol.
  for (std::int32_t c = 1; c <= 10000; c += 137) {
    const auto p = t.find(Value{c});
    ASSERT_TRUE(p.has_value()) << c;
    EXPECT_EQ(valor(*p), c * 2) << c;
  }
  EXPECT_FALSE(t.find(Value{10001}).has_value());
  EXPECT_FALSE(t.find(Value{0}).has_value());
}

TEST_F(BPlusTreeTest, ConOrdenChicoElArbolSeHaceAltoYSigueCorrecto) {
  // Orden 3 y 2 000 claves: muchos niveles y muchos splits internos, que es
  // donde se rompe un B+ mal implementado.
  BPlusTree t(path_, kClaveInt, 4, 512, 3);
  std::vector<std::int32_t> cs(2000);
  std::iota(cs.begin(), cs.end(), 1);
  std::shuffle(cs.begin(), cs.end(), std::mt19937{7});
  for (const auto c : cs) {
    t.insert(Value{c}, carga(c));
    ASSERT_EQ(t.check_invariants(), "") << "se rompio al insertar " << c;
  }
  EXPECT_EQ(t.size(), 2000u);
  EXPECT_GE(t.height(), 6u) << "con orden 3 el arbol tiene que ser alto";
  const auto todo = t.scan();
  ASSERT_EQ(todo.size(), 2000u);
  EXPECT_TRUE(std::is_sorted(todo.begin(), todo.end(), [](const auto& a, const auto& b) {
    return compare(a.first, b.first) < 0;
  }));
}

TEST_F(BPlusTreeTest, InsercionAscendenteYDescendente) {
  // Los dos peores casos para el reparto de un B+.
  {
    BPlusTree t(path_, kClaveInt, 4, 512, 4);
    for (std::int32_t i = 1; i <= 500; ++i) t.insert(Value{i}, carga(i));
    EXPECT_EQ(t.check_invariants(), "");
    EXPECT_EQ(t.scan().size(), 500u);
  }
  BPlusTree t(dir_ / "desc.bplus", kClaveInt, 4, 512, 4);
  for (std::int32_t i = 500; i >= 1; --i) t.insert(Value{i}, carga(i));
  EXPECT_EQ(t.check_invariants(), "");
  const auto todo = t.scan();
  ASSERT_EQ(todo.size(), 500u);
  EXPECT_EQ(std::get<std::int32_t>(todo.front().first), 1);
  EXPECT_EQ(std::get<std::int32_t>(todo.back().first), 500);
}

TEST_F(BPlusTreeTest, LasHojasEstanEncadenadasEnOrden) {
  BPlusTree t(path_, kClaveInt, 4, 512, 4);
  for (std::int32_t i = 1; i <= 300; ++i) t.insert(Value{i}, carga(i));

  // Recorrer la cadena no vuelve a bajar por el arbol: se lee cada hoja una
  // sola vez y ninguna pagina interna.
  t.reset_stats();
  const auto todo = t.scan();
  ASSERT_EQ(todo.size(), 300u);
  EXPECT_LT(t.stats().pages_read, t.page_count())
      << "el scan lee las hojas, no el arbol entero";
  EXPECT_GT(t.stats().pages_read, 0u);
}

TEST_F(BPlusTreeTest, ClaveDuplicadaLanzaYNoDejaRastro) {
  BPlusTree t(path_, kClaveInt, 4, 512, 4);
  for (std::int32_t i = 1; i <= 50; ++i) t.insert(Value{i}, carga(i));
  EXPECT_THROW(t.insert(Value{25}, carga(999)), DuplicateKey);
  EXPECT_EQ(t.size(), 50u);
  EXPECT_EQ(valor(*t.find(Value{25})), 25) << "el payload no se toco";
  EXPECT_EQ(t.check_invariants(), "");
}

TEST_F(BPlusTreeTest, RechazaClaveDeOtroTipoYPayloadDeOtroTamano) {
  BPlusTree t(path_, kClaveInt, 4, 512);
  EXPECT_THROW(t.insert(Value{1.5}, carga(1)), SchemaError);
  std::vector<std::byte> corto(3);
  EXPECT_THROW(t.insert(Value{1}, corto), SchemaError);
  EXPECT_EQ(t.size(), 0u);
}

TEST_F(BPlusTreeTest, RangoInclusivoYRecorriendoLaCadena) {
  BPlusTree t(path_, kClaveInt, 4, 512, 4);
  for (std::int32_t i = 1; i <= 500; ++i) t.insert(Value{i}, carga(i));

  const auto r = t.range(Value{100}, Value{200});
  ASSERT_EQ(r.size(), 101u) << "inclusivo en ambos extremos";
  EXPECT_EQ(std::get<std::int32_t>(r.front().first), 100);
  EXPECT_EQ(std::get<std::int32_t>(r.back().first), 200);
  EXPECT_EQ(t.range(Value{1}, Value{500}).size(), 500u);
  EXPECT_EQ(t.range(Value{250}, Value{250}).size(), 1u);
  EXPECT_TRUE(t.range(Value{600}, Value{700}).empty());
  EXPECT_TRUE(t.range(Value{200}, Value{100}).empty()) << "rango invertido";
  EXPECT_EQ(t.range(Value{-50}, Value{3}).size(), 3u) << "empieza antes de la primera clave";
}

TEST_F(BPlusTreeTest, SetPayloadCambiaElValorSinTocarLaEstructura) {
  BPlusTree t(path_, kClaveInt, 4, 512, 4);
  for (std::int32_t i = 1; i <= 100; ++i) t.insert(Value{i}, carga(i));
  const auto altura = t.height();

  EXPECT_TRUE(t.set_payload(Value{42}, carga(4242)));
  EXPECT_EQ(valor(*t.find(Value{42})), 4242);
  EXPECT_FALSE(t.set_payload(Value{999}, carga(0)));
  EXPECT_EQ(t.size(), 100u);
  EXPECT_EQ(t.height(), altura);
  EXPECT_EQ(t.check_invariants(), "");
}

TEST_F(BPlusTreeTest, ElArbolViveEnDiscoYSobreviveAlReabrir) {
  {
    BPlusTree t(path_, kClaveInt, 4, 512, 4);
    for (std::int32_t i = 1; i <= 1000; ++i) t.insert(Value{i}, carga(i * 3));
    t.flush();
  }
  BPlusTree t(path_, kClaveInt, 4, 512, 4);
  EXPECT_EQ(t.size(), 1000u);
  EXPECT_GT(t.height(), 1u);
  EXPECT_EQ(t.check_invariants(), "") << "la estructura se leyo del disco tal cual";
  EXPECT_EQ(valor(*t.find(Value{777})), 777 * 3);
  EXPECT_EQ(t.scan().size(), 1000u);
  // Y sigue creciendo bien.
  t.insert(Value{1001}, carga(3003));
  EXPECT_EQ(t.check_invariants(), "");
  EXPECT_EQ(t.size(), 1001u);
}

TEST_F(BPlusTreeTest, RechazaAbrirConOtraClaveOOtroPayload) {
  {
    BPlusTree t(path_, kClaveInt, 4, 512, 4);
    t.insert(Value{1}, carga(1));
  }
  EXPECT_THROW(BPlusTree(path_, Column{"k", DataType::Double}, 4, 512, 4), SchemaError);
  EXPECT_THROW(BPlusTree(path_, kClaveInt, 8, 512, 4), SchemaError);
  EXPECT_THROW(BPlusTree(path_, kClaveInt, 4, 1024, 4), IoError) << "otro tamano de pagina";
}

TEST_F(BPlusTreeTest, ClavesDeTextoYDePayloadGrande) {
  const Column texto{"codigo", DataType::Varchar, 8};
  BPlusTree t(path_, texto, 32, 512, 4);
  const std::vector<std::string> cs{"CS2032", "BD2", "MA1101", "AI501", "ZZ999", "FI203", "QU100"};
  std::vector<std::byte> p(32);
  for (const auto& c : cs) {
    std::memcpy(p.data(), c.data(), c.size());
    t.insert(Value{c}, p);
  }
  EXPECT_EQ(t.size(), cs.size());
  EXPECT_EQ(t.check_invariants(), "");
  const auto todo = t.scan();
  ASSERT_EQ(todo.size(), cs.size());
  EXPECT_EQ(std::get<std::string>(todo.front().first), "AI501");
  EXPECT_EQ(std::get<std::string>(todo.back().first), "ZZ999");
  EXPECT_TRUE(t.find(Value{std::string("BD2")}).has_value());
  EXPECT_FALSE(t.find(Value{std::string("NOEXISTE")}).has_value());
  // Orden lexicografico: "D" < "FI203", asi que el rango trae AI501, BD2 y
  // CS2032.
  EXPECT_EQ(t.range(Value{std::string("A")}, Value{std::string("D")}).size(), 3u);
}

TEST_F(BPlusTreeTest, LaBusquedaBajaUnaVezPorNivel) {
  BPlusTree t(path_, kClaveInt, 4, 512, 4);
  for (std::int32_t i = 1; i <= 2000; ++i) t.insert(Value{i}, carga(i));
  const auto altura = t.height();
  ASSERT_GE(altura, 4u);

  t.reset_stats();
  ASSERT_TRUE(t.find(Value{1}).has_value());
  EXPECT_EQ(t.stats().pages_read, altura) << "una pagina por nivel, la primera clave";
  t.reset_stats();
  ASSERT_TRUE(t.find(Value{2000}).has_value());
  EXPECT_EQ(t.stats().pages_read, altura) << "y lo mismo la ultima";
}

}  // namespace
}  // namespace quipudb
