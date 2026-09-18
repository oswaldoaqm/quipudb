# Comparacion de B+ agrupado, B+ no agrupado y Extendible Hashing — issue #40

## Estado y trazabilidad

**Corrida oficial completada: 315 mediciones, 63 resumenes y un CSV de entorno.**
Se ejecuto la suite una sola vez: 21 casos por tamano, un calentamiento y cinco
repeticiones medidas. No se descartaron outliers ni se repitieron casos
selectivamente. No se ejecutaron tests, Ruff ni diagnosticos auxiliares durante
las mediciones. No se modifico la metodologia ni se generaron graficas.

ID: `20260918T035512Z_4e75ba53c6a6497798a0d95a278f9dfe`.
Inicio: 2026-09-18T03:55:12.220013+00:00 (17 de septiembre, 22:55:12, America/Lima).
Etiqueta: `declarado.proposito=experimento_oficial_issue_40`.

- [Mediciones oficiales](results/20260918T035512Z_4e75ba53c6a6497798a0d95a278f9dfe_mediciones.csv)
- [Resumen oficial](results/20260918T035512Z_4e75ba53c6a6497798a0d95a278f9dfe_resumen.csv)
- [Entorno oficial](results/20260918T035512Z_4e75ba53c6a6497798a0d95a278f9dfe_entorno.csv)

Son archivos nuevos ignorados por Git. No se mezclaron con la validacion 1k
`20260918T034824Z_5cd7bb085a15406aa748511983335994`. Deben conservarse aparte
del repositorio; los enlaces requieren tener esos CSV en la carpeta results.

## Entorno verificado y auditoria

- Apple M2, Mac14,7, 8589934592 bytes de RAM (8 GiB), consultados con sysctl.
- macOS 26.2 arm64; CPython 3.14.6.
- SSD interno APFS, volumen Data disk3s5; temporales en /private/tmp, mismo
  volumen verificado con diskutil y df.
- Bindings Release, AppleClang 17.0.0.17000319, -O3 -DNDEBUG y LTO:
  comprobados en CMakeCache.txt, CMakeCXXCompiler.cmake y flags.make.
- Python: `/Users/mauricioteran/anaconda3/python.app/Contents/MacOS/python`.
- Modulo: `build-py/bindings/quipudb_native.cpython-314-darwin.so`.
- SHA-256 del modulo: `2e603d63646294c59e6a23a43ae1a9051c914ffc7866b09db03e9edf396184a4`.
- Commit: `9dd1c30531c2be340f99a14e13ba45cfe151de98`; arbol con cambios locales de #40.
- Pagina real: 4096 bytes; no se sobrescribio el valor predeterminado.
- Se registraron hashes de datasets/scripts y se comprobaron al terminar:
  permanecen iguales. El commit solo no identifica los cambios locales.

Hardware, build y hashes se consultaron automaticamente antes de medir y se
pasaron por --entorno: quedan bajo declarado.*, con sus fuentes. Solo la etiqueta
de proposito es manual. Los campos generales no_documentado se complementan, no
se sobrescriben. La configuracion del build y su hash no certifican un commit
embebido en el binario.

La auditoria comprobo 21 casos x 3 tamanos y cinco muestras 1..5 por grupo; todas
con estado ok, cantidades de operaciones correctas, metricas finitas/no negativas
y tiempos positivos. Se recalcularon medianas, minimos y maximos. Paginas y bytes
fueron identicos entre las cinco repeticiones de cada caso/tamano. Las consultas
no registraron escrituras. Los hashes de selecciones coinciden entre tecnicas.
No hay filas ficticias para capacidades no soportadas.

## Protocolo ejecutado

Se reutilizo el banco de #38 sin cambios: estado independiente, preparacion fuera
del reloj, captura inmediata de contadores, validacion posterior, limpieza y CSV.
Mismos datasets, orden mezclado y semilla 20260906; 1000 consultas exitosas de
igualdad y 100 rangos por lote. Cada rango contiene 1% de N: 10, 100 o 1000 filas.

Parametros, con el Python y metadatos verificados anteriores:

```bash
PYTHONPATH=build-py/bindings python -B benchmarks/scripts/ejecutar_benchmarks.py --suite indices --tamanos 1000 10000 100000 --consultas 1000 --consultas-rango 100 --calentamientos 1 --repeticiones 5 --temporales /private/tmp --entorno proposito=experimento_oficial_issue_40
```

Carga indexada mide N altas logicas y flush; construccion secundaria mide
create_index completo y flush sobre Heap previamente cargado. Igualdad/rango
recuperan registros completos, incluyendo read de cada RID en secundarios.
Recorrido ordenado es una consulta completa de N filas, no ordenar un CSV en RAM.

Altas y bajas incrementales miden D=N//10 operaciones sobre N iniciales.
Mantenimiento mixto mide tres ciclos de D bajas y D reinserciones usando el nuevo
RID en cada alta y un unico flush final: 600, 6000 o 60000 operaciones logicas.
Seleccion, carga previa, validacion y exportacion quedan fuera de esos tiempos.

Hash no soporta rangos ni orden nativos. Agrupado no tiene un indice secundario
que construir sobre Heap. Esos casos no se emulan ni se representan con tiempo cero.

## Los 63 resultados resumidos

Medianas en **milisegundos**; cada celda representa cinco muestras. Igualdad mide
1000 consultas; rango 100; recorrido y construccion una operacion completa. No
comparar tiempos de lotes diferentes como si fueran latencias individuales.

| Caso | 1k (ms) | 10k (ms) | 100k (ms) |
|---|---:|---:|---:|
| bplus_clustered_carga_indexada | 28.957708 | 310.470666 | 3580.329625 |
| bplus_unclustered_carga_indexada | 45.038667 | 498.869208 | 5319.867459 |
| extendible_hash_carga_indexada | 23.785709 | 251.548334 | 2381.193916 |
| bplus_unclustered_construccion_indice | 35.652084 | 396.648458 | 4194.736583 |
| extendible_hash_construccion_indice | 15.812208 | 141.094875 | 1299.946542 |
| bplus_clustered_busqueda_igualdad | 6.401959 | 6.907083 | 9.525416 |
| bplus_unclustered_busqueda_igualdad | 13.384375 | 13.198208 | 15.107042 |
| extendible_hash_busqueda_igualdad | 8.080250 | 8.797209 | 8.937459 |
| bplus_clustered_busqueda_rango | 1.261084 | 4.743666 | 42.202083 |
| bplus_unclustered_busqueda_rango | 4.651375 | 59.737250 | 386.676041 |
| bplus_clustered_recorrido_ordenado | 0.269375 | 3.221833 | 39.324208 |
| bplus_unclustered_recorrido_ordenado | 3.645000 | 38.490708 | 411.402292 |
| bplus_clustered_insercion_incremental | 2.945292 | 31.722625 | 374.333667 |
| bplus_unclustered_insercion_incremental | 4.950334 | 51.254583 | 555.642500 |
| extendible_hash_insercion_incremental | 1.691792 | 17.367541 | 738.599208 |
| bplus_clustered_eliminacion_incremental | 4.713666 | 31.981417 | 446.205042 |
| bplus_unclustered_eliminacion_incremental | 6.058625 | 68.284750 | 857.125875 |
| extendible_hash_eliminacion_incremental | 3.260000 | 33.678625 | 352.550584 |
| bplus_clustered_mantenimiento_mixto | 20.183375 | 184.543375 | 2310.814167 |
| bplus_unclustered_mantenimiento_mixto | 31.521000 | 365.884083 | 4120.714708 |
| extendible_hash_mantenimiento_mixto | 14.830416 | 152.078292 | 1589.558167 |

Dividir la mediana del lote por 1000 (igualdad) o 100 (rango) da el promedio por
consulta del lote representativo, no la mediana de latencias individuales.
El anexo conserva minimos, maximos y paginas de los 63 experimentos.

## Espacio real

Bytes reales tras flush. El total inicial corresponde a carga indexada con N
vivos; tras altas hay N+D. El cero del agrupado significa que no hay archivo
secundario: el arbol esta integrado en datos_bytes, no que indexar sea gratis.

| N inicial | Tecnica | Datos iniciales B | Indice inicial B | Total inicial B | Total tras D altas B |
|---:|---|---:|---:|---:|---:|
| 1000 | B+ agrupado | 61440 | 0 | 61440 | 65536 |
| 1000 | B+ no agrupado | 36864 | 24576 | 61440 | 61440 |
| 1000 | Hash | 36864 | 24576 | 61440 | 61440 |
| 10000 | B+ agrupado | 462848 | 0 | 462848 | 512000 |
| 10000 | B+ no agrupado | 299008 | 139264 | 438272 | 483328 |
| 10000 | Hash | 299008 | 139264 | 438272 | 466944 |
| 100000 | B+ agrupado | 4657152 | 0 | 4657152 | 5083136 |
| 100000 | B+ no agrupado | 2932736 | 1409024 | 4341760 | 4800512 |
| 100000 | Hash | 2932736 | 1056768 | 3989504 | 5328896 |

En 1k las tres organizaciones ocuparon el mismo total inicial. En 10k ambos
secundarios empataron y ocuparon menos que el agrupado. En 100k Hash tuvo el
menor total inicial, seguido del B+ no agrupado y del agrupado.

Despues de las altas de 100k, el orden cambio: B+ no agrupado 4800512 B, agrupado
5083136 B, Hash 5328896 B. En Hash el archivo secundario paso de 1056768 a
2105344 B: el aumento no es solo el crecimiento comun del Heap.

Las bajas dejaron N-D vivos sin reducir la longitud de los archivos en los nueve
casos. Los ciclos mixtos terminaron con N vivos y el mismo espacio que al empezar.
Es compatible con reutilizacion; no demuestra cuantos merges ocurrieron ni exige
truncamiento fisico. No se mezclan esos estados con N+D registros al comparar.

## Muestras altas y comportamientos conservados

Se listan los grupos cuyo maximo fue al menos 1.5 veces la mediana. Es una regla
descriptiva de inspeccion, no un filtro ni una prueba estadistica de descarte.
Se muestran las cinco muestras en orden de repeticion, todas conservadas:

- **bplus_unclustered_busqueda_igualdad, N=1000:** 13.384375, 14.329541, 21.009458, 13.016542, 13.148250 ms. Mediana 13.384375 ms; maximo/mediana 1.57x.
- **extendible_hash_busqueda_igualdad, N=10000:** 19.662708, 8.404750, 8.797209, 33.066416, 8.458250 ms. Mediana 8.797209 ms; maximo/mediana 3.76x.
- **bplus_clustered_busqueda_rango, N=10000:** 4.367083, 4.743666, 4.568584, 10.908083, 4.800041 ms. Mediana 4.743666 ms; maximo/mediana 2.30x.

No se atribuyen las muestras altas a una causa especifica. Los contadores y bytes
fueron estables, pero cache, planificacion del SO y frecuencia de CPU no se controlan.

### Crecimiento de Hash en 100k

El lote de 10000 altas tardo 738.599208 ms, frente a 374.333667 del agrupado y
555.642500 del no agrupado. Hash registro 126170/125473 paginas leidas/escritas,
frente a 1993/2000 en su lote de 1000 altas sobre N=10000. La expansion del
archivo secundario casi al doble acompana el salto de E/S y tiempo.

Los contadores/bytes fueron iguales en las cinco muestras, no un pico aislado.
Es compatible con crecimiento y redistribucion, pero no se afirma un numero de
splits o duplicaciones del directorio: los bindings no exponen esos contadores.
Las altas usan claves nuevas mayores que las existentes, no cualquier carga posible.

### Igualdad, rangos y paginas

Hash registro 2000 lecturas por lote de igualdad en los tres N (indice mas Heap).
Agrupado registro 2000, 2000 y 3000; no agrupado, 4006, 4002 y 4008. Todos
devolvieron registros completos, no RIDs solamente.

Agrupado fue mas rapido en igualdad en 1k/10k. En 100k Hash tuvo una mediana
ligeramente menor: 8.937459 frente a 9.525416 ms. No generalizar esa ventaja
pequena: es un entorno, cinco muestras y distintos caminos Python/binding.

En rangos de 100k, agrupado leyo 1518 paginas frente a 100645 del no agrupado;
en recorrido completo, 1119 frente a 100342. Recuperar filas del Heap forma parte
de la diferencia medida, no debe excluirse de sus contadores.

## Conclusiones sustentadas por esta corrida

1. **Carga inicial y construccion secundaria:** Hash tuvo la menor mediana de
   carga indexada en los tres tamanos. Construir Hash sobre Heap existente tambien
   fue mas rapido que construir el B+ secundario en los tres N. Son operaciones
   diferentes; no se restaron tiempos para inventar costos aislados.
2. **Igualdad:** agrupado fue el mas rapido en 1k/10k; Hash en 100k, por una
   diferencia pequena. No agrupado fue el mas lento en esta consulta de registros
   completos. No se concluye que Hash gane siempre.
3. **Rangos y orden:** agrupado tuvo ventaja sobre no agrupado en los tres N.
   Los cocientes no agrupado/agrupado fueron 3.69x, 12.59x y 9.16x en rangos,
   y 13.53x, 11.95x y 10.46x en recorrido ordenado. Hash no ofrece esas capacidades
   nativas; no hay un tiempo cero comparable.
4. **Altas incrementales:** Hash fue mas rapido en 1k/10k, pero el mas lento en
   100k al crecer su archivo. Agrupado tuvo la menor mediana en ese ultimo caso.
   La ventaja de carga inicial no garantiza menor costo de crecimiento.
5. **Bajas:** Hash fue mas rapido en 1k/100k; agrupado en 10k. **Mixto:** Hash
   tuvo la menor mediana en los tres tamanos, seguido del agrupado. El lote mixto
   no equivale al ensayo de crecimiento con claves nuevas.
6. **Espacio:** Hash tuvo el menor total inicial en 100k, pero el mayor tras las
   altas. Considerar almacenamiento inicial y expansion; en secundarios contar
   Heap mas indice y en agrupado todo el archivo integrado.

En este escenario, agrupado es un candidato favorable para recuperar filas por
rango u orden. Hash lo es para carga y ciclos de mantenimiento sin requerir orden,
con atencion a expansiones; la igualdad depende tambien del tamano medido.
No agrupado ofrece rango/orden manteniendo Heap, pagando resolucion de RIDs.
No se evaluaron duplicados, otras columnas, concurrencia ni otras selectividades;
estas conclusiones no son universales.

## Interpretacion y limites

- La comparacion principal recupera registros completos. Los secundarios incluyen
  search/range/scan del indice y read de cada RID dentro del cronometro, y suman
  las paginas del Heap y del indice. No se compara solo RIDs contra filas completas.
- El costo observado incluye Python/bindings. lookup y lookup_range existen en C++
  pero no estan expuestos; no se modifican bindings para esta comparacion.
- create_index incluye catalogo, archivo, recorrido y construccion. No es un
  build aislado; tampoco se resta un tiempo de Heap medido en otra corrida.
- datos_bytes del agrupado incluye TODO su archivo. indices_bytes=0 significa
  que no existe archivo secundario, no indexacion gratuita. Comparar el total.
- Para los secundarios, espacio adicional es el archivo de indice. Para el
  agrupado, la diferencia con un Heap equivalente es diferencia entre
  organizaciones, no una descomposicion exacta de paginas de indice/datos.
- Los merges pueden liberar paginas para reutilizacion sin truncar el archivo.
  No se infieren merges, splits, altura o buckets desde el tamano ni se inventan
  contadores que los bindings no exponen.
- Las paginas son contadores reales instrumentados, no todas las E/S fisicas:
  las escrituras de metadata mediante write_meta no incrementan necesariamente
  pages_written de la estructura. Tampoco se mide RAM ni pico de espacio.
- Cache del SO y frecuencia de CPU no se controlan; flush no es fsync. Conservar
  todas las muestras, interpretar dispersion y no generalizar un unico equipo.
- Las altas nuevas usan claves N+c para una muestra de codigos c existentes,
  todas mayores que las claves iniciales, aunque mezcladas entre si. Este es un
  escenario de crecimiento de claves; no representa insercion uniforme en huecos.
- Los ciclos mixtos repiten la misma muestra de 10% de claves, no vacian la tabla
  ni garantizan forzar cada tipo de rebalanceo. Terminan con los N registros originales.


## Anexo: dispersion y paginas

Paginas: medianas de contadores instrumentados, no E/S fisicas. Las 315 muestras
y sus bytes exactos se conservan en los CSV originales.

| N | Caso | Min ms | Max ms | Paginas leidas | Paginas escritas |
|---:|---|---:|---:|---:|---:|
| 1000 | bplus_clustered_carga_indexada | 28.604542 | 29.764125 | 3755 | 1024 |
| 1000 | bplus_unclustered_carga_indexada | 44.165958 | 55.107750 | 2584 | 2006 |
| 1000 | extendible_hash_carga_indexada | 23.207834 | 31.826875 | 3239 | 3238 |
| 1000 | bplus_unclustered_construccion_indice | 34.974125 | 35.972834 | 1600 | 1006 |
| 1000 | extendible_hash_construccion_indice | 14.351708 | 19.908250 | 2255 | 2240 |
| 1000 | bplus_clustered_busqueda_igualdad | 6.293041 | 9.291458 | 2000 | 0 |
| 1000 | bplus_unclustered_busqueda_igualdad | 13.016542 | 21.009458 | 4006 | 0 |
| 1000 | extendible_hash_busqueda_igualdad | 8.067875 | 8.431583 | 2000 | 0 |
| 1000 | bplus_clustered_busqueda_rango | 1.228041 | 1.359208 | 307 | 0 |
| 1000 | bplus_unclustered_busqueda_rango | 4.639000 | 4.799917 | 1305 | 0 |
| 1000 | bplus_clustered_recorrido_ordenado | 0.256625 | 0.278792 | 13 | 0 |
| 1000 | bplus_unclustered_recorrido_ordenado | 3.641834 | 3.755375 | 1004 | 0 |
| 1000 | bplus_clustered_insercion_incremental | 2.917167 | 3.023916 | 401 | 102 |
| 1000 | bplus_unclustered_insercion_incremental | 4.828875 | 5.122125 | 300 | 200 |
| 1000 | extendible_hash_insercion_incremental | 1.679458 | 1.735334 | 200 | 200 |
| 1000 | bplus_clustered_eliminacion_incremental | 4.630167 | 4.727459 | 762 | 202 |
| 1000 | bplus_unclustered_eliminacion_incremental | 6.053167 | 6.181750 | 900 | 200 |
| 1000 | extendible_hash_eliminacion_incremental | 3.199834 | 3.302125 | 800 | 200 |
| 1000 | bplus_clustered_mantenimiento_mixto | 19.852584 | 21.259625 | 2982 | 714 |
| 1000 | bplus_unclustered_mantenimiento_mixto | 31.376959 | 32.007916 | 3600 | 1200 |
| 1000 | extendible_hash_mantenimiento_mixto | 14.701875 | 15.015666 | 3000 | 1200 |
| 10000 | bplus_clustered_carga_indexada | 304.233875 | 315.551459 | 39853 | 10220 |
| 10000 | bplus_unclustered_carga_indexada | 495.412875 | 501.271375 | 29548 | 20062 |
| 10000 | extendible_hash_carga_indexada | 239.834500 | 268.589791 | 32798 | 32777 |
| 10000 | bplus_unclustered_construccion_indice | 391.948333 | 418.740667 | 19692 | 10062 |
| 10000 | extendible_hash_construccion_indice | 140.659875 | 146.732708 | 22942 | 22779 |
| 10000 | bplus_clustered_busqueda_igualdad | 6.850375 | 7.288334 | 2000 | 0 |
| 10000 | bplus_unclustered_busqueda_igualdad | 13.155833 | 13.362167 | 4002 | 0 |
| 10000 | extendible_hash_busqueda_igualdad | 8.404750 | 33.066416 | 2000 | 0 |
| 10000 | bplus_clustered_busqueda_rango | 4.367083 | 10.908083 | 415 | 0 |
| 10000 | bplus_unclustered_busqueda_rango | 40.566917 | 70.518000 | 10325 | 0 |
| 10000 | bplus_clustered_recorrido_ordenado | 3.189625 | 3.367667 | 111 | 0 |
| 10000 | bplus_unclustered_recorrido_ordenado | 37.714750 | 44.242625 | 10032 | 0 |
| 10000 | bplus_clustered_insercion_incremental | 31.292041 | 32.033416 | 4012 | 1024 |
| 10000 | bplus_unclustered_insercion_incremental | 50.605000 | 52.139792 | 2997 | 2008 |
| 10000 | extendible_hash_insercion_incremental | 17.240208 | 20.123208 | 1993 | 2000 |
| 10000 | bplus_clustered_eliminacion_incremental | 31.922625 | 32.544833 | 5215 | 1075 |
| 10000 | bplus_unclustered_eliminacion_incremental | 68.181584 | 68.549542 | 9008 | 2000 |
| 10000 | extendible_hash_eliminacion_incremental | 33.610333 | 47.963375 | 8000 | 2000 |
| 10000 | bplus_clustered_mantenimiento_mixto | 183.254709 | 187.142916 | 27267 | 6075 |
| 10000 | bplus_unclustered_mantenimiento_mixto | 364.025541 | 369.053375 | 36024 | 12000 |
| 10000 | extendible_hash_mantenimiento_mixto | 151.318041 | 152.334917 | 30000 | 12000 |
| 100000 | bplus_clustered_carga_indexada | 3551.325209 | 3754.260750 | 578232 | 102266 |
| 100000 | bplus_unclustered_carga_indexada | 5283.398166 | 5793.248750 | 299215 | 200682 |
| 100000 | extendible_hash_carga_indexada | 2354.908042 | 2514.323125 | 305118 | 305068 |
| 100000 | bplus_unclustered_construccion_indice | 4162.284208 | 4440.986333 | 200645 | 100682 |
| 100000 | extendible_hash_construccion_indice | 1296.408000 | 1336.772167 | 206548 | 205070 |
| 100000 | bplus_clustered_busqueda_igualdad | 9.513583 | 9.798708 | 3000 | 0 |
| 100000 | bplus_unclustered_busqueda_igualdad | 14.955792 | 15.272792 | 4008 | 0 |
| 100000 | extendible_hash_busqueda_igualdad | 8.788291 | 8.999083 | 2000 | 0 |
| 100000 | bplus_clustered_busqueda_rango | 40.984125 | 50.045583 | 1518 | 0 |
| 100000 | bplus_unclustered_busqueda_rango | 380.108584 | 399.837667 | 100645 | 0 |
| 100000 | bplus_clustered_recorrido_ordenado | 38.514125 | 40.985500 | 1119 | 0 |
| 100000 | bplus_unclustered_recorrido_ordenado | 407.536042 | 427.521708 | 100342 | 0 |
| 100000 | bplus_clustered_insercion_incremental | 369.047875 | 414.852459 | 60104 | 10208 |
| 100000 | bplus_unclustered_insercion_incremental | 548.673916 | 578.771916 | 29970 | 20082 |
| 100000 | extendible_hash_insercion_incremental | 736.684000 | 757.482500 | 126170 | 125473 |
| 100000 | bplus_clustered_eliminacion_incremental | 431.383583 | 469.625000 | 85831 | 12169 |
| 100000 | bplus_unclustered_eliminacion_incremental | 851.427250 | 919.684583 | 100060 | 23900 |
| 100000 | extendible_hash_eliminacion_incremental | 348.744750 | 383.711250 | 80000 | 20000 |
| 100000 | bplus_clustered_mantenimiento_mixto | 2293.812084 | 2400.766333 | 428634 | 63150 |
| 100000 | bplus_unclustered_mantenimiento_mixto | 4103.163083 | 4364.182416 | 377041 | 126625 |
| 100000 | extendible_hash_mantenimiento_mixto | 1567.809792 | 1606.945000 | 300000 | 120000 |

## Salida para #41 y conservacion

Conservar los tres CSV oficiales juntos y separados de la validacion pequena.
Agrupar por N, tecnica, caso, operacion y n_operaciones, sin mezclar configuraciones.
No se generaron graficas. Antes del commit, revisar protocolo, informe y CSV:
como estan ignorados, el commit del informe NO guarda las mediciones; deben
preservarse mediante el mecanismo de entrega acordado por el equipo.
