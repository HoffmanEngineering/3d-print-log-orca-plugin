"""The fake keeps the real module's rules, so the plugin tests mean something."""

import threading

import orca
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
    with _callback.inside("event"):
        assert host_plugin.storage() == str(tmp_path)


def test_ui_calls_fail_on_the_slicing_thread():
    with _callback.inside("slicing"), pytest.raises(AssertionError):
        ui.message("hi")


def test_register_capability_rejects_a_non_capability():
    with pytest.raises(ValueError):
        orca.register_capability(object)


def test_lifecycle_event_names_match_the_binding():
    assert orca.LifecycleEvent.PrintStateChanged.name == "PrintStateChanged"
    assert orca.slicing.Step.psGCodePostProcess.name == "psGCodePostProcess"


def test_ui_calls_are_allowed_off_callbacks_and_on_the_ui_thread():
    ui.reset()
    ui.message("from a plugin thread")
    with _callback.inside("ui"):
        ui.message("from a script")

    assert [call[-1] for call in ui.calls] == [None, "ui"]


@pytest.mark.parametrize("kind", ["slicing", "upload", "device", "event"])
def test_ui_calls_fail_inside_orca_thread_callbacks(kind):
    with _callback.inside(kind), pytest.raises(AssertionError):
        ui.push_notification(ui.NotificationLevel.RegularNotificationLevel, "hi")


def test_starting_a_thread_inside_a_callback_is_refused():
    with _callback.inside("event"), pytest.raises(PermissionError):
        threading.Thread(target=lambda: None).start()
    thread = threading.Thread(target=lambda: None)
    thread.start()  # outside a callback: not audited
    thread.join()


def test_files_outside_data_dir_and_scoped_roots_are_refused(tmp_path):
    data_dir = tmp_path / "OrcaSlicer"
    storage = data_dir / "orca_plugins" / "plugin_data" / "key"
    storage.mkdir(parents=True)
    outside = tmp_path / "Temp" / "plate.gcode"
    outside.parent.mkdir()
    outside.write_text("G28\n")
    _audit.data_dir = data_dir
    try:
        with _callback.inside("slicing"):
            (storage / "ok.txt").write_text("fine")
            with pytest.raises(PermissionError):
                outside.read_text()
        with _callback.inside("slicing", roots=[outside.parent]):
            assert outside.read_text() == "G28\n"
        assert outside.read_text() == "G28\n"  # outside a callback
    finally:
        _audit.data_dir = None
