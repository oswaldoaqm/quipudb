import { useRef, useState } from "react";

import { Panel } from "@/components/Panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type {
  ColumnInfo,
  IndexInfo,
  LoadResult,
  TableInfo,
} from "@/api/types";
import { cn } from "@/lib/utils";

interface FilesPanelProps {
  tablas: TableInfo[];
  cargando: boolean;
  /** Sube el CSV y devuelve el informe del motor. Lo resuelve `App`. */
  onCargarCsv: (tabla: string, archivo: File) => Promise<LoadResult>;
}

/** VARCHAR es el unico que declara tamano; el resto lo tiene fijo por tipo. */
function tipoDe(columna: ColumnInfo): string {
  return columna.size === null
    ? columna.type
    : `${columna.type}(${columna.size})`;
}

function Columna({ columna }: { columna: ColumnInfo }) {
  return (
    <li className="flex items-baseline justify-between gap-2 px-3 py-1">
      <span className="flex min-w-0 items-baseline gap-1.5">
        <span className="truncate font-mono text-xs">{columna.name}</span>
        {columna.is_primary_key && (
          <Badge variant="secondary" className="shrink-0 px-1 py-0 text-[9px]">
            PK
          </Badge>
        )}
      </span>
      <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
        {tipoDe(columna)}
      </span>
    </li>
  );
}

function Indice({ indice }: { indice: IndexInfo }) {
  return (
    <li className="px-3 py-1.5">
      <div className="truncate font-mono text-xs">{indice.name}</div>
      <div className="mt-0.5 flex flex-wrap items-center gap-1">
        <Badge variant="outline" className="px-1 py-0 font-mono text-[9px]">
          {indice.structure}
        </Badge>
        <span className="font-mono text-[10px] text-muted-foreground">
          {indice.column}
        </span>
        {/* El hash no puede recorrer un intervalo: lo dice el propio contrato
            del core con supports_range (ADR 0002, regla 2). El R-Tree tampoco
            hace rangos escalares, pero responde radio, k-NN y poligono. */}
        <span className="text-[10px] text-muted-foreground">
          {indice.structure === "rtree"
            ? "· radio, k-NN y polígono"
            : indice.supports_range ? "· igualdad y rango" : "· solo igualdad"}
        </span>
      </div>
    </li>
  );
}

/**
 * El informe de una carga: cuantas entraron, cuantas no y por que.
 *
 * Las primeras lineas con error se listan porque un conteo a secas no deja
 * arreglar el archivo; `errors_truncated` avisa de que hay mas para que nadie
 * crea que las que ve son todas.
 */
function Informe({ informe }: { informe: LoadResult }) {
  const hayFallos = informe.failed > 0;

  return (
    <div
      className={cn(
        "mt-2 rounded border px-2 py-1.5 text-[11px]",
        hayFallos
          ? "border-amber-500/40 bg-amber-500/10"
          : "border-emerald-500/40 bg-emerald-500/10",
      )}
    >
      <p className={hayFallos ? "" : "text-emerald-700 dark:text-emerald-400"}>
        {informe.inserted.toLocaleString("es-PE")}{" "}
        {informe.inserted === 1 ? "fila entro" : "filas entraron"}
        {hayFallos
          ? ` y ${informe.failed.toLocaleString("es-PE")} ${
              informe.failed === 1 ? "fallo" : "fallaron"
            }.`
          : "."}
        {/* Que se leyo en cp1252 no es un error, pero explica las tildes
            raras si el archivo en realidad era otra cosa. */}
        {informe.encoding === "cp1252" && (
          <span className="text-muted-foreground"> Leido como cp1252.</span>
        )}
      </p>

      {informe.errors.length > 0 && (
        <ul className="mt-1 max-h-32 space-y-0.5 overflow-auto">
          {informe.errors.map((fallo) => (
            <li key={fallo.line} className="font-mono text-[10px]">
              <span className="text-muted-foreground">
                linea {fallo.line}:
              </span>{" "}
              {fallo.error}
            </li>
          ))}
          {informe.errors_truncated && (
            <li className="text-[10px] text-muted-foreground">
              y {(informe.failed - informe.errors.length).toLocaleString("es-PE")} mas.
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

/**
 * El boton de carga, con el nombre de la tabla a la vista.
 *
 * Va dentro del detalle y no en la cabecera del panel a proposito: la tabla
 * de destino es la que esta seleccionada, y tenerlo aqui hace que se lea sin
 * ambiguedad (criterio 2 del issue #113).
 */
function Cargar({
  tabla,
  onCargarCsv,
}: {
  tabla: TableInfo;
  onCargarCsv: FilesPanelProps["onCargarCsv"];
}) {
  const entradaRef = useRef<HTMLInputElement>(null);
  const [cargando, setCargando] = useState(false);
  const [informe, setInforme] = useState<LoadResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const elegir = async (evento: React.ChangeEvent<HTMLInputElement>) => {
    const archivo = evento.target.files?.[0];
    // Se limpia siempre: sin esto, volver a elegir EL MISMO archivo no
    // dispara `change` y el boton parece roto.
    evento.target.value = "";
    if (!archivo) return;

    setCargando(true);
    setInforme(null);
    setError(null);
    try {
      setInforme(await onCargarCsv(tabla.name, archivo));
    } catch (fallo) {
      setError(fallo instanceof Error ? fallo.message : String(fallo));
    } finally {
      setCargando(false);
    }
  };

  return (
    <div className="px-3 pt-2">
      <input
        ref={entradaRef}
        type="file"
        accept=".csv,text/csv"
        onChange={(evento) => void elegir(evento)}
        className="hidden"
      />
      <Button
        type="button"
        size="xs"
        variant="outline"
        disabled={cargando}
        onClick={() => entradaRef.current?.click()}
      >
        {cargando ? "Cargando..." : "Cargar CSV"}
      </Button>
      <span className="ml-2 text-[10px] text-muted-foreground">
        hacia <span className="font-mono">{tabla.name}</span>
      </span>

      {error !== null && (
        <p className="mt-2 rounded border border-destructive/40 bg-destructive/10 px-2 py-1.5 text-[11px]">
          {error}
        </p>
      )}
      {informe !== null && <Informe informe={informe} />}
    </div>
  );
}

function Detalle({
  tabla,
  onCargarCsv,
}: {
  tabla: TableInfo;
  onCargarCsv: FilesPanelProps["onCargarCsv"];
}) {
  return (
    <div className="pb-2">
      <div className="px-3 py-2">
        <div className="truncate font-mono text-sm font-medium">
          {tabla.name}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1">
          <Badge variant="outline" className="px-1 py-0 font-mono text-[9px]">
            {tabla.storage}
          </Badge>
          <span className="text-[10px] text-muted-foreground">
            {tabla.record_count.toLocaleString("es-PE")} registros
          </span>
        </div>
      </div>

      {/* La clave remonta el componente cuando cambia la tabla: el informe de
          una carga no debe quedar colgando bajo otra tabla. */}
      <Cargar key={tabla.name} tabla={tabla} onCargarCsv={onCargarCsv} />

      <h3 className="px-3 pt-1 pb-0.5 text-[10px] tracking-wide text-muted-foreground uppercase">
        Columnas
      </h3>
      <ul>
        {tabla.columns.map((columna) => (
          <Columna key={columna.name} columna={columna} />
        ))}
      </ul>

      <h3 className="px-3 pt-2 pb-0.5 text-[10px] tracking-wide text-muted-foreground uppercase">
        Indices
      </h3>
      {tabla.indexes.length === 0 ? (
        <p className="px-3 py-1 text-[11px] text-muted-foreground">
          Sin indices secundarios.
        </p>
      ) : (
        <ul>
          {tabla.indexes.map((indice) => (
            <Indice key={indice.name} indice={indice} />
          ))}
        </ul>
      )}
    </div>
  );
}

/** Panel de Archivos (2.1.5, issue #34): que tablas hay y como estan hechas. */
export function FilesPanel({
  tablas,
  cargando,
  onCargarCsv,
}: FilesPanelProps) {
  const [elegida, setElegida] = useState<string | null>(null);

  // Caer a la primera evita un efecto para la seleccion inicial, y sostiene el
  // caso de que la tabla elegida desaparezca del catalogo al recargarlo.
  const tabla = tablas.find((t) => t.name === elegida) ?? tablas[0] ?? null;

  return (
    <Panel titulo="Archivos" nota={`${tablas.length} tablas`}>
      {cargando ? (
        <p className="p-3 text-xs text-muted-foreground">Cargando catalogo...</p>
      ) : tabla === null ? (
        <p className="p-3 text-xs text-muted-foreground">
          No hay tablas en el catalogo.
        </p>
      ) : (
        <div className="flex h-full min-h-0 flex-col">
          {/* El tope de altura es lo que impide que un catalogo largo empuje
              el detalle fuera del panel. */}
          <ul className="max-h-[45%] shrink-0 overflow-auto border-b">
            {tablas.map((fila) => (
              <li key={fila.name}>
                <button
                  type="button"
                  onClick={() => setElegida(fila.name)}
                  aria-current={fila.name === tabla.name}
                  className={cn(
                    "flex w-full items-baseline justify-between gap-2 px-3 py-1.5 text-left hover:bg-muted/60",
                    fila.name === tabla.name && "bg-muted",
                  )}
                >
                  <span className="truncate font-mono text-xs">{fila.name}</span>
                  <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                    {fila.storage}
                  </span>
                </button>
              </li>
            ))}
          </ul>

          <div className="min-h-0 flex-1 overflow-auto">
            <Detalle tabla={tabla} onCargarCsv={onCargarCsv} />
          </div>
        </div>
      )}
    </Panel>
  );
}
