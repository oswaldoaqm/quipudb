#pragma once

// JOIN sobre volumenes que no caben en memoria (issue #22).
//
// Es la tercera parte de la viñeta de External Algorithms del enunciado:
// "GROUP BY y JOIN optimizados con External Hashing o el uso estrategico de
// indices". El plan de ejecucion (ADR 0002) ya reservo el paso `op: "join"`.
//
// Dos caminos, y cual se elige
// ----------------------------
//
//   HASH JOIN (grace). Se particionan las DOS entradas por el hash de su clave
//   de join, escribiendo cada particion a disco. Dos filas que casan comparten
//   clave, luego comparten hash, luego caen en particiones homologas: basta
//   comparar la particion i de un lado con la particion i del otro y nunca con
//   las demas. Esa es toda la idea, y es lo que lo hace externo: en memoria
//   solo vive una particion del lado interno a la vez.
//
//   INDEX NESTED LOOP. Se recorre el lado externo una vez y, por cada fila, se
//   sondea el lado interno por su clave. Quien sondea es un `JoinProbe`: un
//   indice secundario (#16, #19) o la propia tabla cuando la clave de join es
//   su clave primaria, que es el caso del B+ agrupado y del secuencial. No
//   escribe nada a disco.
//
// Cual gana no es cuestion de que exista un indice
// ------------------------------------------------
//
//   El criterio del issue decia "si hay indice sobre la clave de join se usa
//   index nested loop; si no, hash join". Medido sobre el motor real
//   (`docs/medir-condicion-join.cpp`, GCC 13.3, paginas de 4 KB, 81 filas por
//   pagina) esa regla es falsa casi siempre:
//
//     |R|     |S|      indice              INL     hash join    gana
//     10 000  10 000   bplus_unclustered    40 176        474    hash  (85x)
//     10 000  10 000   extendible_hash      20 082        474    hash  (42x)
//     10 000  10 000   bplus (100 claves) 1 033 282        474    hash  (2180x)
//      1 000  10 000   bplus_unclustered     4 017        261    hash
//        500  10 000   bplus_unclustered     2 009        249    hash
//        100  10 000   bplus_unclustered       401        240    hash
//         50  10 000   bplus_unclustered       201        240    INL
//        100  10 000   extendible_hash         201        240    INL
//
//   (paginas leidas de verdad, de `OpStats`, no de una formula)
//
//   El INL paga un costo por FILA externa -- medido: 4,0 paginas por sonda con
//   B+ no agrupado, 2,0 con hash extensible -- mientras que el hash join paga
//   un costo por PAGINA, 3(N_R + N_S). Con 81 filas por pagina, una fila
//   externa de mas le cuesta al INL cuatro paginas y al hash join cuatro
//   centesimas. Por eso el INL solo gana cuando el lado externo es diminuto
//   frente al interno: el cruce medido esta cerca de |R| ~ |S|/100 con B+ y de
//   |S|/50 con hash extensible, y `choose()` implementa exactamente eso.
//
//   El caso que el propio issue manda probar -- dos tablas de 10 000 -- es el
//   caso donde la regla del issue elige la estrategia 85 veces mas cara.
//
//   Esto no es una opinion sobre indices: es la tercera vez en este repo que
//   una regla de folklore no sobrevive a la medicion (#18, #19 dos veces), y
//   por eso `docs/medir-condicion-join.cpp` se queda en el repo, como
//   `docs/medir-condicion-merge.py`.
//
// La particion que no cabe
// ------------------------
//
//   Si una particion del lado interno no entra en memoria, no se re-particiona:
//   se procesa por BLOQUES. Se carga un bloque de lo que cabe, se recorre la
//   particion externa entera contra el, y se pasa al bloque siguiente. Es el
//   nested loop por bloques de toda la vida, aplicado dentro de una particion.
//
//   Se eligio esto en vez del re-particionado del #21 por dos razones. Termina
//   siempre, incluso cuando todas las claves de la particion son iguales, que
//   es justo el caso donde re-particionar no separa nada (el mismo problema de
//   las claves repetidas del #18). Y su costo se sabe de antemano -- bloques
//   por el tamaño de la particion externa -- asi que `stats()` se puede
//   contrastar contra una formula, que es lo que el 2.1.6 hace con el sort.
//
//   `blocked_partitions()` cuenta cuantas particiones necesitaron mas de un
//   bloque. Es la medida de lo mal repartida que estaba la clave, y material
//   para el informe.
//
// Que recibe y que devuelve
// -------------------------
//
//   Recibe `RecordSource` (#20) y devuelve `RecordSource`: el mismo flujo que
//   consumen y producen el sort (#20) y el group by (#21). La salida NO se
//   materializa. Importa mas aqui que en los otros dos: un join puede devolver
//   muchas mas filas de las que recibio, asi que juntarlas todas en un vector
//   seria haber particionado en disco para gastar la memoria al final.
//
//   El esquema de salida es la concatenacion de los dos de entrada y lo arma
//   `output_schema()`. Las columnas que se llaman igual en los dos lados se
//   desambiguan prefijando el nombre de su tabla ("alumnos.codigo",
//   "notas.codigo"); las que no colisionan conservan su nombre tal cual,
//   porque son las que el frontend (#37) muestra como cabecera y prefijarlas
//   todas ensuciaria el caso comun. La columna de join sale DOS veces, una por
//   lado: quedarse con una sola es lo que hace `USING` en SQL, y quien quiera
//   eso pone un `project` encima, que es como el ADR 0002 ya lo modela.

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

#include "quipudb/catalog/table.hpp"
#include "quipudb/catalog/types.hpp"
#include "quipudb/external/external_sort.hpp"

namespace quipudb {

// ---------------------------------------------------------------------------
// El lado interno cuando se puede sondear
// ---------------------------------------------------------------------------

/// Lo que el index nested loop necesita del lado interno: dame las filas cuya
/// clave de join es `key`.
///
/// Es una interfaz y no un `Index&` porque hay dos caminos distintos y el
/// enunciado llama a los dos "uso estrategico de indices":
///
///   - un indice secundario (`Index`) sobre un heap file, que devuelve RIDs
///     que hay que resolver con `TableFile::read`;
///   - la propia tabla, cuando la clave de join ES su clave primaria: ahi
///     `TableFile::search` ya resuelve, y en un B+ agrupado o un secuencial eso
///     es exactamente un indice. Es el caso mas comun de un join real (una
///     clave foranea apuntando a una clave primaria) y el criterio del issue,
///     que solo hablaba de `Index`, lo dejaba fuera.
class JoinProbe {
 public:
  virtual ~JoinProbe() = default;

  /// Filas del lado interno cuya clave de join es `key`. Vacio si no hay.
  [[nodiscard]] virtual std::vector<Record> matches(const Key& key) = 0;

  /// Esquema de las filas que devuelve `matches`.
  [[nodiscard]] virtual const Schema& schema() const noexcept = 0;

  /// Una de las constantes de `kind::`: la del indice que sondea, o la de la
  /// tabla si sondea por clave primaria. Es lo que el plan de ejecucion pone
  /// como `structure` del paso (ADR 0002).
  [[nodiscard]] virtual std::string_view structure() const noexcept = 0;

  /// Filas del lado interno. `choose()` la necesita para decidir.
  [[nodiscard]] virtual std::size_t rows() const = 0;

  /// Paginas que cuesta UNA sonda, medido en `docs/medir-condicion-join.cpp`.
  /// Es el numero que hace que la decision sea una medicion y no una regla.
  [[nodiscard]] virtual std::size_t probe_cost() const noexcept = 0;

  [[nodiscard]] virtual const OpStats& stats() const noexcept = 0;
  virtual void reset_stats() noexcept = 0;
};

/// Sonda por indice secundario: busca los RIDs en `ix` y los resuelve en
/// `datos`. Las dos referencias tienen que sobrevivir a la sonda.
///
/// Por la regla del #3, un indice secundario solo se monta sobre un heap file,
/// asi que `datos` siempre lo es.
[[nodiscard]] std::unique_ptr<JoinProbe> probe_of(Index& ix, TableFile& datos);

/// Sonda por clave primaria: `TableFile::search` sobre la propia tabla. Vale
/// cuando la clave de join es `schema().key_column`, y es lo que convierte al
/// B+ agrupado y al secuencial en caminos de join.
///
/// Lanza SchemaError si `tabla` no esta organizada por esa clave.
[[nodiscard]] std::unique_ptr<JoinProbe> probe_of(TableFile& tabla);

// ---------------------------------------------------------------------------
// El join
// ---------------------------------------------------------------------------

/// Equijoin INTERNO de dos flujos por una columna de cada lado.
///
/// Solo INNER y solo igualdad: es lo que pide la viñeta del 2.1.2, y la
/// gramatica que el 2.1.3 enumera no tiene sintaxis para un OUTER. Agregarlo
/// seria decidir por el parser algo que el parser todavia no pide.
///
/// De un solo uso, igual que `ExternalSort` y `ExternalGroupBy`: se construye,
/// se llama a `joined()` una vez y se lee el flujo. Mientras ese flujo viva,
/// este objeto tiene que seguir vivo: es el dueño de las particiones.
class ExternalJoin {
 public:
  enum class Strategy : std::uint8_t {
    /// Mide las dos entradas y elige con `choose()`. Es lo correcto por
    /// omision: la estrategia depende del tamaño relativo, no de que exista un
    /// indice.
    kAuto,
    /// Solo hash join. Siempre se puede: no necesita indice ni orden.
    kHash,
    /// Solo index nested loop. Lanza `Unsupported` si no se le dio una sonda.
    /// Existe para que los benchmarks puedan medir el INL puro, incluso donde
    /// `kAuto` no lo elegiria.
    kIndexNested,
  };

  /// Particiones minimas. Con menos de 2 no hay particionado que valga.
  static constexpr std::size_t kMinPartitions = 2;

  /// Buffers minimos. Uno para leer la entrada y al menos dos particiones que
  /// escribir.
  static constexpr std::size_t kMinBuffers = 3;

  /// `izquierda` / `derecha` describen los dos flujos de entrada y
  /// `col_izquierda` / `col_derecha` la columna por la que se juntan. Los dos
  /// tipos tienen que coincidir: comparar un INT con un VARCHAR no es un join,
  /// es un error de consulta.
  ///
  /// `buffers` son PAGINAS de memoria, igual que en el sort (#20) y en el
  /// group by (#21): es lo que fija cuantas particiones se abren a la vez y
  /// cuanto entra en un bloque.
  ///
  /// Lanza SchemaError si alguna columna no existe, si los tipos no coinciden,
  /// si `buffers` no alcanza o si un registro de salida no entra en una pagina.
  ExternalJoin(Schema izquierda, std::size_t col_izquierda, Schema derecha,
               std::size_t col_derecha, Strategy strategy = Strategy::kAuto,
               std::size_t buffers = ExternalSort::kDefaultBuffers,
               std::size_t page_size = kDefaultPageSize, std::filesystem::path dir = {});

  ~ExternalJoin();

  ExternalJoin(const ExternalJoin&) = delete;
  ExternalJoin& operator=(const ExternalJoin&) = delete;

  /// Esquema de las filas que devuelve `joined()`: las columnas de la
  /// izquierda seguidas de las de la derecha, con las repetidas prefijadas por
  /// su tabla.
  ///
  /// `key_column` queda en 0 porque un join no tiene clave primaria y el tipo
  /// `Schema` no sabe expresar que no la hay. Nadie debe usarla: la salida de
  /// un join no se busca por clave, se recorre. (Es la misma limitacion que
  /// `ExternalGroupBy::output_schema()`.)
  [[nodiscard]] const Schema& output_schema() const noexcept { return salida_; }

  /// Junta dos flujos. Sin sonda no hay camino por indice, asi que siempre es
  /// hash join; `kIndexNested` lanza `Unsupported` aqui.
  [[nodiscard]] std::unique_ptr<RecordSource> joined(RecordSource& izquierda,
                                                     RecordSource& derecha);

  /// Junta dos flujos cuando el lado DERECHO se puede sondear por indice.
  ///
  /// Pide las dos formas del lado derecho -- la sonda y el flujo -- porque
  /// `kAuto` elige entre ellas y no puede convertir una en la otra: un
  /// `JoinProbe` no se puede recorrer entero y un `RecordSource` no se puede
  /// sondear. Si la estrategia elegida es INL, `derecha` no se toca.
  ///
  /// `filas_izquierda` es cuantas filas trae el lado externo. Hay que decirlo
  /// porque `kAuto` lo necesita para decidir y no hay forma de averiguarlo sin
  /// consumir el flujo, que es justo lo que no se puede hacer antes de
  /// elegir. Quien llama si lo sabe: es `TableFile::size()`, o la cardinalidad
  /// que el planner ya estimo. **Un 0 significa "no lo se"** y elige hash
  /// join, que es la respuesta segura: equivocarse hacia hash cuesta un factor
  /// dos y equivocarse hacia INL cuesta hasta 2180x (ver la tabla de arriba).
  [[nodiscard]] std::unique_ptr<RecordSource> joined(RecordSource& izquierda,
                                                     JoinProbe& sonda, RecordSource& derecha,
                                                     std::size_t filas_izquierda);

  /// Decide la estrategia con la regla MEDIDA en
  /// `docs/medir-condicion-join.cpp`.
  ///
  /// El INL cuesta `filas_externas * costo_sonda` paginas, una por fila; el
  /// hash join cuesta 3(N_R + N_S), dos pasadas de particionado mas una de
  /// sondeo, y eso es por PAGINA. Se elige el menor. Las paginas se piden ya
  /// calculadas para no tener que suponer que los dos lados tienen la misma
  /// densidad de filas por pagina.
  ///
  /// Supone una coincidencia por fila externa, que es el mejor caso posible
  /// para el INL: con claves repetidas el INL empeora y el hash join no, asi
  /// que equivocarse por aqui solo puede llevar a elegir hash de mas, nunca
  /// INL de mas.
  [[nodiscard]] static bool conviene_index_nested(std::size_t filas_externas,
                                                  std::uint64_t paginas_externas,
                                                  std::uint64_t paginas_internas,
                                                  std::size_t costo_sonda) noexcept;

  /// Filas de cada entrada que entran en una pagina. Es lo que convierte un
  /// conteo de filas en el conteo de paginas que `conviene_index_nested` pide.
  [[nodiscard]] std::size_t left_records_per_page() const noexcept { return por_pagina_izq_; }
  [[nodiscard]] std::size_t right_records_per_page() const noexcept { return por_pagina_der_; }

  // --- lo que el plan de ejecucion y el 2.1.6 leen -------------------------

  /// Estrategia que se termino usando. Puede no ser la pedida: `kAuto` elige.
  [[nodiscard]] Strategy used() const noexcept { return usada_; }

  /// `structure` del paso `join` en el plan (ADR 0002): "external_hash" con
  /// hash join, y el `kind::` de la estructura que sondeo con INL.
  [[nodiscard]] std::string_view structure() const noexcept { return estructura_; }

  /// Filas leidas de cada lado y filas emitidas.
  [[nodiscard]] std::size_t left_rows() const noexcept { return filas_izq_; }
  [[nodiscard]] std::size_t right_rows() const noexcept { return filas_der_; }
  [[nodiscard]] std::size_t output_rows() const noexcept { return filas_salida_; }

  /// Particiones que se crearon. 0 con INL.
  [[nodiscard]] std::size_t partitions() const noexcept { return particiones_; }

  /// Particiones que no cupieron en memoria y hubo que recorrer por bloques.
  /// Es la medida del sesgo de la clave.
  [[nodiscard]] std::size_t blocked_partitions() const noexcept { return por_bloques_; }

  /// Filas que entran en una pagina de la SALIDA.
  [[nodiscard]] std::size_t records_per_page() const noexcept { return por_pagina_; }

  [[nodiscard]] const OpStats& stats() const noexcept { return stats_; }
  void reset_stats() noexcept { stats_.reset(); }

  /// Costo en paginas que la teoria predice para un hash join sobre entradas
  /// de `paginas_izq` y `paginas_der` paginas, sin particiones desbordadas:
  /// 3(N_R + N_S). Es lo que el 2.1.6 contrasta contra `stats()`, igual que
  /// `ExternalSort::predicted_pages`.
  [[nodiscard]] static std::uint64_t predicted_pages(std::uint64_t paginas_izq,
                                                     std::uint64_t paginas_der) noexcept;

  /// Bytes que ocupan las particiones vivas. Cero con INL.
  [[nodiscard]] std::uintmax_t temp_bytes() const;

 private:
  class Temporal;
  class Particion;
  class SalidaHash;
  class SalidaIndexNested;

  void validar() const;
  [[nodiscard]] Schema construir_esquema_salida() const;

  /// El hash join propiamente dicho. Lo llaman los dos `joined()`.
  [[nodiscard]] std::unique_ptr<RecordSource> hash_join(RecordSource& izquierda,
                                                        RecordSource& derecha);

  /// Pone los contadores a cero al empezar un `joined()`.
  void reiniciar() noexcept;

  /// Reparte un flujo en `particiones_` archivos por el hash de su clave.
  /// Devuelve los temporales, uno por particion.
  [[nodiscard]] std::vector<std::shared_ptr<Temporal>> particionar(RecordSource& entrada,
                                                                   const Schema& esquema,
                                                                   std::size_t columna,
                                                                   std::string_view lado,
                                                                   std::size_t& filas);

  [[nodiscard]] std::filesystem::path nueva_ruta(std::string_view lado, std::size_t i);

  /// Concatena una fila de cada lado en una de la salida.
  [[nodiscard]] Record unir(const Record& izq, const Record& der) const;

  Schema izq_;
  Schema der_;
  Schema salida_;
  std::size_t col_izq_ = 0;
  std::size_t col_der_ = 0;
  Strategy pedida_ = Strategy::kAuto;
  Strategy usada_ = Strategy::kAuto;
  std::string_view estructura_;
  std::size_t buffers_ = 0;
  std::size_t page_size_ = 0;
  std::size_t por_pagina_ = 0;      // de la salida
  std::size_t por_pagina_izq_ = 0;  // de la entrada izquierda
  std::size_t por_pagina_der_ = 0;  // de la entrada derecha
  std::filesystem::path dir_;
  std::uint64_t serie_ = 0;

  std::size_t filas_izq_ = 0;
  std::size_t filas_der_ = 0;
  std::size_t filas_salida_ = 0;
  std::size_t particiones_ = 0;
  std::size_t por_bloques_ = 0;

  std::vector<std::shared_ptr<Temporal>> vivos_;
  OpStats stats_;
};

}  // namespace quipudb
