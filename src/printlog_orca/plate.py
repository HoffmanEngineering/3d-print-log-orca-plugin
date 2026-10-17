"""What Orca's project calls the plate, for a slice whose GCodeExportFinished had no ctx.name
(an STL project, in 2.5.0-alpha): the saved project's name, else its objects' names. Read on the
UI thread (SlicingJobComplete) from Orca's model in memory: no file, no audited I/O. "" when
there is nothing to go by; the controller then says "your plate".

The model holds the objects of every plate, not only the one sliced, so with several plates the
name can list more than was sliced. It names the slice in the tab and in notifications only.
"""

import logging
from pathlib import PurePath

import orca

MODEL_SUFFIXES = (".stl", ".3mf", ".obj", ".amf", ".step", ".stp", ".svg", ".drc", ".ply")

_log = logging.getLogger("printlog_orca")


def display_name():
    """Never raises."""
    try:
        name = project_name(orca.host.plater().project_path())
    except Exception as error:
        _log.debug("No project path: %s", error)
        name = ""
    if name:
        return name
    try:
        names = [getattr(o, "name", "") for o in orca.host.model().objects()]
    except Exception as error:
        _log.debug("No model objects: %s", error)
        return ""
    return objects_name(names)


def project_name(path):
    """The project file's name without .3mf, from a path string (no file access)."""
    if not isinstance(path, str) or not path.strip():
        return ""
    return _strip_suffix(PurePath(path.strip().replace("\\", "/")).name)


def objects_name(names):
    """'Cube', 'Cube and Benchy', 'Cube and 2 more', or ''."""
    names = [_strip_suffix(n.strip()) for n in names if isinstance(n, str) and n.strip()]
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{names[0]} and {len(names) - 1} more"


def _strip_suffix(name):
    lower = name.lower()
    for suffix in MODEL_SUFFIXES:
        if lower.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name
