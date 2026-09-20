import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from backuprig.crypto import MasterKey
from backuprig.store import BackupRecord, Device, Store


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "backuprig.db"
    key, salt, verifier = MasterKey.new("test-master-password")
    s = Store(db_path, master_key=key)
    s.save_salt_and_verifier(salt, verifier)
    yield s
    s.close()


def test_add_and_list_device(store):
    dev = Device(id=None, name="core-sw", vendor="cisco-ios", host="10.0.0.1",
                 port=22, username="admin", secret="s3cr3t", options={"enable": "e"})
    dev_id = store.add_device(dev)
    assert dev_id > 0

    devices = store.list_devices(reveal_secrets=False)
    assert len(devices) == 1
    assert devices[0].secret is None  # not revealed by default

    devices_revealed = store.list_devices(reveal_secrets=True)
    assert devices_revealed[0].secret == "s3cr3t"
    assert devices_revealed[0].options == {"enable": "e"}


def test_update_device_keeps_secret_if_not_supplied(store):
    dev = Device(id=None, name="fw1", vendor="kerio-control", host="10.0.0.2",
                 secret="oldpass")
    dev_id = store.add_device(dev)

    fetched = store.get_device(dev_id)
    fetched.name = "fw1-renamed"
    fetched.secret = None  # simulate "leave password unchanged" in UI
    store.update_device(fetched)

    after = store.get_device(dev_id)
    assert after.name == "fw1-renamed"
    assert after.secret == "oldpass"


def test_update_device_replaces_secret_when_supplied(store):
    dev = Device(id=None, name="fw1", vendor="kerio-control", host="10.0.0.2",
                 secret="oldpass")
    dev_id = store.add_device(dev)

    fetched = store.get_device(dev_id)
    fetched.secret = "newpass"
    store.update_device(fetched)

    after = store.get_device(dev_id)
    assert after.secret == "newpass"


def test_delete_device_cascades_backups(store):
    dev = Device(id=None, name="esx1", vendor="esxi", host="10.0.0.3")
    dev_id = store.add_device(dev)
    rec = BackupRecord(id=None, device_id=dev_id, taken_at=time.time(),
                        filename="x.tgz", size_bytes=3, sha256="abc", status="ok",
                        content=b"xyz")
    store.add_backup(rec)
    assert len(store.list_backups(dev_id)) == 1

    store.delete_device(dev_id)
    assert store.list_backups(dev_id) == []


def test_backup_content_roundtrip_and_encrypted_at_rest(store, tmp_path):
    dev = Device(id=None, name="mt1", vendor="mikrotik", host="10.0.0.4")
    dev_id = store.add_device(dev)
    content = b"/ip address\nadd address=10.0.0.4/24 interface=ether1\n"
    rec = BackupRecord(id=None, device_id=dev_id, taken_at=time.time(),
                        filename="mikrotik_10.0.0.4.rsc", size_bytes=len(content),
                        sha256="deadbeef", status="ok", content=content)
    rec_id = store.add_backup(rec)

    loaded = store.get_backup(rec_id, load_content=True)
    assert loaded.content == content

    # Raw DB bytes must not contain the plaintext (encrypted at rest).
    raw = store.db_path
    with open(raw, "rb") as fh:
        blob = fh.read()
    assert b"10.0.0.4/24" not in blob


def test_latest_backup_orders_by_taken_at(store):
    dev = Device(id=None, name="d1", vendor="generic-ssh", host="10.0.0.5")
    dev_id = store.add_device(dev)
    store.add_backup(BackupRecord(id=None, device_id=dev_id, taken_at=100.0,
                                   filename="a", size_bytes=1, sha256="1", status="ok"))
    store.add_backup(BackupRecord(id=None, device_id=dev_id, taken_at=200.0,
                                   filename="b", size_bytes=1, sha256="2", status="ok"))
    latest = store.latest_backup(dev_id)
    assert latest.filename == "b"


def test_store_locked_without_master_key(tmp_path):
    s = Store(tmp_path / "locked.db")
    dev = Device(id=None, name="x", vendor="generic-ssh", host="1.2.3.4")
    with pytest.raises(RuntimeError):
        s.add_device(dev)
    s.close()


def test_has_master_key_setup(tmp_path):
    s = Store(tmp_path / "fresh.db")
    assert s.has_master_key_setup() is False
    key, salt, verifier = MasterKey.new("pw")
    s.save_salt_and_verifier(salt, verifier)
    assert s.has_master_key_setup() is True
    s.close()
