#include "quipudb/index/inverted_index.hpp"

#include <algorithm>
#include <cstring>
#include <iterator>
#include <optional>
#include <queue>
#include <span>
#include <string>
#include <system_error>
#include <tuple>
#include <utility>

#include "../external/temporales.hpp"
#include "flujo_paginas.hpp"
#include "quipudb/error.hpp"
#include "quipudb/storage/disk_manager.hpp"
#include "quipudb/storage/page.hpp"
#include "spimi_bloque.hpp"

namespace quipudb {
namespace {

using detail::EscritorDePaginas;
using detail::LectorDePaginas;
using detail::PosicionFlujo;

/// El area meta del indice. Sin relleno entre campos: se escribe cruda y dos
/// construcciones iguales tienen que dar los mismos bytes.
struct MetaIndice {
  char magic[4] = {'Q', 'P', 'I', 'I'};
  std::uint16_t version = 2;
  std::uint16_t reservado = 0;
  std::uint64_t terminos = 0;
  std::uint64_t postings = 0;
  PageId diccionario = kInvalidPage;
  PageId disperso = kInvalidPage;
  std::uint32_t disperso_bytes = 0;
  std::uint32_t disperso_entradas = 0;
  PageId opciones = kInvalidPage;
  std::uint32_t opciones_bytes = 0;
  // Version 2 (#163): las estadisticas de los rankings.
  std::uint32_t documentos = 0;
  PageId longitudes = kInvalidPage;
  std::uint64_t longitud_total = 0;
};
static_assert(sizeof(MetaIndice) == 64, "sin relleno: el archivo tiene que ser reproducible");
static_assert(sizeof(MetaIndice) <= DiskManager::kMetaSize);

constexpr char kMagic[4] = {'Q', 'P', 'I', 'I'};
/// La 1 (#162) no tenia N ni las longitudes de los documentos.
constexpr std::uint16_t kVersion = 2;

/// Bytes fijos de una entrada del diccionario, ademas del termino:
/// u16 largo, u32 df, u32 pagina, u16 offset.
constexpr std::size_t kFijoEntrada = 2 + 4 + 4 + 2;

// ---------------------------------------------------------------------------
// Diccionario
// ---------------------------------------------------------------------------

/// Una entrada del diccionario ya leida.
struct EntradaDiccionario {
  std::string termino;
  std::uint32_t df = 0;
  PosicionFlujo lista;
};

/// Escribe las paginas del diccionario. A diferencia del flujo de postings,
/// una entrada nunca cruza paginas: si no cabe, empieza la siguiente. Asi
/// cada pagina se recorre sola, que es lo que permite buscar leyendo una.
class EscritorDeDiccionario {
 public:
  explicit EscritorDeDiccionario(DiskManager& disco) : disco_(disco), pagina_(disco.page_size()) {}

  void agregar(std::string_view termino, std::uint32_t df, PosicionFlujo lista) {
    const std::size_t largo = kFijoEntrada + termino.size();
    if (largo > pagina_.body_size()) {
      throw SchemaError("indice invertido: el termino de " + std::to_string(termino.size()) +
                        " bytes no cabe en una pagina de " +
                        std::to_string(disco_.page_size()));
    }
    if (actual_ == kInvalidPage) {
      empezar(disco_.allocate_page(), termino);
    } else if (usado_ + largo > pagina_.body_size()) {
      const PageId siguiente = disco_.allocate_page();
      pagina_.set_next(siguiente);
      escribir_actual();
      empezar(siguiente, termino);
    }
    pagina_.write(usado_, static_cast<std::uint16_t>(termino.size()));
    pagina_.write_bytes(usado_ + 2, std::as_bytes(std::span(termino.data(), termino.size())));
    std::size_t o = usado_ + 2 + termino.size();
    pagina_.write(o, df);
    pagina_.write(o + 4, lista.pagina);
    pagina_.write(o + 8, lista.offset);
    usado_ += largo;
    ++entradas_;
  }

  void cerrar() {
    if (actual_ != kInvalidPage) escribir_actual();
    actual_ = kInvalidPage;
  }

  [[nodiscard]] PageId primera() const noexcept { return primera_; }

  /// El primer termino de cada pagina, con su numero: el indice disperso.
  [[nodiscard]] const std::vector<std::pair<std::string, PageId>>& disperso() const noexcept {
    return disperso_;
  }

 private:
  void empezar(PageId id, std::string_view primer_termino) {
    if (primera_ == kInvalidPage) primera_ = id;
    actual_ = id;
    pagina_.clear();
    usado_ = 0;
    entradas_ = 0;
    disperso_.emplace_back(std::string(primer_termino), id);
  }

  void escribir_actual() {
    pagina_.set_record_count(entradas_);
    disco_.write_page(actual_, pagina_);
  }

  DiskManager& disco_;
  Page pagina_;
  PageId actual_ = kInvalidPage;
  PageId primera_ = kInvalidPage;
  std::size_t usado_ = 0;
  std::uint16_t entradas_ = 0;
  std::vector<std::pair<std::string, PageId>> disperso_;
};

/// Las entradas de una pagina del diccionario.
std::vector<EntradaDiccionario> leer_pagina_diccionario(const Page& pagina) {
  std::vector<EntradaDiccionario> entradas(pagina.record_count());
  std::size_t o = 0;
  for (auto& e : entradas) {
    const auto largo = pagina.read<std::uint16_t>(o);
    const auto bytes = pagina.read_bytes(o + 2, largo);
    e.termino.assign(reinterpret_cast<const char*>(bytes.data()), bytes.size());
    o += 2 + largo;
    e.df = pagina.read<std::uint32_t>(o);
    e.lista.pagina = pagina.read<PageId>(o + 4);
    e.lista.offset = pagina.read<std::uint16_t>(o + 8);
    o += 10;
  }
  return entradas;
}

// ---------------------------------------------------------------------------
// Opciones del tokenizador
// ---------------------------------------------------------------------------

void escribir_opciones(EscritorDePaginas& out, const TokenizerOptions& o) {
  out.escribir(static_cast<std::uint8_t>(o.stopword_languages.size()));
  for (const TextLanguage idioma : o.stopword_languages) {
    out.escribir(static_cast<std::uint8_t>(idioma));
  }
  out.escribir(static_cast<std::uint8_t>(o.stemmer.has_value()));
  out.escribir(static_cast<std::uint8_t>(o.stemmer.value_or(TextLanguage::kSpanish)));
  out.escribir(static_cast<std::uint8_t>(o.unaccent_before_stem));
  out.escribir(static_cast<std::uint32_t>(o.max_term_bytes));
  out.escribir(static_cast<std::uint32_t>(o.extra_stopwords.size()));
  for (const std::string& palabra : o.extra_stopwords) {
    out.escribir(static_cast<std::uint16_t>(palabra.size()));
    out.escribir(std::as_bytes(std::span(palabra.data(), palabra.size())));
  }
}

TokenizerOptions leer_opciones(LectorDePaginas& in) {
  TokenizerOptions o;
  o.stopword_languages.resize(in.leer<std::uint8_t>());
  for (auto& idioma : o.stopword_languages) idioma = static_cast<TextLanguage>(in.leer<std::uint8_t>());
  const bool hay_stemmer = in.leer<std::uint8_t>() != 0;
  const auto stemmer = static_cast<TextLanguage>(in.leer<std::uint8_t>());
  o.stemmer = hay_stemmer ? std::optional<TextLanguage>(stemmer) : std::nullopt;
  o.unaccent_before_stem = in.leer<std::uint8_t>() != 0;
  o.max_term_bytes = in.leer<std::uint32_t>();
  o.extra_stopwords.resize(in.leer<std::uint32_t>());
  for (auto& palabra : o.extra_stopwords) {
    palabra.resize(in.leer<std::uint16_t>());
    in.leer(std::as_writable_bytes(std::span(palabra.data(), palabra.size())));
  }
  return o;
}

// ---------------------------------------------------------------------------
// Merge k-way
// ---------------------------------------------------------------------------

/// Mezcla `bloques` y entrega cada termino, con su lista completa, a `salida`
/// en orden de termino. Suma a `stats` las paginas y entradas leidas.
template <typename Salida>
void fusionar(const std::vector<std::filesystem::path>& bloques, std::size_t page_size,
              Salida&& salida, OpStats& stats) {
  struct Frente {
    std::unique_ptr<SpimiBlockReader> lector;
    PostingEntry entrada;
  };
  std::vector<Frente> frentes(bloques.size());

  // El de menor termino primero y, a igual termino, el bloque mas antiguo:
  // ese orden es el de los documentos, y es el que se concatena.
  const auto despues = [&frentes](std::size_t a, std::size_t b) {
    return std::tie(frentes[a].entrada.term, a) > std::tie(frentes[b].entrada.term, b);
  };
  std::priority_queue<std::size_t, std::vector<std::size_t>, decltype(despues)> cola(despues);

  // Avanza un frente y lo devuelve a la cola si le quedan entradas. Cada
  // bloque tiene que venir ordenado por termino y sin repetidos.
  const auto avanzar = [&](std::size_t i) {
    Frente& f = frentes[i];
    std::string anterior = std::move(f.entrada.term);
    if (!f.lector->next(f.entrada)) return;
    ++stats.records_examined;
    if (!anterior.empty() && f.entrada.term <= anterior) {
      throw IoError("SPIMI: el bloque " + bloques[i].string() + " no esta ordenado por termino");
    }
    cola.push(i);
  };

  for (std::size_t i = 0; i < bloques.size(); ++i) {
    frentes[i].lector = std::make_unique<SpimiBlockReader>(bloques[i], page_size);
    avanzar(i);
  }

  std::vector<std::size_t> tomados;
  std::vector<Posting> lista;
  while (!cola.empty()) {
    const std::string termino = frentes[cola.top()].entrada.term;
    tomados.clear();
    while (!cola.empty() && frentes[cola.top()].entrada.term == termino) {
      tomados.push_back(cola.top());
      cola.pop();
    }

    lista.clear();
    for (const std::size_t i : tomados) {
      for (const Posting& p : frentes[i].entrada.postings) {
        if (!lista.empty() && p.doc <= lista.back().doc) {
          throw IoError("SPIMI: el termino '" + termino + "' tiene el documento " +
                        std::to_string(p.doc) + " despues del " +
                        std::to_string(lista.back().doc) + ": los bloques no estan en orden");
        }
        lista.push_back(p);
      }
    }
    salida(termino, lista);

    for (const std::size_t i : tomados) avanzar(i);
  }

  for (const Frente& f : frentes) stats.pages_read += f.lector->stats().pages_read;
}

}  // namespace

// ---------------------------------------------------------------------------
// Construccion
// ---------------------------------------------------------------------------

namespace {

/// Borra los bloques intermedios al salir, tambien si algo lanza. Los bloques
/// del SPIMI no se tocan: son de el.
class Intermedios {
 public:
  Intermedios() = default;
  Intermedios(const Intermedios&) = delete;
  Intermedios& operator=(const Intermedios&) = delete;
  ~Intermedios() {
    for (const auto& p : rutas_) borrar(p);
  }

  void agregar(const std::filesystem::path& p) { rutas_.push_back(p); }

  static void borrar(const std::filesystem::path& p) noexcept {
    std::error_code ec;
    std::filesystem::remove(p, ec);
  }

 private:
  std::vector<std::filesystem::path> rutas_;
};

}  // namespace

InvertedIndexBuild InvertedIndex::build(const SpimiIndexer& spimi,
                                        const std::filesystem::path& path, std::size_t fan_in) {
  if (fan_in < 2) {
    throw SchemaError("indice invertido: el fan-in es " + std::to_string(fan_in) +
                      "; con menos de 2 bloques a la vez no hay mezcla");
  }
  if (!spimi.finished()) {
    throw Unsupported("indice invertido: el SPIMI tiene que terminar (finish) antes del merge");
  }
  if (std::filesystem::exists(path)) {
    throw IoError("indice invertido: " + path.string() + " ya existe y no se pisa");
  }
  const std::size_t page_size = spimi.options().page_size;
  const auto temporal = path.parent_path() /
                        (path.filename().string() + ".tmp_" + detail::etiqueta_unica());

  InvertedIndexBuild reporte;
  reporte.blocks = spimi.blocks().size();
  OpStats& stats = reporte.stats;

  // Los niveles intermedios: mientras haya mas bloques de los que se pueden
  // abrir a la vez, se mezclan de a `fan_in` en bloques del mismo formato.
  std::vector<std::filesystem::path> bloques = spimi.blocks();
  Intermedios intermedios;
  std::vector<std::filesystem::path> del_nivel_anterior;
  while (bloques.size() > fan_in) {
    std::vector<std::filesystem::path> siguientes;
    std::vector<std::filesystem::path> de_este_nivel;
    for (std::size_t i = 0; i < bloques.size(); i += fan_in) {
      const std::size_t fin = std::min(i + fan_in, bloques.size());
      const std::vector<std::filesystem::path> grupo(
          bloques.begin() + static_cast<std::ptrdiff_t>(i),
          bloques.begin() + static_cast<std::ptrdiff_t>(fin));
      if (grupo.size() == 1) {
        // Un bloque suelto al final pasa tal cual al nivel siguiente.
        siguientes.push_back(grupo.front());
        continue;
      }
      const auto ruta = spimi.options().dir / ("quipudb_spimi_nivel_" +
                                               detail::etiqueta_unica() + ".blk");
      intermedios.agregar(ruta);
      de_este_nivel.push_back(ruta);
      detail::EscritorDeBloque bloque(ruta, page_size);
      fusionar(
          grupo, page_size,
          [&](const std::string& termino, const std::vector<Posting>& lista) {
            bloque.agregar(termino, lista);
          },
          stats);
      stats.pages_written += bloque.cerrar().pages_written;
      siguientes.push_back(ruta);
    }
    // Los intermedios del nivel anterior ya se mezclaron en los de este; un
    // bloque suelto que paso tal cual sigue en `siguientes` y no se borra.
    for (const auto& p : del_nivel_anterior) {
      if (std::find(siguientes.begin(), siguientes.end(), p) == siguientes.end()) {
        Intermedios::borrar(p);
      }
    }
    del_nivel_anterior = std::move(de_este_nivel);
    bloques = std::move(siguientes);
    ++reporte.passes;
  }
  ++reporte.passes;  // la pasada final, que escribe el indice

  try {
    {
      DiskManager disco(temporal, page_size);
      EscritorDePaginas postings(disco);
      EscritorDeDiccionario diccionario(disco);
      MetaIndice meta;

      fusionar(
          bloques, page_size,
          [&](const std::string& termino, const std::vector<Posting>& lista) {
            const PosicionFlujo inicio = postings.posicion();
            postings.escribir(std::as_bytes(std::span(lista)));
            diccionario.agregar(termino, static_cast<std::uint32_t>(lista.size()), inicio);
            ++meta.terminos;
            meta.postings += lista.size();
          },
          stats);
      postings.cerrar();
      diccionario.cerrar();
      meta.diccionario = diccionario.primera();

      EscritorDePaginas disperso(disco);
      for (const auto& [termino, pagina] : diccionario.disperso()) {
        disperso.escribir(static_cast<std::uint16_t>(termino.size()));
        disperso.escribir(std::as_bytes(std::span(termino.data(), termino.size())));
        disperso.escribir(pagina);
      }
      disperso.cerrar();
      meta.disperso = disperso.primera();
      meta.disperso_bytes = static_cast<std::uint32_t>(disperso.bytes());
      meta.disperso_entradas = static_cast<std::uint32_t>(diccionario.disperso().size());

      EscritorDePaginas opciones(disco);
      escribir_opciones(opciones, spimi.tokenizer().options());
      opciones.cerrar();
      meta.opciones = opciones.primera();
      meta.opciones_bytes = static_cast<std::uint32_t>(opciones.bytes());

      // Las longitudes se copian tal cual del archivo del SPIMI: ya estan en
      // orden de documento. De a una pagina, para no cargarlas enteras.
      const auto ml = detail::leer_meta_longitudes(spimi.lengths_file(), page_size);
      if (ml.documentos != spimi.documents()) {
        throw IoError("SPIMI: el archivo de longitudes tiene " + std::to_string(ml.documentos) +
                      " documentos y el indexador " + std::to_string(spimi.documents()));
      }
      {
        DiskManager origen(spimi.lengths_file(), page_size);
        LectorDePaginas lector(origen, {ml.primera, 0}, ml.bytes);
        EscritorDePaginas longitudes(disco);
        std::vector<std::byte> trozo(page_size);
        for (std::uint64_t quedan = ml.bytes; quedan > 0;) {
          const auto n = static_cast<std::size_t>(std::min<std::uint64_t>(trozo.size(), quedan));
          lector.leer(std::span(trozo).first(n));
          longitudes.escribir(std::span<const std::byte>(trozo).first(n));
          quedan -= n;
        }
        longitudes.cerrar();
        meta.longitudes = longitudes.primera();
        stats.pages_read += origen.reads();
      }
      meta.documentos = static_cast<std::uint32_t>(ml.documentos);
      meta.longitud_total = ml.longitud_total;

      disco.write_meta(std::as_bytes(std::span<const MetaIndice, 1>(&meta, 1)));
      disco.flush();
      stats.pages_written += disco.writes();
      stats.records_returned = meta.terminos;
    }
    std::filesystem::rename(temporal, path);
  } catch (...) {
    std::error_code ec;
    std::filesystem::remove(temporal, ec);
    throw;
  }
  return reporte;
}

// ---------------------------------------------------------------------------
// Lectura
// ---------------------------------------------------------------------------

class InvertedIndex::Impl {
 public:
  Impl(const std::filesystem::path& path, std::size_t page_size) : disco(path, page_size) {}

  /// La entrada de `termino`, leyendo una pagina del diccionario.
  std::optional<EntradaDiccionario> buscar(std::string_view termino) {
    // La ultima pagina cuyo primer termino no es mayor que el buscado.
    const auto it = std::upper_bound(
        disperso.begin(), disperso.end(), termino,
        [](std::string_view t, const auto& e) { return t < std::string_view(e.first); });
    if (it == disperso.begin()) return std::nullopt;
    Page pagina(disco.page_size());
    disco.read_page(std::prev(it)->second, pagina);
    ++stats.pages_read;
    for (auto& e : leer_pagina_diccionario(pagina)) {
      if (e.termino == termino) return std::move(e);
    }
    return std::nullopt;
  }

  std::vector<Posting> leer_lista(const EntradaDiccionario& e) {
    std::vector<Posting> lista(e.df);
    const std::uint64_t antes = disco.reads();
    LectorDePaginas lector(disco, e.lista, lista.size() * sizeof(Posting));
    lector.leer(std::as_writable_bytes(std::span(lista)));
    stats.pages_read += disco.reads() - antes;
    stats.records_returned += lista.size();
    return lista;
  }

  DiskManager disco;
  MetaIndice meta;
  std::vector<std::pair<std::string, PageId>> disperso;
  TokenizerOptions opciones;
  /// Las longitudes de los documentos, en orden de doc. Se cargan en la
  /// primera consulta.
  std::optional<std::vector<detail::LongitudDocumento>> longitudes;
  OpStats stats;
};

InvertedIndex::InvertedIndex(const std::filesystem::path& path, std::size_t page_size) {
  // DiskManager CREA el archivo si no existe.
  if (!std::filesystem::exists(path)) {
    throw IoError("indice invertido: no existe " + path.string());
  }
  impl_ = std::make_unique<Impl>(path, page_size);
  MetaIndice& meta = impl_->meta;
  impl_->disco.read_meta(std::as_writable_bytes(std::span<MetaIndice, 1>(&meta, 1)));
  if (std::memcmp(meta.magic, kMagic, sizeof kMagic) != 0) {
    throw IoError("indice invertido: " + path.string() + " no es un indice invertido");
  }
  if (meta.version != kVersion) {
    throw IoError("indice invertido: " + path.string() + " es de la version " +
                  std::to_string(meta.version) + " del formato; se esperaba la " +
                  std::to_string(kVersion));
  }

  LectorDePaginas disperso(impl_->disco, {meta.disperso, 0}, meta.disperso_bytes);
  impl_->disperso.resize(meta.disperso_entradas);
  for (auto& [termino, pagina] : impl_->disperso) {
    termino.resize(disperso.leer<std::uint16_t>());
    disperso.leer(std::as_writable_bytes(std::span(termino.data(), termino.size())));
    pagina = disperso.leer<PageId>();
  }
  LectorDePaginas opciones(impl_->disco, {meta.opciones, 0}, meta.opciones_bytes);
  impl_->opciones = leer_opciones(opciones);
  // Abrir no cuenta como consulta.
  impl_->disco.reset_counters();
}

InvertedIndex::~InvertedIndex() = default;

std::uint64_t InvertedIndex::terms() const noexcept { return impl_->meta.terminos; }
std::uint64_t InvertedIndex::postings() const noexcept { return impl_->meta.postings; }
std::uint64_t InvertedIndex::documents() const noexcept { return impl_->meta.documentos; }
std::uint64_t InvertedIndex::total_length() const noexcept { return impl_->meta.longitud_total; }

double InvertedIndex::average_document_length() const noexcept {
  const auto n = impl_->meta.documentos;
  return n == 0 ? 0.0 : static_cast<double>(impl_->meta.longitud_total) / static_cast<double>(n);
}

std::optional<std::uint32_t> InvertedIndex::document_length(DocId doc) {
  auto& longitudes = impl_->longitudes;
  if (!longitudes) {
    longitudes.emplace(impl_->meta.documentos);
    const std::uint64_t antes = impl_->disco.reads();
    LectorDePaginas lector(impl_->disco, {impl_->meta.longitudes, 0},
                           longitudes->size() * sizeof(detail::LongitudDocumento));
    lector.leer(std::as_writable_bytes(std::span(*longitudes)));
    impl_->stats.pages_read += impl_->disco.reads() - antes;
  }
  const auto it = std::lower_bound(
      longitudes->begin(), longitudes->end(), doc,
      [](const detail::LongitudDocumento& l, DocId d) { return l.doc < d; });
  if (it == longitudes->end() || it->doc != doc) return std::nullopt;
  return it->longitud;
}

const TokenizerOptions& InvertedIndex::tokenizer_options() const noexcept {
  return impl_->opciones;
}

std::uint32_t InvertedIndex::document_frequency(std::string_view term) {
  const auto e = impl_->buscar(term);
  return e ? e->df : 0;
}

std::vector<Posting> InvertedIndex::postings(std::string_view term) {
  const auto e = impl_->buscar(term);
  return e ? impl_->leer_lista(*e) : std::vector<Posting>{};
}

void InvertedIndex::for_each(const std::function<void(const PostingEntry&)>& fn) {
  Page pagina(impl_->disco.page_size());
  PostingEntry salida;
  for (PageId id = impl_->meta.diccionario; id != kInvalidPage; id = pagina.next()) {
    impl_->disco.read_page(id, pagina);
    ++impl_->stats.pages_read;
    for (const auto& e : leer_pagina_diccionario(pagina)) {
      salida.term = e.termino;
      salida.postings = impl_->leer_lista(e);
      fn(salida);
    }
  }
}

const OpStats& InvertedIndex::stats() const noexcept { return impl_->stats; }
void InvertedIndex::reset_stats() noexcept { impl_->stats.reset(); }

}  // namespace quipudb
