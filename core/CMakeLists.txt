# Recolecta los .cpp de cada modulo. Al agregar un archivo nuevo basta con
# volver a configurar CMake (cmake -S . -B build).
file(GLOB_RECURSE QUIPUDB_CORE_SOURCES CONFIGURE_DEPENDS src/*.cpp)

add_library(quipudb_core ${QUIPUDB_CORE_SOURCES})

target_include_directories(quipudb_core
  PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)

# El core es una biblioteca estatica, pero los bindings (#23) la enlazan dentro
# de un modulo COMPARTIDO. Sin codigo independiente de la posicion, ese enlace
# falla con "relocation R_X86_64_PC32 ... can not be used when making a shared
# object". Se pide siempre y no solo con QUIPUDB_BUILD_PYTHON=ON para que las
# pruebas del core y los bindings compilen exactamente el mismo objeto: si solo
# se activara con los bindings, un fallo que dependa de -fPIC no aparecería en
# la corrida normal de ctest.
set_target_properties(quipudb_core PROPERTIES POSITION_INDEPENDENT_CODE ON)

target_compile_options(quipudb_core PRIVATE
  $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall -Wextra -Wpedantic>
  $<$<CXX_COMPILER_ID:MSVC>:/W4>)

if(QUIPUDB_BUILD_TESTS)
  file(GLOB_RECURSE QUIPUDB_TEST_SOURCES CONFIGURE_DEPENDS tests/*.cpp)
  if(QUIPUDB_TEST_SOURCES)
    include(FetchContent)
    FetchContent_Declare(googletest
      GIT_REPOSITORY https://github.com/google/googletest.git
      GIT_TAG        v1.15.2)
    set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
    FetchContent_MakeAvailable(googletest)

    add_executable(quipudb_tests ${QUIPUDB_TEST_SOURCES})
    target_link_libraries(quipudb_tests PRIVATE quipudb_core GTest::gtest_main)

    include(GoogleTest)
    enable_testing()
    gtest_discover_tests(quipudb_tests)
  endif()
endif()
