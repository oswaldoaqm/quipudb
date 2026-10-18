// Pruebas de los stemmers de Snowball (issue #160).
//
// El oraculo es PostgreSQL 17: cada par (palabra, raiz) de las tablas salio de
//
//   SELECT p, ts_lexize('spanish_stem', p) FROM palabras;   -- o english_stem
//
// Esa es la raiz que guarda el indice GIN con to_tsvector('spanish', ...), y
// la comparacion del 2.3.2 (#174) solo es justa si el indice propio produce
// la misma.
//
// Las tablas son muestras fijas. Antes de fijarlas se comparo cada stemmer
// contra PostgreSQL sobre un vocabulario grande y coincidieron todas las
// palabras:
//
//   - espanol: 21 646 palabras -- todo el texto de las diapositivas del
//     curso, del enunciado y de docs/, mas formas sinteticas que combinan
//     raices con cada sufijo de cada paso y con cada pronombre enclitico;
//   - ingles: 26 263 palabras -- el texto de la documentacion de las
//     dependencias del frontend, mas formas sinteticas con cada sufijo de
//     cada paso y las palabras de las listas de excepciones.
//
// De ahi salen los pares de abajo: los ejemplos de las diapositivas y de la
// pagina de Snowball, palabras elegidas para cubrir cada paso, y una muestra
// al azar con semilla fija.
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

constexpr Par kIngles[] = {
    {"ponies", "poni"}, {"ties", "tie"}, {"cats", "cat"}, {"gas", "gas"}, {"gaps", "gap"},
    {"kiwis", "kiwi"}, {"agreed", "agre"}, {"feed", "feed"}, {"sing", "sing"},
    {"hoping", "hope"}, {"tanned", "tan"}, {"falling", "fall"}, {"hissing", "hiss"},
    {"fizzed", "fizz"}, {"failing", "fail"}, {"filing", "file"}, {"happy", "happi"},
    {"sky", "sky"}, {"say", "say"}, {"boy", "boy"}, {"toy", "toy"}, {"enjoying", "enjoy"},
    {"conditional", "condit"}, {"hesitanci", "hesit"}, {"digitizer", "digit"},
    {"conformabli", "conform"}, {"radicalli", "radic"}, {"differentli", "differ"},
    {"vileli", "vile"}, {"analogousli", "analog"}, {"vietnamization", "vietnam"},
    {"predication", "predic"}, {"operator", "oper"}, {"hopefulness", "hope"},
    {"electriciti", "electr"}, {"electrical", "electr"}, {"hopeful", "hope"},
    {"inference", "infer"}, {"adjustable", "adjust"}, {"replacement", "replac"},
    {"adjustment", "adjust"}, {"dependent", "depend"}, {"adoption", "adopt"},
    {"communism", "communism"}, {"activate", "activ"}, {"effective", "effect"}, {"rate", "rate"},
    {"controll", "control"}, {"roll", "roll"}, {"generous", "generous"}, {"general", "general"},
    {"communication", "communic"}, {"arsenal", "arsenal"}, {"dying", "die"}, {"lying", "lie"},
    {"tying", "tie"}, {"skies", "sky"}, {"skis", "ski"}, {"news", "news"}, {"early", "earli"},
    {"singly", "singl"}, {"idly", "idl"}, {"gently", "gentl"}, {"ugly", "ugli"},
    {"atlas", "atlas"}, {"inning", "inning"}, {"proceed", "proceed"}, {"exceed", "exceed"},
    {"succeed", "succeed"}, {"information", "inform"}, {"retrieval", "retriev"},
    {"ranking", "rank"}, {"documents", "document"}, {"queries", "queri"},
    {"clonebasenameplugin", "clonebasenameplugin"}, {"openai", "openai"},
    {"honorific", "honorif"}, {"jrfolk", "jrfolk"}, {"dark", "dark"},
    {"renderelement", "renderel"}, {"howeiz", "howeiz"}, {"tiancheng", "tiancheng"},
    {"performed", "perform"}, {"oauth", "oauth"}, {"pppconfig", "pppconfig"},
    {"dualstack", "dualstack"}, {"ucs", "uc"}, {"loginfoplugin", "loginfoplugin"},
    {"mulderbaba", "mulderbaba"}, {"agarwal", "agarw"}, {"flattenerror", "flattenerror"},
    {"cipher", "cipher"}, {"hardware", "hardwar"},
    {"asyncserieswaterfallhook", "asyncserieswaterfallhook"}, {"electrive", "electr"},
    {"atlasbli", "atlasbl"}, {"luego", "luego"}, {"licensee", "license"}, {"orbin", "orbin"},
    {"increments", "increment"}, {"mergereactprops", "mergereactprop"}, {"freshest", "freshest"},
    {"flowing", "flow"}, {"generousness", "generous"}, {"underscorejs", "underscorej"},
    {"strictslashes", "strictslash"}, {"disablemonospaceoptimizations", "disablemonospaceoptim"},
    {"algorithms", "algorithm"}, {"unescaping", "unescap"}, {"scrollbar", "scrollbar"},
    {"throught", "throught"}, {"folds", "fold"}, {"deepfilter", "deepfilt"},
    {"michael", "michael"}, {"tyingization", "tyingiz"}, {"troublnli", "troubln"},
    {"stated", "state"}, {"bingmapsplugin", "bingmapsplugin"}, {"destructuring", "destructur"},
    {"contextmenuradioitemstate", "contextmenuradioitemst"}, {"converts", "convert"},
    {"krizalys", "krizali"}, {"minimistjs", "minimistj"},
    {"getcontainerdomnode", "getcontainerdomnod"}, {"thisentli", "thisent"},
    {"suddenly", "sudden"}, {"absent", "absent"}, {"jblarsen", "jblarsen"},
    {"releaselock", "releaselock"}, {"forrest", "forrest"}, {"indexof", "indexof"},
    {"allowunknownoption", "allowunknownopt"}, {"thegotoguy", "thegotoguy"},
    {"predicer", "predic"}, {"thisize", "thisiz"}, {"vertigo", "vertigo"},
    {"amilajack", "amilajack"}, {"zfy", "zfi"}, {"unreasonable", "unreason"},
    {"remotehost", "remotehost"}, {"switzerland", "switzerland"},
    {"drawerrootchangeeventreason", "drawerrootchangeeventreason"}, {"typescript", "typescript"},
    {"atlasiciti", "atlas"}, {"sdk", "sdk"}, {"maybeglobstring", "maybeglobstr"},
    {"proceedenci", "proceed"}, {"ision", "ision"}, {"derks", "derk"}, {"pkgjs", "pkgjs"},
    {"pm", "pm"}, {"fromversion", "fromvers"}, {"reward", "reward"},
    {"codeblockwriter", "codeblockwrit"}, {"valuemissing", "valuemiss"}, {"sequence", "sequenc"},
    {"runy", "runi"}, {"circumstances", "circumst"}, {"invalidate", "invalid"},
    {"analogization", "analog"}, {"unbalanced", "unbalanc"}, {"popovers", "popov"},
    {"fuzzy", "fuzzi"}, {"forcing", "forc"}, {"compactqueue", "compactqueu"},
    {"chyzwar", "chyzwar"}, {"w", "w"}, {"appendconst", "appendconst"}, {"optimizely", "optim"},
    {"daniele", "daniel"}, {"defensive", "defens"}, {"eastasianwidth", "eastasianwidth"},
    {"snappointchangeeventdetails", "snappointchangeeventdetail"},
    {"setprototypeof", "setprototypeof"},
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

TEST(TextStemmer, InglesCoincideConPostgres) {
  for (const auto& [palabra, raiz] : kIngles) {
    EXPECT_EQ(stem_english(palabra), raiz) << palabra;
  }
}

TEST(TextStemmer, InglesLasVariantesDanLaMismaRaiz) {
  EXPECT_EQ(stem_english("connection"), stem_english("connected"));
  EXPECT_EQ(stem_english("connecting"), stem_english("connections"));
  EXPECT_EQ(stem_english("generous"), "generous");  // R1 fijo tras "gener"
  EXPECT_NE(stem_english("general"), stem_english("gene"));
}

TEST(TextStemmer, InglesPalabrasCortasYExcepciones) {
  EXPECT_EQ(stem_english(""), "");
  EXPECT_EQ(stem_english("is"), "is");
  EXPECT_EQ(stem_english("skies"), "sky");
  EXPECT_EQ(stem_english("news"), "news");
}

TEST(TextStemmer, ElIdiomaSeEligeEnEjecucion) {
  EXPECT_EQ(stem("estudiosos", TextLanguage::kSpanish), "estudi");
  EXPECT_EQ(stem("studies", TextLanguage::kEnglish), "studi");
}

}  // namespace
}  // namespace quipudb
