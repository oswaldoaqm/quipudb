// Pruebas del GROUP BY externo: hash con re-particionado y sort (issue #21).

#include <gtest/gtest.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <map>
#include <numeric>
#include <random>
#include <string>
#include <vector>

#include "quipudb/error.hpp"
#include "quipudb/external/external_group.hpp"
#include "quipudb/storage/heap_file.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

Schema ventas() {
  return Schema{
      .table_name = "ventas",
      .columns = {{"id", DataType::Int},
                  {"region", DataType::Varchar, 10},
                  {"monto", DataType::Double},
                  {"unidades", DataType::Int}},
      .key_column = 0,
  };
}

constexpr std::size_t kId = 0;
constexpr std::size_t kRegion = 1;
constexpr std::size_t kMonto = 2;
constexpr std::size_t kUnidades = 3;

const std::vector<std::string> kRegiones{"lima", "cusco", "piura", "iquitos", "tacna"};

Record venta(std::int32_t id) {
  return {id, kRegiones[static_cast<std::size_t>(id) % kRegiones.size()],
          static_cast<double>((id * 37) % 1000) / 4.0, (id % 7) + 1};
}

std::vector<Record> muestra(int n, unsigned semilla) {
  std::vector<std::int32_t> ids(static_cast<std::size_t>(n));
  std::iota(ids.begin(), ids.end(), 1);
  std::shuffle(ids.begin(), ids.end(), std::mt19937{semilla});
  std::vector<Record> rs;
  rs.reserve(ids.size());
  for (const auto id : ids) rs.push_back(venta(id));
  return rs;
}

std::vector<Record> drenar(RecordSource& s) {
  std::vector<Record> out;
  Record r;
  while (s.next(r)) out.push_back(r);
  return out;
}

/// Oraculo: agrega en memoria con un map, que es lo que el issue pide comparar.
struct Esperado {
  std::int32_t count = 0;
  double suma = 0.0;
  double minimo = 0.0;
  double maximo = 0.0;
};

std::map<std::string, Esperado> agregar_en_memoria(const std::vector<Record>& rs) {
  std::map<std::string, Esperado> m;
  for (const auto& r : rs) {
    const auto& k = std::get<std::string>(r[kRegion]);
    const double v = std::get<double>(r[kMonto]);
    auto& e = m[k];
    if (e.count == 0) {
      e.minimo = v;
      e.maximo = v;
    }
    ++e.count;
    e.suma += v;
    e.minimo = std::min(e.minimo, v);
    e.maximo = std::max(e.maximo, v);
  }
  return m;
}

class GroupTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_group_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
  }
  void TearDown() override { fs::remove_all(dir_); }

  /// Las cinco agregaciones sobre `monto`, mas COUNT.
  static std::vector<AggregateSpec> todas() {
    return {AggregateSpec::count(),
            AggregateSpec::of(Aggregate::Sum, kMonto),
            AggregateSpec::of(Aggregate::Min, kMonto),
            AggregateSpec::of(Aggregate::Max, kMonto),
            AggregateSpec::of(Aggregate::Avg, kMonto)};
  }

  fs::path dir_;
};

// ---------------------------------------------------------------------------
// El criterio del issue: contra una agregacion en memoria
// ---------------------------------------------------------------------------

TEST_F(GroupTest, CienMilRegistrosCoincidenConLaAgregacionEnMemoria) {
  const auto entrada = muestra(100000, 3);
  const auto esperado = agregar_en_memoria(entrada);

  for (const auto estrategia :
       {ExternalGroupBy::Strategy::kHash, ExternalGroupBy::Strategy::kSort}) {
    ExternalGroupBy g(ventas(), kRegion, todas(), estrategia, 8, 512, dir_);
    auto fuente = source_of(entrada);
    const auto salida = drenar(*g.grouped(*fuente));

    ASSERT_EQ(salida.size(), esperado.size()) << "estrategia " << static_cast<int>(estrategia);
    EXPECT_EQ(g.rows(), 100000u);
    EXPECT_EQ(g.groups(), esperado.size());

    for (const auto& fila : salida) {
      const auto& region = std::get<std::string>(fila[0]);
      const auto it = esperado.find(region);
      ASSERT_NE(it, esperado.end()) << "grupo inventado: " << region;
      EXPECT_EQ(std::get<std::int32_t>(fila[1]), it->second.count) << region;
      EXPECT_NEAR(std::get<double>(fila[2]), it->second.suma, 1e-6) << region;
      EXPECT_DOUBLE_EQ(std::get<double>(fila[3]), it->second.minimo) << region;
      EXPECT_DOUBLE_EQ(std::get<double>(fila[4]), it->second.maximo) << region;
      EXPECT_NEAR(std::get<double>(fila[5]), it->second.suma / it->second.count, 1e-9) << region;
    }
  }
}

TEST_F(GroupTest, LosDosCaminosDanExactamenteLoMismo) {
  // Es lo que hace comparable la medicion del 2.1.6: si dieran resultados
  // distintos, comparar sus tiempos no significaria nada.
  const auto entrada = muestra(20000, 5);

  auto recoger = [&](ExternalGroupBy::Strategy s) {
    ExternalGroupBy g(ventas(), kRegion, todas(), s, 6, 256, dir_);
    auto f = source_of(entrada);
    auto rs = drenar(*g.grouped(*f));
    std::sort(rs.begin(), rs.end(), [](const Record& a, const Record& b) {
      return std::get<std::string>(a[0]) < std::get<std::string>(b[0]);
    });
    return rs;
  };

  const auto porHash = recoger(ExternalGroupBy::Strategy::kHash);
  const auto porSort = recoger(ExternalGroupBy::Strategy::kSort);

  ASSERT_EQ(porHash.size(), porSort.size());
  for (std::size_t i = 0; i < porHash.size(); ++i) {
    EXPECT_EQ(std::get<std::string>(porHash[i][0]), std::get<std::string>(porSort[i][0]));
    EXPECT_EQ(std::get<std::int32_t>(porHash[i][1]), std::get<std::int32_t>(porSort[i][1]));
    EXPECT_NEAR(std::get<double>(porHash[i][2]), std::get<double>(porSort[i][2]), 1e-9);
    EXPECT_DOUBLE_EQ(std::get<double>(porHash[i][3]), std::get<double>(porSort[i][3]));
    EXPECT_DOUBLE_EQ(std::get<double>(porHash[i][4]), std::get<double>(porSort[i][4]));
    EXPECT_NEAR(std::get<double>(porHash[i][5]), std::get<double>(porSort[i][5]), 1e-9);
  }
}

// ---------------------------------------------------------------------------
// El esquema de salida
// ---------------------------------------------------------------------------

TEST_F(GroupTest, ElEsquemaDeSalidaNoEsElDeLaEntrada) {
  // Un GROUP BY produce filas con otra forma. Armarlo aqui evita que el
  // planner (2.1.3) lo invente.
  ExternalGroupBy g(ventas(), kRegion,
                    {AggregateSpec::count("cuantas"),
                     AggregateSpec::of(Aggregate::Avg, kMonto, "promedio")},
                    ExternalGroupBy::Strategy::kSort, 8, 512, dir_);

  const Schema& s = g.output_schema();
  ASSERT_EQ(s.columns.size(), 3u);
  EXPECT_EQ(s.columns[0].name, "region");
  EXPECT_EQ(s.columns[0].type, DataType::Varchar);
  EXPECT_EQ(s.columns[1].name, "cuantas");
  EXPECT_EQ(s.columns[1].type, DataType::Int);
  EXPECT_EQ(s.columns[2].name, "promedio");
  EXPECT_EQ(s.columns[2].type, DataType::Double);

  // Y las filas que salen calzan con el.
  const auto entrada = muestra(500, 7);
  auto f = source_of(entrada);
  for (const auto& fila : drenar(*g.grouped(*f))) {
    EXPECT_NO_THROW(s.validate(fila));
  }
}

TEST_F(GroupTest, LosNombresSeGeneranSiNoSeDan) {
  ExternalGroupBy g(ventas(), kRegion,
                    {AggregateSpec::count(), AggregateSpec::of(Aggregate::Sum, kMonto)},
                    ExternalGroupBy::Strategy::kSort, 8, 512, dir_);
  EXPECT_EQ(g.output_schema().columns[1].name, "COUNT_all");
  EXPECT_EQ(g.output_schema().columns[2].name, "SUM_monto");
}

TEST_F(GroupTest, SumDeEnterosSubeADoubleParaNoDesbordar) {
  // Un SUM de int32 puede pasarse; subir a DOUBLE evita tener que elegir entre
  // truncar y lanzar a media agregacion.
  ExternalGroupBy g(ventas(), kRegion, {AggregateSpec::of(Aggregate::Sum, kUnidades)},
                    ExternalGroupBy::Strategy::kSort, 8, 512, dir_);
  EXPECT_EQ(g.output_schema().columns[1].type, DataType::Double);

  const auto entrada = muestra(1000, 11);
  double total = 0;
  for (const auto& r : entrada) total += std::get<std::int32_t>(r[kUnidades]);

  auto f = source_of(entrada);
  double suma = 0;
  for (const auto& fila : drenar(*g.grouped(*f))) suma += std::get<double>(fila[1]);
  EXPECT_DOUBLE_EQ(suma, total);
}

// ---------------------------------------------------------------------------
// El AVG y la precision
// ---------------------------------------------------------------------------

TEST_F(GroupTest, LaSumaEsCompensadaYNoAcumulaErrorDeRedondeo) {
  // Sumar un valor grande con muchos chiquitos es donde la suma ingenua
  // pierde: cada chiquito se traga en el redondeo. El 2.1.6 compara estos
  // resultados contra PostgreSQL, que suma compensado.
  Schema s{.table_name = "medidas",
           .columns = {{"id", DataType::Int}, {"g", DataType::Varchar, 4}, {"v", DataType::Double}},
           .key_column = 0};

  std::vector<Record> rs;
  rs.push_back({std::int32_t{0}, std::string{"a"}, 1e16});
  for (std::int32_t i = 1; i <= 10000; ++i) {
    rs.push_back({i, std::string{"a"}, 1.0});
  }

  ExternalGroupBy g(s, 1, {AggregateSpec::of(Aggregate::Sum, 2)},
                    ExternalGroupBy::Strategy::kSort, 8, 512, dir_);
  auto f = source_of(rs);
  const auto salida = drenar(*g.grouped(*f));
  ASSERT_EQ(salida.size(), 1u);

  // Suma ingenua: los 10 000 unos desaparecen dentro del 1e16.
  double ingenua = 0.0;
  for (const auto& r : rs) ingenua += std::get<double>(r[2]);

  const double compensada = std::get<double>(salida[0][1]);
  EXPECT_DOUBLE_EQ(compensada, 1e16 + 10000.0);
  EXPECT_NE(compensada, ingenua) << "la suma compensada dio lo mismo que la ingenua: "
                                    "esta prueba no esta probando nada";
}

// ---------------------------------------------------------------------------
// Re-particionado y el caso que el hash no resuelve
// ---------------------------------------------------------------------------

TEST_F(GroupTest, UnaParticionConDemasiadosGruposSeReParticiona) {
  // Lo que tiene que caber en memoria son los GRUPOS, no las filas: un grupo
  // ocupa un acumulador, no sus filas. Asi que para forzar el re-particionado
  // hacen falta muchas claves DISTINTAS, no muchas filas.
  //
  // Se agrupa por `id`, que es unico. Los buffers dan un tope de unos cientos
  // de grupos por cubeta y hay 8 000, asi que la primera vuelta desborda y hay
  // que repartir; con 15 particiones el reparto converge y no hace falta caer
  // a sort. Con muy pocas particiones NO convergeria, y eso tambien es
  // correcto: es lo que cubre `SiNiReParticionarAlcanzaElHashCaeASort`.
  const auto entrada = muestra(8000, 13);
  ExternalGroupBy g(ventas(), kId, {AggregateSpec::count()}, ExternalGroupBy::Strategy::kAuto,
                    16, 512, dir_);
  auto f = source_of(entrada);
  const auto salida = drenar(*g.grouped(*f));

  EXPECT_EQ(salida.size(), 8000u);
  EXPECT_EQ(g.rows(), 8000u);
  EXPECT_GT(g.repartitions(), 0u) << "el caso que esta prueba cubre no llego a darse";
  EXPECT_FALSE(g.fell_back()) << "re-particionar deberia haber bastado con 15 particiones";
  for (const auto& fila : salida) EXPECT_EQ(std::get<std::int32_t>(fila[1]), 1);
}

TEST_F(GroupTest, PocosGruposConMuchasFilasNoDesbordanNada) {
  // El caso contrario, y el que la primera version rechazaba mal: 30 000 filas
  // repartidas en 5 grupos caben de sobra, porque son 5 acumuladores.
  const auto entrada = muestra(30000, 13);
  ExternalGroupBy g(ventas(), kRegion, todas(), ExternalGroupBy::Strategy::kHash,
                    ExternalSort::kMinBuffers, 128, dir_);
  auto f = source_of(entrada);
  const auto salida = drenar(*g.grouped(*f));

  EXPECT_EQ(salida.size(), kRegiones.size());
  EXPECT_EQ(g.repartitions(), 0u) << "re-particiono sin necesidad";
  EXPECT_FALSE(g.fell_back());
}

TEST_F(GroupTest, SiNiReParticionarAlcanzaElHashCaeASort) {
  // Muchos grupos distintos y un tope de un solo acumulador: ninguna semilla
  // deja una particion con un grupo solo, asi que el hash se rinde. Ordenar si
  // funciona, porque no depende de que el hash separe nada.
  //
  // Es artificial a proposito -- un tope de 1 grupo no pasa en la practica --,
  // pero es la unica forma de ejercitar el fallback sin datos patologicos.
  std::vector<Record> rs;
  for (std::int32_t i = 1; i <= 4000; ++i) {
    rs.push_back({i, "g" + std::to_string(i), static_cast<double>(i % 100), 1});
  }

  // page_size al minimo y registros grandes: cabe 1 acumulador por vuelta.
  Schema s{.table_name = "grande",
           .columns = {{"id", DataType::Int}, {"g", DataType::Varchar, 30},
                       {"v", DataType::Double}, {"u", DataType::Int}},
           .key_column = 0};
  ExternalGroupBy g(s, 1, {AggregateSpec::count()}, ExternalGroupBy::Strategy::kAuto,
                    ExternalSort::kMinBuffers, 128, dir_);
  auto f = source_of(rs);
  const auto salida = drenar(*g.grouped(*f));

  EXPECT_EQ(salida.size(), 4000u);
  EXPECT_EQ(g.rows(), 4000u);
  // Antes esto era `if (g.fell_back()) { ... }`, y por lo tanto pasaba igual si
  // el fallback dejaba de dispararse: una prueba que no prueba. Se exige.
  EXPECT_TRUE(g.fell_back()) << "el caso que esta prueba cubre no llego a darse";
  EXPECT_EQ(g.used(), ExternalGroupBy::Strategy::kSort);
  // Y el fallback tiene que releer de las particiones de la primera vuelta, no
  // de una copia en memoria: si no se escribio nada, no habia de donde releer.
  EXPECT_GT(g.stats().pages_written, 0u);
}

TEST_F(GroupTest, TodasLasClavesIgualesEsUnSoloGrupoYCabeDeSobra) {
  // Un grupo es un acumulador: 20 000 filas de una sola clave no desbordan
  // nada. Era lo que la primera version rechazaba, culpando ademas a "las
  // claves son todas iguales" -- que es cierto, pero no es un problema.
  std::vector<Record> rs;
  for (std::int32_t i = 1; i <= 20000; ++i) {
    rs.push_back({i, std::string{"unica"}, static_cast<double>(i % 100), 1});
  }

  ExternalGroupBy g(ventas(), kRegion, todas(), ExternalGroupBy::Strategy::kHash,
                    ExternalSort::kMinBuffers, 256, dir_);
  auto f = source_of(rs);
  const auto salida = drenar(*g.grouped(*f));

  ASSERT_EQ(salida.size(), 1u);
  EXPECT_EQ(std::get<std::string>(salida[0][0]), "unica");
  EXPECT_EQ(std::get<std::int32_t>(salida[0][1]), 20000);
  EXPECT_FALSE(g.fell_back());
}

TEST_F(GroupTest, ConEstrategiaHashPuraSeLanzaEnVezDeCaerASort) {
  // `kHash` existe para medir el hash puro en los benchmarks: ahi caer a sort
  // falsearia la medicion. Si el hash no alcanza, lanza.
  //
  // Se fuerza el desborde con muchos grupos y el tope al minimo; si en esta
  // maquina el hash SI alcanza, la prueba no aplica y se salta en vez de
  // fingir que probo algo.
  std::vector<Record> rs;
  for (std::int32_t i = 1; i <= 4000; ++i) {
    rs.push_back({i, "g" + std::to_string(i), 1.0, 1});
  }
  Schema s{.table_name = "grande",
           .columns = {{"id", DataType::Int}, {"g", DataType::Varchar, 30},
                       {"v", DataType::Double}, {"u", DataType::Int}},
           .key_column = 0};

  ExternalGroupBy auto_(s, 1, {AggregateSpec::count()}, ExternalGroupBy::Strategy::kAuto,
                        ExternalSort::kMinBuffers, 128, dir_);
  {
    auto f = source_of(rs);
    static_cast<void>(drenar(*auto_.grouped(*f)));
  }
  if (!auto_.fell_back()) {
    GTEST_SKIP() << "el hash alcanzo con estos datos: no hay caso que probar";
  }

  ExternalGroupBy g(s, 1, {AggregateSpec::count()}, ExternalGroupBy::Strategy::kHash,
                    ExternalSort::kMinBuffers, 128, dir_);
  auto f = source_of(rs);
  EXPECT_THROW(static_cast<void>(g.grouped(*f)), Unsupported);
}

// ---------------------------------------------------------------------------
// El orden de la salida
// ---------------------------------------------------------------------------

TEST_F(GroupTest, PorSortLaSalidaSaleOrdenadaPorLaClave) {
  // Es la ventaja del camino por sort: una consulta con GROUP BY y ORDER BY
  // por la misma columna paga un solo ordenamiento.
  const auto entrada = muestra(5000, 17);
  ExternalGroupBy g(ventas(), kRegion, {AggregateSpec::count()},
                    ExternalGroupBy::Strategy::kSort, 4, 256, dir_);
  auto f = source_of(entrada);
  const auto salida = drenar(*g.grouped(*f));

  ASSERT_GT(salida.size(), 1u);
  for (std::size_t i = 1; i < salida.size(); ++i) {
    EXPECT_LT(std::get<std::string>(salida[i - 1][0]), std::get<std::string>(salida[i][0]));
  }
  EXPECT_EQ(g.used(), ExternalGroupBy::Strategy::kSort);
}

// ---------------------------------------------------------------------------
// Casos borde
// ---------------------------------------------------------------------------

TEST_F(GroupTest, UnaEntradaVaciaDaUnaSalidaVacia) {
  for (const auto s : {ExternalGroupBy::Strategy::kHash, ExternalGroupBy::Strategy::kSort,
                       ExternalGroupBy::Strategy::kAuto}) {
    ExternalGroupBy g(ventas(), kRegion, todas(), s, 8, 512, dir_);
    auto f = source_of(std::vector<Record>{});
    EXPECT_TRUE(drenar(*g.grouped(*f)).empty());
    EXPECT_EQ(g.groups(), 0u);
    EXPECT_EQ(g.rows(), 0u);
  }
}

TEST_F(GroupTest, SinAgregacionesEsUnSelectDistinct) {
  const auto entrada = muestra(2000, 19);
  ExternalGroupBy g(ventas(), kRegion, {}, ExternalGroupBy::Strategy::kSort, 8, 512, dir_);
  const auto salida = [&] {
    auto f = source_of(entrada);
    return drenar(*g.grouped(*f));
  }();
  EXPECT_EQ(salida.size(), kRegiones.size());
  for (const auto& fila : salida) EXPECT_EQ(fila.size(), 1u);
}

TEST_F(GroupTest, UnGrupoPorFilaTambienFunciona) {
  // El extremo contrario: agrupar por la clave primaria, que es unica.
  const auto entrada = muestra(3000, 23);
  ExternalGroupBy g(ventas(), kId, {AggregateSpec::count()}, ExternalGroupBy::Strategy::kAuto,
                    4, 256, dir_);
  auto f = source_of(entrada);
  const auto salida = drenar(*g.grouped(*f));
  EXPECT_EQ(salida.size(), 3000u);
  for (const auto& fila : salida) EXPECT_EQ(std::get<std::int32_t>(fila[1]), 1);
}

TEST_F(GroupTest, AgrupaLaSalidaDeUnScanDeTabla) {
  HeapFile datos(dir_ / "ventas.heap", ventas(), 512);
  const auto entrada = muestra(4000, 29);
  for (const auto& r : entrada) datos.insert(r);

  ExternalGroupBy g(ventas(), kRegion, todas(), ExternalGroupBy::Strategy::kAuto, 8, 512, dir_);
  auto f = source_of(datos);
  const auto salida = drenar(*g.grouped(*f));

  EXPECT_EQ(salida.size(), kRegiones.size());
  const auto esperado = agregar_en_memoria(entrada);
  for (const auto& fila : salida) {
    const auto it = esperado.find(std::get<std::string>(fila[0]));
    ASSERT_NE(it, esperado.end());
    EXPECT_EQ(std::get<std::int32_t>(fila[1]), it->second.count);
  }
}

TEST_F(GroupTest, NoDejaArchivosTemporales) {
  const auto entrada = muestra(20000, 31);
  {
    ExternalGroupBy g(ventas(), kRegion, todas(), ExternalGroupBy::Strategy::kSort,
                      ExternalSort::kMinBuffers, 256, dir_);
    auto f = source_of(entrada);
    EXPECT_FALSE(drenar(*g.grouped(*f)).empty());
  }
  std::size_t runs = 0;
  for (const auto& e : fs::directory_iterator(dir_)) {
    if (e.path().extension() == ".run") ++runs;
  }
  EXPECT_EQ(runs, 0u);
}

// ---------------------------------------------------------------------------
// El hash tiene que tocar el disco
// ---------------------------------------------------------------------------

TEST_F(GroupTest, ElHashEscribeLasParticionesADisco) {
  // LA prueba de este arreglo. La primera version del #21 particionaba EN
  // MEMORIA: `cubetas` era un vector<vector<Record>> y no se escribia nada.
  // Con 100 000 filas y 8 buffers reportaba pages_read=0, pages_written=0 y
  // cero temporales, o sea que el "External" del nombre era falso y un paso
  // `group` del plan (ADR 0002) habria reportado cero paginas al 2.1.6.
  //
  // Si esta prueba vuelve a ver ceros, es que alguien deshizo el arreglo.
  std::vector<Record> rs;
  rs.reserve(100000);
  for (std::int32_t i = 0; i < 100000; ++i) {
    rs.push_back({i, "g" + std::to_string(i % 40000), static_cast<double>(i % 997), 1});
  }
  Schema s{.table_name = "muchas",
           .columns = {{"id", DataType::Int},
                       {"g", DataType::Varchar, 12},
                       {"v", DataType::Double},
                       {"u", DataType::Int}},
           .key_column = 0};

  ExternalGroupBy g(s, 1, {AggregateSpec::count()}, ExternalGroupBy::Strategy::kHash, 8,
                    kDefaultPageSize, dir_);
  auto f = source_of(rs);
  const auto salida = drenar(*g.grouped(*f));

  EXPECT_EQ(salida.size(), 40000u);
  EXPECT_GT(g.stats().pages_written, 0u) << "el hash no escribio ninguna particion a disco";
  EXPECT_GT(g.stats().pages_read, 0u) << "el hash no releyo ninguna particion";
  EXPECT_GT(g.temp_bytes(), 0u) << "no hay ningun archivo de particion vivo";
  EXPECT_FALSE(g.fell_back()) << "esto no deberia necesitar el fallback a sort";
}

TEST_F(GroupTest, LasParticionesDelHashSeBorranAlDestruirElGroupBy) {
  {
    std::vector<Record> rs;
    for (std::int32_t i = 0; i < 20000; ++i) {
      rs.push_back({i, "g" + std::to_string(i % 9000), static_cast<double>(i % 97), 1});
    }
    Schema s{.table_name = "muchas",
             .columns = {{"id", DataType::Int},
                         {"g", DataType::Varchar, 12},
                         {"v", DataType::Double},
                         {"u", DataType::Int}},
             .key_column = 0};
    ExternalGroupBy g(s, 1, {AggregateSpec::count()}, ExternalGroupBy::Strategy::kHash, 8,
                      kDefaultPageSize, dir_);
    auto f = source_of(rs);
    EXPECT_FALSE(drenar(*g.grouped(*f)).empty());
    EXPECT_GT(g.temp_bytes(), 0u) << "no llego a escribir particiones, no prueba nada";
  }
  std::size_t partes = 0;
  for (const auto& e : fs::directory_iterator(dir_)) {
    if (e.path().extension() == ".part") ++partes;
  }
  EXPECT_EQ(partes, 0u);
}

TEST_F(GroupTest, ElHashNoDejaParticionesNiSiquieraSiFalla) {
  // Una excepcion a media primera vuelta no puede dejar p archivos regados.
  // Los temporales se registran ANTES de escribir en ellos justamente por esto.
  class FuenteQueLanza final : public RecordSource {
   public:
    bool next(Record& out) override {
      if (++n_ > 500) throw IoError("fuente rota a proposito");
      out = venta(static_cast<std::int32_t>(n_));
      return true;
    }

   private:
    std::size_t n_ = 0;
  };

  {
    FuenteQueLanza rota;
    ExternalGroupBy g(ventas(), kRegion, todas(), ExternalGroupBy::Strategy::kHash, 8, 256,
                      dir_);
    EXPECT_THROW(static_cast<void>(g.grouped(rota)), IoError);
  }
  std::size_t partes = 0;
  for (const auto& e : fs::directory_iterator(dir_)) {
    if (e.path().extension() == ".part") ++partes;
  }
  EXPECT_EQ(partes, 0u);
}

// ---------------------------------------------------------------------------
// Validaciones
// ---------------------------------------------------------------------------

TEST_F(GroupTest, RechazaSumOAvgSobreUnaColumnaQueNoEsNumerica) {
  for (const auto f : {Aggregate::Sum, Aggregate::Avg}) {
    EXPECT_THROW(ExternalGroupBy(ventas(), kId, {AggregateSpec::of(f, kRegion)},
                                 ExternalGroupBy::Strategy::kAuto, 8, 512, dir_),
                 SchemaError)
        << to_string(f);
  }
  // MIN y MAX si valen sobre texto: hay orden.
  EXPECT_NO_THROW(ExternalGroupBy(ventas(), kId, {AggregateSpec::of(Aggregate::Min, kRegion)},
                                  ExternalGroupBy::Strategy::kAuto, 8, 512, dir_));
}

TEST_F(GroupTest, RechazaColumnasQueNoExistenYPocosBuffers) {
  EXPECT_THROW(ExternalGroupBy(ventas(), 99, {}, ExternalGroupBy::Strategy::kAuto, 8, 512, dir_),
               SchemaError);
  EXPECT_THROW(ExternalGroupBy(ventas(), kId, {AggregateSpec::of(Aggregate::Sum, 99)},
                               ExternalGroupBy::Strategy::kAuto, 8, 512, dir_),
               SchemaError);
  EXPECT_THROW(ExternalGroupBy(ventas(), kId, {}, ExternalGroupBy::Strategy::kAuto, 2, 512, dir_),
               SchemaError);
}

TEST_F(GroupTest, RechazaUnRegistroQueNoCalzaConElEsquema) {
  ExternalGroupBy g(ventas(), kRegion, todas(), ExternalGroupBy::Strategy::kHash, 8, 512, dir_);
  auto f = source_of(std::vector<Record>{{std::int32_t{1}, std::string{"lima"}}});
  EXPECT_THROW(static_cast<void>(g.grouped(*f)), InvalidRecord);
}

}  // namespace
}  // namespace quipudb
