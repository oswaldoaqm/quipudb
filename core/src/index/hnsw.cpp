#include "quipudb/index/hnsw.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <functional>
#include <limits>
#include <queue>

#include "quipudb/error.hpp"

namespace quipudb {

namespace {

constexpr std::uint32_t kSinCapas = Hnsw::kNoVisto;
constexpr std::size_t kMaxM = 1000;

/// splitmix64: mezcla bien 64 bits y es igual en cualquier compilador, a
/// diferencia de las distribuciones de <random>.
std::uint64_t mezclar(std::uint64_t z) noexcept {
  z += 0x9E3779B97F4A7C15ULL;
  z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
  z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
  return z ^ (z >> 31);
}

}  // namespace

// ---------------------------------------------------------------------------
// Crear y abrir
// ---------------------------------------------------------------------------

Hnsw::Hnsw(const std::filesystem::path& path, std::size_t page_size)
    : disk_(path, page_size), scratch_(disk_.page_size()) {}

std::unique_ptr<Hnsw> Hnsw::create(const std::filesystem::path& path, std::size_t dim,
                                   VectorMetric metric, HnswParams params,
                                   std::size_t page_size) {
  if (dim == 0) throw SchemaError("un HNSW necesita vectores de dimension al menos 1");
  if (params.m < 2 || params.m > kMaxM) {
    throw SchemaError("M tiene que estar entre 2 y " + std::to_string(kMaxM) + "; llego " +
                      std::to_string(params.m));
  }
  if (params.ef_construction == 0) {
    throw SchemaError("efConstruction tiene que ser al menos 1");
  }
  // name_of lanza si la metrica no es una de las tres.
  (void)name_of(metric);

  std::unique_ptr<Hnsw> h(new Hnsw(path, page_size));
  Meta previa;
  std::vector<std::byte> buf(sizeof(Meta));
  h->disk_.read_meta(buf);
  std::memcpy(&previa, buf.data(), sizeof(Meta));
  if (previa.version != 0 || h->disk_.page_count() != 0) {
    throw SchemaError("'" + path.string() + "' ya tiene un indice; abrelo con Hnsw::open");
  }

  h->dim_ = dim;
  h->metric_ = metric;
  h->m_ = params.m;
  h->ef_construction_ = params.ef_construction;
  h->seed_ = params.seed;
  const std::size_t body = h->scratch_.body_size();
  h->nodes_per_page_ = body / h->node_record_size();
  h->uppers_per_page_ = body / h->upper_record_size();
  if (h->nodes_per_page_ == 0 || h->uppers_per_page_ == 0) {
    throw SchemaError("un nodo de dimension " + std::to_string(dim) + " con M = " +
                      std::to_string(params.m) + " ocupa " +
                      std::to_string(h->node_record_size()) +
                      " bytes y no cabe en una pagina de " + std::to_string(page_size) +
                      "; crea el indice con paginas mas grandes");
  }
  h->save_meta();
  return h;
}

std::unique_ptr<Hnsw> Hnsw::open(const std::filesystem::path& path, std::size_t page_size) {
  std::unique_ptr<Hnsw> h(new Hnsw(path, page_size));
  h->load();
  return h;
}

void Hnsw::save_meta() {
  Meta m;
  m.version = kMetaVersion;
  m.dim = static_cast<std::uint32_t>(dim_);
  m.metric = static_cast<std::uint8_t>(metric_);
  m.m = static_cast<std::uint16_t>(m_);
  m.ef_construction = static_cast<std::uint32_t>(ef_construction_);
  m.seed = seed_;
  m.count = static_cast<std::uint32_t>(rids_.size());
  m.entry = entry_;
  m.max_level = max_level_;
  m.upper_count = upper_count_;
  m.node_head = node_pages_.empty() ? kInvalidPage : node_pages_.front();
  m.upper_head = upper_pages_.empty() ? kInvalidPage : upper_pages_.front();
  std::vector<std::byte> buf(sizeof(Meta));
  std::memcpy(buf.data(), &m, sizeof(Meta));
  disk_.write_meta(buf);
}

void Hnsw::load() {
  const std::string nombre = "'" + disk_.path().string() + "'";
  Meta m;
  std::vector<std::byte> buf(sizeof(Meta));
  disk_.read_meta(buf);
  std::memcpy(&m, buf.data(), sizeof(Meta));
  if (m.version == 0 && disk_.page_count() == 0) {
    throw IoError(nombre + " esta vacio: no es un HNSW");
  }
  if (m.version != kMetaVersion) {
    throw IoError(nombre + " usa el formato de HNSW " + std::to_string(m.version) +
                  " y este core escribe el " + std::to_string(kMetaVersion));
  }
  if (m.dim == 0 || m.m < 2 || m.m > kMaxM || m.metric > 2) {
    throw IoError(nombre + " tiene parametros de HNSW invalidos");
  }

  dim_ = m.dim;
  metric_ = static_cast<VectorMetric>(m.metric);
  m_ = m.m;
  ef_construction_ = m.ef_construction;
  seed_ = m.seed;
  entry_ = m.entry;
  max_level_ = m.max_level;
  upper_count_ = m.upper_count;
  const std::size_t body = scratch_.body_size();
  nodes_per_page_ = body / node_record_size();
  uppers_per_page_ = body / upper_record_size();

  // Registros de capas altas: [vecinos u16][reservado u16][M ids].
  std::vector<std::vector<std::uint32_t>> altas;
  altas.reserve(upper_count_);
  for (PageId p = m.upper_head; p != kInvalidPage && altas.size() < upper_count_;
       p = scratch_.next()) {
    upper_pages_.push_back(p);
    disk_.read_page(p, scratch_);
    ++stats_.pages_read;
    for (std::size_t j = 0; j < scratch_.record_count() && altas.size() < upper_count_; ++j) {
      const std::size_t off = j * upper_record_size();
      const auto n = scratch_.read<std::uint16_t>(off);
      if (n > m_) throw IoError(nombre + " tiene un nodo con mas vecinos que M");
      auto& v = altas.emplace_back(n);
      for (std::size_t i = 0; i < n; ++i) {
        v[i] = scratch_.read<std::uint32_t>(off + kLinkHeader + i * sizeof(std::uint32_t));
      }
    }
  }
  if (altas.size() != upper_count_) throw IoError(nombre + " tiene las capas del HNSW incompletas");

  const std::size_t total = m.count;
  rids_.reserve(total);
  levels_.reserve(total);
  first_upper_.reserve(total);
  vectors_.reserve(total * dim_);
  links_.reserve(total);
  for (PageId p = m.node_head; p != kInvalidPage && rids_.size() < total; p = scratch_.next()) {
    node_pages_.push_back(p);
    disk_.read_page(p, scratch_);
    ++stats_.pages_read;
    for (std::size_t j = 0; j < scratch_.record_count() && rids_.size() < total; ++j) {
      const std::size_t off = j * node_record_size();
      rids_.push_back(
          RID{scratch_.read<PageId>(off), scratch_.read<SlotId>(off + sizeof(PageId))});
      const auto nivel = scratch_.read<std::uint8_t>(off + 6);
      const auto primero = scratch_.read<std::uint32_t>(off + 8);
      if (nivel > kMaxLevel || (nivel > 0 && (primero == kSinCapas ||
                                              std::size_t{primero} + nivel > altas.size()))) {
        throw IoError(nombre + " tiene un nodo con un nivel invalido");
      }
      levels_.push_back(nivel);
      first_upper_.push_back(primero);
      const auto muerto = scratch_.read<std::uint8_t>(off + 7);
      muerto_.push_back(muerto != 0 ? 1 : 0);
      if (muerto != 0) {
        ++borrados_;
      } else {
        por_rid_[rids_.back()].push_back(static_cast<std::uint32_t>(rids_.size() - 1));
      }
      const auto bytes = scratch_.read_bytes(off + kNodeFixed, dim_ * sizeof(float));
      const std::size_t inicio = vectors_.size();
      vectors_.resize(inicio + dim_);
      std::memcpy(vectors_.data() + inicio, bytes.data(), bytes.size());

      auto& capas = links_.emplace_back(std::size_t{nivel} + 1);
      const std::size_t lnk = off + kNodeFixed + dim_ * sizeof(float);
      const auto n0 = scratch_.read<std::uint16_t>(lnk);
      if (n0 > 2 * m_) throw IoError(nombre + " tiene un nodo con mas vecinos que 2M");
      capas[0].resize(n0);
      for (std::size_t i = 0; i < n0; ++i) {
        capas[0][i] = scratch_.read<std::uint32_t>(lnk + kLinkHeader + i * sizeof(std::uint32_t));
      }
      for (std::size_t l = 1; l <= nivel; ++l) capas[l] = altas[primero + l - 1];
      if (nivel > 0) {
        if (upper_owner_.size() < std::size_t{primero} + nivel) {
          upper_owner_.resize(std::size_t{primero} + nivel, kSinCapas);
        }
        for (std::size_t l = 0; l < nivel; ++l) {
          upper_owner_[primero + l] = static_cast<std::uint32_t>(rids_.size() - 1);
        }
      }
    }
  }
  if (rids_.size() != total) throw IoError(nombre + " tiene los nodos del HNSW incompletos");
  if (upper_owner_.size() != upper_count_ ||
      std::find(upper_owner_.begin(), upper_owner_.end(), kSinCapas) != upper_owner_.end()) {
    throw IoError(nombre + " tiene registros de capas altas sin nodo");
  }
  if (total > 0 && (entry_ >= total || max_level_ != levels_[entry_])) {
    throw IoError(nombre + " tiene un punto de entrada invalido");
  }
  for (const auto& capas : links_) {
    for (const auto& vecinos : capas) {
      for (const std::uint32_t v : vecinos) {
        if (v >= total) throw IoError(nombre + " tiene una arista a un nodo que no existe");
      }
    }
  }
}

void Hnsw::flush() {
  write_dirty();
  disk_.flush();
}

// ---------------------------------------------------------------------------
// Niveles y distancias
// ---------------------------------------------------------------------------

std::size_t Hnsw::level_for(std::uint64_t seed, std::uint64_t id, std::size_t m) {
  // 53 bits al azar dan u en [0, 1); 1 - u esta en (0, 1] y su logaritmo es
  // finito.
  const double u = static_cast<double>(mezclar(seed ^ mezclar(id)) >> 11) * 0x1.0p-53;
  const double nivel = -std::log1p(-u) / std::log(static_cast<double>(m));
  return std::min(static_cast<std::size_t>(nivel), kMaxLevel);
}

double Hnsw::distance(std::span<const float> a, std::span<const float> b) {
  ++stats_.records_examined;
  return vector_distance(a, b, metric_);
}

std::span<const float> Hnsw::vector(std::size_t node) const {
  if (node >= rids_.size()) throw InvalidRecord("el HNSW no tiene el nodo " + std::to_string(node));
  return std::span<const float>(vectors_).subspan(node * dim_, dim_);
}

std::span<const std::uint32_t> Hnsw::neighbors(std::size_t node, std::size_t layer) const {
  if (node >= rids_.size()) throw InvalidRecord("el HNSW no tiene el nodo " + std::to_string(node));
  if (layer > levels_[node]) return {};
  return links_[node][layer];
}

void Hnsw::check_vector(std::span<const float> vector, const char* where) const {
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

// ---------------------------------------------------------------------------
// Busqueda en una capa y seleccion de vecinos
// ---------------------------------------------------------------------------

std::vector<Hnsw::Candidato> Hnsw::search_layer(std::span<const float> query,
                                                const std::vector<Candidato>& entrada,
                                                std::size_t ef, std::size_t layer,
                                                bool solo_vivos) {
  if (visitado_.size() < rids_.size()) visitado_.resize(rids_.size(), 0);
  if (++vuelta_ == 0) {
    std::fill(visitado_.begin(), visitado_.end(), 0);
    vuelta_ = 1;
  }

  // Por explorar, el mas cercano arriba; y los mejores encontrados, el mas
  // lejano arriba para saber cuando dejar de buscar.
  std::priority_queue<Candidato, std::vector<Candidato>, std::greater<>> por_ver;
  std::priority_queue<Candidato> mejores;
  for (const Candidato& c : entrada) {
    if (visitado_[c.node] == vuelta_) continue;
    visitado_[c.node] = vuelta_;
    por_ver.push(c);
    if (solo_vivos && muerto_[c.node] != 0) continue;
    mejores.push(c);
    if (mejores.size() > ef) mejores.pop();
  }

  while (!por_ver.empty()) {
    const Candidato c = por_ver.top();
    // Si el mas cercano por ver ya es peor que el peor de los mejores, nada
    // de lo que queda puede entrar.
    if (mejores.size() >= ef && mejores.top() < c) break;
    por_ver.pop();
    for (const std::uint32_t v : links_[c.node][layer]) {
      if (visitado_[v] == vuelta_) continue;
      visitado_[v] = vuelta_;
      const Candidato nuevo{distance(query, vector(v)), v};
      if (mejores.size() < ef || nuevo < mejores.top()) {
        // Un borrado sirve para seguir navegando, pero no es resultado.
        por_ver.push(nuevo);
        if (solo_vivos && muerto_[v] != 0) continue;
        mejores.push(nuevo);
        if (mejores.size() > ef) mejores.pop();
      }
    }
  }

  std::vector<Candidato> out(mejores.size());
  for (auto it = out.rbegin(); it != out.rend(); ++it) {
    *it = mejores.top();
    mejores.pop();
  }
  return out;
}

std::vector<std::uint32_t> Hnsw::select_neighbors(const std::vector<Candidato>& candidatos,
                                                  std::size_t max) {
  // `candidatos` viene de mas cerca a mas lejos del nodo base.
  std::vector<std::uint32_t> elegidos;
  for (const Candidato& c : candidatos) {
    if (elegidos.size() == max) break;
    bool entra = true;
    for (const std::uint32_t e : elegidos) {
      if (distance(vector(c.node), vector(e)) < c.distance) {
        entra = false;
        break;
      }
    }
    if (entra) elegidos.push_back(c.node);
  }
  return elegidos;
}

void Hnsw::shrink(std::uint32_t node, std::size_t layer) {
  auto& vecinos = links_[node][layer];
  std::vector<Candidato> c;
  c.reserve(vecinos.size());
  for (const std::uint32_t v : vecinos) c.push_back({distance(vector(node), vector(v)), v});
  std::sort(c.begin(), c.end());
  vecinos = select_neighbors(c, max_neighbors(layer));
}

// ---------------------------------------------------------------------------
// Insertar
// ---------------------------------------------------------------------------

void Hnsw::mark_dirty(std::uint32_t node, std::size_t layer) {
  if (layer == 0) {
    dirty_nodes_.insert(node / nodes_per_page_);
  } else {
    dirty_uppers_.insert((first_upper_[node] + layer - 1) / uppers_per_page_);
  }
}

void Hnsw::add_node(RID rid, std::span<const float> v) {
  const auto id = static_cast<std::uint32_t>(rids_.size());
  const std::size_t nivel = level_for(seed_, id, m_);

  rids_.push_back(rid);
  levels_.push_back(static_cast<std::uint8_t>(nivel));
  muerto_.push_back(0);
  por_rid_[rid].push_back(id);
  first_upper_.push_back(nivel > 0 ? upper_count_ : kSinCapas);
  upper_count_ += static_cast<std::uint32_t>(nivel);
  upper_owner_.insert(upper_owner_.end(), nivel, id);
  vectors_.insert(vectors_.end(), v.begin(), v.end());
  links_.emplace_back(nivel + 1);

  // Paginas nuevas: la anterior cambia su `next`, asi que tambien se reescribe.
  while (node_pages_.size() * nodes_per_page_ < rids_.size()) {
    if (!node_pages_.empty()) dirty_nodes_.insert(node_pages_.size() - 1);
    node_pages_.push_back(disk_.allocate_page());
  }
  while (upper_pages_.size() * uppers_per_page_ < upper_count_) {
    if (!upper_pages_.empty()) dirty_uppers_.insert(upper_pages_.size() - 1);
    upper_pages_.push_back(disk_.allocate_page());
  }
  for (std::size_t l = 0; l <= nivel; ++l) mark_dirty(id, l);

  if (id == 0) {
    entry_ = 0;
    max_level_ = static_cast<int>(nivel);
    return;
  }

  const std::span<const float> q = vector(id);
  std::vector<Candidato> puntos{{distance(q, vector(entry_)), entry_}};
  // Bajada voraz por las capas que el nodo nuevo no tiene.
  for (auto capa = static_cast<std::size_t>(max_level_); capa > nivel; --capa) {
    puntos = {search_layer(q, puntos, 1, capa).front()};
  }
  // Desde su capa mas alta (o la del grafo, si es mas baja) hasta la 0.
  for (std::size_t capa = std::min(nivel, static_cast<std::size_t>(max_level_)) + 1; capa-- > 0;) {
    puntos = search_layer(q, puntos, ef_construction_, capa);
    links_[id][capa] = select_neighbors(puntos, max_neighbors(capa));
    for (const std::uint32_t e : links_[id][capa]) {
      links_[e][capa].push_back(id);
      if (links_[e][capa].size() > max_neighbors(capa)) shrink(e, capa);
      mark_dirty(e, capa);
    }
  }

  if (static_cast<int>(nivel) > max_level_) {
    max_level_ = static_cast<int>(nivel);
    entry_ = id;
  }
}

std::vector<std::uint32_t> Hnsw::reachable(std::size_t layer) const {
  std::vector<std::uint32_t> desde(rids_.size(), kNoVisto);
  if (rids_.empty()) return desde;
  std::vector<std::uint32_t> pila{entry_};
  desde[entry_] = entry_;
  while (!pila.empty()) {
    const std::uint32_t u = pila.back();
    pila.pop_back();
    for (const std::uint32_t e : links_[u][layer]) {
      if (desde[e] == kNoVisto) {
        desde[e] = u;
        pila.push_back(e);
      }
    }
  }
  return desde;
}

void Hnsw::connect_components() {
  if (rids_.empty()) return;
  for (std::size_t capa = 0; capa <= static_cast<std::size_t>(max_level_); ++capa) {
    const std::size_t max = max_neighbors(capa);
    // Cada vuelta suma al menos un nodo a los alcanzados y no quita ninguno.
    while (true) {
      const std::vector<std::uint32_t> padre = reachable(capa);
      std::size_t suelto = rids_.size();
      for (std::size_t id = 0; id < rids_.size(); ++id) {
        if (levels_[id] >= capa && padre[id] == kNoVisto) {
          suelto = id;
          break;
        }
      }
      if (suelto == rids_.size()) break;
      const auto x = static_cast<std::uint32_t>(suelto);
      const std::span<const float> q = vector(x);

      // Los alcanzados de esta capa, del mas cercano a x al mas lejano. Los
      // primeros salen de una busqueda normal desde la entrada, que solo
      // encuentra alcanzados; si ninguno sirve se miran todos.
      const auto cerca =
          search_layer(q, {{distance(q, vector(entry_)), entry_}}, ef_construction_, capa);
      std::vector<Candidato> todos;
      const auto candidatos = [&](std::size_t vuelta) -> const std::vector<Candidato>& {
        if (vuelta == 0) return cerca;
        if (todos.empty()) {
          for (std::size_t id = 0; id < rids_.size(); ++id) {
            if (levels_[id] >= capa && padre[id] != kNoVisto) {
              todos.push_back({distance(q, vector(id)), static_cast<std::uint32_t>(id)});
            }
          }
          std::sort(todos.begin(), todos.end());
        }
        return todos;
      };

      std::uint32_t elegido = kNoVisto;
      for (std::size_t vuelta = 0; vuelta < 2 && elegido == kNoVisto; ++vuelta) {
        for (const Candidato& c : candidatos(vuelta)) {
          if (links_[c.node][capa].size() < max) {
            elegido = c.node;
            break;
          }
        }
      }
      // Todos llenos: el mas cercano que tenga una arista fuera del arbol del
      // recorrido la cede, la que va mas lejos.
      for (std::size_t vuelta = 0; vuelta < 2 && elegido == kNoVisto; ++vuelta) {
        for (const Candidato& c : candidatos(vuelta)) {
          auto& v = links_[c.node][capa];
          auto quitar = v.end();
          Candidato peor{-std::numeric_limits<double>::infinity(), 0};
          for (auto it = v.begin(); it != v.end(); ++it) {
            if (padre[*it] == c.node) continue;  // del arbol: no se toca
            const Candidato d{distance(vector(c.node), vector(*it)), *it};
            if (peor < d) {
              peor = d;
              quitar = it;
            }
          }
          if (quitar != v.end()) {
            v.erase(quitar);
            elegido = c.node;
            break;
          }
        }
      }
      if (elegido == kNoVisto) {
        throw IoError("el HNSW no pudo conectar el nodo " + std::to_string(x) + " en la capa " +
                      std::to_string(capa));
      }
      links_[elegido][capa].push_back(x);
      mark_dirty(elegido, capa);
    }
  }
}

void Hnsw::insert(RID rid, std::span<const float> vector) {
  check_vector(vector, "insert");
  if (rids_.size() >= std::numeric_limits<std::uint32_t>::max() - 1) {
    throw SchemaError("el HNSW llego a su maximo de nodos");
  }
  add_node(rid, vector);
  connect_components();
  write_dirty();
}

void Hnsw::build(std::span<const RID> rids, std::span<const float> vectors) {
  if (vectors.size() != rids.size() * dim_) {
    throw InvalidRecord("build: llegaron " + std::to_string(vectors.size()) +
                        " componentes para " + std::to_string(rids.size()) +
                        " vectores de dimension " + std::to_string(dim_));
  }
  for (std::size_t i = 0; i < rids.size(); ++i) {
    check_vector(vectors.subspan(i * dim_, dim_), "build");
  }
  const auto inicio = std::chrono::steady_clock::now();
  for (std::size_t i = 0; i < rids.size(); ++i) add_node(rids[i], vectors.subspan(i * dim_, dim_));
  connect_components();
  write_dirty();
  last_build_ms_ =
      std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - inicio)
          .count();
}

// ---------------------------------------------------------------------------
// Buscar y borrar (#186)
// ---------------------------------------------------------------------------

std::vector<VectorMatch> Hnsw::search(std::span<const float> query, std::size_t k,
                                      VectorMetric metric, std::size_t ef_search) {
  if (metric != metric_) {
    throw SchemaError("el HNSW se construyo con " + std::string(name_of(metric_)) +
                      " y la consulta pide " + std::string(name_of(metric)) +
                      ": el grafo solo sirve para la metrica con la que se armo");
  }
  if (ef_search == 0) throw InvalidRecord("search: ef_search tiene que ser al menos 1");
  check_vector(query, "search");
  std::vector<VectorMatch> out;
  if (k == 0 || live_size() == 0) return out;

  // Bajada voraz hasta la capa 1 y busqueda ampliada en la 0.
  std::vector<Candidato> puntos{{distance(query, vector(entry_)), entry_}};
  for (auto capa = static_cast<std::size_t>(max_level_); capa > 0; --capa) {
    puntos = {search_layer(query, puntos, 1, capa).front()};
  }
  const auto mejores = search_layer(query, puntos, std::max(ef_search, k), 0, true);

  // El mismo orden que la busqueda exacta: distancia y, si empatan, RID.
  out.reserve(mejores.size());
  for (const Candidato& c : mejores) out.push_back(VectorMatch{rids_[c.node], c.distance});
  std::sort(out.begin(), out.end(), [](const VectorMatch& a, const VectorMatch& b) {
    return a.distance != b.distance ? a.distance < b.distance : a.rid < b.rid;
  });
  if (out.size() > k) out.resize(k);
  stats_.records_returned += out.size();
  return out;
}

bool Hnsw::remove(RID rid, std::span<const float> v) {
  check_vector(v, "remove");
  const auto it = por_rid_.find(rid);
  if (it == por_rid_.end()) return false;
  auto& nodos = it->second;
  for (auto n = nodos.begin(); n != nodos.end(); ++n) {
    const auto guardado = vector(*n);
    if (!std::equal(guardado.begin(), guardado.end(), v.begin(), v.end())) continue;
    muerto_[*n] = 1;
    ++borrados_;
    mark_dirty(*n, 0);
    nodos.erase(n);
    if (nodos.empty()) por_rid_.erase(it);
    write_dirty();
    return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// Escribir
// ---------------------------------------------------------------------------

void Hnsw::write_dirty() {
  // Las paginas se arman enteras desde memoria: no hace falta leerlas antes.
  for (const std::size_t p : dirty_nodes_) write_node_page(p);
  for (const std::size_t p : dirty_uppers_) write_upper_page(p);
  dirty_nodes_.clear();
  dirty_uppers_.clear();
  save_meta();
}

void Hnsw::write_node_page(std::size_t index) {
  scratch_.clear();
  scratch_.set_next(index + 1 < node_pages_.size() ? node_pages_[index + 1] : kInvalidPage);
  const std::size_t primero = index * nodes_per_page_;
  const std::size_t n = std::min(nodes_per_page_, rids_.size() - primero);
  scratch_.set_record_count(static_cast<std::uint16_t>(n));
  for (std::size_t j = 0; j < n; ++j) {
    const std::size_t id = primero + j;
    const std::size_t off = j * node_record_size();
    scratch_.write<PageId>(off, rids_[id].page);
    scratch_.write<SlotId>(off + sizeof(PageId), rids_[id].slot);
    scratch_.write<std::uint8_t>(off + 6, levels_[id]);
    scratch_.write<std::uint8_t>(off + 7, muerto_[id]);
    scratch_.write<std::uint32_t>(off + 8, first_upper_[id]);
    scratch_.write_bytes(off + kNodeFixed, std::as_bytes(vector(id)));
    const std::size_t lnk = off + kNodeFixed + dim_ * sizeof(float);
    const auto& vecinos = links_[id][0];
    scratch_.write<std::uint16_t>(lnk, static_cast<std::uint16_t>(vecinos.size()));
    for (std::size_t i = 0; i < vecinos.size(); ++i) {
      scratch_.write<std::uint32_t>(lnk + kLinkHeader + i * sizeof(std::uint32_t), vecinos[i]);
    }
  }
  disk_.write_page(node_pages_[index], scratch_);
  ++stats_.pages_written;
}

void Hnsw::write_upper_page(std::size_t index) {
  const std::size_t primero = index * uppers_per_page_;
  const std::size_t n = std::min<std::size_t>(uppers_per_page_, upper_count_ - primero);
  scratch_.clear();
  scratch_.set_next(index + 1 < upper_pages_.size() ? upper_pages_[index + 1] : kInvalidPage);
  scratch_.set_record_count(static_cast<std::uint16_t>(n));
  for (std::size_t j = 0; j < n; ++j) {
    const std::size_t r = primero + j;
    const std::uint32_t id = upper_owner_[r];
    const auto& vecinos = links_[id][r - first_upper_[id] + 1];
    const std::size_t off = j * upper_record_size();
    scratch_.write<std::uint16_t>(off, static_cast<std::uint16_t>(vecinos.size()));
    for (std::size_t i = 0; i < vecinos.size(); ++i) {
      scratch_.write<std::uint32_t>(off + kLinkHeader + i * sizeof(std::uint32_t), vecinos[i]);
    }
  }
  disk_.write_page(upper_pages_[index], scratch_);
  ++stats_.pages_written;
}

// ---------------------------------------------------------------------------
// Verificar
// ---------------------------------------------------------------------------

std::string Hnsw::check_invariants() const {
  const std::size_t n = rids_.size();
  if (n == 0) return max_level_ == -1 ? "" : "el indice vacio tiene nivel maximo";
  if (entry_ >= n) return "el punto de entrada no existe";
  if (static_cast<int>(levels_[entry_]) != max_level_) {
    return "el punto de entrada no tiene el nivel maximo";
  }
  for (std::size_t id = 0; id < n; ++id) {
    const std::string nodo = "el nodo " + std::to_string(id);
    if (static_cast<int>(levels_[id]) > max_level_) return nodo + " pasa el nivel maximo";
    if (levels_[id] != level_for(seed_, id, m_)) return nodo + " no tiene el nivel de su semilla";
    for (std::size_t l = 0; l <= levels_[id]; ++l) {
      const std::string capa = " en la capa " + std::to_string(l);
      const auto& v = links_[id][l];
      if (v.size() > max_neighbors(l)) {
        return nodo + " tiene mas vecinos que su grado maximo" + capa;
      }
      std::vector<std::uint32_t> orden(v.begin(), v.end());
      std::sort(orden.begin(), orden.end());
      if (std::adjacent_find(orden.begin(), orden.end()) != orden.end()) {
        return nodo + " tiene un vecino repetido" + capa;
      }
      for (const std::uint32_t e : v) {
        if (e == id) return nodo + " es vecino de si mismo" + capa;
        if (e >= n) return nodo + " apunta a un nodo que no existe" + capa;
        if (levels_[e] < l) return nodo + " apunta al nodo " + std::to_string(e) +
                                   ", que no llega" + capa;
      }
    }
  }

  // Alcanzables desde la entrada siguiendo aristas, capa por capa.
  for (std::size_t l = 0; l <= static_cast<std::size_t>(max_level_); ++l) {
    const std::vector<std::uint32_t> visto = reachable(l);
    for (std::size_t id = 0; id < n; ++id) {
      if (levels_[id] >= l && visto[id] == kNoVisto) {
        return "el nodo " + std::to_string(id) + " no se alcanza desde la entrada en la capa " +
               std::to_string(l);
      }
    }
  }
  return "";
}

}  // namespace quipudb
