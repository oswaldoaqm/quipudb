// Pruebas del tokenizador de texto (issue #160).
//
// Lo que se cuida aqui es que el indice y la consulta vean las mismas
// palabras: cada caso compara lo que sale del tokenizador con la lista exacta
// esperada, con el orden y las repeticiones, porque el SPIMI (#161) cuenta la
// frecuencia de cada termino a partir de esta lista.

#include "quipudb/index/text_tokenizer.hpp"

#include <gtest/gtest.h>

#include <algorithm>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "quipudb/index/text_stopwords.hpp"

namespace quipudb {
namespace {

using Terminos = std::vector<std::string>;

/// Sin stopwords ni stemmer: solo separar, minusculas, siglas y acentos.
TextTokenizer crudo() {
  return TextTokenizer(TokenizerOptions{.stopword_languages = {}, .stemmer = std::nullopt});
}

/// Con stopwords pero sin stemmer, para mirar solo las stopwords.
TextTokenizer sin_stemmer(TokenizerOptions o = {}) {
  o.stemmer = std::nullopt;
  return TextTokenizer(std::move(o));
}

TEST(TextTokenizer, SeparaPorTodoLoQueNoEsLetraNiDigito) {
  EXPECT_EQ(crudo().tokenize("Hola, mundo! (base_de-datos)  2026;fin"),
            (Terminos{"hola", "mundo", "base", "de", "datos", "2026", "fin"}));
}

TEST(TextTokenizer, PasaAMinusculasTambienLasLetrasConTilde) {
  EXPECT_EQ(crudo().tokenize("ÁRBOL Ñandú ÉXITO"), (Terminos{"arbol", "ñandu", "exito"}));
}

TEST(TextTokenizer, QuitaTildesDieresisYCedillaPeroNoLaEnie) {
  EXPECT_EQ(crudo().tokenize("canción pingüino façade año ano"),
            (Terminos{"cancion", "pinguino", "facade", "año", "ano"}));
}

TEST(TextTokenizer, LaFormaDescompuestaDaLaMismaPalabra) {
  // "canción" en NFD: la o seguida de la tilde combinante U+0301.
  EXPECT_EQ(crudo().tokenize("cancio\xCC\x81n"), crudo().tokenize("canción"));
}

TEST(TextTokenizer, ConservaElOrdenYLasRepeticiones) {
  EXPECT_EQ(crudo().tokenize("casa grande, casa bonita, CASA"),
            (Terminos{"casa", "grande", "casa", "bonita", "casa"}));
}

TEST(TextTokenizer, LosNumerosSonTerminos) {
  EXPECT_EQ(crudo().tokenize("covid19 en 2020, versión 3.14"),
            (Terminos{"covid19", "en", "2020", "version", "3", "14"}));
}

TEST(TextTokenizer, JuntaLasSiglasConPuntos) {
  // Es la pregunta de la diapositiva de normalizacion: O.N.U. y ONU deben
  // coincidir.
  EXPECT_EQ(crudo().tokenize("La O.N.U. y la ONU"), (Terminos{"la", "onu", "y", "la", "onu"}));
  EXPECT_EQ(crudo().tokenize("EE.UU."), (Terminos{"ee", "uu"}));
  EXPECT_EQ(crudo().tokenize("a las 10 a.m."), (Terminos{"a", "las", "10", "am"}));
}

TEST(TextTokenizer, NoJuntaLoQueNoEsUnaSigla) {
  // Una oracion que termina en una letra y otra que empieza con una letra.
  EXPECT_EQ(crudo().tokenize("vitamina A. Y luego"),
            (Terminos{"vitamina", "a", "y", "luego"}));
  // Letras separadas por algo que no es un punto exacto.
  EXPECT_EQ(crudo().tokenize("a-b a..b a. b"), (Terminos{"a", "b", "a", "b", "a", "b"}));
}

TEST(TextTokenizer, PuntuacionTipograficaYEmojiSeparan) {
  EXPECT_EQ(crudo().tokenize("«hola»—“mundo” ¿qué? ¡ya! 🙂fin…ok"),
            (Terminos{"hola", "mundo", "que", "ya", "fin", "ok"}));
}

TEST(TextTokenizer, OtrosAlfabetosNoSePartenEnPedazos) {
  EXPECT_EQ(crudo().tokenize("λόγος kraków"), (Terminos{"λόγος", "krakow"}));
}

TEST(TextTokenizer, UnUtf8InvalidoCortaPeroNoLanza) {
  // 0xE9 es "é" en Latin-1: en UTF-8 es un byte suelto e invalido.
  const std::string latin1 = "caf\xE9 con leche";
  EXPECT_EQ(crudo().tokenize(latin1), (Terminos{"caf", "con", "leche"}));
  // Secuencia truncada al final del texto.
  EXPECT_EQ(crudo().tokenize("fin\xC3"), (Terminos{"fin"}));
}

TEST(TextTokenizer, TextoVacioOSoloPuntuacion) {
  EXPECT_TRUE(crudo().tokenize("").empty());
  EXPECT_TRUE(crudo().tokenize("  ,;.!?  \n\t").empty());
}

TEST(TextTokenizer, DescartaLosTerminosDemasiadoLargos) {
  const std::string largo(kMaxTermBytes + 1, 'x');
  const std::string justo(kMaxTermBytes, 'y');
  EXPECT_EQ(crudo().tokenize("antes " + largo + " " + justo + " despues"),
            (Terminos{"antes", justo, "despues"}));

  TextTokenizer corto(
      TokenizerOptions{.stopword_languages = {}, .stemmer = std::nullopt, .max_term_bytes = 4});
  EXPECT_EQ(corto.tokenize("sol luna estrella"), (Terminos{"sol", "luna"}));
}

TEST(TextStopwords, SonLasListasDeSnowball) {
  // Los tamanos de spanish.stop y english.stop de PostgreSQL 17.
  EXPECT_EQ(stopword_list(TextLanguage::kSpanish).size(), 313U);
  EXPECT_EQ(stopword_list(TextLanguage::kEnglish).size(), 127U);

  const auto contiene = [](TextLanguage idioma, std::string_view palabra) {
    const auto lista = stopword_list(idioma);
    return std::find(lista.begin(), lista.end(), palabra) != lista.end();
  };
  EXPECT_TRUE(contiene(TextLanguage::kSpanish, "de"));
  EXPECT_TRUE(contiene(TextLanguage::kSpanish, "estábamos"));
  EXPECT_TRUE(contiene(TextLanguage::kEnglish, "the"));
  EXPECT_FALSE(contiene(TextLanguage::kSpanish, "the"));
  EXPECT_FALSE(contiene(TextLanguage::kEnglish, "de"));
}

TEST(TextTokenizer, PorDefectoDescartaLasStopwordsEnEspanol) {
  // El ejemplo de la diapositiva de Bag of Words: "La casa es grande" queda
  // en ["casa", "grande"].
  const TextTokenizer t = sin_stemmer();
  EXPECT_EQ(t.tokenize("La casa es grande"), (Terminos{"casa", "grande"}));
  EXPECT_EQ(t.tokenize("El gato está en la casa"), (Terminos{"gato", "casa"}));
  EXPECT_EQ(t.tokenize("La casa es bonita y grande"), (Terminos{"casa", "bonita", "grande"}));
  EXPECT_EQ(t.tokenize("El sol brilla sobre la casa"), (Terminos{"sol", "brilla", "casa"}));
  // La lista inglesa no se aplica si no se pide.
  EXPECT_EQ(t.tokenize("the house"), (Terminos{"the", "house"}));
}

TEST(TextTokenizer, LasStopwordsSeComparanSinTildesNiMayusculas) {
  const TextTokenizer t = sin_stemmer();
  EXPECT_EQ(t.tokenize("ESTÁ esta Esta está"), Terminos{});
  EXPECT_EQ(t.tokenize("Él dijo que si, sí"), (Terminos{"dijo"}));
  EXPECT_TRUE(t.is_stopword("TAMBIÉN"));
  EXPECT_TRUE(t.is_stopword("tambien"));
  EXPECT_FALSE(t.is_stopword("casa"));
}

TEST(TextTokenizer, LosIdiomasDeStopwordsSeEligen) {
  const TextTokenizer ingles = sin_stemmer({.stopword_languages = {TextLanguage::kEnglish}});
  EXPECT_EQ(ingles.tokenize("The house of the rising sun"), (Terminos{"house", "rising", "sun"}));
  EXPECT_EQ(ingles.tokenize("la casa"), (Terminos{"la", "casa"}));

  const TextTokenizer ambos =
      sin_stemmer({.stopword_languages = {TextLanguage::kSpanish, TextLanguage::kEnglish}});
  EXPECT_EQ(ambos.tokenize("the house y la casa"), (Terminos{"house", "casa"}));
}

TEST(TextTokenizer, StopwordsPropiasDelCorpus) {
  const TextTokenizer t = sin_stemmer({.extra_stopwords = {"Artículo", "ADEMÁS"}});
  EXPECT_EQ(t.tokenize("El articulo dice, ademas, que el artículo es largo"),
            (Terminos{"dice", "largo"}));
}

TEST(TextTokenizer, OpcionesIgualesSeReconocen) {
  EXPECT_EQ(TokenizerOptions{}, TokenizerOptions{});
  EXPECT_NE(TokenizerOptions{}, TokenizerOptions{.stopword_languages = {}});
  EXPECT_NE(TokenizerOptions{}, TokenizerOptions{.extra_stopwords = {"x"}});
  EXPECT_NE(TokenizerOptions{}, TokenizerOptions{.stemmer = TextLanguage::kEnglish});
  EXPECT_NE(TokenizerOptions{}, TokenizerOptions{.unaccent_before_stem = true});
}

TEST(TextTokenizer, LasVariantesDeUnaPalabraDanElMismoTermino) {
  // La diapositiva de preprocesamiento: amigas y amigos -> amig.
  EXPECT_EQ(TextTokenizer().tokenize("Mis amigas y amigos peruanos son estudiosos."),
            (Terminos{"amig", "amig", "peruan", "estudi"}));
}

/// Un texto y los lexemas de to_tsvector(config, texto) en PostgreSQL 17,
/// ordenados por posicion.
struct Paridad {
  const char* texto;
  Terminos postgres;
};

TEST(TextTokenizer, ConLasOpcionesPorDefectoProduceLoMismoQuePostgres) {
  // Los documentos y la consulta del ejemplo de pesos TF-IDF de la
  // diapositiva, y los de la diapositiva de LIKE frente a Bag of Words.
  const Paridad casos[] = {
      {"Cargamento de oro dañado por el fuego", {"cargament", "oro", "dañ", "fueg"}},
      {"La entrega de la plata llegó en el camión color plata",
       {"entreg", "plat", "lleg", "camion", "color", "plat"}},
      {"El cargamento de oro llegó en un camión", {"cargament", "oro", "lleg", "camion"}},
      {"oro plata camión", {"oro", "plat", "camion"}},
      {"Aprender a programar es esencial en el mundo actual.",
       {"aprend", "program", "esencial", "mund", "actual"}},
      {"La programación es una habilidad clave para los desarrolladores.",
       {"program", "habil", "clav", "desarroll"}},
      {"Los programadores crean soluciones innovadoras con código.",
       {"program", "cre", "solucion", "innov", "codig"}},
  };
  const TextTokenizer t;
  for (const auto& [texto, postgres] : casos) EXPECT_EQ(t.tokenize(texto), postgres) << texto;
}

TEST(TextTokenizer, EnInglesProduceLoMismoQuePostgres) {
  // Los tres documentos de la diapositiva del indice invertido.
  const Paridad casos[] = {
      {"The bright blue butterfly hangs on the breeze.",
       {"bright", "blue", "butterfli", "hang", "breez"}},
      {"It's best to forget the great sky and to retire from every wind.",
       {"best", "forget", "great", "sky", "retir", "everi", "wind"}},
      {"Under blue sky, in bright sunlight, one need not search around.",
       {"blue", "sky", "bright", "sunlight", "one", "need", "search", "around"}},
  };
  const TextTokenizer t(TokenizerOptions{.stopword_languages = {TextLanguage::kEnglish},
                                         .stemmer = TextLanguage::kEnglish});
  for (const auto& [texto, postgres] : casos) EXPECT_EQ(t.tokenize(texto), postgres) << texto;
}

TEST(TextTokenizer, LaConsultaSinTildesEncuentraLaPalabraConTilde) {
  const TextTokenizer t;
  EXPECT_EQ(t.tokenize("camión"), t.tokenize("camion"));
  EXPECT_EQ(t.tokenize("CAMIÓN"), t.tokenize("camion"));
  // El costo documentado de stemmear con las tildes puestas.
  EXPECT_NE(t.tokenize("información"), t.tokenize("informacion"));

  const TextTokenizer sin_tildes(TokenizerOptions{.unaccent_before_stem = true});
  EXPECT_EQ(sin_tildes.tokenize("información"), sin_tildes.tokenize("informacion"));
}

TEST(TextTokenizer, LasPalabrasConDigitosNoSeStemmean) {
  EXPECT_EQ(TextTokenizer().tokenize("covid19 casos2020 2020"),
            (Terminos{"covid19", "casos2020", "2020"}));
}

TEST(TextTokenizer, LasStopwordsNoLleganAlStemmer) {
  // "estábamos" es stopword: no debe aparecer reducida a otra cosa.
  EXPECT_EQ(TextTokenizer().tokenize("estábamos estudiando"), (Terminos{"estudi"}));
}

TEST(TextTokenizer, EsDeterminista) {
  const std::string texto = "Mis amigas y amigos peruanos son estudiosos. ¿O.N.U.? 3.14";
  const Terminos primera = TextTokenizer().tokenize(texto);
  for (int i = 0; i < 5; ++i) EXPECT_EQ(TextTokenizer().tokenize(texto), primera);
}

}  // namespace
}  // namespace quipudb
