// Pruebas del External Sorting: k-way merge para ORDER BY (issue #20).

#include <gtest/gtest.h>

#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <numeric>
#include <random>
#include <string>
#include <vector>

#include "quipudb/error.hpp"
#include "quipudb/external/external_sort.hpp"
#include "quipudb/storage/heap_file.hpp"

namespace quipudb {
namespace {

namespace fs = std::filesystem;

/// Se ordena por `nombre` y por `promedio`, que NO son la clave primaria: un
/// ORDER BY ordena por cualquier columna.
Schema alumnos() {
  return Schema{
      .table_name = "alumnos",
      .columns = {{"codigo", DataType::Int},
                  {"nombre", DataType::Varchar, 12},
                  {"promedio", DataType::Double}},
      .key_column = 0,
  };
}

constexpr std::size_t kCodigo = 0;
constexpr std::size_t kNombre = 1;
constexpr std::size_t kPromedio = 2;

/// El nombre NO sigue el orden del codigo: si coincidieran, ordenar por nombre
/// daria el mismo resultado que no ordenar y la prueba no probaria nada.
std::string nombre_de(std::int32_t c) {
  return "a" + std::to_string((c * 7919) % 100000);
}

Record alumno(std::int32_t c) {
  return {c, nombre_de(c), static_cast<double>((c * 31) % 1000) / 10.0};
}

std::vector<Record> muestra(int n, unsigned semilla) {
  std::vector<std::int32_t> cs(static_cast<std::size_t>(n));
  std::iota(cs.begin(), cs.end(), 1);
  std::shuffle(cs.begin(), cs.end(), std::mt19937{semilla});
  std::vector<Record> rs;
  rs.reserve(cs.size());
  for (const auto c : cs) rs.push_back(alumno(c));
  return rs;
}

std::vector<Record> drenar(RecordSource& s) {
  std::vector<Record> out;
  Record r;
  while (s.next(r)) out.push_back(r);
  return out;
}

/// Comprueba que sale ordenado por esa columna y que no se perdio ni se
/// invento nada: compara el multiconjunto de codigos contra el de la entrada.
void verificar(const std::vector<Record>& salida, const std::vector<Record>& entrada,
               std::size_t columna) {
  ASSERT_EQ(salida.size(), entrada.size()) << "se perdieron o duplicaron registros";
  for (std::size_t i = 1; i < salida.size(); ++i) {
    ASSERT_LE(compare(salida[i - 1][columna], salida[i][columna]), 0)
        << "desordenado en la posicion " << i;
  }
  std::vector<std::int32_t> a, b;
  for (const auto& r : salida) a.push_back(std::get<std::int32_t>(r[kCodigo]));
  for (const auto& r : entrada) b.push_back(std::get<std::int32_t>(r[kCodigo]));
  std::sort(a.begin(), a.end());
  std::sort(b.begin(), b.end());
  EXPECT_EQ(a, b) << "la salida no tiene exactamente los mismos registros";
}

class SortTest : public ::testing::Test {
 protected:
  void SetUp() override {
    const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
    dir_ = fs::temp_directory_path() / ("quipudb_sort_" + std::string(info->name()));
    fs::remove_all(dir_);
    fs::create_directories(dir_);
  }
  void TearDown() override { fs::remove_all(dir_); }

  /// Archivos que quedaron en el directorio de temporales.
  [[nodiscard]] std::size_t temporales() const {
    std::size_t n = 0;
    for (const auto& e : fs::directory_iterator(dir_)) {
      if (e.path().extension() == ".run") ++n;
    }
    return n;
  }

  fs::path dir_;
};

// ---------------------------------------------------------------------------
// Lo que cabe en memoria no toca el disco
// ---------------------------------------------------------------------------

TEST_F(SortTest, SiTodoCabeEnMemoriaNoSeUsaElDisco) {
  // Es el caso comun en tablas chicas, y `passes() == 0` es lo que lo
  // distingue en el plan de ejecucion.
  const auto entrada = muestra(50, 1);
  ExternalSort sort(alumnos(), kNombre, 16, 512, dir_);
  auto fuente = source_of(entrada);
  const auto salida = drenar(*sort.sorted(*fuente));

  verificar(salida, entrada, kNombre);
  EXPECT_EQ(sort.passes(), 0u) << "hubo fusion cuando todo cabia en memoria";
  EXPECT_EQ(sort.stats().pages_written, 0u) << "escribio en disco sin necesidad";
  EXPECT_EQ(sort.stats().pages_read, 0u);
  EXPECT_EQ(temporales(), 0u);
}

TEST_F(SortTest, UnaEntradaVaciaDaUnaSalidaVacia) {
  ExternalSort sort(alumnos(), kNombre, 8, 512, dir_);
  auto fuente = source_of(std::vector<Record>{});
  EXPECT_TRUE(drenar(*sort.sorted(*fuente)).empty());
  EXPECT_EQ(sort.size(), 0u);
  EXPECT_EQ(sort.passes(), 0u);
}

// ---------------------------------------------------------------------------
// El caso que da nombre al issue
// ---------------------------------------------------------------------------

TEST_F(SortTest, OrdenaMasDeLoQueCabeEnMemoriaYHaceVariasPasadas) {
  // Buffers al minimo para forzar muchos runs y mas de una pasada con pocos
  // datos: es la unica forma de ejercitar la fase 2 sin cargar 100 000
  // registros en cada prueba.
  const auto entrada = muestra(2000, 3);
  ExternalSort sort(alumnos(), kNombre, ExternalSort::kMinBuffers, 256, dir_);
  auto fuente = source_of(entrada);
  const auto salida = drenar(*sort.sorted(*fuente));

  verificar(salida, entrada, kNombre);
  EXPECT_EQ(sort.size(), 2000u);
  EXPECT_GT(sort.runs(), 1u) << "la fase 1 no partio la entrada en runs";
  EXPECT_GT(sort.passes(), 1u) << "no llego a hacer mas de una pasada de fusion";
  EXPECT_GT(sort.stats().pages_written, 0u);
}

TEST_F(SortTest, CienMilRegistrosConMemoriaArtificialmenteBaja) {
  // El criterio literal del issue.
  const auto entrada = muestra(100000, 5);
  ExternalSort sort(alumnos(), kNombre, 8, 1024, dir_);
  auto fuente = source_of(entrada);
  const auto salida = drenar(*sort.sorted(*fuente));

  verificar(salida, entrada, kNombre);
  EXPECT_EQ(sort.size(), 100000u);
  EXPECT_GT(sort.runs(), 1u);
  EXPECT_GE(sort.passes(), 1u);
}

TEST_F(SortTest, OrdenaTambienPorUnaColumnaDoubleYPorLaClave) {
  const auto entrada = muestra(1500, 7);
  for (const std::size_t col : {kPromedio, kCodigo}) {
    ExternalSort sort(alumnos(), col, ExternalSort::kMinBuffers, 256, dir_);
    auto fuente = source_of(entrada);
    const auto salida = drenar(*sort.sorted(*fuente));
    verificar(salida, entrada, col);
    EXPECT_GT(sort.passes(), 0u) << "columna " << col;
  }
}

TEST_F(SortTest, LasClavesRepetidasSalenTodasYJuntas) {
  // `promedio` toma solo 100 valores distintos en 3 000 registros.
  const auto entrada = muestra(3000, 11);
  ExternalSort sort(alumnos(), kPromedio, 4, 256, dir_);
  auto fuente = source_of(entrada);
  const auto salida = drenar(*sort.sorted(*fuente));

  verificar(salida, entrada, kPromedio);
  // Que esten juntas es lo que un GROUP BY (#21) necesita: si el sort las
  // dejara desperdigadas, agrupar exigiria otra pasada.
  std::vector<double> vistos;
  for (std::size_t i = 0; i < salida.size(); ++i) {
    const double p = std::get<double>(salida[i][kPromedio]);
    if (i == 0 || p != std::get<double>(salida[i - 1][kPromedio])) {
      EXPECT_EQ(std::find(vistos.begin(), vistos.end(), p), vistos.end())
          << "el valor " << p << " aparece en dos corridas distintas";
      vistos.push_back(p);
    }
  }
}

// ---------------------------------------------------------------------------
// Se puede encadenar detras de una tabla
// ---------------------------------------------------------------------------

TEST_F(SortTest, OrdenaLaSalidaDeUnScanDeTabla) {
  // Es el plan que dibuja el ADR 0002: `sort` con un hijo `scan`.
  HeapFile datos(dir_ / "alumnos.heap", alumnos(), 512);
  const auto entrada = muestra(3000, 13);
  for (const auto& r : entrada) datos.insert(r);

  ExternalSort sort(alumnos(), kNombre, 4, 512, dir_);
  auto fuente = source_of(datos);
  const auto salida = drenar(*sort.sorted(*fuente));

  verificar(salida, entrada, kNombre);
  EXPECT_GT(sort.passes(), 0u);
}

// ---------------------------------------------------------------------------
// Memoria y disco acotados
// ---------------------------------------------------------------------------

TEST_F(SortTest, NoDejaArchivosTemporalesAlTerminar) {
  const auto entrada = muestra(4000, 17);
  {
    ExternalSort sort(alumnos(), kNombre, ExternalSort::kMinBuffers, 256, dir_);
    auto fuente = source_of(entrada);
    auto salida = sort.sorted(*fuente);
    EXPECT_GT(temporales(), 0u) << "deberia haber temporales mientras se lee la salida";
    EXPECT_GT(sort.temp_bytes(), 0u);
    const auto rs = drenar(*salida);
    verificar(rs, entrada, kNombre);
  }
  EXPECT_EQ(temporales(), 0u) << "quedaron temporales despues de destruir el sort";
}

/// Fuente que lanza a la mitad, para comprobar que los temporales se limpian
/// igual.
class FuenteQueFalla final : public RecordSource {
 public:
  FuenteQueFalla(std::vector<Record> rs, std::size_t hasta)
      : rs_(std::move(rs)), hasta_(hasta) {}
  bool next(Record& out) override {
    if (i_ == hasta_) throw IoError("fallo simulado a media lectura");
    if (i_ >= rs_.size()) return false;
    out = rs_[i_++];
    return true;
  }

 private:
  std::vector<Record> rs_;
  std::size_t hasta_;
  std::size_t i_ = 0;
};

TEST_F(SortTest, NoDejaArchivosTemporalesNiSiquieraSiFalla) {
  // Borrarlos al terminar no basta: si algo lanza a media pasada, quedarian
  // regados. Por eso cada temporal se borra en su destructor.
  const auto entrada = muestra(4000, 19);
  {
    ExternalSort sort(alumnos(), kNombre, ExternalSort::kMinBuffers, 256, dir_);
    FuenteQueFalla fuente(entrada, 2500);
    EXPECT_THROW(static_cast<void>(sort.sorted(fuente)), IoError);
  }
  EXPECT_EQ(temporales(), 0u) << "una excepcion a media pasada dejo temporales regados";
}

TEST_F(SortTest, ElEspacioEnDiscoNoCreceConCadaPasada) {
  // Los runs de una pasada se sueltan en cuanto la siguiente los consumio, asi
  // que el disco queda acotado a dos pasadas y no a todas.
  const auto entrada = muestra(20000, 23);
  ExternalSort sort(alumnos(), kNombre, ExternalSort::kMinBuffers, 256, dir_);
  auto fuente = source_of(entrada);
  auto salida = sort.sorted(*fuente);

  ASSERT_GT(sort.passes(), 2u) << "hicieron falta mas pasadas para que la prueba valga";
  // Al terminar solo debe quedar el run final: un archivo.
  EXPECT_EQ(temporales(), 1u) << "quedaron runs de pasadas anteriores sin soltar";
  verificar(drenar(*salida), entrada, kNombre);
}

// ---------------------------------------------------------------------------
// Lo que el 2.1.6 mide
// ---------------------------------------------------------------------------

TEST_F(SortTest, LasPaginasMedidasSeParecenALaFormula) {
  // La formula es 2N(1 + ceil(log_{B-1}(N/B))). Si la medicion se alejara
  // mucho, o el algoritmo no es el que dice ser o la formula del informe esta
  // mal escrita. Se compara con holgura: la formula cuenta paginas de datos y
  // el ultimo run puede quedar a media carga.
  const auto entrada = muestra(20000, 29);
  ExternalSort sort(alumnos(), kNombre, 8, 512, dir_);
  auto fuente = source_of(entrada);
  const auto salida = drenar(*sort.sorted(*fuente));
  verificar(salida, entrada, kNombre);

  const auto n = static_cast<std::uint64_t>(
      (entrada.size() + sort.records_per_page() - 1) / sort.records_per_page());
  const auto predicho = sort.predicted_pages(n);
  const auto medido = sort.stats().pages_read + sort.stats().pages_written;

  ASSERT_GT(predicho, 0u);
  EXPECT_GE(medido, predicho / 2) << "medido " << medido << ", predicho " << predicho;
  EXPECT_LE(medido, predicho * 2) << "medido " << medido << ", predicho " << predicho;
}

TEST_F(SortTest, MasBuffersSonMenosPasadasYMenosPaginas) {
  // Es la comparacion que el informe tiene que mostrar: el costo baja
  // logaritmicamente con la memoria disponible.
  const auto entrada = muestra(20000, 31);

  std::size_t pasadas_pocos = 0, pasadas_muchos = 0;
  std::uint64_t paginas_pocos = 0, paginas_muchos = 0;
  {
    ExternalSort sort(alumnos(), kNombre, ExternalSort::kMinBuffers, 512, dir_);
    auto f = source_of(entrada);
    const auto s = drenar(*sort.sorted(*f));
    verificar(s, entrada, kNombre);
    pasadas_pocos = sort.passes();
    paginas_pocos = sort.stats().pages_read + sort.stats().pages_written;
  }
  {
    ExternalSort sort(alumnos(), kNombre, 64, 512, dir_);
    auto f = source_of(entrada);
    const auto s = drenar(*sort.sorted(*f));
    verificar(s, entrada, kNombre);
    pasadas_muchos = sort.passes();
    paginas_muchos = sort.stats().pages_read + sort.stats().pages_written;
  }
  EXPECT_LT(pasadas_muchos, pasadas_pocos)
      << "con 64 buffers hizo " << pasadas_muchos << " pasadas y con "
      << ExternalSort::kMinBuffers << " hizo " << pasadas_pocos;
  EXPECT_LT(paginas_muchos, paginas_pocos);
}

// ---------------------------------------------------------------------------
// Validaciones
// ---------------------------------------------------------------------------

TEST_F(SortTest, RechazaMenosBuffersDeLosQueUnMergeNecesita) {
  // Con menos de 3 no hay k-way merge: uno para la salida y al menos dos
  // frentes que fusionar.
  for (const std::size_t b : {std::size_t{0}, std::size_t{1}, std::size_t{2}}) {
    EXPECT_THROW(ExternalSort(alumnos(), kNombre, b, 512, dir_), SchemaError) << "buffers " << b;
  }
  EXPECT_NO_THROW(ExternalSort(alumnos(), kNombre, ExternalSort::kMinBuffers, 512, dir_));
}

TEST_F(SortTest, RechazaUnaColumnaQueNoExiste) {
  EXPECT_THROW(ExternalSort(alumnos(), 99, 8, 512, dir_), SchemaError);
}

TEST_F(SortTest, RechazaUnRegistroQueNoEntraEnUnaPagina) {
  const Schema gordo{
      .table_name = "gordo",
      .columns = {{"id", DataType::Int}, {"texto", DataType::Varchar, 4000}},
      .key_column = 0,
  };
  EXPECT_THROW(ExternalSort(gordo, 0, 8, 256, dir_), SchemaError);
}

TEST_F(SortTest, RechazaUnRegistroQueNoCalzaConElEsquema) {
  ExternalSort sort(alumnos(), kNombre, 8, 512, dir_);
  auto fuente = source_of(std::vector<Record>{{std::int32_t{1}, std::string{"solo dos"}}});
  EXPECT_THROW(static_cast<void>(sort.sorted(*fuente)), InvalidRecord);
}

}  // namespace
}  // namespace quipudb
