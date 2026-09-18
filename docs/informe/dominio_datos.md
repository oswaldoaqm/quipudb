# Dominio y representación de datos

[Volver al informe de la Parte 1](parte_1.md).

Estado: síntesis técnica del dominio realmente empleado, sin atribución de
autoría individual. Este capítulo separa tres fuentes de datos que no deben
confundirse: experimentos, pruebas automatizadas y demostración visual.

## Dataset experimental académico sintético

| Campo | Tipo nativo | Regla del generador |
|---|---|---|
| `codigo` | `INT` | Clave única: exactamente `1..N` |
| `nombre` | `VARCHAR(16)` | `alumno` seguido del código; texto ASCII que cabe en 16 bytes/caracteres |
| `promedio` | `DOUBLE` | Valor entre 0.00 y 20.00, generado en centésimas |

Se usa N=1000, 10000 o 100000. No son alumnos reales ni una muestra estadística
de una institución: sirven para comparar estructuras con el mismo contenido.
El esquema nativo selecciona `codigo` como clave mediante `key_column=0`.
El intervalo del promedio es una regla del dataset; no implica soporte SQL de
una restricción `CHECK`. Tampoco se definen aquí relaciones ni claves foráneas.

El [generador existente](../../benchmarks/scripts/generar_datasets.py) crea para
cada tamaño un `random.Random(20260906)` independiente. Construye los códigos,
genera enteros de 0 a 2000 para los promedios y mezcla los registros con ese
generador local. La conversión a CSV representa dos decimales sin depender de
redondear una cadena obtenida de un float aleatorio.

El formato es CSV UTF-8 sin BOM, separador coma, fin de línea LF y cabecera
`codigo,nombre,promedio`. El orden mezclado se conserva al cargar las técnicas;
no se ordena el CSV para beneficiar una estructura. Generar un tamaño individual
produce el mismo contenido que generarlo junto con los otros en el mismo entorno.
Para identificar las entradas de las corridas ya realizadas se conservan hashes
y metadatos: una semilla por sí sola no es una garantía universal entre versiones
de Python o cambios futuros del generador.

La especificación, las pruebas de cantidad/unicidad/rango/reproducibilidad y los
protocolos están en el [README de benchmarks](../../benchmarks/README.md),
[test_generar_datasets.py](../../benchmarks/test_generar_datasets.py) y
[test_banco_pruebas.py](../../benchmarks/test_banco_pruebas.py).

## Representación dentro del motor

El [sistema de tipos](../../core/include/quipudb/catalog/types.hpp) y
[RecordCodec](../../core/src/catalog/record_codec.cpp) usan registros de ancho
fijo: `INT` ocupa 4 bytes, `DOUBLE` 8, `BOOL` 1, `DATE` 4 y `VARCHAR(n)` reserva
n bytes. `DATE` representa días respecto de la época; el texto se completa con
ceros y no admite un carácter nulo embebido. El límite de bytes importa para
texto no ASCII; los nombres experimentales evitan esa ambigüedad.

El payload del registro común ocupa **28 bytes: 4 + 16 + 8**. No es su costo
total en disco: deben añadirse estados de slots, páginas, metadatos, espacio libre
y estructuras de índices según el caso. El CSV textual tampoco representa el
tamaño del archivo nativo. Un `DOUBLE` usa representación binaria y no constituye
un tipo decimal exacto, aunque el CSV se publique con dos decimales.

Los RIDs identifican página y slot, no una clave lógica. Su estabilidad depende
de la organización: las tablas ordenadas pueden mover registros; por eso los
índices secundarios del catálogo se restringen a Heap. Incluso en Heap, un slot
eliminado puede reutilizarse y un RID antiguo no debe conservarse como identidad
permanente después de borrar su registro.

Referencias de validación: [pruebas del codec](../../core/tests/catalog/record_codec_test.cpp)
y [pruebas de catálogo](../../core/tests/catalog/catalog_test.cpp).

## Datos de pruebas automatizadas

Los tests del core y del engine crean esquemas y registros pequeños específicos:
tipos, límites de páginas, duplicados, splits, borrados, errores y persistencia.
No todos usan `VARCHAR(16)` ni los tamaños oficiales. Esos fixtures verifican
comportamiento; no se agregan a las muestras experimentales ni reemplazan sus CSV.

## Datos simulados del frontend

[Los mocks](../../frontend/src/api/mock/datos.ts) presentan `alumnos`, `cursos`,
`matriculas` y `profesores`. El mock de alumnos usa códigos desde 1000,
`nombre VARCHAR(32)` y promedios de su propia generación, no el dataset común.
Las otras tablas enriquecen la demostración de la interfaz; no demuestran que
existan tablas, relaciones o restricciones equivalentes en una base nativa.

Hay incluso una combinación ilustrativa de tabla secuencial e índice secundario
en los mocks que el catálogo nativo no permite. Se documenta como diferencia
del material visual, no como capacidad del motor. Sus tiempos y páginas son
simulados: solo los datos del [capítulo experimental](comparacion_experimental.md)
constituyen evidencia de las corridas oficiales.
