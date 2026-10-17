# /// script
# requires-python = ">=3.12"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# name = "E2E permission probe"
# description = "Test harness only: does audited I/O on purpose so Orca shows its prompts."
# author = "3D Print Log e2e"
# version = "1.0.0"
# ///
"""A throwaway variant for the permission spike (e2e.sh MODE=permissions); never shipped.

The real plugin keeps all I/O on a thread it starts at import, which Orca does not audit. This
one does the same kinds of I/O where Orca does audit them, so each prompt Orca can show for the
plugin's needs appears once, in this order:

  1. request_permissions(fs_read=[<plate temp folder>]) while the plugin registers.
  2. GCodeExportFinished (slicing thread, off the main thread): a urllib POST.
  3. SlicingJobComplete (main thread): starting a thread, then push_notification whose on_click
     does a urllib POST and webbrowser.open twice, with two different URLs.

Every attempt and its outcome is logged to the Python log as "[E2E permissions] ...". All
modules are imported up front, so no lazy stdlib import shows a prompt of its own.
"""

import sys
import threading
import urllib.error
import urllib.request
import webbrowser

import orca

PLATE_TEMP_FOLDER = "/tmp/orcaslicer_0"
POST_URL = "http://127.0.0.1:5555/e2e-permission-probe/"
BROWSER_URLS = (
    "http://localhost:4310/e2e-permission-probe/first",
    "http://localhost:4310/e2e-permission-probe/second",
)


def say(text):
    sys.stderr.write(f"[E2E permissions] {text}\n")
    sys.stderr.flush()


def attempt(what, fn):
    say(f"{what}: trying")  # the harness names a prompt's screenshot after the last "trying" line
    try:
        result = fn()
        say(f"{what}: OK {result!r}")
    except Exception as error:  # noqa: BLE001
        say(f"{what}: {type(error).__name__}: {error}")


def post(case):
    def run():
        request = urllib.request.Request(POST_URL + case, data=b"probe", method="POST")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code  # the fake OctoPrint answers 404: the request got through

    return run


def on_click():
    say("on_click: called on thread " + threading.current_thread().name)
    attempt("on_click POST", post("on-click"))
    attempt("on_click webbrowser.open #1", lambda: webbrowser.open(BROWSER_URLS[0]))
    attempt("on_click webbrowser.open #2 (different URL)", lambda: webbrowser.open(BROWSER_URLS[1]))
    return True


class PermissionProbe(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "E2E permission probe"

    def execute(self, ctx):
        return orca.ExecutionResult.skipped()

    def on_lifecycle_event(self, event, ctx):
        name = getattr(event, "name", "")
        thread = threading.current_thread().name
        if name == "GCodeExportFinished":
            say(f"GCodeExportFinished on {thread}")
            attempt("event-handler POST (off the main thread)", post("event-handler"))
        elif name == "SlicingJobComplete":
            say(f"SlicingJobComplete on {thread}")
            attempt(
                "thread start inside a callback",
                lambda: threading.Thread(target=lambda: None).start(),
            )
            ui = orca.host.ui
            # The same link text as the real plugin's, so the harness's template finds it.
            attempt(
                "push_notification",
                lambda: ui.push_notification(
                    ui.NotificationLevel.RegularNotificationLevel,
                    "E2E permission probe.",
                    hyper_text="Log to 3D Print Log",
                    on_click=on_click,
                ),
            )


@orca.plugin
class PermissionProbePlugin(orca.base):
    def register_capabilities(self):
        orca.request_permissions(fs_read=[PLATE_TEMP_FOLDER])
        orca.register_capability(PermissionProbe)
