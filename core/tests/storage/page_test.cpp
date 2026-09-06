// Pruebas de Page (issue #6).

#include <gtest/gtest.h>

#include <array>
#include <cstdint>
#include <stdexcept>

#include "quipudb/storage/page.hpp"

namespace quipudb {
namespace {

TEST(Page, NaceEnBlancoConFreeSpaceIgualAlBody) {
  Page p;
  EXPECT_EQ(p.size(), kDefaultPageSize);
  EXPECT_EQ(p.body_size(), kDefaultPageSize - Page::kHeaderSize);
  EXPECT_EQ(p.next(), kInvalidPage);
  EXPECT_EQ(p.record_count(), 0);
  EXPECT_EQ(p.free_space(), p.body_size());
  for (const auto b : p.body()) EXPECT_EQ(b, std::byte{0});
}

TEST(Page, TamanoConfigurableYConLimites) {
  EXPECT_EQ(Page(512).size(), 512u);
  EXPECT_EQ(Page(8192).body_size(), 8192u - Page::kHeaderSize);
  EXPECT_EQ(Page(Page::kMaxSize).free_space(), Page::kMaxSize - Page::kHeaderSize);
  EXPECT_THROW(Page(Page::kMinSize - 1), std::invalid_argument);
  EXPECT_THROW(Page(Page::kMaxSize + 1), std::invalid_argument);
}

TEST(Page, CabeceraIdaYVuelta) {
  Page p(256);
  p.set_next(42);
  p.set_record_count(7);
  p.set_free_space(100);
  const auto h = p.header();
  EXPECT_EQ(h.next, 42u);
  EXPECT_EQ(h.record_count, 7);
  EXPECT_EQ(h.free_space, 100);

  p.set_header(Page::Header{kInvalidPage, 0, 248});
  EXPECT_EQ(p.next(), kInvalidPage);
  EXPECT_EQ(p.free_space(), 248);
}

TEST(Page, LaCabeceraOcupaLosPrimerosBytesYElBodyElResto) {
  Page p(256);
  p.set_next(1);
  // El body no ve la cabecera: escribir en el body offset 0 no pisa `next`.
  p.write<std::uint32_t>(0, 0xDEADBEEF);
  EXPECT_EQ(p.next(), 1u);
  EXPECT_EQ(p.read<std::uint32_t>(0), 0xDEADBEEFu);
  EXPECT_EQ(p.bytes().size(), 256u);
  EXPECT_EQ(p.body().size(), 248u);
}

TEST(Page, ReadWriteTipados) {
  Page p(256);
  p.write<std::int32_t>(0, -5);
  p.write<double>(4, 3.25);
  p.write<std::uint16_t>(12, 65535);
  EXPECT_EQ(p.read<std::int32_t>(0), -5);
  EXPECT_DOUBLE_EQ(p.read<double>(4), 3.25);
  EXPECT_EQ(p.read<std::uint16_t>(12), 65535);
}

TEST(Page, BytesCrudosIdaYVuelta) {
  Page p(256);
  const std::array<std::byte, 4> src{std::byte{1}, std::byte{2}, std::byte{3}, std::byte{4}};
  p.write_bytes(10, src);
  const auto got = p.read_bytes(10, 4);
  ASSERT_EQ(got.size(), 4u);
  EXPECT_EQ(got[0], std::byte{1});
  EXPECT_EQ(got[3], std::byte{4});
}

TEST(Page, AccesoFueraDelBodyLanza) {
  Page p(256);
  const std::size_t body = p.body_size();
  EXPECT_NO_THROW(p.write<std::uint8_t>(body - 1, 1));
  EXPECT_THROW(p.write<std::uint8_t>(body, 1), std::out_of_range);
  EXPECT_THROW(p.write<std::uint32_t>(body - 3, 1), std::out_of_range);
  EXPECT_THROW(static_cast<void>(p.read<std::uint64_t>(body - 7)), std::out_of_range);
  EXPECT_THROW(static_cast<void>(p.read_bytes(body - 1, 2)), std::out_of_range);
  // Un offset enorme no puede desbordar la suma y pasar el chequeo.
  EXPECT_THROW(p.write<std::uint8_t>(static_cast<std::size_t>(-1), 1), std::out_of_range);
}

TEST(Page, ClearDejaTodoEnCero) {
  Page p(256);
  p.set_next(9);
  p.set_record_count(3);
  p.write<std::uint32_t>(0, 1234);
  p.clear();
  EXPECT_EQ(p, Page(256));
}

TEST(Page, IgualdadComparaByteABytes) {
  Page a(256), b(256);
  EXPECT_EQ(a, b);
  b.write<std::uint8_t>(100, 1);
  EXPECT_FALSE(a == b);
}

}  // namespace
}  // namespace quipudb
