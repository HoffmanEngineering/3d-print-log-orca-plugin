"""The fake keeps the real module's rules, so the plugin tests mean something."""

import socket
import sys
import threading
import webbrowser

import orca
import orca_driver
import pytest
from orca import _audit, _callback
from orca.host import plugin as host_plugin
from orca.host import ui


def test_execution_result_factories_match_the_binding():
    assert orca.ExecutionResult.success().status == orca.PluginResult.Success
    assert orca.ExecutionResult.skipped("why").message == "why"
    failure = orca.ExecutionResult.failure(orca.PluginResult.RecoverableError, "no", "d")
    assert (failure.status, failure.message, failure.data) == (
        orca.PluginResult.RecoverableError,
        "no",
        "d",
    )


def test_storage_is_only_reachable_from_a_callback(tmp_path):
    host_plugin.storage_dir = tmp_path
    with pytest.raises(RuntimeError):
        host_plugin.storage()
    with _callback.inside("slicing"):
        assert host_plugin.storage() == str(tmp_path)


def test_register_capability_rejects_a_non_capability():
    with pytest.raises(ValueError):
        orca.register_capability(object)


def test_lifecycle_event_names_match_the_binding():
    assert orca.LifecycleEvent.PrintStateChanged.name == "PrintStateChanged"
    assert orca.slicing.Step.psGCodePostProcess.name == "psGCodePostProcess"


def test_ui_calls_are_allowed_only_on_the_ui_thread_inside_a_callback():
    ui.reset()
    with _callback.inside("ui"):
        ui.push_notification(ui.NotificationLevel.RegularNotificationLevel, "from a callback")
    with pytest.raises(AssertionError):
        ui.push_notification(ui.NotificationLevel.RegularNotificationLevel, "from a thread")

    assert [n.thread_kind for n in ui.notifications] == ["ui"]


@pytest.mark.parametrize("kind", ["slicing", "upload", "job", "load"])
def test_ui_calls_fail_inside_worker_thread_callbacks(kind):
    with _callback.inside(kind), pytest.raises(AssertionError):
        ui.push_notification(ui.NotificationLevel.RegularNotificationLevel, "hi")


def test_a_thread_start_in_a_worker_callback_is_denied():
    with _callback.inside("slicing"), pytest.raises(PermissionError):
        threading.Thread(target=lambda: None).start()
    assert _audit.prompts == [("thread", "thread", "slicing", False)]


def test_a_thread_start_on_the_ui_thread_prompts():
    with _callback.inside("ui"):
        thread = threading.Thread(target=lambda: None)
        thread.start()
    thread.join()
    assert _audit.prompts == [("thread", "thread", "ui", True)]


def test_files_outside_data_dir_and_scoped_roots_prompt(tmp_path):
    data_dir = tmp_path / "OrcaSlicer"
    storage = data_dir / "orca_plugins" / "plugin_data" / "key"
    storage.mkdir(parents=True)
    outside = tmp_path / "Temp" / "plate.gcode"
    outside.parent.mkdir()
    outside.write_text("G28\n")
    _audit.data_dir = data_dir

    with _callback.inside("slicing"):
        (storage / "ok.txt").write_text("fine")
        with pytest.raises(PermissionError):
            outside.read_text()
    with _callback.inside("slicing", roots=[outside.parent]):
        assert outside.read_text() == "G28\n"
    with _callback.inside("ui"):
        assert outside.read_text() == "G28\n"  # modal prompt, Yes
    assert outside.read_text() == "G28\n"  # outside a callback: not audited

    assert [(c, k, yes) for c, _, k, yes in _audit.prompts] == [
        ("file", "slicing", False),
        ("file", "ui", True),
    ]


def test_a_declined_prompt_raises_permission_error(tmp_path):
    outside = tmp_path / "plate.gcode"
    outside.write_text("G28\n")
    _audit.answer = lambda category, target: False

    with _callback.inside("ui"), pytest.raises(PermissionError):
        outside.read_text()


def test_network_off_the_ui_thread_is_denied():
    with _callback.inside("upload"), pytest.raises(PermissionError):
        request("https://api.example/x")
    with _callback.inside("ui"):
        request("https://api.example/x")
    with _callback.inside("upload"):
        request("https://api.example/x")  # the URL was granted on the UI thread
        with pytest.raises(PermissionError):
            socket.socket().close()  # socket.__new__ has no target: never persisted


def test_a_yes_covers_the_rest_of_that_call():
    with _callback.inside("ui"):
        request("https://api.example/x")
        socket.socket().close()
    assert [c for c, *_ in _audit.prompts] == ["network"]


def test_plugin_io_outside_a_callback_is_recorded_when_strict(tmp_path):
    _audit.strict = True
    path = tmp_path / "x.txt"
    path.write_text("from a test")  # not plugin code
    assert _audit.unaudited == []


def test_the_browser_stand_in_runs_through_the_process_audit():
    with _callback.inside("ui"):
        assert webbrowser.open("https://www.example/a") is True
    _audit.answer = lambda category, target: False
    with _callback.inside("ui"):
        assert webbrowser.open("https://www.example/b") is False
    assert [(c, t, yes) for c, t, _, yes in _audit.prompts] == [
        ("process", "https://www.example/a", True),
        ("process", "https://www.example/b", False),
    ]


class _Page(orca.pages.PagesPluginCapabilityBase):
    def get_name(self):
        return "Page"

    def get_ui(self):
        return "<p>hi</p>"

    def on_message(self, data):
        assert _callback.on_ui_thread()
        self.post_message({"echo": data})


def test_a_page_gets_its_messages_on_the_ui_thread_and_replies_after_the_call():
    capability = _Page()
    capability.post_message({"dropped": True})  # before the page is built

    page = orca_driver.Page(capability)

    assert page.html == "<p>hi</p>"
    assert page.send({"type": "ready"}) == [{"echo": {"type": "ready"}}]
    assert capability.get_type() == orca.PluginType.Pages


def request(url):
    """The event OpenerDirector.open raises before it connects."""
    sys.audit("urllib.Request", url, None, {}, "POST")
