"""orca.host.ui (host/PluginHostUi.cpp).

Every call is marshaled to the UI thread and waited for (run_on_ui_blocking). From a slicing hook
or an event on the slicing thread that can deadlock, so the fake allows UI calls only where the
plugin should make them: on a thread it started itself, or on the UI thread. Calls are recorded
with the kind of thread that made them.
"""

import threading
from dataclasses import dataclass

from orca import _callback

_lock = threading.Lock()
calls: list[tuple] = []
notifications: list["Notification"] = []
pushed = threading.Condition(_lock)


@dataclass
class Notification:
    level: object
    text: str
    hyper_text: str
    on_click: object
    thread_kind: str | None
    # PluginHostUi binds on_click to PluginAuditManager::current_plugin(): empty off a callback, so
    # Orca cannot disable the callback when the plugin unloads (the plugin has to).
    plugin_key: str


def _check(name):
    kind = _callback.current()
    if kind not in (None, "ui"):
        raise AssertionError(f"orca.host.ui.{name} called from a {kind!r} callback")
    return kind


def message(text, title="OrcaSlicer", buttons="ok", icon="info"):
    kind = _check("message")
    with _lock:
        calls.append(("message", text, title, buttons, icon, kind))
    return "ok"


def push_notification(notification_level, text, hyper_text="", on_click=None):
    kind = _check("push_notification")
    key = "" if kind is None else "plugin"
    note = Notification(notification_level, text, hyper_text, on_click, kind, key)
    with pushed:
        calls.append(("push_notification", notification_level, text, hyper_text, kind))
        notifications.append(note)
        pushed.notify_all()


def wait_for_notifications(count, timeout=10):
    """Blocks until `count` notifications were pushed (they come from the plugin's own thread)."""
    with pushed:
        return pushed.wait_for(lambda: len(notifications) >= count, timeout)


def reset():
    with _lock:
        calls.clear()
        notifications.clear()


class NotificationLevel:
    RegularNotificationLevel = "RegularNotificationLevel"
    PrintInfoNotificationLevel = "PrintInfoNotificationLevel"
    ImportantNotificationLevel = "ImportantNotificationLevel"
    WarningNotificationLevel = "WarningNotificationLevel"
    ErrorNotificationLevel = "ErrorNotificationLevel"


# pybind11's export_values() also puts the values on the module.
RegularNotificationLevel = NotificationLevel.RegularNotificationLevel
ImportantNotificationLevel = NotificationLevel.ImportantNotificationLevel
