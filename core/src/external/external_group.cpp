#include "quipudb/external/external_group.hpp"

#include <algorithm>
#include <cmath>
#include <map>
#include <utility>

#include "quipudb/catalog/record_codec.hpp"
#include "quipudb/error.hpp"
#include "quipudb/index/extendible_hash.hpp"

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
// Camino por hash
// ---------------------------------------------------------------------------

namespace {

/// Filas de una particion, en memoria mientras se agrega.
using Lote = std::vector<Record>;

}  // namespace

std::vector<Record> ExternalGroupBy::por_hash(RecordSource& entrada,
                                             std::vector<Record>* sin_agrupar,
                                             bool& se_rindio) {
  const Column& col = entrada_.columns[key_column_];
  // Una particion por buffer disponible, menos uno para leer la entrada.
  const std::size_t p = std::max(kMinPartitions, buffers_ - 1);
  particiones_ = p;

  // Las particiones se sostienen en memoria y se vuelcan a `pendientes` cuando
  // una se pasa del tope. Es lo mismo que hace un motor real con su buffer
  // pool: aqui el tope se expresa en filas, derivado de los buffers.
  // Cuantos acumuladores caben en memoria. Un acumulador es la clave mas un
  // punado de contadores, bastante mas chico que una fila, pero se acota con la
  // misma unidad -- paginas -- para que `buffers` signifique lo mismo aqui que
  // en el sort (#20).
  const RecordCodec codec{entrada_};
  const std::size_t por_pagina = std::max<std::size_t>(
      1, (page_size_ - Page::kHeaderSize) / codec.size());
  const std::size_t tope_grupos = std::max<std::size_t>(1, buffers_ * por_pagina);

  std::vector<Lote> cubetas(p);
  Record fila;
  while (entrada.next(fila)) {
    entrada_.validate(fila);
    ++filas_;
    const std::uint64_t h = ExtendibleHash::hash_of(col, fila[key_column_]);
    cubetas[h % p].push_back(fila);
  }

  // Cada particion se agrega por separado: todas las filas de un grupo
  // comparten hash, asi que estan en la misma cubeta y no hay que mirar las
  // demas. Eso es lo que hace que el hash no necesite ordenar nada.
  se_rindio = false;
  std::size_t ultimo_tamano = 0;
  std::vector<Record> salida;
  // Filas de las cubetas ya agregadas. Se sostienen hasta el final por si
  // alguna cubeta posterior se rinde y hay que rehacer todo por sort.
  std::vector<Lote> agregadas;
  for (std::size_t semilla_vuelta = 0; !cubetas.empty();) {
    std::vector<Lote> siguientes;
    for (auto& cubeta : cubetas) {
      if (cubeta.empty()) continue;

      // Lo que tiene que caber en memoria son los GRUPOS, no las filas: un
      // grupo ocupa un acumulador de unas decenas de bytes, no sus filas. Por
      // eso se agrega al vuelo y se mira cuantos grupos distintos van
      // apareciendo, en vez de mirar cuantas filas trae la cubeta.
      //
      // Ese era el error de la primera version: rechazaba una cubeta de 4 000
      // filas repartidas en 5 grupos, que cabe de sobra, y encima culpaba a
      // "las claves son todas iguales", que era justo el caso contrario.
      {
        std::map<Key, Acumulador, KeyLess> acc;
        bool desbordo = false;
        for (const auto& r : cubeta) {
          const auto it = acc.find(r[key_column_]);
          if (it == acc.end() && acc.size() >= tope_grupos) {
            desbordo = true;
            break;
          }
          acumular(r, it == acc.end() ? acc[r[key_column_]] : it->second);
        }
        if (!desbordo) {
          for (const auto& [clave, a] : acc) salida.push_back(cerrar(clave, a));
          // La cubeta NO se libera aqui: si una cubeta posterior se rinde, el
          // fallback a sort necesita TODAS las filas, tambien las de las que ya
          // se agregaron. Se mueven a `agregadas` y se liberan al final, cuando
          // ya se sabe que nadie se rindio.
          agregadas.push_back(std::move(cubeta));
          cubeta.clear();
          continue;
        }
      }

      // Demasiados grupos distintos para esta cubeta: se re-particiona con
      // otra semilla para repartirlos entre varias.
      //
      // Cuantas vueltas hacen falta depende de cuantos grupos haya, asi que el
      // limite no es un contador fijo sino no haber avanzado: si una cubeta
      // sale de una vuelta con exactamente el mismo tamaño que entro, esa
      // semilla no separo nada y las siguientes tampoco lo haran.
      if (semilla_vuelta >= kMaxRepartitions && cubeta.size() == ultimo_tamano) {
        if (pedida_ == Strategy::kHash) {
          throw Unsupported(
              "una particion con mas de " + std::to_string(tope_grupos) +
              " grupos distintos no cabe en memoria y re-particionarla no la separa. "
              "Usa Strategy::kAuto o kSort");
        }
        // Se rinde. Las filas que quedan se devuelven por `sin_agrupar` para
        // que el llamador las ordene: la entrada ya se consumio y no se puede
        // releer, asi que perderlas aqui haria imposible el fallback.
        se_rindio = true;
        for (auto& c : cubetas) {
          sin_agrupar->insert(sin_agrupar->end(), std::make_move_iterator(c.begin()),
                              std::make_move_iterator(c.end()));
        }
        for (auto& s : siguientes) {
          sin_agrupar->insert(sin_agrupar->end(), std::make_move_iterator(s.begin()),
                              std::make_move_iterator(s.end()));
        }
        for (auto& a : agregadas) {
          sin_agrupar->insert(sin_agrupar->end(), std::make_move_iterator(a.begin()),
                              std::make_move_iterator(a.end()));
        }
        // Lo ya agregado en esta vuelta NO se puede descartar sin mas: sus
        // filas ya se liberaron con `cubeta.clear()`, asi que rehacer todo por
        // sort perderia esos grupos. Se convierten de vuelta en filas -- una
        // por grupo, con sus agregados ya calculados -- para que el sort las
        // vuelva a agrupar. Funciona porque agregar es asociativo... salvo
        // para COUNT y AVG, que contarian 1 en vez de N.
        //
        // Asi que en vez de eso se descarta `salida` y se avisa: el llamador
        // tiene que rehacer la agregacion entera sobre TODAS las filas, y por
        // eso `por_hash` las conserva enteras hasta saber si va a rendirse.
        salida.clear();
        return {};
      }
      ++reparticiones_;
      ultimo_tamano = cubeta.size();
      std::vector<Lote> sub(p);
      for (const auto& r : cubeta) {
        // Mezclar con la semilla cambia el reparto sin cambiar que dos claves
        // iguales sigan juntas, que es lo unico que no se puede romper.
        const std::uint64_t h = ExtendibleHash::hash_of(col, r[key_column_]);
        sub[((h >> (8 * (semilla_vuelta + 1))) ^ h) % p].push_back(r);
      }
      cubeta.clear();
      cubeta.shrink_to_fit();
      for (auto& s : sub) {
        if (!s.empty()) siguientes.push_back(std::move(s));
      }
    }
    cubetas = std::move(siguientes);
    if (!cubetas.empty()) ++semilla_vuelta;
  }

  agregadas.clear();
  agregadas.shrink_to_fit();
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

  std::vector<Record> rescatadas;
  bool se_rindio = false;
  auto porHash = por_hash(entrada, &rescatadas, se_rindio);

  if (!se_rindio) {
    usada_ = Strategy::kHash;
    return std::make_unique<Salida>(std::move(porHash));
  }

  // El hash no pudo: una particion no cabe y re-particionarla no la separa,
  // porque sus claves son todas iguales. La entrada ya se consumio, asi que se
  // ordenan las filas que `por_hash` conservo. Ordenar siempre funciona: no
  // depende de que el hash separe nada.
  cayo_ = true;
  usada_ = Strategy::kSort;
  filas_ = 0;
  grupos_ = 0;
  auto fuente = source_of(std::move(rescatadas));
  return std::make_unique<Salida>(por_sort(*fuente));
}

}  // namespace quipudb
