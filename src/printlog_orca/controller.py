"""State the capabilities share: config, the recent slices, and the log flow the tab drives.

No Orca here: plugin.py, page.py and script.py pass in plain values read from Orca.

Consent model (see the spec's Decisions): nothing uploads on its own. Lifecycle events and the
optional hook only record what happened to a slice, on whatever Orca thread raised them, and do
no I/O except the hook's copy into plugin storage. The I/O that Orca guards with a permission
prompt (reading the plate's temp G-code, the upload, opening the browser) happens only in
request_log / finish_log / open_browser / log_latest, which the tab's on_message and the script
call on Orca's UI thread inside an audited callback. Orca's prompts therefore appear right after
the user's click, and a "No" is reported in the tab. The plugin starts no thread.

The slices list holds the newest MAX_SLICES slices of this session. A slice is one
GCodeExportFinished (code Ok): the plate's temp G-code path, the model name and the time. A
re-slice of the same plate overwrites that file, so it replaces the plate's pending entry.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from printlog_orca import browser, client, install, payload, presets, staging, triggers, uploader

DEFAULT_CONFIG = {
    "template": "default",
    "open_claim_page": "window",
    "api_base": "https://api.3dprintlog.com",
    "web_base": "https://www.3dprintlog.com",
    "slice_notification": True,
    "debug": False,
}
TEMPLATES = ("default", "full")
# "window": the claim page in an OrcaSlicer window; "browser": this install's uploads page in
# the system browser. "tab" is what 0.1.0 called the first one.
OPEN_MODES = ("window", "browser")
OPEN_MODE_ALIASES = {"tab": "window"}
PRESET_KEYS = ("print_settings_id", "filament_settings_id", "printer_settings_id")
MAX_SLICES = 10
TAB_NAME = "3D Print Log"

# The lifecycle events the plugin acts on; plugin.py ignores the rest unread.
SLICE_EVENT = "GCodeExportFinished"  # slicing thread: record the slice
SLICE_DONE_EVENT = "SlicingJobComplete"  # UI thread: the after-slice notification
UPLOAD_EVENT = "UploadStarted"  # print-host queue thread: mark "sent to a print host"
BAMBU_EVENTS = frozenset({"PrintJobStarted", "SendJobStarted"})  # job worker: "sent to Bambu"
STATE_EVENT = "PrintStateChanged"  # UI thread: show the notice a Bambu send left
WATCHED_EVENTS = (
    frozenset({SLICE_EVENT, SLICE_DONE_EVENT, UPLOAD_EVENT, STATE_EVENT}) | BAMBU_EVENTS
)

NOT_LOGGED = "not logged"
UPLOADING = "uploading"
LOGGED = "logged"
UNNAMED = "your plate"

_log = logging.getLogger("printlog_orca")


@dataclass(frozen=True)
class Settings:
    template: str
    open_claim_page: str
    api_base: str
    web_base: str
    slice_notification: bool
    debug: bool


def parse_settings(raw):
    """The capability's stored config (a JSON string) over the defaults; a bad value is
    logged and replaced by its default. Keys from earlier versions are ignored."""
    try:
        stored = json.loads(raw) if raw else {}
    except ValueError:
        _log.warning("Plugin config is not JSON; using the defaults")
        stored = {}
    if not isinstance(stored, dict):
        stored = {}

    def pick(key, valid, aliases=None):
        value = stored.get(key, DEFAULT_CONFIG[key])
        if aliases and isinstance(value, str):
            value = aliases.get(value, value)
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

    return Settings(
        template=pick("template", lambda v: v in TEMPLATES),
        open_claim_page=pick("open_claim_page", lambda v: v in OPEN_MODES, OPEN_MODE_ALIASES),
        api_base=pick("api_base", is_url).rstrip("/"),
        web_base=pick("web_base", is_url).rstrip("/"),
        slice_notification=pick("slice_notification", is_bool),
        debug=pick("debug", is_bool),
    )


@dataclass
class Slice:
    id: int
    path: str  # the plate's temp G-code
    model_name: str  # GCodeExportFinished's ctx.name; empty for an STL project
    sliced_at: float
    display_name: str = ""  # from the project or its objects, for the tab and notifications only
    destination: str = ""  # "" while not sent; else what the tab shows
    host: str = triggers.FILE_HOST  # the upload's `host`
    output_name: str = ""  # an export path, a host file name or a project name
    copy: Path | None = None  # the hook's copy of the exported file, in storage
    orca_version: str = ""
    preset_ids: dict | None = None
    status: str = NOT_LOGGED
    status_before: str = NOT_LOGGED  # restored when an upload fails
    setting_id: str | None = None
    claim_url: str | None = None
    message: str = ""
    error: bool = False
    replaced: bool = False  # the temp file now holds a newer slice
    notified: bool = False

    @property
    def label(self):
        """The name notifications use."""
        return self.model_name or self.display_name or UNNAMED

    @property
    def name(self):
        """What the tab lists it as."""
        if self.model_name or self.display_name:
            return self.model_name or self.display_name
        name = payload.file_name(self.output_name) if self.output_name else ""
        return name or UNNAMED.capitalize()

    @property
    def can_log(self):
        return self.copy is not None or not self.replaced


class Controller:
    def __init__(self, clock=time.time, upload=client.upload):
        self._clock = clock
        self._upload = upload
        # Events arrive on several Orca threads; every method holds this only for assignments.
        self._lock = threading.Lock()
        self._slices = []  # oldest first
        self._count = 0
        self._config_source = None
        self._send_notice = None  # the slice id a Bambu send left a notice for
        self._install_id = None

    # --- wiring ---------------------------------------------------------------------------

    def set_config_source(self, get_config):
        """get_config() returns the uploader capability's stored config (a JSON string)."""
        self._config_source = get_config

    def settings(self):
        raw = None
        if self._config_source is not None:
            try:
                raw = self._config_source()
            except Exception as error:
                _log.warning("Could not read the plugin config: %s", error)
        return parse_settings(raw)

    # --- events: record only, on any Orca thread ------------------------------------------

    def record_slice(self, gcode_path, model_name):
        """GCodeExportFinished with code Ok, on the slicing thread. No I/O."""
        if not gcode_path:
            return
        with self._lock:
            for old in self._slices:
                if _same(old.path, gcode_path):
                    old.replaced = True
            self._slices = [
                s
                for s in self._slices
                if not (s.replaced and s.status != LOGGED and s.copy is None)
            ]
            self._count += 1
            self._slices.append(
                Slice(
                    id=self._count,
                    path=gcode_path,
                    model_name=(model_name or "").strip(),
                    sliced_at=self._clock(),
                )
            )
            del self._slices[:-MAX_SLICES]

    def mark_sent(self, event_name, name=""):
        """UploadStarted (print-host queue thread), PrintJobStarted / SendJobStarted (job
        worker): the latest slice is being sent. Record it; the user logs it from the tab."""
        with self._lock:
            latest = self._latest()
            if latest is None:
                return
            if event_name == UPLOAD_EVENT:
                # The hook, when a preset lists it, already named the host; the event cannot.
                if latest.copy is None or latest.host == triggers.FILE_HOST:
                    latest.destination = "Sent to a print host"
                    latest.host = "PrintHost"
            else:
                latest.destination = "Sent to Bambu"
                latest.host = "Bambu"
                # The project name; UploadStarted's name is a temp file and is never used.
                if latest.copy is None and triggers.upload_name(name, ""):
                    latest.output_name = name
                if latest.status != LOGGED:
                    self._send_notice = latest.id

    def on_gcode(self, *, storage_dir, host, gcode_path, output_name, orca_version, preset_ids):
        """The optional psGCodePostProcess hook, on the slicing thread, with Orca's file
        granted for this call only. Copies it into storage (inside data_dir(): no prompt) so the
        tab can log the exact file exported or uploaded. No network. Returns a short message."""
        if triggers.for_gcode(host, gcode_path, output_name) is triggers.Action.IGNORE:
            return ""
        copy = staging.export_copy(storage_dir, gcode_path)
        with self._lock:
            latest = self._latest()
            if latest is None:  # enabled after the slice: list the exported file itself
                self._count += 1
                latest = Slice(
                    id=self._count, path=str(copy), model_name="", sliced_at=self._clock()
                )
                self._slices.append(latest)
                del self._slices[:-MAX_SLICES]
            latest.copy = copy
            latest.host = host
            latest.output_name = output_name
            latest.orca_version = orca_version or ""
            latest.preset_ids = dict(preset_ids)
            if host == triggers.FILE_HOST:
                latest.destination = f"Exported to {payload.file_name(output_name)}"
            else:
                latest.destination = f"Sent to {host}"
            keep = [s.copy for s in self._slices]
        staging.remove_unreferenced(storage_dir, keep)
        return "3D Print Log: kept a copy to log from the 3D Print Log tab"

    # --- UI thread ------------------------------------------------------------------------

    def slicing_done(self, display_name=""):
        """SlicingJobComplete, on the UI thread: the notification text for the latest slice,
        once per slice, or None. display_name is what Orca's project calls the plate (its saved
        name, or its objects), for a slice whose event carried no name. It names the slice in
        the tab and in notifications only; the upload is named from the G-code."""
        settings = self.settings()
        with self._lock:
            latest = self._latest()
            if latest is None:
                return None
            if not latest.display_name:
                latest.display_name = (display_name or "").strip()
            if not settings.slice_notification or latest.notified:
                return None
            latest.notified = True
            if latest.status == LOGGED:
                return None
            return f"Sliced {latest.label}. Open the {TAB_NAME} tab to log it."

    def needs_display_name(self):
        """True when the latest slice has no name of its own yet."""
        with self._lock:
            latest = self._latest()
            return latest is not None and not (latest.model_name or latest.display_name)

    def take_send_notice(self):
        """PrintStateChanged, on the UI thread: the notice a Bambu send left, once, or None."""
        settings = self.settings()
        with self._lock:
            slice_id, self._send_notice = self._send_notice, None
            found = self._find(slice_id)
            if not settings.slice_notification or found is None or found.status == LOGGED:
                return None
            return f"Sent {found.label} to the printer. Open the {TAB_NAME} tab to log it."

    def state(self):
        """What the tab shows, newest first. JSON-compatible."""
        settings = self.settings()
        with self._lock:
            latest = self._latest()
            slices = [
                {
                    "id": s.id,
                    "name": s.name,
                    "slicedAt": s.sliced_at,
                    "destination": s.destination or "Not sent yet",
                    "status": s.status,
                    "message": s.message,
                    "error": s.error,
                    "claimUrl": s.claim_url,
                    "canLog": s.can_log,
                    "latest": s is latest,
                }
                for s in reversed(self._slices)
            ]
        return {"type": "state", "mode": settings.open_claim_page, "slices": slices}

    def page_ready(self, storage_dir):
        """The tab (re)loaded: no upload can be waiting on it, and copies of slices no longer
        listed can go (plugin storage: no prompt)."""
        with self._lock:
            for s in self._slices:
                if s.status == UPLOADING:
                    s.status = s.status_before
            keep = [s.copy for s in self._slices]
        staging.remove_unreferenced(storage_dir, keep)

    def request_log(self, slice_id, relog=False):
        """The Log button: step one, no I/O. Marks the slice uploading so the page can show it
        before step two (finish_log) blocks the UI thread."""
        with self._lock:
            found = self._find(slice_id)
            if found is None or not found.can_log:
                return {
                    "kind": "missing",
                    "id": slice_id,
                    "message": uploader.GONE,
                }
            if found.status == UPLOADING:
                return {"kind": "busy", "id": slice_id}
            if found.status == LOGGED and not relog:
                return {"kind": "already_logged", "id": slice_id, "url": found.claim_url}
            found.status_before = found.status
            found.status = UPLOADING
            found.message = "Uploading…"
            found.error = False
            return {"kind": "uploading", "id": slice_id}

    def finish_log(self, slice_id, storage_dir):
        """Step two, on the UI thread in an audited callback: read, upload, and in browser mode
        open the device page. Blocks until the upload ends (client.TIMEOUT_SECONDS at most)."""
        settings = self.settings()
        with self._lock:
            found = self._find(slice_id)
            if found is None or found.status != UPLOADING:
                return {"kind": "missing", "id": slice_id, "message": "Click Log to upload it."}
            source = found.copy if found.copy is not None else Path(found.path)
            job_fields = {
                "gcode_path": source,
                "output_name": triggers.upload_name(found.output_name, found.model_name),
                "host": found.host,
                "orca_version": found.orca_version,
                "preset_ids": None if found.preset_ids is None else dict(found.preset_ids),
            }
        storage_dir = Path(storage_dir)
        job = uploader.Job(
            **job_fields,
            data_dir=presets.data_dir_from_storage(storage_dir),
            debug_dir=storage_dir / "debug" if settings.debug else None,
            template=settings.template,
            api_base=settings.api_base,
            web_base=settings.web_base,
            install_id=self.install_id(storage_dir),
        )
        outcome = uploader.run(job, upload=self._upload)

        with self._lock:
            if outcome.ok:
                found.status = LOGGED
                found.setting_id = outcome.setting_id
                found.claim_url = outcome.claim_url
                found.message = ""
                found.error = False
                if self._send_notice == found.id:
                    self._send_notice = None
            else:
                found.status = found.status_before
                found.message = outcome.message
                found.error = True
        if not outcome.ok:
            return {
                "kind": "failed",
                "id": slice_id,
                "message": outcome.message,
                "declined": outcome.declined,
            }
        event = {
            "kind": "logged",
            "id": slice_id,
            "url": outcome.claim_url,
            "mode": settings.open_claim_page,
        }
        if settings.open_claim_page == "browser":
            event["browser"] = self.open_browser(storage_dir)
        return event

    def claim_url(self, slice_id):
        """The claim page of a logged slice, or None."""
        with self._lock:
            found = self._find(slice_id)
            return None if found is None or found.status != LOGGED else found.claim_url

    def log_latest(self, storage_dir):
        """The script: logs the latest slice, again if it was logged already (on purpose).
        None when there is no slice."""
        with self._lock:
            latest = self._latest()
            slice_id = None if latest is None else latest.id
        if slice_id is None:
            return None
        first = self.request_log(slice_id, relog=True)
        if first["kind"] != "uploading":
            return first
        return self.finish_log(slice_id, storage_dir)

    def open_browser(self, storage_dir):
        """The B2 fallback: the device page lists this install's uploads. Its URL never
        changes, so Orca asks once per install before it opens the browser."""
        url = browser.device_url(self.settings().web_base, self.install_id(storage_dir))
        ok, message = browser.open_page(url)
        return {"kind": "browser", "ok": ok, "message": message, "url": url}

    def install_id(self, storage_dir):
        if self._install_id is None:
            self._install_id = install.install_id(storage_dir)
        return self._install_id

    # --- internals (under the lock) ---------------------------------------------------------

    def _latest(self):
        return self._slices[-1] if self._slices else None

    def _find(self, slice_id):
        return next((s for s in self._slices if s.id == slice_id), None)


def _same(a, b):
    return triggers._same_path(a, b)


# The one instance the capabilities share; tests swap it. Creating it starts nothing.
shared = Controller()
