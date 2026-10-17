"""The "3D Print Log" tab (orca.pages): the recent slices, each with a Log button.

get_ui() and on_message() run on Orca's UI thread inside the plugin's audit context
(PagesPluginCapabilityTrampoline.hpp), so the upload done here gets Orca's real permission
prompts, modal, right after the click. Message protocol (page -> plugin, every message carries
the page's token):

    ready          the page loaded (first show, reload, or Back from the claim page)
    refresh        the page is visible again, or its 5 s poll
    log {id, relog}  step one of Log: no I/O. The plugin marks the slice uploading and replies;
                   the page paints "Uploading…" and only then sends
    upload {id}    step two: read the G-code, upload (blocks the UI thread), reply with the
                   result; in tab mode the page then navigates itself to the claim page
    open_browser   open this install's device page in the system browser

plugin -> page: {type: "state", mode, slices: [...], event?: {kind, ...}} after every message
(post_message goes through CallAfter, so it arrives once on_message has returned).

The token keeps other pages out: after the tab navigates to the claim page, that remote page
(and any page it links to) gets window.orca too and could post here, but it cannot read the token
from this page.
"""

import logging
import uuid

import orca

from printlog_orca import controller, page_html

TOKEN = uuid.uuid4().hex

_log = logging.getLogger("printlog_orca")
_built = []  # the page capability whose tab was built, if any


def refresh():
    """Pushes the current list to the tab, from a UI-thread callback. Dropped by Orca before the
    tab is built, and ignored by a remote page shown in it."""
    for capability in _built:
        try:
            capability.post_message(controller.shared.state())
        except Exception as error:
            _log.warning("Could not update the 3D Print Log tab: %s", error)


class PrintLogPage(orca.pages.PagesPluginCapabilityBase):
    def get_name(self):
        return controller.TAB_NAME

    def get_ui(self):
        _built[:] = [self]
        return page_html.render(TOKEN)

    def on_message(self, data):
        try:
            self._handle(data)
        except Exception:
            _log.exception("The 3D Print Log tab's message failed")
            self._post(
                {"kind": "failed", "id": None, "message": "Something went wrong; see the log."}
            )

    def _handle(self, data):
        if not isinstance(data, dict) or data.get("token") != TOKEN:
            _log.debug("Ignored a message without this page's token")
            return
        kind = data.get("type")
        shared = controller.shared
        if kind == "ready":
            shared.page_ready(orca.host.plugin.storage())
            self._post()
        elif kind == "refresh":
            self._post()
        elif kind == "log":
            self._post(shared.request_log(_slice_id(data), relog=data.get("relog") is True))
        elif kind == "upload":
            self._post(shared.finish_log(_slice_id(data), orca.host.plugin.storage()))
        elif kind == "open_browser":
            self._post(shared.open_browser(orca.host.plugin.storage()))
        else:
            _log.debug("Ignored a tab message of type %r", kind)

    def _post(self, event=None):
        message = controller.shared.state()
        if event is not None:
            message["event"] = event
        self.post_message(message)


def _slice_id(data):
    value = data.get("id")
    return value if isinstance(value, int) and not isinstance(value, bool) else None
