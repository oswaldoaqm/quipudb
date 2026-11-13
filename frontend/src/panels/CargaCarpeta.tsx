import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import type { TableInfo, VocabularyInfo } from "@/api/types";
import {
  consultaDeEjemplo,
  filtrarArchivos,
  revisarTabla,
  type ArchivoElegido,
  type Avance,
  type InformeCarga,
  type Vocabulario,
} from "@/lib/media-ingest";
import { cn } from "@/lib/utils";

export interface CargaCarpetaProps {
  tabla: TableInfo;
  /** Sube, entrena o asocia el vocabulario y carga. Lo resuelve `App`. */
  onCargarCarpeta: (
    opciones: { tabla: string; nombre: string; archivos: ArchivoElegido[]; vocabulario: Vocabulario },
    alAvanzar: (avance: Avance) => void,
  ) => Promise<InformeCarga>;
  onListarVocabularios: () => Promise<VocabularyInfo[]>;
  /** Lleva una consulta al editor sin ejecutarla. */
  onUsarConsulta: (sql: string) => void;
}

interface Elegida {
  nombre: string;
  archivos: ArchivoElegido[];
  omitidos: number;
  imagenes: number;
  audios: number;
}

const ETIQUETA: Record<Avance["fase"], string> = {
  subiendo: "Subiendo",
  entrenando: "Entrenando el vocabulario:",
  cargando: "Cargando",
};

/**
 * Lo que manda el navegador al elegir una carpeta trae la carpeta misma
 * delante (`fotos/a.png`). Se le quita: la carpeta subida ya se llama asi.
 */
function elegidosDe(lista: FileList): Elegida {
  const archivos = Array.from(lista, (file) => {
    const ruta = file.webkitRelativePath || file.name;
    return { name: ruta.split("/").slice(1).join("/") || file.name, file, raiz: ruta.split("/")[0] };
  });
  const nombre = archivos[0]?.raiz ?? "carpeta";
  const { multimedia, omitidos, imagenes, audios } = filtrarArchivos(
    archivos.map(({ name, file }) => ({ name, file })),
  );
  return { nombre, archivos: multimedia, omitidos, imagenes, audios };
}

function plural(n: number, uno: string, varios: string): string {
  return `${n.toLocaleString("es-PE")} ${n === 1 ? uno : varios}`;
}

/** El mismo recuadro del informe del CSV, con los archivos en vez de lineas. */
function InformeCarpeta({
  informe,
  consulta,
  onUsarConsulta,
}: {
  informe: InformeCarga;
  consulta: string | null;
  onUsarConsulta: (sql: string) => void;
}) {
  const hayFallos = informe.failed > 0 || informe.errors.length > 0;
  return (
    <div
      className={cn(
        "mt-2 rounded border px-2 py-1.5 text-[11px]",
        hayFallos ? "border-amber-500/40 bg-amber-500/10" : "border-emerald-500/40 bg-emerald-500/10",
      )}
    >
      <p className={hayFallos ? "" : "text-emerald-700 dark:text-emerald-400"}>
        {plural(informe.inserted, "archivo entró", "archivos entraron")}
        {informe.failed > 0 ? ` y ${plural(informe.failed, "falló", "fallaron")}.` : "."}
      </p>
      <p className="mt-0.5 text-[10px] text-muted-foreground">
        {informe.entrenado
          ? `Vocabulario entrenado con ${plural(informe.entrenado.documents ?? 0, "objeto", "objetos")}: `
          : "Vocabulario: "}
        <span className="font-mono">{informe.vocabulary}</span>
      </p>

      {informe.errors.length > 0 && (
        <ul className="mt-1 max-h-32 space-y-0.5 overflow-auto">
          {informe.errors.map((fallo, i) => (
            <li key={`${fallo.path}-${i}`} className="font-mono text-[10px]">
              <span className="text-muted-foreground">{fallo.path}:</span> {fallo.error}
            </li>
          ))}
          {informe.errors_truncated && (
            <li className="text-[10px] text-muted-foreground">y otros que no se listan.</li>
          )}
        </ul>
      )}

      {consulta ? (
        <Button
          type="button"
          size="xs"
          variant="outline"
          className="mt-1.5"
          onClick={() => onUsarConsulta(consulta)}
        >
          Probar SIMILAR_TO
        </Button>
      ) : (
        <p className="mt-1 text-[10px] text-muted-foreground">
          Para buscar con SIMILAR_TO, crea un índice HNSW o IVF sobre{" "}
          <span className="font-mono">rasgos</span>.
        </p>
      )}
    </div>
  );
}

/**
 * Cargar una carpeta de imágenes o audios en una tabla multimedia (#289).
 *
 * Va debajo de "Cargar CSV" y con el mismo aspecto. Elegir la carpeta no sube
 * nada todavía: primero se ve cuántos archivos hay y con qué vocabulario se
 * van a describir, y recién con "Cargar" empiezan los tres pasos.
 */
export function CargaCarpeta({
  tabla,
  onCargarCarpeta,
  onListarVocabularios,
  onUsarConsulta,
}: CargaCarpetaProps) {
  const entradaRef = useRef<HTMLInputElement>(null);
  const [elegida, setElegida] = useState<Elegida | null>(null);
  const [vocabularios, setVocabularios] = useState<VocabularyInfo[]>([]);
  const [vocabulario, setVocabulario] = useState<string>("nuevo");
  const [avance, setAvance] = useState<Avance | null>(null);
  const [informe, setInforme] = useState<InformeCarga | null>(null);
  const [error, setError] = useState<string | null>(null);

  const revision = revisarTabla(tabla);
  const dimension = "dimension" in revision ? revision.dimension : null;

  // `webkitdirectory` no esta en los tipos de React; sin el, el dialogo
  // elige archivos sueltos en vez de una carpeta.
  useEffect(() => {
    entradaRef.current?.setAttribute("webkitdirectory", "");
  }, []);

  if (dimension === null) {
    // Solo se avisa en tablas con un VECTOR: en una tabla de alumnos el
    // mensaje no le dice nada a nadie.
    return tabla.columns.some((c) => c.type === "VECTOR") && "motivo" in revision ? (
      <p className="px-3 pt-2 text-[10px] text-muted-foreground">{revision.motivo}</p>
    ) : null;
  }

  const compatibles = vocabularios.filter((v) => v.k === dimension);
  const mezcla = elegida !== null && elegida.imagenes > 0 && elegida.audios > 0;
  const cargando = avance !== null;

  const elegir = (evento: React.ChangeEvent<HTMLInputElement>) => {
    const lista = evento.target.files;
    if (!lista || lista.length === 0) return;
    setElegida(elegidosDe(lista));
    evento.target.value = "";
    setInforme(null);
    setError(null);
    setVocabulario("nuevo");
    void onListarVocabularios()
      .then(setVocabularios)
      .catch(() => setVocabularios([]));
  };

  const cargar = async () => {
    if (elegida === null) return;
    setError(null);
    setInforme(null);
    setAvance({ fase: "subiendo", hechos: 0, total: elegida.archivos.length });
    try {
      setInforme(
        await onCargarCarpeta(
          {
            tabla: tabla.name,
            nombre: elegida.nombre,
            archivos: elegida.archivos,
            vocabulario:
              vocabulario === "nuevo"
                ? { tipo: "nuevo", k: dimension }
                : { tipo: "existente", path: vocabulario },
          },
          setAvance,
        ),
      );
      setElegida(null);
    } catch (fallo) {
      setError(fallo instanceof Error ? fallo.message : String(fallo));
    } finally {
      setAvance(null);
    }
  };

  const consulta =
    informe?.ejemplo != null
      ? consultaDeEjemplo(tabla.name, informe.ejemplo, tabla.indexes.map((i) => i.structure))
      : null;

  return (
    <div className="px-3 pt-2">
      <input
        ref={entradaRef}
        type="file"
        multiple
        onChange={elegir}
        className="hidden"
      />
      <Button
        type="button"
        size="xs"
        variant="outline"
        disabled={cargando}
        onClick={() => entradaRef.current?.click()}
      >
        Cargar carpeta
      </Button>
      <span className="ml-2 text-[10px] text-muted-foreground">
        imágenes o audios
      </span>

      {elegida !== null && !cargando && (
        <div className="mt-2 rounded border px-2 py-1.5 text-[11px]">
          <p>
            <span className="font-mono">{elegida.nombre}</span>:{" "}
            {elegida.imagenes > 0 && plural(elegida.imagenes, "imagen", "imágenes")}
            {elegida.imagenes > 0 && elegida.audios > 0 && " y "}
            {elegida.audios > 0 && plural(elegida.audios, "audio", "audios")}
            {elegida.archivos.length === 0 && "ningún archivo multimedia"}
            {elegida.omitidos > 0 && (
              <span className="text-muted-foreground">
                {" "}
                ({plural(elegida.omitidos, "archivo omitido", "archivos omitidos")})
              </span>
            )}
          </p>

          <label className="mt-1.5 block text-[10px] text-muted-foreground">
            Vocabulario
            <select
              value={vocabulario}
              onChange={(e) => setVocabulario(e.target.value)}
              title={vocabulario === "nuevo" ? undefined : vocabulario}
              className="mt-0.5 block w-full rounded border bg-background px-1 py-0.5 text-[11px] text-foreground"
            >
              <option value="nuevo">entrenar con esta carpeta (K = {dimension})</option>
              {compatibles.map((v) => (
                <option key={v.path} value={v.path}>
                  {v.path.split("/").pop()} · {v.kind}, {v.documents} objetos
                </option>
              ))}
            </select>
          </label>
          {mezcla && vocabulario === "nuevo" && (
            <p className="mt-1 text-[10px] text-amber-700 dark:text-amber-400">
              La carpeta mezcla imágenes y audios; un vocabulario es de una sola modalidad.
            </p>
          )}

          <div className="mt-1.5 flex gap-1.5">
            <Button
              type="button"
              size="xs"
              disabled={elegida.archivos.length === 0 || (mezcla && vocabulario === "nuevo")}
              onClick={() => void cargar()}
            >
              Cargar en <span className="font-mono">{tabla.name}</span>
            </Button>
            <Button type="button" size="xs" variant="ghost" onClick={() => setElegida(null)}>
              Cancelar
            </Button>
          </div>
        </div>
      )}

      {avance !== null && (
        <div className="mt-2 text-[11px]">
          <p>
            {ETIQUETA[avance.fase]} {avance.hechos.toLocaleString("es-PE")} de{" "}
            {avance.total.toLocaleString("es-PE")}
          </p>
          <div className="mt-1 h-1 overflow-hidden rounded bg-muted">
            <div
              className="h-full bg-primary transition-[width]"
              style={{ width: `${avance.total ? (100 * avance.hechos) / avance.total : 0}%` }}
            />
          </div>
        </div>
      )}

      {error !== null && (
        <p className="mt-2 rounded border border-destructive/40 bg-destructive/10 px-2 py-1.5 text-[11px]">
          {error}
        </p>
      )}
      {informe !== null && (
        <InformeCarpeta informe={informe} consulta={consulta} onUsarConsulta={onUsarConsulta} />
      )}
    </div>
  );
}
