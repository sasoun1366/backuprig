"""Generic SSH adapter: runs a user-supplied command and stores its raw
stdout. This is the escape hatch for any device with an SSH CLI that isn't
covered by a dedicated adapter (Juniper, Fortinet, HP/Aruba, Linux boxes,
etc) - set `options.command` to whatever prints the configuration you want
backed up (e.g. `show configuration | display set` on Juniper, or
`show full-configuration` on Fortinet).
"""

from __future__ import annotations

from typing import Callable

from .base import BackupResult, ConnectionParams, VendorAdapter

DEFAULT_COMMAND = "cat /etc/os-release 2>/dev/null || uname -a"


class GenericSSHAdapter(VendorAdapter):
    key = "generic-ssh"
    label = "Generic SSH (custom command)"
    default_port = 22
    transport = "ssh"

    def default_filename(self, conn: ConnectionParams) -> str:
        safe_host = conn.host.replace(":", "_").replace("/", "_")
        return f"generic_{safe_host}.txt"

    def fetch(self, conn: ConnectionParams, exec_fn: Callable[[str, float], str],
              timeout: float = 20.0,
              fetch_file_fn=None) -> BackupResult:
        command = (conn.options.get("command") or DEFAULT_COMMAND).strip()
        raw = exec_fn(command, timeout)
        if raw is None or not raw.strip():
            return BackupResult(ok=False, filename=self.default_filename(conn),
                                 message=f"empty response to: {command}")
        return BackupResult(ok=True, filename=self.default_filename(conn),
                             content=raw.encode("utf-8"))
