import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from backuprig.crypto import MasterKey
from backuprig.engine import backup_and_store, run_backup, unchanged_since_last
from backuprig.store import Device, Store


class _FakeSession:
    """A fake transport session standing in for transport.SSHSession."""

    def __init__(self, script, fail_connect=False):
        if fail_connect:
            raise ConnectionError("simulated connection failure")
        self.script = script
        self.closed = False

    def exec(self, command, timeout=20.0):
        return self.script.get(command, "")

    def close(self):
        self.closed = True


def _session_factory(script, fail_connect=False):
    return lambda conn: _FakeSession(script, fail_connect=fail_connect)


def test_run_backup_success_cisco():
    device = Device(id=1, name="r1", vendor="cisco-ios", host="10.0.0.1",
                     username="admin", secret="pw")
    script = {
        "terminal length 0": "",
        "show running-config": "hostname R1\ninterface Gi0/1\nend\n",
    }
    outcome = run_backup(device, _session_factory(script))
    assert outcome.ok
    assert b"hostname R1" in outcome.content
    assert outcome.sha256 != ""
    assert outcome.size_bytes > 0


def test_run_backup_transport_error_is_captured():
    device = Device(id=1, name="r1", vendor="cisco-ios", host="10.0.0.1")

    def factory(conn):
        raise TimeoutError("no route to host")

    outcome = run_backup(device, factory)
    assert not outcome.ok
    assert "no route to host" in outcome.message


def test_run_backup_session_closed_even_on_adapter_failure():
    device = Device(id=1, name="r1", vendor="mikrotik", host="10.0.0.2")
    script = {"/export": ""}  # empty -> adapter reports failure
    session_holder = {}

    def factory(conn):
        s = _FakeSession(script)
        session_holder["s"] = s
        return s

    outcome = run_backup(device, factory)
    assert not outcome.ok
    assert session_holder["s"].closed


@pytest.fixture
def store(tmp_path):
    key, salt, verifier = MasterKey.new("pw")
    s = Store(tmp_path / "db.sqlite", master_key=key)
    s.save_salt_and_verifier(salt, verifier)
    yield s
    s.close()


def test_backup_and_store_persists_success(monkeypatch, store):
    device = Device(id=None, name="r1", vendor="cisco-ios", host="10.0.0.1", secret="pw")
    dev_id = store.add_device(device)
    device.id = dev_id

    script = {
        "terminal length 0": "",
        "show running-config": "hostname R1\nend\n",
    }
    monkeypatch.setattr(
        "backuprig.engine.backup_device",
        lambda dev, timeout=30.0: run_backup(dev, _session_factory(script)),
    )

    record = backup_and_store(store, device)
    assert record.status == "ok"
    assert record.id is not None

    loaded = store.get_backup(record.id)
    assert b"hostname R1" in loaded.content


def test_backup_and_store_persists_failure_without_content(monkeypatch, store):
    device = Device(id=None, name="r1", vendor="cisco-ios", host="10.0.0.1", secret="pw")
    dev_id = store.add_device(device)
    device.id = dev_id

    monkeypatch.setattr(
        "backuprig.engine.backup_device",
        lambda dev, timeout=30.0: run_backup(dev, _session_factory({}, fail_connect=True)),
    )

    record = backup_and_store(store, device)
    assert record.status == "error"
    assert record.content is None


def test_unchanged_since_last(monkeypatch, store):
    device = Device(id=None, name="mt", vendor="mikrotik", host="10.0.0.3")
    dev_id = store.add_device(device)
    device.id = dev_id

    script = {"/export": "/ip address\nadd address=1.1.1.1/24\n"}
    monkeypatch.setattr(
        "backuprig.engine.backup_device",
        lambda dev, timeout=30.0: run_backup(dev, _session_factory(script)),
    )

    # Before any backup exists, nothing to compare against.
    assert unchanged_since_last(store, dev_id, "somehash") is False

    rec1 = backup_and_store(store, device)

    # A fresh fetch with the *same* content hash as the last stored backup
    # should be reported as unchanged...
    assert unchanged_since_last(store, dev_id, rec1.sha256) is True
    # ...while a different hash should not be.
    assert unchanged_since_last(store, dev_id, "different-hash") is False
