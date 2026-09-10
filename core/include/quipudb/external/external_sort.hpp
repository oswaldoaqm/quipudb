#pragma once

// External Sorting: k-way merge para ORDER BY (issue #20).
//
// Ordena mas registros de los que caben en memoria, que es lo que el enunciado
// pide en la viñeta de External Algorithms de 2.1.2. El ORDER BY de la 2.1.3
// (#28) se apoya en esto, y el plan de ejecucion (ADR 0002) ya reservo el paso
// `op: "sort"` con `structure: "external_sort"`.
//
// El algoritmo, en dos fases
// --------------------------
//
//   Fase 1 (generar runs). Se lee la entrada de a B paginas -- todo lo que la
//   memoria permite --, se ordena ese trozo EN memoria y se escribe a disco
//   como un "run" ya ordenado. La entrada queda partida en N/B runs.
//
//   Fase 2 (fusion k-way). Se abren k = B-1 runs a la vez, se reserva la
//   pagina que sobra para la salida, y se van sacando registros del menor de
//   los k frentes con un heap. Cada pasada reduce los runs por un factor k,
//   asi que hacen falta ceil(log_k(N/B)) pasadas.
//
//   Por que k = B-1 y no B: una pagina tiene que quedar libre para acumular la
//   salida. Escribir de a un registro convertiria el merge en una escritura de
//   pagina por registro.
//
// El costo, que es lo que el 2.1.6 mide
// -------------------------------------
//
//   Con N paginas de datos y B buffers:
//
//     fase 1        2N          (leer todo, escribir todo)
//     cada pasada   2N
//     pasadas       ceil(log_{B-1}(N/B))
//     TOTAL         2N * (1 + ceil(log_{B-1}(N/B)))
//
//   De ahi que B se cuente en PAGINAS y no en megabytes: la formula solo tiene
//   sentido en paginas, y es la que el informe tiene que explicar. `stats()`
//   devuelve las paginas realmente leidas y escritas, para contrastar la
//   formula contra la medicion en vez de suponer que coinciden.
//
// Que ordena: un flujo, no una tabla
// ----------------------------------
//
//   Recibe un `RecordSource` y devuelve otro. No recibe un `TableFile` porque
//   el GROUP BY (#21) y el JOIN (#22) van a necesitar ordenar cosas que no son
//   tablas: la salida de un filtro, el resultado de un join. Atarlo a una tabla
//   obligaria a rehacerlo dos veces.
//
//   `RecordCursor` (#56) no sirve como esa entrada: exige `rid()`, y un
//   registro que sale de un sort no tiene direccion fisica. `RecordSource` es
//   la parte de `RecordCursor` que no depende de vivir en una tabla, y
//   `source_of()` adapta cualquier `TableFile` a ella.
//
// Que devuelve: un flujo, no un vector
// ------------------------------------
//
//   El ADR 0002 dibuja `sort` como un paso con un hijo que le entrega su
//   salida: un pipeline. Devolver un `vector<Record>` con 100 000 registros
//   dentro seria haber ordenado en disco para materializarlo todo al final,
//   que es justo lo que el issue quiere evitar. La salida se lee de a un
//   registro y la memoria queda acotada a B paginas de principio a fin.
//
// Los archivos temporales
// -----------------------
//
//   Cada pasada escribe runs nuevos y descarta los de la pasada anterior. Se
//   sigue el patron que ya uso la reorganizacion del archivo secuencial (#12):
//   escribir aparte y no tocar nada de lo que todavia se esta leyendo.
//
//   La diferencia es que aqui hay VARIOS temporales vivos a la vez, asi que
//   borrarlos al terminar no basta: si una excepcion interrumpe a media
//   pasada, quedarian regados. Por eso los temporales viven en un objeto que
//   los borra en su destructor, y la prueba
//   `NoDejaArchivosTemporalesNiSiquieraSiFalla` lo comprueba lanzando a
//   proposito desde la fuente.

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

#include "quipudb/catalog/table.hpp"
#include "quipudb/catalog/types.hpp"

namespace quipudb {

/// Flujo de registros de a uno. Es la entrada y la salida de los external
/// algorithms.
///
/// Es lo mismo que `RecordCursor` menos `rid()`: un registro que sale de un
/// sort o de un join no tiene direccion fisica que ofrecer, y exigirsela
/// obligaria a inventar un RID falso. `source_of(tabla)` adapta un `TableFile`
/// a esta interfaz.
class RecordSource {
 public:
  virtual ~RecordSource() = default;

  /// Escribe el siguiente registro en `out` y devuelve true; false cuando ya
  /// no quedan.
  virtual bool next(Record& out) = 0;
};

/// Adapta una tabla a `RecordSource`, recorriendola con su cursor.
[[nodiscard]] std::unique_ptr<RecordSource> source_of(TableFile& tabla);

/// Adapta un vector ya materializado. Para pruebas y para trozos chicos que
/// alguien ya tiene en memoria.
[[nodiscard]] std::unique_ptr<RecordSource> source_of(std::vector<Record> registros);

/// Ordena un flujo de registros por una columna, usando disco cuando no cabe
/// en memoria.
///
/// El objeto es de un solo uso: se construye, se llama a `sorted()` una vez y
/// se lee el flujo que devuelve. Mientras ese flujo viva, este objeto tiene
/// que seguir vivo: es el dueño de los archivos temporales.
class ExternalSort {
 public:
  /// Buffers minimos. Con menos de 3 no hay k-way merge posible: uno para la
  /// salida y al menos dos frentes que fusionar. Con 2 el merge seria binario
  /// y con 1 no habria merge.
  static constexpr std::size_t kMinBuffers = 3;

  /// Buffers por omision. Con paginas de 4 KB son 256 KB, que ordena 100 000
  /// registros en dos pasadas.
  static constexpr std::size_t kDefaultBuffers = 64;

  /// `schema` describe los registros del flujo y `key_column` es la columna
  /// por la que se ordena -- que no tiene por que ser la clave primaria: un
  /// ORDER BY ordena por cualquier columna.
  ///
  /// `buffers` es la cantidad de PAGINAS de memoria disponibles, no megabytes.
  /// Es lo que fija el tamaño de los runs y el orden de la fusion, y lo que
  /// hace interpretable la formula de costo. Bajarlo a proposito es lo que
  /// permite forzar varias pasadas con pocos datos, que es lo que hacen las
  /// pruebas.
  ///
  /// `dir` es donde viven los archivos temporales. Si esta vacio se usa el
  /// directorio temporal del sistema.
  ///
  /// Lanza SchemaError si `key_column` no existe, si `buffers` es menor que
  /// `kMinBuffers`, o si un registro no entra en una pagina.
  ExternalSort(Schema schema, std::size_t key_column,
               std::size_t buffers = kDefaultBuffers,
               std::size_t page_size = kDefaultPageSize,
               std::filesystem::path dir = {});

  ~ExternalSort();

  ExternalSort(const ExternalSort&) = delete;
  ExternalSort& operator=(const ExternalSort&) = delete;

  /// Ordena `entrada` y devuelve el resultado como flujo.
  ///
  /// Si todo cabe en `buffers` paginas no toca el disco: se ordena en memoria y
  /// se devuelve. Ese caso importa porque es el comun en tablas chicas, y
  /// `passes()` devolviendo 0 es lo que lo distingue en el plan de ejecucion.
  ///
  /// El flujo devuelto deja de valer si este objeto se destruye.
  [[nodiscard]] std::unique_ptr<RecordSource> sorted(RecordSource& entrada);

  // --- lo que el plan de ejecucion y el 2.1.6 leen -------------------------

  /// Registros ordenados.
  [[nodiscard]] std::size_t size() const noexcept { return total_; }

  /// Runs que produjo la fase 1.
  [[nodiscard]] std::size_t runs() const noexcept { return runs_iniciales_; }

  /// Pasadas de fusion. 0 significa que todo cupo en memoria y no hubo disco.
  [[nodiscard]] std::size_t passes() const noexcept { return pasadas_; }

  /// Buffers con los que se construyo.
  [[nodiscard]] std::size_t buffers() const noexcept { return buffers_; }

  /// Registros que entran en una pagina.
  [[nodiscard]] std::size_t records_per_page() const noexcept { return por_pagina_; }

  /// Paginas leidas y escritas de verdad. Es lo que el 2.1.6 contrasta contra
  /// la formula 2N(1 + ceil(log_{B-1}(N/B))).
  [[nodiscard]] const OpStats& stats() const noexcept { return stats_; }
  void reset_stats() noexcept { stats_.reset(); }

  /// Costo en paginas que la teoria predice para N paginas de datos. Con los
  /// mismos buffers con los que se construyo este objeto.
  [[nodiscard]] std::uint64_t predicted_pages(std::uint64_t paginas_de_datos) const noexcept;

  /// Bytes que ocupan los temporales vivos. Cero cuando todo cupo en memoria.
  [[nodiscard]] std::uintmax_t temp_bytes() const;

 private:
  class Run;
  class MergeSource;
  class MemorySource;

  /// Un archivo temporal que se borra solo. Borrarlos al terminar no basta:
  /// una excepcion a media pasada dejaria varios regados.
  class Temporal {
   public:
    explicit Temporal(std::filesystem::path p) : ruta_(std::move(p)) {}
    ~Temporal();
    Temporal(const Temporal&) = delete;
    Temporal& operator=(const Temporal&) = delete;

    [[nodiscard]] const std::filesystem::path& path() const noexcept { return ruta_; }

   private:
    std::filesystem::path ruta_;
  };

  /// Lee hasta `buffers_` paginas de registros, los ordena en memoria y los
  /// devuelve. Vacio cuando la entrada se agoto.
  std::vector<Record> leer_trozo(RecordSource& entrada);

  /// Escribe registros ya ordenados como un run nuevo.
  std::shared_ptr<Temporal> escribir_run(const std::vector<Record>& registros);

  /// Fusiona hasta `buffers_ - 1` runs en uno solo.
  std::shared_ptr<Temporal> fusionar(const std::vector<std::shared_ptr<Temporal>>& entradas);

  [[nodiscard]] std::filesystem::path nueva_ruta();
  [[nodiscard]] bool menor(const Record& a, const Record& b) const;

  Schema schema_;
  std::size_t key_column_ = 0;
  std::size_t buffers_ = 0;
  std::size_t page_size_ = 0;
  std::size_t por_pagina_ = 0;
  std::filesystem::path dir_;

  std::size_t total_ = 0;
  std::size_t runs_iniciales_ = 0;
  std::size_t pasadas_ = 0;
  std::uint64_t serie_ = 0;  // para nombrar los temporales

  std::vector<std::shared_ptr<Temporal>> vivos_;
  OpStats stats_;
};

}  // namespace quipudb
