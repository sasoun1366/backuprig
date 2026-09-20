"""Diff helpers for comparing two backups of the same device over time.

Text configs (Cisco/MikroTik/generic) get a real line-based unified diff.
Binary bundles (Kerio tar.gz, ESXi configBundle.tgz) can't be diffed as text,
so we diff the *list of member files* inside the archive instead - which
still answers "what changed" for the common case of a setting flipping a
file's presence/size.
"""

from __future__ import annotations

import difflib
import io
import tarfile
from dataclasses import dataclass
from typing import List, Optional


def is_probably_text(data: bytes) -> bool:
    if not data:
        return True
    sample = data[:4096]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def unified_text_diff(old: bytes, new: bytes, old_label: str = "old", new_label: str = "new") -> str:
    old_lines = old.decode("utf-8", errors="replace").splitlines(keepends=True)
    new_lines = new.decode("utf-8", errors="replace").splitlines(keepends=True)
    diff = difflib.unified_diff(old_lines, new_lines, fromfile=old_label, tofile=new_label)
    return "".join(diff)


@dataclass
class TarMemberInfo:
    name: str
    size: int


def list_tar_members(data: bytes) -> List[TarMemberInfo]:
    members: List[TarMemberInfo] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            for m in tf.getmembers():
                if m.isfile():
                    members.append(TarMemberInfo(name=m.name, size=m.size))
    except tarfile.TarError:
        return []
    return sorted(members, key=lambda m: m.name)


def archive_member_diff(old: bytes, new: bytes) -> str:
    """A human-readable summary of which files were added/removed/resized
    between two tar(.gz) archives."""
    old_members = {m.name: m.size for m in list_tar_members(old)}
    new_members = {m.name: m.size for m in list_tar_members(new)}

    added = sorted(set(new_members) - set(old_members))
    removed = sorted(set(old_members) - set(new_members))
    common = sorted(set(old_members) & set(new_members))
    resized = [(name, old_members[name], new_members[name])
               for name in common if old_members[name] != new_members[name]]

    lines: List[str] = []
    if not added and not removed and not resized:
        lines.append("No file-level changes detected inside the archive.")
    if added:
        lines.append(f"Added files ({len(added)}):")
        lines.extend(f"  + {name}" for name in added)
    if removed:
        lines.append(f"Removed files ({len(removed)}):")
        lines.extend(f"  - {name}" for name in removed)
    if resized:
        lines.append(f"Resized files ({len(resized)}):")
        lines.extend(f"  ~ {name}: {old_sz} -> {new_sz} bytes" for name, old_sz, new_sz in resized)
    return "\n".join(lines)


def diff_backups(old: bytes, new: bytes, old_label: str = "old", new_label: str = "new") -> str:
    """Pick the right diff strategy automatically based on content."""
    if old == new:
        return "No changes."
    if is_probably_text(old) and is_probably_text(new):
        out = unified_text_diff(old, new, old_label, new_label)
        return out if out else "No changes."
    if tarfile.is_tarfile(io.BytesIO(old)) or tarfile.is_tarfile(io.BytesIO(new)):
        return archive_member_diff(old, new)
    return (
        f"Binary content changed ({len(old)} -> {len(new)} bytes); "
        "no text or archive diff available."
    )
