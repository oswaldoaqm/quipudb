#include "quipudb/external/external_group.hpp"

#include <algorithm>
#include <cmath>
#include <deque>
#include <map>
#include <system_error>
#include <utility>

#include "quipudb/catalog/record_codec.hpp"
#include "quipudb/error.hpp"
#include "quipudb/index/extendible_hash.hpp"
#include "quipudb/storage/disk_manager.hpp"
#include "quipudb/storage/page.hpp"

namespace quipudb {

std::string_view to_string(Aggregate a) noexcept {
  switch (a) {
    case Aggregate::Count: return "COUNT";
    case Aggregate::Sum: return "SUM";
    case Aggregate::Min: return "MIN";
    case Aggregate::Max: return "MAX";
    case Aggregate::Avg: return "AVG";
  }
  return "?";
}

// ---------------------------------------------------------------------------
// Acumuladores
// ---------------------------------------------------------------------------

/// Suma de Neumaier. Sumar 100 000 doubles de magnitudes distintas acumula
/// error de redondeo, y el 2.1.6 compara estos resultados contra PostgreSQL,
/// que suma compensado. La correccion `c` guarda lo que se perdio en cada paso
/// y se aplica al final.
namespace {

class SumaCompensada {
 public:
  void add(double x) noexcept {
    const double t = suma_ + x;
    // Cual de los dos operandos es mas grande decide donde estan los bits que
    // se pierden.
    if (std::abs(suma_) >= std::abs(x)) {
      correccion_ += (suma_ - t) + x;
    } else {
      correccion_ += (x - t) + suma_;
    }
    suma_ = t;
  }
  [[nodiscard]] double value() const noexcept { return suma_ + correccion_; }

 private:
  double suma_ = 0.0;
  double correccion_ = 0.0;
};

/// Valor numerico de una celda, para SUM y AVG. El esquema ya garantizo que la
/// columna es INT o DOUBLE.
double numerico(const Value& v) {
  if (std::holds_alternative<std::int32_t>(v)) return static_cast<double>(std::get<std::int32_t>(v));
  return std::get<double>(v);
}

}  // namespace

struct ExternalGroupBy::Acumulador {
  std::uint64_t count = 0;
  std::vector<SumaCompensada> sumas;  // una por agregacion SUM o AVG
  std::vector<Value> extremos;        // una por agregacion MIN o MAX
  std::vector<bool> hay_extremo;
};

// ---------------------------------------------------------------------------
// Construccion y validacion
// ---------------------------------------------------------------------------

ExternalGroupBy::ExternalGroupBy(Schema schema, std::size_t key_column,
                                 std::vector<AggregateSpec> aggregates, Strategy strategy,
                                 std::size_t buffers, std::size_t page_size,
                                 std::filesystem::path dir)
    : entrada_(std::move(schema)),
      key_column_(key_column),
      aggs_(std::move(aggregates)),
      pedida_(strategy),
      usada_(strategy),
      buffers_(buffers),
      page_size_(page_size),
      dir_(std::move(dir)) {
  validar();
  salida_ = construir_esquema_salida();
  if (dir_.empty()) dir_ = std::filesystem::temp_directory_path();
}

ExternalGroupBy::~ExternalGroupBy() = default;

void ExternalGroupBy::validar() {
  if (key_column_ >= entrada_.columns.size()) {
    throw SchemaError("la columna de agrupacion " + std::to_string(key_column_) +
                      " no existe en " + entrada_.table_name);
  }
  if (buffers_ < ExternalSort::kMinBuffers) {
    throw SchemaError("hacen falta al menos " + std::to_string(ExternalSort::kMinBuffers) +
                      " buffers y se pidieron " + std::to_string(buffers_));
  }
  for (const auto& a : aggs_) {
    // COUNT no mira la columna, asi que no se le exige que exista.
    if (a.func == Aggregate::Count) continue;
    if (a.column >= entrada_.columns.size()) {
      throw SchemaError(std::string(to_string(a.func)) + ": la columna " +
                        std::to_string(a.column) + " no existe en " + entrada_.table_name);
    }
    const DataType t = entrada_.columns[a.column].type;
    if ((a.func == Aggregate::Sum || a.func == Aggregate::Avg) && t != DataType::Int &&
        t != DataType::Double) {
      throw SchemaError(std::string(to_string(a.func)) + " sobre la columna " +
                        entrada_.columns[a.column].name + ", que es " +
                        std::string(to_string(t)) + ": solo se puede sobre INT o DOUBLE");
    }
  }
}

Schema ExternalGroupBy::construir_esquema_salida() const {
  // Un GROUP BY produce filas con otra forma: la clave seguida de un valor por
  // agregacion. Armarlo aqui evita que el planner (2.1.3) lo invente.
  Schema s;
  s.table_name = entrada_.table_name + "_agrupado";
  s.key_column = 0;
  s.columns.push_back(entrada_.columns[key_column_]);

  for (std::size_t i = 0; i < aggs_.size(); ++i) {
    const auto& a = aggs_[i];
    Column c;
    c.name = a.name;
    if (c.name.empty()) {
      c.name = std::string(to_string(a.func)) + "_" +
               (a.func == Aggregate::Count ? "all" : entrada_.columns[a.column].name);
      // Dos agregaciones iguales sobre la misma columna colisionarian en el
      // nombre; el indice lo desambigua.
      for (std::size_t j = 0; j < i; ++j) {
        if (s.columns[j + 1].name == c.name) {
          c.name += "_" + std::to_string(i);
          break;
        }
      }
    }
    switch (a.func) {
      case Aggregate::Count:
        c.type = DataType::Int;
        break;
      case Aggregate::Avg:
        // Siempre DOUBLE: el promedio de enteros rara vez es entero.
        c.type = DataType::Double;
        break;
      case Aggregate::Sum:
        // Un SUM de INT puede desbordar un int32, asi que sube a DOUBLE. Lo
        // contrario obligaria a decidir entre truncar o lanzar a mitad de una
        // agregacion.
        c.type = DataType::Double;
        break;
      case Aggregate::Min:
      case Aggregate::Max:
        c.type = entrada_.columns[a.column].type;
        c.length = entrada_.columns[a.column].length;
        break;
    }
    s.columns.push_back(std::move(c));
  }
  return s;
}

// ---------------------------------------------------------------------------
// Acumulacion
// ---------------------------------------------------------------------------

void ExternalGroupBy::acumular(const Record& fila, Acumulador& acc) const {
  if (acc.sumas.empty() && acc.extremos.empty()) {
    acc.sumas.resize(aggs_.size());
    acc.extremos.resize(aggs_.size());
    acc.hay_extremo.assign(aggs_.size(), false);
  }
  ++acc.count;
  for (std::size_t i = 0; i < aggs_.size(); ++i) {
    const auto& a = aggs_[i];
    switch (a.func) {
      case Aggregate::Count:
        break;  // ya se conto arriba
      case Aggregate::Sum:
      case Aggregate::Avg:
        acc.sumas[i].add(numerico(fila[a.column]));
        break;
      case Aggregate::Min:
        if (!acc.hay_extremo[i] || compare(fila[a.column], acc.extremos[i]) < 0) {
          acc.extremos[i] = fila[a.column];
          acc.hay_extremo[i] = true;
        }
        break;
      case Aggregate::Max:
        if (!acc.hay_extremo[i] || compare(fila[a.column], acc.extremos[i]) > 0) {
          acc.extremos[i] = fila[a.column];
          acc.hay_extremo[i] = true;
        }
        break;
    }
  }
}

Record ExternalGroupBy::cerrar(const Key& clave, const Acumulador& acc) const {
  Record r;
  r.reserve(1 + aggs_.size());
  r.push_back(clave);
  for (std::size_t i = 0; i < aggs_.size(); ++i) {
    switch (aggs_[i].func) {
      case Aggregate::Count:
        r.emplace_back(static_cast<std::int32_t>(acc.count));
        break;
      case Aggregate::Sum:
        r.emplace_back(acc.sumas[i].value());
        break;
      case Aggregate::Avg:
        // count nunca es 0 aqui: un grupo existe porque tuvo al menos una fila.
        r.emplace_back(acc.sumas[i].value() / static_cast<double>(acc.count));
        break;
      case Aggregate::Min:
      case Aggregate::Max:
        r.push_back(acc.extremos[i]);
        break;
    }
  }
  return r;
}

// ---------------------------------------------------------------------------
// Camino por sort
// ---------------------------------------------------------------------------

std::vector<Record> ExternalGroupBy::por_sort(RecordSource& entrada) {
  // El sort del #20 es ESTABLE, asi que las filas de un grupo salen juntas:
  // agrupar es recorrer una vez y cortar cuando la clave cambia. La memoria
  // queda acotada a un acumulador, no a un grupo entero.
  ExternalSort sort(entrada_, key_column_, buffers_, page_size_, dir_);
  auto ordenado = sort.sorted(entrada);
  stats_ += sort.stats();

  std::vector<Record> salida;
  Record fila;
  Key actual;
  Acumulador acc;
  bool abierto = false;

  while (ordenado->next(fila)) {
    ++filas_;
    if (abierto && compare(fila[key_column_], actual) != 0) {
      salida.push_back(cerrar(actual, acc));
      acc = Acumulador{};
      abierto = false;
    }
    if (!abierto) {
      actual = fila[key_column_];
      abierto = true;
    }
    acumular(fila, acc);
  }
  if (abierto) salida.push_back(cerrar(actual, acc));

  grupos_ = salida.size();
  return salida;
}

// ---------------------------------------------------------------------------
// Camino por hash: particiones en disco
// ---------------------------------------------------------------------------

/// Un archivo de particion que se borra solo. Mismo patron que los runs del
/// #20: borrarlos al terminar no basta, porque una excepcion a media escritura
/// dejaria p archivos regados.
class ExternalGroupBy::Temporal {
 public:
  explicit Temporal(std::filesystem::path ruta) : ruta_(std::move(ruta)) {}
  ~Temporal() {
    // El destructor no puede lanzar: si el archivo ya no esta, no hay nada
    // mejor que hacer que seguir.
    std::error_code ec;
    std::filesystem::remove(ruta_, ec);
  }
  Temporal(const Temporal&) = delete;
  Temporal& operator=(const Temporal&) = delete;

  [[nodiscard]] const std::filesystem::path& path() const noexcept { return ruta_; }
  [[nodiscard]] std::size_t rows() const noexcept { return filas_; }
  void add_row() noexcept { ++filas_; }

 private:
  std::filesystem::path ruta_;
  std::size_t filas_ = 0;
};

namespace {

/// Escribe registros a una particion, de a una pagina. El layout es el de un
/// run del #20: registros serializados uno tras otro y `record_count` en la
/// cabecera.
///
/// Aqui esta el nucleo de lo que este arreglo corrige: la memoria que ocupa
/// una particion mientras se escribe es UNA pagina, no sus filas.
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

/// Lee una particion de a una pagina.
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

/// Recorre varias particiones seguidas como si fueran un flujo solo. Es lo que
/// alimenta al fallback por sort: cuando el hash se rinde, las filas originales
/// no estan en memoria -- estan en los archivos de la primera vuelta -- y desde
/// aqui se releen sin materializar nada.
class ExternalGroupBy::FuenteDeParticiones final : public RecordSource {
 public:
  FuenteDeParticiones(std::vector<std::shared_ptr<Temporal>> partes, Schema esquema,
                      std::size_t page_size, OpStats& stats)
      : partes_(std::move(partes)),
        codec_(std::move(esquema)),
        page_size_(page_size),
        stats_(&stats) {}

  bool next(Record& out) override {
    for (;;) {
      if (lector_ && lector_->next(out)) return true;
      lector_.reset();
      while (i_ < partes_.size() && partes_[i_]->rows() == 0) ++i_;
      if (i_ >= partes_.size()) return false;
      lector_ = std::make_unique<LectorDeParticion>(partes_[i_++]->path(), codec_, page_size_,
                                                    *stats_);
    }
  }

 private:
  std::vector<std::shared_ptr<Temporal>> partes_;
  RecordCodec codec_;
  std::size_t page_size_;
  OpStats* stats_;
  std::size_t i_ = 0;
  std::unique_ptr<LectorDeParticion> lector_;
};

std::uintmax_t ExternalGroupBy::temp_bytes() const {
  std::uintmax_t total = 0;
  for (const auto& t : vivos_) {
    std::error_code ec;
    const auto n = std::filesystem::file_size(t->path(), ec);
    if (!ec) total += n;
  }
  return total;
}

std::filesystem::path ExternalGroupBy::nueva_ruta() {
  return dir_ / ("quipudb_group_" + std::to_string(reinterpret_cast<std::uintptr_t>(this)) +
                 "_" + std::to_string(serie_++) + ".part");
}

std::vector<std::shared_ptr<ExternalGroupBy::Temporal>> ExternalGroupBy::repartir(
    RecordSource& entrada, std::size_t p, std::size_t vuelta) {
  const Column& col = entrada_.columns[key_column_];
  const RecordCodec codec{entrada_};
  const std::size_t por_pagina =
      std::max<std::size_t>(1, (page_size_ - Page::kHeaderSize) / codec.size());

  std::vector<std::shared_ptr<Temporal>> partes;
  std::vector<std::unique_ptr<EscritorDeParticion>> escritores;
  partes.reserve(p);
  escritores.reserve(p);
  for (std::size_t i = 0; i < p; ++i) {
    partes.push_back(std::make_shared<Temporal>(nueva_ruta()));
    // Se registran ANTES de escribir: si esto lanza a media vuelta, el
    // destructor ya los conoce y los borra. Registrarlos al final dejaria
    // archivos regados justo en el caso que importa.
    vivos_.push_back(partes.back());
    escritores.push_back(std::make_unique<EscritorDeParticion>(partes.back()->path(), codec,
                                                               page_size_, por_pagina, stats_));
  }

  Record r;
  while (entrada.next(r)) {
    if (vuelta == 0) {
      entrada_.validate(r);
      ++filas_;
    }
    const std::uint64_t h = ExtendibleHash::hash_of(col, r[key_column_]);
    // Mezclar con la vuelta cambia el reparto sin romper lo unico que no se
    // puede romper: que dos claves iguales sigan cayendo juntas.
    const std::uint64_t mezclado = vuelta == 0 ? h : ((h >> (8 * vuelta)) ^ h);
    const std::size_t destino = mezclado % p;
    escritores[destino]->write(r);
    partes[destino]->add_row();
  }
  for (auto& e : escritores) e->close();
  return partes;
}

std::vector<Record> ExternalGroupBy::por_hash(RecordSource& entrada, bool& se_rindio) {
  // Una particion por buffer disponible, menos uno para leer la entrada.
  const std::size_t p = std::max(kMinPartitions, buffers_ - 1);
  particiones_ = p;

  // Cuantos acumuladores caben en memoria. Un acumulador es la clave mas un
  // punado de contadores, bastante mas chico que una fila, pero se acota con la
  // misma unidad -- paginas -- para que `buffers` signifique lo mismo aqui que
  // en el sort (#20).
  const RecordCodec codec{entrada_};
  const std::size_t por_pagina =
      std::max<std::size_t>(1, (page_size_ - Page::kHeaderSize) / codec.size());
  const std::size_t tope_grupos = std::max<std::size_t>(1, buffers_ * por_pagina);

  // Primera vuelta: la entrada se reparte en p archivos. A partir de aqui la
  // entrada ya no existe, y las filas viven en disco.
  nivel0_ = repartir(entrada, p, 0);

  // Cada particion se agrega por separado: todas las filas de un grupo
  // comparten hash, asi que estan en el mismo archivo y no hay que mirar los
  // demas. Eso es lo que hace que el hash no necesite ordenar nada.
  //
  // La cola puede crecer: una particion que no cabe se re-particiona y sus
  // trozos vuelven a la cola, un nivel mas abajo.
  struct Pendiente {
    std::shared_ptr<Temporal> archivo;
    std::size_t vuelta;      // cuantas veces se re-particiono ya
    std::size_t tamano_padre;  // filas del archivo del que salio
  };
  std::deque<Pendiente> cola;
  for (auto& t : nivel0_) cola.push_back(Pendiente{t, 0, 0});

  se_rindio = false;
  std::vector<Record> salida;

  while (!cola.empty()) {
    Pendiente actual = std::move(cola.front());
    cola.pop_front();
    if (actual.archivo->rows() == 0) continue;

    // Lo que tiene que caber en memoria son los GRUPOS, no las filas: un grupo
    // ocupa un acumulador de unas decenas de bytes. Por eso se agrega al vuelo
    // y se mira cuantos grupos distintos van apareciendo, en vez de mirar
    // cuantas filas trae la particion.
    std::map<Key, Acumulador, KeyLess> acc;
    bool desbordo = false;
    {
      LectorDeParticion lector(actual.archivo->path(), codec, page_size_, stats_);
      Record r;
      while (lector.next(r)) {
        const auto it = acc.find(r[key_column_]);
        if (it == acc.end() && acc.size() >= tope_grupos) {
          desbordo = true;
          break;
        }
        acumular(r, it == acc.end() ? acc[r[key_column_]] : it->second);
      }
    }
    if (!desbordo) {
      for (const auto& [clave, a] : acc) salida.push_back(cerrar(clave, a));
      continue;
    }
    acc.clear();

    // Demasiados grupos distintos para esta particion: se re-particiona con
    // otra mezcla para repartirlos entre varias.
    //
    // Cuantas vueltas hacen falta depende de cuantos grupos haya, asi que el
    // limite no es un contador fijo sino no haber avanzado: si una particion
    // sale de una vuelta con exactamente el mismo tamano que entro, esa mezcla
    // no separo nada y las siguientes tampoco lo haran.
    if (actual.vuelta >= kMaxRepartitions && actual.archivo->rows() == actual.tamano_padre) {
      if (pedida_ == Strategy::kHash) {
        throw Unsupported("una particion con mas de " + std::to_string(tope_grupos) +
                          " grupos distintos no cabe en memoria y re-particionarla no la "
                          "separa. Usa Strategy::kAuto o kSort");
      }
      // Se rinde. No hace falta rescatar filas a memoria: las originales siguen
      // en los archivos de la primera vuelta, que `nivel0_` mantiene vivos, y
      // el fallback los relee desde ahi.
      //
      // Hay que rehacer la agregacion ENTERA, tambien la de las particiones ya
      // cerradas: agregar es asociativo salvo para COUNT y AVG, que contarian 1
      // en vez de N si se reagregaran sobre filas ya agregadas.
      se_rindio = true;
      salida.clear();
      return {};
    }

    LectorDeParticion lector(actual.archivo->path(), codec, page_size_, stats_);
    class FuenteDeUna final : public RecordSource {
     public:
      explicit FuenteDeUna(LectorDeParticion& l) : l_(&l) {}
      bool next(Record& out) override { return l_->next(out); }

     private:
      LectorDeParticion* l_;
    } fuente{lector};

    ++reparticiones_;
    const std::size_t tamano = actual.archivo->rows();
    auto trozos = repartir(fuente, p, actual.vuelta + 1);
    for (auto& t : trozos) {
      if (t->rows() > 0) cola.push_back(Pendiente{t, actual.vuelta + 1, tamano});
    }
  }

  grupos_ = salida.size();
  return salida;
}

// ---------------------------------------------------------------------------
// Salida
// ---------------------------------------------------------------------------

class ExternalGroupBy::Salida final : public RecordSource {
 public:
  explicit Salida(std::vector<Record> rs) : rs_(std::move(rs)) {}
  bool next(Record& out) override {
    if (i_ >= rs_.size()) return false;
    out = std::move(rs_[i_++]);
    return true;
  }

 private:
  std::vector<Record> rs_;
  std::size_t i_ = 0;
};

std::unique_ptr<RecordSource> ExternalGroupBy::grouped(RecordSource& entrada) {
  filas_ = 0;
  grupos_ = 0;
  particiones_ = 0;
  reparticiones_ = 0;
  cayo_ = false;
  usada_ = pedida_;

  if (pedida_ == Strategy::kSort) {
    return std::make_unique<Salida>(por_sort(entrada));
  }

  bool se_rindio = false;
  auto porHash = por_hash(entrada, se_rindio);

  if (!se_rindio) {
    usada_ = Strategy::kHash;
    return std::make_unique<Salida>(std::move(porHash));
  }

  // El hash no pudo: una particion sigue teniendo mas grupos de los que caben y
  // re-particionarla ya no la separa. La entrada ya se consumio, pero las filas
  // originales no se perdieron: estan en los archivos de la primera vuelta, que
  // `nivel0_` mantiene vivos. Se releen desde ahi y se ordenan.
  //
  // Ordenar siempre funciona porque no depende de que el hash separe nada, y
  // releer de disco es lo que permite que el fallback no cueste memoria: la
  // version anterior conservaba TODAS las filas en RAM por si tenia que llegar
  // hasta aqui.
  cayo_ = true;
  usada_ = Strategy::kSort;
  filas_ = 0;
  grupos_ = 0;
  auto fuente = std::make_unique<FuenteDeParticiones>(nivel0_, entrada_, page_size_, stats_);
  return std::make_unique<Salida>(por_sort(*fuente));
}

}  // namespace quipudb
