"""Cisco IOS / IOS-XE adapter: fetches `show running-config` over SSH.

Cisco devices with pagination enabled will interleave "--More--" prompts
into the output on interactive sessions; running `terminal length 0` first
(when the transport is a real interactive shell) avoids that. When using a
plain `exec_command`-style channel (as most SSH libraries provide), each
command runs in its own non-interactive session so pagination doesn't apply,
but we still send it defensively for terminal-server-style setups.
"""

from __future__ import annotations

import re
from typing import Callable

from .base import BackupResult, ConnectionParams, VendorAdapter


def clean_running_config(raw: str) -> str:
    """Strip Cisco CLI cruft: '--More--' pagination markers, ANSI backspace
    sequences used to erase them, and the command echo itself if present."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    # Remove "--More--" plus the backspace/space sequence terminals use to
    # erase it, in whatever encoding it survived transport as.
    text = re.sub(r"[\x08 ]*--More--[\x08 ]*", "", text)
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)  # stray ANSI sequences
    lines = [ln for ln in text.split("\n") if not ln.strip().startswith("Building configuration")]
    return "\n".join(lines).strip() + "\n"


class CiscoIOSAdapter(VendorAdapter):
    key = "cisco-ios"
    label = "Cisco IOS / IOS-XE (SSH)"
    default_port = 22
    transport = "ssh"

    def default_filename(self, conn: ConnectionParams) -> str:
        safe_host = conn.host.replace(":", "_").replace("/", "_")
        return f"cisco-ios_{safe_host}.cfg"

    def fetch(self, conn: ConnectionParams, exec_fn: Callable[[str, float], str],
              timeout: float = 20.0,
              fetch_file_fn=None) -> BackupResult:
        exec_fn("terminal length 0", timeout)  # best-effort; harmless if ignored
        raw = exec_fn("show running-config", timeout)
        if not raw or not raw.strip():
            return BackupResult(ok=False, filename=self.default_filename(conn),
                                 message="empty response to 'show running-config'")
        content = clean_running_config(raw)
        return BackupResult(ok=True, filename=self.default_filename(conn),
                             content=content.encode("utf-8"))
