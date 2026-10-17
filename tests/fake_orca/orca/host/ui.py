"""orca.host.ui (host/PluginHostUi.cpp).

Every call is marshaled to the UI thread and waited for (run_on_ui_blocking). From the slicing
thread that can deadlock, and from a plugin-started thread it works only because that thread is
unaudited, so the fake allows UI calls only on the UI thread inside a callback. Calls are
recorded with the kind of thread that made them.
"""

from dataclasses import dataclass

from orca import _callback

calls: list[tuple] = []
notifications: list["Notification"] = []


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


def reset():
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
