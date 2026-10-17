"""The OrcaSlicer side: the package class and the uploader capability.

Zero setup: once the plugin is enabled, Orca sends every lifecycle event to the uploader, for
every printer and profile. Events only record what happened to a slice; nothing uploads until the
user clicks Log in the 3D Print Log tab (page.py) or runs the script (script.py), so Orca's
permission prompts come at that click. The uploader watches:
- GCodeExportFinished (slicing thread): records the slice. No I/O.
- SlicingJobComplete (UI thread): names a slice whose event had no name (plate.py), and the
  "Sliced <name>" notification.
- UploadStarted (print-host queue thread), PrintJobStarted / SendJobStarted (job worker): marks
  the latest slice sent. No I/O.
- PrintStateChanged (UI thread): shows the notice a Bambu send left.
execute() is the optional psGCodePostProcess hook, for presets that list it: it copies the
exported file into plugin storage (Orca grants its folder for the call), so the tab logs that
exact file. No network.

Nothing here may raise into Orca, fail the slice, start a thread, or call orca.host.ui off the UI
thread.
"""

import logging
import sys

import orca

from printlog_orca import controller, page, plate
from printlog_orca.page import PrintLogPage
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


def push_notification(text):
    """Only from a callback on the UI thread (orca.host.ui blocks until the UI thread runs it).
    Plain text, no link: a link's on_click runs outside any audit context, so it must not do
    the upload, and Python cannot bring the tab forward."""
    ui = orca.host.ui
    ui.push_notification(ui.NotificationLevel.RegularNotificationLevel, text)


class PrintLogUploader(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "3D Print Log uploader"

    def get_default_config(self):
        return dict(controller.DEFAULT_CONFIG)

    def on_load(self):
        controller.shared.set_config_source(self.get_config)

    def execute(self, ctx):
        if ctx.step != orca.slicing.Step.psGCodePostProcess:
            return orca.ExecutionResult.skipped()
        try:
            controller.shared.set_config_source(self.get_config)
            message = controller.shared.on_gcode(
                storage_dir=orca.host.plugin.storage(),
                host=ctx.host,
                gcode_path=ctx.gcode_path,
                output_name=ctx.output_name,
                orca_version=ctx.orca_version,
                preset_ids={key: ctx.config_value(key) for key in controller.PRESET_KEYS},
            )
            return orca.ExecutionResult.success(message)
        except Exception:
            _log.exception("Could not keep a copy of %s", ctx.output_name)
            return orca.ExecutionResult.success("3D Print Log: no copy kept after an error")

    def on_lifecycle_event(self, event, ctx):
        # Every event of every kind reaches every capability, on the raising thread: return
        # fast for the ones the plugin does not watch.
        name = getattr(event, "name", "")
        if name not in controller.WATCHED_EVENTS:
            return
        try:
            controller.shared.set_config_source(self.get_config)
            self._on_event(name, ctx)
        except Exception:
            _log.exception("Could not handle %s", name)

    def _on_event(self, name, ctx):
        shared = controller.shared
        if name == controller.SLICE_EVENT:
            # ctx.msg is the temp G-code path, or "path\n<error>" with an Error code.
            if ctx.code == orca.LifecycleEvtCode.Ok:
                shared.record_slice(ctx.msg, ctx.name)
        elif name == controller.SLICE_DONE_EVENT:
            if ctx.code == orca.LifecycleEvtCode.Ok:
                # GCodeExportFinished's ctx.name is empty for an STL project: ask the project.
                display_name = plate.display_name() if shared.needs_display_name() else ""
                self._notify(shared.slicing_done(display_name))
        elif name == controller.STATE_EVENT:
            self._notify(shared.take_send_notice())
        else:
            shared.mark_sent(name, ctx.name)

    @staticmethod
    def _notify(text):
        """On the UI thread (SlicingJobComplete, PrintStateChanged)."""
        if not text:
            return
        page.refresh()
        try:
            push_notification(text)
        except Exception as error:
            _log.warning("Could not show the 3D Print Log notification: %s", error)


@orca.plugin
class PrintLogPlugin(orca.base):
    def register_capabilities(self):
        orca.register_capability(PrintLogUploader)
        orca.register_capability(PrintLogPage)
        orca.register_capability(LogLastSlice)
