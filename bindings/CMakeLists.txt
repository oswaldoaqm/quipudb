# Bindings de Python. Se compilan solo con -DQUIPUDB_BUILD_PYTHON=ON para que
# quien trabaja en engine/, frontend/ o benchmarks/ no necesite pybind11.
include(FetchContent)
FetchContent_Declare(pybind11
  GIT_REPOSITORY https://github.com/pybind/pybind11.git
  GIT_TAG        v2.13.6)
FetchContent_MakeAvailable(pybind11)

pybind11_add_module(quipudb_native module.cpp)
target_link_libraries(quipudb_native PRIVATE quipudb_core)

# En Windows con MinGW, el modulo queda dependiendo de libstdc++-6.dll y
# libgcc_s_seh-1.dll. Desde Python 3.8 Windows NO usa el PATH para resolver las
# dependencias de un modulo de extension, asi que importar falla con
# "DLL load failed while importing quipudb_native" aunque el .pyd este donde
# debe -- y el mensaje no dice que DLL falta, que es lo que lo hace dificil de
# diagnosticar.
#
# Se enlaza el runtime estaticamente: el .pyd queda autocontenido y se puede
# copiar a una maquina sin MinGW. Cuesta algo de tamano y se paga una sola vez.
if(MINGW)
  target_link_options(quipudb_native PRIVATE
    -static-libgcc -static-libstdc++ -static)
endif()
