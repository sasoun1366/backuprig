import base64
import io
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backuprig.adapters import ADAPTERS, get_adapter, list_adapters
from backuprig.adapters.base import ConnectionParams
from backuprig.adapters.cisco_ios import CiscoIOSAdapter, clean_running_config
from backuprig.adapters.esxi import (
    ESXiAdapter, build_download_command, build_locate_command, decode_b64,
    parse_bundle_path,
)
from backuprig.adapters.generic_ssh import GenericSSHAdapter
from backuprig.adapters.kerio import (
    KerioControlAdapter, build_capture_command, decode_tar_b64,
)
from backuprig.adapters.mikrotik import MikroTikAdapter, clean_export


def _conn(**kw):
    base = dict(host="192.168.1.1", port=22, username="admin", password="pw")
    base.update(kw)
    return ConnectionParams(**base)


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------
def test_registry_has_all_expected_vendors():
    assert set(ADAPTERS) == {"cisco-ios", "mikrotik", "kerio-control", "esxi", "generic-ssh"}


def test_get_adapter_unknown_raises():
    try:
        get_adapter("does-not-exist")
        assert False
    except ValueError:
        pass


def test_list_adapters_sorted_by_label():
    items = list_adapters()
    labels = [label for _key, label in items]
    assert labels == sorted(labels)


# --------------------------------------------------------------------------
# Cisco IOS
# --------------------------------------------------------------------------
def test_clean_running_config_strips_more_prompt():
    raw = "Building configuration...\n\nhostname R1\n--More--        \ninterface Gi0/1\n"
    cleaned = clean_running_config(raw)
    assert "Building configuration" not in cleaned
    assert "--More--" not in cleaned
    assert "hostname R1" in cleaned
    assert "interface Gi0/1" in cleaned


def test_cisco_fetch_success():
    adapter = CiscoIOSAdapter()
    calls = []

    def exec_fn(cmd, timeout):
        calls.append(cmd)
        if cmd == "show running-config":
            return "hostname R1\ninterface Gi0/1\n no shutdown\nend\n"
        return ""

    result = adapter.fetch(_conn(), exec_fn)
    assert result.ok
    assert b"hostname R1" in result.content
    assert result.filename == "cisco-ios_192.168.1.1.cfg"
    assert "terminal length 0" in calls


def test_cisco_fetch_empty_response_is_failure():
    adapter = CiscoIOSAdapter()
    result = adapter.fetch(_conn(), lambda cmd, timeout: "")
    assert not result.ok
    assert "empty" in result.message


# --------------------------------------------------------------------------
# MikroTik
# --------------------------------------------------------------------------
def test_clean_export_normalizes_newlines():
    raw = "/ip address\r\nadd address=1.2.3.4/24\r\n"
    cleaned = clean_export(raw)
    assert "\r" not in cleaned
    assert cleaned.endswith("\n")


def test_mikrotik_fetch_default_export():
    adapter = MikroTikAdapter()
    seen = {}

    def exec_fn(cmd, timeout):
        seen["cmd"] = cmd
        return "/ip address\nadd address=10.0.0.1/24 interface=ether1\n"

    result = adapter.fetch(_conn(), exec_fn)
    assert result.ok
    assert seen["cmd"] == "/export"
    assert result.filename.endswith(".rsc")


def test_mikrotik_fetch_verbose_when_requested():
    adapter = MikroTikAdapter()
    seen = {}

    def exec_fn(cmd, timeout):
        seen["cmd"] = cmd
        return "/ip address\nadd address=10.0.0.1/24 interface=ether1\n"

    conn = _conn(options={"show_sensitive": "true"})
    adapter.fetch(conn, exec_fn)
    assert seen["cmd"] == "/export verbose"


def test_mikrotik_empty_export_is_failure():
    adapter = MikroTikAdapter()
    result = adapter.fetch(_conn(), lambda cmd, timeout: "   ")
    assert not result.ok


# --------------------------------------------------------------------------
# Kerio Control
# --------------------------------------------------------------------------
def _make_tar_gz(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_build_capture_command_uses_config_dir():
    cmd = build_capture_command("/opt/kerio/winroute")
    assert "/opt/kerio/winroute" in cmd
    assert "base64" in cmd


def test_decode_tar_b64_roundtrip():
    tar_bytes = _make_tar_gz({"winroute.cfg": b"<config/>"})
    encoded = base64.b64encode(tar_bytes).decode("ascii")
    # simulate a remote shell wrapping the output with newlines every 76 chars
    wrapped = "\n".join(encoded[i:i + 76] for i in range(0, len(encoded), 76))
    decoded = decode_tar_b64(wrapped)
    assert decoded == tar_bytes


def test_decode_tar_b64_empty_raises():
    try:
        decode_tar_b64("   \n  ")
        assert False
    except ValueError:
        pass


def test_kerio_fetch_success():
    adapter = KerioControlAdapter()
    tar_bytes = _make_tar_gz({"winroute.cfg": b"<config/>"})
    encoded = base64.b64encode(tar_bytes).decode("ascii")

    def exec_fn(cmd, timeout):
        assert "tar -czf" in cmd
        return encoded

    result = adapter.fetch(_conn(), exec_fn)
    assert result.ok
    assert result.content == tar_bytes
    assert result.filename.endswith(".tar.gz")


def test_kerio_fetch_custom_config_dir():
    adapter = KerioControlAdapter()
    seen = {}

    def exec_fn(cmd, timeout):
        seen["cmd"] = cmd
        return base64.b64encode(_make_tar_gz({"a": b"1"})).decode("ascii")

    conn = _conn(options={"config_dir": "/custom/path"})
    adapter.fetch(conn, exec_fn)
    assert "/custom/path" in seen["cmd"]


def test_kerio_fetch_empty_is_failure_with_hint():
    adapter = KerioControlAdapter()
    result = adapter.fetch(_conn(), lambda cmd, timeout: "")
    assert not result.ok
    assert "SSH" in result.message


# --------------------------------------------------------------------------
# ESXi
# --------------------------------------------------------------------------
def test_parse_bundle_path_from_typical_output():
    out = (
        "Content-Length: 12345\r\n\r\n"
        "Bundle can be downloaded at : "
        "http://*/downloads/9f1c2a/configBundle-esxi01.local.tgz"
    )
    path = parse_bundle_path(out)
    assert path == "/scratch/downloads/9f1c2a/configBundle-esxi01.local.tgz"


def test_parse_bundle_path_none_when_absent():
    assert parse_bundle_path("no url here") is None


def test_build_locate_command_and_download_command():
    locate = build_locate_command()
    assert "configBundle" in locate
    dl = build_download_command("/scratch/downloads/x/configBundle.tgz")
    assert dl.startswith("cat ")
    assert "base64" in dl


def test_decode_b64_roundtrip():
    data = b"some binary-ish \x00\x01 data"
    encoded = base64.b64encode(data).decode("ascii")
    assert decode_b64(encoded) == data


def test_esxi_fetch_full_flow_with_url_parse():
    adapter = ESXiAdapter()
    tgz = _make_tar_gz({"local.tgz/state.tgz": b"dummy"})
    b64 = base64.b64encode(tgz).decode("ascii")
    calls = []

    def exec_fn(cmd, timeout):
        calls.append(cmd)
        if "sync_config" in cmd:
            return "OK"
        if "backup_config" in cmd:
            return "Bundle can be downloaded at : http://*/downloads/abc123/configBundle-h.tgz"
        if cmd.startswith("cat "):
            assert cmd == "cat /scratch/downloads/abc123/configBundle-h.tgz | base64"
            return b64
        return ""

    result = adapter.fetch(_conn(), exec_fn)
    assert result.ok
    assert result.content == tgz
    assert any("sync_config" in c for c in calls)


def test_esxi_fetch_falls_back_to_locate_when_url_unparseable():
    adapter = ESXiAdapter()
    tgz = _make_tar_gz({"x": b"1"})
    b64 = base64.b64encode(tgz).decode("ascii")

    def exec_fn(cmd, timeout):
        if "sync_config" in cmd:
            return "OK"
        if "backup_config" in cmd:
            return "some unexpected output with no url"
        if cmd.startswith("ls -t"):
            return "/scratch/downloads/zzz/configBundle-h.tgz\n"
        if cmd.startswith("cat "):
            return b64
        return ""

    result = adapter.fetch(_conn(), exec_fn)
    assert result.ok
    assert result.content == tgz


def test_esxi_fetch_fails_when_bundle_not_found():
    adapter = ESXiAdapter()

    def exec_fn(cmd, timeout):
        if "sync_config" in cmd:
            return "OK"
        if "backup_config" in cmd:
            return "nothing useful"
        if cmd.startswith("ls -t"):
            return ""
        return ""

    result = adapter.fetch(_conn(), exec_fn)
    assert not result.ok
    assert "could not locate" in result.message


# --------------------------------------------------------------------------
# Generic SSH
# --------------------------------------------------------------------------
def test_generic_ssh_uses_default_command_when_unset():
    adapter = GenericSSHAdapter()
    seen = {}

    def exec_fn(cmd, timeout):
        seen["cmd"] = cmd
        return "Linux myhost 6.1.0\n"

    result = adapter.fetch(_conn(), exec_fn)
    assert result.ok
    assert "uname" in seen["cmd"] or "os-release" in seen["cmd"]


def test_generic_ssh_uses_custom_command():
    adapter = GenericSSHAdapter()
    seen = {}

    def exec_fn(cmd, timeout):
        seen["cmd"] = cmd
        return "set version 1.0\n"

    conn = _conn(options={"command": "show configuration | display set"})
    result = adapter.fetch(conn, exec_fn)
    assert result.ok
    assert seen["cmd"] == "show configuration | display set"
    assert b"set version 1.0" in result.content


def test_generic_ssh_empty_is_failure():
    adapter = GenericSSHAdapter()
    result = adapter.fetch(_conn(), lambda cmd, timeout: "")
    assert not result.ok
