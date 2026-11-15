import type { Rostro } from "./api.ts";

export const INTERVALO_MS = 334; // 3 cuadros/s como maximo, no 3.003

export class BucleCamara<T> {
  private generacion = 0;
  private activo = false;
  private ocupado = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private controller: AbortController | null = null;
  private tarea: (signal: AbortSignal) => Promise<T | null>;
  private recibir: (valor: T) => void;
  private error: (error: unknown) => void;
  private reloj: () => number;
  private programar: (fn: () => void, ms: number) => ReturnType<typeof setTimeout>;
  private cancelar: (id: ReturnType<typeof setTimeout>) => void;
  constructor(
    tarea: (signal: AbortSignal) => Promise<T | null>,
    recibir: (valor: T) => void,
    error: (error: unknown) => void,
    reloj = () => performance.now(),
    programar = (fn: () => void, ms: number) => setTimeout(fn, ms),
    cancelar = (id: ReturnType<typeof setTimeout>) => clearTimeout(id),
  ) {
    this.tarea = tarea; this.recibir = recibir; this.error = error;
    this.reloj = reloj; this.programar = programar; this.cancelar = cancelar;
  }
  iniciar() {
    if (this.activo) return;
    this.activo = true;
    void this.tick(++this.generacion);
  }
  detener() {
    this.activo = false;
    ++this.generacion;
    if (this.timer !== undefined) this.cancelar(this.timer);
    this.controller?.abort();
  }
  private async tick(generacion: number) {
    if (!this.activo || generacion !== this.generacion) return;
    const inicio = this.reloj();
    if (!this.ocupado) {
      this.ocupado = true;
      this.controller = new AbortController();
      const timer = setTimeout(() => this.controller?.abort(), 90_000);
      try {
        const result = await this.tarea(this.controller.signal);
        if (this.activo && generacion === this.generacion && result !== null) this.recibir(result);
      } catch (e) {
        if (this.activo && generacion === this.generacion) this.error(e);
      } finally {
        clearTimeout(timer);
        this.ocupado = false;
      }
    }
    if (this.activo && generacion === this.generacion) {
      this.timer = this.programar(() => { void this.tick(generacion); }, Math.max(0, INTERVALO_MS - (this.reloj() - inicio)));
    }
  }
}

export function apagarStream(stream: MediaStream | null): void {
  stream?.getTracks().forEach(track => track.stop());
}

function solapamiento(a: Rostro["caja"], b: Rostro["caja"]): number {
  const w = Math.max(0, Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0]));
  const h = Math.max(0, Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1]));
  const interseccion = w * h;
  return interseccion / Math.max(1, a[2] * a[3] + b[2] * b[3] - interseccion);
}

interface Pista { rostro: Rostro; siguiente: number | null; repeticiones: number }

export class Estabilizador {
  private anteriores: Pista[] = [];
  limpiar() { this.anteriores = []; }
  aplicar(rostros: Rostro[]): Rostro[] {
    const disponibles = [...this.anteriores];
    this.anteriores = rostros.map(rostro => {
      const indice = disponibles.findIndex(a => solapamiento(a.rostro.caja, rostro.caja) > .3);
      const anterior = indice >= 0 ? disponibles.splice(indice, 1)[0] : null;
      if (!anterior || !rostro.persona || !anterior.rostro.persona || rostro.persona_id === anterior.rostro.persona_id) {
        // Un desconocido invalida inmediatamente cualquier nombre anterior.
        return { rostro, siguiente: null, repeticiones: 0 };
      }
      const repeticiones = anterior.siguiente === rostro.persona_id ? anterior.repeticiones + 1 : 1;
      return repeticiones >= 3 ? { rostro, siguiente: null, repeticiones: 0 }
        : { rostro: { ...anterior.rostro, caja: rostro.caja }, siguiente: rostro.persona_id, repeticiones };
    });
    return this.anteriores.map(p => p.rostro);
  }
}

export function errorCamara(error: unknown): string {
  const nombre = error instanceof Error ? error.name : "";
  if (nombre === "NotAllowedError" || nombre === "SecurityError") return "El navegador no permitió usar la cámara. Puedes dar permiso o usar la subida de imagen.";
  if (nombre === "NotFoundError") return "No se encontró una cámara. Puedes subir una imagen.";
  return "No se pudo iniciar la cámara. Usa localhost o HTTPS y revisa que no la esté usando otra aplicación.";
}
