"""Copies of exported G-code in plugin storage: <storage>/uploads/exports/<id>.gcode.

The optional psGCodePostProcess hook gets Orca's file for the length of the call only (Orca
grants its folder as a scoped root, then deletes or reuses the file), so the hook copies it here,
inside data_dir(), where reading it later never prompts. The tab then logs that exact file. A
copy is removed once no listed slice refers to it.
"""

import shutil
import uuid
from pathlib import Path


def exports_dir(storage_dir):
    return Path(storage_dir) / "uploads" / "exports"


def export_copy(storage_dir, source):
    """Copies `source` to a fresh path in storage and returns it."""
    target = exports_dir(storage_dir) / f"{uuid.uuid4().hex}.gcode"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return target


def remove_unreferenced(storage_dir, keep):
    """Deletes the copies not in `keep`: ones whose slice left the list, or an earlier
    session's. Returns how many were removed."""
    folder = exports_dir(storage_dir)
    kept = {Path(path).name for path in keep if path is not None}
    removed = 0
    try:
        entries = list(folder.iterdir())
    except OSError:
        return 0
    for entry in entries:
        if entry.name in kept:
            continue
        try:
            if entry.is_file():
                entry.unlink()
                removed += 1
        except OSError:
            pass
    return removed
