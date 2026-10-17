"""Reads, measures and uploads one slice, synchronously, when the user asks for it.

Called only from audited callbacks on Orca's UI thread: the 3D Print Log tab's on_message and the
"Log last slice" script. Orca therefore checks every file read and network call here and shows
its permission prompt at the moment the user clicked. The plugin starts no thread and does no I/O
anywhere Orca does not audit.

What Orca did not hand over (no hook ran for this slice) is read from the G-code itself: preset
names, slicer version, and a name from the first object when the slice has no model name.
"""

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from printlog_orca import (
    __version__,
    client,
    gcode_info,
    gcode_window,
    object_bounds,
    payload,
    presets,
    scrub,
)

DECLINED = (
    "Orca blocked the upload because permission was declined. Click Log again to be asked again."
)
GONE = (
    "This slice is gone: Orca removes it when the plate is sliced again or the project closes. "
    "Slice again to log it."
)

_log = logging.getLogger("printlog_orca")


@dataclass(frozen=True)
class Job:
    gcode_path: Path  # the plate's temp G-code, or the hook's copy in storage; never modified
    output_name: str  # "" names the upload after the first object
    host: str
    orca_version: str  # "" reads it from the G-code header
    preset_ids: dict | None  # None reads them from the G-code's config block
    data_dir: Path | None
    debug_dir: Path | None  # set when the debug option is on
    template: str
    api_base: str
    web_base: str
    install_id: str


@dataclass(frozen=True)
class Outcome:
    ok: bool
    message: str
    setting_id: str | None = None
    claim_url: str | None = None
    declined: bool = False  # Orca's prompt was answered No


def run(job, upload=client.upload):
    """Never raises: every failure becomes an Outcome with a message for the tab."""
    name = payload.file_name(job.output_name) or "the slice"
    try:
        return _run(job, upload)
    except PermissionError as error:
        _log.warning("Orca declined a permission for the upload of %s: %s", name, error)
        return Outcome(False, DECLINED, declined=True)
    except FileNotFoundError:
        _log.warning("The slice is gone (%s)", job.gcode_path)
        return Outcome(False, GONE)
    except Exception as error:
        _log.exception("Upload of %s failed", name)
        return Outcome(False, f"The upload failed: {error}")


def _run(job, upload):
    raw = gcode_window.read(job.gcode_path)
    text = scrub.scrub_text(raw)

    try:
        bounds = object_bounds.scan_file(job.gcode_path, _log.debug)
    except PermissionError:
        raise
    except Exception as error:
        _log.warning("Could not measure the objects in %s: %s", job.gcode_path, error)
        bounds = []

    output_name = job.output_name or gcode_info.output_name_from_objects(bounds)
    name = payload.file_name(output_name)
    orca_version = job.orca_version or gcode_info.slicer_version(raw)
    preset_ids = job.preset_ids if job.preset_ids is not None else gcode_info.preset_ids(raw)

    user_presets = []
    if job.data_dir is not None:
        try:
            user_presets = presets.find(job.data_dir, *_ids(preset_ids), _log.info)
        except PermissionError:
            raise
        except Exception as error:
            _log.warning("Could not read the user presets: %s", error)

    metadata = payload.metadata(
        plugin_version=__version__,
        slicer_version=orca_version,
        output_name=output_name,
        host=job.host,
        template=job.template,
        install_id=job.install_id,
        bounds=bounds,
        user_presets=user_presets,
    )
    body, content_type = payload.encode(text, metadata)
    result = upload(job.api_base, body, content_type, timeout=client.TIMEOUT_SECONDS)

    url = None
    if result.ok:
        url = client.new_print_url(job.web_base, orca_version, __version__, result.setting_id)
        _log.info("Uploaded %s to 3D Print Log (setting %s)", name, result.setting_id)
    else:
        _log.warning("Upload of %s to 3D Print Log failed: %s", name, result.message)

    if job.debug_dir is not None:
        _write_debug(job.debug_dir, metadata, len(text), result, url)

    if not result.ok:
        return Outcome(False, f"The upload failed: {result.message}")
    return Outcome(True, "Logged", setting_id=result.setting_id, claim_url=url)


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
