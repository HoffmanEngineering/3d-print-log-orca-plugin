"""The "Log last slice to 3D Print Log" script (Plugins > Run): uploads the latest slice, again
if it was logged already. execute() runs on the UI thread inside the plugin's audit context, so
it does the same upload as the tab's Log button, with the same prompts, and then opens the claim
page the same way (an OrcaSlicer window, or the browser)."""

import logging

import orca

from printlog_orca import controller, page

_log = logging.getLogger("printlog_orca")


class LogLastSlice(orca.script.ScriptPluginCapabilityBase):
    def get_name(self):
        return "Log last slice to 3D Print Log"

    def execute(self):
        try:
            event = controller.shared.log_latest(orca.host.plugin.storage())
        except Exception as error:
            _log.exception("Log last slice failed")
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError, f"Could not upload the last slice: {error}"
            )
        page.refresh()
        if event is None:
            return orca.ExecutionResult.skipped("Nothing to log: slice a plate first.")
        if event["kind"] != "logged":
            return orca.ExecutionResult.failure(
                orca.PluginResult.RecoverableError, event.get("message", "Not logged.")
            )
        if event.get("mode") == "browser":
            opened = event.get("browser", {})
            if opened.get("ok"):
                return orca.ExecutionResult.success("Logged. Claim it in your browser.")
            return orca.ExecutionResult.success(f"Logged. {opened.get('message', '')}".strip())
        opened = page.open_claim_page(event.get("url"))
        if opened["ok"]:
            return orca.ExecutionResult.success(
                "Logged. The claim page opened in an OrcaSlicer window."
            )
        return orca.ExecutionResult.success(
            f"Logged. Open the {controller.TAB_NAME} tab and click Open claim page to claim it."
        )
