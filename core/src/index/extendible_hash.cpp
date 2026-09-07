#include "quipudb/index/extendible_hash.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>

#include "quipudb/error.hpp"

namespace quipudb {

namespace {

// FNV-1a de 64 bits.
constexpr std::uint64_t kFnvOffset = 14695981039346656037ULL;
constexpr std::uint64_t kFnvPrime = 1099511628211ULL;

}  // namespace

// ---------------------------------------------------------------------------
// Hash
// ---------------------------------------------------------------------------

std::uint64_t ExtendibleHash::hash_of(const Column& column, const Key& key) {
  if (type_of(key) != column.type) {
    throw SchemaError("la clave es " + std::string(to_string(type_of(key))) + " y la columna " +
                      column.name + " es " + std::string(to_string(column.type)));
  }
  Value normal = key;
  if (column.type == DataType::Double) {
    // Dos claves que `compare` considera iguales tienen que hashear igual, y
    // los bytes crudos no lo garantizan: 0.0 y -0.0 solo difieren en el bit de
    // signo, y dos NaN con carga distinta tienen bytes distintos aunque
    // `compare` los declare iguales.
    const double d = std::get<double>(normal);
    if (d == 0.0) {
      normal = 0.0;
    } else if (std::isnan(d)) {
      normal = std::numeric_limits<double>::quiet_NaN();
    }
  }
  std::vector<std::byte> buf(column.byte_size());
  RecordCodec::encode_value(column, normal, buf);

  // FNV-1a a secas, sin finalizador. Ver la cabecera para las mediciones que
  // sostienen esa decision.
  std::uint64_t h = kFnvOffset;
  for (const std::byte b : buf) {
    h ^= static_cast<std::uint64_t>(std::to_integer<unsigned char>(b));
    h *= kFnvPrime;
  }
  return h;
}

// ---------------------------------------------------------------------------
// Construccion y metadatos
// ---------------------------------------------------------------------------

ExtendibleHash::ExtendibleHash(std::filesystem::path path, Column key_column,
                               std::size_t payload_size, std::size_t page_size,
                               std::size_t bucket_capacity)
    : disk_(std::move(path), page_size),
      key_column_(std::move(key_column)),
      key_size_(key_column_.byte_size()),
      payload_size_(payload_size),
      scratch_(page_size) {
  if (key_size_ == 0) {
    throw SchemaError("la columna " + key_column_.name +
                      " no ocupa bytes en disco: un VARCHAR necesita longitud");
  }
  entry_size_ = key_size_ + payload_size_;

  const std::size_t utiles = scratch_.body_size() - kBucketHeader;
  const std::size_t maxima = utiles / entry_size_;
  if (maxima < 2) {
    throw SchemaError("en paginas de " + std::to_string(page_size) + " bytes entran " +
                      std::to_string(maxima) + " entradas de " + std::to_string(entry_size_) +
                      " bytes por bucket, y hacen falta al menos 2: con una sola, partir un "
                      "bucket lleno nunca deja sitio");
  }
  capacity_ = bucket_capacity == 0 ? maxima : bucket_capacity;
  if (capacity_ < 2 || capacity_ > maxima) {
    throw SchemaError("se pidieron buckets de " + std::to_string(bucket_capacity) +
                      " entradas y tienen que estar entre 2 y " + std::to_string(maxima));
  }

  load_meta();
}

void ExtendibleHash::load_meta() {
  Meta m;
  std::vector<std::byte> buf(sizeof(Meta));
  disk_.read_meta(buf);
  std::memcpy(&m, buf.data(), sizeof(Meta));

  if (m.version == 0 && m.key_size == 0 && disk_.page_count() == 0) {
    // Archivo nuevo: profundidad global 0, un bucket de profundidad local 0 y
    // un directorio de una sola entrada que le apunta.
    global_depth_ = 0;
    dir_head_ = kInvalidPage;
    dir_pages_ = 0;
    free_head_ = kInvalidPage;
    count_ = 0;
    dir_.assign(1, new_bucket(0));
    save_directory();
    save_meta();
    return;
  }

  if (m.version != kMetaVersion) {
    throw IoError("'" + disk_.path().string() + "' usa el formato de hash extensible " +
                  std::to_string(m.version) + " y este core escribe el " +
                  std::to_string(kMetaVersion));
  }
  if (m.key_type != static_cast<std::uint32_t>(key_column_.type) || m.key_size != key_size_ ||
      m.payload_size != payload_size_) {
    throw SchemaError("'" + disk_.path().string() + "' guarda claves " +
                      std::string(to_string(static_cast<DataType>(m.key_type))) + " de " +
                      std::to_string(m.key_size) + " bytes con payload de " +
                      std::to_string(m.payload_size) + ", y se pidio abrirlo con " +
                      std::string(to_string(key_column_.type)) + " de " +
                      std::to_string(key_size_) + " y payload de " +
                      std::to_string(payload_size_));
  }
  if (m.capacity != capacity_) {
    throw SchemaError("'" + disk_.path().string() + "' se creo con buckets de " +
                      std::to_string(m.capacity) + " entradas y se pidio abrirlo con " +
                      std::to_string(capacity_));
  }
  if (m.global_depth > kMaxGlobalDepth) {
    throw IoError("'" + disk_.path().string() + "' dice profundidad global " +
                  std::to_string(m.global_depth) + ", fuera del maximo " +
                  std::to_string(kMaxGlobalDepth));
  }

  global_depth_ = m.global_depth;
  dir_head_ = m.dir_head;
  dir_pages_ = m.dir_pages;
  free_head_ = m.free_head;
  count_ = m.count;
  load_directory();
}

void ExtendibleHash::save_meta() {
  Meta m;
  m.key_type = static_cast<std::uint32_t>(key_column_.type);
  m.key_size = static_cast<std::uint32_t>(key_size_);
  m.payload_size = static_cast<std::uint32_t>(payload_size_);
  m.capacity = static_cast<std::uint32_t>(capacity_);
  m.global_depth = static_cast<std::uint32_t>(global_depth_);
  m.dir_head = dir_head_;
  m.dir_pages = dir_pages_;
  m.free_head = free_head_;
  m.count = count_;
  std::vector<std::byte> buf(sizeof(Meta));
  std::memcpy(buf.data(), &m, sizeof(Meta));
  disk_.write_meta(buf);
}

void ExtendibleHash::flush() {
  save_meta();
  disk_.flush();
}

// ---------------------------------------------------------------------------
// Paginas
// ---------------------------------------------------------------------------

void ExtendibleHash::fetch(PageId id) {
  disk_.read_page(id, scratch_);
  ++stats_.pages_read;
}

void ExtendibleHash::store(PageId id) {
  disk_.write_page(id, scratch_);
  ++stats_.pages_written;
}

PageId ExtendibleHash::allocate() {
  if (free_head_ != kInvalidPage) {
    const PageId id = free_head_;
    fetch(id);
    free_head_ = scratch_.next();
    return id;
  }
  return disk_.allocate_page();
}

void ExtendibleHash::free_page(PageId id) {
  scratch_.clear();
  scratch_.set_next(free_head_);
  store(id);
  free_head_ = id;
}

void ExtendibleHash::set_entradas(std::uint16_t n) {
  scratch_.set_record_count(n);
  const std::size_t usados = kBucketHeader + static_cast<std::size_t>(n) * entry_size_;
  scratch_.set_free_space(static_cast<std::uint16_t>(scratch_.body_size() - usados));
}

PageId ExtendibleHash::new_bucket(std::size_t local) {
  const PageId id = allocate();
  scratch_.clear();
  scratch_.write<std::uint32_t>(0, static_cast<std::uint32_t>(local));
  set_entradas(0);
  store(id);
  return id;
}

std::size_t ExtendibleHash::depth_of(PageId bucket) {
  fetch(bucket);
  return scratch_.read<std::uint32_t>(0);
}

// ---------------------------------------------------------------------------
// Directorio
// ---------------------------------------------------------------------------

std::size_t ExtendibleHash::dir_per_page() const noexcept {
  return scratch_.body_size() / sizeof(PageId);
}

std::size_t ExtendibleHash::dir_index(std::uint64_t hash) const noexcept {
  if (global_depth_ == 0) return 0;
  return static_cast<std::size_t>(hash & ((1ULL << global_depth_) - 1ULL));
}

void ExtendibleHash::load_directory() {
  const std::size_t esperadas = std::size_t{1} << global_depth_;
  dir_.clear();
  dir_.reserve(esperadas);
  for (PageId p = dir_head_; p != kInvalidPage;) {
    fetch(p);
    const std::uint16_t n = scratch_.record_count();
    for (std::uint16_t i = 0; i < n; ++i) {
      dir_.push_back(scratch_.read<PageId>(static_cast<std::size_t>(i) * sizeof(PageId)));
    }
    p = scratch_.next();
  }
  if (dir_.size() != esperadas) {
    throw IoError("'" + disk_.path().string() + "' dice profundidad global " +
                  std::to_string(global_depth_) + ", que son " + std::to_string(esperadas) +
                  " entradas de directorio, y en disco hay " + std::to_string(dir_.size()));
  }
}

void ExtendibleHash::save_directory() {
  const std::size_t por_pagina = dir_per_page();
  const std::size_t necesarias = (dir_.size() + por_pagina - 1) / por_pagina;

  // 1. Reunir la cadena que ya existe y alargarla si hace falta. El
  //    directorio de este issue solo crece, asi que nunca se devuelven
  //    paginas: si sobran, quedan encadenadas con cero entradas.
  std::vector<PageId> paginas;
  paginas.reserve(necesarias);
  for (PageId p = dir_head_; p != kInvalidPage;) {
    paginas.push_back(p);
    fetch(p);
    p = scratch_.next();
  }
  while (paginas.size() < necesarias) paginas.push_back(allocate());
  if (dir_head_ == kInvalidPage) dir_head_ = paginas.front();

  // 2. Volcar las entradas y encadenar.
  for (std::size_t i = 0; i < paginas.size(); ++i) {
    const std::size_t desde = i * por_pagina;
    const std::size_t cuantas =
        desde >= dir_.size() ? 0 : std::min(por_pagina, dir_.size() - desde);
    scratch_.clear();
    scratch_.set_next(i + 1 < paginas.size() ? paginas[i + 1] : kInvalidPage);
    scratch_.set_record_count(static_cast<std::uint16_t>(cuantas));
    scratch_.set_free_space(
        static_cast<std::uint16_t>(scratch_.body_size() - cuantas * sizeof(PageId)));
    for (std::size_t j = 0; j < cuantas; ++j) {
      scratch_.write<PageId>(j * sizeof(PageId), dir_[desde + j]);
    }
    store(paginas[i]);
  }
  dir_pages_ = static_cast<std::uint32_t>(paginas.size());
}

void ExtendibleHash::duplicar_directorio() {
  if (global_depth_ >= kMaxGlobalDepth) {
    // Inalcanzable con el resto del codigo: antes de duplicar se comprueba que
    // el bit nuevo separe algo, y si no separa se va a overflow. Si aun asi se
    // llega hasta aqui, el archivo esta mal y decirlo es mejor que seguir.
    throw IoError("el directorio llego a la profundidad " + std::to_string(kMaxGlobalDepth) +
                  ": partir buckets dejo de repartir las claves");
  }
  const std::size_t antes = dir_.size();
  dir_.resize(antes * 2);
  // Las dos copias de cada entrada apuntan al MISMO bucket: duplicar el
  // directorio no crea buckets, solo agrega un bit para poder distinguirlos
  // cuando alguno se parta.
  for (std::size_t i = 0; i < antes; ++i) dir_[antes + i] = dir_[i];
  ++global_depth_;
  save_directory();
}

// ---------------------------------------------------------------------------
// Cadenas de bucket
// ---------------------------------------------------------------------------

Key ExtendibleHash::clave_de(std::span<const std::byte> entrada) const {
  return RecordCodec::decode_value(key_column_, entrada.subspan(0, key_size_));
}

bool ExtendibleHash::append(PageId bucket, std::span<const std::byte> entrada) {
  for (PageId p = bucket; p != kInvalidPage;) {
    fetch(p);
    const std::uint16_t n = scratch_.record_count();
    if (n < capacity_) {
      scratch_.write_bytes(kBucketHeader + static_cast<std::size_t>(n) * entry_size_, entrada);
      set_entradas(static_cast<std::uint16_t>(n + 1));
      store(p);
      return true;
    }
    p = scratch_.next();
  }
  return false;
}

void ExtendibleHash::encadenar_overflow(PageId bucket) {
  const std::size_t local = depth_of(bucket);
  const PageId nueva = new_bucket(local);

  PageId ultimo = bucket;
  for (;;) {
    fetch(ultimo);
    const PageId sig = scratch_.next();
    if (sig == kInvalidPage) break;
    ultimo = sig;
  }
  // `scratch_` tiene cargada la ultima pagina de la cadena.
  scratch_.set_next(nueva);
  store(ultimo);
}

void ExtendibleHash::append_forzado(PageId bucket, std::span<const std::byte> entrada) {
  if (append(bucket, entrada)) return;
  encadenar_overflow(bucket);
  if (!append(bucket, entrada)) {
    throw IoError("una pagina de overflow recien creada no acepto una entrada");
  }
}

std::vector<ExtendibleHash::Entrada> ExtendibleHash::recolectar(PageId bucket) {
  std::vector<Entrada> out;
  for (PageId p = bucket; p != kInvalidPage;) {
    fetch(p);
    const std::uint16_t n = scratch_.record_count();
    for (std::uint16_t i = 0; i < n; ++i) {
      const auto bytes =
          scratch_.read_bytes(kBucketHeader + static_cast<std::size_t>(i) * entry_size_,
                              entry_size_);
      Entrada e;
      e.bytes.assign(bytes.begin(), bytes.end());
      out.push_back(std::move(e));
    }
    p = scratch_.next();
  }
  // El hash se calcula despues de soltar la pagina: `hash_of` no toca
  // `scratch_`, pero dejarlo aparte evita depender de eso.
  for (auto& e : out) e.hash = hash_of(key_column_, clave_de(e.bytes));
  return out;
}

void ExtendibleHash::limpiar(PageId bucket) {
  std::vector<PageId> overflow;
  for (PageId p = bucket; p != kInvalidPage;) {
    fetch(p);
    const PageId sig = scratch_.next();
    if (p != bucket) overflow.push_back(p);
    p = sig;
  }
  fetch(bucket);
  scratch_.set_next(kInvalidPage);
  set_entradas(0);
  store(bucket);
  for (const PageId p : overflow) free_page(p);
}

// ---------------------------------------------------------------------------
// Insercion y split
// ---------------------------------------------------------------------------

void ExtendibleHash::insert(const Key& key, std::span<const std::byte> payload) {
  if (type_of(key) != key_column_.type) {
    throw SchemaError("la clave es " + std::string(to_string(type_of(key))) + " y la columna " +
                      key_column_.name + " es " + std::string(to_string(key_column_.type)));
  }
  if (payload.size() != payload_size_) {
    throw SchemaError("el payload mide " + std::to_string(payload.size()) +
                      " bytes y este indice guarda " + std::to_string(payload_size_));
  }

  std::vector<std::byte> entrada(entry_size_);
  RecordCodec::encode_value(key_column_, key,
                            std::span<std::byte>(entrada).subspan(0, key_size_));
  if (payload_size_ > 0) {
    std::memcpy(entrada.data() + key_size_, payload.data(), payload_size_);
  }

  const std::uint64_t h = hash_of(key_column_, key);
  // El bucle es necesario: un split puede dejar la entrada nueva del lado que
  // sigue lleno, y entonces hay que volver a hacer sitio. Termina siempre
  // porque cada vuelta o encadena overflow (que acepta la entrada en la
  // siguiente) o sube en uno la profundidad local, acotada por kMaxGlobalDepth.
  for (;;) {
    const std::size_t i = dir_index(h);
    const PageId b = dir_[i];
    if (append(b, entrada)) {
      ++count_;
      save_meta();
      return;
    }
    crecer(i, b);
  }
}

void ExtendibleHash::crecer(std::size_t index, PageId bucket) {
  const std::size_t local = depth_of(bucket);
  if (local > global_depth_) {
    throw IoError("el bucket de la pagina " + std::to_string(bucket) +
                  " dice profundidad local " + std::to_string(local) + " y la global es " +
                  std::to_string(global_depth_));
  }
  const std::uint64_t bit = 1ULL << local;

  const auto entradas = recolectar(bucket);

  // Las entradas de un bucket ya comparten sus `local` bits bajos. Un split
  // -- este o cualquiera de los siguientes -- solo puede separarlas si
  // difieren en algun bit por ENCIMA de esos, asi que la pregunta se hace
  // sobre el hash entero desplazado y no sobre el bit que toca ahora.
  //
  // Preguntar solo por el bit `local` seria demasiado estricto: con buckets de
  // 4 entradas, una de cada ocho veces las cuatro coinciden en ese bit por
  // casualidad, y ahi lo correcto es partir igual (el hermano nace vacio) y
  // volver a intentarlo un bit mas arriba, no rendirse y encadenar overflow.
  const std::uint64_t altos = entradas.front().hash >> local;
  bool separables = false;
  for (const auto& e : entradas) {
    if ((e.hash >> local) != altos) {
      separables = true;
      break;
    }
  }

  // Si todas tienen el mismo hash son claves repetidas y ningun split las
  // separara nunca; y si ademas hiciera falta un bit de directorio que ya no
  // se puede pedir, tampoco hay split posible. En los dos casos el bucket
  // crece por overflow, igual que el area del archivo secuencial (#10).
  if (!separables || (local == global_depth_ && global_depth_ == kMaxGlobalDepth)) {
    encadenar_overflow(bucket);
    return;
  }

  if (local == global_depth_) duplicar_directorio();

  const std::size_t nueva_local = local + 1;
  limpiar(bucket);
  fetch(bucket);
  scratch_.write<std::uint32_t>(0, static_cast<std::uint32_t>(nueva_local));
  store(bucket);

  const PageId hermano = new_bucket(nueva_local);

  // Repuntar las entradas del directorio que compartian este bucket: las que
  // tienen el bit nuevo en 1 pasan al hermano, el resto se queda.
  const std::size_t mascara = static_cast<std::size_t>(bit) - 1;
  const std::size_t bajos = index & mascara;
  for (std::size_t j = 0; j < dir_.size(); ++j) {
    if ((j & mascara) != bajos) continue;
    dir_[j] = (j & static_cast<std::size_t>(bit)) != 0 ? hermano : bucket;
  }
  save_directory();

  for (const auto& e : entradas) {
    append_forzado((e.hash & bit) != 0 ? hermano : bucket, e.bytes);
  }
}

// ---------------------------------------------------------------------------
// Forma de la estructura
// ---------------------------------------------------------------------------

PageId ExtendibleHash::bucket_at(std::size_t index) const {
  if (index >= dir_.size()) {
    throw SchemaError("la entrada " + std::to_string(index) +
                      " no existe: el directorio tiene " + std::to_string(dir_.size()));
  }
  return dir_[index];
}

std::size_t ExtendibleHash::local_depth(std::size_t index) { return depth_of(bucket_at(index)); }

std::size_t ExtendibleHash::bucket_count() const {
  std::vector<PageId> copia = dir_;
  std::sort(copia.begin(), copia.end());
  const auto fin = std::unique(copia.begin(), copia.end());
  return static_cast<std::size_t>(fin - copia.begin());
}

std::size_t ExtendibleHash::overflow_pages() {
  std::size_t total = 0;
  for (std::size_t j = 0; j < dir_.size(); ++j) {
    fetch(dir_[j]);
    const std::size_t local = scratch_.read<std::uint32_t>(0);
    if (local > global_depth_) {
      throw IoError("el bucket de la pagina " + std::to_string(dir_[j]) +
                    " dice profundidad local " + std::to_string(local) + " y la global es " +
                    std::to_string(global_depth_));
    }
    // Un bucket de profundidad local L aparece en 2^(global-L) entradas y solo
    // la de indice menor que 2^L tiene los bits altos en cero: esa es su
    // entrada canonica, y desde las demas ya esta contado.
    if ((j >> local) != 0) continue;
    for (PageId p = scratch_.next(); p != kInvalidPage;) {
      ++total;
      fetch(p);
      p = scratch_.next();
    }
  }
  return total;
}

std::size_t ExtendibleHash::free_pages() {
  std::size_t n = 0;
  PageId p = free_head_;
  while (p != kInvalidPage) {
    ++n;
    fetch(p);
    p = scratch_.next();
  }
  return n;
}

// ---------------------------------------------------------------------------
// Recorrido
// ---------------------------------------------------------------------------

class ExtendibleHash::Cursor final : public HashEntryCursor {
 public:
  explicit Cursor(ExtendibleHash& h) : h_(&h), pag_(h.disk_.page_size()) {}

  bool next(Key& key, std::vector<std::byte>& payload) override {
    for (;;) {
      if (slot_ < n_) {
        const std::size_t off = kBucketHeader + static_cast<std::size_t>(slot_) * h_->entry_size_;
        const auto bytes = pag_.read_bytes(off, h_->entry_size_);
        key = h_->clave_de(bytes);
        payload.assign(bytes.begin() + static_cast<std::ptrdiff_t>(h_->key_size_), bytes.end());
        rid_ = RID{actual_, static_cast<SlotId>(slot_)};
        ++slot_;
        return true;
      }
      if (!avanzar()) return false;
    }
  }

  [[nodiscard]] RID position() const override { return rid_; }

 private:
  /// Carga la proxima pagina con entradas: primero sigue la cadena de overflow
  /// del bucket actual y, al agotarla, avanza por el directorio hasta la
  /// siguiente entrada que estrene bucket.
  bool avanzar() {
    for (;;) {
      if (actual_ != kInvalidPage) {
        const PageId sig = pag_.next();
        if (sig != kInvalidPage) {
          cargar(sig);
          return true;
        }
      }
      soltar();
      if (j_ >= h_->dir_.size()) return false;
      const std::size_t j = j_++;
      cargar(h_->dir_[j]);
      const std::size_t local = pag_.read<std::uint32_t>(0);
      if (local > h_->global_depth_) {
        throw IoError("el bucket de la pagina " + std::to_string(h_->dir_[j]) +
                      " dice profundidad local " + std::to_string(local) + " y la global es " +
                      std::to_string(h_->global_depth_));
      }
      if ((j >> local) != 0) {
        soltar();  // ya se recorrio desde su entrada canonica
        continue;
      }
      return true;
    }
  }

  void cargar(PageId id) {
    h_->disk_.read_page(id, pag_);
    ++h_->stats_.pages_read;
    actual_ = id;
    n_ = pag_.record_count();
    slot_ = 0;
  }

  void soltar() noexcept {
    actual_ = kInvalidPage;
    n_ = 0;
    slot_ = 0;
  }

  ExtendibleHash* h_;
  Page pag_;
  std::size_t j_ = 0;          // proxima entrada del directorio
  PageId actual_ = kInvalidPage;
  std::uint16_t n_ = 0;
  std::uint16_t slot_ = 0;
  RID rid_{};
};

std::unique_ptr<HashEntryCursor> ExtendibleHash::entries() {
  return std::make_unique<Cursor>(*this);
}

std::vector<std::pair<Key, std::vector<std::byte>>> ExtendibleHash::scan() {
  std::vector<std::pair<Key, std::vector<std::byte>>> out;
  out.reserve(size());
  auto cur = entries();
  Key k;
  std::vector<std::byte> p;
  while (cur->next(k, p)) out.emplace_back(k, p);
  return out;
}

// ---------------------------------------------------------------------------
// Invariantes
// ---------------------------------------------------------------------------

std::string ExtendibleHash::check_invariants() {
  if (global_depth_ > kMaxGlobalDepth) {
    return "la profundidad global es " + std::to_string(global_depth_) + ", fuera del maximo " +
           std::to_string(kMaxGlobalDepth);
  }
  const std::size_t esperadas = std::size_t{1} << global_depth_;
  if (dir_.size() != esperadas) {
    return "el directorio tiene " + std::to_string(dir_.size()) + " entradas y la profundidad " +
           std::to_string(global_depth_) + " pide " + std::to_string(esperadas);
  }

  std::size_t total = 0;
  for (std::size_t j = 0; j < dir_.size(); ++j) {
    const PageId b = dir_[j];
    if (b == 0 || b == kInvalidPage || b > disk_.page_count()) {
      return "la entrada " + std::to_string(j) + " del directorio apunta a la pagina " +
             std::to_string(b) + ", que no es una pagina de datos";
    }
    const std::size_t local = depth_of(b);
    if (local > global_depth_) {
      return "el bucket de la entrada " + std::to_string(j) + " dice profundidad local " +
             std::to_string(local) + " y la global es " + std::to_string(global_depth_);
    }
    const std::size_t mascara = (std::size_t{1} << local) - 1;

    // Todas las entradas que comparten los `local` bits bajos tienen que
    // apuntar al mismo bucket.
    if (dir_[j & mascara] != b) {
      return "las entradas " + std::to_string(j) + " y " + std::to_string(j & mascara) +
             " comparten los " + std::to_string(local) +
             " bits bajos y apuntan a paginas distintas (" + std::to_string(b) + " y " +
             std::to_string(dir_[j & mascara]) + ")";
    }
    if ((j >> local) != 0) continue;  // ya revisado desde su entrada canonica

    // ...y ninguna otra puede apuntarle.
    const auto veces = static_cast<std::size_t>(std::count(dir_.begin(), dir_.end(), b));
    const std::size_t debidas = std::size_t{1} << (global_depth_ - local);
    if (veces != debidas) {
      return "la pagina " + std::to_string(b) + " tiene profundidad local " +
             std::to_string(local) + ", asi que le corresponden " + std::to_string(debidas) +
             " entradas de directorio, y le apuntan " + std::to_string(veces);
    }

    for (PageId p = b; p != kInvalidPage;) {
      fetch(p);
      const std::size_t l = scratch_.read<std::uint32_t>(0);
      if (l != local) {
        return "la pagina " + std::to_string(p) + " de la cadena del bucket " +
               std::to_string(b) + " dice profundidad local " + std::to_string(l) +
               " y su primaria dice " + std::to_string(local);
      }
      const std::uint16_t n = scratch_.record_count();
      if (n > capacity_) {
        return "la pagina " + std::to_string(p) + " tiene " + std::to_string(n) +
               " entradas y la capacidad de un bucket es " + std::to_string(capacity_);
      }
      for (std::uint16_t i = 0; i < n; ++i) {
        const auto bytes =
            scratch_.read_bytes(kBucketHeader + static_cast<std::size_t>(i) * entry_size_,
                                entry_size_);
        const std::uint64_t h = hash_of(key_column_, clave_de(bytes));
        if ((h & mascara) != j) {
          return "una entrada de la pagina " + std::to_string(p) + " hashea a los bits bajos " +
                 std::to_string(h & mascara) + " y esta en el bucket " + std::to_string(j);
        }
        ++total;
      }
      p = scratch_.next();
    }
  }

  if (total != count_) {
    return "hay " + std::to_string(total) + " entradas en disco y el contador dice " +
           std::to_string(count_);
  }
  return {};
}

}  // namespace quipudb
