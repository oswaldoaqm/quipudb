#pragma once

// Extendible Hashing sobre paginas de disco (issue #18).
//
// Es la tercera estructura de indexacion que pide el enunciado (2.1.2), y la
// primera que NO mantiene un orden. Resuelve la busqueda por igualdad en dos
// accesos -- uno al directorio y uno al bucket -- sin importar cuantas claves
// haya, y a cambio no puede responder un rango: claves contiguas caen en
// buckets sin relacion entre si. Esa asimetria es justamente lo que el
// analisis comparativo del 2.1.6 tiene que medir.
//
// Igual que `BPlusTree`, esta clase es la MAQUINARIA y no el indice: guarda
// `payload_size` bytes opacos por clave y no sabe que son. El adaptador que
// implementa `Index` (con `search`, `remove` y `supports_range() == false`)
// llega en el #19, del mismo modo que `BPlusUnclusteredIndex` (#16) se apoya
// en el arbol del #14.
//
// Estructura
// ----------
//
//   La pagina 0 es la cabecera del archivo (DiskManager) y su area meta
//   guarda la profundidad global, la cabeza del directorio y la free list.
//
//   directorio (cadena de paginas)        bucket (cadena de paginas)
//   [PageId][PageId][PageId]...           [4 bytes profundidad local]
//   next -> siguiente pagina              [clave][payload]
//   record_count = entradas usadas        [clave][payload]
//                                         ...
//                                         next -> pagina de overflow
//                                         record_count = entradas vivas
//
//   El directorio tiene 2^profundidad_global entradas y varias pueden apuntar
//   al MISMO bucket: un bucket con profundidad local L es compartido por
//   2^(global-L) entradas, las que coinciden en sus L bits bajos.
//
//   El directorio se mantiene ademas en memoria (`dir_`) porque se consulta en
//   cada operacion y solo cambia al duplicarse. Es una copia, no la fuente:
//   toda modificacion se escribe a disco antes de volver.
//
// Split y duplicacion
// -------------------
//
//   Al llenarse un bucket de profundidad local L se mira el bit L del hash:
//
//     L <  global   se parte el bucket en dos (L+1) y se reparten las entradas;
//                   el directorio no cambia de tamano, solo repunta la mitad
//                   de las entradas que compartian ese bucket.
//     L == global   no hay bit L en el directorio para distinguirlos, asi que
//                   primero se DUPLICA el directorio (global+1) y despues se
//                   parte.
//
//   Duplicar el directorio no crea buckets: las dos copias de cada entrada
//   apuntan al mismo, y solo el que se parte deja de compartirse. Por eso el
//   costo de crecer se paga sobre el directorio y no sobre los datos, que es
//   la diferencia con el hashing estatico.
//
// Claves repetidas y overflow
// ---------------------------
//
//   Un indice secundario admite claves repetidas (el contrato de `Index` lo
//   dice: el par (clave, RID) simplemente se agrega), y ahi el split deja de
//   servir: dos claves iguales tienen el MISMO hash, asi que ningun bit las
//   separa y partir el bucket lo deja igual de lleno. Duplicar el directorio
//   en ese caso no arregla nada y lo hace crecer sin freno.
//
//   Por eso antes de partir se comprueba si las entradas difieren en algun bit
//   por ENCIMA de los que ya comparten. Si no difieren en ninguno, ningun
//   split futuro las separara, y el bucket crece por una PAGINA DE OVERFLOW
//   encadenada por el campo `next`, igual que el area de overflow del archivo
//   secuencial (#10).
//
//   La pregunta se hace sobre todos los bits altos y no solo sobre el que toca
//   partir ahora: con buckets de 4 entradas, una de cada ocho veces las cuatro
//   coinciden en ese bit por casualidad, y rendirse ahi llenaria de overflow
//   una estructura con claves perfectamente separables un bit mas arriba.
//
//   Es la degradacion conocida de esta estructura, y hay que medirla: una
//   busqueda deja de costar dos accesos y pasa a costar dos mas la cadena.
//   `overflow_pages()` esta para que el 2.1.6 lo reporte en vez de suponerlo.
//
// El hash
// -------
//
//   La clave se serializa con el mismo codec que los registros (#7) y sobre
//   esos bytes se aplica FNV-1a de 64 bits. El directorio se indexa con los
//   bits BAJOS del hash, que es lo que permite que duplicarlo conserve el
//   reparto: la entrada j y la j + 2^d siguen apuntando al mismo bucket.
//
//   Lo que NO lleva es un finalizador de mezcla (el de splitmix64 y parecidos).
//   La objecion clasica es que FNV termina en una multiplicacion y los bits
//   bajos de un producto dependen solo de los bits bajos de sus factores. Es
//   cierto, pero para claves de tamano FIJO eso no las agrupa: multiplicar por
//   un impar es una biyeccion modulo 2^k, asi que el byte que varia sigue
//   dando bits bajos distintos. Se midio, con 2 000 claves sobre 512 buckets,
//   la carga maxima de un bucket:
//
//     patron de clave     hash = la clave    FNV-1a    FNV-1a + splitmix
//     int 1..n                        5           5                 10
//     int i*4                        16           8                 10
//     int i*512                    2000           5                 11
//     varchar(12)                     -           9                 10
//     double i*1,5                    -           9                 12
//
//   El finalizador reparte como el azar (Poisson), y FNV a secas reparte mejor
//   que el azar en los patrones que de verdad aparecen en una clave de tabla:
//   correlativos, con paso fijo, texto con prefijo comun. Agregarlo empeoraba
//   la carga maxima en todos los casos medidos, asi que no esta.
//
//   La columna "hash = la clave" es por que hace falta un hash: con un paso
//   que es potencia de dos, usar el valor tal cual manda las 2 000 claves al
//   mismo bucket. Es el caso que cubre la prueba de reparto.
//
//   Dos claves que `compare` considera iguales tienen que hashear igual, y
//   los bytes crudos no lo garantizan: `0.0` y `-0.0` son iguales para
//   `compare` y difieren en el bit de signo. `hash_of` los normaliza antes de
//   mezclar (y hace lo mismo con NaN, que `Schema::validate` ya no deja
//   entrar en un registro pero que puede llegar como clave suelta).
//
// Lo que NO hace este issue
// -------------------------
//
//   Buscar, borrar y fusionar buckets hermanos son del #19, junto con el
//   adaptador `Index`. Aqui se construye la estructura y se comprueba que
//   ninguna clave se pierde al crecer, que es lo que `check_invariants()`
//   verifica y lo que las pruebas comparan contra un `unordered_multimap`.

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <span>
#include <string>
#include <utility>
#include <vector>

#include "quipudb/catalog/record_codec.hpp"
#include "quipudb/catalog/table.hpp"
#include "quipudb/storage/disk_manager.hpp"
#include "quipudb/storage/page.hpp"

namespace quipudb {

/// Recorre las entradas del hash de a una, bucket por bucket y siguiendo las
/// cadenas de overflow. Deja de valer en cuanto la estructura se modifica.
///
/// El orden es el de bucket, que es lo que el contrato de `Index::scan`
/// promete para un hash: no hay orden de clave que ofrecer.
class HashEntryCursor {
 public:
  virtual ~HashEntryCursor() = default;
  virtual bool next(Key& key, std::vector<std::byte>& payload) = 0;

  /// Pagina y posicion de la entrada que acaba de devolver `next`. Solo vale
  /// inmediatamente despues de un `next` que devolvio true. Es lo que el #19
  /// necesita para borrar una entrada concreta sin volver a buscarla.
  [[nodiscard]] virtual RID position() const = 0;
};

class ExtendibleHash {
 public:
  /// Version del formato en disco. Si cambia el layout de las paginas o del
  /// area meta, sube: reabrir un archivo viejo tiene que ser un IoError y no
  /// una lectura silenciosamente mal interpretada.
  static constexpr std::uint32_t kMetaVersion = 1;

  /// Bytes que ocupa la profundidad local al inicio del body de un bucket.
  static constexpr std::size_t kBucketHeader = 4;

  /// Tope defensivo de la profundidad global. 2^32 entradas de directorio ya
  /// son 16 GB, asi que llegar aqui significa que el hash degenero, no que
  /// hagan falta mas bits. No deberia alcanzarse: un split que no separa nada
  /// va a overflow en vez de duplicar el directorio.
  static constexpr std::size_t kMaxGlobalDepth = 32;

  /// Abre el archivo si existe, o lo crea con un unico bucket de profundidad
  /// local 0 y un directorio de una entrada.
  ///
  /// `bucket_capacity` es cuantas entradas entran en una pagina de bucket; 0
  /// significa las maximas que quepan. Fijarla a mano es lo que permite forzar
  /// splits y duplicaciones con pocas claves, que es lo que hacen las pruebas.
  /// Lanza SchemaError si la capacidad pedida no entra o es menor que 2 (con
  /// una entrada por bucket un split nunca deja sitio).
  ExtendibleHash(std::filesystem::path path, Column key_column, std::size_t payload_size,
                 std::size_t page_size = kDefaultPageSize, std::size_t bucket_capacity = 0);

  /// Hash de una clave. Publico porque las pruebas y los benchmarks necesitan
  /// saber a que bucket deberia ir una clave sin depender de la
  /// implementacion interna.
  [[nodiscard]] static std::uint64_t hash_of(const Column& column, const Key& key);

  // --- forma de la estructura ---------------------------------------------

  [[nodiscard]] const Column& key_column() const noexcept { return key_column_; }
  [[nodiscard]] std::size_t key_size() const noexcept { return key_size_; }
  [[nodiscard]] std::size_t payload_size() const noexcept { return payload_size_; }

  /// Entradas que entran en una pagina de bucket.
  [[nodiscard]] std::size_t bucket_capacity() const noexcept { return capacity_; }

  /// Bits del hash que usa el directorio.
  [[nodiscard]] std::size_t global_depth() const noexcept { return global_depth_; }

  /// Entradas del directorio: siempre 2^global_depth().
  [[nodiscard]] std::size_t directory_size() const noexcept { return dir_.size(); }

  /// Buckets distintos. Es menor o igual que `directory_size()`: varias
  /// entradas del directorio comparten bucket mientras su profundidad local
  /// sea menor que la global.
  [[nodiscard]] std::size_t bucket_count() const;

  /// Pagina del bucket al que apunta esa entrada del directorio.
  [[nodiscard]] PageId bucket_at(std::size_t dir_index) const;

  /// Profundidad local del bucket al que apunta esa entrada del directorio.
  [[nodiscard]] std::size_t local_depth(std::size_t dir_index);

  /// Paginas de overflow vivas. Cero mientras los splits alcancen; crece solo
  /// cuando hay claves repetidas o colisiones totales.
  [[nodiscard]] std::size_t overflow_pages();

  /// Paginas que quedaron libres y esperan reuso.
  [[nodiscard]] std::size_t free_pages();

  /// Entradas (clave, payload) guardadas.
  [[nodiscard]] std::size_t size() const noexcept { return static_cast<std::size_t>(count_); }

  [[nodiscard]] PageId page_count() const noexcept { return disk_.page_count(); }
  [[nodiscard]] std::uintmax_t file_size() const { return disk_.file_size(); }

  // --- operaciones ---------------------------------------------------------

  /// Agrega la entrada. Admite claves repetidas: un indice secundario indexa
  /// una columna cualquiera, asi que aqui no hay DuplicateKey.
  ///
  /// Lanza SchemaError si la clave no es del tipo de la columna o si el
  /// payload no mide exactamente `payload_size()`.
  void insert(const Key& key, std::span<const std::byte> payload);

  /// Todas las entradas, en orden de bucket. Materializa todo: para recorridos
  /// grandes, `entries()`.
  [[nodiscard]] std::vector<std::pair<Key, std::vector<std::byte>>> scan();

  /// Recorrido incremental con memoria acotada a una pagina.
  ///
  /// Recorre el directorio y visita cada bucket una sola vez: un bucket de
  /// profundidad local L aparece en 2^(global-L) entradas y la primera es la
  /// unica con indice menor que 2^L. Eso evita tener que recordar cuales ya
  /// se visitaron, a cambio de leer una pagina por entrada del directorio en
  /// vez de una por bucket.
  [[nodiscard]] std::unique_ptr<HashEntryCursor> entries();

  /// Comprueba las invariantes y devuelve la primera que falle, o una cadena
  /// vacia si esta todo bien. Lo que de verdad prueba que ninguna clave se
  /// perdio es la combinacion de dos: cada entrada esta en el bucket que le
  /// toca por sus bits bajos, y la cantidad total coincide con `size()`.
  [[nodiscard]] std::string check_invariants();

  [[nodiscard]] const OpStats& stats() const noexcept { return stats_; }
  void reset_stats() noexcept { stats_.reset(); }

  void flush();

 private:
  class Cursor;

  /// Una entrada sacada de un bucket, con su hash ya calculado.
  struct Entrada {
    std::uint64_t hash = 0;
    std::vector<std::byte> bytes;  // clave seguida de payload
  };

  struct Meta {
    std::uint32_t version = kMetaVersion;
    std::uint32_t key_type = 0;
    std::uint32_t key_size = 0;
    std::uint32_t payload_size = 0;
    std::uint32_t capacity = 0;
    std::uint32_t global_depth = 0;
    std::uint32_t dir_head = kInvalidPage;
    std::uint32_t dir_pages = 0;
    std::uint32_t free_head = kInvalidPage;
    std::uint32_t reserved = 0;
    std::uint64_t count = 0;
  };
  static_assert(sizeof(Meta) <= DiskManager::kMetaSize);

  void load_meta();
  void save_meta();

  void fetch(PageId id);
  void store(PageId id);

  /// Saca una pagina de la free list, o agrega una al archivo.
  PageId allocate();
  void free_page(PageId id);

  /// Crea una pagina de bucket vacia con esa profundidad local.
  PageId new_bucket(std::size_t local);

  [[nodiscard]] std::size_t dir_index(std::uint64_t hash) const noexcept;
  [[nodiscard]] std::size_t dir_per_page() const noexcept;

  void load_directory();
  void save_directory();

  /// Profundidad local leyendo la pagina primaria del bucket.
  [[nodiscard]] std::size_t depth_of(PageId bucket);

  /// Fija la cantidad de entradas de la pagina cargada en `scratch_` y
  /// recalcula su espacio libre fisico. Ojo: `free_space` cuenta bytes, y el
  /// tope logico es `capacity_`, que puede ser menor que los que entran.
  void set_entradas(std::uint16_t n);

  /// Escribe la entrada en la primera pagina de la cadena con sitio. Devuelve
  /// false si la cadena entera esta llena. No parte ni encadena nada.
  bool append(PageId bucket, std::span<const std::byte> entrada);

  /// Igual que `append`, pero si no hay sitio encadena una pagina de overflow.
  /// Es lo que usa el reparto tras un split, que no puede volver a partir.
  void append_forzado(PageId bucket, std::span<const std::byte> entrada);

  /// Lee todas las entradas de la cadena, con su hash, sin modificar nada.
  std::vector<Entrada> recolectar(PageId bucket);

  /// Deja la cadena con la pagina primaria vacia y sin overflow; las paginas
  /// de overflow van a la free list. No lee las entradas: quien las necesite
  /// llama antes a `recolectar`.
  void limpiar(PageId bucket);

  /// Engancha una pagina de overflow vacia al final de la cadena.
  void encadenar_overflow(PageId bucket);

  /// Hace sitio en el bucket de `dir_index`: lo parte (duplicando el
  /// directorio si hace falta) o le encadena una pagina de overflow cuando
  /// partirlo no separaria nada.
  void crecer(std::size_t index, PageId bucket);

  void duplicar_directorio();

  /// Clave de una entrada serializada.
  [[nodiscard]] Key clave_de(std::span<const std::byte> entrada) const;

  DiskManager disk_;
  Column key_column_;
  std::size_t key_size_ = 0;
  std::size_t payload_size_ = 0;
  std::size_t entry_size_ = 0;
  std::size_t capacity_ = 0;

  std::size_t global_depth_ = 0;
  std::vector<PageId> dir_;
  PageId dir_head_ = kInvalidPage;
  std::uint32_t dir_pages_ = 0;
  PageId free_head_ = kInvalidPage;
  std::uint64_t count_ = 0;

  Page scratch_;
  OpStats stats_;
};

}  // namespace quipudb
