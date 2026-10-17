"""G-code copies in plugin storage, and the one staged slice waiting for a Bambu send.

Orca deletes or reuses its working file as soon as the hook returns, so every upload works
from a copy. Layout under the storage folder: staged/slice.gcode (the staged slice) and
jobs/<id>.gcode (copies being uploaded; the worker deletes each one when done).
"""

import shutil
import threading
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class Slice:
    path: Path
    host: str
    output_name: str
    orca_version: str
    preset_ids: dict  # print/filament/printer_settings_id, as ctx.config_value returned them
    staged_at: float


class Staging:
    def __init__(self, storage_dir, clock=time.time):
        self._staged_dir = Path(storage_dir) / "staged"
        self._jobs_dir = Path(storage_dir) / "jobs"
        self._clock = clock
        self._lock = threading.Lock()
        self._staged = None
        # Nothing from an earlier session can still be in use; its staged slice is unknown.
        for folder in (self._staged_dir, self._jobs_dir):
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True, exist_ok=True)

    def working_copy(self, source):
        """A private copy of `source` for one upload."""
        target = self._jobs_dir / f"{uuid.uuid4().hex}.gcode"
        shutil.copyfile(source, target)
        return target

    def stage(self, source, *, host, output_name, orca_version, preset_ids):
        """Copies `source` in as the staged slice, replacing any earlier one."""
        target = self._staged_dir / "slice.gcode"
        partial = self._staged_dir / f"{uuid.uuid4().hex}.partial"
        try:
            shutil.copyfile(source, partial)
            with self._lock:
                partial.replace(target)
                self._staged = Slice(
                    target, host, output_name, orca_version, dict(preset_ids), self._clock()
                )
                return self._staged
        finally:
            partial.unlink(missing_ok=True)

    def staged(self):
        with self._lock:
            return self._staged

    def take(self):
        """The staged slice, moved out of the way of the next one; None if nothing is staged.
        Each staged slice is handed out once."""
        with self._lock:
            staged, self._staged = self._staged, None
            if staged is None:
                return None
            moved = self._jobs_dir / f"{uuid.uuid4().hex}.gcode"
            staged.path.replace(moved)
            return replace(staged, path=moved)

    def clear(self):
        with self._lock:
            staged, self._staged = self._staged, None
            if staged is not None:
                staged.path.unlink(missing_ok=True)
