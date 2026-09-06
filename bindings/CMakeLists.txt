# Bindings de Python. Se compilan solo con -DQUIPUDB_BUILD_PYTHON=ON para que
# quien trabaja en engine/, frontend/ o benchmarks/ no necesite pybind11.
include(FetchContent)
FetchContent_Declare(pybind11
  GIT_REPOSITORY https://github.com/pybind/pybind11.git
  GIT_TAG        v2.13.6)
FetchContent_MakeAvailable(pybind11)

pybind11_add_module(quipudb_native module.cpp)
target_link_libraries(quipudb_native PRIVATE quipudb_core)
