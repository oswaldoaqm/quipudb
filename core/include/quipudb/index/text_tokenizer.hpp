#pragma once

// Tokenizacion del texto para el indice invertido (issue #160).
//
// Que es aqui
// -----------
//
//   La funcion que convierte un texto en la lista de terminos que se indexan
//   y se buscan. Es la primera etapa del preprocesamiento de la Parte 3: lo
//   que entra al SPIMI (#161) y lo que se busca en los rankings (#164, #165)
//   sale de aqui.
//
//   El indice y la consulta tienen que pasar por la MISMA instancia, o por dos
//   con las mismas `TokenizerOptions`. Si indexan "canción" como "cancion" y
//   la consulta llega como "canción", la busqueda no encuentra nada aunque la
//   palabra este en el documento. Por eso las opciones se comparan con `==` y
//   se guardaran junto al indice: abrirlo con otras opciones es un error, no
//   una busqueda que devuelve vacio.
//
// Las etapas, en orden
// --------------------
//
//   1. Separar. Una palabra es una corrida de letras o digitos; todo lo demas
//      corta. Letra es ASCII, las letras de Latin-1 (á, ñ, ü, ç...) y
//      cualquier caracter fuera de los bloques de puntuacion y simbolos de
//      Unicode, para no partir en pedazos un nombre en otro alfabeto. Las
//      marcas diacriticas combinantes (U+0300-U+036F) y el guion suave se
//      ignoran sin cortar: asi un texto en forma NFD ("e" + tilde suelta) da
//      la misma palabra que en NFC.
//
//   2. Pasar a minusculas.
//
//   3. Juntar siglas con puntos: "O.N.U." da "onu", igual que "ONU". Solo
//      junta letras sueltas separadas por un punto exacto, para no pegar
//      "3.14" en "314" ni el final de una oracion con el inicio de otra.
//
//   4. Quitar los acentos: tildes, dieresis, circunflejos y cedilla. La enie
//      se conserva: "año" y "ano" no son la misma palabra.
//
// Que NO hace
// -----------
//
//   - No conserva posiciones: la Parte 3 rankea por frecuencia, no busca
//     frases exactas.
//   - No corrige ortografia ni expande sinonimos.

#include <cstddef>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace quipudb {

/// Un termino mas largo que esto, en bytes UTF-8, se descarta. No se trunca:
/// cortarlo haria coincidir dos palabras distintas con el mismo prefijo.
///
/// El limite existe porque el termino viaja dentro de una entrada del
/// diccionario del indice, y esa entrada vive en una pagina de 4 KB. Una
/// palabra de 64 bytes ya es una cadena en base64 o una URL pegada, no algo
/// que alguien vaya a buscar.
inline constexpr std::size_t kMaxTermBytes = 64;

/// Como se tokeniza. Dos tokenizadores con opciones iguales producen
/// exactamente los mismos terminos para el mismo texto.
struct TokenizerOptions {
  std::size_t max_term_bytes{kMaxTermBytes};

  bool operator==(const TokenizerOptions&) const = default;
};

class TextTokenizer {
 public:
  explicit TextTokenizer(TokenizerOptions options = {});

  /// Los terminos del texto, en el orden en que aparecen y con repeticiones:
  /// el indice cuenta la frecuencia de cada uno.
  ///
  /// `text` es UTF-8. Una secuencia invalida no lanza: se trata como
  /// separador, y el resto del documento se indexa igual.
  ///
  /// Es determinista: el mismo texto da siempre la misma lista.
  [[nodiscard]] std::vector<std::string> tokenize(std::string_view text) const;

  [[nodiscard]] const TokenizerOptions& options() const noexcept { return options_; }

 private:
  TokenizerOptions options_;
};

}  // namespace quipudb
