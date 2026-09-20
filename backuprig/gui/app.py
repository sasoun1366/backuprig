#!/usr/bin/env python3
"""backuprig desktop GUI - PyQt6 application.

Launch with `backuprig-gui` (after `pip install "backuprig[gui]"`) or
`python -m backuprig.gui.app`.
"""

from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import __version__
from ..adapters import list_adapters
from ..crypto import MasterKey, WrongMasterPassword
from ..diffing import diff_backups
from ..engine import backup_and_store
from ..store import BackupRecord, Device, Store

DEFAULT_DB_PATH = os.environ.get(
    "BACKUPRIG_DB", str(Path.home() / ".backuprig" / "backuprig.db")
)

VENDOR_HINTS = {
    "cisco-ios": "Runs 'show running-config' over SSH.",
    "mikrotik": "Runs '/export' over SSH (check 'verbose' to include secrets).",
    "kerio-control": "Requires SSH enabled in Status > System Health, root login. "
                      "Archives /opt/kerio/winroute by default.",
    "esxi": "Runs the vim-cmd hostsvc/firmware backup flow over SSH.",
    "generic-ssh": "Runs a custom command you specify and stores its output.",
}


# --------------------------------------------------------------------------
# Master password dialogs
# --------------------------------------------------------------------------
class MasterPasswordDialog(QDialog):
    def __init__(self, creating: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create vault password" if creating else "Unlock vault")
        self.creating = creating
        layout = QFormLayout(self)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow("Master password:", self.password_edit)

        self.confirm_edit = None
        if creating:
            self.confirm_edit = QLineEdit()
            self.confirm_edit.setEchoMode(QLineEdit.EchoMode.Password)
            layout.addRow("Confirm password:", self.confirm_edit)
            note = QLabel(
                "This password encrypts every stored credential and backup.\n"
                "There is no recovery if you forget it - store it safely."
            )
            note.setWordWrap(True)
            layout.addRow(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        self.password: Optional[str] = None

    def _on_accept(self) -> None:
        pw = self.password_edit.text()
        if not pw:
            QMessageBox.warning(self, "backuprig", "Password cannot be empty.")
            return
        if self.creating and pw != self.confirm_edit.text():
            QMessageBox.warning(self, "backuprig", "Passwords do not match.")
            return
        self.password = pw
        self.accept()


# --------------------------------------------------------------------------
# Device add/edit dialog
# --------------------------------------------------------------------------
class DeviceDialog(QDialog):
    def __init__(self, device: Optional[Device] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit device" if device else "Add device")
        self.setMinimumWidth(420)
        self._device = device

        layout = QFormLayout(self)

        self.name_edit = QLineEdit(device.name if device else "")
        layout.addRow("Name:", self.name_edit)

        self.vendor_combo = QComboBox()
        for key, label in list_adapters():
            self.vendor_combo.addItem(label, userData=key)
        if device:
            idx = self.vendor_combo.findData(device.vendor)
            if idx >= 0:
                self.vendor_combo.setCurrentIndex(idx)
        self.vendor_combo.currentIndexChanged.connect(self._update_hint)
        layout.addRow("Vendor:", self.vendor_combo)

        self.hint_label = QLabel()
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addRow(self.hint_label)

        self.host_edit = QLineEdit(device.host if device else "")
        layout.addRow("Host / IP:", self.host_edit)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(0, 65535)
        self.port_spin.setValue(device.port if device and device.port else 22)
        layout.addRow("Port:", self.port_spin)

        self.user_edit = QLineEdit(device.username if device else "")
        layout.addRow("Username:", self.user_edit)

        self.secret_edit = QLineEdit()
        self.secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        placeholder = "(leave blank to keep existing)" if device else ""
        self.secret_edit.setPlaceholderText(placeholder)
        layout.addRow("Password:", self.secret_edit)

        self.key_edit = QLineEdit(
            device.options.get("key_path", "") if device and device.options.get("use_key") == "true" else ""
        )
        key_browse = QPushButton("Browse...")
        key_browse.clicked.connect(self._browse_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit)
        key_row.addWidget(key_browse)
        layout.addRow("SSH key (optional):", key_row)

        # vendor-specific extras
        self.command_edit = QLineEdit(device.options.get("command", "") if device else "")
        layout.addRow("Custom command (generic-ssh):", self.command_edit)

        self.config_dir_edit = QLineEdit(device.options.get("config_dir", "") if device else "")
        self.config_dir_edit.setPlaceholderText("/opt/kerio/winroute (default)")
        layout.addRow("Config dir (kerio-control):", self.config_dir_edit)

        self.verbose_combo = QComboBox()
        self.verbose_combo.addItems(["no", "yes"])
        if device and device.options.get("show_sensitive") == "true":
            self.verbose_combo.setCurrentText("yes")
        layout.addRow("Verbose export (mikrotik, may include secrets):", self.verbose_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self._update_hint()

    def _browse_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select SSH private key")
        if path:
            self.key_edit.setText(path)

    def _update_hint(self) -> None:
        key = self.vendor_combo.currentData()
        self.hint_label.setText(VENDOR_HINTS.get(key, ""))

    def result_device(self, existing_id: Optional[int]) -> Device:
        options = {}
        if self.command_edit.text().strip():
            options["command"] = self.command_edit.text().strip()
        if self.config_dir_edit.text().strip():
            options["config_dir"] = self.config_dir_edit.text().strip()
        if self.verbose_combo.currentText() == "yes":
            options["show_sensitive"] = "true"

        key_path = self.key_edit.text().strip()
        secret = self.secret_edit.text()
        if key_path:
            options["use_key"] = "true"
            secret = key_path  # secret column stores the key path in this mode

        return Device(
            id=existing_id,
            name=self.name_edit.text().strip(),
            vendor=self.vendor_combo.currentData(),
            host=self.host_edit.text().strip(),
            port=self.port_spin.value(),
            username=self.user_edit.text().strip(),
            secret=secret or None,
            options=options,
        )


# --------------------------------------------------------------------------
# Background backup worker (keeps the UI responsive)
# --------------------------------------------------------------------------
class BackupWorker(QThread):
    finished_one = pyqtSignal(int, bool, str)  # device_id, ok, message

    def __init__(self, store: Store, devices: list, timeout: float = 30.0):
        super().__init__()
        self.store = store
        self.devices = devices
        self.timeout = timeout

    def run(self) -> None:
        for dev in self.devices:
            try:
                record: BackupRecord = backup_and_store(self.store, dev, timeout=self.timeout)
                self.finished_one.emit(dev.id, record.status == "ok", record.message or "OK")
            except Exception as exc:  # keep going even if one device blows up
                self.finished_one.emit(dev.id, False, str(exc))


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        self.setWindowTitle(f"backuprig {__version__}")
        self.resize(1000, 620)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        # --- left: devices ---
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("<b>Devices</b>"))

        self.device_table = QTableWidget(0, 4)
        self.device_table.setHorizontalHeaderLabels(["Name", "Vendor", "Host", "Last backup"])
        self.device_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.device_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.device_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.device_table.itemSelectionChanged.connect(self._refresh_history)
        left_layout.addWidget(self.device_table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add device")
        add_btn.clicked.connect(self._add_device)
        edit_btn = QPushButton("Edit")
        edit_btn.clicked.connect(self._edit_device)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove_device)
        backup_btn = QPushButton("Backup now")
        backup_btn.clicked.connect(self._backup_selected)
        backup_all_btn = QPushButton("Backup all")
        backup_all_btn.clicked.connect(self._backup_all)
        for b in (add_btn, edit_btn, remove_btn, backup_btn, backup_all_btn):
            btn_row.addWidget(b)
        left_layout.addLayout(btn_row)

        splitter.addWidget(left)

        # --- right: backup history + viewer ---
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("<b>Backup history</b>"))

        self.history_table = QTableWidget(0, 4)
        self.history_table.setHorizontalHeaderLabels(["Taken", "Status", "Size", "SHA-256"])
        self.history_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.history_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        right_layout.addWidget(self.history_table)

        hist_btn_row = QHBoxLayout()
        view_btn = QPushButton("View")
        view_btn.clicked.connect(self._view_backup)
        export_btn = QPushButton("Export to file...")
        export_btn.clicked.connect(self._export_backup)
        diff_btn = QPushButton("Diff selected two")
        diff_btn.clicked.connect(self._diff_selected)
        for b in (view_btn, export_btn, diff_btn):
            hist_btn_row.addWidget(b)
        right_layout.addLayout(hist_btn_row)

        self.status_label = QLabel("Ready.")
        right_layout.addWidget(self.status_label)

        splitter.addWidget(right)
        splitter.setSizes([420, 580])

        self._reload_devices()

    # -- helpers -------------------------------------------------------
    def _selected_device_id(self) -> Optional[int]:
        rows = self.device_table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.device_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)

    def _reload_devices(self) -> None:
        devices = self.store.list_devices()
        self.device_table.setRowCount(len(devices))
        for row, d in enumerate(devices):
            last = self.store.latest_backup(d.id)
            last_str = "never"
            if last:
                ts = datetime.datetime.fromtimestamp(last.taken_at).strftime("%Y-%m-%d %H:%M:%S")
                last_str = f"{last.status} @ {ts}"
            name_item = QTableWidgetItem(d.name)
            name_item.setData(Qt.ItemDataRole.UserRole, d.id)
            self.device_table.setItem(row, 0, name_item)
            self.device_table.setItem(row, 1, QTableWidgetItem(d.vendor))
            self.device_table.setItem(row, 2, QTableWidgetItem(d.host))
            self.device_table.setItem(row, 3, QTableWidgetItem(last_str))
        self._refresh_history()

    def _refresh_history(self) -> None:
        dev_id = self._selected_device_id()
        self.history_table.setRowCount(0)
        if dev_id is None:
            return
        backups = self.store.list_backups(dev_id)
        self.history_table.setRowCount(len(backups))
        for row, b in enumerate(backups):
            ts = datetime.datetime.fromtimestamp(b.taken_at).strftime("%Y-%m-%d %H:%M:%S")
            ts_item = QTableWidgetItem(ts)
            ts_item.setData(Qt.ItemDataRole.UserRole, b.id)
            self.history_table.setItem(row, 0, ts_item)
            status_item = QTableWidgetItem(b.status if b.status == "ok" else f"error: {b.message}")
            self.history_table.setItem(row, 1, status_item)
            self.history_table.setItem(row, 2, QTableWidgetItem(str(b.size_bytes or 0)))
            self.history_table.setItem(row, 3, QTableWidgetItem((b.sha256 or "")[:16]))

    # -- device actions ---------------------------------------------------
    def _add_device(self) -> None:
        dlg = DeviceDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            device = dlg.result_device(existing_id=None)
            if not device.name or not device.host:
                QMessageBox.warning(self, "backuprig", "Name and host are required.")
                return
            self.store.add_device(device)
            self._reload_devices()

    def _edit_device(self) -> None:
        dev_id = self._selected_device_id()
        if dev_id is None:
            return
        device = self.store.get_device(dev_id, reveal_secret=False)
        dlg = DeviceDialog(device=device, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            updated = dlg.result_device(existing_id=dev_id)
            self.store.update_device(updated)
            self._reload_devices()

    def _remove_device(self) -> None:
        dev_id = self._selected_device_id()
        if dev_id is None:
            return
        if QMessageBox.question(
            self, "backuprig", "Remove this device and all of its backup history?"
        ) == QMessageBox.StandardButton.Yes:
            self.store.delete_device(dev_id)
            self._reload_devices()

    def _run_backup_worker(self, devices: list) -> None:
        if not devices:
            return
        self.status_label.setText(f"Backing up {len(devices)} device(s)...")
        self.worker = BackupWorker(self.store, devices)
        self.worker.finished_one.connect(self._on_backup_finished)
        self.worker.start()

    def _on_backup_finished(self, device_id: int, ok: bool, message: str) -> None:
        self.status_label.setText(
            f"Device #{device_id}: {'OK' if ok else 'FAILED - ' + message}"
        )
        self._reload_devices()

    def _backup_selected(self) -> None:
        dev_id = self._selected_device_id()
        if dev_id is None:
            QMessageBox.information(self, "backuprig", "Select a device first.")
            return
        device = self.store.get_device(dev_id, reveal_secret=True)
        self._run_backup_worker([device])

    def _backup_all(self) -> None:
        devices = self.store.list_devices(reveal_secrets=True)
        self._run_backup_worker(devices)

    # -- backup viewer/export/diff ----------------------------------------
    def _selected_backup_ids(self) -> list:
        rows = self.history_table.selectionModel().selectedRows()
        return [self.history_table.item(r.row(), 0).data(Qt.ItemDataRole.UserRole) for r in rows]

    def _view_backup(self) -> None:
        ids = self._selected_backup_ids()
        if not ids:
            return
        record = self.store.get_backup(ids[0], load_content=True)
        if not record or record.content is None:
            QMessageBox.information(self, "backuprig", "This backup has no content (it failed).")
            return
        self._show_text_dialog(record.filename, self._preview_text(record.content))

    def _preview_text(self, content: bytes) -> str:
        from .. import diffing

        if diffing.is_probably_text(content):
            return content.decode("utf-8", errors="replace")
        members = diffing.list_tar_members(content)
        if members:
            lines = [f"(binary archive, {len(content)} bytes - showing file list)", ""]
            lines += [f"  {m.name}  ({m.size} bytes)" for m in members]
            return "\n".join(lines)
        return f"(binary content, {len(content)} bytes - not previewable as text)"

    def _show_text_dialog(self, title: str, text: str) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(700, 500)
        layout = QVBoxLayout(dlg)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(text)
        editor.setFontFamily("monospace")
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)
        dlg.exec()

    def _export_backup(self) -> None:
        ids = self._selected_backup_ids()
        if not ids:
            return
        record = self.store.get_backup(ids[0], load_content=True)
        if not record or record.content is None:
            QMessageBox.information(self, "backuprig", "This backup has no content (it failed).")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export backup", record.filename)
        if path:
            Path(path).write_bytes(record.content)
            self.status_label.setText(f"Exported to {path}")

    def _diff_selected(self) -> None:
        ids = self._selected_backup_ids()
        if len(ids) != 2:
            QMessageBox.information(self, "backuprig", "Select exactly two backups (ctrl-click) to diff.")
            return
        # order oldest -> newest for a sensible diff direction
        recs = sorted(
            [self.store.get_backup(i, load_content=True) for i in ids],
            key=lambda r: r.taken_at,
        )
        old, new = recs
        if old.content is None or new.content is None:
            QMessageBox.information(self, "backuprig", "Both selected backups must have content.")
            return
        result = diff_backups(old.content, new.content, old.filename, new.filename)
        self._show_text_dialog(f"Diff: {old.filename} -> {new.filename}", result)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def _unlock_or_create_store(db_path: str) -> Optional[Store]:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    store = Store(db_path)
    creating = not store.has_master_key_setup()
    dlg = MasterPasswordDialog(creating=creating)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None

    if creating:
        key, salt, verifier = MasterKey.new(dlg.password)
        store.save_salt_and_verifier(salt, verifier)
        store.set_master_key(key)
        return store

    salt, verifier = store.get_salt_and_verifier()
    try:
        key = MasterKey.unlock(dlg.password, salt, verifier)
    except WrongMasterPassword:
        QMessageBox.critical(None, "backuprig", "Incorrect master password.")
        return None
    store.set_master_key(key)
    return store


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("backuprig")

    db_path = DEFAULT_DB_PATH
    store = _unlock_or_create_store(db_path)
    if store is None:
        return 1

    window = MainWindow(store)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
