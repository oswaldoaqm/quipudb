import { Panel } from "@/components/Panel";
import { Button } from "@/components/ui/button";

interface QueryPanelProps {
  sql: string;
  onSqlChange: (sql: string) => void;
  onEjecutar: () => void;
  ejecutando: boolean;
}

/** Panel de Consultas (2.1.5). El editor Monaco entra en el issue #35. */
export function QueryPanel({
  sql,
  onSqlChange,
  onEjecutar,
  ejecutando,
}: QueryPanelProps) {
  return (
    <Panel titulo="Consultas" nota="Ctrl+Enter para ejecutar">
      <div className="flex h-full min-h-0 flex-col">
        <textarea
          value={sql}
          onChange={(evento) => onSqlChange(evento.target.value)}
          onKeyDown={(evento) => {
            if (evento.key === "Enter" && (evento.ctrlKey || evento.metaKey)) {
              evento.preventDefault();
              onEjecutar();
            }
          }}
          spellCheck={false}
          className="min-h-0 flex-1 resize-none bg-transparent p-3 font-mono text-sm outline-none"
          placeholder="SELECT * FROM alumnos WHERE promedio BETWEEN 15 AND 17"
        />
        <div className="flex shrink-0 justify-end border-t px-3 py-2">
          <Button size="sm" onClick={onEjecutar} disabled={ejecutando}>
            {ejecutando ? "Ejecutando..." : "Ejecutar"}
          </Button>
        </div>
      </div>
    </Panel>
  );
}
