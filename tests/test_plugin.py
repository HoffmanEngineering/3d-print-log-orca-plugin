"""The capabilities as Orca loads and calls them, through the fake orca module."""

import json

import orca
import orca_driver
import pytest
from orca.host import ui

from printlog_orca import controller

UPLOADER = "3D Print Log uploader"
SCRIPT = "Log last slice to 3D Print Log"
IDS = {
    "print_settings_id": "0.20mm Standard @MK4S",
    "filament_settings_id": '"Prusa Generic PLA"',
    "printer_settings_id": "Prusa MK4S",
}


class Jobs(list):
    def __call__(self, job):
        self.append(job)


@pytest.fixture
def env(tmp_path, monkeypatch):
    storage = tmp_path / "OrcaSlicer" / "orca_plugins" / "plugin_data" / "printlog"
    storage.mkdir(parents=True)
    orca_driver.reset(storage)
    jobs = Jobs()
    monkeypatch.setattr(controller, "shared", controller.Controller(start_job=jobs))
    capabilities = orca_driver.load()
    temp = tmp_path / "temp"
    temp.mkdir()
    return capabilities, jobs, temp


def gcode(temp, name="plate_1.gcode"):
    path = temp / name
    path.write_text("; G-code\n")
    return path


def post_process(capabilities, host, gcode_path, output_name, step=None):
    ctx = orca.slicing.SlicingPipelineContext(
        step=step or orca.slicing.Step.psGCodePostProcess,
        gcode_path=str(gcode_path),
        host=host,
        output_name=str(output_name),
        config=IDS,
    )
    return orca_driver.execute(capabilities[UPLOADER], ctx)


def test_registers_the_two_capabilities(env):
    capabilities, _, _ = env

    assert set(capabilities) == {UPLOADER, SCRIPT}
    assert capabilities[UPLOADER].get_type() == orca.PluginType.SlicingPipeline
    assert capabilities[SCRIPT].get_type() == orca.PluginType.Script


def test_default_config_is_a_dict_matching_the_spec(env):
    capabilities, _, _ = env

    assert capabilities[UPLOADER].get_default_config() == controller.DEFAULT_CONFIG


def test_export_to_file_starts_one_upload_and_succeeds(env):
    capabilities, jobs, temp = env

    result = post_process(capabilities, "File", gcode(temp, "plate_1.gcode.pp"), "out.gcode")

    assert result.status == orca.PluginResult.Success
    assert len(jobs) == 1
    assert jobs[0].preset_ids == IDS
    assert jobs[0].orca_version == "2.5.0-alpha"


def test_the_stored_config_reaches_the_job(env):
    capabilities, jobs, temp = env
    capabilities[UPLOADER].save_config(json.dumps({"template": "full", "open_browser": False}))

    post_process(capabilities, "OctoPrint", gcode(temp), "out.gcode")

    assert (jobs[0].template, jobs[0].open_browser) == ("full", False)


def test_other_pipeline_steps_are_skipped_quietly(env):
    capabilities, jobs, temp = env

    result = post_process(capabilities, "", "", "", step=orca.slicing.Step.posPerimeters)

    assert result.status == orca.PluginResult.Skipped
    assert result.message == ""
    assert jobs == []


def test_a_bambu_slice_then_a_printer_start_uploads_once(env):
    capabilities, jobs, temp = env
    path = gcode(temp)
    post_process(capabilities, "File", path, path)
    assert jobs == []

    for status in ("PREPARE", "RUNNING"):
        ctx = orca.LifecycleEventContext(name="dev-1", msg=status)
        orca_driver.fire(capabilities, orca.LifecycleEvent.PrintStateChanged, ctx)

    assert len(jobs) == 1


def test_a_multi_machine_send_uploads_the_staged_slice(env):
    capabilities, jobs, temp = env
    path = gcode(temp)
    post_process(capabilities, "File", path, path)

    ctx = orca.LifecycleEventContext(name="project", device_id="dev-1", source="task_manager")
    orca_driver.fire(capabilities, orca.LifecycleEvent.PrintJobStarted, ctx)

    assert len(jobs) == 1


def test_unrelated_events_are_ignored_without_reading_storage(env, monkeypatch):
    capabilities, jobs, _ = env

    def no_storage():
        raise AssertionError("storage() read for an unrelated event")

    monkeypatch.setattr(orca.host.plugin, "storage", no_storage)
    ctx = orca.LifecycleEventContext(name="Cube")
    orca_driver.fire(capabilities, orca.LifecycleEvent.ObjectTransformed, ctx)

    assert jobs == []


def test_the_script_uploads_the_last_staged_slice(env):
    capabilities, jobs, temp = env
    path = gcode(temp)
    post_process(capabilities, "File", path, path)

    result = orca_driver.run_script(capabilities[SCRIPT])

    assert result.status == orca.PluginResult.Success
    assert len(jobs) == 1


def test_the_script_says_when_there_is_nothing_to_log(env):
    capabilities, jobs, _ = env

    result = orca_driver.run_script(capabilities[SCRIPT])

    assert result.status == orca.PluginResult.Skipped
    assert "slice" in result.message.lower()
    assert jobs == []


def test_a_failing_hook_never_raises_or_fails_the_slice(env, monkeypatch):
    capabilities, _, temp = env

    def broken(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(controller.shared, "on_gcode", broken)

    result = post_process(capabilities, "File", gcode(temp, "a.pp"), "out.gcode")

    assert result.status == orca.PluginResult.Success
    assert "error" in result.message.lower()


def test_a_failing_event_handler_never_raises(env, monkeypatch):
    capabilities, _, _ = env

    def broken(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(controller.shared, "on_event", broken)

    ctx = orca.LifecycleEventContext(msg="RUNNING")
    orca_driver.fire(capabilities, orca.LifecycleEvent.PrintStateChanged, ctx)


def test_a_failing_script_reports_instead_of_raising(env, monkeypatch):
    capabilities, _, _ = env

    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(controller.shared, "log_last_slice", broken)

    result = orca_driver.run_script(capabilities[SCRIPT])

    assert result.status == orca.PluginResult.RecoverableError


def test_the_hook_never_touches_the_ui(env):
    capabilities, _, temp = env
    path = gcode(temp)

    post_process(capabilities, "File", path, path)
    post_process(capabilities, "File", gcode(temp, "b.pp"), "out.gcode")

    assert ui.calls == []


def test_orcas_file_is_left_in_place(env):
    capabilities, _, temp = env
    path = gcode(temp, "plate_1.gcode.pp")

    post_process(capabilities, "File", path, "out.gcode")

    assert path.read_text() == "; G-code\n"
