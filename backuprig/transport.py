"""Thin SSH transport used by the adapters. Kept intentionally small and
separate from adapter logic so it's the only piece that needs a real network
socket - everything else is unit-testable with a fake exec_fn.
"""

from __future__ import annotations

from typing import Optional

try:
    import paramiko

    PARAMIKO_AVAILABLE = True
except ImportError:  # pragma: no cover
    PARAMIKO_AVAILABLE = False

from .adapters.base import ConnectionParams


class SSHSession:
    """A connected SSH session exposing a simple exec(command, timeout) -> str
    interface, suitable for passing as `exec_fn` to a VendorAdapter."""

    def __init__(self, client: "paramiko.SSHClient"):
        self._client = client

    @classmethod
    def connect(cls, conn: ConnectionParams, connect_timeout: float = 10.0) -> "SSHSession":
        if not PARAMIKO_AVAILABLE:
            raise RuntimeError(
                "paramiko is required for SSH connections. Install with: "
                "pip install \"backuprig[ssh]\""
            )
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = dict(
            hostname=conn.host,
            port=conn.port or 22,
            username=conn.username,
            timeout=connect_timeout,
            banner_timeout=connect_timeout,
            auth_timeout=connect_timeout,
            look_for_keys=bool(conn.key_path),
            allow_agent=False,
        )
        if conn.key_path:
            kwargs["key_filename"] = conn.key_path
        else:
            kwargs["password"] = conn.password
        client.connect(**kwargs)
        return cls(client)

    def exec(self, command: str, timeout: float = 20.0) -> str:
        _stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        if not out.strip():
            err = stderr.read().decode("utf-8", errors="replace")
            if err.strip():
                return ""  # adapters treat empty as failure and surface their own message
        return out

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def __enter__(self) -> "SSHSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
