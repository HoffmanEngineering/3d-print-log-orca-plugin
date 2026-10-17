"""The "Log last slice to 3D Print Log" script (Plugins > Run): logs the latest slice, for a
send no event reported or a deliberate re-log. Runs on the UI thread; the upload itself runs on
the plugin's background thread."""

import logging

import orca

from printlog_orca import controller

_log = logging.getLogger("printlog_orca")


class LogLastSlice(orca.script.ScriptPluginCapabilityBase):
    def get_name(self):
        return "Log last slice to 3D Print Log"

    def execute(self):
        try:
            if controller.shared.log_last_slice():
                return orca.ExecutionResult.success(
                    "Uploading the last slice to 3D Print Log; the browser opens when it is done."
                )
            return orca.ExecutionResult.skipped(
                "Nothing to log: slice a plate first. Print-host uploads and Bambu sends are "
                "logged on their own; after an export, use the notification's link."
            )
        except Exception as error:
            _log.exception("Log last slice failed")
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError, f"Could not upload the last slice: {error}"
            )
