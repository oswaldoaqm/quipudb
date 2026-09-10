#include "quipudb/external/external_join.hpp"

#include <algorithm>
#include <map>
#include <system_error>
#include <utility>

#include "quipudb/catalog/record_codec.hpp"
#include "quipudb/error.hpp"
#include "quipudb/index/extendible_hash.hpp"
#include "quipudb/storage/disk_manager.hpp"
#include "quipudb/storage/page.hpp"

namespace quipudb {

/// `structure` del paso `join` cuando se resolvio por hash. Es el valor que el
/// ADR 0002 ya reservo para los external algorithms que particionan.
static constexpr std::string_view kEstructuraHash = "external_hash";

// ---------------------------------------------------------------------------
// Sondas
// ---------------------------------------------------------------------------

namespace {

/// Costo medido de una sonda, en paginas, por estructura. Sale de
/// `docs/medir-condicion-join.cpp` corrido sobre 10 000 filas con paginas de
/// 4 KB: son las paginas leidas de verdad divididas por las filas externas.
///
/// No se calcula a partir de la altura del arbol porque `Index` no la expone y
/// porque el numero que importa incluye la lectura del registro por RID, que
/// no es parte del indice. Si alguna estructura cambia de forma, se vuelve a
/// correr el programa y se actualiza esta tabla; ese es justamente el motivo
/// de que el programa se quede en el repo.
std::size_t costo_medido(std::string_view estructura) noexcept {
  if (estructura == kind::kExtendibleHash) return 2;   // ~1 pagina de bucket + 1 de datos
  if (estructura == kind::kBPlusUnclustered) return 4;  // ~3 de altura + 1 de datos
  if (estructura == kind::kBPlusClustered) return 3;    // el registro vive en la hoja
  if (estructura == kind::kSequential) return 3;        // busqueda binaria por grupos
  return 4;
}

/// Sonda por indice secundario: RIDs en el indice, registro en la tabla.
class SondaPorIndice final : public JoinProbe {
 public:
  SondaPorIndice(Index& ix, TableFile& datos) : ix_(&ix), datos_(&datos) {}

  std::vector<Record> matches(const Key& key) override {
    std::vector<Record> salida;
    for (const RID rid : ix_->search(key)) {
      // Un RID que el indice conoce y la tabla no resuelve es una fila
      // borrada cuya entrada quedo colgada. No es un error de esta operacion:
      // se salta, igual que hace el resto del motor con un slot libre.
      if (auto r = datos_->read(rid)) salida.push_back(std::move(*r));
    }
    return salida;
  }

  [[nodiscard]] const Schema& schema() const noexcept override { return datos_->schema(); }
  [[nodiscard]] std::string_view structure() const noexcept override { return ix_->kind(); }
  [[nodiscard]] std::size_t rows() const override { return datos_->size(); }
  [[nodiscard]] std::size_t probe_cost() const noexcept override {
    return costo_medido(ix_->kind());
  }

  [[nodiscard]] const OpStats& stats() const noexcept override {
    // Las paginas del indice y las de la tabla se suman: las dos se pagan por
    // sonda, y el paso `join` del plan las reporta juntas. El ADR 0002 las
    // separa cuando son dos pasos (`index_search` + `fetch`); aqui son un
    // paso, porque el sondeo no es un recorrido que se pueda dibujar aparte.
    acumulado_ = ix_->stats();
    acumulado_ += datos_->stats();
    return acumulado_;
  }

  void reset_stats() noexcept override {
    ix_->reset_stats();
    datos_->reset_stats();
    acumulado_.reset();
  }

 private:
  Index* ix_;
  TableFile* datos_;
  mutable OpStats acumulado_;
};

/// Sonda por clave primaria: la propia tabla resuelve.
class SondaPorTabla final : public JoinProbe {
 public:
  explicit SondaPorTabla(TableFile& tabla) : tabla_(&tabla) {}

  std::vector<Record> matches(const Key& key) override { return tabla_->search(key); }

  [[nodiscard]] const Schema& schema() const noexcept override { return tabla_->schema(); }
  [[nodiscard]] std::string_view structure() const noexcept override { return tabla_->kind(); }
  [[nodiscard]] std::size_t rows() const override { return tabla_->size(); }
  [[nodiscard]] std::size_t probe_cost() const noexcept override {
    return costo_medido(tabla_->kind());
  }
  [[nodiscard]] const OpStats& stats() const noexcept override { return tabla_->stats(); }
  void reset_stats() noexcept override { tabla_->reset_stats(); }

 private:
  TableFile* tabla_;
};

}  // namespace

std::unique_ptr<JoinProbe> probe_of(Index& ix, TableFile& datos) {
  return std::make_unique<SondaPorIndice>(ix, datos);
}

std::unique_ptr<JoinProbe> probe_of(TableFile& tabla) {
  // Un heap file no esta organizado por su clave: su `search` recorre la tabla
  // entera, asi que sondearlo una vez por fila externa es un producto
  // cartesiano con otro nombre. Dejarlo pasar convertiria la "estrategia por
  // indice" en la peor de las tres.
  if (tabla.kind() == kind::kHeap) {
    throw SchemaError(
        "un heap file no esta ordenado por su clave primaria: sondearlo por fila "
        "externa recorre la tabla entera. Para " +
        tabla.schema().table_name +
        " hace falta un indice secundario (probe_of(Index&, TableFile&)) o una "
        "organizacion por clave (sequential, bplus_clustered)");
  }
  return std::make_unique<SondaPorTabla>(tabla);
}

// ---------------------------------------------------------------------------
// Temporales
// ---------------------------------------------------------------------------

/// Un archivo de particion que se borra solo. Mismo patron que el #20:
/// borrarlos al terminar no basta, porque una excepcion a medio particionar
/// dejaria 2p archivos regados.
class ExternalJoin::Temporal {
 public:
  explicit Temporal(std::filesystem::path p) : ruta_(std::move(p)) {}
  ~Temporal() {
    std::error_code ec;
    std::filesystem::remove(ruta_, ec);
  }
  Temporal(const Temporal&) = delete;
  Temporal& operator=(const Temporal&) = delete;

  [[nodiscard]] const std::filesystem::path& path() const noexcept { return ruta_; }

  [[nodiscard]] std::size_t rows() const noexcept { return filas_; }
  void add_rows(std::size_t n) noexcept { filas_ += n; }

 private:
  std::filesystem::path ruta_;
  std::size_t filas_ = 0;
};

namespace {

/// Escribe registros a un archivo de particion, de a una pagina.
///
/// El layout es el mismo que el de un run del #20: registros serializados uno
/// tras otro y `record_count` en la cabecera. Se escribe una vez y se lee en
/// orden, asi que no hace falta nada mas.
class EscritorDeParticion {
 public:
  EscritorDeParticion(const std::filesystem::path& ruta, const RecordCodec& codec,
                      std::size_t page_size, std::size_t por_pagina, OpStats& stats)
      : disco_(ruta, page_size),
        codec_(&codec),
        pagina_(page_size),
        por_pagina_(por_pagina),
        stats_(&stats) {}

  void write(const Record& r) {
    codec_->encode(r, pagina_.body().subspan(en_pagina_ * codec_->size(), codec_->size()));
    ++en_pagina_;
    if (en_pagina_ == por_pagina_) volcar();
  }

  void close() {
    if (en_pagina_ > 0) volcar();
    disco_.flush();
  }

 private:
  void volcar() {
    pagina_.set_record_count(static_cast<std::uint16_t>(en_pagina_));
    const PageId id = disco_.allocate_page();
    disco_.write_page(id, pagina_);
    ++stats_->pages_written;
    pagina_.clear();
    en_pagina_ = 0;
  }

  DiskManager disco_;
  const RecordCodec* codec_;
  Page pagina_;
  std::size_t por_pagina_;
  OpStats* stats_;
  std::size_t en_pagina_ = 0;
};

/// Lee una particion de a una pagina. Se puede rebobinar, que es lo que
/// necesita el recorrido por bloques: la particion externa se vuelve a leer
/// entera por cada bloque de la interna.
class LectorDeParticion {
 public:
  LectorDeParticion(const std::filesystem::path& ruta, const RecordCodec& codec,
                    std::size_t page_size, OpStats& stats)
      : disco_(ruta, page_size), codec_(&codec), pagina_(page_size), stats_(&stats) {}

  bool next(Record& out) {
    while (slot_ >= en_pagina_) {
      if (proxima_ > disco_.page_count()) return false;
      disco_.read_page(proxima_, pagina_);
      ++stats_->pages_read;
      ++proxima_;
      en_pagina_ = pagina_.record_count();
      slot_ = 0;
    }
    out = codec_->decode(pagina_.read_bytes(slot_ * codec_->size(), codec_->size()));
    ++slot_;
    ++stats_->records_examined;
    return true;
  }

  void rewind() noexcept {
    proxima_ = 1;
    en_pagina_ = 0;
    slot_ = 0;
  }

 private:
  DiskManager disco_;
  const RecordCodec* codec_;
  Page pagina_;
  OpStats* stats_;
  PageId proxima_ = 1;
  std::uint16_t en_pagina_ = 0;
  std::uint16_t slot_ = 0;
};

}  // namespace

// ---------------------------------------------------------------------------
// Construccion y validacion
// ---------------------------------------------------------------------------

ExternalJoin::ExternalJoin(Schema izquierda, std::size_t col_izquierda, Schema derecha,
                           std::size_t col_derecha, Strategy strategy, std::size_t buffers,
                           std::size_t page_size, std::filesystem::path dir)
    : izq_(std::move(izquierda)),
      der_(std::move(derecha)),
      col_izq_(col_izquierda),
      col_der_(col_derecha),
      pedida_(strategy),
      usada_(strategy),
      buffers_(buffers),
      page_size_(page_size),
      dir_(std::move(dir)) {
  validar();
  salida_ = construir_esquema_salida();

  const std::size_t cuerpo = page_size_ - Page::kHeaderSize;
  const auto caben = [&](const Schema& s, const char* que) {
    const RecordCodec codec{s};
    const std::size_t n = cuerpo / codec.size();
    if (n == 0) {
      throw SchemaError(std::string("una fila de ") + que + " ocupa " +
                        std::to_string(codec.size()) + " bytes y no entra en una pagina de " +
                        std::to_string(page_size_));
    }
    return n;
  };
  por_pagina_izq_ = caben(izq_, izq_.table_name.c_str());
  por_pagina_der_ = caben(der_, der_.table_name.c_str());
  por_pagina_ = caben(salida_, "la salida");

  if (dir_.empty()) dir_ = std::filesystem::temp_directory_path();
}

ExternalJoin::~ExternalJoin() = default;

void ExternalJoin::validar() const {
  if (col_izq_ >= izq_.columns.size()) {
    throw SchemaError("la columna de join " + std::to_string(col_izq_) + " no existe en " +
                      izq_.table_name + ", que tiene " +
                      std::to_string(izq_.columns.size()));
  }
  if (col_der_ >= der_.columns.size()) {
    throw SchemaError("la columna de join " + std::to_string(col_der_) + " no existe en " +
                      der_.table_name + ", que tiene " +
                      std::to_string(der_.columns.size()));
  }
  // Comparar un INT con un VARCHAR no da 0 coincidencias: da un orden entre
  // tipos distintos (`compare` lo define para tener un orden total) y por lo
  // tanto un resultado silenciosamente incorrecto. Mejor que no compile la
  // consulta.
  if (izq_.columns[col_izq_].type != der_.columns[col_der_].type) {
    throw SchemaError("no se puede juntar " + izq_.table_name + "." +
                      izq_.columns[col_izq_].name + " (" +
                      std::string(to_string(izq_.columns[col_izq_].type)) + ") con " +
                      der_.table_name + "." + der_.columns[col_der_].name + " (" +
                      std::string(to_string(der_.columns[col_der_].type)) + ")");
  }
  if (buffers_ < kMinBuffers) {
    throw SchemaError("hacen falta al menos " + std::to_string(kMinBuffers) +
                      " buffers para particionar (uno para leer y al menos dos "
                      "particiones) y se pidieron " + std::to_string(buffers_));
  }
}

Schema ExternalJoin::construir_esquema_salida() const {
  Schema s;
  s.table_name = izq_.table_name + "_" + der_.table_name;
  // Un join no tiene clave primaria. Ver la nota de `output_schema()`.
  s.key_column = 0;
  s.columns.reserve(izq_.columns.size() + der_.columns.size());

  // Solo se prefijan las que colisionan: las cabeceras del caso comun se
  // quedan como estaban, que es lo que el panel de resultados (#36) muestra.
  const auto colisiona = [this](const std::string& nombre) {
    const bool en_izq = std::any_of(izq_.columns.begin(), izq_.columns.end(),
                                    [&](const Column& c) { return c.name == nombre; });
    const bool en_der = std::any_of(der_.columns.begin(), der_.columns.end(),
                                    [&](const Column& c) { return c.name == nombre; });
    return en_izq && en_der;
  };

  for (const auto& c : izq_.columns) {
    Column n = c;
    if (colisiona(c.name)) n.name = izq_.table_name + "." + c.name;
    s.columns.push_back(std::move(n));
  }
  for (const auto& c : der_.columns) {
    Column n = c;
    if (colisiona(c.name)) n.name = der_.table_name + "." + c.name;
    s.columns.push_back(std::move(n));
  }
  return s;
}

void ExternalJoin::reiniciar() noexcept {
  filas_izq_ = 0;
  filas_der_ = 0;
  filas_salida_ = 0;
  particiones_ = 0;
  por_bloques_ = 0;
  usada_ = pedida_;
}

Record ExternalJoin::unir(const Record& izq, const Record& der) const {
  Record r;
  r.reserve(izq.size() + der.size());
  r.insert(r.end(), izq.begin(), izq.end());
  r.insert(r.end(), der.begin(), der.end());
  return r;
}

std::filesystem::path ExternalJoin::nueva_ruta(std::string_view lado, std::size_t i) {
  return dir_ / ("quipudb_join_" + std::to_string(reinterpret_cast<std::uintptr_t>(this)) +
                 "_" + std::string(lado) + "_" + std::to_string(i) + "_" +
                 std::to_string(serie_++) + ".part");
}

std::uintmax_t ExternalJoin::temp_bytes() const {
  std::uintmax_t total = 0;
  for (const auto& t : vivos_) {
    std::error_code ec;
    const auto n = std::filesystem::file_size(t->path(), ec);
    if (!ec) total += n;
  }
  return total;
}

std::uint64_t ExternalJoin::predicted_pages(std::uint64_t paginas_izq,
                                            std::uint64_t paginas_der) noexcept {
  // Particionar lee todo y escribe todo (2N); sondear vuelve a leer todo (N).
  return 3ULL * (paginas_izq + paginas_der);
}

bool ExternalJoin::conviene_index_nested(std::size_t filas_externas,
                                         std::uint64_t paginas_externas,
                                         std::uint64_t paginas_internas,
                                         std::size_t costo_sonda) noexcept {
  // Sin filas externas no hay nada que juntar; da igual la estrategia, y hash
  // join es la que no necesita sonda.
  if (filas_externas == 0) return false;
  const std::uint64_t inl = static_cast<std::uint64_t>(filas_externas) * costo_sonda;
  return inl < predicted_pages(paginas_externas, paginas_internas);
}

// ---------------------------------------------------------------------------
// Particionado
// ---------------------------------------------------------------------------

std::vector<std::shared_ptr<ExternalJoin::Temporal>> ExternalJoin::particionar(
    RecordSource& entrada, const Schema& esquema, std::size_t columna, std::string_view lado,
    std::size_t& filas) {
  const RecordCodec codec{esquema};
  const std::size_t cuerpo = page_size_ - Page::kHeaderSize;
  const std::size_t por_pagina = cuerpo / codec.size();
  if (por_pagina == 0) {
    throw SchemaError("un registro de " + esquema.table_name + " ocupa " +
                      std::to_string(codec.size()) + " bytes y no entra en una pagina de " +
                      std::to_string(page_size_));
  }

  std::vector<std::shared_ptr<Temporal>> partes;
  std::vector<std::unique_ptr<EscritorDeParticion>> escritores;
  partes.reserve(particiones_);
  escritores.reserve(particiones_);
  for (std::size_t i = 0; i < particiones_; ++i) {
    partes.push_back(std::make_shared<Temporal>(nueva_ruta(lado, i)));
    // Los temporales se registran ANTES de escribir nada: si el particionado
    // lanza a media escritura, el destructor de ExternalJoin ya los conoce y
    // los borra. Registrarlos al final dejaria archivos regados justo en el
    // caso que importa.
    vivos_.push_back(partes.back());
    escritores.push_back(std::make_unique<EscritorDeParticion>(partes.back()->path(), codec,
                                                               page_size_, por_pagina, stats_));
  }

  const Column& col = esquema.columns[columna];
  Record r;
  while (entrada.next(r)) {
    esquema.validate(r);
    ++filas;
    // La misma funcion en los dos lados: es lo unico que garantiza que dos
    // filas que casan caigan en particiones homologas. Cambiarla en un lado y
    // no en el otro perderia coincidencias en silencio.
    const std::uint64_t h = ExtendibleHash::hash_of(col, r[columna]);
    const std::size_t p = h % particiones_;
    escritores[p]->write(r);
    partes[p]->add_rows(1);
  }
  for (auto& e : escritores) e->close();
  return partes;
}

// ---------------------------------------------------------------------------
// Salida del hash join
// ---------------------------------------------------------------------------

/// Recorre las particiones de a una y, dentro de cada una, de a un bloque de
/// lo que cabe en memoria.
///
/// El estado es el de un bucle triple aplanado: particion, bloque de la
/// derecha, fila de la izquierda. Se aplana porque `RecordSource::next` tiene
/// que devolver UNA fila y volver, y no puede quedarse dentro de tres bucles.
class ExternalJoin::SalidaHash final : public RecordSource {
 public:
  SalidaHash(ExternalJoin& j, std::vector<std::shared_ptr<Temporal>> izq,
             std::vector<std::shared_ptr<Temporal>> der)
      : j_(&j),
        izq_(std::move(izq)),
        der_(std::move(der)),
        codec_izq_(j.izq_),
        codec_der_(j.der_) {
    // Cuantas filas de la derecha caben en memoria a la vez. Es el mismo
    // criterio que el sort (#20): buffers en PAGINAS por filas por pagina.
    const std::size_t cuerpo = j.page_size_ - Page::kHeaderSize;
    const std::size_t por_pagina_der = std::max<std::size_t>(1, cuerpo / codec_der_.size());
    tope_ = std::max<std::size_t>(1, j.buffers_ * por_pagina_der);
  }

  bool next(Record& out) override {
    for (;;) {
      // 3. Coincidencias pendientes de la fila izquierda que ya se leyo.
      if (pendientes_ < coincidencias_.size()) {
        out = j_->unir(fila_izq_, *coincidencias_[pendientes_++]);
        ++j_->filas_salida_;
        ++j_->stats_.records_returned;
        return true;
      }
      // 2. Siguiente fila de la particion izquierda contra el bloque cargado.
      if (lector_izq_ && lector_izq_->next(fila_izq_)) {
        const auto rango = bloque_.equal_range(fila_izq_[j_->col_izq_]);
        coincidencias_.clear();
        for (auto it = rango.first; it != rango.second; ++it) {
          coincidencias_.push_back(&it->second);
        }
        pendientes_ = 0;
        continue;
      }
      // 1. Siguiente bloque, o siguiente particion.
      if (!avanzar()) return false;
    }
  }

 private:
  /// Carga el siguiente bloque de la particion derecha actual y rebobina la
  /// izquierda. Devuelve false cuando ya no quedan particiones.
  bool avanzar() {
    for (;;) {
      if (lector_der_) {
        bloque_.clear();
        Record r;
        std::size_t n = 0;
        bool hubo = false;
        while (n < tope_ && lector_der_->next(r)) {
          bloque_.emplace(r[j_->col_der_], r);
          ++n;
          hubo = true;
        }
        if (hubo) {
          ++bloques_de_esta_;
          // Se cuenta la particion, no los bloques: `blocked_partitions()` es
          // "cuantas no cupieron", asi que solo suma al aparecer el SEGUNDO
          // bloque. Contarla en el primero marcaria como desbordada a toda
          // particion, incluidas las que cupieron de sobra.
          if (bloques_de_esta_ == 2) ++j_->por_bloques_;
          // La particion izquierda se vuelve a leer entera por cada bloque de
          // la derecha. Con un solo bloque -- el caso normal -- se lee una vez
          // y esto es un hash join clasico.
          if (lector_izq_) lector_izq_->rewind();
          coincidencias_.clear();
          pendientes_ = 0;
          return true;
        }
        lector_der_.reset();
        lector_izq_.reset();
      }
      if (i_ >= j_->particiones_) return false;
      const std::size_t i = i_++;
      // Una particion derecha vacia no puede casar con nada: se salta sin
      // abrir la izquierda.
      if (der_[i]->rows() == 0 || izq_[i]->rows() == 0) continue;
      lector_der_ = std::make_unique<LectorDeParticion>(der_[i]->path(), codec_der_,
                                                        j_->page_size_, j_->stats_);
      lector_izq_ = std::make_unique<LectorDeParticion>(izq_[i]->path(), codec_izq_,
                                                        j_->page_size_, j_->stats_);
      bloques_de_esta_ = 0;
    }
  }

  ExternalJoin* j_;
  std::vector<std::shared_ptr<Temporal>> izq_;
  std::vector<std::shared_ptr<Temporal>> der_;
  RecordCodec codec_izq_;
  RecordCodec codec_der_;
  std::size_t tope_ = 1;

  std::size_t i_ = 0;
  std::size_t bloques_de_esta_ = 0;
  std::unique_ptr<LectorDeParticion> lector_der_;
  std::unique_ptr<LectorDeParticion> lector_izq_;
  std::multimap<Key, Record, KeyLess> bloque_;

  Record fila_izq_;
  std::vector<const Record*> coincidencias_;
  std::size_t pendientes_ = 0;
};

// ---------------------------------------------------------------------------
// Salida del index nested loop
// ---------------------------------------------------------------------------

class ExternalJoin::SalidaIndexNested final : public RecordSource {
 public:
  SalidaIndexNested(ExternalJoin& j, RecordSource& izq, JoinProbe& sonda)
      : j_(&j), izq_(&izq), sonda_(&sonda) {}

  bool next(Record& out) override {
    for (;;) {
      if (pendientes_ < coincidencias_.size()) {
        out = j_->unir(fila_izq_, coincidencias_[pendientes_++]);
        ++j_->filas_salida_;
        ++j_->stats_.records_returned;
        return true;
      }
      if (!izq_->next(fila_izq_)) return false;
      j_->izq_.validate(fila_izq_);
      ++j_->filas_izq_;
      coincidencias_ = sonda_->matches(fila_izq_[j_->col_izq_]);
      j_->filas_der_ += coincidencias_.size();
      pendientes_ = 0;
    }
  }

 private:
  ExternalJoin* j_;
  RecordSource* izq_;
  JoinProbe* sonda_;
  Record fila_izq_;
  std::vector<Record> coincidencias_;
  std::size_t pendientes_ = 0;
};

// ---------------------------------------------------------------------------
// joined()
// ---------------------------------------------------------------------------

std::unique_ptr<RecordSource> ExternalJoin::hash_join(RecordSource& izquierda,
                                                      RecordSource& derecha) {
  usada_ = Strategy::kHash;
  estructura_ = kEstructuraHash;

  // Una particion por buffer disponible, menos uno para leer la entrada. Es el
  // mismo reparto que el group by (#21).
  particiones_ = std::max(kMinPartitions, buffers_ - 1);

  auto pizq = particionar(izquierda, izq_, col_izq_, "izq", filas_izq_);
  auto pder = particionar(derecha, der_, col_der_, "der", filas_der_);
  return std::make_unique<SalidaHash>(*this, std::move(pizq), std::move(pder));
}

std::unique_ptr<RecordSource> ExternalJoin::joined(RecordSource& izquierda,
                                                   RecordSource& derecha) {
  if (pedida_ == Strategy::kIndexNested) {
    throw Unsupported(
        "se pidio index nested loop pero no se dio una sonda: usa el joined() de cuatro "
        "argumentos, o Strategy::kHash / kAuto");
  }
  reiniciar();
  return hash_join(izquierda, derecha);
}

std::unique_ptr<RecordSource> ExternalJoin::joined(RecordSource& izquierda, JoinProbe& sonda,
                                                   RecordSource& derecha,
                                                   std::size_t filas_izquierda) {
  reiniciar();

  bool con_indice = pedida_ == Strategy::kIndexNested;
  if (pedida_ == Strategy::kAuto) {
    const auto paginas = [](std::size_t filas, std::size_t por_pagina) -> std::uint64_t {
      if (por_pagina == 0) return filas;
      return (filas + por_pagina - 1) / por_pagina;
    };
    con_indice = conviene_index_nested(filas_izquierda,
                                       paginas(filas_izquierda, por_pagina_izq_),
                                       paginas(sonda.rows(), por_pagina_der_),
                                       sonda.probe_cost());
  }

  if (con_indice) {
    usada_ = Strategy::kIndexNested;
    estructura_ = sonda.structure();
    return std::make_unique<SalidaIndexNested>(*this, izquierda, sonda);
  }
  return hash_join(izquierda, derecha);
}

}  // namespace quipudb
