// Pruebas del tokenizador de texto (issue #160).
//
// Lo que se cuida aqui es que el indice y la consulta vean las mismas
// palabras: cada caso compara lo que sale del tokenizador con la lista exacta
// esperada, con el orden y las repeticiones, porque el SPIMI (#161) cuenta la
// frecuencia de cada termino a partir de esta lista.

#include "quipudb/index/text_tokenizer.hpp"

#include <gtest/gtest.h>

#include <algorithm>
#include <string>
#include <vector>

#include "quipudb/index/text_stopwords.hpp"

namespace quipudb {
namespace {

using Terminos = std::vector<std::string>;

/// Sin stopwords ni stemmer: solo separar, minusculas, siglas y acentos.
TextTokenizer crudo() { return TextTokenizer(TokenizerOptions{.stopword_languages = {}}); }

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
  EXPECT_EQ(crudo().tokenize("canción"), crudo().tokenize("canción"));
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

  TextTokenizer corto(TokenizerOptions{.stopword_languages = {}, .max_term_bytes = 4});
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
  const TextTokenizer t;
  EXPECT_EQ(t.tokenize("La casa es grande"), (Terminos{"casa", "grande"}));
  EXPECT_EQ(t.tokenize("El gato está en la casa"), (Terminos{"gato", "casa"}));
  EXPECT_EQ(t.tokenize("La casa es bonita y grande"), (Terminos{"casa", "bonita", "grande"}));
  EXPECT_EQ(t.tokenize("El sol brilla sobre la casa"), (Terminos{"sol", "brilla", "casa"}));
  // La lista inglesa no se aplica si no se pide.
  EXPECT_EQ(t.tokenize("the house"), (Terminos{"the", "house"}));
}

TEST(TextTokenizer, LasStopwordsSeComparanSinTildesNiMayusculas) {
  const TextTokenizer t;
  EXPECT_EQ(t.tokenize("ESTÁ esta Esta está"), Terminos{});
  EXPECT_EQ(t.tokenize("Él dijo que si, sí"), (Terminos{"dijo"}));
  EXPECT_TRUE(t.is_stopword("TAMBIÉN"));
  EXPECT_TRUE(t.is_stopword("tambien"));
  EXPECT_FALSE(t.is_stopword("casa"));
}

TEST(TextTokenizer, LosIdiomasDeStopwordsSeEligen) {
  const TextTokenizer ingles(TokenizerOptions{.stopword_languages = {TextLanguage::kEnglish}});
  EXPECT_EQ(ingles.tokenize("The house of the rising sun"), (Terminos{"house", "rising", "sun"}));
  EXPECT_EQ(ingles.tokenize("la casa"), (Terminos{"la", "casa"}));

  const TextTokenizer ambos(
      TokenizerOptions{.stopword_languages = {TextLanguage::kSpanish, TextLanguage::kEnglish}});
  EXPECT_EQ(ambos.tokenize("the house y la casa"), (Terminos{"house", "casa"}));
}

TEST(TextTokenizer, StopwordsPropiasDelCorpus) {
  const TextTokenizer t(TokenizerOptions{.extra_stopwords = {"Artículo", "ADEMÁS"}});
  EXPECT_EQ(t.tokenize("El articulo dice, ademas, que el artículo es largo"),
            (Terminos{"dice", "largo"}));
}

TEST(TextTokenizer, OpcionesIgualesSeReconocen) {
  EXPECT_EQ(TokenizerOptions{}, TokenizerOptions{});
  EXPECT_NE(TokenizerOptions{}, TokenizerOptions{.stopword_languages = {}});
  EXPECT_NE(TokenizerOptions{}, TokenizerOptions{.extra_stopwords = {"x"}});
}

TEST(TextTokenizer, EsDeterminista) {
  const std::string texto = "Mis amigas y amigos peruanos son estudiosos. ¿O.N.U.? 3.14";
  const Terminos primera = crudo().tokenize(texto);
  for (int i = 0; i < 5; ++i) EXPECT_EQ(crudo().tokenize(texto), primera);
}

}  // namespace
}  // namespace quipudb
