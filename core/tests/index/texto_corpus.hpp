#pragma once

// Corpus y oraculo compartidos por las pruebas de SPIMI (#161) y del indice
// invertido (#162).

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <map>
#include <string>
#include <vector>

#include "quipudb/index/spimi.hpp"
#include "quipudb/index/text_tokenizer.hpp"

namespace quipudb::prueba {

/// Un corpus sintetico y reproducible: documentos de 5 a 40 palabras tomadas
/// de un vocabulario de 300, con frecuencias sesgadas -- unas pocas palabras
/// aparecen en casi todos los documentos y la mayoria en muy pocos, como en un
/// texto real. Las palabras llevan digitos, asi que el stemmer no las toca.
inline std::vector<std::string> corpus(std::size_t documentos) {
  std::uint64_t estado = 160;
  const auto siguiente = [&estado]() {
    estado = estado * 6364136223846793005ULL + 1442695040888963407ULL;
    return static_cast<std::uint32_t>(estado >> 33);
  };
  std::vector<std::string> docs;
  for (std::size_t d = 0; d < documentos; ++d) {
    std::string texto;
    const std::uint32_t palabras = 5 + siguiente() % 36;
    for (std::uint32_t i = 0; i < palabras; ++i) {
      // El minimo de dos uniformes sesga hacia los indices chicos.
      const std::uint32_t a = siguiente() % 300;
      const std::uint32_t b = siguiente() % 300;
      texto += "w" + std::to_string(std::min(a, b)) + " ";
    }
    docs.push_back(std::move(texto));
  }
  return docs;
}

/// El indice invertido hecho a mano, con todo en memoria.
inline std::map<std::string, std::vector<Posting>> oraculo(
    const std::vector<std::string>& docs, const TokenizerOptions& opciones) {
  const TextTokenizer tokenizador(opciones);
  std::map<std::string, std::vector<Posting>> indice;
  for (std::size_t i = 0; i < docs.size(); ++i) {
    std::map<std::string, std::uint32_t> tf;
    for (const std::string& t : tokenizador.tokenize(docs[i])) ++tf[t];
    for (const auto& [t, n] : tf) indice[t].push_back(Posting{static_cast<DocId>(i + 1), n});
  }
  return indice;
}

}  // namespace quipudb::prueba
