#include "quipudb/external/external_sort.hpp"

#include <algorithm>
#include <cmath>
#include <queue>
#include <system_error>
#include <utility>

#include "quipudb/catalog/record_codec.hpp"
#include "quipudb/error.hpp"
#include "quipudb/storage/disk_manager.hpp"
#include "quipudb/storage/page.hpp"

namespace quipudb {

// ---------------------------------------------------------------------------
// Fuentes
// ---------------------------------------------------------------------------

namespace {

/// Adapta el cursor de una tabla. El cursor tiene `rid()` y `RecordSource` no
/// lo pide: se descarta, que es justamente lo que permite encadenar un sort
/// despues de un scan sin inventar direcciones fisicas.
class FuenteDeTabla final : public RecordSource {
 public:
  explicit FuenteDeTabla(TableFile& t) : cursor_(t.cursor()) {}
  bool next(Record& out) override { return cursor_->next(out); }

 private:
  std::unique_ptr<RecordCursor> cursor_;
};

class FuenteDeVector final : public RecordSource {
 public:
  explicit FuenteDeVector(std::vector<Record> rs) : rs_(std::move(rs)) {}
  bool next(Record& out) override {
    if (i_ >= rs_.size()) return false;
    out = rs_[i_++];
    return true;
  }

 private:
  std::vector<Record> rs_;
  std::size_t i_ = 0;
};

}  // namespace

std::unique_ptr<RecordSource> source_of(TableFile& tabla) {
  return std::make_unique<FuenteDeTabla>(tabla);
}

std::unique_ptr<RecordSource> source_of(std::vector<Record> registros) {
  return std::make_unique<FuenteDeVector>(std::move(registros));
}

// ---------------------------------------------------------------------------
// Temporales
// ---------------------------------------------------------------------------

ExternalSort::Temporal::~Temporal() {
  // El destructor no puede lanzar: si el archivo ya no esta, o el sistema no
  // deja borrarlo, no hay nada mejor que hacer que seguir.
  std::error_code ec;
  std::filesystem::remove(ruta_, ec);
}

// ---------------------------------------------------------------------------
// Un run en disco
// ---------------------------------------------------------------------------

namespace {

/// Layout de una pagina de run: los registros serializados uno tras otro,
/// `record_count` en la cabecera dice cuantos. No hace falta nada mas: un run
/// se escribe entero de una vez y se lee entero en orden.
///
/// Se lee de a UNA pagina, que es lo que hace que fusionar k runs cueste k
/// paginas de memoria y no k runs enteros.
class LectorDeRun {
 public:
  LectorDeRun(const std::filesystem::path& ruta, const RecordCodec& codec,
              std::size_t page_size, OpStats& stats)
      : disco_(ruta, page_size), codec_(&codec), pagina_(page_size), stats_(&stats) {}

  /// Siguiente registro del run, o false si se agoto.
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
    ++stats_->records_returned;
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

// ---------------------------------------------------------------------------
// Construccion
// ---------------------------------------------------------------------------

ExternalSort::ExternalSort(Schema schema, std::size_t key_column, std::size_t buffers,
                           std::size_t page_size, std::filesystem::path dir)
    : schema_(std::move(schema)),
      key_column_(key_column),
      buffers_(buffers),
      page_size_(page_size),
      dir_(std::move(dir)) {
  if (key_column_ >= schema_.columns.size()) {
    throw SchemaError("la columna " + std::to_string(key_column_) + " no existe en " +
                      schema_.table_name + ", que tiene " +
                      std::to_string(schema_.columns.size()));
  }
  if (buffers_ < kMinBuffers) {
    throw SchemaError("hacen falta al menos " + std::to_string(kMinBuffers) +
                      " buffers para un k-way merge (uno para la salida y dos frentes "
                      "que fusionar), y se pidieron " + std::to_string(buffers_));
  }

  const RecordCodec codec{schema_};
  const std::size_t cuerpo = page_size_ - Page::kHeaderSize;
  por_pagina_ = cuerpo / codec.size();
  if (por_pagina_ == 0) {
    throw SchemaError("un registro de " + std::to_string(codec.size()) +
                      " bytes no entra en una pagina de " + std::to_string(page_size_));
  }
  if (dir_.empty()) dir_ = std::filesystem::temp_directory_path();
}

ExternalSort::~ExternalSort() = default;

std::filesystem::path ExternalSort::nueva_ruta() {
  return dir_ / ("quipudb_sort_" + std::to_string(reinterpret_cast<std::uintptr_t>(this)) + "_" +
                 std::to_string(serie_++) + ".run");
}

bool ExternalSort::menor(const Record& a, const Record& b) const {
  return compare(a[key_column_], b[key_column_]) < 0;
}

std::uint64_t ExternalSort::predicted_pages(std::uint64_t n) const noexcept {
  if (n == 0) return 0;
  const auto b = static_cast<double>(buffers_);
  const auto runs = std::ceil(static_cast<double>(n) / b);
  if (runs <= 1.0) return 2 * n;  // solo la fase 1
  const auto pasadas = std::ceil(std::log(runs) / std::log(b - 1.0));
  return static_cast<std::uint64_t>(2.0 * static_cast<double>(n) * (1.0 + pasadas));
}

std::uintmax_t ExternalSort::temp_bytes() const {
  std::uintmax_t total = 0;
  for (const auto& t : vivos_) {
    std::error_code ec;
    const auto n = std::filesystem::file_size(t->path(), ec);
    if (!ec) total += n;
  }
  return total;
}

// ---------------------------------------------------------------------------
// Fase 1: generar runs
// ---------------------------------------------------------------------------

std::vector<Record> ExternalSort::leer_trozo(RecordSource& entrada) {
  // Se lee exactamente lo que cabe en los buffers: B paginas por
  // `por_pagina_` registros. Ese numero es el que define el tamaño de un run,
  // y por eso los buffers se cuentan en paginas.
  const std::size_t tope = buffers_ * por_pagina_;
  std::vector<Record> trozo;
  trozo.reserve(tope);

  Record r;
  while (trozo.size() < tope && entrada.next(r)) {
    schema_.validate(r);
    trozo.push_back(r);
  }
  std::stable_sort(trozo.begin(), trozo.end(),
                   [this](const Record& a, const Record& b) { return menor(a, b); });
  return trozo;
}

std::shared_ptr<ExternalSort::Temporal> ExternalSort::escribir_run(
    const std::vector<Record>& registros) {
  auto tmp = std::make_shared<Temporal>(nueva_ruta());
  const RecordCodec codec{schema_};

  DiskManager disco(tmp->path(), page_size_);
  Page pagina(page_size_);
  std::size_t en_pagina = 0;

  auto cerrar = [&] {
    pagina.set_record_count(static_cast<std::uint16_t>(en_pagina));
    const PageId id = disco.allocate_page();
    disco.write_page(id, pagina);
    ++stats_.pages_written;
    pagina.clear();
    en_pagina = 0;
  };

  for (const auto& r : registros) {
    codec.encode(r, pagina.body().subspan(en_pagina * codec.size(), codec.size()));
    ++en_pagina;
    if (en_pagina == por_pagina_) cerrar();
  }
  if (en_pagina > 0) cerrar();
  disco.flush();
  return tmp;
}

// ---------------------------------------------------------------------------
// Fase 2: fusion k-way
// ---------------------------------------------------------------------------

std::shared_ptr<ExternalSort::Temporal> ExternalSort::fusionar(
    const std::vector<std::shared_ptr<Temporal>>& entradas) {
  const RecordCodec codec{schema_};

  // Por puntero y no por valor: `LectorDeRun` tiene un `DiskManager` adentro,
  // que no es copiable ni movible a proposito (posee un fstream abierto).
  std::vector<std::unique_ptr<LectorDeRun>> lectores;
  lectores.reserve(entradas.size());
  for (const auto& e : entradas) {
    lectores.push_back(std::make_unique<LectorDeRun>(e->path(), codec, page_size_, stats_));
  }

  // El heap guarda un frente por run: el registro y de que run salio. Es lo
  // que hace que sacar el menor de k cueste log(k) en vez de k.
  struct Frente {
    Record registro;
    std::size_t run = 0;
  };
  auto peor = [this](const Frente& a, const Frente& b) {
    // `priority_queue` saca el MAYOR, asi que se invierte para sacar el menor.
    // El desempate por numero de run conserva el orden entre iguales: es lo
    // que hace que el sort sea estable de una pasada a la otra.
    if (menor(a.registro, b.registro)) return false;
    if (menor(b.registro, a.registro)) return true;
    return a.run > b.run;
  };
  std::priority_queue<Frente, std::vector<Frente>, decltype(peor)> heap(peor);

  for (std::size_t i = 0; i < lectores.size(); ++i) {
    Frente f;
    f.run = i;
    if (lectores[i]->next(f.registro)) heap.push(std::move(f));
  }

  auto salida = std::make_shared<Temporal>(nueva_ruta());
  DiskManager disco(salida->path(), page_size_);
  Page pagina(page_size_);
  std::size_t en_pagina = 0;

  auto cerrar = [&] {
    pagina.set_record_count(static_cast<std::uint16_t>(en_pagina));
    const PageId id = disco.allocate_page();
    disco.write_page(id, pagina);
    ++stats_.pages_written;
    pagina.clear();
    en_pagina = 0;
  };

  while (!heap.empty()) {
    Frente f = heap.top();
    heap.pop();

    codec.encode(f.registro, pagina.body().subspan(en_pagina * codec.size(), codec.size()));
    ++en_pagina;
    if (en_pagina == por_pagina_) cerrar();

    // Se repone el frente del run del que se acaba de sacar, y solo de ese.
    Frente sig;
    sig.run = f.run;
    if (lectores[f.run]->next(sig.registro)) heap.push(std::move(sig));
  }
  if (en_pagina > 0) cerrar();
  disco.flush();
  return salida;
}

// ---------------------------------------------------------------------------
// Salidas
// ---------------------------------------------------------------------------

/// Lee el run final de a una pagina. Mientras viva, el `ExternalSort` que lo
/// creo tiene que seguir vivo: es el dueño del temporal.
class ExternalSort::MergeSource final : public RecordSource {
 public:
  MergeSource(std::shared_ptr<Temporal> run, Schema esquema, std::size_t page_size,
              OpStats& stats)
      : run_(std::move(run)),
        codec_(std::move(esquema)),
        lector_(run_->path(), codec_, page_size, stats) {}

  bool next(Record& out) override { return lector_.next(out); }

 private:
  std::shared_ptr<Temporal> run_;
  RecordCodec codec_;
  LectorDeRun lector_;
};

/// Todo cupo en memoria: no se toco el disco.
class ExternalSort::MemorySource final : public RecordSource {
 public:
  explicit MemorySource(std::vector<Record> rs) : rs_(std::move(rs)) {}
  bool next(Record& out) override {
    if (i_ >= rs_.size()) return false;
    out = std::move(rs_[i_++]);
    return true;
  }

 private:
  std::vector<Record> rs_;
  std::size_t i_ = 0;
};

// ---------------------------------------------------------------------------

std::unique_ptr<RecordSource> ExternalSort::sorted(RecordSource& entrada) {
  total_ = 0;
  runs_iniciales_ = 0;
  pasadas_ = 0;
  vivos_.clear();

  // --- fase 1 -------------------------------------------------------------
  std::vector<std::shared_ptr<Temporal>> runs;
  std::vector<Record> primero = leer_trozo(entrada);
  total_ += primero.size();

  // Si el primer trozo no lleno los buffers, la entrada cabia entera en
  // memoria: no hay nada que escribir a disco. Es el caso comun en tablas
  // chicas, y `passes() == 0` es lo que lo distingue en el plan de ejecucion.
  if (primero.size() < buffers_ * por_pagina_) {
    return std::make_unique<MemorySource>(std::move(primero));
  }

  runs.push_back(escribir_run(primero));
  primero.clear();
  primero.shrink_to_fit();

  for (;;) {
    auto trozo = leer_trozo(entrada);
    if (trozo.empty()) break;
    total_ += trozo.size();
    runs.push_back(escribir_run(trozo));
  }
  runs_iniciales_ = runs.size();
  vivos_ = runs;

  // --- fase 2 -------------------------------------------------------------
  // Cada pasada fusiona de a k = buffers-1 runs: uno de los buffers se reserva
  // para acumular la salida, porque escribir de a un registro costaria una
  // pagina por registro.
  const std::size_t k = buffers_ - 1;
  while (runs.size() > 1) {
    std::vector<std::shared_ptr<Temporal>> siguientes;
    for (std::size_t i = 0; i < runs.size(); i += k) {
      const auto hasta = std::min(i + k, runs.size());
      std::vector<std::shared_ptr<Temporal>> grupo(runs.begin() + static_cast<std::ptrdiff_t>(i),
                                                   runs.begin() + static_cast<std::ptrdiff_t>(hasta));
      // Un run solo no se fusiona consigo mismo: pasa tal cual a la siguiente
      // pasada. Copiarlo costaria 2N paginas por nada.
      siguientes.push_back(grupo.size() == 1 ? grupo.front() : fusionar(grupo));
    }
    ++pasadas_;
    runs = std::move(siguientes);
    // Los de la pasada anterior se sueltan aqui: el ultimo `shared_ptr` que
    // los sostenia era `vivos_`, asi que sus archivos se borran ahora y no al
    // final. Es lo que acota el espacio en disco a dos pasadas.
    vivos_ = runs;
  }

  return std::make_unique<MergeSource>(runs.front(), schema_, page_size_, stats_);
}

}  // namespace quipudb
