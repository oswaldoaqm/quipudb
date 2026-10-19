#include "quipudb/index/text_ranking.hpp"

#include <algorithm>
#include <cstddef>

namespace quipudb {
namespace {

/// Mayor puntaje primero; a igual puntaje, el doc menor.
bool antes(const ScoredDocument& a, const ScoredDocument& b) noexcept {
  if (a.score != b.score) return a.score > b.score;
  return a.doc < b.doc;
}

}  // namespace

void keep_top_k(std::vector<ScoredDocument>& resultado, std::size_t k) {
  if (k < resultado.size()) {
    std::partial_sort(resultado.begin(), resultado.begin() + static_cast<std::ptrdiff_t>(k),
                      resultado.end(), antes);
    resultado.resize(k);
  } else {
    std::sort(resultado.begin(), resultado.end(), antes);
  }
}

}  // namespace quipudb
