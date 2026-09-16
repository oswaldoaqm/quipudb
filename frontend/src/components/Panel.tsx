import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface PanelProps {
  titulo: string;
  nota?: ReactNode;
  children: ReactNode;
  className?: string;
}

/** Marco comun de los cuatro paneles: cabecera fija y cuerpo con scroll propio. */
export function Panel({ titulo, nota, children, className }: PanelProps) {
  return (
    <section
      className={cn(
        "flex min-h-0 min-w-0 flex-col overflow-hidden rounded-lg border bg-card",
        className,
      )}
    >
      <header className="flex shrink-0 items-center justify-between gap-2 border-b px-3 py-2">
        <h2 className="text-sm font-medium">{titulo}</h2>
        {nota ? (
          <span className="truncate text-xs text-muted-foreground">{nota}</span>
        ) : null}
      </header>
      <div className="min-h-0 flex-1 overflow-auto">{children}</div>
    </section>
  );
}
