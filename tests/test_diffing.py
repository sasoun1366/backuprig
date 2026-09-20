import io
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backuprig.diffing import (
    archive_member_diff, diff_backups, is_probably_text, list_tar_members,
    unified_text_diff,
)


def _tar_gz(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_is_probably_text():
    assert is_probably_text(b"hostname R1\ninterface Gi0/1\n")
    assert is_probably_text(b"")
    assert not is_probably_text(b"\x00\x01\x02binarydata")


def test_unified_text_diff_shows_added_line():
    old = b"line1\nline2\n"
    new = b"line1\nline2\nline3\n"
    diff = unified_text_diff(old, new, "old.cfg", "new.cfg")
    assert "+line3" in diff
    assert "old.cfg" in diff and "new.cfg" in diff


def test_diff_backups_identical_content():
    data = b"same content\n"
    assert diff_backups(data, data) == "No changes."


def test_diff_backups_text_change():
    old = b"hostname R1\n"
    new = b"hostname R2\n"
    result = diff_backups(old, new)
    assert "-hostname R1" in result
    assert "+hostname R2" in result


def test_list_tar_members():
    data = _tar_gz({"a.txt": b"1", "sub/b.txt": b"22"})
    members = list_tar_members(data)
    names = [m.name for m in members]
    assert names == sorted(names)
    assert {"a.txt", "sub/b.txt"} == set(names)


def test_archive_member_diff_added_removed_resized():
    old = _tar_gz({"a.txt": b"1", "b.txt": b"22"})
    new = _tar_gz({"a.txt": b"1234", "c.txt": b"new"})
    result = archive_member_diff(old, new)
    assert "Added files" in result and "c.txt" in result
    assert "Removed files" in result and "b.txt" in result
    assert "Resized files" in result and "a.txt" in result


def test_archive_member_diff_no_changes():
    data = _tar_gz({"a.txt": b"1"})
    result = archive_member_diff(data, data)
    assert "No file-level changes" in result


def test_diff_backups_routes_binary_archives_to_member_diff():
    old = _tar_gz({"winroute.cfg": b"<a/>"})
    new = _tar_gz({"winroute.cfg": b"<a/>", "extra.cfg": b"<b/>"})
    result = diff_backups(old, new)
    assert "Added files" in result
    assert "extra.cfg" in result


def test_diff_backups_binary_non_archive_fallback():
    old = b"\x00\x01\x02"
    new = b"\x00\x01\x03\x04"
    result = diff_backups(old, new)
    assert "Binary content changed" in result


def test_list_tar_members_invalid_data_returns_empty():
    assert list_tar_members(b"not a tar file") == []
