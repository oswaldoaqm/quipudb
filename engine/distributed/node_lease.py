"""Exclusión de procesos servidores sobre un directorio de datos local."""

from pathlib import Path


class NodeLease:
    """Lock del SO, liberado incluso al morir el proceso; el archivo no se borra.

    Evita dos servidores de este tipo sobre el mismo volumen. No protege frente
    a programas que ignoren el lock (API local antigua o herramientas offline).
    """

    def __init__(self, directory: Path):
        self.path = directory / ".quipudb-node.lock"
        self._file = None

    def acquire(self):
        import os

        if self._file is not None:
            raise RuntimeError("el lease ya está adquirido")
        if not self.path.parent.is_dir() or self.path.is_symlink():
            raise ValueError("se requiere un directorio propio existente")
        file = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                if self.path.stat().st_size == 0:
                    file.write(b"0")
                    file.flush()
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            file.close()
            raise
        self._file = file

    def close(self):
        if self._file is not None:
            self._file.close()
            self._file = None
