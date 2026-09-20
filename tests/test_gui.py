"""GUI smoke tests. Require PyQt6 and run with QT_QPA_PLATFORM=offscreen in CI
(no real display needed). These check that windows/dialogs construct and
basic interactions work - they do not attempt real network connections.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication, QDialog

from backuprig.crypto import MasterKey
from backuprig.gui.app import DeviceDialog, MainWindow, MasterPasswordDialog
from backuprig.store import Device, Store


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def store(tmp_path):
    key, salt, verifier = MasterKey.new("pw")
    s = Store(tmp_path / "gui.db", master_key=key)
    s.save_salt_and_verifier(salt, verifier)
    yield s
    s.close()


def test_main_window_constructs_and_lists_devices(qapp, store):
    dev = Device(id=None, name="r1", vendor="cisco-ios", host="10.0.0.1",
                 username="admin", secret="pw")
    store.add_device(dev)

    win = MainWindow(store)
    assert win.device_table.rowCount() == 1
    assert win.device_table.item(0, 0).text() == "r1"
    assert win.device_table.item(0, 1).text() == "cisco-ios"


def test_main_window_history_updates_on_selection(qapp, store):
    import time
    from backuprig.store import BackupRecord

    dev = Device(id=None, name="r1", vendor="mikrotik", host="10.0.0.2")
    dev_id = store.add_device(dev)
    store.add_backup(BackupRecord(id=None, device_id=dev_id, taken_at=time.time(),
                                   filename="f.rsc", size_bytes=5, sha256="abc",
                                   status="ok", content=b"hello"))

    win = MainWindow(store)
    win.device_table.selectRow(0)
    win._refresh_history()
    assert win.history_table.rowCount() == 1


def test_device_dialog_vendor_dropdown_populated(qapp):
    dlg = DeviceDialog()
    assert dlg.vendor_combo.count() == 5
    keys = [dlg.vendor_combo.itemData(i) for i in range(dlg.vendor_combo.count())]
    assert set(keys) == {"cisco-ios", "mikrotik", "kerio-control", "esxi", "generic-ssh"}


def test_device_dialog_prefills_from_existing_device(qapp):
    dev = Device(id=1, name="fw1", vendor="kerio-control", host="10.0.0.9",
                 port=22, username="root", options={"config_dir": "/opt/kerio/winroute"})
    dlg = DeviceDialog(device=dev)
    assert dlg.name_edit.text() == "fw1"
    assert dlg.host_edit.text() == "10.0.0.9"
    assert dlg.config_dir_edit.text() == "/opt/kerio/winroute"


def test_device_dialog_result_device_builds_options(qapp):
    dlg = DeviceDialog()
    dlg.name_edit.setText("gsw1")
    dlg.host_edit.setText("10.0.0.20")
    dlg.user_edit.setText("admin")
    dlg.command_edit.setText("show configuration | display set")
    device = dlg.result_device(existing_id=None)
    assert device.name == "gsw1"
    assert device.options["command"] == "show configuration | display set"


def test_device_dialog_key_path_sets_use_key_option(qapp):
    dlg = DeviceDialog()
    dlg.name_edit.setText("d1")
    dlg.host_edit.setText("10.0.0.1")
    dlg.key_edit.setText("/home/user/.ssh/id_ed25519")
    device = dlg.result_device(existing_id=None)
    assert device.options.get("use_key") == "true"
    assert device.secret == "/home/user/.ssh/id_ed25519"


def test_master_password_dialog_mismatch_does_not_accept(qapp, monkeypatch):
    # QMessageBox.warning() is a blocking modal even under the offscreen
    # platform; stub it out so the test can assert on the validation
    # behavior without hanging waiting for a user click.
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)

    dlg = MasterPasswordDialog(creating=True)
    dlg.password_edit.setText("abc")
    dlg.confirm_edit.setText("xyz")
    dlg._on_accept()
    assert dlg.password is None  # mismatched passwords must not be accepted


def test_master_password_dialog_matching_accepts(qapp):
    dlg = MasterPasswordDialog(creating=True)
    dlg.password_edit.setText("abc123")
    dlg.confirm_edit.setText("abc123")
    dlg._on_accept()
    assert dlg.password == "abc123"


def test_preview_text_for_plain_text_backup(qapp, store):
    win = MainWindow(store)
    text = win._preview_text(b"hostname R1\ninterface Gi0/1\n")
    assert "hostname R1" in text


def test_preview_text_for_tar_archive(qapp, store):
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("winroute.cfg")
        data = b"<config/>"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))

    win = MainWindow(store)
    text = win._preview_text(buf.getvalue())
    assert "winroute.cfg" in text
