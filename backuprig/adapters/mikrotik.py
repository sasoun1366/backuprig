"""MikroTik RouterOS adapter: fetches a full text export via SSH.

`/export` prints the router's configuration as a RouterOS script (the same
format `rosentinel` and countless RouterOS tools consume), which makes it
both a good backup and something that's directly diffable/restorable by
pasting it back into a terminal.
"""

from __future__ import annotations

from typing import Callable

from .base import BackupResult, ConnectionParams, VendorAdapter


def clean_export(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip() + "\n"


class MikroTikAdapter(VendorAdapter):
    key = "mikrotik"
    label = "MikroTik RouterOS (SSH)"
    default_port = 22
    transport = "ssh"

    def default_filename(self, conn: ConnectionParams) -> str:
        safe_host = conn.host.replace(":", "_").replace("/", "_")
        return f"mikrotik_{safe_host}.rsc"

    def fetch(self, conn: ConnectionParams, exec_fn: Callable[[str, float], str],
              timeout: float = 20.0,
              fetch_file_fn=None) -> BackupResult:
        show_sensitive = conn.options.get("show_sensitive", "false").lower() == "true"
        cmd = "/export verbose" if show_sensitive else "/export"
        raw = exec_fn(cmd, timeout)
        if not raw or not raw.strip():
            return BackupResult(ok=False, filename=self.default_filename(conn),
                                 message=f"empty response to '{cmd}'")
        content = clean_export(raw)
        return BackupResult(ok=True, filename=self.default_filename(conn),
                             content=content.encode("utf-8"))
