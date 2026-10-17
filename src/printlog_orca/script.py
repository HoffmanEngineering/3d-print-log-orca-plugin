"""The "Log last slice to 3D Print Log" script (Plugins > Run), for a Bambu send that no
lifecycle event reported. Runs on the UI thread; the upload itself runs in the background."""

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
                "Nothing to log: slice a plate for a Bambu printer first. Exports and print-host "
                "uploads are logged on their own."
            )
        except Exception as error:
            _log.exception("Log last slice failed")
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError, f"Could not upload the last slice: {error}"
            )
