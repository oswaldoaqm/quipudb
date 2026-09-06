cmake_minimum_required(VERSION 3.20)
project(quipudb VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

option(QUIPUDB_BUILD_TESTS   "Compilar las pruebas del core"        ON)
option(QUIPUDB_BUILD_PYTHON  "Compilar los bindings de Python"      OFF)

add_subdirectory(core)

if(QUIPUDB_BUILD_PYTHON)
  add_subdirectory(bindings)
endif()
