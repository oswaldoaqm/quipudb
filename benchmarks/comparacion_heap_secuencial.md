# Comparacion Heap File vs Archivo Secuencial Paginado — issue #39

## Estado del informe

Corrida oficial completada: **90 mediciones y 18 filas de resumen**, sin errores
ni casos incompletos. Cinco muestras medidas por cada uno de los seis casos y
tres tamanos; los calentamientos no entran al resumen. No se descarto ninguna muestra.

Identificador: `20260918T025045Z_530093b2675c4ff8a483020858915b56`.
Inicio: 2026-09-18 02:50:45 UTC (2026-09-17 21:50:45, America/Lima).
Etiqueta del entorno: `declarado.proposito=experimento_oficial_issue_39`.

- [Mediciones oficiales](results/20260918T025045Z_530093b2675c4ff8a483020858915b56_mediciones.csv)
- [Resumen oficial](results/20260918T025045Z_530093b2675c4ff8a483020858915b56_resumen.csv)
- [Entorno oficial](results/20260918T025045Z_530093b2675c4ff8a483020858915b56_entorno.csv)

Los tres CSV estan ignorados por Git y deben conservarse aparte del repositorio.
No se mezclaron con la validacion pequena anterior, identificada por
`20260918T024318Z_97ec441bbe854b1b8ead2e55d1225461`. No se generaron graficas.

## Entorno verificado

| Dato | Valor y fuente |
|---|---|
| Sistema | macOS 26.2, arm64; deteccion del banco |
| CPU/modelo | Apple M2 / Mac14,7; `sysctl` |
| RAM | 8589934592 bytes (8 GiB); `sysctl hw.memsize` |
| Disco temporal | SSD interno, APFS, volumen Data `disk3s5`; `diskutil info -plist` |
| Temporales | `/private/tmp`, en `/System/Volumes/Data`; comprobado con `df -P` |
| Python | CPython 3.14.6, ejecutable `/Users/mauricioteran/anaconda3/python.app/Contents/MacOS/python` |
| Build | Release; `build-py/CMakeCache.txt` |
| Compilador core | AppleClang 17.0.0.17000319; `CMakeCXXCompiler.cmake` del build |
| Flags del binding | `-O3 -DNDEBUG -std=gnu++20 -arch arm64`, con LTO; `flags.make` |
| Pagina | 4096 bytes en todos los casos; metadata real del binding, no constante del banco |
| Commit | `869fb826ef2f41eb1c5947d5307247bfbfb93e18` |
| Arbol de trabajo | Con cambios locales de #39; `git_cambios=True` |

Modulo utilizado: `build-py/bindings/quipudb_native.cpython-314-darwin.so`.
SHA-256: `2e603d63646294c59e6a23a43ae1a9051c914ffc7866b09db03e9edf396184a4`.
El entorno registra tambien hashes de los cuatro scripts ejecutados y de los datasets,
porque el commit por si solo no identifica los cambios locales. Los archivos del build
confirman su configuracion Release; no se inventa una certificacion de commit embebida
en el binario ni se recompilo para esta corrida.

Los datos adicionales se consultaron automaticamente antes de medir y se pasaron por
`--entorno`, por lo que el banco los guarda bajo `declarado.*`, junto con sus fuentes.
La etiqueta de proposito es manual; hardware, build y hashes no fueron estimados.
Los campos generales `no_documentado` del banco no se sobrescribieron: consultar sus
complementos `declarado.*`. Se conservaron cache de SO no controlada y flush sin fsync.

## Preguntas y metodo

Comparar insercion, busqueda por codigo y espacio con los mismos datasets de
1000, 10000 y 100000 registros. Medir tambien el costo de reorganizar Secuencial.
El [README](README.md#comparacion-de-archivos-issue-39) define los seis casos,
comandos, configuracion y limites exactos del intervalo medido.

Se uso 1 calentamiento y 5 repeticiones por caso/tamano, tablas nuevas e independientes,
orden mezclado identico, y 1000 consultas exitosas sin reemplazo con semilla
20260906. Se exportaron repeticiones, resumen con mediana y entorno sin quitar outliers.
Los tiempos incluyen llamadas Python/binding. Preparacion y validacion quedan fuera.

La reorganizacion explicita mide `reorganize()` mas flush tras borrar exactamente
30%. La automatica mide la siguiente eliminacion mas flush, incluido el trabajo de
reorganizacion que dispara: son operaciones diferentes, no dos estimaciones del
mismo tiempo aislado. No se prepara mas del 30% para despues volver a reorganizar.

Parametros efectivos, usando el Python indicado arriba y los metadatos adicionales:

```bash
PYTHONPATH=build-py/bindings python -B benchmarks/scripts/ejecutar_benchmarks.py \
  --suite archivos --tamanos 1000 10000 100000 --consultas 1000 \
  --calentamientos 1 --repeticiones 5 --temporales /private/tmp \
  --entorno proposito=experimento_oficial_issue_39
```

## Resultados oficiales

Tiempos en **ms**. Cada fila conserva mediana y rango minimo-maximo de cinco
repeticiones. Lecturas/escrituras son medianas de paginas instrumentadas; bytes
corresponde al archivo final real tras flush. Todos esos contadores y tamanos
fueron identicos entre las cinco repeticiones de cada caso/tamano.

| N | Caso | Mediana ms | Min-max ms | Paginas leidas | Paginas escritas | Bytes finales |
|---:|---|---:|---:|---:|---:|---:|
| 1000 | heap_insercion | 8.961542 | 8.873333-9.112375 | 992 | 1000 | 36864 |
| 1000 | sequential_insercion | 15.122250 | 15.011083-15.463708 | 3164 | 1014 | 36864 |
| 1000 | heap_busqueda_pk | 18.241916 | 18.176708-23.178125 | 4080 | 0 | 36864 |
| 1000 | sequential_busqueda_pk | 5.954958 | 5.744083-6.133125 | 1440 | 0 | 36864 |
| 1000 | sequential_reorganizacion_explicita | 0.852625 | 0.793542-7.118417 | 8 | 7 | 32768 |
| 1000 | sequential_eliminacion_con_reorganizacion | 0.797833 | 0.766375-0.888500 | 10 | 8 | 32768 |
| 10000 | heap_insercion | 91.116334 | 90.806917-92.967166 | 9928 | 10000 | 299008 |
| 10000 | sequential_insercion | 165.905209 | 157.620208-167.485583 | 28368 | 10219 | 458752 |
| 10000 | heap_busqueda_pk | 158.962167 | 158.153333-177.013375 | 35140 | 0 | 299008 |
| 10000 | sequential_busqueda_pk | 4.162667 | 4.070333-4.327541 | 1114 | 0 | 458752 |
| 10000 | sequential_reorganizacion_explicita | 4.852500 | 4.215459-5.144791 | 111 | 63 | 262144 |
| 10000 | sequential_eliminacion_con_reorganizacion | 5.149333 | 4.082125-5.170333 | 113 | 64 | 262144 |
| 100000 | heap_insercion | 936.778250 | 931.366167-944.899708 | 99285 | 100000 | 2932736 |
| 100000 | sequential_insercion | 1507.178583 | 1503.811333-1645.496625 | 297528 | 102050 | 4210688 |
| 100000 | heap_busqueda_pk | 1595.403916 | 1582.552167-1649.002875 | 352111 | 0 | 2932736 |
| 100000 | sequential_busqueda_pk | 3.986584 | 3.969042-4.205500 | 1025 | 0 | 4210688 |
| 100000 | sequential_reorganizacion_explicita | 35.309333 | 34.500959-36.020000 | 1027 | 625 | 2564096 |
| 100000 | sequential_eliminacion_con_reorganizacion | 31.431958 | 31.091833-32.153500 | 1029 | 626 | 2564096 |

Insercion mide N operaciones; busqueda mide el lote de 1000 consultas; cada
reorganizacion mide una operacion de su propio tipo. No confundir tiempos de lote
con latencias individuales. Los valores exactos en nanosegundos estan en los CSV.

### Comparacion Heap vs Secuencial

| N | Tiempo insercion Secuencial/Heap | Tiempo busqueda Heap/Secuencial | Espacio adicional Secuencial sobre Heap |
|---:|---:|---:|---:|
| 1000 | 1.69x | 3.06x | 0 bytes (0%) |
| 10000 | 1.82x | 38.19x | 159744 bytes (53.42%) |
| 100000 | 1.61x | 400.19x | 1277952 bytes (43.58%) |

El tiempo medio por consulta obtenido de la mediana del lote es, en microsegundos:

| N | Heap | Secuencial |
|---:|---:|---:|
| 1000 | 18.241916 | 5.954958 |
| 10000 | 158.962167 | 4.162667 |
| 100000 | 1595.403916 | 3.986584 |

Estos valores son `mediana_tiempo_ns / 1000 / 1000`, **no medianas de latencias
individuales**. Ambos casos usaron exactamente la misma secuencia de claves por N,
verificada tambien mediante sus hashes de entorno.

### Reorganizacion y espacio

La medida principal del costo de reorganizar es la explicita: 0.852625, 4.852500
y 35.309333 ms. La eliminacion disparadora se conserva por separado: 0.797833,
5.149333 y 31.431958 ms; incluye busqueda, eliminacion, metadatos, reorganizacion
automatica y flush, y deja un sobreviviente menos.

| N inicial | Borrados antes de medir | Vivos finales explicita / automatica | Bytes antes | Bytes despues (ambas) | Reduccion |
|---:|---:|---:|---:|---:|---:|
| 1000 | 300 | 700 / 699 | 36864 | 32768 | 4096 |
| 10000 | 3000 | 7000 / 6999 | 458752 | 262144 | 196608 |
| 100000 | 30000 | 70000 / 69999 | 4210688 | 2564096 | 1646592 |

Todos los escenarios verificaron desperdicio inicial 0.30 sin reorganizacion
automatica previa, y final cero, con exactamente los sobrevivientes esperados.
La igualdad de bytes finales de ambos escenarios no implica igual numero de
registros: el archivo se organiza por paginas. No se mide el pico temporal de
coexistencia del archivo original y `.reorg`.

## Observaciones, dispersion y limites

- Reorganizacion explicita de 1k: muestras 0.820042, **7.118417**, 0.793542,
  0.860958 y 0.852625 ms. La muestra alta es aproximadamente 8.35 veces la mediana;
  permanece en los datos y en el maximo. Los contadores y bytes fueron iguales.
  No hay evidencia para atribuirla a una causa concreta del SO o del motor.
- Las busquedas secuenciales bajaron de 5.954958 a 4.162667 y 3.986584 ms al
  aumentar N. Tambien bajaron sus paginas leidas por lote: 1440, 1114 y 1025.
  Esto es consistente con distinto recorrido efectivo de grupos/overflow para
  las muestras y distribuciones de estos archivos; no demuestra que buscar sea
  intrinsecamente mas rapido cuanto mas grande sea cualquier archivo secuencial.
- En 100k la eliminacion disparadora fue mas rapida que la reorganizacion explicita,
  aunque registro dos lecturas y una escritura adicionales. No convertir esa
  diferencia en una afirmacion de que el trabajo adicional acelera reorganizar:
  son ejecuciones separadas, con distinto estado final y cache/orden no controlados.
- No aparecieron paginas negativas, valores perdidos ni escrituras durante
  busquedas. Los contadores y bytes fueron estables por caso/N; los archivos
  tienen tamanos multiplos de la pagina real. Heap leyo 4080, 35140 y 352111 paginas
  por lote PK, compatible con el crecimiento del recorrido sin indice.
- Los contadores son los reales expuestos por el binding, no E/S fisicas de SSD.
  La instrumentacion del core no incluye necesariamente todo acceso interno
  (por ejemplo, la correccion de ultima pagina durante reorganizacion). Por ello
  no se exige igualdad entre paginas escritas y paginas finales del archivo.
- La cache del SO, frecuencia de CPU, orden fijo de casos y procesos externos no
  se controlaron. No se ejecutaron tests ni lint concurrentemente con la corrida.
  Flush no equivale a fsync; estos resultados no miden persistencia fisica durable.
  Cinco muestras y un solo entorno no permiten afirmar superioridad universal.

## Conclusiones sustentadas por esta corrida

| Tecnica | Ventajas observadas | Costos/limitaciones observados |
|---|---|---|
| Heap | Menor tiempo de insercion en los tres N; igual espacio en 1k y menor en 10k/100k | Busqueda PK mucho mas costosa al crecer N sin indice; no se mide reorganizacion equivalente |
| Secuencial | Menor tiempo de busqueda PK en los tres N; reorganizacion recupero espacio en todos | Insercion 1.61-1.82x mas lenta; mas espacio en 10k/100k; costo de reorganizacion y de la eliminacion que la dispara |

1. **Para cargas mezcladas centradas en insercion**, Heap resulta la opcion mas
   conveniente entre las dos evaluadas: menor mediana en los tres tamanos y sin
   penalizacion de espacio frente al Secuencial en estas cargas.
2. **Para consultas frecuentes por PK sin un indice adicional**, Secuencial es
   preferible en esta prueba: la ventaja medida va de 3.06x en 1k a 400.19x en
   100k. Esta conclusion no compara Heap con B+ o Hash; eso corresponde a #40.
3. **Cuando importa el espacio despues de insertar**, Heap empata en 1k y ocupa
   menos en 10k/100k. No concluir una proporcion fija de ahorro para otros datos:
   el orden y la ocupacion de paginas afectan la diferencia.
4. **Tras eliminaciones lazy**, reorganizar el Secuencial recupero espacio y dejo
   desperdicio cero. Su costo explicito crecio con N; la operacion que cruza el
   umbral carga ademas con mantenimiento automatico. Deben presupuestarse y
   presentarse ambos tiempos por separado, no inventar un costo equivalente Heap.

Estas conclusiones se refieren a estas implementaciones concretas, al orden
mezclado y a busquedas exitosas. No extrapolar a indices, rangos, claves ausentes,
inserciones ya ordenadas ni concurrencia que este experimento no mide.

No comparar el espacio de N registros con el de 70% sobreviviente como si fuese
una diferencia exclusivamente atribuible a la estructura. Tampoco informar como
cero el tiempo de reorganizacion Heap: el caso no aplica y no se ejecuta.

## Salida para el issue #41

Conservar `<id>_mediciones.csv`, `<id>_resumen.csv` y `<id>_entorno.csv` juntos,
fuera de Git. Agrupar por `caso`, `tecnica`, `operacion`, `n_registros` y
`n_operaciones`; no mezclar ejecuciones/configuraciones inadvertidamente.
Las columnas ya permiten graficar insercion, busqueda, espacio y reorganizacion,
ademas de paginas instrumentadas y dispersion. Este informe no genera graficas
ni incorpora resultados de indices fuera del alcance de #39.
