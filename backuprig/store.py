"""SQLite-backed storage for devices, credentials, and backup history.

Credential secrets (passwords, private keys, enable secrets, API tokens) are
always stored encrypted with the app's master key (see crypto.py). Backup
content itself is stored on disk under a per-device directory, also
encrypted at rest.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .crypto import MasterKey

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value BLOB
);

CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    vendor TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER,
    username TEXT,
    secret_blob BLOB,
    options_json TEXT,
    created_at REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    taken_at REAL NOT NULL,
    filename TEXT NOT NULL,
    size_bytes INTEGER,
    sha256 TEXT,
    status TEXT NOT NULL,
    message TEXT,
    content_blob BLOB
);

CREATE INDEX IF NOT EXISTS idx_backups_device ON backups(device_id, taken_at);
"""


@dataclass
class Device:
    id: Optional[int]
    name: str
    vendor: str
    host: str
    port: Optional[int] = None
    username: Optional[str] = None
    secret: Optional[str] = None  # decrypted password/key/token, in-memory only
    options: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass
class BackupRecord:
    id: Optional[int]
    device_id: int
    taken_at: float
    filename: str
    size_bytes: int
    sha256: str
    status: str  # "ok" | "error"
    message: str = ""
    content: Optional[bytes] = None  # decrypted content, only loaded on demand


class Store:
    """Wraps a SQLite database file. All secret-bearing fields are encrypted
    with the supplied MasterKey before touching disk, and decrypted on the
    way out."""

    def __init__(self, db_path: str | Path, master_key: Optional[MasterKey] = None):
        self.db_path = str(db_path)
        self.master_key = master_key
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- master key bootstrap -------------------------------------------------
    def has_master_key_setup(self) -> bool:
        row = self._conn.execute("SELECT value FROM meta WHERE key='salt'").fetchone()
        return row is not None

    def get_salt_and_verifier(self) -> Optional[tuple]:
        salt_row = self._conn.execute("SELECT value FROM meta WHERE key='salt'").fetchone()
        ver_row = self._conn.execute("SELECT value FROM meta WHERE key='verifier'").fetchone()
        if not salt_row or not ver_row:
            return None
        return bytes(salt_row["value"]), bytes(ver_row["value"])

    def save_salt_and_verifier(self, salt: bytes, verifier: bytes) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('salt', ?)", (salt,)
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('verifier', ?)", (verifier,)
        )
        self._conn.commit()

    def set_master_key(self, master_key: MasterKey) -> None:
        self.master_key = master_key

    def _require_key(self) -> MasterKey:
        if self.master_key is None:
            raise RuntimeError("store is locked: no master key set")
        return self.master_key

    # -- devices ---------------------------------------------------------------
    def add_device(self, device: Device) -> int:
        key = self._require_key()
        secret_blob = key.encrypt_str(device.secret) if device.secret else None
        now = time.time()
        cur = self._conn.execute(
            """INSERT INTO devices
               (name, vendor, host, port, username, secret_blob, options_json,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                device.name, device.vendor, device.host, device.port, device.username,
                secret_blob, json.dumps(device.options), now, now,
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def update_device(self, device: Device) -> None:
        key = self._require_key()
        if not device.id:
            raise ValueError("device.id is required for update")
        secret_blob = key.encrypt_str(device.secret) if device.secret else None
        now = time.time()
        if secret_blob is not None:
            self._conn.execute(
                """UPDATE devices SET name=?, vendor=?, host=?, port=?, username=?,
                   secret_blob=?, options_json=?, updated_at=? WHERE id=?""",
                (device.name, device.vendor, device.host, device.port, device.username,
                 secret_blob, json.dumps(device.options), now, device.id),
            )
        else:
            # keep existing secret if none supplied
            self._conn.execute(
                """UPDATE devices SET name=?, vendor=?, host=?, port=?, username=?,
                   options_json=?, updated_at=? WHERE id=?""",
                (device.name, device.vendor, device.host, device.port, device.username,
                 json.dumps(device.options), now, device.id),
            )
        self._conn.commit()

    def delete_device(self, device_id: int) -> None:
        self._conn.execute("DELETE FROM devices WHERE id=?", (device_id,))
        self._conn.commit()

    def _row_to_device(self, row: sqlite3.Row, reveal_secret: bool) -> Device:
        secret = None
        if reveal_secret and row["secret_blob"] is not None:
            key = self._require_key()
            secret = key.decrypt_str(bytes(row["secret_blob"]))
        return Device(
            id=row["id"], name=row["name"], vendor=row["vendor"], host=row["host"],
            port=row["port"], username=row["username"], secret=secret,
            options=json.loads(row["options_json"] or "{}"),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def list_devices(self, reveal_secrets: bool = False) -> List[Device]:
        rows = self._conn.execute("SELECT * FROM devices ORDER BY name").fetchall()
        return [self._row_to_device(r, reveal_secrets) for r in rows]

    def get_device(self, device_id: int, reveal_secret: bool = True) -> Optional[Device]:
        row = self._conn.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        if not row:
            return None
        return self._row_to_device(row, reveal_secret)

    # -- backups -----------------------------------------------------------
    def add_backup(self, record: BackupRecord) -> int:
        key = self._require_key()
        content_blob = key.encrypt(record.content) if record.content is not None else None
        cur = self._conn.execute(
            """INSERT INTO backups
               (device_id, taken_at, filename, size_bytes, sha256, status, message, content_blob)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (record.device_id, record.taken_at, record.filename, record.size_bytes,
             record.sha256, record.status, record.message, content_blob),
        )
        self._conn.commit()
        return cur.lastrowid

    def _row_to_backup(self, row: sqlite3.Row, load_content: bool) -> BackupRecord:
        content = None
        if load_content and row["content_blob"] is not None:
            key = self._require_key()
            content = key.decrypt(bytes(row["content_blob"]))
        return BackupRecord(
            id=row["id"], device_id=row["device_id"], taken_at=row["taken_at"],
            filename=row["filename"], size_bytes=row["size_bytes"], sha256=row["sha256"],
            status=row["status"], message=row["message"] or "", content=content,
        )

    def list_backups(self, device_id: int, load_content: bool = False) -> List[BackupRecord]:
        rows = self._conn.execute(
            "SELECT * FROM backups WHERE device_id=? ORDER BY taken_at DESC", (device_id,)
        ).fetchall()
        return [self._row_to_backup(r, load_content) for r in rows]

    def get_backup(self, backup_id: int, load_content: bool = True) -> Optional[BackupRecord]:
        row = self._conn.execute("SELECT * FROM backups WHERE id=?", (backup_id,)).fetchone()
        if not row:
            return None
        return self._row_to_backup(row, load_content)

    def delete_backup(self, backup_id: int) -> None:
        self._conn.execute("DELETE FROM backups WHERE id=?", (backup_id,))
        self._conn.commit()

    def latest_backup(self, device_id: int, load_content: bool = False) -> Optional[BackupRecord]:
        row = self._conn.execute(
            "SELECT * FROM backups WHERE device_id=? ORDER BY taken_at DESC LIMIT 1",
            (device_id,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_backup(row, load_content)
