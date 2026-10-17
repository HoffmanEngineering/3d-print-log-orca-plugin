"""This install's id: a random 128-bit value, created once and kept in plugin storage.

Every upload carries it (metadata `installId`), and the browser fallback opens
/prints/new/orca?device=<id>, which lists this install's uploads waiting to be claimed. The URL
is the same every time, so Orca asks once per install (or update) before it opens the browser.
The id is a bearer value: the page still needs sign-in to claim, and uploads expire.

Called only from audited UI-thread callbacks; plugin storage is inside data_dir(), so reading
and writing it never prompts.
"""

import json
import logging
import re
import uuid
from pathlib import Path

FILE_NAME = "install.json"
_VALID = re.compile(r"[0-9a-f]{32}")

_log = logging.getLogger("printlog_orca")


def install_id(storage_dir):
    path = Path(storage_dir) / FILE_NAME
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("installId")
        if isinstance(value, str) and _VALID.fullmatch(value):
            return value
    except FileNotFoundError:
        pass
    except (OSError, ValueError, AttributeError) as error:
        _log.warning("Replacing the unreadable %s: %s", path, error)

    value = uuid.uuid4().hex
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"installId": value}), encoding="utf-8")
    except OSError as error:
        _log.warning("Could not save the install id to %s: %s", path, error)
    return value
