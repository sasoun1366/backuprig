"""Kerio Control adapter.

Kerio Control's official "Export configuration" flow is a web-admin action
that produces a `ControlBackup_*.tar.gz` file; there is no supported
unauthenticated shell command that reproduces it exactly. Kerio Control does,
however, expose a root shell over SSH (once enabled in the admin console
under Status > System Health > Enable SSH), and the entire live configuration
lives under `/opt/kerio/winroute` (chief file: `winroute.cfg`, an XML
document - see Kerio's own KB on editing it directly).

This adapter packs that directory into a single base64-encoded tar stream
over the SSH command channel, so the transport layer never needs a
dedicated SFTP path. The result is a `.tar.gz` you can hand back to Kerio
via "Import configuration" for a restore, or just diff over time to see
what changed.
"""

from __future__ import annotations

import base64
import binascii
from typing import Callable

from .base import BackupResult, ConnectionParams, VendorAdapter

DEFAULT_CONFIG_DIR = "/opt/kerio/winroute"


def build_capture_command(config_dir: str = DEFAULT_CONFIG_DIR) -> str:
    """A single shell command that tars up the config dir and base64-encodes
    it to stdout, so it survives a non-interactive SSH exec_command channel
    cleanly (no binary-safety issues)."""
    return f"tar -czf - -C {config_dir} . 2>/dev/null | base64"


def decode_tar_b64(text: str) -> bytes:
    """Decode the base64 blob produced by build_capture_command(), tolerating
    the whitespace/newlines a remote shell will have wrapped it in."""
    cleaned = "".join(text.split())
    if not cleaned:
        raise ValueError("empty capture output")
    return base64.b64decode(cleaned)


class KerioControlAdapter(VendorAdapter):
    key = "kerio-control"
    label = "Kerio Control (SSH shell)"
    default_port = 22
    transport = "ssh"

    def default_filename(self, conn: ConnectionParams) -> str:
        safe_host = conn.host.replace(":", "_").replace("/", "_")
        return f"kerio-control_{safe_host}.tar.gz"

    def fetch(self, conn: ConnectionParams, exec_fn: Callable[[str, float], str],
              timeout: float = 30.0,
              fetch_file_fn=None) -> BackupResult:
        config_dir = conn.options.get("config_dir", DEFAULT_CONFIG_DIR)
        cmd = build_capture_command(config_dir)
        raw = exec_fn(cmd, timeout)
        if not raw or not raw.strip():
            return BackupResult(
                ok=False, filename=self.default_filename(conn),
                message=(
                    f"empty response capturing {config_dir} - is SSH root shell access "
                    "enabled (Status > System Health > Enable SSH) and the path correct?"
                ),
            )
        try:
            content = decode_tar_b64(raw)
        except (ValueError, binascii.Error) as exc:
            return BackupResult(ok=False, filename=self.default_filename(conn),
                                 message=f"failed to decode capture output: {exc}")
        return BackupResult(ok=True, filename=self.default_filename(conn), content=content)
