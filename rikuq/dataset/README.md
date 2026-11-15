# Dataset de rostros de Rikuq

La base de rostros tiene dos partes: un dataset publico y las fotos del equipo.
Ninguna de las dos se versiona en git. Lo que si se versiona es
`rostros.tsv`, el manifiesto: una fila por imagen con su persona, su ruta y su
SHA-256. Con el se comprueba que todos tenemos exactamente los mismos
archivos sin tener que subirlos.

## Como se arma

Desde `rikuq/server`:

```bash
python -m app.dataset.lfw descargar
```

```bash
python -m app.dataset.lfw manifiesto --minimo 10
```

```bash
python -m app.dataset.lfw verificar
```

El primero baja `lfw.tgz` y lo extrae en `rikuq/datos/lfw/`. El segundo
recorre esa carpeta mas `rikuq/datos/equipo/` y escribe `rostros.tsv`. El
tercero vuelve a calcular los SHA-256 y avisa si algo falta o cambio.

Las imagenes del equipo van en `rikuq/datos/equipo/<Nombre_Apellido>/`, una
carpeta por persona, igual que LFW.

## Por que un minimo de fotos por persona

LFW tiene 13 233 imagenes de 5 749 personas, pero la mayoria aparece una sola
vez. Con una sola foto no se puede evaluar nada: si esa imagen se usa para
cargar la base, no queda ninguna para preguntar, y si se usa para preguntar,
la persona no esta cargada. `--minimo` descarta esas identidades. El paso
imprime cuantas personas y cuantas imagenes quedaron, que es la cifra que va
al informe.

El minimo solo se aplica a LFW. Las fotos del equipo entran siempre: son pocas
y son justamente las que la demostracion tiene que reconocer.

## Procedencia y licencia

**LFW (Labeled Faces in the Wild)** — Universidad de Massachusetts, Amherst.
Se publica para investigacion. Las imagenes salieron de noticias y los
derechos siguen siendo de sus autores originales, asi que se usan aqui solo
con fines academicos y no se redistribuyen: el repositorio guarda el
manifiesto, no las fotos.

La pagina oficial pide citar el informe tecnico:

> Gary B. Huang, Manu Ramesh, Tamara Berg y Erik Learned-Miller.
> *Labeled Faces in the Wild: A Database for Studying Face Recognition in
> Unconstrained Environments.* University of Massachusetts, Amherst,
> Technical Report 07-49, octubre de 2007.

Origen: <http://vis-www.cs.umass.edu/lfw/>

**Fotos del equipo** — tomadas por nosotros para este trabajo. No se
redistribuyen y no salen del equipo.

## Consentimiento

La clase 10 lo marca explicitamente: un rostro es un dato biometrico, y como
tal necesita consentimiento y proteccion. No alcanza con que la foto sea
nuestra.

Cada persona fotografiada dio su permiso para que su imagen se use **solo** en
este trabajo del curso, sabiendo que:

- las fotos se guardan en la maquina de cada quien, nunca en el repositorio ni
  en ningun servicio externo;
- de cada foto se extrae un vector de 512 numeros que se guarda en QuipuDB;
- al terminar el curso las fotos se borran, y quien quiera puede pedir que se
  borren antes, sin dar explicaciones.

| Persona | Fotos | Consentimiento | Fecha |
|---|---|---|---|
| | | | |

> Llenar una fila por integrante antes de agregar sus fotos a
> `rikuq/datos/equipo/`.

## Lo que el manifiesto no garantiza

Que los archivos sean los mismos, no que las personas esten bien etiquetadas.
LFW trae errores de etiquetado conocidos; si una identidad da resultados raros
en la evaluacion, vale la pena mirar sus imagenes antes de culpar al modelo.
