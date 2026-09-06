# Guia de contribucion

El historial de commits de este repositorio es parte de la evaluacion del
curso. Estas convenciones existen para que ese historial se lea como el registro
real de como se construyo el motor.

## Antes del primer commit

Activa el hook local que valida los mensajes. Se corre una sola vez por clon:

```bash
git config core.hooksPath .githooks
```

A partir de ahi, un mensaje mal formado se rechaza en tu maquina en vez de
romper el CI del pull request.

## Formato de commits

```
tipo(alcance): descripcion breve
```

- Todo en **minusculas**
- **Sin punto** al final
- En **imperativo**: agregar, corregir, actualizar
- Maximo **72 caracteres** en total
- Un commit = **un cambio logico**

### Tipos

| Tipo | Cuando |
|---|---|
| `feat` | Nueva funcionalidad |
| `fix` | Correccion de un error |
| `docs` | Cambios en documentacion |
| `refactor` | Reestructuracion sin cambiar comportamiento |
| `test` | Creacion o modificacion de pruebas |
| `chore` | Mantenimiento, configuracion o dependencias |

### Alcances

Solo estos. El alcance es opcional, pero si lo pones tiene que ser uno de la
lista; el CI lo verifica.

| Alcance | Modulo |
|---|---|
| `storage` | `core/` gestion de archivos y paginas |
| `index` | `core/` B+ y extendible hashing |
| `external` | `core/` external sorting y hashing |
| `catalog` | `core/` esquemas y metadata de tablas |
| `parser` | `engine/parser` |
| `txn` | `engine/transactions` |
| `planner` | `engine/planner` |
| `api` | `engine/api` |
| `frontend` | `frontend/` |
| `bench` | `benchmarks/` |
| `docs` | `docs/`, README, ADRs |
| `build` | CMake, dependencias, empaquetado |
| `ci` | GitHub Actions y scripts de `.github/` |

### Ejemplos

```
feat(storage): implementar heap file con reutilizacion de espacio
feat(index): agregar busqueda por rango en b+ agrupado
fix(external): corregir fusion de runs cuando k supera los buffers
test(index): agregar pruebas de split en extendible hashing
refactor(planner): extraer seleccion de indice a una funcion propia
docs: documentar el contrato del plan de ejecucion
chore(build): configurar cmake con pybind11
```

Lo que **no** pasa la revision: `cambios varios`, `fix`, `avance`, `final`,
`update proyecto`, `correcciones de errores`.

## Ramas

```
feat/<alcance>-<slug>
```

Ejemplos: `feat/storage-heap-file`, `feat/index-bplus-clustered`,
`fix/external-merge-buffers`.

Nunca se trabaja directo sobre `main`.

## Pull requests

1. Abre el PR contra `main` y enlaza el issue con `Closes #N`.
2. Espera que el CI pase: compilacion del core, lint de Python y validacion de
   los mensajes de commit.
3. Pide **una** revision a alguien del equipo.
4. Al mezclar, usa **Squash and merge** solo si tus commits intermedios son
   ruido. Si cada commit representa un cambio logico limpio, usa merge normal:
   ese historial es justamente lo que se evalua.

## Cambios que afectan a otro modulo

Si tu cambio modifica una interfaz que consume otra persona (las operaciones del
core, la forma del plan de ejecucion, el contrato HTTP de la API), avisalo en el
issue **antes** de implementarlo. Esos contratos estan listados en
[`docs/arquitectura.md`](docs/arquitectura.md).

## Decisiones de diseno

Cuando el equipo tome una decision de arquitectura, se escribe una nota corta en
`docs/adr/` numerada y fechada. Toma cinco minutos y ahorra reconstruir el
razonamiento cuando toque escribir el informe final.
