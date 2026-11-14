import Editor, { type OnMount } from "@monaco-editor/react";
import type * as Monaco from "monaco-editor";
import { useEffect, useRef, useState } from "react";

import { ETIQUETA_ERROR, type MotorError } from "@/api/errors";
import { Panel } from "@/components/Panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EXTENSIONES_AUDIO, EXTENSIONES_IMAGEN, insertarRuta } from "@/lib/media-ingest";
import "@/lib/monaco";

interface QueryPanelProps {
  sql: string;
  onSqlChange: (sql: string) => void;
  onEjecutar: () => void;
  ejecutando: boolean;
  error: MotorError | null;
  /** Sube un archivo para SIMILAR_TO y devuelve su ruta en el servidor (#290). */
  onSubirArchivo: (archivo: File) => Promise<string>;
}

const ACEPTA = [...EXTENSIONES_IMAGEN, ...EXTENSIONES_AUDIO].join(",");

const OPCIONES: Monaco.editor.IStandaloneEditorConstructionOptions = {
  minimap: { enabled: false },
  fontSize: 13,
  lineNumbersMinChars: 3,
  scrollBeyondLastLine: false,
  wordWrap: "on",
  // Sin esto el editor no se entera de que el panel cambio de tamano.
  automaticLayout: true,
  padding: { top: 8, bottom: 8 },
  renderLineHighlight: "none",
  overviewRulerLanes: 0,
  scrollbar: { verticalScrollbarSize: 8, horizontalScrollbarSize: 8 },
};

/** Panel de Consultas (2.1.5, issue #35): donde se escribe y se ejecuta SQL. */
export function QueryPanel({
  sql,
  onSqlChange,
  onEjecutar,
  ejecutando,
  error,
  onSubirArchivo,
}: QueryPanelProps) {
  const archivoRef = useRef<HTMLInputElement>(null);
  const [subiendo, setSubiendo] = useState(false);
  const [errorArchivo, setErrorArchivo] = useState<string | null>(null);
  const editorRef = useRef<Monaco.editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<typeof Monaco | null>(null);
  // Monaco tarda mas en montar que el mock en responder, asi que un error puede
  // llegar antes que el editor. Sin este estado en las dependencias, el efecto
  // de abajo correria una sola vez, sin editor, y el subrayado no aparaceria.
  const [editorListo, setEditorListo] = useState(false);

  // El comando de Monaco se registra una vez y conserva el closure de ese
  // momento; la ref es lo que hace que siempre ejecute el SQL actual.
  const ejecutarRef = useRef(onEjecutar);
  useEffect(() => {
    ejecutarRef.current = onEjecutar;
  });

  const alMontar: OnMount = (editor, monaco) => {
    editorRef.current = editor;
    monacoRef.current = monaco;
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => {
      ejecutarRef.current();
    });
    setEditorListo(true);
  };

  // Subraya en el editor el fragmento exacto que el parser senalo. Span y
  // Monaco cuentan lineas y columnas desde 1, asi que el rango viaja sin ajuste.
  useEffect(() => {
    const editor = editorRef.current;
    const monaco = monacoRef.current;
    const model = editor?.getModel();
    if (!monaco || !model) return;

    const marcadores =
      error && error.tieneUbicacion
        ? [
            {
              severity: monaco.MarkerSeverity.Error,
              message: error.message,
              startLineNumber: error.line ?? 1,
              startColumn: error.column ?? 1,
              endLineNumber: error.endLine ?? error.line ?? 1,
              endColumn: error.endColumn ?? (error.column ?? 1) + 1,
            },
          ]
        : [];

    monaco.editor.setModelMarkers(model, "quipudb", marcadores);
  }, [error, editorListo]);

  /**
   * La ruta entra donde esta el cursor, o reemplaza lo seleccionado. Va por
   * `executeEdits` y no por `onSqlChange` para que Ctrl+Z la deshaga.
   */
  const ponerRuta = (ruta: string) => {
    const editor = editorRef.current;
    const model = editor?.getModel();
    const seleccion = editor?.getSelection();
    if (!editor || !model || !seleccion) {
      onSqlChange(insertarRuta(sql, sql.length, sql.length, ruta).texto);
      return;
    }
    const inicio = model.getOffsetAt(seleccion.getStartPosition());
    const fin = model.getOffsetAt(seleccion.getEndPosition());
    const { texto, cursor } = insertarRuta(model.getValue(), inicio, fin, ruta);
    editor.executeEdits("quipudb-archivo", [
      { range: seleccion, text: texto.slice(inicio, cursor), forceMoveMarkers: true },
    ]);
    editor.setPosition(model.getPositionAt(cursor));
    editor.focus();
  };

  const elegirArchivo = async (evento: React.ChangeEvent<HTMLInputElement>) => {
    const archivo = evento.target.files?.[0];
    evento.target.value = "";
    if (!archivo) return;
    setSubiendo(true);
    setErrorArchivo(null);
    try {
      ponerRuta(await onSubirArchivo(archivo));
    } catch (fallo) {
      setErrorArchivo(fallo instanceof Error ? fallo.message : String(fallo));
    } finally {
      setSubiendo(false);
    }
  };

  return (
    <Panel titulo="Consultas" nota="Ctrl+Enter para ejecutar">
      <div className="flex h-full min-h-0 flex-col">
        <div className="min-h-0 flex-1">
          <Editor
            height="100%"
            language="sql"
            theme="vs"
            value={sql}
            onChange={(valor) => onSqlChange(valor ?? "")}
            onMount={alMontar}
            options={OPCIONES}
            loading={
              <span className="text-xs text-muted-foreground">
                Cargando editor...
              </span>
            }
          />
        </div>

        {error && (
          <div className="flex shrink-0 items-baseline gap-2 border-t border-destructive/30 bg-destructive/5 px-3 py-1.5">
            {error.kind && (
              <Badge variant="outline" className="shrink-0 px-1 py-0 text-[9px]">
                {ETIQUETA_ERROR[error.kind]}
              </Badge>
            )}
            <span className="min-w-0 flex-1 text-xs text-destructive">
              {error.message}
            </span>
            {error.tieneUbicacion && (
              <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                linea {error.line}, columna {error.column}
              </span>
            )}
          </div>
        )}

        {errorArchivo && (
          <div className="flex shrink-0 items-baseline gap-2 border-t border-destructive/30 bg-destructive/5 px-3 py-1.5">
            <span className="min-w-0 flex-1 text-xs text-destructive">{errorArchivo}</span>
          </div>
        )}

        <div className="flex shrink-0 items-center gap-2 border-t px-3 py-2">
          <input
            ref={archivoRef}
            type="file"
            accept={ACEPTA}
            onChange={(evento) => void elegirArchivo(evento)}
            className="hidden"
          />
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={subiendo}
            onClick={() => archivoRef.current?.click()}
          >
            {subiendo ? "Subiendo..." : "Usar un archivo"}
          </Button>
          <span className="min-w-0 truncate text-[11px] text-muted-foreground">
            imagen o audio de tu computador para SIMILAR_TO
          </span>
          <Button
            className="ml-auto"
            size="sm"
            onClick={onEjecutar}
            disabled={ejecutando || sql.trim() === ""}
          >
            {ejecutando ? "Ejecutando..." : "Ejecutar"}
          </Button>
        </div>
      </div>
    </Panel>
  );
}
