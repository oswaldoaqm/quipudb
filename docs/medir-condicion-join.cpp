// Mide que estrategia conviene para el JOIN (#22): index nested loop o hash
// join por particiones.
//
// A diferencia de `medir-condicion-merge.py`, que simula la estructura, esto
// mide el motor REAL: las paginas del INL salen de `OpStats` del indice y de
// la tabla, no de una formula. Se puede porque las dos piezas que sondea ya
// existen (#16, #19); para el hash join no hay medicion posible antes de
// escribirlo, asi que su costo es la formula 3(N_R + N_S): dos pasadas de
// particionado (leer todo y escribir todo) mas una de sondeo. Una vez escrito,
// el join contrasta esa formula contra sus paginas reales.
//
// Por que existe este archivo
// ---------------------------
//
// El criterio de aceptacion del #22 decia: "si hay indice sobre la clave de
// join se usa index nested loop; si no, hash join". Es una regla de folklore,
// y en este repo las reglas de folklore ya fallaron tres veces (#18, #19 dos
// veces). Esta tambien falla: el INL paga por FILA externa y el hash join paga
// por PAGINA, asi que con 81 filas por pagina el INL solo gana cuando el lado
// externo es unas cien veces menor que el interno.
//
// Resultado con GCC 13.3.0, paginas de 4 KB:
//
//   |R|      |S|      claves  indice                 INL      hash    gana
//   10 000   10 000   10 000  bplus_unclustered   40 176       474    hash (85x)
//   10 000   10 000   10 000  extendible_hash     20 082       474    hash (42x)
//   10 000   10 000     100   bplus_unclustered 1 033 282      474    hash (2180x)
//    1 000   10 000   10 000  bplus_unclustered    4 017       261    hash
//      500   10 000   10 000  bplus_unclustered    2 009       249    hash
//      200   10 000   10 000  bplus_unclustered      802       243    hash
//      100   10 000   10 000  bplus_unclustered      401       240    hash
//       50   10 000   10 000  bplus_unclustered      201       240    INL
//      100   10 000   10 000  extendible_hash        201       240    INL
//      500   10 000   10 000  extendible_hash      1 005       249    hash
//    1 000    1 000    1 000  bplus_unclustered    4 015        48    hash
//
// De ahi salen los dos numeros con los que el join decide: una sonda cuesta 4,0
// paginas con B+ no agrupado (40 176 / 10 000) y 2,0 con hash extensible. La
// tercera fila es la que mas importa para el informe: con claves poco
// selectivas el INL no se degrada un poco, se degrada dos mil veces, porque
// cada fila externa arrastra 100 lecturas por RID.
//
// Y la fila que salva al INL de ser codigo muerto es la de |R| = 50: existe un
// regimen donde gana, y es el que la condicion tiene que reconocer. Sin esa
// fila, tener dos estrategias no se justificaria.
//
// Como se corre (no esta en el build, igual que el .py: es una herramienta)
//
//   cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build --parallel
//   g++ -std=c++20 -O2 -Icore/include docs/medir-condicion-join.cpp
//       build/core/libquipudb_core.a -o /tmp/medirjoin && /tmp/medirjoin
//
//   (las dos ultimas lineas son un solo comando; van sin la barra final para
//   no dejar un -Wcomment en el propio archivo que predica no tener warnings)
#include <cstdio>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

#include "quipudb/catalog/database.hpp"
#include "quipudb/catalog/table.hpp"
#include "quipudb/catalog/types.hpp"
#include "quipudb/catalog/record_codec.hpp"
#include "quipudb/storage/page.hpp"

using namespace quipudb;

namespace {

Schema esquema(const std::string& nombre) {
  Schema s;
  s.table_name = nombre;
  s.key_column = 0;
  s.columns.push_back(Column{"id", DataType::Int, 0});
  s.columns.push_back(Column{"fk", DataType::Int, 0});
  s.columns.push_back(Column{"relleno", DataType::Varchar, 24});
  return s;
}

std::size_t por_pagina(const Schema& s) {
  const RecordCodec codec{s};
  return (kDefaultPageSize - Page::kHeaderSize) / codec.size();
}

struct Medicion {
  std::uint64_t inl_pages = 0;
  std::uint64_t filas_salida = 0;
  std::size_t n_r = 0;  // paginas de la tabla externa
  std::size_t n_s = 0;  // paginas de la tabla interna
};

// R (externa) tiene `filas_r` filas; S (interna) tiene `filas_s` y un indice
// secundario sobre la columna de join. `distintas` controla la selectividad:
// cuantas claves distintas hay en S.
Medicion medir(std::size_t filas_r, std::size_t filas_s, std::size_t distintas,
               std::string_view kind_indice) {
  const auto dir = std::filesystem::temp_directory_path() / "quipudb_medir_join";
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);

  Database db(dir / "catalogo.txt");
  auto sr = esquema("r");
  auto ss = esquema("s");
  db.create_table(sr, kind::kHeap);
  db.create_table(ss, kind::kHeap);

  TableFile& r = db.table("r");
  TableFile& s = db.table("s");

  const std::string relleno(20, 'x');
  for (std::size_t i = 0; i < filas_s; ++i) {
    s.insert(Record{Value{static_cast<std::int32_t>(i)},
                    Value{static_cast<std::int32_t>(i % distintas)}, Value{relleno}});
  }
  for (std::size_t i = 0; i < filas_r; ++i) {
    r.insert(Record{Value{static_cast<std::int32_t>(i)},
                    Value{static_cast<std::int32_t>(i % distintas)}, Value{relleno}});
  }

  Index& ix = db.create_index("s", "por_fk", "fk", kind_indice);

  Medicion m;
  m.n_r = (filas_r + por_pagina(sr) - 1) / por_pagina(sr);
  m.n_s = (filas_s + por_pagina(ss) - 1) / por_pagina(ss);

  // --- index nested loop: recorrer R y sondear el indice de S por cada fila --
  r.reset_stats();
  s.reset_stats();
  ix.reset_stats();

  auto cur = r.cursor();
  Record fila;
  while (cur->next(fila)) {
    const auto rids = ix.search(fila[1]);
    for (const RID rid : rids) {
      const auto reg = s.read(rid);
      if (reg) ++m.filas_salida;
    }
  }
  cur.reset();

  m.inl_pages = r.stats().pages_read + s.stats().pages_read + ix.stats().pages_read;
  std::filesystem::remove_all(dir);
  return m;
}

}  // namespace

int main() {
  std::printf(
      "%-9s %-9s %-7s %-9s %12s %12s %10s %9s\n", "|R|", "|S|", "claves", "indice",
      "INL pags", "hash pags", "salida", "gana");
  std::printf("%s\n", std::string(84, '-').c_str());

  struct Caso {
    std::size_t r, s, distintas;
    std::string_view kind;
  };
  const std::vector<Caso> casos = {
      // El caso que el criterio C4 del #22 manda probar.
      {10000, 10000, 10000, kind::kBPlusUnclustered},
      {10000, 10000, 10000, kind::kExtendibleHash},
      {10000, 10000, 100, kind::kBPlusUnclustered},
      // Externa chica contra interna grande: donde el INL deberia ganar.
      {100, 10000, 10000, kind::kBPlusUnclustered},
      {10, 10000, 10000, kind::kBPlusUnclustered},
      {50, 10000, 10000, kind::kBPlusUnclustered},
      {200, 10000, 10000, kind::kBPlusUnclustered},
      {500, 10000, 10000, kind::kBPlusUnclustered},
      {1000, 10000, 10000, kind::kBPlusUnclustered},
      {100, 10000, 10000, kind::kExtendibleHash},
      {500, 10000, 10000, kind::kExtendibleHash},
      // Ambas chicas.
      {1000, 1000, 1000, kind::kBPlusUnclustered},
  };

  for (const auto& c : casos) {
    const Medicion m = medir(c.r, c.s, c.distintas, c.kind);
    // Grace hash join sin re-particionado: se lee todo y se escribe todo al
    // particionar (2(N_R+N_S)), y se vuelve a leer todo en la fase de sondeo
    // (N_R+N_S).
    const std::uint64_t hash_pages = 3ULL * (m.n_r + m.n_s);
    const char* gana = m.inl_pages < hash_pages ? "INL" : "hash";
    std::printf("%-9zu %-9zu %-7zu %-9s %12llu %12llu %10llu %9s\n", c.r, c.s, c.distintas,
                std::string(c.kind).c_str(), (unsigned long long)m.inl_pages,
                (unsigned long long)hash_pages, (unsigned long long)m.filas_salida, gana);
  }
  return 0;
}
