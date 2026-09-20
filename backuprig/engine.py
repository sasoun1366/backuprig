"""Orchestrates a backup run: pick the adapter, open a transport, fetch the
config, hash it, and (optionally) store the result.

`run_backup()` accepts an injectable `session_factory` so it is fully
testable offline (see tests/test_engine.py), while `backup_device()` is the
convenience entry point the GUI and CLI actually call, which wires up the
real SSH transport.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .adapters import get_adapter
from .adapters.base import BackupResult, ConnectionParams
from .store import BackupRecord, Device, Store


@dataclass
class RunOutcome:
    ok: bool
    filename: str
    content: bytes
    message: str
    sha256: str
    size_bytes: int
    took_seconds: float


def _device_to_conn(device: Device) -> ConnectionParams:
    return ConnectionParams(
        host=device.host,
        port=device.port,
        username=device.username,
        password=device.secret if not device.options.get("use_key") else None,
        key_path=device.secret if device.options.get("use_key") else None,
        options=device.options or {},
    )


def run_backup(device: Device, session_factory: Callable[[ConnectionParams], object],
                timeout: float = 30.0) -> RunOutcome:
    """session_factory(conn) -> an object with .exec(cmd, timeout) -> str and
    .close(). Real code passes transport.SSHSession.connect; tests pass a
    fake in-memory session."""
    start = time.monotonic()
    adapter = get_adapter(device.vendor)
    conn = _device_to_conn(device)

    session = None
    try:
        session = session_factory(conn)
        result: BackupResult = adapter.fetch(conn, session.exec, timeout=timeout)
    except Exception as exc:
        result = BackupResult(ok=False, filename=adapter.default_filename(conn),
                               message=f"connection/transport error: {exc}")
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    took = time.monotonic() - start
    digest = hashlib.sha256(result.content).hexdigest() if result.content else ""
    return RunOutcome(
        ok=result.ok, filename=result.filename, content=result.content,
        message=result.message, sha256=digest, size_bytes=len(result.content),
        took_seconds=took,
    )


def backup_device(device: Device, timeout: float = 30.0) -> RunOutcome:
    """Convenience wrapper wiring up the real SSH transport."""
    from .transport import SSHSession

    return run_backup(device, lambda conn: SSHSession.connect(conn, connect_timeout=timeout),
                       timeout=timeout)


def backup_and_store(store: Store, device: Device, timeout: float = 30.0) -> BackupRecord:
    """Run a backup and persist the result (success or failure) in the store,
    returning the saved BackupRecord."""
    outcome = backup_device(device, timeout=timeout)
    record = BackupRecord(
        id=None, device_id=device.id, taken_at=time.time(), filename=outcome.filename,
        size_bytes=outcome.size_bytes, sha256=outcome.sha256,
        status="ok" if outcome.ok else "error", message=outcome.message,
        content=outcome.content if outcome.ok else None,
    )
    record.id = store.add_backup(record)
    return record


def unchanged_since_last(store: Store, device_id: int, new_sha256: str) -> bool:
    """True if the most recent *successful* backup for this device has the
    same content hash - useful to skip storing no-op backups on a schedule."""
    last = store.latest_backup(device_id)
    return bool(last and last.status == "ok" and last.sha256 == new_sha256)
