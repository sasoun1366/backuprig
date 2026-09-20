"""VMware ESXi adapter: generates and downloads a host configuration bundle
over SSH, using the same `vim-cmd hostsvc/firmware` flow documented by
VMware/Broadcom for CLI-based host backups.

Flow:
  1. `vim-cmd hostsvc/firmware/sync_config`   - flush pending changes to disk
  2. `vim-cmd hostsvc/firmware/backup_config` - generates the bundle and
     prints a URL like `http://*/downloads/<token>/configBundle-<fqdn>.tgz`
  3. locate the resulting file under /scratch/downloads (or /tmp as a
     fallback on hosts without persistent /scratch) and stream it back
     base64-encoded over the same SSH command channel - no separate SFTP
     session required, avoiding extra ESXi service dependencies.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Callable, Optional

from .base import BackupResult, ConnectionParams, VendorAdapter

_BUNDLE_URL_RE = re.compile(r"downloads/([\w\-\.]+)/(configBundle[\w\-\.]*\.tgz)")


def parse_bundle_path(backup_config_output: str) -> Optional[str]:
    """Given the stdout of `vim-cmd hostsvc/firmware/backup_config`, return the
    on-host filesystem path of the generated bundle, or None if not found."""
    m = _BUNDLE_URL_RE.search(backup_config_output)
    if not m:
        return None
    token, filename = m.group(1), m.group(2)
    return f"/scratch/downloads/{token}/{filename}"


def build_locate_command() -> str:
    """Fallback locator: find the newest configBundle*.tgz anywhere under the
    usual scratch/tmp locations, in case the URL parse above fails (path
    layout has shifted across ESXi releases)."""
    return (
        "ls -t /scratch/downloads/*/configBundle*.tgz /tmp/configBundle*.tgz "
        "2>/dev/null | head -n1"
    )


def build_download_command(remote_path: str) -> str:
    return f"cat {remote_path} | base64"


def decode_b64(text: str) -> bytes:
    cleaned = "".join(text.split())
    if not cleaned:
        raise ValueError("empty download output")
    return base64.b64decode(cleaned)


class ESXiAdapter(VendorAdapter):
    key = "esxi"
    label = "VMware ESXi (SSH host config bundle)"
    default_port = 22
    transport = "ssh"

    def default_filename(self, conn: ConnectionParams) -> str:
        safe_host = conn.host.replace(":", "_").replace("/", "_")
        return f"esxi_{safe_host}_configBundle.tgz"

    def fetch(self, conn: ConnectionParams, exec_fn: Callable[[str, float], str],
              timeout: float = 30.0,
              fetch_file_fn=None) -> BackupResult:
        exec_fn("vim-cmd hostsvc/firmware/sync_config", timeout)
        backup_out = exec_fn("vim-cmd hostsvc/firmware/backup_config", timeout)

        remote_path = parse_bundle_path(backup_out or "")
        if not remote_path:
            located = exec_fn(build_locate_command(), timeout)
            remote_path = (located or "").strip().splitlines()[0].strip() if located else ""
        if not remote_path:
            return BackupResult(
                ok=False, filename=self.default_filename(conn),
                message=(
                    "could not locate the generated configBundle - "
                    f"backup_config output was: {backup_out!r}"
                ),
            )

        raw = exec_fn(build_download_command(remote_path), timeout)
        if not raw or not raw.strip():
            return BackupResult(ok=False, filename=self.default_filename(conn),
                                 message=f"empty response downloading {remote_path}")
        try:
            content = decode_b64(raw)
        except (ValueError, binascii.Error) as exc:
            return BackupResult(ok=False, filename=self.default_filename(conn),
                                 message=f"failed to decode bundle: {exc}")
        return BackupResult(ok=True, filename=self.default_filename(conn), content=content)
