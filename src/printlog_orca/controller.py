"""State shared by the uploader and script capabilities: config, the staged slice, starting jobs.

No Orca here: plugin.py and script.py pass in plain values read from Orca.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from printlog_orca import presets, triggers, worker
from printlog_orca.staging import Staging
from printlog_orca.triggers import Action

DEFAULT_CONFIG = {
    "template": "default",
    "open_browser": True,
    "api_base": "https://api.3dprintlog.com",
    "web_base": "https://www.3dprintlog.com",
    "bambu_send_window_minutes": 30,
    "debug": False,
}
TEMPLATES = ("default", "full")
PRESET_KEYS = ("print_settings_id", "filament_settings_id", "printer_settings_id")
# The lifecycle events that can upload a staged slice; plugin.py ignores the rest unread.
WATCHED_EVENTS = frozenset({"PrintJobStarted", "PrintStateChanged"})

_log = logging.getLogger("printlog_orca")


@dataclass(frozen=True)
class Settings:
    template: str
    open_browser: bool
    api_base: str
    web_base: str
    bambu_send_window_minutes: float
    debug: bool


def parse_settings(raw):
    """The capability's stored config (a JSON string) over the defaults; a bad value is
    logged and replaced by its default."""
    try:
        stored = json.loads(raw) if raw else {}
    except ValueError:
        _log.warning("Plugin config is not JSON; using the defaults")
        stored = {}
    if not isinstance(stored, dict):
        stored = {}

    def pick(key, valid):
        value = stored.get(key, DEFAULT_CONFIG[key])
        if valid(value):
            return value
        _log.warning(
            "Plugin config %s = %r is not valid; using %r", key, value, DEFAULT_CONFIG[key]
        )
        return DEFAULT_CONFIG[key]

    def is_url(value):
        return isinstance(value, str) and value.startswith(("https://", "http://"))

    def is_bool(value):
        return isinstance(value, bool)

    def is_minutes(value):
        return isinstance(value, int | float) and not isinstance(value, bool) and value >= 0

    return Settings(
        template=pick("template", lambda v: v in TEMPLATES),
        open_browser=pick("open_browser", is_bool),
        api_base=pick("api_base", is_url).rstrip("/"),
        web_base=pick("web_base", is_url).rstrip("/"),
        bambu_send_window_minutes=pick("bambu_send_window_minutes", is_minutes),
        debug=pick("debug", is_bool),
    )


class Controller:
    def __init__(self, start_job=worker.start, clock=time.time):
        self._start_job = start_job
        self._clock = clock
        self._lock = threading.Lock()
        self._staging = None
        self._storage_dir = None
        self._settings = None  # the uploader's, for the script capability

    def on_gcode(
        self, *, storage_dir, settings, host, gcode_path, output_name, orca_version, preset_ids
    ):
        """The psGCodePostProcess hook. Returns a short message for Orca's log."""
        action = triggers.for_gcode(host, gcode_path, output_name)
        if action is Action.IGNORE:
            return ""
        staging = self._use(storage_dir, settings)
        if action is Action.STAGE:
            staging.stage(
                gcode_path,
                host=host,
                output_name=output_name,
                orca_version=orca_version,
                preset_ids=preset_ids,
            )
            return "3D Print Log: slice staged; it uploads when it is sent to the printer"

        copy = staging.working_copy(gcode_path)
        staging.clear()  # this export or upload stands for the staged slice, if any
        self._start(copy, host, output_name, orca_version, preset_ids, settings)
        return "3D Print Log: uploading"

    def on_event(self, *, settings, event_name, msg):
        staging = self._staging
        staged = staging.staged() if staging else None
        staged_at = staged.staged_at if staged else None
        window = settings.bambu_send_window_minutes
        if triggers.for_event(event_name, msg, staged_at, self._clock(), window) is not (
            Action.UPLOAD_STAGED
        ):
            return
        self._upload_staged(settings, f"{event_name} {msg}".strip())

    def log_last_slice(self):
        """The script's fallback for a send no event reported. True if an upload started."""
        return self._upload_staged(self._settings, "Log last slice")

    def _upload_staged(self, settings, reason):
        staging = self._staging
        taken = staging.take() if staging else None
        if taken is None:
            return False
        _log.info("Uploading the staged slice (%s)", reason)
        self._start(
            taken.path,
            taken.host,
            taken.output_name,
            taken.orca_version,
            taken.preset_ids,
            settings,
        )
        return True

    def _use(self, storage_dir, settings):
        with self._lock:
            storage_dir = Path(storage_dir)
            if self._staging is None or storage_dir != self._storage_dir:
                self._staging = Staging(storage_dir / "uploads", self._clock)
                self._storage_dir = storage_dir
            self._settings = settings
            return self._staging

    def _start(self, path, host, output_name, orca_version, preset_ids, settings):
        self._start_job(
            worker.Job(
                gcode_path=path,
                output_name=output_name,
                host=host,
                orca_version=orca_version,
                preset_ids=dict(preset_ids),
                data_dir=presets.data_dir_from_storage(self._storage_dir),
                debug_dir=self._storage_dir / "debug" if settings.debug else None,
                template=settings.template,
                api_base=settings.api_base,
                web_base=settings.web_base,
                open_browser=settings.open_browser,
            )
        )


# The one instance the capabilities share; tests swap it.
shared = Controller()
