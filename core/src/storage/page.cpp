#include "quipudb/storage/page.hpp"

#include <stdexcept>
#include <string>

namespace quipudb {

std::size_t Page::check_size(std::size_t page_size) {
  if (page_size < kMinSize || page_size > kMaxSize) {
    throw std::invalid_argument("tamano de pagina fuera de [" + std::to_string(kMinSize) + ", " +
                                std::to_string(kMaxSize) + "]: " + std::to_string(page_size));
  }
  return page_size;
}

void Page::check_range(std::size_t offset, std::size_t length) const {
  if (offset > body_size() || length > body_size() - offset) {
    throw std::out_of_range("acceso fuera del body de la pagina: offset " +
                            std::to_string(offset) + " + " + std::to_string(length) + " > " +
                            std::to_string(body_size()));
  }
}

}  // namespace quipudb
