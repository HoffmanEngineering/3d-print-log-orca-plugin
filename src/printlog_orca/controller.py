"""State the capabilities share: config, the latest slice, and what starts an upload.

No Orca here: plugin.py and script.py pass in plain values read from Orca, and plugin.py hands
in the function that pushes a notification.

The latest slice is the one GCodeExportFinished last reported: the plate's temp G-code path,
the model name and the time. A newer slice replaces it. Its file is not copied when it is
recorded (that would be I/O on the slicing thread); the background thread copies it when an
upload starts. That is the file Orca sends: an export to file and a print-host upload both copy
the same temp G-code, and a re-slice in between replaces both. Each slice is uploaded at most
once, by the first of: the notification's link, a print-host upload, a Bambu send, the optional
slicing hook, or the script (which can also log it again on purpose).
"""

import functools
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from printlog_orca import presets, staging, triggers, worker
from printlog_orca.background import Background
from printlog_orca.triggers import Action

DEFAULT_CONFIG = {
    "template": "default",
    "open_browser": True,
    "api_base": "https://api.3dprintlog.com",
    "web_base": "https://www.3dprintlog.com",
    "bambu_send_window_minutes": 30,
    "slice_notification": True,
    "debug": False,
}
TEMPLATES = ("default", "full")
PRESET_KEYS = ("print_settings_id", "filament_settings_id", "printer_settings_id")
# The lifecycle events the plugin acts on; plugin.py ignores the rest unread.
SLICE_EVENT = "GCodeExportFinished"
UPLOAD_EVENT = "UploadStarted"
BAMBU_EVENTS = frozenset({"PrintJobStarted", "PrintStateChanged"})
WATCHED_EVENTS = frozenset({SLICE_EVENT, UPLOAD_EVENT}) | BAMBU_EVENTS
# A burst of slices (background slicing while editing) gives one notification, this long after
# the last one. Also gives a print-host upload, which follows its slice at once, time to log the
# slice before a notification shows.
NOTIFY_DELAY_SECONDS = 2.0
NOTIFICATION_LINK = "Log to 3D Print Log"

_log = logging.getLogger("printlog_orca")


@dataclass(frozen=True)
class Settings:
    template: str
    open_browser: bool
    api_base: str
    web_base: str
    bambu_send_window_minutes: float
    slice_notification: bool
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
        slice_notification=pick("slice_notification", is_bool),
        debug=pick("debug", is_bool),
    )


@dataclass
class Slice:
    """One GCodeExportFinished (code Ok): the identity the dedupe works on. `number` counts
    slices in this session; a re-slice of the same plate reuses the path but gets a new number."""

    number: int
    path: str
    model_name: str
    sliced_at: float
    storage_dir: Path
    settings: Settings
    uploaded_by: str | None = None  # what uploaded it; None while it has not been


class Controller:
    def __init__(
        self,
        start_job=None,
        clock=time.time,
        background=None,
        notify_delay=NOTIFY_DELAY_SECONDS,
    ):
        # Started here, at import of the shared instance: outside any Orca callback, where
        # starting a thread is not audited (see background.py).
        self._background = Background(notify_delay) if background is None else background
        self._background.start()
        self._start_job = self._submit_job if start_job is None else start_job
        self._clock = clock
        self._started_at = clock()
        self._lock = threading.Lock()
        self._slice = None
        self._count = 0
        self._push = None
        self._active = True
        self._storage_dir = None
        self._settings = None
        self._cleaned = False

    # --- wiring (plugin.py) ---------------------------------------------------------------

    def set_notifier(self, push):
        """push(text, hyper_text, on_click): orca.host.ui.push_notification, called only from
        the background thread."""
        self._push = push

    def set_active(self, active):
        """False after on_unload: Orca does not disable a notification's on_click that was
        pushed from the plugin's own thread (it is bound to an empty plugin key), so a click
        after unload must do nothing."""
        self._active = active

    @property
    def background(self):
        return self._background

    # --- events ---------------------------------------------------------------------------

    def on_slice_finished(self, *, storage_dir, settings, gcode_path, model_name):
        """GCodeExportFinished with code Ok, on the slicing thread: record it and return. No
        file access here; the notification is pushed later from the background thread."""
        if not gcode_path:
            return
        with self._lock:
            self._count += 1
            current = Slice(
                number=self._count,
                path=gcode_path,
                model_name=(model_name or "").strip(),
                sliced_at=self._clock(),
                storage_dir=Path(storage_dir),
                settings=settings,
            )
            self._slice = current
            self._remember(storage_dir, settings)
        if settings.slice_notification and self._push is not None:
            self._background.notify_later(functools.partial(self._notify, current.number))

    def on_upload_started(self, *, settings, name):
        """UploadStarted, on the print-host queue thread: the latest slice is being sent."""
        upload = self._take_latest("print-host upload")
        if upload is None:
            return
        self._start_latest(
            upload, settings, host="PrintHost", output_name=triggers.upload_name(name, "")
        )

    def on_bambu_event(self, *, settings, event_name, msg, name):
        """PrintJobStarted / PrintStateChanged, on the device thread."""
        with self._lock:
            latest = self._slice
            sliced_at = latest.sliced_at if latest is not None else None
            action = triggers.for_bambu_event(
                event_name, msg, sliced_at, self._clock(), settings.bambu_send_window_minutes
            )
            if action is not Action.UPLOAD_LATEST or latest.uploaded_by is not None:
                return
            latest.uploaded_by = f"Bambu send ({event_name})"
        # PrintJobStarted carries the project name; PrintStateChanged a device id.
        candidate = name if event_name == "PrintJobStarted" else ""
        self._start_latest(
            latest, settings, host="Bambu", output_name=triggers.upload_name(candidate, "")
        )

    def on_gcode(
        self, *, storage_dir, settings, host, gcode_path, output_name, orca_version, preset_ids
    ):
        """The optional psGCodePostProcess hook, on the slicing thread. Returns a short message
        for Orca's log. It copies Orca's file now (Orca removes it after the hook) and stands
        for the latest slice: the plate just sliced is the one exported or uploaded."""
        if triggers.for_gcode(host, gcode_path, output_name) is Action.IGNORE:
            return ""
        with self._lock:
            self._remember(storage_dir, settings)
            latest = self._slice
            if latest is not None:
                if latest.uploaded_by is not None:
                    _log.info(
                        "Slice %d was logged already (by %s); not logging this %s",
                        latest.number,
                        latest.uploaded_by,
                        "export" if host == triggers.FILE_HOST else "upload",
                    )
                    return "3D Print Log: this slice was logged already"
                latest.uploaded_by = "export" if host == triggers.FILE_HOST else "print-host hook"
        try:
            copy = staging.working_copy(storage_dir, gcode_path)
        except Exception:
            if latest is not None:
                with self._lock:
                    latest.uploaded_by = None  # still loggable through the other paths
            raise
        self._start(
            worker.Job(
                gcode_path=copy,
                output_name=output_name,
                host=host,
                orca_version=orca_version,
                preset_ids=dict(preset_ids),
                **self._job_settings(Path(storage_dir), settings),
            )
        )
        return "3D Print Log: uploading"

    def click(self):
        """The notification's link, on the UI thread outside any callback. Returns True, which
        closes the notification. Never raises."""
        try:
            if not self._active:
                return True
            upload = self._take_latest("notification")
            if upload is not None:
                self._start_latest(upload, upload.settings, host="File", output_name="")
        except Exception:
            _log.exception("Could not log the slice")
        return True

    def log_last_slice(self):
        """The script. Uploads the latest slice even if it was logged already (a deliberate
        re-log). True if an upload started."""
        with self._lock:
            latest = self._slice
            if latest is None:
                return False
            if latest.uploaded_by is not None:
                _log.info("Logging the last slice again (first logged by %s)", latest.uploaded_by)
            latest.uploaded_by = latest.uploaded_by or "script"
            settings = self._settings or latest.settings
        self._start_latest(latest, settings, host="File", output_name="")
        return True

    # --- internals ------------------------------------------------------------------------

    def _take_latest(self, reason):
        """The latest slice, marked uploaded by `reason`; None (logged) if there is none or it
        was uploaded already."""
        with self._lock:
            latest = self._slice
            if latest is None:
                _log.info("Nothing to log for the %s: no slice yet in this session", reason)
                return None
            if latest.uploaded_by is not None:
                _log.info(
                    "Slice %d was logged already (by %s); not logging it again for the %s",
                    latest.number,
                    latest.uploaded_by,
                    reason,
                )
                return None
            latest.uploaded_by = reason
            return latest

    def _notify(self, number):
        """On the background thread, after the debounce delay."""
        with self._lock:
            latest = self._slice
            if (
                not self._active
                or latest is None
                or latest.number != number
                or latest.uploaded_by is not None
                or self._push is None
            ):
                return
            text = f"Sliced {latest.model_name or 'the plate'}."
        try:
            self._push(text, NOTIFICATION_LINK, self.click)
        except Exception as error:
            _log.warning("Could not show the 3D Print Log notification: %s", error)

    def _start_latest(self, latest, settings, *, host, output_name):
        _log.info("Logging slice %d (%s)", latest.number, latest.uploaded_by)
        self._start(
            worker.Job(
                gcode_path=staging.new_job_path(latest.storage_dir),
                source_path=Path(latest.path),
                output_name=triggers.upload_name(output_name, latest.model_name),
                host=host,
                orca_version="",
                preset_ids=None,
                **self._job_settings(latest.storage_dir, settings),
            )
        )

    def _job_settings(self, storage_dir, settings):
        return {
            "data_dir": presets.data_dir_from_storage(storage_dir),
            "debug_dir": storage_dir / "debug" if settings.debug else None,
            "template": settings.template,
            "api_base": settings.api_base,
            "web_base": settings.web_base,
            "open_browser": settings.open_browser,
        }

    def _remember(self, storage_dir, settings):
        """Under the lock. Also clears an earlier session's leftover copies, once, from the
        background thread."""
        self._storage_dir = Path(storage_dir)
        self._settings = settings
        if not self._cleaned:
            self._cleaned = True
            self._background.submit(
                functools.partial(staging.remove_leftovers, self._storage_dir, self._started_at)
            )

    def _start(self, job):
        self._start_job(job)

    def _submit_job(self, job):
        self._background.submit(functools.partial(worker.run, job))


# The one instance the capabilities share; tests swap it. Created at import, which Orca runs
# outside any audit context, so its background thread starts without a permission prompt.
shared = Controller()
