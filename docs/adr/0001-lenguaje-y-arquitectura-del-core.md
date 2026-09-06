# ADR 0001 - Core en C++ con capa Python encima

- **Fecha:** 2026-09-06
- **Estado:** Aceptada

## Contexto

El proyecto exige medir rendimiento con datasets de hasta 100 000 registros y
comparar las estructuras propias contra PostgreSQL (secciones 2.1.6, 2.2.4,
2.3.2 y 2.4.4). Al mismo tiempo, cuatro de las cinco secciones de la Parte 1
(parser SQL, transacciones, frontend y benchmarks) se desarrollan mas rapido en
Python, y el equipo ya tiene experiencia previa en Python con los laboratorios
01, 02 y 03 del curso.

## Decision

El core del motor (gestion de archivos, indices y external algorithms) se
implementa en C++20. La capa de parser SQL, transacciones, planner y API REST
se implementa en Python y consume el core a traves de bindings pybind11.

## Consecuencias

- Los benchmarks contra PostgreSQL se hacen sobre una implementacion comparable
  en lenguaje, y no penalizada por el interprete.
- Se agrega un paso de compilacion. Para que no bloquee a quien no trabaja en el
  core, los bindings se compilan solo con `-DQUIPUDB_BUILD_PYTHON=ON` y el resto
  del equipo trabaja contra una interfaz Python estable.
- El contrato entre core y capa Python (firmas de scan, search, range_search,
  insert, remove y la forma del plan de ejecucion) pasa a ser un artefacto de
  diseno explicito, no un detalle de implementacion. Se llama `remove` y no
  `delete` porque `delete` es palabra reservada de C++ y no puede ser nombre
  de metodo.
