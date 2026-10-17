"""Private G-code copies in plugin storage, one per upload: <storage>/uploads/jobs/<id>.gcode.

Every upload works from a copy. The slicing hook's file is deleted or reused by Orca as soon as
the hook returns, so the hook copies it on the spot (Orca grants its folder for that call). The
plate's temp G-code is copied by the background thread when the upload starts, so a re-slice
while the upload runs cannot change the file under it. The worker deletes each copy when done.
"""

import shutil
import uuid
from pathlib import Path


def jobs_dir(storage_dir):
    return Path(storage_dir) / "uploads" / "jobs"


def new_job_path(storage_dir):
    """A fresh path for one upload's copy. Pure: nothing is created."""
    return jobs_dir(storage_dir) / f"{uuid.uuid4().hex}.gcode"


def working_copy(storage_dir, source):
    """Copies `source` to a fresh job path and returns it."""
    target = new_job_path(storage_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return target


def remove_leftovers(storage_dir, before):
    """Deletes copies older than `before` (a time.time() value): an earlier session's, which a
    crash left behind. Newer copies may belong to an upload starting right now."""
    folder = jobs_dir(storage_dir)
    removed = 0
    try:
        entries = list(folder.iterdir())
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.is_file() and entry.stat().st_mtime < before:
                entry.unlink()
                removed += 1
        except OSError:
            pass
    return removed
