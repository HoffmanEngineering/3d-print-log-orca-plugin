"""When to upload. Pure decisions, no Orca and no I/O.

Where Orca runs the psGCodePostProcess hook (BackgroundSlicingProcess.cpp, 2.5):
- export to file, every printer: host "File", gcode_path is a working copy of the export;
- upload to a print host (non-Bambu): host is the print host's name;
- Bambu printers, after every slice: host "File" and gcode_path == output_name, the plate's
  temp G-code. A Bambu send does not run the hook again, so that slice is staged and uploaded
  when a send shows up as a lifecycle event.
"""

import enum
import os

FILE_HOST = "File"
# Bambu device states (PrintStateChanged msg) that mean a job just started.
STARTING_STATES = frozenset({"PREPARE", "RUNNING"})


class Action(enum.Enum):
    UPLOAD_NOW = "upload now"
    STAGE = "stage"
    UPLOAD_STAGED = "upload staged"
    IGNORE = "ignore"


def for_gcode(host, gcode_path, output_name):
    """The psGCodePostProcess hook."""
    if not host or not gcode_path:
        return Action.IGNORE
    if host != FILE_HOST:
        return Action.UPLOAD_NOW
    if _same_path(gcode_path, output_name):
        return Action.STAGE
    return Action.UPLOAD_NOW


def for_event(event_name, msg, staged_at, now, window_minutes):
    """A lifecycle event, given when the current slice was staged (None: nothing staged).

    PrintJobStarted comes only from the multi-machine send. A single-printer send fires no
    event, so a connected printer starting soon after the slice stands in for it.
    """
    if staged_at is None:
        return Action.IGNORE
    if event_name == "PrintJobStarted":
        return Action.UPLOAD_STAGED
    starting = event_name == "PrintStateChanged" and (msg or "").strip().upper() in STARTING_STATES
    if starting and 0 <= now - staged_at <= window_minutes * 60:
        return Action.UPLOAD_STAGED
    return Action.IGNORE


def _same_path(a, b):
    return bool(b) and os.path.normcase(os.path.normpath(a)) == os.path.normcase(
        os.path.normpath(b)
    )
