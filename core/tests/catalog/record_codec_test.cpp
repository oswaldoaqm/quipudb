// Pruebas de RecordCodec (issue #7): ida y vuelta de registros de longitud
// fija con todos los tipos del esquema.

#include <gtest/gtest.h>

#include <string>
#include <vector>

#include "quipudb/catalog/record_codec.hpp"
#include "quipudb/error.hpp"

namespace quipudb {
namespace {

Schema alumnos() {
  return Schema{
      .table_name = "alumnos",
      .columns = {{"codigo", DataType::Int},
                  {"nombre", DataType::Varchar, 12},
                  {"promedio", DataType::Double},
                  {"activo", DataType::Bool},
                  {"ingreso", DataType::Date}},
      .key_column = 0,
  };
}

TEST(RecordCodec, TamanoYOffsetsSalenDelEsquema) {
  const RecordCodec codec(alumnos());
  EXPECT_EQ(codec.size(), 4u + 12u + 8u + 1u + 4u);
  EXPECT_EQ(codec.offset_of(0), 0u);
  EXPECT_EQ(codec.offset_of(1), 4u);
  EXPECT_EQ(codec.offset_of(2), 16u);
  EXPECT_EQ(codec.offset_of(3), 24u);
  EXPECT_EQ(codec.offset_of(4), 25u);
}

TEST(RecordCodec, IdaYVueltaConLosCincoTipos) {
  const RecordCodec codec(alumnos());
  const Record original{202410123, std::string{"ana"}, 17.25, true, Date{20300}};
  const auto bytes = codec.encode(original);
  ASSERT_EQ(bytes.size(), codec.size());
  const Record vuelta = codec.decode(bytes);
  EXPECT_EQ(vuelta, original);
}

TEST(RecordCodec, VarcharSeRellenaYSeRecortaAlDecodificar) {
  const RecordCodec codec(alumnos());
  const auto corto = codec.encode(Record{1, std::string{"ab"}, 0.0, false, Date{0}});
  // Despues de "ab" vienen ceros hasta completar los 12 bytes.
  EXPECT_EQ(corto[4], std::byte{'a'});
  EXPECT_EQ(corto[5], std::byte{'b'});
  for (std::size_t i = 6; i < 16; ++i) EXPECT_EQ(corto[i], std::byte{0}) << i;
  EXPECT_EQ(std::get<std::string>(codec.decode(corto)[1]), "ab");

  // Exactamente 12 caracteres: cabe, sin terminador, y vuelve completo.
  const std::string lleno(12, 'x');
  const auto exacto = codec.encode(Record{1, lleno, 0.0, false, Date{0}});
  EXPECT_EQ(std::get<std::string>(codec.decode(exacto)[1]), lleno);

  // Vacio tambien es valido.
  const auto vacio = codec.encode(Record{1, std::string{}, 0.0, false, Date{0}});
  EXPECT_EQ(std::get<std::string>(codec.decode(vacio)[1]), "");
}

TEST(RecordCodec, ValoresExtremos) {
  const RecordCodec codec(alumnos());
  const Record r{-2147483647 - 1, std::string{"z"}, -1e300, false, Date{-2147483647}};
  EXPECT_EQ(codec.decode(codec.encode(r)), r);
}

TEST(RecordCodec, RegistroQueNoCalzaLanza) {
  const RecordCodec codec(alumnos());
  // static_cast<void> porque encode() es [[nodiscard]] y aqui solo importa que lance.
  EXPECT_THROW(static_cast<void>(codec.encode(Record{1, std::string{"a"}})),
               InvalidRecord);  // faltan columnas
  EXPECT_THROW(static_cast<void>(codec.encode(Record{1.5, std::string{"a"}, 0.0, true, Date{}})),
               InvalidRecord);  // tipo equivocado
  EXPECT_THROW(static_cast<void>(codec.encode(Record{1, std::string(13, 'x'), 0.0, true, Date{}})),
               InvalidRecord);  // VARCHAR(12) desbordado
}

TEST(RecordCodec, BufferCortoLanzaEnAmbasDirecciones) {
  const RecordCodec codec(alumnos());
  std::vector<std::byte> chico(codec.size() - 1);
  EXPECT_THROW(
      static_cast<void>(codec.encode(Record{1, std::string{"a"}, 0.0, true, Date{}}, chico)),
      InvalidRecord);
  EXPECT_THROW(static_cast<void>(codec.decode(chico)), InvalidRecord);
  EXPECT_THROW(static_cast<void>(codec.decode_column(chico, 0)), InvalidRecord);
}

TEST(RecordCodec, EncodeEnBufferMasGrandeSoloTocaSuParte) {
  const RecordCodec codec(alumnos());
  std::vector<std::byte> buf(codec.size() + 4, std::byte{0xAB});
  codec.encode(Record{7, std::string{"q"}, 1.0, true, Date{1}}, buf);
  for (std::size_t i = codec.size(); i < buf.size(); ++i) EXPECT_EQ(buf[i], std::byte{0xAB});
  EXPECT_EQ(std::get<std::int32_t>(codec.decode(buf)[0]), 7);
}

TEST(RecordCodec, DecodeColumnLeeSoloUnaColumna) {
  const RecordCodec codec(alumnos());
  const auto bytes = codec.encode(Record{99, std::string{"maria"}, 12.5, false, Date{5}});
  EXPECT_EQ(std::get<std::int32_t>(codec.decode_column(bytes, 0)), 99);
  EXPECT_EQ(std::get<std::string>(codec.decode_column(bytes, 1)), "maria");
  EXPECT_DOUBLE_EQ(std::get<double>(codec.decode_column(bytes, 2)), 12.5);
  EXPECT_EQ(std::get<bool>(codec.decode_column(bytes, 3)), false);
  EXPECT_EQ(std::get<Date>(codec.decode_column(bytes, 4)).days, 5);
}

TEST(RecordCodec, ValorSueltoIdaYVuelta) {
  const Column c{"nombre", DataType::Varchar, 8};
  std::vector<std::byte> buf(8);
  RecordCodec::encode_value(c, Value{std::string{"quipu"}}, buf);
  EXPECT_EQ(std::get<std::string>(RecordCodec::decode_value(c, buf)), "quipu");
  EXPECT_THROW(RecordCodec::encode_value(c, Value{1}, buf), InvalidRecord);
  std::vector<std::byte> chico(7);
  EXPECT_THROW(RecordCodec::encode_value(c, Value{std::string{"q"}}, chico), InvalidRecord);
}

TEST(RecordCodec, MilRegistrosIdaYVuelta) {
  const RecordCodec codec(alumnos());
  for (std::int32_t i = 0; i < 1000; ++i) {
    const Record r{i, "a" + std::to_string(i), i * 0.5, (i % 2) == 0, Date{i}};
    EXPECT_EQ(codec.decode(codec.encode(r)), r) << i;
  }
}

}  // namespace
}  // namespace quipudb
