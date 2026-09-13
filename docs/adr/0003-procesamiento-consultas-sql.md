# ADR 0003 - Procesamiento de consultas SQL

- **Fecha:** 2026-09-13
- **Estado:** Aceptado
- **Issues:** #24, #25, #26, #27 y #28

## Contexto

La seccion 2.1.3 necesita convertir una consulta SQL en operaciones sobre las
estructuras del core definidas por el ADR 0001. Los issues #24 a #28 reparten
ese trabajo entre tokenizer y parser, `CREATE TABLE` e `INSERT`, seleccion y
filtrado, borrado, ordenamiento y agrupacion. Sin un subconjunto comun, cada
incremento podria aceptar una sintaxis distinta o mezclar el AST con el plan
fisico que consume el frontend.

El ADR 0002 ya fija la forma del plan de ejecucion y sus estadisticas. Este ADR
fija el lenguaje que precede a ese plan, la separacion entre sus fases y el
contrato de resultado que las conectara. Es una decision de arquitectura: no
implica que la ejecucion prevista para los issues #25 a #28 ya este
implementada.

## Decision

Se implementara un lexer propio y un parser descendente recursivo en Python,
sin una dependencia externa de parsing. `parse_sql(source)` sera una funcion
pura: recibira una cadena, consumira exactamente una sentencia y devolvera un
AST; no abrira una base de datos ni importara `quipudb_native`.

La implementacion se separa en estas fases:

1. El lexer conserva el lexema y la posicion de cada token.
2. El parser construye un AST sintactico, puro e inmutable.
3. El analisis semantico posterior resuelve catalogo, nombres y tipos.
4. El optimizador transforma la sentencia enlazada en el plan fisico del ADR
   0002.
5. El ejecutor consume el plan mediante los bindings del core y devuelve un
   `QueryResult`.

`Step` y `Plan` no se reutilizaran como nodos del AST. El AST representa lo que
pidio el usuario; el plan representa como se ejecutara.

### Gramatica EBNF

La siguiente es la gramatica sintactica completa. Los terminales alfabeticos
ASCII entre comillas son keywords y se comparan sin distinguir mayusculas de
minusculas ASCII. El lexer omite espacios, tabuladores y saltos CR/LF entre
tokens. `EOF` representa el final de la cadena.

```ebnf
document             = statement, [ ";" ], EOF ;

statement            = create_table
                     | insert
                     | select
                     | delete ;

create_table         = "CREATE", "TABLE", identifier, "(",
                       column_definition, { ",", column_definition }, ")",
                       [ "USING", storage_kind ] ;
column_definition    = identifier, data_type, [ "PRIMARY", "KEY" ] ;
data_type            = "INT"
                     | "DOUBLE"
                     | "BOOL"
                     | "DATE"
                     | "VARCHAR", "(", unsigned_integer, ")" ;
storage_kind         = "HEAP" | "SEQUENTIAL" ;

insert               = "INSERT", "INTO", identifier, "VALUES", "(",
                       literal, { ",", literal }, ")" ;

select               = "SELECT", projection_list, "FROM", identifier,
                       [ where_clause ], [ group_by_clause ],
                       [ order_by_clause ] ;
projection_list      = "*" | projection, { ",", projection } ;
projection           = identifier | aggregate ;
aggregate            = "COUNT", "(", "*", ")"
                     | aggregate_name, "(", identifier, ")" ;
aggregate_name       = "SUM" | "MIN" | "MAX" | "AVG" ;

where_clause         = "WHERE", condition ;
condition            = identifier, comparison_operator, literal
                     | identifier, "BETWEEN", literal, "AND", literal ;
comparison_operator  = "=" | "<" | "<=" | ">" | ">=" ;

group_by_clause      = "GROUP", "BY", identifier ;
order_by_clause      = "ORDER", "BY", identifier,
                       [ "ASC" | "DESC" ] ;

delete               = "DELETE", "FROM", identifier, where_clause ;

literal              = signed_double
                     | signed_integer
                     | string_literal
                     | boolean_literal
                     | date_literal ;
boolean_literal      = "TRUE" | "FALSE" ;
date_literal         = "DATE", string_literal ;

identifier           = identifier_start, { identifier_continue } ;
identifier_start     = letter | "_" ;
identifier_continue  = letter | digit | "_" ;
unsigned_integer     = digit, { digit } ;
signed_integer       = [ "-" ], unsigned_integer ;
signed_double        = [ "-" ], unsigned_integer, ".",
                       unsigned_integer ;
string_literal       = "'", { string_character | "''" }, "'" ;
string_character     = ? cualquier caracter excepto comilla simple ? ;
letter               = ? cualquier letra alfabetica Unicode ? ;
digit                = "0" | ... | "9" ;
```

Cuando dos tokens comparten prefijo, el lexer elige el mas largo; por ejemplo,
`<=` es un solo operador y `15.5` es un `signed_double`. El signo menos debe
estar unido al numero. No se admiten `+1`, `.5`, `5.`, notacion exponencial ni
otros formatos numericos.

Una comilla simple dentro de un string se representa con `''`; la barra
invertida no introduce escapes. Una fecha usa exactamente
`DATE 'YYYY-MM-DD'` y debe representar una fecha de calendario valida. Los
comentarios y los identificadores delimitados por comillas no forman parte de
la gramatica.

El punto y coma final es opcional, pero no se acepta ningun token despues de
el. Por tanto, una cadena con dos sentencias siempre falla. `DELETE` exige
`WHERE` sintacticamente para impedir un borrado total accidental. `BETWEEN` es
inclusivo y `AND` solo actua como separador de sus limites; no introduce una
segunda condicion.

### Reglas sintacticas y semanticas

Las keywords aceptan cualquier combinacion de mayusculas y minusculas. Los
identificadores conservan exactamente el texto recibido y se resolveran contra
el catalogo con esa misma escritura. Una keyword no puede usarse como
identificador porque no se admiten identificadores delimitados.

El parser de #24 conserva informacion sin consultar el catalogo. En particular,
puede representar columnas con o sin `PRIMARY KEY`, cualquier longitud entera
sin signo de `VARCHAR` y valores aun no comparados con un esquema. Desde #25, el
analisis semantico aplicara estas reglas antes de tocar disco:

- `CREATE TABLE` debe declarar al menos una columna y exactamente una
  `PRIMARY KEY`; `VARCHAR(n)` exige `n > 0`.
- Si se omite `USING`, la organizacion es `HEAP`. La alternativa explicita es
  `USING SEQUENTIAL`.
- `INSERT` es posicional, sin lista de columnas, y debe aportar un valor por
  columna con un tipo compatible.
- La tabla, las columnas proyectadas y las columnas de `WHERE`, `GROUP BY` y
  `ORDER BY` deben existir.
- Los dos limites de `BETWEEN` y el literal de una comparacion deben ser
  compatibles con la columna izquierda.
- Sin `GROUP BY`, la proyeccion admitida es `*` o una lista de columnas. Con
  `GROUP BY` debe existir al menos un agregado y toda columna proyectada sin
  agregar debe ser la unica columna de agrupacion.
- `COUNT` solo admite `*`. `SUM` y `AVG` exigen una columna numerica; `MIN` y
  `MAX` exigen una columna comparable.
- `ORDER BY` admite una sola columna. La direccion omitida equivale a `ASC`;
  `DESC` invierte el orden. En una consulta agrupada, la columna de orden debe
  ser la columna de agrupacion, pues no hay aliases para nombrar agregados.

El orden de clausulas queda fijado por la EBNF: `WHERE`, luego `GROUP BY` y por
ultimo `ORDER BY`. Cada clausula aparece como maximo una vez.

### AST, posiciones y errores

Los nodos del AST seran dataclasses congeladas con `slots`; sus colecciones
seran tuplas. Modelaran de forma separada las cuatro sentencias, identificadores,
tipos, definiciones de columna, literales, proyecciones, agregados, condiciones,
agrupacion y orden. Las keywords y operadores se almacenaran como enums
normalizados; identificadores y valores de string conservaran su contenido.
Omitir `USING` producira `HEAP` en el AST y omitir la direccion de orden
producira `ASC`.

Cada token y cada nodo tendra un `Span`. Sus offsets `start` y `end` cuentan
caracteres desde cero y usan un extremo final exclusivo; linea y columna
empiezan en uno. Un span de sentencia cubre desde su primera keyword hasta su
ultimo token sintactico y no incluye espacios circundantes ni el punto y coma
opcional. El token `EOF` usa un span vacio en el final de la entrada.

La familia de errores distinguira:

- `SQLLexError`: caracter no reconocido o literal sin cerrar.
- `SQLParseError`: token inesperado, sentencia incompleta o entrada sobrante.
- `SQLUnsupportedError`: construccion reconocible pero fuera del subconjunto.
- `SQLSemanticError`: nombre, aridad, tipo o regla de esquema invalida.

Todos expondran al menos mensaje, offset, linea, columna y span; cuando se
disponga de la fuente, su representacion incluira la linea afectada y un
marcador. Los errores lexicos y sintacticos pertenecen a #24. Los errores que
dependen del catalogo o del tipo real de una columna se incorporaran en los
issues posteriores.

### Resultado y plan de ejecucion

Desde #25, la fachada es `QueryProcessor.execute(source)` y devuelve un
`QueryResult` inmutable con cuatro campos:

```text
columns       tuple[str, ...] con los nombres devueltos
rows          tuple[tuple[object, ...], ...] con las filas materializadas
affected_rows int no negativo con la cantidad de filas modificadas
plan          Plan del ADR 0002 o None mientras la sentencia no tenga plan
```

El resultado rechaza filas cuyo ancho no coincida con `columns` y copia las
colecciones recibidas a tuplas, por lo que no conserva listas mutables del
binding. Cada ejecutor debe convertir tambien los escalares nativos antes de
construir las filas que exponga a otras capas.

Para `SELECT`, `columns` y `rows` contienen la salida y `affected_rows` es cero.
Para `INSERT` y `DELETE`, `affected_rows` informa las filas modificadas y no se
devuelven filas. `CREATE TABLE` no devuelve filas ni cuenta filas modificadas.
En #25, `CREATE TABLE` e `INSERT` devuelven `plan=None`: el ADR 0002 no define
una operacion `CREATE`, y la atribucion de escrituras entre una tabla y varios
indices todavia necesita el acuerdo descrito a continuacion.

El optimizador de #26 elegira busqueda por clave o indice cuando la estructura
y el operador lo permitan; en caso contrario usara scan y filtro. El ejecutor
de #28 conectara `ORDER BY` con external sorting y `GROUP BY` con external
hashing. En todos los casos, `plan` respetara el arbol, los nombres de
operaciones y las estadisticas propias de cada paso definidos por el ADR 0002.
La forma de representar `CREATE TABLE` y de atribuir las escrituras de
`INSERT`/`DELETE` entre tabla e indices requiere un acuerdo previo con quien
consume ese JSON; se documentara como una enmienda del ADR 0002 antes de
implementar esos planes, sin inventar aqui nuevos valores de `op`.

### Limites explicitos

Quedan fuera de este subconjunto:

- `UPDATE`, `JOIN`, `CREATE INDEX`, `DROP`, `ALTER` y el resto de DDL;
- `NULL` y la logica de tres valores;
- operadores `!=` y `<>`, condiciones generales con `AND`, `OR` o `NOT`;
- subconsultas, expresiones aritmeticas y funciones escalares;
- aliases, `HAVING`, `LIMIT` y listas de varias columnas en `GROUP BY` u
  `ORDER BY`;
- listas de columnas en `INSERT`, varias sentencias por llamada, comentarios e
  identificadores delimitados;
- transacciones y concurrencia, y SQL espacial, textual o multimedia.

Encontrar una de estas construcciones siempre termina con un `SQLError`; no se
intenta interpretarla parcialmente. Las caracteristicas reconocibles usan
`SQLUnsupportedError`, mientras una forma que el lexer no puede tokenizar usa
`SQLLexError` y el resto de entradas fuera de la EBNF usa `SQLParseError`.
Ampliar el subconjunto exige actualizar primero este ADR y agregar pruebas
validas e invalidas.

## Consecuencias

- El parser de #24 se puede probar sin compilar el core y sin crear archivos de
  base de datos.
- La sintaxis aceptada por #24 sirve como contrato comun para los cuatro issues
  de ejecucion posteriores, mientras las reglas dependientes del catalogo
  permanecen en la fase semantica.
- La separacion entre AST y plan evita que una eleccion fisica cambie el
  significado sintactico de la consulta y conserva el contrato del ADR 0002.
- El lexer y el parser manuales evitan una dependencia nueva, pero obligan a
  extender explicitamente la EBNF, el tokenizer, el AST y sus pruebas cada vez
  que crezca el lenguaje.
- La sensibilidad exacta de identificadores y la ausencia de quoting simplifican
  el catalogo inicial, a costa de ser mas restrictivos que SQL completo.
- La prohibicion de borrado sin `WHERE` y de multiples sentencias reduce el
  riesgo de modificaciones accidentales desde la API.
