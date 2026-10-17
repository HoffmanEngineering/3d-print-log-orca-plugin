"""The capabilities as Orca loads and calls them, through the fake orca module: on Orca's
threads, inside its audit context, with orca.host.ui allowed only off Orca's callbacks."""

import json
import logging

import orca
import orca_driver
import pytest
from orca.host import ui

from printlog_orca import controller, plugin

UPLOADER = "3D Print Log uploader"
SCRIPT = "Log last slice to 3D Print Log"
IDS = {
    "print_settings_id": "0.20mm Standard @MK4S",
    "filament_settings_id": '"Prusa Generic PLA"',
    "printer_settings_id": "Prusa MK4S",
}
Event = orca.LifecycleEvent


class Jobs(list):
    def __call__(self, job):
        self.append(job)


@pytest.fixture
def env(tmp_path, monkeypatch, plugin_log):
    storage = tmp_path / "OrcaSlicer" / "orca_plugins" / "plugin_data" / "printlog"
    storage.mkdir(parents=True)
    orca_driver.reset(storage)
    jobs = Jobs()
    ctl = controller.Controller(start_job=jobs, notify_delay=0)
    monkeypatch.setattr(controller, "shared", ctl)
    capabilities = orca_driver.load()
    orca_driver.on_load(capabilities)
    temp = tmp_path / "temp"  # Orca's temp folder: outside data_dir
    temp.mkdir()
    yield capabilities, jobs, temp, ctl
    ctl.background.stop()
    # Nothing Orca would have refused (a PermissionError) or that raised inside a callback.
    errors = [r for r in plugin_log if r.levelno >= logging.ERROR]
    assert errors == [], [r.getMessage() for r in errors]


def gcode(temp, name=".1234.0.gcode"):
    path = temp / name
    path.write_text("; G-code\n")
    return path


def sliced(capabilities, temp, model="Benchy", code=None, name=".1234.0.gcode"):
    path = gcode(temp, name)
    msg = str(path) if code is None else f"{path}\nG-code export failed"
    ctx = orca.LifecycleEventContext(
        name=model, msg=msg, code=code or orca.LifecycleEvtCode.Ok, id="7"
    )
    orca_driver.fire(capabilities, Event.GCodeExportFinished, ctx)
    return path


def upload_started(capabilities, name=".OrcaSlicer.upload.1a2b-3c4d"):
    ctx = orca.LifecycleEventContext(name=name, code=orca.LifecycleEvtCode.Ok)
    orca_driver.fire(capabilities, Event.UploadStarted, ctx)


def post_process(capabilities, host, gcode_path, output_name, step=None):
    ctx = orca.slicing.SlicingPipelineContext(
        step=step or orca.slicing.Step.psGCodePostProcess,
        gcode_path=str(gcode_path),
        host=host,
        output_name=str(output_name),
        config=IDS,
    )
    return orca_driver.execute(capabilities[UPLOADER], ctx)


def settle(ctl):
    assert ctl.background.wait_idle()


def test_registers_the_two_capabilities(env):
    capabilities, *_ = env

    assert set(capabilities) == {UPLOADER, SCRIPT}
    assert capabilities[UPLOADER].get_type() == orca.PluginType.SlicingPipeline
    assert capabilities[SCRIPT].get_type() == orca.PluginType.Script


def test_default_config_is_a_dict_matching_the_spec(env):
    capabilities, *_ = env

    assert capabilities[UPLOADER].get_default_config() == controller.DEFAULT_CONFIG


def test_a_slice_notifies_from_the_plugin_thread_and_a_click_uploads_once(env):
    capabilities, jobs, temp, ctl = env

    path = sliced(capabilities, temp, model="Benchy")
    settle(ctl)

    (note,) = ui.notifications
    assert (note.text, note.hyper_text) == ("Sliced Benchy.", "Log to 3D Print Log")
    assert note.level == ui.NotificationLevel.RegularNotificationLevel
    assert note.thread_kind is None  # the plugin's own thread, not Orca's slicing thread
    assert jobs == []

    assert orca_driver.click(note) is True  # closes the notification
    assert orca_driver.click(note) is True

    (job,) = jobs
    assert (job.source_path, job.output_name, job.host) == (path, "Benchy.gcode", "File")


def test_ui_is_never_called_on_an_orca_callback_thread(env):
    capabilities, _, temp, ctl = env

    sliced(capabilities, temp)
    upload_started(capabilities)
    post_process(capabilities, "File", gcode(temp, ".1234.0.gcode.pp"), "out.gcode")
    settle(ctl)

    assert all(call[-1] is None for call in ui.calls)


def test_a_failed_slice_is_ignored(env):
    capabilities, jobs, temp, ctl = env

    sliced(capabilities, temp, code=orca.LifecycleEvtCode.Error)
    upload_started(capabilities)
    settle(ctl)

    assert ui.notifications == []
    assert jobs == []
    assert orca_driver.run_script(capabilities[SCRIPT]).status == orca.PluginResult.Skipped


def test_a_reslice_replaces_the_slice(env):
    capabilities, jobs, temp, ctl = env

    sliced(capabilities, temp, name="a.gcode")
    newer = sliced(capabilities, temp, name="b.gcode")
    upload_started(capabilities)

    assert [job.source_path for job in jobs] == [newer]


def test_a_print_host_upload_logs_the_slice_under_the_model_name(env):
    capabilities, jobs, temp, ctl = env
    path = sliced(capabilities, temp, model="Benchy")

    upload_started(capabilities)

    (job,) = jobs
    assert (job.source_path, job.host, job.output_name) == (path, "PrintHost", "Benchy.gcode")


def test_a_click_after_an_automatic_upload_is_a_no_op(env):
    capabilities, jobs, temp, ctl = env
    sliced(capabilities, temp)
    upload_started(capabilities)
    settle(ctl)

    # The notification may already be up if the upload came late; its click does nothing.
    ctl._notify(1)
    for note in ui.notifications:
        orca_driver.click(note)

    assert len(jobs) == 1


def test_the_hook_and_upload_started_give_one_upload(env):
    capabilities, jobs, temp, ctl = env
    sliced(capabilities, temp)
    source = gcode(temp, ".OrcaSlicer.upload.1a2b")

    result = post_process(capabilities, "OctoPrint", source, "Benchy_PLA_1h.gcode")
    upload_started(capabilities, source.name)

    assert result.status == orca.PluginResult.Success
    (job,) = jobs
    assert (job.host, job.output_name, job.preset_ids) == ("OctoPrint", "Benchy_PLA_1h.gcode", IDS)
    assert job.gcode_path.read_text() == "; G-code\n"


def test_the_hook_exports_to_file_by_itself(env):
    capabilities, jobs, temp, ctl = env
    sliced(capabilities, temp)

    post_process(capabilities, "File", gcode(temp, ".1234.0.gcode.pp"), temp / "out.gcode")
    settle(ctl)
    for note in ui.notifications:
        orca_driver.click(note)

    assert [job.output_name for job in jobs] == [str(temp / "out.gcode")]


def test_the_stored_config_reaches_the_job(env):
    capabilities, jobs, temp, _ = env
    capabilities[UPLOADER].save_config(json.dumps({"template": "full", "open_browser": False}))

    sliced(capabilities, temp)
    upload_started(capabilities)

    assert (jobs[0].template, jobs[0].open_browser) == ("full", False)


def test_slice_notification_false_shows_nothing(env):
    capabilities, jobs, temp, ctl = env
    capabilities[UPLOADER].save_config(json.dumps({"slice_notification": False}))

    sliced(capabilities, temp)
    settle(ctl)

    assert ui.notifications == []
    assert orca_driver.run_script(capabilities[SCRIPT]).status == orca.PluginResult.Success
    assert len(jobs) == 1


def test_a_bambu_printer_starting_logs_the_slice_under_the_model_name(env):
    capabilities, jobs, temp, _ = env
    sliced(capabilities, temp, model="Benchy")

    for status in ("PREPARE", "RUNNING"):
        ctx = orca.LifecycleEventContext(name="dev-1", msg=status)
        orca_driver.fire(capabilities, Event.PrintStateChanged, ctx)

    (job,) = jobs
    assert (job.host, job.output_name) == ("Bambu", "Benchy.gcode")


def test_a_multi_machine_send_logs_the_slice_under_the_project_name(env):
    capabilities, jobs, temp, _ = env
    sliced(capabilities, temp, model="")

    ctx = orca.LifecycleEventContext(
        name="Desk organizer", device_id="dev-1", source="task_manager"
    )
    orca_driver.fire(capabilities, Event.PrintJobStarted, ctx)

    assert jobs[0].output_name == "Desk organizer.gcode"


def test_other_pipeline_steps_are_skipped_quietly(env):
    capabilities, jobs, *_ = env

    result = post_process(capabilities, "", "", "", step=orca.slicing.Step.posPerimeters)

    assert result.status == orca.PluginResult.Skipped
    assert result.message == ""
    assert jobs == []


def test_unrelated_events_are_ignored_without_reading_storage(env, monkeypatch):
    capabilities, jobs, *_ = env

    def no_storage():
        raise AssertionError("storage() read for an unrelated event")

    monkeypatch.setattr(orca.host.plugin, "storage", no_storage)
    for event in (Event.ObjectTransformed, Event.SliceStarted, Event.UploadFinished):
        orca_driver.fire(capabilities, event, orca.LifecycleEventContext(name="Cube"))

    assert jobs == []


def test_the_script_uploads_the_last_slice(env):
    capabilities, jobs, temp, _ = env
    sliced(capabilities, temp)

    result = orca_driver.run_script(capabilities[SCRIPT])

    assert result.status == orca.PluginResult.Success
    assert len(jobs) == 1


def test_the_script_says_when_there_is_nothing_to_log(env):
    capabilities, jobs, *_ = env

    result = orca_driver.run_script(capabilities[SCRIPT])

    assert result.status == orca.PluginResult.Skipped
    assert "slice" in result.message.lower()
    assert jobs == []


def test_after_unload_a_click_does_nothing(env):
    capabilities, jobs, temp, ctl = env
    sliced(capabilities, temp)
    settle(ctl)

    orca_driver.unload(capabilities)
    orca_driver.click(ui.notifications[0])

    assert jobs == []


def test_a_failing_hook_never_raises_or_fails_the_slice(env, monkeypatch, plugin_log):
    capabilities, _, temp, _ = env

    def broken(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(controller.shared, "on_gcode", broken)

    result = post_process(capabilities, "File", gcode(temp, "a.pp"), "out.gcode")

    assert result.status == orca.PluginResult.Success
    assert "error" in result.message.lower()
    plugin_log.clear()


@pytest.mark.parametrize(
    "event", [Event.GCodeExportFinished, Event.UploadStarted, Event.PrintStateChanged]
)
def test_a_failing_event_handler_never_raises(env, monkeypatch, plugin_log, event):
    capabilities, *_ = env

    def broken(**kwargs):
        raise RuntimeError("boom")

    for name in ("on_slice_finished", "on_upload_started", "on_bambu_event"):
        monkeypatch.setattr(controller.shared, name, broken)

    orca_driver.fire(capabilities, event, orca.LifecycleEventContext(msg="RUNNING"))
    plugin_log.clear()


def test_a_failing_script_reports_instead_of_raising(env, monkeypatch, plugin_log):
    capabilities, *_ = env

    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(controller.shared, "log_last_slice", broken)

    result = orca_driver.run_script(capabilities[SCRIPT])

    assert result.status == orca.PluginResult.RecoverableError
    plugin_log.clear()


def test_orcas_file_is_left_in_place(env):
    capabilities, _, temp, _ = env
    path = gcode(temp, "plate_1.gcode.pp")

    post_process(capabilities, "File", path, "out.gcode")

    assert path.read_text() == "; G-code\n"


def test_push_notification_uses_the_binding(env):
    plugin.push_notification("text", "link", lambda: True)

    (note,) = ui.notifications
    assert (note.text, note.hyper_text, note.plugin_key) == ("text", "link", "")
