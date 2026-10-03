# R-Tree vs busqueda secuencial — issue #131

## Estado y trazabilidad

**Corrida oficial completada: 555 mediciones, 111 resumenes y un CSV de entorno.**
Suite `espacial`, 37 casos por tamano, un calentamiento y cinco repeticiones
medidas, 100 consultas por lote. Cada repeticion de las dos tecnicas se valido
contra la respuesta de la busqueda secuencial; ninguna fallo. No se descartaron
outliers ni se repitieron casos selectivamente.

ID: `20261003T063020Z_ad4d7cbd6da347c3847d749ba6c6931a`.
Inicio: 2026-10-03T06:30:20Z (3 de octubre, 01:30:20, America/Lima).
Commit medido: `9d88c67` (`git_cambios=True` solo por un `.gitignore` local
que no toca codigo).

- [Mediciones](results/20261003T063020Z_ad4d7cbd6da347c3847d749ba6c6931a_mediciones.csv)
- [Resumen](results/20261003T063020Z_ad4d7cbd6da347c3847d749ba6c6931a_resumen.csv)
- [Entorno](results/20261003T063020Z_ad4d7cbd6da347c3847d749ba6c6931a_entorno.csv)

Los CSV estan ignorados por Git, como los de #39 y #40: hay que conservarlos
aparte. Las tablas de Resultados las genera
`python benchmarks/scripts/resumir_espacial.py 20261003T063020Z_ad4d7cbd6da347c3847d749ba6c6931a`,
sin copiar numeros a mano.

### Una corrida previa descartada, y por que

Una primera corrida completa (`20261003T054615Z_962321d0972d4a50b18ac641b2741248`)
paso todas las validaciones, pero sus tiempos eran **bimodales**: la misma
operacion daba ~17 ms en unas repeticiones y ~45 ms en otras
(`secuencial_knn_10_haversine` a 100k: 14, 47, 51, 28 y 66 ms). El i5-1335U es
hibrido (2 nucleos P y 8 nucleos E), y Windows movia el proceso entre ellos y
bajaba la frecuencia entre consultas, asi que la mediana dependia de en que
nucleo cayo cada repeticion. Las paginas leidas eran identicas; solo el tiempo
estaba contaminado.

Se descarto entera, no por casos. Para la oficial se fijo el proceso a la CPU
logica 2 (un nucleo P) con prioridad alta, y el estado minimo del procesador con
corriente al 100 % (restaurado al 5 % al terminar). Con eso, un caso de prueba que
antes variaba mas del 80 % entre repeticiones paso a variar menos del 1 %. Las
dos condiciones quedan en el entorno como `declarado.afinidad` y
`declarado.energia`. Ademas, la validacion de radios paso a guardar un hash por
respuesta en lugar de la lista de RIDs: la referencia de 2 000 km a 100k ocupaba
mas de 1 GB y el recolector de basura agregaba ruido.

## Entorno

- Intel Core i5-1335U (13.a gen.), 16 GB de RAM, SSD NVMe KIOXIA BG6 512 GB.
- Windows 11 Pro 10.0.26200, con corriente alterna.
- CPython 3.11.9 de MSYS2 ucrt64; core y bindings en Release con g++ 14.1.0.
- Temporales en `%TEMP%`, el mismo SSD para todos los casos.

La cache del sistema operativo no se controla: los archivos recien escritos
estan, en la practica, en memoria. Los tiempos miden CPU y acceso a paginas en
cache, no lecturas fisicas del SSD.

## Protocolo

Resumido; el detalle esta en el [README](README.md#r-tree-vs-busqueda-secuencial-issue-131).

- Datasets de #130: 1k, 10k y 100k puntos concentrados en 15 ciudades del Peru.
- **Secuencial**: `scan_radius` / `scan_k_nearest` en C++, que recorren el Heap
  con su cursor. **R-Tree**: `search_radius` / `k_nearest` del indice. Las dos
  devuelven RID sin leer registros.
- 100 centros: coordenadas de puntos del dataset elegidos con la semilla
  20260906 entre los primeros 1 000; son los mismos en los tres tamanos.
- Radios del enunciado (1, 5, 10 km), k del enunciado (10, 50, 100), las dos
  metricas, y radios de cruce (25 a 2 000 km) solo con Haversine.
- El tiempo por consulta es la mediana del lote de 100 dividida entre 100: el
  promedio de 100 consultas que pide el enunciado.

## Resultados

"Aceleracion" es tiempo secuencial / tiempo R-Tree: mayor que 1 significa que
gana el R-Tree. Tiempos y paginas son por consulta.

<!-- generado por resumir_espacial.py desde 20261003T063020Z_ad4d7cbd6da347c3847d749ba6c6931a -->

#### Radio (Haversine)

| Consulta | N | Secuencial ms | R-Tree ms | Aceleracion | Pag. sec. | Pag. R-Tree | Devueltos/consulta |
|---|---:|---:|---:|---:|---:|---:|---:|
| radio_1km | 1 000 | 0.117 | 0.010 | 11.5x | 10 | 3.5 | 2 |
| radio_1km | 10 000 | 1.284 | 0.027 | 47.6x | 91 | 7.4 | 10 |
| radio_1km | 100 000 | 19.9 | 0.153 | 129.6x | 910 | 23.2 | 90 |
| radio_5km | 1 000 | 0.122 | 0.016 | 7.6x | 10 | 4.3 | 20 |
| radio_5km | 10 000 | 1.349 | 0.088 | 15.4x | 91 | 14.1 | 193 |
| radio_5km | 100 000 | 16.5 | 0.783 | 21.1x | 910 | 82.9 | 1 915 |
| radio_10km | 1 000 | 0.126 | 0.027 | 4.7x | 10 | 5.3 | 59 |
| radio_10km | 10 000 | 1.493 | 0.216 | 6.9x | 91 | 23.4 | 608 |
| radio_10km | 100 000 | 17.0 | 2.140 | 7.9x | 910 | 169.7 | 6 059 |

#### Radio (euclidiana)

| Consulta | N | Secuencial ms | R-Tree ms | Aceleracion | Pag. sec. | Pag. R-Tree | Devueltos/consulta |
|---|---:|---:|---:|---:|---:|---:|---:|
| radio_1km | 1 000 | 0.090 | 0.011 | 8.1x | 10 | 3.5 | 2 |
| radio_1km | 10 000 | 1.632 | 0.026 | 63.6x | 91 | 7.3 | 9 |
| radio_1km | 100 000 | 11.6 | 0.167 | 69.7x | 910 | 23.1 | 88 |
| radio_5km | 1 000 | 0.095 | 0.016 | 5.9x | 10 | 4.3 | 19 |
| radio_5km | 10 000 | 1.122 | 0.087 | 13.0x | 91 | 14.0 | 189 |
| radio_5km | 100 000 | 12.1 | 0.846 | 14.3x | 910 | 82.5 | 1 879 |
| radio_10km | 1 000 | 0.100 | 0.025 | 4.0x | 10 | 5.3 | 58 |
| radio_10km | 10 000 | 1.068 | 0.192 | 5.6x | 91 | 23.3 | 597 |
| radio_10km | 100 000 | 15.1 | 2.205 | 6.8x | 910 | 168.5 | 5 953 |

#### k-NN (Haversine)

| Consulta | N | Secuencial ms | R-Tree ms | Aceleracion | Pag. sec. | Pag. R-Tree | Devueltos/consulta |
|---|---:|---:|---:|---:|---:|---:|---:|
| knn_10 | 1 000 | 0.120 | 0.027 | 4.4x | 10 | 4.0 | 10 |
| knn_10 | 10 000 | 1.534 | 0.069 | 22.1x | 91 | 7.3 | 10 |
| knn_10 | 100 000 | 20.0 | 0.226 | 88.6x | 910 | 14.2 | 10 |
| knn_50 | 1 000 | 0.135 | 0.043 | 3.2x | 10 | 5.4 | 50 |
| knn_50 | 10 000 | 1.327 | 0.095 | 14.0x | 91 | 9.7 | 50 |
| knn_50 | 100 000 | 16.6 | 0.283 | 58.6x | 910 | 19.6 | 50 |
| knn_100 | 1 000 | 0.151 | 0.060 | 2.5x | 10 | 6.5 | 100 |
| knn_100 | 10 000 | 1.280 | 0.119 | 10.8x | 91 | 11.6 | 100 |
| knn_100 | 100 000 | 15.7 | 0.361 | 43.5x | 910 | 23.8 | 100 |

#### k-NN (euclidiana)

| Consulta | N | Secuencial ms | R-Tree ms | Aceleracion | Pag. sec. | Pag. R-Tree | Devueltos/consulta |
|---|---:|---:|---:|---:|---:|---:|---:|
| knn_10 | 1 000 | 0.094 | 0.018 | 5.1x | 10 | 4.0 | 10 |
| knn_10 | 10 000 | 0.949 | 0.038 | 24.7x | 91 | 7.3 | 10 |
| knn_10 | 100 000 | 18.2 | 0.124 | 147.3x | 910 | 14.3 | 10 |
| knn_50 | 1 000 | 0.111 | 0.032 | 3.5x | 10 | 5.5 | 50 |
| knn_50 | 10 000 | 0.959 | 0.060 | 15.9x | 91 | 9.7 | 50 |
| knn_50 | 100 000 | 11.7 | 0.177 | 66.1x | 910 | 19.7 | 50 |
| knn_100 | 1 000 | 0.125 | 0.047 | 2.7x | 10 | 6.5 | 100 |
| knn_100 | 10 000 | 0.963 | 0.077 | 12.5x | 91 | 11.6 | 100 |
| knn_100 | 100 000 | 12.4 | 0.207 | 59.9x | 910 | 23.8 | 100 |

#### Cruce: radios grandes (Haversine)

| Consulta | N | Secuencial ms | R-Tree ms | Aceleracion | Pag. sec. | Pag. R-Tree | Devueltos/consulta |
|---|---:|---:|---:|---:|---:|---:|---:|
| radio_1km | 1 000 | 0.117 | 0.010 | 11.5x | 10 | 3.5 | 2 |
| radio_1km | 10 000 | 1.284 | 0.027 | 47.6x | 91 | 7.4 | 10 |
| radio_1km | 100 000 | 19.9 | 0.153 | 129.6x | 910 | 23.2 | 90 |
| radio_5km | 1 000 | 0.122 | 0.016 | 7.6x | 10 | 4.3 | 20 |
| radio_5km | 10 000 | 1.349 | 0.088 | 15.4x | 91 | 14.1 | 193 |
| radio_5km | 100 000 | 16.5 | 0.783 | 21.1x | 910 | 82.9 | 1 915 |
| radio_10km | 1 000 | 0.126 | 0.027 | 4.7x | 10 | 5.3 | 59 |
| radio_10km | 10 000 | 1.493 | 0.216 | 6.9x | 91 | 23.4 | 608 |
| radio_10km | 100 000 | 17.0 | 2.140 | 7.9x | 910 | 169.7 | 6 059 |
| radio_25km | 1 000 | 0.158 | 0.062 | 2.6x | 10 | 6.8 | 225 |
| radio_25km | 10 000 | 1.663 | 0.577 | 2.9x | 91 | 44.7 | 2 272 |
| radio_25km | 100 000 | 21.1 | 8.034 | 2.6x | 910 | 395.3 | 22 644 |
| radio_50km | 1 000 | 0.175 | 0.085 | 2.1x | 10 | 7.2 | 355 |
| radio_50km | 10 000 | 1.865 | 0.857 | 2.2x | 91 | 52.8 | 3 514 |
| radio_50km | 100 000 | 23.8 | 11.7 | 2.0x | 910 | 498.4 | 35 429 |
| radio_100km | 1 000 | 0.182 | 0.092 | 2.0x | 10 | 7.5 | 392 |
| radio_100km | 10 000 | 1.892 | 0.911 | 2.1x | 91 | 55.1 | 3 861 |
| radio_100km | 100 000 | 23.8 | 12.7 | 1.9x | 910 | 534.2 | 38 888 |
| radio_250km | 1 000 | 0.192 | 0.107 | 1.8x | 10 | 8.6 | 452 |
| radio_250km | 10 000 | 1.957 | 1.034 | 1.9x | 91 | 64.2 | 4 470 |
| radio_250km | 100 000 | 26.3 | 14.7 | 1.8x | 910 | 626.7 | 45 126 |
| radio_500km | 1 000 | 0.224 | 0.198 | 1.1x | 10 | 12.3 | 636 |
| radio_500km | 10 000 | 2.353 | 1.492 | 1.6x | 91 | 92.0 | 6 361 |
| radio_500km | 100 000 | 30.7 | 21.4 | 1.4x | 910 | 899.0 | 64 112 |
| radio_2000km | 1 000 | 0.281 | 0.252 | 1.1x | 10 | 15.0 | 1 000 |
| radio_2000km | 10 000 | 2.938 | 2.469 | 1.2x | 91 | 133.0 | 10 000 |
| radio_2000km | 100 000 | 40.4 | 35.4 | 1.1x | 910 | 1323.0 | 100 000 |

#### Construccion y espacio

| N | Construccion R-Tree ms | Pag. leidas | Pag. escritas | Heap KiB | R-Tree KiB | R-Tree / Heap |
|---:|---:|---:|---:|---:|---:|---:|
| 1 000 | 12.8 | 1 895 | 1 055 | 44 | 64 | 1.45 |
| 10 000 | 137.3 | 21 602 | 10 449 | 368 | 536 | 1.46 |
| 100 000 | 1946.0 | 292 421 | 105 017 | 3 644 | 5 296 | 1.45 |

#### Haversine vs euclidiana (N = 100 000)

| Consulta | N | Tecnica | Haversine ms | Euclidiana ms | Haversine / Euclidiana |
|---|---:|---|---:|---:|---:|
| radio_1km | 100 000 | secuencial | 19.9 | 11.6 | 1.71 |
| radio_1km | 100 000 | rtree | 0.153 | 0.167 | 0.92 |
| radio_5km | 100 000 | secuencial | 16.5 | 12.1 | 1.36 |
| radio_5km | 100 000 | rtree | 0.783 | 0.846 | 0.93 |
| radio_10km | 100 000 | secuencial | 17.0 | 15.1 | 1.12 |
| radio_10km | 100 000 | rtree | 2.140 | 2.205 | 0.97 |
| knn_10 | 100 000 | secuencial | 20.0 | 18.2 | 1.10 |
| knn_10 | 100 000 | rtree | 0.226 | 0.124 | 1.82 |
| knn_50 | 100 000 | secuencial | 16.6 | 11.7 | 1.42 |
| knn_50 | 100 000 | rtree | 0.283 | 0.177 | 1.60 |
| knn_100 | 100 000 | secuencial | 15.7 | 12.4 | 1.27 |
| knn_100 | 100 000 | rtree | 0.361 | 0.207 | 1.74 |

## Lectura de los resultados

### El R-Tree gana en todo lo que pide el enunciado

Con radios de 1, 5 y 10 km y con k = 10, 50 y 100, el R-Tree es mas rapido en
los tres tamanos. A 100k la ventaja va de 7x (radio de 10 km) a 130x (radio de
1 km) y a 147x (k-NN con k = 10, euclidiana).

La razon se ve en las paginas. El secuencial lee **siempre** el Heap entero
(10, 91 y 910 paginas), sin importar la consulta. El R-Tree lee solo los nodos
cuyo MBR toca la region: a 100k, 23 paginas para un radio de 1 km y 14 para un
k-NN de 10. Por eso la ventaja **crece con N**: el secuencial escala lineal (x10
por cada x10 de datos), y el R-Tree con la altura del arbol mas lo que devuelve.

### La ventaja cae cuando la consulta devuelve mucho

En radios, lo que paga el R-Tree es proporcional a lo que devuelve. A 100k, el
radio de 1 km devuelve 90 puntos por consulta y el de 10 km 6 059 (6 % del
dataset): las paginas del R-Tree suben de 23 a 170 y la ventaja baja de 130x a
8x. El dataset esta concentrado a proposito: la mayoria de los centros caen en
Lima, donde un radio de 10 km abarca buena parte de una nube de ~12 km de
desviacion.

En k-NN, en cambio, el R-Tree devuelve siempre k puntos y sus paginas casi no
crecen con N (de 4 a 14 para k = 10, de 1k a 100k). Es la consulta donde el
indice rinde mas: el best-first se detiene en cuanto tiene k vecinos y nunca
abre los subarboles mas lejanos.

### El cruce: donde el R-Tree deja de convenir

| Radio (100k) | Fraccion devuelta | Pag. R-Tree / Pag. secuencial | Aceleracion |
|---|---:|---:|---:|
| 10 km | 6 % | 0,19 | 7,9x |
| 25 km | 23 % | 0,43 | 2,6x |
| 100 km | 39 % | 0,59 | 1,9x |
| 500 km | 64 % | 0,99 | 1,4x |
| 2 000 km | 100 % | 1,45 | 1,1x |

**En paginas, el cruce ocurre hacia los 500 km**, cuando la consulta abarca dos
tercios del dataset: ahi el R-Tree ya lee tantas paginas como el recorrido
completo (899 frente a 910), y con 2 000 km lee un 45 % mas, porque recorre el
arbol entero y sus nodos ocupan mas que el Heap. Es lo que se esperaba: cuando
la region cubre casi todo, la poda no descarta nada y el indice solo agrega
trabajo.

**En tiempo, el R-Tree no llega a perder** en esta maquina: con 2 000 km sigue
1,1x por delante. Hay dos razones. Las paginas estan en cache, asi que leer una
pagina de mas cuesta poco. Y una hoja del R-Tree trae solo (punto, RID), 22
bytes, mientras que el secuencial decodifica cada registro completo del Heap
(id, ciudad y punto) antes de medir la distancia. Con lecturas fisicas reales
-- cache fria, un disco mecanico o un dataset que no cabe en memoria -- pesarian
las paginas, y el cruce en tiempo se acercaria al de 500 km. Esta medicion no
puede afirmarlo, porque la cache no se controla.

Regla practica para un planner: usar el R-Tree mientras la consulta vaya a
devolver menos de la mitad de la tabla; por encima, las dos tecnicas cuestan
casi lo mismo y el recorrido es mas predecible.

### Tamano chico

Con 1k puntos el Heap ocupa 10 paginas y el secuencial tarda ~0,1 ms. El R-Tree
sigue ganando (2,5x a 11x), pero la diferencia absoluta es de centesimas de
milisegundo: con tablas de unas decenas de paginas, mantener un indice no se
justifica por velocidad de consulta.

### Haversine frente a euclidiana

- **Secuencial**: la Haversine es 1,1x a 1,7x mas lenta, porque calcula senos,
  cosenos y un arcoseno por cada uno de los N puntos.
- **R-Tree, radio**: practicamente igual (0,92x a 0,97x). La formula se evalua
  solo sobre los candidatos que deja pasar el MBR, y el costo lo dominan los
  nodos leidos, que son los mismos con las dos metricas.
- **R-Tree, k-NN**: la Haversine es 1,6x a 1,8x mas lenta. El best-first calcula
  `min_distance` a cada MBR que encola, y sobre la esfera esa cota necesita el
  pie de la perpendicular al meridiano (un `atan2` y varias funciones
  trigonometricas), mucho mas cara que recortar coordenadas en el plano.

Las dos metricas leen las mismas paginas y devuelven casi lo mismo (~2 % menos
puntos con la euclidiana en radios, porque el circulo en grados es mas angosto
de este a oeste). La euclidiana solo sirve para ordenar dentro de una zona
chica; para "a menos de 5 km" hace falta la Haversine, y su costo extra en el
R-Tree es bajo.

### Construccion y espacio

- Construir el R-Tree sobre 100k puntos toma ~1,9 s, aproximadamente lineal en
  N (12,8 ms, 137 ms, 1 946 ms). Son inserciones una por una: ~2,9 paginas
  leidas por punto (el camino hasta la hoja) y ~1,05 escritas (la hoja mas los
  splits). Una carga masiva ordenada (STR, Hilbert) lo reduciria; no esta
  implementada.
- El R-Tree ocupa **1,45 veces el Heap** en los tres tamanos (5,2 MB frente a
  3,6 MB a 100k). Una hoja guarda 22 bytes por punto, menos que un registro del
  Heap, pero el split deja los nodos parcialmente llenos y los internos agregan
  sus MBR de 36 bytes.
- El secuencial no tiene construccion ni espacio extra: es su unica ventaja.

**Memoria.** Las dos estructuras viven en disco y se leen pagina a pagina. Por
consulta, el secuencial usa una pagina y O(k) para el k-NN; el R-Tree, el camino
de la recursion en radios y la cola del best-first en k-NN. El banco no mide RAM.

## Cuando usar cada tecnica

| | Busqueda secuencial | R-Tree |
|---|---|---|
| Ventajas | Sin construccion ni espacio extra. Costo fijo y predecible: lee la tabla una vez. Sirve para cualquier predicado. | 7x a 150x mas rapido en las consultas del enunciado a 100k. El k-NN casi no crece con N. Lee pocas paginas. |
| Desventajas | Lineal en N: a 100k, ~12-20 ms por consulta aunque devuelva 10 puntos. | Construccion de ~2 s a 100k y 45 % mas de espacio que la tabla. Hay que mantenerlo en cada INSERT/DELETE. Pierde la poda con regiones grandes. |
| Conviene cuando | La tabla es chica (decenas de paginas), la consulta devuelve mas de la mitad de los puntos, o se consulta muy rara vez. | La tabla es grande y la consulta es selectiva: radios chicos o k-NN. Siempre para k-NN. |

## Limites

- Cache del SO no controlada; tiempos con paginas en memoria.
- El tiempo incluye el cruce Python/binding de cada consulta y la lista de RIDs
  que devuelve, igual para las dos tecnicas.
- La dispersion tipica entre repeticiones es de ~11 % ((max - min) / mediana);
  los peores casos llegan a 60-110 %, con medianas por debajo de 20 ms. Ninguno
  cambia que tecnica gana. Las paginas no tienen dispersion.
- Un solo equipo, con afinidad y frecuencia fijadas a mano. Otra maquina dara
  otros tiempos, no otras paginas.
- Los resultados dependen de la distribucion: con puntos uniformes las consultas
  devolverian menos y el R-Tree saldria mas favorecido.

La comparacion contra GIST de PostgreSQL corresponde a #132.
