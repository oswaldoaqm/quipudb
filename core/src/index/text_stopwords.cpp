#include "quipudb/index/text_stopwords.hpp"

#include <array>
#include <string_view>

namespace quipudb {
namespace {

// Las listas de Snowball (https://snowballstem.org, licencia BSD), copiadas
// tal cual de las que distribuye PostgreSQL 17 en
// share/tsearch_data/{spanish,english}.stop. Son las que aplica
// to_tsvector('spanish', ...) y to_tsvector('english', ...), asi que el indice
// propio y el GIN de la comparacion del 2.3.2 (#174) descartan exactamente las
// mismas palabras.
//
// Van con sus tildes, como en el original. El tokenizador las compara sin
// tildes (ver `TextTokenizer`).

constexpr std::array<std::string_view, 313> kSpanish{
    "de", "la", "que", "el", "en", "y", "a", "los", "del", "se", "las", "por", "un", "para",
    "con", "no", "una", "su", "al", "lo", "como", "más", "pero", "sus", "le", "ya", "o", "este",
    "sí", "porque", "esta", "entre", "cuando", "muy", "sin", "sobre", "también", "me", "hasta",
    "hay", "donde", "quien", "desde", "todo", "nos", "durante", "todos", "uno", "les", "ni",
    "contra", "otros", "ese", "eso", "ante", "ellos", "e", "esto", "mí", "antes", "algunos",
    "qué", "unos", "yo", "otro", "otras", "otra", "él", "tanto", "esa", "estos", "mucho",
    "quienes", "nada", "muchos", "cual", "poco", "ella", "estar", "estas", "algunas", "algo",
    "nosotros", "mi", "mis", "tú", "te", "ti", "tu", "tus", "ellas", "nosotras", "vosostros",
    "vosostras", "os", "mío", "mía", "míos", "mías", "tuyo", "tuya", "tuyos", "tuyas", "suyo",
    "suya", "suyos", "suyas", "nuestro", "nuestra", "nuestros", "nuestras", "vuestro", "vuestra",
    "vuestros", "vuestras", "esos", "esas", "estoy", "estás", "está", "estamos", "estáis",
    "están", "esté", "estés", "estemos", "estéis", "estén", "estaré", "estarás", "estará",
    "estaremos", "estaréis", "estarán", "estaría", "estarías", "estaríamos", "estaríais",
    "estarían", "estaba", "estabas", "estábamos", "estabais", "estaban", "estuve", "estuviste",
    "estuvo", "estuvimos", "estuvisteis", "estuvieron", "estuviera", "estuvieras",
    "estuviéramos", "estuvierais", "estuvieran", "estuviese", "estuvieses", "estuviésemos",
    "estuvieseis", "estuviesen", "estando", "estado", "estada", "estados", "estadas", "estad",
    "he", "has", "ha", "hemos", "habéis", "han", "haya", "hayas", "hayamos", "hayáis", "hayan",
    "habré", "habrás", "habrá", "habremos", "habréis", "habrán", "habría", "habrías",
    "habríamos", "habríais", "habrían", "había", "habías", "habíamos", "habíais", "habían",
    "hube", "hubiste", "hubo", "hubimos", "hubisteis", "hubieron", "hubiera", "hubieras",
    "hubiéramos", "hubierais", "hubieran", "hubiese", "hubieses", "hubiésemos", "hubieseis",
    "hubiesen", "habiendo", "habido", "habida", "habidos", "habidas", "soy", "eres", "es",
    "somos", "sois", "son", "sea", "seas", "seamos", "seáis", "sean", "seré", "serás", "será",
    "seremos", "seréis", "serán", "sería", "serías", "seríamos", "seríais", "serían", "era",
    "eras", "éramos", "erais", "eran", "fui", "fuiste", "fue", "fuimos", "fuisteis", "fueron",
    "fuera", "fueras", "fuéramos", "fuerais", "fueran", "fuese", "fueses", "fuésemos", "fueseis",
    "fuesen", "sintiendo", "sentido", "sentida", "sentidos", "sentidas", "siente", "sentid",
    "tengo", "tienes", "tiene", "tenemos", "tenéis", "tienen", "tenga", "tengas", "tengamos",
    "tengáis", "tengan", "tendré", "tendrás", "tendrá", "tendremos", "tendréis", "tendrán",
    "tendría", "tendrías", "tendríamos", "tendríais", "tendrían", "tenía", "tenías", "teníamos",
    "teníais", "tenían", "tuve", "tuviste", "tuvo", "tuvimos", "tuvisteis", "tuvieron",
    "tuviera", "tuvieras", "tuviéramos", "tuvierais", "tuvieran", "tuviese", "tuvieses",
    "tuviésemos", "tuvieseis", "tuviesen", "teniendo", "tenido", "tenida", "tenidos", "tenidas",
    "tened",
};

constexpr std::array<std::string_view, 127> kEnglish{
    "i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you", "your", "yours",
    "yourself", "yourselves", "he", "him", "his", "himself", "she", "her", "hers", "herself",
    "it", "its", "itself", "they", "them", "their", "theirs", "themselves", "what", "which",
    "who", "whom", "this", "that", "these", "those", "am", "is", "are", "was", "were", "be",
    "been", "being", "have", "has", "had", "having", "do", "does", "did", "doing", "a", "an",
    "the", "and", "but", "if", "or", "because", "as", "until", "while", "of", "at", "by", "for",
    "with", "about", "against", "between", "into", "through", "during", "before", "after",
    "above", "below", "to", "from", "up", "down", "in", "out", "on", "off", "over", "under",
    "again", "further", "then", "once", "here", "there", "when", "where", "why", "how", "all",
    "any", "both", "each", "few", "more", "most", "other", "some", "such", "no", "nor", "not",
    "only", "own", "same", "so", "than", "too", "very", "s", "t", "can", "will", "just", "don",
    "should", "now",
};

}  // namespace

std::span<const std::string_view> stopword_list(TextLanguage language) noexcept {
  if (language == TextLanguage::kEnglish) return kEnglish;
  return kSpanish;
}

}  // namespace quipudb
