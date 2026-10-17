"""What a hook call means, and the name an upload gets. Pure decisions, no Orca and no I/O.

Nothing here uploads: every upload starts from a click in the 3D Print Log tab or the script
(see controller.py). Events and the hook only record what happened to the latest slice.
"""

import enum
import os

FILE_HOST = "File"


class Action(enum.Enum):
    RECORD = "record the exported file"
    IGNORE = "ignore"


def for_gcode(host, gcode_path, output_name):
    """The optional psGCodePostProcess hook.

    - host != "File": an upload to a print host (non-Bambu); keep a copy of this file.
    - host == "File", gcode_path != output_name: an export to file; keep a copy.
    - host == "File", gcode_path == output_name: Bambu's after-slice run on the plate's temp
      G-code. GCodeExportFinished already recorded that slice, so nothing to do.
    """
    if not host or not gcode_path:
        return Action.IGNORE
    if host != FILE_HOST:
        return Action.RECORD
    if _same_path(gcode_path, output_name):
        return Action.IGNORE
    return Action.RECORD


def upload_name(candidate, model_name):
    """The outputName for an upload: `candidate` (an export path, a host file name, or a project
    name an event carries) when it is a real file or project name, else the model name, else ""
    (the uploader then names it after the first object).

    UploadStarted's ctx.name is the upload's source file, which Orca names
    ".OrcaSlicer.upload.<random>" (or the plate's ".<pid>.<n>.3mf" for a 3MF upload), so it is
    skipped as hidden. PrintJobStarted's and SendJobStarted's ctx.name is the project name.
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
