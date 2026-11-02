// Pruebas de la columna VECTOR(dim) (issue #180): tamano, validacion del
// esquema, ida y vuelta por el codec y orden entre vectores.
//
// Es el equivalente de POINT en la Parte 2, con una diferencia que manda en
// todo lo demas: la dimension es parte del TIPO, como la capacidad de un
// VARCHAR, asi que un vector de otra dimension no es un valor fuera de rango
// sino un valor de otro tipo.

#include <gtest/gtest.h>

#include <cmath>
#include <limits>
#include <string>
#include <vector>

#include "quipudb/catalog/record_codec.hpp"
#include "quipudb/error.hpp"

namespace quipudb {
namespace {

/// Una tabla de objetos multimedia: metadatos y el vector que los describe.
/// Es la forma que pide el criterio 4 del issue.
Schema fotos(std::uint16_t dimension = 4) {
  return Schema{
      .table_name = "fotos",
      .columns = {{"id", DataType::Int},
                  {"ruta", DataType::Varchar, 16},
                  {"rasgos", DataType::Vector, dimension}},
      .key_column = 0,
  };
}

Record foto(VectorValue rasgos) {
  return Record{1, std::string{"a.jpg"}, std::move(rasgos)};
}

// ---------------------------------------------------------------------------
// Tamano en disco
// ---------------------------------------------------------------------------

TEST(VectorColumn, OcupaCuatroBytesPorComponente) {
  EXPECT_EQ((Column{"v", DataType::Vector, 1}.byte_size()), 4u);
  EXPECT_EQ((Column{"v", DataType::Vector, 13}.byte_size()), 52u);
  EXPECT_EQ((Column{"v", DataType::Vector, 128}.byte_size()), 512u);
}

TEST(VectorColumn, ElTipoSeNombraVECTOR) {
  EXPECT_EQ(to_string(DataType::Vector), "VECTOR");
}

TEST(VectorColumn, UnValorVectorialSeReconoceComoVector) {
  // type_of hace static_cast sobre el indice del variant: si el enum y el
  // variant se desalinearan, un vector se leeria como otro tipo.
  const Value v = VectorValue{1.0F, 2.0F};
  EXPECT_EQ(type_of(v), DataType::Vector);
}

TEST(VectorColumn, ElEsquemaSumaElVectorAlTamanoDelRegistro) {
  EXPECT_EQ(fotos(4).record_size(), 4u + 16u + 16u);
}

// ---------------------------------------------------------------------------
// Validacion del esquema
// ---------------------------------------------------------------------------

TEST(VectorColumn, UnVectorDeLaDimensionDeclaradaPasa) {
  EXPECT_NO_THROW(fotos().validate(foto({1.0F, 2.0F, 3.0F, 4.0F})));
}

TEST(VectorColumn, UnVectorMasCortoSeRechaza) {
  EXPECT_THROW(fotos().validate(foto({1.0F, 2.0F})), InvalidRecord);
}

TEST(VectorColumn, UnVectorMasLargoSeRechaza) {
  EXPECT_THROW(fotos().validate(foto({1.0F, 2.0F, 3.0F, 4.0F, 5.0F})), InvalidRecord);
}

TEST(VectorColumn, ElErrorDeDimensionDiceCuantasHayYCuantasSeEsperaban) {
  try {
    fotos().validate(foto({1.0F, 2.0F}));
    FAIL() << "se esperaba InvalidRecord";
  } catch (const InvalidRecord& error) {
    const std::string mensaje = error.what();
    EXPECT_NE(mensaje.find("2 componentes"), std::string::npos) << mensaje;
    EXPECT_NE(mensaje.find("VECTOR(4)"), std::string::npos) << mensaje;
  }
}

TEST(VectorColumn, UnNaNEnUnaComponenteSeRechaza) {
  // Un NaN haria falsa cualquier distancia posterior y el objeto quedaria en
  // un lugar arbitrario del ranking sin que nada lo denuncie.
  EXPECT_THROW(
      fotos().validate(foto({1.0F, std::numeric_limits<float>::quiet_NaN(), 3.0F, 4.0F})),
      InvalidRecord);
}

TEST(VectorColumn, UnInfinitoEnUnaComponenteSeRechaza) {
  EXPECT_THROW(
      fotos().validate(foto({1.0F, 2.0F, std::numeric_limits<float>::infinity(), 4.0F})),
      InvalidRecord);
}

TEST(VectorColumn, ElErrorDeComponenteNoFinitaDiceCualEs) {
  try {
    fotos().validate(foto({1.0F, 2.0F, std::numeric_limits<float>::quiet_NaN(), 4.0F}));
    FAIL() << "se esperaba InvalidRecord";
  } catch (const InvalidRecord& error) {
    EXPECT_NE(std::string(error.what()).find("componente 3"), std::string::npos) << error.what();
  }
}

TEST(VectorColumn, UnValorDeOtroTipoEnLaColumnaSeRechaza) {
  EXPECT_THROW(fotos().validate(Record{1, std::string{"a.jpg"}, 3.5}), InvalidRecord);
}

// ---------------------------------------------------------------------------
// Ida y vuelta por el codec
// ---------------------------------------------------------------------------

TEST(VectorColumn, IdaYVueltaDeUnRegistroConVector) {
  const RecordCodec codec(fotos());
  const Record original = foto({0.5F, -1.25F, 0.0F, 1024.5F});
  const auto bytes = codec.encode(original);
  ASSERT_EQ(bytes.size(), codec.size());
  EXPECT_EQ(codec.decode(bytes), original);
}

TEST(VectorColumn, LosOffsetsDejanElVectorDespuesDeLasDemasColumnas) {
  const RecordCodec codec(fotos());
  EXPECT_EQ(codec.offset_of(0), 0u);
  EXPECT_EQ(codec.offset_of(1), 4u);
  EXPECT_EQ(codec.offset_of(2), 20u);
  EXPECT_EQ(codec.size(), 36u);
}

TEST(VectorColumn, SePuedeLeerSoloLaColumnaDelVector) {
  // Es lo que necesita una busqueda por similitud: traer el vector sin
  // deserializar el resto del registro.
  const RecordCodec codec(fotos());
  const auto bytes = codec.encode(foto({1.0F, 2.0F, 3.0F, 4.0F}));
  EXPECT_EQ(std::get<VectorValue>(codec.decode_column(bytes, 2)),
            (VectorValue{1.0F, 2.0F, 3.0F, 4.0F}));
}

TEST(VectorColumn, LasComponentesSobrevivenExactamenteEnFloat) {
  // Sin redondeo: lo que entra en float32 vuelve igual, bit a bit.
  const RecordCodec codec(fotos());
  const VectorValue rasgos{0.1F, 1.0F / 3.0F, -2.718281828F, 3.4028235e38F};
  const auto vuelta = codec.decode(codec.encode(foto(rasgos)));
  EXPECT_EQ(std::get<VectorValue>(vuelta[2]), rasgos);
}

TEST(VectorColumn, UnVectorDeOtraDimensionNoSeSerializa) {
  const RecordCodec codec(fotos());
  EXPECT_THROW(static_cast<void>(codec.encode(foto({1.0F, 2.0F}))), InvalidRecord);
}

TEST(VectorColumn, UnaDimensionGrandeSigueDandoLaVuelta) {
  const Schema esquema{
      .table_name = "descriptores",
      .columns = {{"id", DataType::Int}, {"sift", DataType::Vector, 128}},
      .key_column = 0,
  };
  VectorValue sift(128);
  for (std::size_t i = 0; i < sift.size(); ++i) sift[i] = static_cast<float>(i) / 7.0F;
  const RecordCodec codec(esquema);
  EXPECT_EQ(codec.size(), 4u + 512u);
  EXPECT_EQ(codec.decode(codec.encode(Record{7, sift})), (Record{7, sift}));
}

// ---------------------------------------------------------------------------
// Orden entre vectores
// ---------------------------------------------------------------------------

TEST(VectorColumn, DosVectoresIgualesComparanIgual) {
  EXPECT_EQ(compare(Value{VectorValue{1.0F, 2.0F}}, Value{VectorValue{1.0F, 2.0F}}), 0);
}

TEST(VectorColumn, ElOrdenEsLexicograficoComponenteAComponente) {
  EXPECT_LT(compare(Value{VectorValue{1.0F, 2.0F}}, Value{VectorValue{1.0F, 3.0F}}), 0);
  EXPECT_GT(compare(Value{VectorValue{2.0F, 0.0F}}, Value{VectorValue{1.0F, 9.0F}}), 0);
}

TEST(VectorColumn, UnPrefijoVaAntesQueElVectorMasLargo) {
  EXPECT_LT(compare(Value{VectorValue{1.0F}}, Value{VectorValue{1.0F, 0.0F}}), 0);
}

TEST(VectorColumn, UnNaNTieneUnLugarFijoEnElOrden) {
  // Sin esto, KeyLess dejaria de ser un orden estricto y los std::map y
  // std::sort del core quedarian con comportamiento indefinido.
  const Value nan = VectorValue{std::numeric_limits<float>::quiet_NaN()};
  const Value uno = VectorValue{1.0F};
  EXPECT_GT(compare(nan, uno), 0);
  EXPECT_LT(compare(uno, nan), 0);
  EXPECT_EQ(compare(nan, nan), 0);
}

TEST(VectorColumn, CompararUnVectorConOtroTipoEsUnErrorDeEsquema) {
  EXPECT_THROW(static_cast<void>(compare(Value{VectorValue{1.0F}}, Value{3.5})), SchemaError);
}

}  // namespace
}  // namespace quipudb
