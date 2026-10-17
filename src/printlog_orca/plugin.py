"""The OrcaSlicer side: the package class and the uploader capability.

Orca calls execute() on the slicing worker thread, at every pipeline step of a process preset
that lists this capability; only psGCodePostProcess matters here. Nothing in this file may
raise into Orca, fail the slice, or call orca.host.ui (it can deadlock on that thread).
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


class PrintLogUploader(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "3D Print Log uploader"

    def get_default_config(self):
        return dict(controller.DEFAULT_CONFIG)

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
            controller.shared.on_event(
                settings=controller.parse_settings(self.get_config()),
                event_name=name,
                msg=ctx.msg,
            )
        except Exception:
            _log.exception("Could not handle %s", name)


@orca.plugin
class PrintLogPlugin(orca.base):
    def register_capabilities(self):
        orca.register_capability(PrintLogUploader)
        orca.register_capability(LogLastSlice)
