#include "quipudb/catalog/record_codec.hpp"

#include <algorithm>
#include <cstring>
#include <string>

#include "quipudb/error.hpp"

namespace quipudb {

namespace {

template <typename T>
void put(std::span<std::byte> out, const T& v) {
  std::memcpy(out.data(), &v, sizeof(T));
}

template <typename T>
T get(std::span<const std::byte> in) {
  T v;
  std::memcpy(&v, in.data(), sizeof(T));
  return v;
}

}  // namespace

RecordCodec::RecordCodec(Schema schema) : schema_(std::move(schema)) {
  offsets_.reserve(schema_.columns.size());
  for (const auto& c : schema_.columns) {
    offsets_.push_back(size_);
    size_ += c.byte_size();
  }
}

void RecordCodec::encode_value(const Column& col, const Value& v, std::span<std::byte> out) {
  if (type_of(v) != col.type) {
    throw InvalidRecord("columna " + col.name + ": se esperaba " + std::string(to_string(col.type)) +
                        " y llego " + std::string(to_string(type_of(v))));
  }
  if (out.size() < col.byte_size()) {
    throw InvalidRecord("columna " + col.name + ": buffer de " + std::to_string(out.size()) +
                        " bytes, se necesitan " + std::to_string(col.byte_size()));
  }
  switch (col.type) {
    case DataType::Int:
      put(out, std::get<std::int32_t>(v));
      break;
    case DataType::Double:
      put(out, std::get<double>(v));
      break;
    case DataType::Varchar: {
      const auto& s = std::get<std::string>(v);
      if (s.size() > col.length) {
        throw InvalidRecord("columna " + col.name + ": el texto de " + std::to_string(s.size()) +
                            " bytes supera VARCHAR(" + std::to_string(col.length) + ")");
      }
      std::memcpy(out.data(), s.data(), s.size());
      std::fill(out.begin() + static_cast<std::ptrdiff_t>(s.size()),
                out.begin() + static_cast<std::ptrdiff_t>(col.length), std::byte{0});
      break;
    }
    case DataType::Bool:
      out[0] = std::get<bool>(v) ? std::byte{1} : std::byte{0};
      break;
    case DataType::Date:
      put(out, std::get<Date>(v).days);
      break;
  }
}

Value RecordCodec::decode_value(const Column& col, std::span<const std::byte> in) {
  if (in.size() < col.byte_size()) {
    throw InvalidRecord("columna " + col.name + ": buffer de " + std::to_string(in.size()) +
                        " bytes, se necesitan " + std::to_string(col.byte_size()));
  }
  switch (col.type) {
    case DataType::Int:
      return get<std::int32_t>(in);
    case DataType::Double:
      return get<double>(in);
    case DataType::Varchar: {
      const auto* chars = reinterpret_cast<const char*>(in.data());
      const auto* end = std::find(chars, chars + col.length, '\0');
      return std::string(chars, end);
    }
    case DataType::Bool:
      return in[0] != std::byte{0};
    case DataType::Date:
      return Date{get<std::int32_t>(in)};
  }
  throw InvalidRecord("columna " + col.name + ": tipo desconocido");
}

void RecordCodec::encode(const Record& record, std::span<std::byte> out) const {
  schema_.validate(record);
  if (out.size() < size_) {
    throw InvalidRecord("buffer de " + std::to_string(out.size()) + " bytes para un registro de " +
                        std::to_string(size_));
  }
  for (std::size_t i = 0; i < schema_.columns.size(); ++i) {
    encode_value(schema_.columns[i], record[i],
                 out.subspan(offsets_[i], schema_.columns[i].byte_size()));
  }
}

std::vector<std::byte> RecordCodec::encode(const Record& record) const {
  std::vector<std::byte> buf(size_);
  encode(record, buf);
  return buf;
}

Record RecordCodec::decode(std::span<const std::byte> in) const {
  if (in.size() < size_) {
    throw InvalidRecord("buffer de " + std::to_string(in.size()) + " bytes para un registro de " +
                        std::to_string(size_));
  }
  Record r;
  r.reserve(schema_.columns.size());
  for (std::size_t i = 0; i < schema_.columns.size(); ++i) {
    r.push_back(decode_value(schema_.columns[i],
                             in.subspan(offsets_[i], schema_.columns[i].byte_size())));
  }
  return r;
}

Value RecordCodec::decode_column(std::span<const std::byte> in, std::size_t column) const {
  const auto& col = schema_.columns.at(column);
  if (in.size() < size_) {
    throw InvalidRecord("buffer de " + std::to_string(in.size()) + " bytes para un registro de " +
                        std::to_string(size_));
  }
  return decode_value(col, in.subspan(offsets_[column], col.byte_size()));
}

}  // namespace quipudb
