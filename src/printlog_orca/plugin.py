"""The OrcaSlicer side: the package class and the uploader capability.

Zero setup: once the plugin is enabled, Orca sends every lifecycle event to the uploader, for
every printer and profile. The uploader watches four:
- GCodeExportFinished (slicing thread): records the latest slice; a notification with a
  "Log to 3D Print Log" link follows from the plugin's background thread.
- UploadStarted (print-host queue thread): logs the latest slice.
- PrintJobStarted / PrintStateChanged (device threads): Bambu sends.
execute() is the optional psGCodePostProcess hook, for presets that list it: an export to file
then uploads by itself.

Nothing here may raise into Orca, fail the slice, start a thread, or call orca.host.ui: these
callbacks run on Orca's own threads, inside an audit context (see background.py).
"""

import logging
import sys

import orca

from printlog_orca import controller
from printlog_orca.script import LogLastSlice

_log = logging.getLogger("printlog_orca")


class _StderrHandler(logging.Handler):
    # Orca tees sys.stderr to <data_dir>/log/python_*.log, and may swap it after import.
    def emit(self, record):
        try:
            sys.stderr.write(self.format(record) + "\n")
        except Exception:
            self.handleError(record)


if not any(isinstance(h, _StderrHandler) for h in _log.handlers):
    _handler = _StderrHandler()
    _handler.setFormatter(logging.Formatter("[3D Print Log] %(levelname)s %(message)s"))
    _log.addHandler(_handler)
    _log.setLevel(logging.INFO)
    _log.propagate = False


def push_notification(text, hyper_text, on_click):
    """Called only from the plugin's background thread, never from an Orca callback: the call
    blocks until the UI thread shows it."""
    ui = orca.host.ui
    ui.push_notification(
        ui.NotificationLevel.RegularNotificationLevel,
        text,
        hyper_text=hyper_text,
        on_click=on_click,
    )


class PrintLogUploader(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "3D Print Log uploader"

    def get_default_config(self):
        return dict(controller.DEFAULT_CONFIG)

    def on_load(self):
        try:
            controller.shared.set_notifier(push_notification)
            controller.shared.set_active(True)
        except Exception:
            _log.exception("Could not start the 3D Print Log uploader")

    def on_unload(self):
        try:
            controller.shared.set_active(False)
        except Exception:
            _log.exception("Could not stop the 3D Print Log uploader")

    def execute(self, ctx):
        if ctx.step != orca.slicing.Step.psGCodePostProcess:
            return orca.ExecutionResult.skipped()
        try:
            message = controller.shared.on_gcode(
                storage_dir=orca.host.plugin.storage(),
                settings=controller.parse_settings(self.get_config()),
                host=ctx.host,
                gcode_path=ctx.gcode_path,
                output_name=ctx.output_name,
                orca_version=ctx.orca_version,
                preset_ids={key: ctx.config_value(key) for key in controller.PRESET_KEYS},
            )
            return orca.ExecutionResult.success(message)
        except Exception:
            _log.exception("Could not prepare the upload of %s", ctx.output_name)
            return orca.ExecutionResult.success("3D Print Log: not uploaded after an error")

    def on_lifecycle_event(self, event, ctx):
        # Every event of every kind reaches every capability, on the raising thread: return
        # fast for the ones that cannot start an upload.
        name = getattr(event, "name", "")
        if name not in controller.WATCHED_EVENTS:
            return
        try:
            self._on_event(name, ctx)
        except Exception:
            _log.exception("Could not handle %s", name)

    def _on_event(self, name, ctx):
        shared = controller.shared
        if name == controller.SLICE_EVENT:
            # ctx.msg is the temp G-code path, or "path\n<error>" with an Error code.
            if ctx.code != orca.LifecycleEvtCode.Ok:
                return
            shared.on_slice_finished(
                storage_dir=orca.host.plugin.storage(),
                settings=controller.parse_settings(self.get_config()),
                gcode_path=ctx.msg,
                model_name=ctx.name,
            )
        elif name == controller.UPLOAD_EVENT:
            shared.on_upload_started(
                settings=controller.parse_settings(self.get_config()), name=ctx.name
            )
        else:
            shared.on_bambu_event(
                settings=controller.parse_settings(self.get_config()),
                event_name=name,
                msg=ctx.msg,
                name=ctx.name,
            )


@orca.plugin
class PrintLogPlugin(orca.base):
    def register_capabilities(self):
        orca.register_capability(PrintLogUploader)
        orca.register_capability(LogLastSlice)


# Set at import too, in case Orca does not call on_load before the first event.
controller.shared.set_notifier(push_notification)
