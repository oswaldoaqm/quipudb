# Esquema de Rikuq en QuipuDB

Cómo guarda Rikuq las caras, y por qué así (#302). El ADR de la aplicación es
[0011](adr/0011-aplicacion-rikuq.md); esto es el detalle
reproducible del esquema, como
[consultas-multimedia.md](consultas-multimedia.md) lo es de la Parte 4.

## Las dos tablas

```sql
CREATE TABLE personas (
  id INT PRIMARY KEY,
  nombre VARCHAR(100),
  origen VARCHAR(10),
  fotos INT
) USING HEAP;                        -- 118 bytes por registro

CREATE TABLE rostros (
  id INT PRIMARY KEY,
  persona_id INT,
  ruta VARCHAR(200),
  embedding VECTOR(512)
) USING HEAP;                        -- 2 256 bytes por registro

CREATE INDEX rostros_por_embedding ON rostros (embedding) USING HNSW WITH METRIC=cosine;
CREATE INDEX rostros_por_persona   ON rostros (persona_id) USING HASH;
CREATE INDEX personas_por_nombre   ON personas (nombre) USING HASH;
```

El esquema no se escribe como texto suelto: se genera de la lista de columnas
de `app/carga/esquema.py`. Así el presupuesto de bytes se **calcula** y una
prueba lo comprueba, en vez de quedar como un comentario que nadie revisa
cuando el esquema cambia.

| Decisión | Por qué |
|---|---|
| `personas` y `rostros` separadas | Son los dos tipos de dato que exige el enunciado: lo relacional y lo multimedia. Además una persona tiene varias fotos, y repetir su nombre en cada rostro sería guardarlo 530 veces para George W. Bush |
| Ambas en HEAP | Los índices secundarios solo funcionan sobre heap, y un archivo secuencial admite como máximo 2 041 bytes por registro: el vector de 512 más la ruta no entrarían con holgura |
| `VECTOR(512)` | Es la dimensión que devuelve ArcFace, medida contra el modelo real en #305, no supuesta. El enunciado pide ese modelo por nombre: "modelos preentrenados (FaceNet, ArcFace)" |
| `ruta VARCHAR(200)` | La ruta relativa del manifiesto (`lfw/Abdullah_Gul/Abdullah_Gul_0001.jpg`) ronda los 40 caracteres; 200 deja sitio para las carpetas del equipo |
| `nombre VARCHAR(100)` | El nombre más largo de LFW ronda los 35 caracteres |
| `fotos` en `personas` | Cuántos rostros suyos entraron de verdad, que no es lo mismo que cuántas fotos tenía en el manifiesto: las que no dieron vector no cuentan |

## Por qué cabe

Las reglas son las del motor, en `engine/parser/semantic.py` y
`engine/parser/bound_ast.py`:

- una página son 4 096 bytes, menos 8 de cabecera y 1 de estado de slot, así
  que un registro de un heap entra en **4 087 bytes**;
- `INT` ocupa 4, `VARCHAR(n)` ocupa n, y `VECTOR(d)` ocupa d × 4, porque cada
  componente es un float de 32 bits.

| Tabla | Cuenta | Bytes |
|---|---|---|
| `personas` | 4 + 100 + 10 + 4 | **118** |
| `rostros` | 4 + 4 + 200 + 2048 | **2 256** |

Los dos entran de sobra. `rostros` es la que aprieta —el vector solo son
2 048 bytes, la mitad del presupuesto—. Cabe **un registro por página**:
dos necesitarían 4 512 bytes, más que los 4 087 disponibles. Solo las páginas
de datos de 4 324 rostros requieren aproximadamente 16,89 MiB, sin contar
cabeceras adicionales ni los índices. No son 9 MB.

Vale la pena notar que con ArcFace el margen se achicó bastante: con los 128
números de SFace el registro eran 720 bytes y ahora son 2 256. Si algún día se
quisiera un descriptor de 1 024 dimensiones, el vector solo ya ocuparía
4 096 bytes: acortar `ruta` o moverlo a otra tabla con las mismas páginas no
alcanzaría. Harían falta páginas mayores o almacenamiento fuera del registro.

## Por qué HNSW y no IVF

- **IVF necesita datos para entrenar** sus centroides, así que no se puede
  crear antes de cargar. HNSW se crea vacío y acepta las filas a medida que
  entran, que es justo el orden en que la carga las inserta.
- El enunciado pide **consultar en menos de 100 ms**. Sobre 100 000 vectores,
  los benchmarks de la Parte 4 miden HNSW en 0,69 ms por consulta contra
  11,92 ms de IVF. El precio es construirlo más lento (117,69 s contra 5,65 s)
  y ocupar más (41,42 MB contra 28,02 MB), que para 4 324 rostros no se siente.
- Las dos estructuras dan Recall@10 ≈ 0,99, así que la precisión no decide.

## Por qué `METRIC=cosine`

Los vectores salen de #305 **normalizados a norma 1**. Con vectores unitarios
el producto punto vale lo mismo que el coseno, así que `cosine` y `dot`
ordenarían igual; se elige `cosine` porque es la métrica que usa la literatura
de reconocimiento facial y la que hace comparables nuestros números con los
que publica SFace.

Importa que la normalización ocurra **antes de insertar** y no al consultar:
`cv2.match` normaliza por dentro, pero QuipuDB no. Insertar el vector crudo
—que no es unitario— y pedir distancia por producto punto no daría el
coseno, y el umbral de #303 dejaría de significar lo que midió #306.

## La carga

```bash
python -m app.carga.dataset --limite 200   # para probar
python -m app.carga.dataset                # el dataset entero
```

- Va por el **API REST** del motor (`POST /query`). El servidor de Rikuq no
  importa `engine` ni `quipudb_native`, que es lo que exige el enunciado de la
  Parte 5. Hay una prueba que recorre el árbol de `app/` y falla si alguien
  agrega ese import.
- Los `INSERT` se mandan **en lotes de 50**. Uno por fila son 4 324 idas y
  vueltas; todos en uno hace una petición enorme que, si falla, no dice qué
  fila tuvo la culpa.
- Una imagen **sin rostro no corta la carga**: se anota y se sigue. De 4 324
  fotos de prensa siempre hay alguna donde YuNet no encuentra nada, y perder
  la carga entera por una sola obligaría a empezar de cero. Al final se
  informa cuántas entraron y cuántas no.
- Una persona de la que **no entró ningún rostro no se inserta**: una fila en
  `personas` sin un solo vector no sirve para reconocer a nadie.
- Una foto con **dos caras** aporta solo la más confiable: el manifiesto dice
  de quién es la foto, no cuál de las dos caras.
