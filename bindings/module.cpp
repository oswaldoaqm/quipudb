#include <pybind11/pybind11.h>

#include "quipudb/version.hpp"

namespace py = pybind11;

// Superficie que el core expone a Python. Cada estructura nueva del core que
// deba ser visible desde engine/ se registra aqui.
PYBIND11_MODULE(quipudb_native, m) {
  m.doc() = "Bindings del core de QuipuDB";
  m.def("version", &quipudb::version, "Version del motor");
}
