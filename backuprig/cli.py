#!/usr/bin/env python3
"""backuprig command-line interface - manage devices and run backups without
the GUI. Designed for cron/systemd-timer automation on Unix systems.

The encrypted vault (SQLite database) is fully shared between the CLI and
the GUI: add a device in one, back it up from the other.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .adapters import list_adapters
from .crypto import MasterKey, WrongMasterPassword
from .diffing import diff_backups
from .engine import backup_and_store
from .store import Device, Store

DEFAULT_DB_PATH = os.environ.get(
    "BACKUPRIG_DB", str(Path.home() / ".backuprig" / "backuprig.db")
)


def _open_store(db_path: str, password: Optional[str]) -> Store:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    store = Store(db_path)
    if not password:
        password = os.environ.get("BACKUPRIG_MASTER_PASSWORD")
    if not store.has_master_key_setup():
        if not password:
            password = getpass.getpass("Set a new master password: ")
            confirm = getpass.getpass("Confirm master password: ")
            if password != confirm:
                print("backuprig: error: passwords do not match", file=sys.stderr)
                sys.exit(1)
        key, salt, verifier = MasterKey.new(password)
        store.save_salt_and_verifier(salt, verifier)
        store.set_master_key(key)
        print(f"backuprig: initialized a new encrypted vault at {db_path}")
        return store

    if not password:
        password = getpass.getpass("Master password: ")
    salt, verifier = store.get_salt_and_verifier()
    try:
        key = MasterKey.unlock(password, salt, verifier)
    except WrongMasterPassword:
        print("backuprig: error: incorrect master password", file=sys.stderr)
        sys.exit(1)
    store.set_master_key(key)
    return store


def cmd_list_vendors(_args: argparse.Namespace) -> int:
    for key, label in list_adapters():
        print(f"{key:16s} {label}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    store = _open_store(args.db, args.password)
    secret = args.secret or os.environ.get("BACKUPRIG_DEVICE_SECRET")
    if not secret and not args.no_secret_prompt and not args.key:
        secret = getpass.getpass("Device password/secret (leave blank if using --key): ")
    options = {}
    if args.command:
        options["command"] = args.command
    if args.config_dir:
        options["config_dir"] = args.config_dir
    if args.show_sensitive:
        options["show_sensitive"] = "true"
    if args.key:
        options["use_key"] = "true"
        secret = args.key

    device = Device(
        id=None, name=args.name, vendor=args.vendor, host=args.host,
        port=args.port, username=args.user, secret=secret or None, options=options,
    )
    dev_id = store.add_device(device)
    print(f"backuprig: added device #{dev_id} '{args.name}' ({args.vendor} @ {args.host})")
    store.close()
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    store = _open_store(args.db, args.password)
    devices = store.list_devices()
    if not devices:
        print("(no devices configured)")
    for d in devices:
        last = store.latest_backup(d.id)
        last_str = "never" if not last else \
            f"{last.status} @ {__import__('datetime').datetime.fromtimestamp(last.taken_at):%Y-%m-%d %H:%M:%S}"
        print(f"#{d.id:<4} {d.name:<20s} {d.vendor:<14s} {d.host:<18s} last backup: {last_str}")
    store.close()
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    store = _open_store(args.db, args.password)
    if args.all:
        devices = store.list_devices(reveal_secrets=True)
    else:
        dev = store.get_device(args.device_id, reveal_secret=True)
        if not dev:
            print(f"backuprig: error: no device with id {args.device_id}", file=sys.stderr)
            return 1
        devices = [dev]

    exit_code = 0
    for dev in devices:
        record = backup_and_store(store, dev, timeout=args.timeout)
        if record.status == "ok":
            print(f"[OK]    #{dev.id} {dev.name}: {record.filename} "
                  f"({record.size_bytes} bytes, sha256={record.sha256[:12]}...)")
        else:
            print(f"[ERROR] #{dev.id} {dev.name}: {record.message}", file=sys.stderr)
            exit_code = 2
    store.close()
    return exit_code


def cmd_history(args: argparse.Namespace) -> int:
    store = _open_store(args.db, args.password)
    backups = store.list_backups(args.device_id)
    if not backups:
        print("(no backups yet)")
    for b in backups:
        import datetime
        ts = datetime.datetime.fromtimestamp(b.taken_at).strftime("%Y-%m-%d %H:%M:%S")
        print(f"#{b.id:<5} {ts}  {b.status:<6s} {b.size_bytes:>8} bytes  {b.filename}")
    store.close()
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    store = _open_store(args.db, args.password)
    record = store.get_backup(args.backup_id, load_content=True)
    if not record or record.content is None:
        print("backuprig: error: backup not found or has no content", file=sys.stderr)
        return 1
    Path(args.output).write_bytes(record.content)
    print(f"backuprig: wrote {len(record.content)} bytes to {args.output}")
    store.close()
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    store = _open_store(args.db, args.password)
    old = store.get_backup(args.old_id, load_content=True)
    new = store.get_backup(args.new_id, load_content=True)
    if not old or not new or old.content is None or new.content is None:
        print("backuprig: error: one or both backups not found / have no content", file=sys.stderr)
        return 1
    print(diff_backups(old.content, new.content, old.filename, new.filename))
    store.close()
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    store = _open_store(args.db, args.password)
    store.delete_device(args.device_id)
    print(f"backuprig: removed device #{args.device_id}")
    store.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="backuprig",
        description="Back up and diff network/virtualization device configs from the command line.",
    )
    p.add_argument("--db", default=DEFAULT_DB_PATH, help=f"vault path (default: {DEFAULT_DB_PATH})")
    p.add_argument("--password", help="master password (or set BACKUPRIG_MASTER_PASSWORD; prompted otherwise)")
    p.add_argument("--version", action="version", version=f"backuprig {__version__}")

    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("vendors", help="list supported vendor adapters")
    sp.set_defaults(func=cmd_list_vendors)

    sp = sub.add_parser("add", help="add a device to the vault")
    sp.add_argument("name")
    sp.add_argument("vendor", help="one of: cisco-ios, mikrotik, kerio-control, esxi, generic-ssh")
    sp.add_argument("host")
    sp.add_argument("-u", "--user", required=True)
    sp.add_argument("-p", "--port", type=int)
    sp.add_argument("--secret", help="password (avoid on shared shells; prefer the prompt)")
    sp.add_argument("--key", help="path to an SSH private key, instead of a password")
    sp.add_argument("--no-secret-prompt", action="store_true",
                     help="don't prompt for a secret (device has none, e.g. agent auth)")
    sp.add_argument("--command", help="[generic-ssh] custom command to run")
    sp.add_argument("--config-dir", help="[kerio-control] override the config directory to archive")
    sp.add_argument("--show-sensitive", action="store_true",
                     help="[mikrotik] use '/export verbose' (may include secrets in plaintext)")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("list", help="list configured devices")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("backup", help="run a backup now")
    grp = sp.add_mutually_exclusive_group(required=True)
    grp.add_argument("device_id", nargs="?", type=int, help="device id to back up")
    grp.add_argument("--all", action="store_true", help="back up every configured device")
    sp.add_argument("-t", "--timeout", type=float, default=30.0)
    sp.set_defaults(func=cmd_backup)

    sp = sub.add_parser("history", help="show backup history for a device")
    sp.add_argument("device_id", type=int)
    sp.set_defaults(func=cmd_history)

    sp = sub.add_parser("export", help="write a stored backup's content to a file")
    sp.add_argument("backup_id", type=int)
    sp.add_argument("output")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("diff", help="diff two stored backups")
    sp.add_argument("old_id", type=int)
    sp.add_argument("new_id", type=int)
    sp.set_defaults(func=cmd_diff)

    sp = sub.add_parser("remove", help="remove a device (and its backup history)")
    sp.add_argument("device_id", type=int)
    sp.set_defaults(func=cmd_remove)

    return p


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
