# QuipuDB — Informe técnico de la Parte 1

## 1. Presentación, objetivo y alcance

QuipuDB es un motor educativo desarrollado para el proyecto de Base de Datos 2.
La Parte 1 aborda almacenamiento relacional, índices, operaciones externas,
procesamiento SQL, transacciones, interfaz y comparación experimental.
El objetivo de este informe es explicar la implementación existente y sus límites,
sin confundir capacidades del core, soporte SQL e integración de la interfaz.

La revisión técnica toma como referencia el código en
`f2f0879a7ae41cf65ac3127d0f24881b9976f905`, punto de partida de este documento.
Esta referencia no reemplaza los commits ni metadatos históricos de las corridas
oficiales. Los capítulos de otros responsables son **borradores técnicos basados
en código y pruebas, pendientes de su aporte y revisión**; no acreditan su autoría
ni aprobación. La sección experimental reutiliza el material terminado de #39–#41.

Este es el documento principal del [issue #42](https://github.com/oswaldoaqm/quipudb/issues/42).
Los capítulos separados permiten actualizar el informe en las Partes 2–5 sin
duplicar resultados. Los componentes espaciales, textuales, vectoriales y de IA
previstos para avances posteriores no se presentan como implementados aquí.

## 2. Arquitectura y organización del código

### Arquitectura implementada

```text
Cliente Python
  QueryProcessor: análisis → validación → planificación → ejecución
  Transacciones: deshacer en memoria y locks de tabla
                         ↓
                quipudb_native (pybind11)
                         ↓
  C++: catálogo, tablas, índices y algoritmos externos
                         ↓
               archivos y páginas en disco

Benchmarks Python ─────→ quipudb_native (sin pasar por SQL ni HTTP)

Frontend React → MotorClient → mock predeterminado
                            → cliente HTTP previsto /query y /tables
                              (servidor todavía no implementado)
```

| Ubicación | Responsabilidad actual |
|---|---|
| [core/src/storage](../../core/src/storage/) | Páginas, acceso a disco, Heap y Secuencial |
| [core/src/index](../../core/src/index/) | B+ compartido, tabla agrupada, índice no agrupado y hash extensible |
| [core/src/catalog](../../core/src/catalog/) | Esquemas, codificación, catálogo y apertura de tablas/índices |
| [core/src/external](../../core/src/external/) | Ordenamiento, agrupación y JOIN externos |
| [bindings/module.cpp](../../bindings/module.cpp) | Interfaces nativas para Python |
| [engine/parser](../../engine/parser/) y [engine/planner](../../engine/planner/) | Lexer, AST, validación semántica y selección de accesos |
| [engine/executor](../../engine/executor/) y [engine/transactions](../../engine/transactions/) | Ejecución, mantenimiento de índices, resultados, undo y locks |
| [engine/api](../../engine/api/) | Paquete reservado; no contiene una aplicación HTTP funcional |
| [frontend/src](../../frontend/src/) | Cuatro paneles y clientes mock/HTTP |
| [benchmarks](../../benchmarks/) | Datasets, banco común, experimentos e informes de resultados |
| [docs/informe](./) | Informe modular y material experimental versionado |

`Database` coordina catálogo y apertura de recursos; `TableFile` representa tablas
y `Index` relaciona claves con RIDs. B+ agrupado es una organización de tabla,
no un índice secundario agregado a Heap. B+ no agrupado y Hash extensible son
índices secundarios sobre Heap: las otras organizaciones pueden mover registros.
La coherencia tabla–índice se mantiene explícitamente en los adaptadores de DML
y de experimentos; una llamada aislada al core no sustituye ese trabajo.

### Arquitectura prevista y diferencias documentales

[La arquitectura general](../arquitectura.md) y los [ADRs](../adr/) conservan
decisiones y contratos del proyecto. Deben leerse junto con el código actual:

- El backend FastAPI descrito como conexión entre frontend y motor sigue previsto;
  [engine/api/__init__.py](../../engine/api/__init__.py) solo declara el paquete.
- La reorganización secuencial actual recorre registros incrementalmente por
  grupos y escribe un archivo temporal; no carga toda la tabla en memoria como
  indica una descripción anterior. Conserva buffers y claves por página.
- La presencia de JOIN nativo o de un nodo visual de plan no implica que el parser
  acepte SQL JOIN. Cada nivel tiene un alcance distinto.

Este informe documenta esas diferencias sin modificar la arquitectura histórica.

## 3. Dominio y representación de datos

El experimento utiliza alumnos sintéticos con `codigo INT`, `nombre VARCHAR(16)`
y `promedio DOUBLE`. La clave es `codigo`; no se presupone un sistema académico
completo ni datos personales reales.

[Dominio de datos](dominio_datos.md) explica esquema, representación, generación
reproducible y separación entre datasets oficiales, fixtures de pruebas y mocks.

## 4. Gestión de archivos e índices

[Gestión de archivos, índices y algoritmos externos](gestion_archivos_indices.md)
explica propósito, estructuras, pasos y limitaciones de Heap, Secuencial, B+
agrupado/no agrupado, Hash extensible, ordenamiento, agrupación y JOIN externos.
Incluye referencias a implementaciones y pruebas, sin atribuir soporte SQL por
la sola existencia de una operación nativa.

Estado: borrador técnico pendiente del aporte/revisión de **Oswaldo Alejandro
Quispe Monzon**, responsable declarado de 2.1.1 y 2.1.2.

## 5. Procesamiento de consultas SQL

[Procesamiento SQL](procesamiento_sql.md) describe el recorrido desde texto hasta
resultado, la elección de accesos y el subconjunto realmente aceptado.
La planificación actual es por reglas; no es un optimizador general basado en
costos ni expone todas las capacidades del core.

Estado: borrador técnico pendiente del aporte/revisión de **Sebastian Cangalaya
Martinez**, responsable declarado de 2.1.3.

## 6. Transacciones y concurrencia

[Transacciones y concurrencia](transacciones_concurrencia.md) explica
`BEGIN TRANSACTION`/`END TRANSACTION`, undo en memoria, locks de tabla, timeout y
el alcance de la demostración concurrente. No se afirma recuperación ante caídas
ni un sistema ACID completo.

Estado: borrador técnico pendiente del aporte/revisión de **Juan David Velo Poma**,
responsable declarado de 2.1.4.

## 7. Interfaz de usuario

[Interfaz de usuario](interfaz_usuario.md) describe los cuatro paneles y distingue
su funcionamiento con mocks del contrato HTTP todavía pendiente de integración.
Las métricas simuladas de la interfaz no son evidencia experimental del motor.

Estado: borrador técnico pendiente del aporte/revisión de **Danna Gala**,
responsable declarada de 2.1.5.

## 8. Comparación experimental

El capítulo completo es [Comparación experimental](comparacion_experimental.md):
contiene las gráficas, tablas, ventajas/desventajas, conclusiones y cautelas de
interpretación. Se enlaza como parte integral de este informe para evitar copias
manuales divergentes de sus resultados.

La trazabilidad es #38 (banco común) → #39 (archivos) y #40 (índices) → #41
(gráficas e informe integrado). El issue #42 menciona «gráficas de #38» y depende
de #41; aquí se reutiliza ese material existente, no se ejecutan nuevas corridas.
El [manifiesto de fuentes](fuentes_resultados.json) identifica los seis CSV
oficiales con sus hashes. Los resultados de archivos e índices permanecen
separados; las capacidades no soportadas no se convierten en ceros comparables.

Estado: sección completa reutilizada para **Mauricio Teran**, responsable
de 2.1.6. No atribuye revisión ni aprobación a otros integrantes.

## 9. Conclusiones generales y limitaciones

- El motor separa almacenamiento e índices nativos de procesamiento SQL y
  presentación. La disponibilidad en una capa no demuestra integración total.
- Los resultados oficiales favorecen Heap para la carga inicial frente al
  Secuencial y al Secuencial para las búsquedas PK medidas. Los índices requieren
  distinguir organización de tabla, índice secundario y recuperación del registro;
  las recomendaciones y cifras permanecen en el capítulo experimental.
- No hay una estructura universalmente mejor: importan carga, operación, espacio
  y capacidades. No se extrapolan estos experimentos a otros equipos, dominios,
  concurrencia, JOIN o agrupación externa sin nuevas mediciones.
- SQL es un subconjunto educativo; la API HTTP sigue pendiente. Las transacciones
  coordinadas en Python no aportan WAL, recuperación de caídas ni locks entre
  procesos. `flush` tampoco demuestra persistencia física mediante `fsync`.
- Este avance ofrece una base reutilizable para las Partes 2–5, no una afirmación
  de que los módulos multimodales futuros ya existan.

## 10. Contribuciones y trazabilidad

Las responsabilidades provienen exclusivamente de la sección
[Equipo del README](../../README.md#equipo). Organizan la revisión, **no prueban
que una persona escribió o aprobó estos borradores**.

| Sección | Responsable declarado | Estado del contenido |
|---|---|---|
| 2.1.1–2.1.2: archivos, índices y externos | Oswaldo Alejandro Quispe Monzon | Borrador técnico; requiere aporte y revisión |
| 2.1.3: SQL | Sebastian Cangalaya Martinez | Borrador técnico; requiere aporte y revisión |
| 2.1.4: transacciones | Juan David Velo Poma | Borrador técnico; requiere aporte y revisión |
| 2.1.5: interfaz | Danna Gala | Borrador técnico; requiere aporte y revisión |
| 2.1.6: experimentos | Mauricio Teran | Material completo reutilizado de #39, #40 y #41 |
| Presentación, arquitectura, dominio e integración | Sin autoría individual atribuida | Síntesis documental contrastada con el repositorio |

| Criterio del issue #42 | Evidencia en el informe |
|---|---|
| Diseño arquitectónico y organización del código | Sección 2 y rutas de implementación |
| Dominio de datos usado | [Dominio](dominio_datos.md) y generador existente |
| Explicación de cada algoritmo implementado | Capítulos técnicos de archivos/índices, SQL y transacciones, con pruebas de referencia |
| Parte experimental con gráficas | [Capítulo experimental existente](comparacion_experimental.md) y manifiesto |
| Cada integrante escribe su sección | **Pendiente del aporte individual** en los cuatro borradores señalados; no se considera satisfecho por este armado documental |

Los enlaces a pruebas identifican evidencia de comportamiento en el repositorio;
no significan que se hayan vuelto a ejecutar al redactar el informe. Los cambios
futuros deben actualizar el capítulo correspondiente y registrar su evidencia,
sin alterar ni reinterpretar retroactivamente las corridas oficiales.
