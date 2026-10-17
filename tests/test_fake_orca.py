"""The fake keeps the real module's rules, so the plugin tests mean something."""

import orca
import pytest
from orca import _callback
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
