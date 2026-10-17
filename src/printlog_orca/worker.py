"""Reads, measures, uploads and opens the browser, off the thread Orca called the plugin on.

A thread the plugin starts has no Orca audit context: orca.host.* is not used here, and every
path it needs (data_dir, debug folder) comes in the Job.
"""

import json
import logging
import threading
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path

from printlog_orca import (
    __version__,
    client,
    gcode_window,
    object_bounds,
    payload,
    presets,
    scrub,
)

_log = logging.getLogger("printlog_orca")


@dataclass(frozen=True)
class Job:
    gcode_path: Path  # a private copy; deleted when the job ends
    output_name: str
    host: str
    orca_version: str
    preset_ids: dict
    data_dir: Path | None
    debug_dir: Path | None  # set when the debug option is on
    template: str
    api_base: str
    web_base: str
    open_browser: bool


def start(job, upload=client.upload, open_url=webbrowser.open):
    # Daemon, so a slow upload never holds up closing Orca.
    thread = threading.Thread(
        target=run, args=(job,), kwargs={"upload": upload, "open_url": open_url}, daemon=True
    )
    thread.name = "3D Print Log upload"
    thread.start()
    return thread


def run(job, upload=client.upload, open_url=webbrowser.open):
    """Never raises: every failure is logged."""
    try:
        _run(job, upload, open_url)
    except Exception:
        _log.exception("Upload of %s failed", payload.file_name(job.output_name))
    finally:
        Path(job.gcode_path).unlink(missing_ok=True)


def _run(job, upload, open_url):
    name = payload.file_name(job.output_name)
    text = scrub.scrub_text(gcode_window.read(job.gcode_path))

    try:
        bounds = object_bounds.scan_file(job.gcode_path, _log.debug)
    except Exception as error:
        _log.warning("Could not measure the objects in %s: %s", name, error)
        bounds = []

    user_presets = []
    if job.data_dir is not None:
        try:
            user_presets = presets.find(job.data_dir, *_ids(job.preset_ids), _log.info)
        except Exception as error:
            _log.warning("Could not read the user presets: %s", error)

    metadata = payload.metadata(
        plugin_version=__version__,
        slicer_version=job.orca_version,
        output_name=job.output_name,
        host=job.host,
        template=job.template,
        bounds=bounds,
        user_presets=user_presets,
    )
    body, content_type = payload.encode(text, metadata)
    result = upload(job.api_base, body, content_type, timeout=client.TIMEOUT_SECONDS)

    url = None
    if result.ok:
        url = client.new_print_url(job.web_base, job.orca_version, __version__, result.setting_id)
        _log.info("Uploaded %s to 3D Print Log (setting %s)", name, result.setting_id)
    else:
        _log.error("Upload of %s to 3D Print Log failed: %s", name, result.message)

    if job.debug_dir is not None:
        _write_debug(job.debug_dir, metadata, len(text), result, url)

    if url and job.open_browser:
        try:
            open_url(url)
        except Exception as error:
            _log.warning("Could not open the browser at %s: %s", url, error)


def _ids(preset_ids):
    return (
        preset_ids.get("print_settings_id"),
        preset_ids.get("filament_settings_id"),
        preset_ids.get("printer_settings_id"),
    )


def _write_debug(debug_dir, metadata, gcode_chars, result, url):
    """Like the uploader's --debug: what was sent (less the G-code) and what came back."""
    try:
        Path(debug_dir).mkdir(parents=True, exist_ok=True)
        record = {"metadata": metadata, "gcodeChars": gcode_chars, "result": asdict(result)}
        record["url"] = url
        (Path(debug_dir) / "last-upload.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as error:
        _log.warning("Could not write the debug record: %s", error)
