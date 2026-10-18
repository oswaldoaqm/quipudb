// Pruebas de los stemmers de Snowball (issue #160).
//
// El oraculo es PostgreSQL 17: cada par (palabra, raiz) de las tablas salio de
//
//   SELECT p, ts_lexize('spanish_stem', p) FROM palabras;
//
// Esa es la raiz que guarda el indice GIN con to_tsvector('spanish', ...), y
// la comparacion del 2.3.2 (#174) solo es justa si el indice propio produce
// la misma.
//
// La tabla es una muestra fija. Antes de fijarla se comparo el stemmer contra
// PostgreSQL sobre 21 646 palabras -- todo el texto de las diapositivas del
// curso, del enunciado y de docs/, mas formas sinteticas que combinan raices
// con cada sufijo de cada paso del algoritmo y con cada pronombre enclitico --
// y coincidieron todas. De ahi salen los 201 pares de abajo: los ejemplos de
// las diapositivas, palabras elegidas para cubrir cada paso, y 160 tomadas al
// azar con semilla fija.
//
// Las stopwords no estan: el diccionario spanish_stem de PostgreSQL las
// descarta antes de stemmear y devuelve vacio, asi que no hay contra que
// comparar. El tokenizador tampoco las stemmea.

#include "quipudb/index/text_stemmer.hpp"

#include <gtest/gtest.h>

#include <string_view>

namespace quipudb {
namespace {

struct Par {
  std::string_view palabra;
  std::string_view raiz;
};

constexpr Par kEspanol[] = {
    {"averigüen", "averigü"}, {"oíd", "oid"}, {"aéreo", "aere"}, {"cargamento", "cargament"},
    {"prudencia", "prudenci"}, {"amigas", "amig"}, {"amigos", "amig"}, {"peruanos", "peruan"},
    {"estudiosos", "estudi"}, {"estudiando", "estudi"}, {"autorizar", "autoriz"},
    {"autorización", "autoriz"}, {"información", "inform"}, {"programación", "program"},
    {"programadores", "program"}, {"desarrolladores", "desarroll"}, {"haciéndola", "hac"},
    {"dándoselos", "dandosel"}, {"comerlo", "com"}, {"construyendolo", "constru"},
    {"persiguen", "persig"}, {"huyen", "huy"}, {"concluyó", "conclu"}, {"rápidamente", "rapid"},
    {"felizmente", "feliz"}, {"actividades", "activ"}, {"biología", "biolog"},
    {"soluciones", "solucion"}, {"entrega", "entreg"}, {"vectorial", "vectorial"},
    {"búsqueda", "busqued"}, {"índices", "indic"}, {"consultas", "consult"},
    {"recuperación", "recuper"}, {"documentos", "document"}, {"relevancia", "relev"},
    {"computacional", "computacional"}, {"abrazándolos", "abraz"}, {"averiguó", "averigu"},
    {"organizaciones", "organiz"}, {"generativamente", "gener"}, {"biologerías", "biolog"},
    {"posibadamente", "posib"}, {"hacé", "hac"}, {"logicación", "logic"},
    {"concluaban", "conclu"}, {"rincón", "rincon"}, {"huiendolos", "huiendol"},
    {"productiendoselo", "product"}, {"peruanarlas", "peruan"}, {"programírle", "program"},
    {"creírlo", "cre"}, {"navandosela", "nav"}, {"oíerlos", "oierl"}, {"abilo", "abil"},
    {"estudiirá", "estudi"}, {"barata", "barat"}, {"generírlo", "gener"}, {"adrs", "adrs"},
    {"desarrollirles", "desarroll"}, {"aeríamos", "aer"}, {"naviendoselas", "nav"},
    {"naciarselo", "naci"}, {"posibicos", "posib"}, {"informiéndosela", "inform"},
    {"organiziré", "organiz"}, {"evoluativamente", "evolu"}, {"estudimente", "estudi"},
    {"fecha", "fech"}, {"corresponda", "correspond"}, {"abrazamiento", "abraz"},
    {"sprint", "sprint"}, {"estudiyendoselas", "estudiyendosel"}, {"conclusiones", "conclusion"},
    {"organizuyendonos", "organizu"}, {"aplicamos", "aplic"}, {"construaré", "constru"},
    {"coincide", "coincid"}, {"comibles", "comibl"}, {"menor", "menor"},
    {"organizírles", "organiz"}, {"felizivo", "feliz"}, {"abamiento", "abamient"},
    {"lleva", "llev"}, {"reyendole", "reyendol"}, {"evoluabilidad", "evolu"},
    {"informuyendoselas", "informu"}, {"abrazencia", "abrazent"}, {"prudentadora", "prudent"},
    {"abrazirlo", "abraz"}, {"reárse", "rears"}, {"creándolo", "cre"}, {"agregaciones", "agreg"},
    {"status", "status"}, {"organiza", "organiz"}, {"prudentérle", "prudent"},
    {"leiéndole", "leiendol"}, {"construación", "construacion"}, {"productérnos", "product"},
    {"programeréis", "program"}, {"evoluidades", "evolu"}, {"segundo", "segund"},
    {"biologiríais", "biolog"}, {"uancia", "uanci"}, {"soluarlas", "solu"}, {"be", "be"},
    {"learsela", "learsel"}, {"abrazyamos", "abrazy"}, {"programiéndoles", "program"},
    {"evolulogía", "evolulog"}, {"estudiían", "estudi"}, {"cantadores", "cantador"},
    {"backend", "backend"}, {"activadora", "activ"}, {"posibe", "posib"}, {"comís", "com"},
    {"concluándole", "conclu"}, {"dejar", "dej"}, {"uiera", "uier"}, {"oíyendolas", "oiyendol"},
    {"averiguárlo", "averigu"}, {"abrazyendoselos", "abrazyendosel"}, {"aborto", "abort"},
    {"autorizárse", "autoriz"}, {"oriéndosela", "oriendosel"}, {"abrazerme", "abraz"},
    {"rapidamos", "rapid"}, {"construerselos", "constru"}, {"posibuyendosela", "posibu"},
    {"activador", "activ"}, {"amigerás", "amig"}, {"concluírle", "conclu"},
    {"organizándolo", "organiz"}, {"autorizándosela", "autoriz"}, {"construables", "construabl"},
    {"reiendose", "reiendos"}, {"seguiéndolo", "segu"}, {"comismo", "comism"},
    {"felizandolo", "feliz"}, {"posibándosela", "posib"}, {"miembros", "miembr"},
    {"leuyendoselas", "leu"}, {"navyendosela", "navyendosel"}, {"generieron", "gener"},
    {"estudiiéndoselas", "estudi"}, {"caracterización", "caracteriz"}, {"evoluya", "evolu"},
    {"permita", "permit"}, {"airearas", "air"}, {"uuyendola", "uuyendol"},
    {"evoluaréis", "evolu"}, {"prudentaron", "prudent"}, {"tablelock", "tablelock"},
    {"posibuciones", "posibu"}, {"costosos", "costos"}, {"oíiéndolos", "oiiendol"},
    {"navivas", "naviv"}, {"info", "info"}, {"organizérselas", "organiz"},
    {"distingumente", "distingu"}, {"amigasteis", "amig"}, {"uándoselos", "uandosel"},
    {"duplicar", "duplic"}, {"conclugue", "conclug"}, {"prudenteré", "prudent"},
    {"esferoide", "esferoid"}, {"dicas", "dic"}, {"euyendoselo", "euyendosel"},
    {"averiguaréis", "averigu"}, {"nacied", "naci"}, {"seguuyendole", "seguu"},
    {"concluándonos", "conclu"}, {"amigieseis", "amig"}, {"informiéndolas", "inform"},
    {"abrazyais", "abrazyais"}, {"realizada", "realiz"}, {"felizara", "feliz"},
    {"autorizico", "autoriz"}, {"cae", "cae"}, {"persiguérles", "persigu"}, {"cantad", "cant"},
    {"persiguiéndonos", "persigu"}, {"rapidyendolas", "rapidyendol"}, {"logiendoles", "log"},
    {"uidad", "uid"}, {"eandola", "eandol"}, {"construyó", "constru"},
    {"programérlas", "program"}, {"oradora", "orador"}, {"seguyendola", "segu"},
    {"coste", "cost"}, {"abilarás", "abil"}, {"paginas", "pagin"}, {"abió", "abi"},
    {"compariendolas", "compar"}, {"creirselas", "cre"}, {"navyendolas", "navyendol"},
    {"vertical", "vertical"}, {"logyendoles", "logyendol"}, {"leicación", "leicacion"},
    {"detectado", "detect"},
};

TEST(TextStemmer, EspanolCoincideConPostgres) {
  for (const auto& [palabra, raiz] : kEspanol) {
    EXPECT_EQ(stem_spanish(palabra), raiz) << palabra;
  }
}

TEST(TextStemmer, EspanolLosEjemplosDeLaDiapositiva) {
  // "Mis amigas y amigos peruanos son estudiosos": las variantes de una
  // palabra dan la misma raiz.
  EXPECT_EQ(stem_spanish("amigas"), stem_spanish("amigos"));
  EXPECT_EQ(stem_spanish("estudiosos"), stem_spanish("estudiando"));
  EXPECT_EQ(stem_spanish("autorizar"), stem_spanish("autorización"));
}

TEST(TextStemmer, EspanolQuitaElPronombreYLaTildeQueLoAcompana) {
  EXPECT_EQ(stem_spanish("haciéndola"), stem_spanish("haciendo"));
  EXPECT_EQ(stem_spanish("comiéndoselo"), stem_spanish("comiendo"));
}

TEST(TextStemmer, EspanolElPronombreSoloSeQuitaSiElVerboEstaEnRv) {
  // En "dándoselos" RV empieza en la cuarta letra, y "ándo" empieza en la
  // segunda: Snowball no quita el pronombre. Es una limitacion del algoritmo,
  // no de esta implementacion; PostgreSQL da la misma raiz.
  EXPECT_EQ(stem_spanish("dándoselos"), "dandosel");
  EXPECT_NE(stem_spanish("dándoselos"), stem_spanish("dando"));
}

TEST(TextStemmer, EspanolPalabrasCortasYVacia) {
  EXPECT_EQ(stem_spanish(""), "");
  EXPECT_EQ(stem_spanish("x"), "x");
  EXPECT_EQ(stem_spanish("sol"), "sol");
}

}  // namespace
}  // namespace quipudb
