#include "quipudb/index/ivf.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <limits>
#include <numeric>
#include <queue>

#include "quipudb/error.hpp"

namespace quipudb {

namespace {

/// Distancia euclidiana al cuadrado, sin validar ni sacar raiz: solo se usa
/// para comparar, y los vectores ya se validaron al entrar. Acumula en double
/// por la misma razon que vector_metric.cpp.
double l2_cuadrado(std::span<const float> a, std::span<const float> b) noexcept {
  double suma = 0.0;
  for (std::size_t i = 0; i < a.size(); ++i) {
    const double d = static_cast<double>(a[i]) - static_cast<double>(b[i]);
    suma += d * d;
  }
  return suma;
}

}  // namespace

// ---------------------------------------------------------------------------
// Crear y abrir
// ---------------------------------------------------------------------------

Ivf::Ivf(const std::filesystem::path& path, std::size_t page_size)
    : disk_(path, page_size), scratch_(disk_.page_size()) {}

std::unique_ptr<Ivf> Ivf::create(const std::filesystem::path& path,
                                 const std::vector<std::vector<float>>& centroids,
                                 std::size_t page_size) {
  if (centroids.empty()) {
    throw SchemaError("un IVF necesita al menos un centroide");
  }
  const std::size_t dim = centroids.front().size();
  if (dim == 0) throw SchemaError("los centroides del IVF no pueden estar vacios");
  for (std::size_t i = 0; i < centroids.size(); ++i) {
    if (centroids[i].size() != dim) {
      throw SchemaError("el centroide " + std::to_string(i) + " tiene dimension " +
                        std::to_string(centroids[i].size()) + " y el primero " +
                        std::to_string(dim));
    }
    for (const float x : centroids[i]) {
      if (!std::isfinite(x)) {
        throw InvalidRecord("el centroide " + std::to_string(i) + " tiene un componente no finito");
      }
    }
  }

  std::unique_ptr<Ivf> ivf(new Ivf(path, page_size));
  Meta previa;
  std::vector<std::byte> buf(sizeof(Meta));
  ivf->disk_.read_meta(buf);
  std::memcpy(&previa, buf.data(), sizeof(Meta));
  if (previa.version != 0 || ivf->disk_.page_count() != 0) {
    throw SchemaError("'" + path.string() + "' ya tiene un indice; abrelo con Ivf::open");
  }

  ivf->dim_ = dim;
  const std::size_t body = ivf->scratch_.body_size();
  ivf->entries_per_page_ = body / ivf->entry_size();
  ivf->dir_per_page_ = body / ivf->dir_entry_size();
  if (ivf->entries_per_page_ == 0 || ivf->dir_per_page_ == 0) {
    throw SchemaError("un vector de dimension " + std::to_string(dim) +
                      " no cabe en una pagina de " + std::to_string(page_size) +
                      " bytes; crea el indice con paginas mas grandes");
  }

  ivf->lists_.resize(centroids.size());
  for (std::size_t i = 0; i < centroids.size(); ++i) ivf->lists_[i].centroid = centroids[i];

  // El directorio se escribe completo, una vez por pagina.
  const std::size_t paginas = (centroids.size() + ivf->dir_per_page_ - 1) / ivf->dir_per_page_;
  for (std::size_t p = 0; p < paginas; ++p) ivf->dir_pages_.push_back(ivf->allocate());
  ivf->dir_head_ = ivf->dir_pages_.front();
  for (std::size_t i = 0; i < centroids.size(); ++i) ivf->write_directory_entry(i);
  ivf->save_meta();
  return ivf;
}

std::unique_ptr<Ivf> Ivf::open(const std::filesystem::path& path, std::size_t page_size) {
  std::unique_ptr<Ivf> ivf(new Ivf(path, page_size));
  ivf->load();
  return ivf;
}

void Ivf::load() {
  Meta m;
  std::vector<std::byte> buf(sizeof(Meta));
  disk_.read_meta(buf);
  std::memcpy(&m, buf.data(), sizeof(Meta));
  if (m.version == 0 && disk_.page_count() == 0) {
    throw IoError("'" + disk_.path().string() + "' esta vacio: no es un IVF");
  }
  if (m.version != kMetaVersion) {
    throw IoError("'" + disk_.path().string() + "' usa el formato de IVF " +
                  std::to_string(m.version) + " y este core escribe el " +
                  std::to_string(kMetaVersion));
  }

  dim_ = m.dim;
  count_ = m.count;
  dir_head_ = m.dir_head;
  free_head_ = m.free_head;
  const std::size_t body = scratch_.body_size();
  entries_per_page_ = body / entry_size();
  dir_per_page_ = body / dir_entry_size();
  lists_.assign(m.nlist, List{});

  std::size_t i = 0;
  for (PageId p = dir_head_; p != kInvalidPage && i < lists_.size(); p = scratch_.next()) {
    dir_pages_.push_back(p);
    disk_.read_page(p, scratch_);
    ++stats_.pages_read;
    for (std::size_t j = 0; j < dir_per_page_ && i < lists_.size(); ++j, ++i) {
      const std::size_t off = j * dir_entry_size();
      List& l = lists_[i];
      l.centroid.resize(dim_);
      std::memcpy(l.centroid.data(), scratch_.read_bytes(off, dim_ * sizeof(float)).data(),
                  dim_ * sizeof(float));
      const std::size_t fijo = off + dim_ * sizeof(float);
      l.head = scratch_.read<PageId>(fijo);
      l.tail = scratch_.read<PageId>(fijo + 4);
      l.size = scratch_.read<std::uint32_t>(fijo + 8);
    }
  }
  if (i != lists_.size()) {
    throw IoError("'" + disk_.path().string() + "' tiene el directorio del IVF incompleto");
  }
}

void Ivf::save_meta() {
  Meta m;
  m.version = kMetaVersion;
  m.dim = static_cast<std::uint32_t>(dim_);
  m.nlist = static_cast<std::uint32_t>(lists_.size());
  m.dir_head = dir_head_;
  m.free_head = free_head_;
  m.count = count_;
  std::vector<std::byte> buf(sizeof(Meta));
  std::memcpy(buf.data(), &m, sizeof(Meta));
  disk_.write_meta(buf);
}

void Ivf::flush() {
  save_meta();
  disk_.flush();
}

void Ivf::write_directory_entry(std::size_t list) {
  const PageId pagina = dir_pages_[list / dir_per_page_];
  const std::size_t primero = (list / dir_per_page_) * dir_per_page_;
  const std::size_t en_pagina = std::min(dir_per_page_, lists_.size() - primero);
  disk_.read_page(pagina, scratch_);
  ++stats_.pages_read;
  // Una pagina recien asignada viene en ceros: se le pone su enlace y cuantas
  // entradas tiene.
  const std::size_t indice_pagina = list / dir_per_page_;
  scratch_.set_next(indice_pagina + 1 < dir_pages_.size() ? dir_pages_[indice_pagina + 1]
                                                          : kInvalidPage);
  scratch_.set_record_count(static_cast<std::uint16_t>(en_pagina));

  const List& l = lists_[list];
  const std::size_t off = (list % dir_per_page_) * dir_entry_size();
  scratch_.write_bytes(off, std::as_bytes(std::span<const float>(l.centroid)));
  const std::size_t fijo = off + dim_ * sizeof(float);
  scratch_.write<PageId>(fijo, l.head);
  scratch_.write<PageId>(fijo + 4, l.tail);
  scratch_.write<std::uint32_t>(fijo + 8, l.size);
  disk_.write_page(pagina, scratch_);
  ++stats_.pages_written;
}

// ---------------------------------------------------------------------------
// Asignar e insertar
// ---------------------------------------------------------------------------

void Ivf::check_vector(std::span<const float> vector, const char* where) const {
  if (vector.size() != dim_) {
    throw InvalidRecord(std::string(where) + ": el vector tiene dimension " +
                        std::to_string(vector.size()) + " y el indice es de dimension " +
                        std::to_string(dim_));
  }
  for (std::size_t i = 0; i < vector.size(); ++i) {
    if (!std::isfinite(vector[i])) {
      throw InvalidRecord(std::string(where) + ": el componente " + std::to_string(i) +
                          " no es finito");
    }
  }
}

std::size_t Ivf::nearest_list(std::span<const float> vector) const {
  check_vector(vector, "nearest_list");
  std::size_t mejor = 0;
  double mejor_d = std::numeric_limits<double>::infinity();
  for (std::size_t i = 0; i < lists_.size(); ++i) {
    const double d = l2_cuadrado(vector, lists_[i].centroid);
    if (d < mejor_d) {  // estricto: con empate se queda el de menor indice
      mejor_d = d;
      mejor = i;
    }
  }
  return mejor;
}

void Ivf::append(std::size_t list, std::span<const RID> rids,
                 std::span<const std::span<const float>> vectors) {
  const std::size_t total = rids.size();
  if (total == 0) return;
  List& l = lists_[list];

  PageId actual = l.tail;
  std::size_t n = 0;
  if (actual != kInvalidPage) {
    disk_.read_page(actual, scratch_);
    ++stats_.pages_read;
    n = scratch_.record_count();
  } else {
    actual = allocate();
    scratch_.clear();
    l.head = actual;
  }

  std::size_t hechos = 0;
  while (true) {
    while (n < entries_per_page_ && hechos < total) {
      const std::size_t off = n * entry_size();
      scratch_.write<PageId>(off, rids[hechos].page);
      scratch_.write<SlotId>(off + sizeof(PageId), rids[hechos].slot);
      scratch_.write_bytes(off + kRidSize, std::as_bytes(vectors[hechos]));
      ++n;
      ++hechos;
    }
    scratch_.set_record_count(static_cast<std::uint16_t>(n));
    if (hechos == total) {
      disk_.write_page(actual, scratch_);
      ++stats_.pages_written;
      break;
    }
    // La pagina se lleno y quedan entradas: se enlaza una nueva antes de
    // escribir esta, para no tener que volver a leerla.
    const PageId nueva = allocate();
    scratch_.set_next(nueva);
    disk_.write_page(actual, scratch_);
    ++stats_.pages_written;
    actual = nueva;
    scratch_.clear();
    n = 0;
  }

  l.tail = actual;
  l.size += static_cast<std::uint32_t>(total);
  count_ += total;
  write_directory_entry(list);
}

void Ivf::insert(RID rid, std::span<const float> vector) {
  const std::size_t list = nearest_list(vector);
  const std::span<const float> uno[] = {vector};
  append(list, std::span<const RID>(&rid, 1), uno);
  save_meta();
}

void Ivf::build(std::span<const RID> rids, std::span<const float> vectors) {
  if (vectors.size() != rids.size() * dim_) {
    throw InvalidRecord("build: llegaron " + std::to_string(vectors.size()) +
                        " componentes para " + std::to_string(rids.size()) +
                        " vectores de dimension " + std::to_string(dim_));
  }
  const auto inicio = std::chrono::steady_clock::now();

  // Se valida todo antes de escribir nada: un NaN en el vector 90 000 no debe
  // dejar el indice a medio construir.
  std::vector<std::span<const float>> vistas(rids.size());
  for (std::size_t i = 0; i < rids.size(); ++i) {
    vistas[i] = vectors.subspan(i * dim_, dim_);
    check_vector(vistas[i], "build");
  }

  // Primero se decide la lista de cada vector y despues se escribe lista por
  // lista: asi cada pagina de datos se escribe una sola vez.
  std::vector<std::vector<std::size_t>> por_lista(lists_.size());
  for (std::size_t i = 0; i < vistas.size(); ++i) por_lista[nearest_list(vistas[i])].push_back(i);

  for (std::size_t list = 0; list < lists_.size(); ++list) {
    const auto& idx = por_lista[list];
    if (idx.empty()) continue;
    std::vector<RID> r;
    std::vector<std::span<const float>> v;
    r.reserve(idx.size());
    v.reserve(idx.size());
    for (const std::size_t i : idx) {
      r.push_back(rids[i]);
      v.push_back(vistas[i]);
    }
    append(list, r, v);
  }
  save_meta();

  last_build_ms_ = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() -
                                                             inicio)
                       .count();
}

// ---------------------------------------------------------------------------
// Free list
// ---------------------------------------------------------------------------

PageId Ivf::allocate() {
  if (free_head_ == kInvalidPage) return disk_.allocate_page();
  // Pagina propia y no scratch_: `append` pide una pagina nueva mientras
  // todavia tiene en scratch_ la que esta llenando.
  Page libre(disk_.page_size());
  disk_.read_page(free_head_, libre);
  ++stats_.pages_read;
  const PageId id = free_head_;
  free_head_ = libre.next();
  return id;
}

void Ivf::free_page(PageId id) {
  Page libre(disk_.page_size());
  libre.set_next(free_head_);
  disk_.write_page(id, libre);
  ++stats_.pages_written;
  free_head_ = id;
}

std::size_t Ivf::free_pages() {
  std::size_t n = 0;
  Page libre(disk_.page_size());
  for (PageId p = free_head_; p != kInvalidPage && n <= disk_.page_count(); ++n) {
    disk_.read_page(p, libre);
    p = libre.next();
  }
  return n;
}

// ---------------------------------------------------------------------------
// Borrar (#184)
// ---------------------------------------------------------------------------

bool Ivf::remove(RID rid, std::span<const float> vector) {
  const std::size_t list = nearest_list(vector);
  List& l = lists_[list];
  if (l.size == 0) return false;

  // Se recorre la cadena entera: hace falta saber donde esta la entrada y cual
  // es la penultima pagina, por si la ultima queda vacia.
  const auto buscado = std::as_bytes(vector);
  std::vector<PageId> paginas;
  PageId hueco_pagina = kInvalidPage;
  std::size_t hueco_slot = 0;
  for (PageId p = l.head; p != kInvalidPage && paginas.size() <= disk_.page_count();
       p = scratch_.next()) {
    paginas.push_back(p);
    disk_.read_page(p, scratch_);
    ++stats_.pages_read;
    for (std::size_t j = 0; hueco_pagina == kInvalidPage && j < scratch_.record_count(); ++j) {
      const std::size_t off = j * entry_size();
      const RID r{scratch_.read<PageId>(off), scratch_.read<SlotId>(off + sizeof(PageId))};
      const auto v = scratch_.read_bytes(off + kRidSize, buscado.size());
      if (r == rid && std::equal(v.begin(), v.end(), buscado.begin())) {
        hueco_pagina = p;
        hueco_slot = j;
      }
    }
  }
  if (hueco_pagina == kInvalidPage) return false;

  // La ultima entrada de la lista tapa el hueco.
  const PageId cola = paginas.back();
  disk_.read_page(cola, scratch_);
  ++stats_.pages_read;
  const std::size_t n = scratch_.record_count();
  const auto ultima_span = scratch_.read_bytes((n - 1) * entry_size(), entry_size());
  const std::vector<std::byte> ultima(ultima_span.begin(), ultima_span.end());

  if (hueco_pagina != cola) {
    disk_.read_page(hueco_pagina, scratch_);
    ++stats_.pages_read;
    scratch_.write_bytes(hueco_slot * entry_size(), ultima);
    disk_.write_page(hueco_pagina, scratch_);
    ++stats_.pages_written;
    disk_.read_page(cola, scratch_);
    ++stats_.pages_read;
  } else if (hueco_slot != n - 1) {
    scratch_.write_bytes(hueco_slot * entry_size(), ultima);
  }

  if (n > 1) {
    scratch_.set_record_count(static_cast<std::uint16_t>(n - 1));
    disk_.write_page(cola, scratch_);
    ++stats_.pages_written;
  } else if (paginas.size() == 1) {
    // Era la unica entrada de la lista.
    free_page(cola);
    l.head = l.tail = kInvalidPage;
  } else {
    // La ultima pagina quedo vacia: sale de la cadena.
    const PageId penultima = paginas[paginas.size() - 2];
    disk_.read_page(penultima, scratch_);
    ++stats_.pages_read;
    scratch_.set_next(kInvalidPage);
    disk_.write_page(penultima, scratch_);
    ++stats_.pages_written;
    free_page(cola);
    l.tail = penultima;
  }

  --l.size;
  --count_;
  write_directory_entry(list);
  save_meta();
  return true;
}

// ---------------------------------------------------------------------------
// Buscar (#184)
// ---------------------------------------------------------------------------

std::vector<std::size_t> Ivf::probe_order(std::span<const float> query) const {
  check_vector(query, "probe_order");
  std::vector<double> d(lists_.size());
  for (std::size_t i = 0; i < lists_.size(); ++i) d[i] = l2_cuadrado(query, lists_[i].centroid);
  std::vector<std::size_t> orden(lists_.size());
  std::iota(orden.begin(), orden.end(), std::size_t{0});
  std::stable_sort(orden.begin(), orden.end(),
                   [&](std::size_t a, std::size_t b) { return d[a] < d[b]; });
  return orden;
}

std::vector<VectorMatch> Ivf::search(std::span<const float> query, std::size_t k,
                                     VectorMetric metric, std::size_t nprobe) {
  check_vector(query, "search");
  if (nprobe == 0) throw InvalidRecord("search: nprobe tiene que ser al menos 1");
  std::vector<VectorMatch> out;
  if (k == 0) return out;

  // El mismo orden total que la busqueda exacta: distancia y, si empatan, RID.
  auto antes = [](const VectorMatch& a, const VectorMatch& b) {
    return a.distance != b.distance ? a.distance < b.distance : a.rid < b.rid;
  };
  std::priority_queue<VectorMatch, std::vector<VectorMatch>, decltype(antes)> mejores(antes);

  const auto orden = probe_order(query);
  const std::size_t listas = std::min(nprobe, lists_.size());
  std::vector<float> v(dim_);
  for (std::size_t i = 0; i < listas; ++i) {
    const List& l = lists_[orden[i]];
    std::size_t paginas = 0;
    for (PageId p = l.head; p != kInvalidPage && paginas <= disk_.page_count(); ++paginas) {
      disk_.read_page(p, scratch_);
      ++stats_.pages_read;
      for (std::size_t j = 0; j < scratch_.record_count(); ++j) {
        const std::size_t off = j * entry_size();
        // Se copia a un buffer: los floats de la pagina no estan alineados.
        std::memcpy(v.data(), scratch_.read_bytes(off + kRidSize, dim_ * sizeof(float)).data(),
                    dim_ * sizeof(float));
        const VectorMatch candidato{
            RID{scratch_.read<PageId>(off), scratch_.read<SlotId>(off + sizeof(PageId))},
            vector_distance(query, v, metric)};
        ++stats_.records_examined;
        if (mejores.size() < k) {
          mejores.push(candidato);
        } else if (antes(candidato, mejores.top())) {
          mejores.pop();
          mejores.push(candidato);
        }
      }
      p = scratch_.next();
    }
  }

  out.resize(mejores.size());
  for (auto it = out.rbegin(); it != out.rend(); ++it) {
    *it = mejores.top();
    mejores.pop();
  }
  stats_.records_returned += out.size();
  return out;
}

// ---------------------------------------------------------------------------
// Leer y verificar
// ---------------------------------------------------------------------------

std::vector<std::pair<RID, std::vector<float>>> Ivf::list_entries(std::size_t list) {
  const List& l = lists_.at(list);
  std::vector<std::pair<RID, std::vector<float>>> out;
  out.reserve(l.size);
  // El tope evita colgarse si una corrupcion hizo un ciclo en la cadena.
  std::size_t paginas = 0;
  for (PageId p = l.head; p != kInvalidPage && paginas <= disk_.page_count(); ++paginas) {
    disk_.read_page(p, scratch_);
    ++stats_.pages_read;
    for (std::size_t j = 0; j < scratch_.record_count(); ++j) {
      const std::size_t off = j * entry_size();
      RID rid{scratch_.read<PageId>(off), scratch_.read<SlotId>(off + sizeof(PageId))};
      std::vector<float> v(dim_);
      std::memcpy(v.data(), scratch_.read_bytes(off + kRidSize, dim_ * sizeof(float)).data(),
                  dim_ * sizeof(float));
      out.emplace_back(rid, std::move(v));
    }
    p = scratch_.next();
  }
  return out;
}

std::string Ivf::check_invariants() {
  std::uint64_t total = 0;
  for (std::size_t i = 0; i < lists_.size(); ++i) {
    const auto entradas = list_entries(i);
    if (entradas.size() != lists_[i].size) {
      return "la lista " + std::to_string(i) + " dice tener " + std::to_string(lists_[i].size) +
             " vectores y sus paginas tienen " + std::to_string(entradas.size());
    }
    for (const auto& [rid, v] : entradas) {
      const std::size_t correcta = nearest_list(v);
      if (correcta != i) {
        return "un vector de la lista " + std::to_string(i) + " pertenece a la " +
               std::to_string(correcta);
      }
    }
    if ((lists_[i].head == kInvalidPage) != (lists_[i].size == 0)) {
      return "la lista " + std::to_string(i) + " tiene cabeza y tamano que no cuadran";
    }
    total += entradas.size();
  }
  if (total != count_) {
    return "la meta dice " + std::to_string(count_) + " vectores y las listas tienen " +
           std::to_string(total);
  }
  return "";
}

}  // namespace quipudb
