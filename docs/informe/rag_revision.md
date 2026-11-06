# Revisión de respuestas del RAG (#253)

Las métricas de [rag_evaluacion.md](rag_evaluacion.md) dicen si Gemini *citó* el
paper esperado, no si lo que afirma está en los fragmentos que leyó. Para eso se
revisaron cinco respuestas de la corrida
[`rag_respuestas.json`](rag_respuestas.json) (corrida `20261106T070829Z`): cada afirmación se
buscó en el texto del chunk que cita. Los chunks se recuperaron otra vez con la
misma consulta; la recuperación es determinista y devolvió las mismas fuentes,
en el mismo orden, en los cinco casos.

> **Quién revisó.** Esta revisión la hizo Claude (el asistente que implementó
> la Parte 5), comparando cada afirmación con el texto de su fuente. Conviene
> que alguien del equipo confirme al menos dos casos antes de la entrega.

## Resultado

| Pregunta | ¿Lo afirmado está en las fuentes? | ¿Las citas apuntan al chunk correcto? | Observaciones |
|---|---|---|---|
| p04: dos codificadores densos para preguntas de dominio abierto | Sí | Sí, pero a RocketQA y no a DPR | Los chunks de DPR, el paper esperado, estaban entre las fuentes 4, 5 y 7, pero Gemini citó los de RocketQA, que describen la misma arquitectura como antecedente. Es el único caso de las 30 preguntas sin el paper esperado citado. La recuperación funcionó; la elección fue del modelo |
| p07: cómo soporta ALEX las inserciones | Sí | En parte | Los *gapped arrays*, la búsqueda exponencial y el desplazamiento a la derecha están en las fuentes, pero dos citas apuntan a chunks que no los mencionan: [7] para los espacios vacíos y [1][5] para los *gapped arrays*, que están en [6] y [8]. Ninguna afirmación queda sin respaldo; algunas quedan mal atribuidas |
| p20: olas de miseria en los R-trees | Sí | Sí | Agrupación de *splits*, presión sobre el búfer, rendimiento no determinista y efecto en la utilización de nodos: todo está en las tres fuentes, todas del paper esperado |
| p27: inferencia de pertenencia sigilosa en RAG | Sí | Sí | *Interrogation Attack*, doc2query, *few-shot prompting* y la ausencia de *jailbreaking* están en la fuente [2]. "Consultas topográficas" es una mala traducción de *topically aligned* |
| p13: BM25 y BM25F en Lucene | Sí | Sí | Todo sale de la fuente [3], del paper esperado: los componentes Query, Weight y Scorers, las clases `ShouldBooleanScorer`, `MustBooleanScorer` y `NotBooleanScorer`, los constructores de BM25 y BM25F, y que se ignoran los campos del `QueryParser`. Los nombres de clase se copiaron exactos |

Ninguna de las cinco inventa hechos. Lo que falla es la precisión de las citas,
y una vez el paper elegido para citar. Las citas a números que no existen ya se
quitan solas (0 en las 30 respuestas).

## Defecto encontrado en la extracción

Al revisar p07 apareció un defecto que no es de Gemini: en el PDF de ALEX,
`pypdf` no sabe a qué letras corresponden los glifos de ligadura de su fuente y
escribe su nombre (`shi/f_ted`, `signi/f_icantly`). BM25 nunca encuentra esas
palabras. Instalar `fontTools`, que `pypdf` sugiere en sus avisos, no lo
arregla. En una muestra de 30 PDFs del corpus no apareció ninguno, así que es
raro, pero se corrigió en la limpieza (#241): un nombre de glifo `/x_y` vuelve a
ser sus letras. La evaluación se corrió antes de esa corrección; afecta a un
solo paper.

## Lo que queda para mejorar

- Pedirle a Gemini, en las instrucciones, que cite el chunk exacto de cada
  afirmación y no el primero del mismo tema.
- Revisar más respuestas, en particular las de preguntas con dos papers
  esperados (p02, p12, p19 y p22).
