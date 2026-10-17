"""orca.host.ui (host/PluginHostUi.cpp).

message() and push_notification() are marshaled to the UI thread and waited for
(run_on_ui_blocking). From the slicing thread that can deadlock, and from a plugin-started thread
it works only because that thread is unaudited, so the fake allows UI calls only on the UI thread
inside a callback. Calls are recorded with the kind of thread that made them.

create_window() does not wait: it reserves the window's id and defers building the WebDialog to a
CallAfter, so it returns a live handle at once and is safe from a page's on_message (the WebKit
backends deliver script messages from inside the native callback). The WebDialog loads `html`
with SetPage; there is no URL argument. close() is CallAfter-marshaled too. on_message and
on_close run later on the UI thread outside any audit context (no plugin scope), so the plugin
must do no I/O there. The fake queues the deferred work in `pending`; orca_driver runs it once
the callback that queued it has returned, the way wx's event loop would.
"""

import json
from dataclasses import dataclass, field

from orca import _callback

calls: list[tuple] = []
notifications: list["Notification"] = []
windows: list["Window"] = []
pending: list = []  # CallAfter work: run by run_pending(), outside any callback

WINDOW_MODELESS = 0
WINDOW_MODAL = 1


@dataclass
class Notification:
    level: object
    text: str
    hyper_text: str
    on_click: object
    thread_kind: str | None
    # PluginHostUi binds on_click to PluginAuditManager::current_plugin(): the plugin's key inside
    # a callback, empty outside one.
    plugin_key: str


@dataclass
class Window:
    """A WebDialog the plugin opened, with the UiWindow handle's methods. `posted` holds what
    post() delivered to the page."""

    html: str
    title: str
    width: int
    height: int
    on_message: object
    on_close: object
    style: int
    thread_kind: str | None
    posted: list = field(default_factory=list)
    open: bool = True
    closed_by: str = ""

    @property
    def id(self):
        return windows.index(self) + 1

    def is_open(self):
        return self.open

    def post(self, data):
        payload = json.loads(json.dumps(data))
        pending.append(lambda: self.posted.append(payload) if self.open else None)

    def close(self):
        """UiWindow.close(): Close() through CallAfter, which fires on_close."""
        pending.append(lambda: self._finish("plugin"))

    def _finish(self, by):
        # WebDialog::fire_close: once, only while the window is alive.
        if not self.open:
            return
        self.open = False
        self.closed_by = by
        if self.on_close is not None:
            with _callback.inside(None):
                self.on_close()


def _check(name):
    kind = _callback.current()
    if kind != "ui":
        raise AssertionError(f"orca.host.ui.{name} called from {kind or 'outside a callback'!r}")
    return kind


def message(text, title="OrcaSlicer", buttons="ok", icon="info"):
    kind = _check("message")
    calls.append(("message", text, title, buttons, icon, kind))
    return "ok"


def push_notification(notification_level, text, hyper_text="", on_click=None):
    kind = _check("push_notification")
    calls.append(("push_notification", notification_level, text, hyper_text, kind))
    notifications.append(
        Notification(notification_level, text, hyper_text, on_click, kind, "plugin")
    )


def create_window(
    html,
    title="OrcaSlicer",
    width=820,
    height=600,
    on_message=None,
    on_close=None,
    style=WINDOW_MODELESS,
    on_submit=None,
):
    kind = _check("create_window")
    if not isinstance(html, str):
        raise TypeError("create_window(): html must be a str")
    if style & ~WINDOW_MODAL:
        raise ValueError("unsupported orca.host.ui.create_window style flags")
    window = Window(
        html=html,
        title=title,
        width=width if width > 0 else 820,
        height=height if height > 0 else 600,
        on_message=on_message,
        on_close=on_close,
        style=style,
        thread_kind=kind,
    )
    windows.append(window)
    calls.append(("create_window", title, kind))
    return window


def run_pending():
    """The event loop runs the queued CallAfter work, outside any plugin callback."""
    while pending:
        pending.pop(0)()


def reset():
    calls.clear()
    notifications.clear()
    windows.clear()
    pending.clear()


class NotificationLevel:
    RegularNotificationLevel = "RegularNotificationLevel"
    PrintInfoNotificationLevel = "PrintInfoNotificationLevel"
    ImportantNotificationLevel = "ImportantNotificationLevel"
    WarningNotificationLevel = "WarningNotificationLevel"
    ErrorNotificationLevel = "ErrorNotificationLevel"


# pybind11's export_values() also puts the values on the module.
RegularNotificationLevel = NotificationLevel.RegularNotificationLevel
ImportantNotificationLevel = NotificationLevel.ImportantNotificationLevel
