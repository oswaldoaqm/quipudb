#pragma once

#include <string_view>

namespace quipudb {

// Version del motor. Sirve tambien como archivo semilla para que el core
// compile antes de que existan los modulos de storage e indices.
constexpr std::string_view kVersion = "0.1.0";

std::string_view version() noexcept;

}  // namespace quipudb
