"""Exclusión del SO sin necesidad de abrir bindings."""

import pytest

from engine.distributed.node_lease import NodeLease


def test_lease_no_se_adquiere_dos_veces(tmp_path):
    lease = NodeLease(tmp_path)
    try:
        lease.acquire()
        with pytest.raises(RuntimeError):
            lease.acquire()
    finally:
        lease.close()
    lease.close()


def test_lease_se_libera_sin_borrar_archivo(tmp_path):
    first, second = NodeLease(tmp_path), NodeLease(tmp_path)
    try:
        first.acquire()
        with pytest.raises(OSError):
            second.acquire()
        first.close()
        second.acquire()
        assert first.path.exists()
    finally:
        first.close()
        second.close()


def test_lease_no_crea_directorio(tmp_path):
    with pytest.raises(ValueError):
        NodeLease(tmp_path / "missing").acquire()
    assert not (tmp_path / "missing").exists()
