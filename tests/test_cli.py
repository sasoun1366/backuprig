import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import backuprig.cli as cli
import backuprig.engine as engine


class _FakeSession:
    def __init__(self, script):
        self.script = script

    def exec(self, command, timeout=20.0):
        return self.script.get(command, "")

    def close(self):
        pass


@pytest.fixture(autouse=True)
def fake_ssh(monkeypatch):
    """Never touch a real network in CLI tests: patch backup_device."""
    script = {
        "terminal length 0": "",
        "show running-config": "hostname R1\nend\n",
    }

    def fake_backup_device(device, timeout=30.0):
        return engine.run_backup(device, lambda conn: _FakeSession(script))

    monkeypatch.setattr(cli, "backup_and_store", lambda store, dev, timeout=30.0:
                         _real_backup_and_store(store, dev, fake_backup_device, timeout))
    yield


def _real_backup_and_store(store, device, backup_fn, timeout):
    import time
    from backuprig.store import BackupRecord

    outcome = backup_fn(device, timeout=timeout)
    record = BackupRecord(
        id=None, device_id=device.id, taken_at=time.time(), filename=outcome.filename,
        size_bytes=outcome.size_bytes, sha256=outcome.sha256,
        status="ok" if outcome.ok else "error", message=outcome.message,
        content=outcome.content if outcome.ok else None,
    )
    record.id = store.add_backup(record)
    return record


def test_vendors_command(capsys):
    rc = cli.main(["vendors"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "cisco-ios" in out
    assert "mikrotik" in out
    assert "kerio-control" in out
    assert "esxi" in out
    assert "generic-ssh" in out


def test_add_list_backup_history_flow(tmp_path, capsys):
    db = str(tmp_path / "vault.db")
    pw = "test-master-pw"

    rc = cli.main(["--db", db, "--password", pw, "add", "r1", "cisco-ios", "10.0.0.1",
                   "-u", "admin", "--secret", "cisco-pw"])
    assert rc == 0
    out = cli.main
    captured = capsys.readouterr()
    assert "added device #1" in captured.out

    rc = cli.main(["--db", db, "--password", pw, "list"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "r1" in captured.out
    assert "cisco-ios" in captured.out
    assert "never" in captured.out

    rc = cli.main(["--db", db, "--password", pw, "backup", "1"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "[OK]" in captured.out

    rc = cli.main(["--db", db, "--password", pw, "history", "1"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "ok" in captured.out


def test_add_requires_matching_master_password_afterwards(tmp_path, capsys):
    db = str(tmp_path / "vault.db")
    cli.main(["--db", db, "--password", "right-pw", "add", "r1", "cisco-ios", "10.0.0.1",
              "-u", "admin", "--secret", "x"])
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--db", db, "--password", "wrong-pw", "list"])
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "incorrect master password" in captured.err


def test_export_and_diff(tmp_path, capsys):
    db = str(tmp_path / "vault.db")
    pw = "pw"
    cli.main(["--db", db, "--password", pw, "add", "r1", "cisco-ios", "10.0.0.1",
              "-u", "admin", "--secret", "x"])
    capsys.readouterr()
    cli.main(["--db", db, "--password", pw, "backup", "1"])
    capsys.readouterr()

    out_file = tmp_path / "backup.cfg"
    rc = cli.main(["--db", db, "--password", pw, "export", "1", str(out_file)])
    assert rc == 0
    assert out_file.exists()
    assert b"hostname R1" in out_file.read_bytes()


def test_backup_all_flag(tmp_path, capsys):
    db = str(tmp_path / "vault.db")
    pw = "pw"
    cli.main(["--db", db, "--password", pw, "add", "r1", "cisco-ios", "10.0.0.1",
              "-u", "admin", "--secret", "x"])
    cli.main(["--db", db, "--password", pw, "add", "r2", "cisco-ios", "10.0.0.2",
              "-u", "admin", "--secret", "y"])
    capsys.readouterr()

    rc = cli.main(["--db", db, "--password", pw, "backup", "--all"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.count("[OK]") == 2


def test_add_with_key_never_prompts_for_password(tmp_path, capsys, monkeypatch):
    """Regression test: --key should short-circuit the interactive password
    prompt entirely (it used to call getpass.getpass() even with --key set,
    which hangs/raises EOFError in any non-interactive context, e.g. cron)."""
    def _boom(*a, **k):
        raise AssertionError("getpass.getpass() should not be called when --key is set")

    monkeypatch.setattr(cli.getpass, "getpass", _boom)

    db = str(tmp_path / "vault.db")
    rc = cli.main(["--db", db, "--password", "pw", "add", "r1", "generic-ssh", "10.0.0.1",
                   "-u", "admin", "--key", "/path/to/key"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "added device #1" in captured.out


def test_remove_device(tmp_path, capsys):
    db = str(tmp_path / "vault.db")
    pw = "pw"
    cli.main(["--db", db, "--password", pw, "add", "r1", "cisco-ios", "10.0.0.1",
              "-u", "admin", "--secret", "x"])
    capsys.readouterr()

    rc = cli.main(["--db", db, "--password", pw, "remove", "1"])
    assert rc == 0

    cli.main(["--db", db, "--password", pw, "list"])
    out = capsys.readouterr().out
    assert "no devices configured" in out
