"""Common types shared by all vendor adapters.

Every adapter exposes a pure, offline-testable `build_commands()` /
`parse_result()` (or equivalent) split from the actual network I/O, so the
adapter's *logic* can be unit tested without a real device. The thin I/O
layer (SSH session, HTTP calls) lives in `runner.py` and is exercised via
integration tests against local fakes, never a real vendor box.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass
class BackupResult:
    ok: bool
    filename: str
    content: bytes = b""
    message: str = ""


@dataclass
class ConnectionParams:
    host: str
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    key_path: Optional[str] = None
    options: Dict[str, str] = field(default_factory=dict)


class VendorAdapter:
    """Base class for a vendor backup adapter.

    Subclasses implement `fetch(conn, exec_fn)` where `exec_fn` is a callable
    `(command: str, timeout: float) -> str` supplied by the transport layer
    (SSH session, etc). This keeps adapters transport-agnostic and makes them
    trivial to test with a fake `exec_fn`.

    Adapters that need to pull a remote file verbatim (e.g. a binary config
    bundle) can set `needs_sftp = True`; the transport layer will then also
    pass a `fetch_file_fn(remote_path: str) -> bytes` callable to `fetch()`.
    """

    key = "generic-ssh"
    label = "Generic SSH (custom command)"
    default_port = 22
    transport = "ssh"  # "ssh" | "http"
    needs_sftp = False

    def default_filename(self, conn: ConnectionParams) -> str:
        safe_host = conn.host.replace(":", "_").replace("/", "_")
        return f"{self.key}_{safe_host}.txt"

    def fetch(self, conn: ConnectionParams, exec_fn: Callable[[str, float], str],
              timeout: float = 20.0,
              fetch_file_fn: Optional[Callable[[str], bytes]] = None) -> BackupResult:
        raise NotImplementedError

