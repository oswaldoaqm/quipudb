"""
Mide que condicion de merge conviene para el hash extensible (#19).

No simula el disco: simula la ESTRUCTURA. Directorio de 2^g entradas, buckets
con profundidad local, split cuando se llena, merge segun la condicion que se
este probando. Las claves entran y salen como en el regimen que el 2.1.6 dice
medir: "rendimiento con inserciones/eliminaciones frecuentes".

Lo que se quiere saber de cada condicion:
  - cuantos merges dispara de verdad (si son 0, la condicion no sirve)
  - cuantas veces oscila (partir y fusionar el mismo bucket seguido)
  - cuanto espacio recupera al final (buckets vivos)
"""
import random

FNV_OFF = 14695981039346656037
FNV_PR = 1099511628211
M64 = (1 << 64) - 1


def h64(k: int) -> int:
    h = FNV_OFF
    for b in k.to_bytes(4, "little", signed=False):
        h = ((h ^ b) * FNV_PR) & M64
    return h


class Hash:
    def __init__(self, capacidad, condicion):
        self.c = capacidad
        self.cond = condicion          # (a, b, cap) -> bool
        self.g = 0
        self.dir = [0]                 # indice de bucket
        self.buckets = {0: []}         # id -> lista de hashes
        self.local = {0: 0}
        self.sig = 1
        self.overflow = {0: 0}         # paginas de overflow por bucket
        self.splits = 0
        self.merges = 0
        self.dobla = 0
        self.reduce = 0
        self.oscila = 0
        self._ultimo_split = None

    # -- consulta -----------------------------------------------------------
    def idx(self, h):
        return h & ((1 << self.g) - 1)

    def cabe(self, b):
        # capacidad total = primaria + overflow
        return len(self.buckets[b]) < self.c * (1 + self.overflow[b])

    # -- insercion ----------------------------------------------------------
    def insert(self, k):
        h = h64(k)
        while True:
            b = self.dir[self.idx(h)]
            if self.cabe(b):
                self.buckets[b].append(h)
                return
            self.crecer(self.idx(h), b)

    def crecer(self, i, b):
        L = self.local[b]
        altos = {e >> L for e in self.buckets[b]}
        if len(altos) == 1:            # inseparables: overflow
            self.overflow[b] += 1
            return
        if L == self.g:
            self.dir = self.dir + self.dir
            self.g += 1
            self.dobla += 1
        bit = 1 << L
        entradas = self.buckets[b]
        nuevo = self.sig
        self.sig += 1
        self.local[b] = L + 1
        self.local[nuevo] = L + 1
        self.buckets[b] = []
        self.buckets[nuevo] = []
        self.overflow[b] = 0
        self.overflow[nuevo] = 0
        bajos = i & (bit - 1)
        for j in range(len(self.dir)):
            if (j & (bit - 1)) == bajos:
                self.dir[j] = nuevo if (j & bit) else b
        for e in entradas:
            (self.buckets[nuevo] if (e & bit) else self.buckets[b]).append(e)
        self.splits += 1
        if self._ultimo_split == (b, L):
            self.oscila += 1
        self._ultimo_split = (b, L)

    # -- borrado ------------------------------------------------------------
    def remove(self, k):
        h = h64(k)
        b = self.dir[self.idx(h)]
        if h in self.buckets[b]:
            self.buckets[b].remove(h)
            self.fusionar(self.idx(h), b)
            return True
        return False

    def fusionar(self, i, b):
        L = self.local[b]
        if L == 0:
            return
        bit = 1 << (L - 1)
        hermano_i = i ^ bit
        if hermano_i >= len(self.dir):
            return
        hb = self.dir[hermano_i]
        if hb == b or self.local[hb] != L:
            return
        if self.overflow[b] or self.overflow[hb]:
            return                      # cadena de overflow: no se fusiona
        if not self.cond(len(self.buckets[b]), len(self.buckets[hb]), self.c):
            return
        # fusiona hb dentro de b
        self.buckets[b] += self.buckets[hb]
        self.local[b] = L - 1
        for j in range(len(self.dir)):
            if self.dir[j] == hb:
                self.dir[j] = b
        del self.buckets[hb], self.local[hb], self.overflow[hb]
        self.merges += 1
        self._ultimo_split = None
        # reducir el directorio si ningun bucket queda en la profundidad global
        while self.g > 0 and all(self.local[x] < self.g for x in set(self.dir)):
            self.dir = self.dir[: len(self.dir) // 2]
            self.g -= 1
            self.reduce += 1


CONDICIONES = {
    "vacio (lo que dice el #19)": lambda a, b, c: a == 0 or b == 0,
    "suma <= c/4":               lambda a, b, c: a + b <= c // 4,
    "suma <= c/2":               lambda a, b, c: a + b <= c // 2,
    "suma <= 3c/4":              lambda a, b, c: a + b <= (3 * c) // 4,
    "suma <= c (caben juntos)":  lambda a, b, c: a + b <= c,
}


def escenario(nombre, capacidad, guion, semilla=1):
    print(f"\n### {nombre}   (capacidad {capacidad})")
    print(f"{'condicion':30} {'merges':>7} {'oscila':>7} {'dobla':>6} "
          f"{'reduce':>7} {'buckets':>8} {'carga':>7}")
    for etiqueta, cond in CONDICIONES.items():
        random.seed(semilla)
        H = Hash(capacidad, cond)
        vivas = set()
        for op, k in guion():
            if op == "i":
                if k not in vivas:
                    H.insert(k)
                    vivas.add(k)
            else:
                if k in vivas:
                    H.remove(k)
                    vivas.discard(k)
        nb = len(set(H.dir))
        carga = len(vivas) / (nb * capacidad) if nb else 0
        print(f"{etiqueta:30} {H.merges:>7} {H.oscila:>7} {H.dobla:>6} "
              f"{H.reduce:>7} {nb:>8} {carga:>6.0%}")


def guion_equilibrado(n=20000, universo=6000):
    """Regimen del 2.1.6: inserciones y eliminaciones frecuentes, tamano estable."""
    def g():
        rng = random.Random(7)
        for _ in range(n):
            yield ("i" if rng.random() < 0.5 else "d", rng.randrange(universo))
    return g


def guion_carga_y_vaciado(n=6000):
    """Cargar todo y despues borrar todo: el caso facil."""
    def g():
        ks = list(range(n))
        rng = random.Random(9)
        rng.shuffle(ks)
        for k in ks:
            yield ("i", k)
        rng.shuffle(ks)
        for k in ks:
            yield ("d", k)
    return g


def guion_crece_y_encoge(n=8000):
    """Crece hasta n, encoge a n/4, vuelve a crecer. Lo que pasa en una tabla real."""
    def g():
        rng = random.Random(11)
        ks = list(range(n))
        rng.shuffle(ks)
        for k in ks:
            yield ("i", k)
        for k in ks[: (3 * n) // 4]:
            yield ("d", k)
        for k in ks[: (3 * n) // 4]:
            yield ("i", k)
    return g


if __name__ == "__main__":
    for cap in (4, 32, 408):
        escenario("inserciones y borrados frecuentes", cap, guion_equilibrado())
    for cap in (4, 408):
        escenario("cargar todo y vaciar todo", cap, guion_carga_y_vaciado())
        escenario("crece, encoge a 1/4, vuelve a crecer", cap, guion_crece_y_encoge())
