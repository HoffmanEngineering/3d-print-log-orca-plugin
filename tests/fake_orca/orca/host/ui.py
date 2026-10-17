"""orca.host.ui (host/PluginHostUi.cpp). Calls are recorded; from a slicing hook they fail,
because in Orca they can deadlock there."""

from orca import _callback

calls: list[tuple] = []


def _record(name, *args):
    if _callback.current() == "slicing":
        raise AssertionError(f"orca.host.ui.{name} called from the slicing thread")
    calls.append((name, *args))


def message(text, title="OrcaSlicer", buttons="ok", icon="info"):
    _record("message", text, title, buttons, icon)
    return "ok"


def push_notification(notification_level, text, hyper_text="", on_click=None):
    _record("push_notification", notification_level, text, hyper_text)


class NotificationLevel:
    RegularNotificationLevel = "RegularNotificationLevel"
    WarningNotificationLevel = "WarningNotificationLevel"
    ErrorNotificationLevel = "ErrorNotificationLevel"
