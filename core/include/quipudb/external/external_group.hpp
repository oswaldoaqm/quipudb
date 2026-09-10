#pragma once

// GROUP BY sobre volumenes que no caben en memoria (issue #21).
//
// Es la segunda mitad de la viñeta de External Algorithms del enunciado:
// "GROUP BY y JOIN optimizados con External Hashing o el uso estrategico de
// indices". El GROUP BY de la 2.1.3 (#28) se apoya en esto, y el plan de
// ejecucion (ADR 0002) ya reservo el paso `op: "group"`.
//
// Dos caminos, y por que hay dos
// ------------------------------
//
//   POR HASH. Se particiona la entrada por el hash de la clave de agrupacion,
//   escribiendo cada particion a disco. Como todas las filas de un grupo
//   comparten hash, caen en la misma particion: se puede agregar particion por
//   particion sin volver a mirar las demas. Si una particion no cabe en
//   memoria, se re-particiona con otra semilla.
//
//   POR SORT. Se ordena por la clave con el external sorting (#20) y se
//   recorre una vez cortando cuando la clave cambia. El sort es ESTABLE, asi
//   que las filas de un grupo salen juntas y no hace falta nada mas.
//
//   El enunciado solo pide uno. Estan los dos porque el 2.1.6 pide "analisis
//   comparativo entre las diferentes tecnicas implementadas", y con uno solo
//   esa comparacion se afirma en vez de medirse. Ademas cada uno gana en un
//   caso distinto:
//
//     hash   no necesita orden; una pasada sobre los datos si las particiones
//            caben. Es el mejor cuando hay muchos grupos chicos.
//     sort   la salida sale ORDENADA por la clave de agrupacion, asi que una
//            consulta con GROUP BY y ORDER BY por la misma columna paga un solo
//            ordenamiento. Y no depende de que el hash separe nada.
//
// El caso que el hash no puede resolver
// -------------------------------------
//
//   Si una particion no cabe en memoria y TODAS sus claves son iguales,
//   re-particionar no separa nada: es el mismo problema que las claves
//   repetidas del hash extensible (#18), donde la salida fue encadenar
//   overflow. Aqui la salida es mejor: se cae al camino por SORT, que no
//   depende de que el hash separe.
//
//   Por eso `Strategy::kAuto` no es una comodidad sino la opcion correcta por
//   omision: empieza por hash y cae a sort cuando el hash no alcanza. Elegir
//   `kHash` a mano es pedir que falle con `Unsupported` en ese caso, y existe
//   para que los benchmarks puedan medir el hash puro.
//
// Que devuelve
// ------------
//
//   Un `RecordSource` -- el mismo tipo que consume y produce el sort (#20) --
//   mas el `Schema` de esa salida, que NO es el de la entrada: un GROUP BY
//   produce filas con otra forma, `(clave, agregado, agregado, ...)`.
//   `output_schema()` lo da armado para que el planner (2.1.3) lo pase al
//   frontend sin inventarlo.
//
// El AVG no es SUM/COUNT
// ----------------------
//
//   Sumar 100 000 doubles de magnitudes distintas acumula error de redondeo, y
//   el 2.1.6 compara estos resultados contra PostgreSQL, que suma compensado.
//   Un AVG que devuelve 15,699999999999998 donde PostgreSQL dice 15,7 hace
//   quedar mal a la comparacion por una razon que no es del algoritmo. SUM y
//   AVG usan suma de Neumaier, que cuesta una resta y una suma mas por
//   elemento.

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

#include "quipudb/catalog/types.hpp"
#include "quipudb/external/external_sort.hpp"

namespace quipudb {

/// Funcion de agregacion, tal como aparece en la consulta.
enum class Aggregate : std::uint8_t {
  Count,  ///< COUNT(col). Cuenta filas; la columna se ignora.
  Sum,    ///< SUM(col). Solo sobre INT o DOUBLE.
  Min,    ///< MIN(col). Sobre cualquier tipo ordenable.
  Max,    ///< MAX(col).
  Avg,    ///< AVG(col). Solo sobre INT o DOUBLE; devuelve DOUBLE.
};

[[nodiscard]] std::string_view to_string(Aggregate a) noexcept;

/// Una agregacion pedida: que funcion, sobre que columna, y como se llama la
/// columna resultante.
struct AggregateSpec {
  Aggregate func = Aggregate::Count;
  std::size_t column = 0;  ///< posicion en el esquema de ENTRADA
  std::string name;        ///< nombre en el esquema de SALIDA; vacio lo genera

  static AggregateSpec count(std::string nombre = {}) {
    return AggregateSpec{Aggregate::Count, 0, std::move(nombre)};
  }
  static AggregateSpec of(Aggregate f, std::size_t col, std::string nombre = {}) {
    return AggregateSpec{f, col, std::move(nombre)};
  }
};

/// Agrupa un flujo por una columna y agrega, usando disco cuando no cabe en
/// memoria.
///
/// De un solo uso, igual que `ExternalSort`: se construye, se llama a
/// `grouped()` una vez y se lee el flujo. Mientras ese flujo viva, este objeto
/// tiene que seguir vivo.
class ExternalGroupBy {
 public:
  enum class Strategy : std::uint8_t {
    /// Empieza por hash y cae a sort si una particion no se deja separar. Es
    /// lo correcto por omision: siempre termina.
    kAuto,
    /// Solo hash. Lanza `Unsupported` si una particion no cabe ni tras
    /// re-particionar. Existe para medir el hash puro en los benchmarks.
    kHash,
    /// Solo sort. La salida sale ordenada por la clave de agrupacion.
    kSort,
  };

  /// Particiones minimas. Con menos de 2 no hay particionado que valga.
  static constexpr std::size_t kMinPartitions = 2;

  /// Vueltas minimas de re-particionado antes de empezar a comprobar si se
  /// avanza. Cuantas hacen falta depende de cuantos grupos haya, asi que el
  /// limite real no es este numero sino no haber avanzado: una cubeta que sale
  /// de una vuelta del mismo tamaño que entro no se va a separar nunca.
  static constexpr std::size_t kMaxRepartitions = 3;

  /// `schema` describe la entrada, `key_column` es la columna por la que se
  /// agrupa, y `aggregates` que se calcula. Una lista vacia equivale a un
  /// `SELECT DISTINCT`: devuelve las claves distintas y nada mas.
  ///
  /// `buffers` son PAGINAS de memoria, igual que en el sort (#20): es lo que
  /// fija cuantas particiones se abren a la vez y cuanto cabe en memoria.
  ///
  /// Lanza SchemaError si la columna de agrupacion o alguna de las agregadas no
  /// existe, si se pide SUM o AVG sobre una columna que no es numerica, o si
  /// `buffers` no alcanza.
  ExternalGroupBy(Schema schema, std::size_t key_column, std::vector<AggregateSpec> aggregates,
                  Strategy strategy = Strategy::kAuto,
                  std::size_t buffers = ExternalSort::kDefaultBuffers,
                  std::size_t page_size = kDefaultPageSize, std::filesystem::path dir = {});

  ~ExternalGroupBy();

  ExternalGroupBy(const ExternalGroupBy&) = delete;
  ExternalGroupBy& operator=(const ExternalGroupBy&) = delete;

  /// Esquema de las filas que devuelve `grouped()`: la columna de agrupacion
  /// seguida de una columna por agregacion. No es el esquema de la entrada.
  [[nodiscard]] const Schema& output_schema() const noexcept { return salida_; }

  /// Agrupa y devuelve el resultado como flujo.
  ///
  /// Con `kSort` (o cuando `kAuto` cae a sort) las filas salen ordenadas por la
  /// clave; con `kHash` salen en orden de particion, que no es ningun orden
  /// util. El planner no debe prometer orden salvo que sepa cual se uso, y
  /// `used()` se lo dice.
  [[nodiscard]] std::unique_ptr<RecordSource> grouped(RecordSource& entrada);

  // --- lo que el plan de ejecucion y el 2.1.6 leen -------------------------

  /// Estrategia que se termino usando. Puede no ser la pedida: `kAuto` cae a
  /// sort cuando el hash no alcanza.
  [[nodiscard]] Strategy used() const noexcept { return usada_; }

  /// Grupos distintos encontrados.
  [[nodiscard]] std::size_t groups() const noexcept { return grupos_; }

  /// Filas leidas de la entrada.
  [[nodiscard]] std::size_t rows() const noexcept { return filas_; }

  /// Particiones que creo la primera vuelta. 0 si se agrupo por sort o si todo
  /// cupo en memoria.
  [[nodiscard]] std::size_t partitions() const noexcept { return particiones_; }

  /// Veces que hubo que re-particionar. Es la medida de lo mal repartida que
  /// estaba la clave, y material para el informe.
  [[nodiscard]] std::size_t repartitions() const noexcept { return reparticiones_; }

  /// true si el hash no alcanzo y hubo que caer a sort.
  [[nodiscard]] bool fell_back() const noexcept { return cayo_; }

  [[nodiscard]] const OpStats& stats() const noexcept { return stats_; }
  void reset_stats() noexcept { stats_.reset(); }

 private:
  class Salida;
  struct Acumulador;

  void validar();
  [[nodiscard]] Schema construir_esquema_salida() const;

  /// Agrupa en memoria un lote de filas ya leidas. Es el nucleo de los dos
  /// caminos: el hash lo llama por particion, el sort por corrida de claves
  /// iguales.
  void acumular(const Record& fila, Acumulador& acc) const;
  [[nodiscard]] Record cerrar(const Key& clave, const Acumulador& acc) const;

  /// Agrupa por hash. Si una particion no cabe ni tras re-particionar,
  /// pone en `sin_agrupar` las filas que quedaban y marca `se_rindio`: la
  /// entrada ya se consumio y sin esas filas el fallback a sort seria
  /// imposible.
  [[nodiscard]] std::vector<Record> por_hash(RecordSource& entrada,
                                            std::vector<Record>* sin_agrupar,
                                            bool& se_rindio);
  [[nodiscard]] std::vector<Record> por_sort(RecordSource& entrada);

  Schema entrada_;
  Schema salida_;
  std::size_t key_column_ = 0;
  std::vector<AggregateSpec> aggs_;
  Strategy pedida_ = Strategy::kAuto;
  Strategy usada_ = Strategy::kAuto;
  std::size_t buffers_ = 0;
  std::size_t page_size_ = 0;
  std::filesystem::path dir_;

  std::size_t filas_ = 0;
  std::size_t grupos_ = 0;
  std::size_t particiones_ = 0;
  std::size_t reparticiones_ = 0;
  bool cayo_ = false;

  OpStats stats_;
};

}  // namespace quipudb
