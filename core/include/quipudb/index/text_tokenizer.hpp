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
//   4. Descartar las stopwords: las listas de Snowball del idioma elegido
//      mas las propias del corpus (ver `TokenizerOptions`). Se comparan SIN
//      tildes, asi que "esta" y "está" se descartan las dos aunque alguien
//      escriba sin acentos.
//
//   5. Reducir cada palabra a su raiz con el stemmer de Snowball del idioma
//      elegido: "amigas" y "amigos" dan "amig" (ver `text_stemmer.hpp`). Las
//      palabras con digitos no se stemmean: "covid19" no tiene sufijo que
//      quitar, y "2020" tampoco.
//
//   6. Quitar los acentos: tildes, dieresis, circunflejos y cedilla. La enie
//      se conserva: "año" y "ano" no son la misma palabra.
//
//   Los acentos se quitan AL FINAL y no antes del stemmer porque Snowball los
//   necesita: el sufijo "-ación" lleva tilde en sus reglas, y sin ella
//   "información" no se reduce a "inform". Asi lo hace tambien
//   to_tsvector('spanish', ...), y con las opciones por defecto el
//   tokenizador produce los mismos terminos que PostgreSQL.
//
//   El costo de ese orden: una consulta escrita sin tildes no siempre
//   encuentra la palabra con tilde. "camion" y "camión" coinciden, porque
//   Snowball quita la tilde de la raiz, pero "informacion" se queda entera
//   mientras "información" da "inform". Quien prefiera que las tildes no
//   importen nunca puede pedir `unaccent_before_stem`, que es la receta de
//   PostgreSQL con el diccionario unaccent delante del stemmer: las dos
//   formas coinciden siempre, a cambio de que los sufijos con tilde ya no se
//   reduzcan.
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
#include <unordered_set>
#include <vector>

#include "quipudb/index/text_language.hpp"

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
  /// Idiomas cuyas listas de stopwords se descartan. Por defecto solo el
  /// espanol, que es lo que hace to_tsvector('spanish', ...) en PostgreSQL:
  /// la comparacion del 2.3.2 mide los dos indices sobre los mismos terminos.
  ///
  /// Con un corpus mezclado se pueden pedir los dos idiomas, sabiendo que la
  /// lista inglesa descarta palabras que en espanol si significan algo, como
  /// "once" o "don". Una lista vacia no descarta nada.
  std::vector<TextLanguage> stopword_languages{TextLanguage::kSpanish};

  /// Stopwords propias del corpus, una palabra por entrada: por ejemplo
  /// "articulo" en una coleccion de articulos, donde aparece en todos. Se
  /// normalizan igual que el texto, asi que "Artículo" y "articulo" valen lo
  /// mismo.
  std::vector<std::string> extra_stopwords{};

  /// El stemmer de Snowball que se aplica, o ninguno. Por defecto el
  /// espanol, por la misma razon que las stopwords. Sin stemmer, "amigas" y
  /// "amigos" son terminos distintos.
  std::optional<TextLanguage> stemmer{TextLanguage::kSpanish};

  /// Quitar las tildes ANTES del stemmer y no despues. Ver la etapa 5.
  bool unaccent_before_stem{false};

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

  /// Si la palabra se descarta como stopword. Acepta la palabra en
  /// cualquier forma -- mayusculas, con o sin tildes --, igual que el texto.
  [[nodiscard]] bool is_stopword(std::string_view word) const;

  [[nodiscard]] const TokenizerOptions& options() const noexcept { return options_; }

 private:
  TokenizerOptions options_;
  /// Las stopwords ya normalizadas: en minusculas y sin tildes.
  std::unordered_set<std::string> stopwords_;
};

}  // namespace quipudb
