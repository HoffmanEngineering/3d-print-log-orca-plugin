"""When to upload, and under what name. Pure decisions, no Orca and no I/O.

Every slice raises GCodeExportFinished (GCode.cpp) with the plate's temp G-code; the controller
keeps the latest one. These events then upload it (once per slice, see controller.py):
- UploadStarted (PrintHost.cpp): any print-host upload (OctoPrint, Moonraker, PrusaLink, ...).
- Bambu: PrintJobStarted (multi-machine send) or a connected printer starting soon after the
  slice (PrintStateChanged), since a single-printer send raises no event.
- The optional psGCodePostProcess hook, for an export to file or a print-host upload, when a
  process preset lists it.
"""

import enum
import os

FILE_HOST = "File"
# Bambu device states (PrintStateChanged msg) that mean a job just started.
STARTING_STATES = frozenset({"PREPARE", "RUNNING"})


class Action(enum.Enum):
    UPLOAD_NOW = "upload now"
    UPLOAD_LATEST = "upload the latest slice"
    IGNORE = "ignore"


def for_gcode(host, gcode_path, output_name):
    """The optional psGCodePostProcess hook.

    - host != "File": an upload to a print host (non-Bambu); upload this file now.
    - host == "File", gcode_path != output_name: an export to file; upload now.
    - host == "File", gcode_path == output_name: Bambu's after-slice run on the plate's temp
      G-code. GCodeExportFinished already recorded that slice, so nothing to do.
    """
    if not host or not gcode_path:
        return Action.IGNORE
    if host != FILE_HOST:
        return Action.UPLOAD_NOW
    if _same_path(gcode_path, output_name):
        return Action.IGNORE
    return Action.UPLOAD_NOW


def for_bambu_event(event_name, msg, sliced_at, now, window_minutes):
    """A Bambu lifecycle event, given when the latest slice was made (None: no slice).

    PrintJobStarted comes only from the multi-machine send. A single-printer send fires no
    event, so a connected printer starting soon after the slice stands in for it.
    """
    if sliced_at is None:
        return Action.IGNORE
    if event_name == "PrintJobStarted":
        return Action.UPLOAD_LATEST
    starting = event_name == "PrintStateChanged" and (msg or "").strip().upper() in STARTING_STATES
    if starting and 0 <= now - sliced_at <= window_minutes * 60:
        return Action.UPLOAD_LATEST
    return Action.IGNORE


def upload_name(candidate, model_name):
    """The outputName for an upload of the latest slice: `candidate` (a name an event carries)
    when it is a real file or project name, else the model name, else "" (the worker then names
    it after the first object).

    UploadStarted's ctx.name is the upload's source file, which Orca names
    ".OrcaSlicer.upload.<random>" (or the plate's ".<pid>.<n>.3mf" for a 3MF upload), so it is
    skipped as hidden. PrintJobStarted's ctx.name is the project name.
    """
    for name in (candidate, model_name):
        name = (name or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
        if not name or name.startswith("."):
            continue
        lower = name.lower()
        if lower.endswith(".gcode.3mf"):
            return name[: -len(".3mf")]
        if lower.endswith((".gcode", ".bgcode")):
            return name
        if lower.endswith(".3mf"):
            name = name[: -len(".3mf")]
        return f"{name}.gcode"
    return ""


def _same_path(a, b):
    return bool(b) and os.path.normcase(os.path.normpath(a)) == os.path.normcase(
        os.path.normpath(b)
    )
