"""The trigger table end to end, without Orca: what each slice, event and hook call leads to."""

import json
import threading

import pytest

from printlog_orca import controller
from printlog_orca.controller import DEFAULT_CONFIG, Controller


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


class Jobs(list):
    def __call__(self, job):
        self.append(job)


class Pushes(list):
    """Stands in for orca.host.ui.push_notification; records the thread it was called on."""

    def __call__(self, text, hyper_text, on_click):
        self.append((text, hyper_text, on_click, threading.current_thread().name))


IDS = {"print_settings_id": "P", "filament_settings_id": '"F"', "printer_settings_id": "M"}
SETTINGS = controller.parse_settings("{}")


@pytest.fixture
def env(tmp_path):
    storage = tmp_path / "OrcaSlicer" / "orca_plugins" / "plugin_data" / "printlog"
    storage.mkdir(parents=True)
    temp = tmp_path / "temp"
    temp.mkdir()
    jobs, clock, pushes = Jobs(), Clock(), Pushes()
    ctl = Controller(start_job=jobs, clock=clock, notify_delay=0)
    ctl.set_notifier(pushes)
    yield storage, temp, jobs, clock, ctl, pushes
    ctl.background.stop()


def gcode(temp, name=".1234.0.gcode", text="; G-code\n"):
    path = temp / name
    path.write_text(text)
    return path


def slice_(ctl, storage, temp, model="Benchy", settings=SETTINGS, name=".1234.0.gcode"):
    path = gcode(temp, name)
    ctl.on_slice_finished(
        storage_dir=storage, settings=settings, gcode_path=str(path), model_name=model
    )
    return path


def hook(ctl, storage, host, gcode_path, output_name, settings=SETTINGS):
    return ctl.on_gcode(
        storage_dir=storage,
        settings=settings,
        host=host,
        gcode_path=str(gcode_path),
        output_name=str(output_name),
        orca_version="2.5.0-alpha",
        preset_ids=IDS,
    )


def bambu(ctl, event_name, msg="", name="", settings=SETTINGS):
    ctl.on_bambu_event(settings=settings, event_name=event_name, msg=msg, name=name)


def settled(ctl):
    assert ctl.background.wait_idle()


# --- a slice and its notification -------------------------------------------------------------


def test_a_slice_alone_uploads_nothing(env):
    storage, temp, jobs, _, ctl, _ = env

    slice_(ctl, storage, temp)
    settled(ctl)

    assert jobs == []


def test_a_slice_shows_one_notification_from_the_background_thread(env):
    storage, temp, _, _, ctl, pushes = env

    slice_(ctl, storage, temp, model="Benchy")
    settled(ctl)

    ((text, link, _, thread),) = pushes
    assert (text, link) == ("Sliced Benchy.", "Log to 3D Print Log")
    assert thread == "3D Print Log"


def test_a_slice_without_a_model_name_says_the_plate(env):
    storage, temp, _, _, ctl, pushes = env

    slice_(ctl, storage, temp, model="")
    settled(ctl)

    assert pushes[0][0] == "Sliced the plate."


def test_clicking_uploads_the_latest_slice_once(env):
    storage, temp, jobs, _, ctl, pushes = env
    path = slice_(ctl, storage, temp, model="Benchy")
    settled(ctl)
    on_click = pushes[0][2]

    assert on_click() is True
    assert on_click() is True

    (job,) = jobs
    assert job.source_path == path
    assert job.gcode_path.parent == storage / "uploads" / "jobs"
    assert job.gcode_path != path
    assert job.output_name == "Benchy.gcode"
    assert job.host == "File"
    assert (job.orca_version, job.preset_ids) == ("", None)  # read from the G-code
    assert job.data_dir == storage.parents[2]


def test_a_click_without_a_model_name_lets_the_worker_name_it(env):
    storage, temp, jobs, _, ctl, _ = env
    slice_(ctl, storage, temp, model="")

    ctl.click()

    assert jobs[0].output_name == ""


def test_a_reslice_replaces_the_latest_slice(env):
    storage, temp, jobs, _, ctl, pushes = env
    gate = threading.Event()
    ctl.background.submit(gate.wait)  # both slices land within the notification delay
    slice_(ctl, storage, temp, model="First", name="a.gcode")
    slice_(ctl, storage, temp, model="Second", name="b.gcode")
    gate.set()
    settled(ctl)

    pushes[-1][2]()

    assert [job.source_path.name for job in jobs] == ["b.gcode"]
    assert [p[0] for p in pushes] == ["Sliced Second."]  # the burst gave one notification


def test_slice_notification_false_shows_nothing_but_the_script_still_works(env):
    storage, temp, jobs, _, ctl, pushes = env
    quiet = controller.parse_settings('{"slice_notification": false}')

    slice_(ctl, storage, temp, settings=quiet)
    settled(ctl)

    assert pushes == []
    assert ctl.log_last_slice() is True
    assert len(jobs) == 1


def test_no_notification_once_the_slice_was_logged(env):
    storage, temp, jobs, _, ctl, pushes = env
    gate = threading.Event()
    ctl.background.submit(gate.wait)  # the upload event lands before the notification is due

    slice_(ctl, storage, temp)
    ctl.on_upload_started(settings=SETTINGS, name=".OrcaSlicer.upload.1a2b")
    gate.set()
    settled(ctl)

    assert len(jobs) == 1
    assert pushes == []


def test_a_click_after_unload_does_nothing(env):
    storage, temp, jobs, _, ctl, pushes = env
    slice_(ctl, storage, temp)
    settled(ctl)

    ctl.set_active(False)

    assert pushes[0][2]() is True
    assert jobs == []


def test_a_failing_notification_is_logged_not_raised(env):
    storage, temp, _, _, ctl, _ = env

    def broken(*args):
        raise RuntimeError("OrcaSlicer application is not initialized")

    ctl.set_notifier(broken)
    slice_(ctl, storage, temp)

    settled(ctl)


# --- print hosts ------------------------------------------------------------------------------


def test_a_print_host_upload_logs_the_latest_slice_under_the_model_name(env):
    storage, temp, jobs, _, ctl, _ = env
    path = slice_(ctl, storage, temp, model="Benchy")

    # Orca names the upload's source file, not the name the print host gets.
    ctl.on_upload_started(settings=SETTINGS, name=".OrcaSlicer.upload.1a2b-3c4d-5e6f-7a8b")

    (job,) = jobs
    assert (job.source_path, job.host, job.output_name) == (path, "PrintHost", "Benchy.gcode")


def test_a_click_after_a_print_host_upload_is_a_no_op(env, plugin_log):
    storage, temp, jobs, _, ctl, _ = env
    slice_(ctl, storage, temp)
    ctl.on_upload_started(settings=SETTINGS, name=".OrcaSlicer.upload.x")

    ctl.click()

    assert len(jobs) == 1
    assert any("logged already" in r.getMessage() for r in plugin_log)


def test_an_upload_before_any_slice_does_nothing(env):
    *_, jobs, _, ctl, _ = env

    ctl.on_upload_started(settings=SETTINGS, name="x")

    assert jobs == []


# --- the optional hook ------------------------------------------------------------------------


def test_hook_export_to_file_uploads_its_copy_and_stands_for_the_slice(env):
    storage, temp, jobs, _, ctl, _ = env
    slice_(ctl, storage, temp)
    working = gcode(temp, ".1234.0.gcode.pp")

    message = hook(ctl, storage, "File", working, temp / "out" / "benchy.gcode")
    ctl.click()

    assert message == "3D Print Log: uploading"
    (job,) = jobs
    assert job.source_path is None
    assert job.gcode_path.read_text() == "; G-code\n"
    assert job.gcode_path != working
    assert (job.host, job.output_name) == ("File", str(temp / "out" / "benchy.gcode"))
    assert (job.orca_version, job.preset_ids) == ("2.5.0-alpha", IDS)
    assert job.debug_dir is None
    assert (job.template, job.api_base, job.web_base, job.open_browser) == (
        "default",
        "https://api.3dprintlog.com",
        "https://www.3dprintlog.com",
        True,
    )


def test_hook_and_upload_started_for_one_print_host_upload_give_one_upload(env):
    storage, temp, jobs, _, ctl, _ = env
    slice_(ctl, storage, temp)
    source = gcode(temp, ".OrcaSlicer.upload.1a2b")

    hook(ctl, storage, "OctoPrint", source, "Benchy_PLA_1h.gcode")
    ctl.on_upload_started(settings=SETTINGS, name=source.name)

    (job,) = jobs
    assert (job.host, job.output_name) == ("OctoPrint", "Benchy_PLA_1h.gcode")


def test_hook_bambu_after_slice_run_does_nothing(env):
    storage, temp, jobs, _, ctl, _ = env
    path = slice_(ctl, storage, temp)

    assert hook(ctl, storage, "File", path, path) == ""
    assert jobs == []


def test_hook_export_after_a_click_does_not_log_the_slice_twice(env):
    storage, temp, jobs, _, ctl, _ = env
    slice_(ctl, storage, temp)
    ctl.click()

    message = hook(ctl, storage, "File", gcode(temp, ".1234.0.gcode.pp"), temp / "b.gcode")

    assert len(jobs) == 1
    assert "logged already" in message


def test_a_failed_hook_copy_leaves_the_slice_loggable(env):
    storage, temp, jobs, _, ctl, _ = env
    slice_(ctl, storage, temp)

    with pytest.raises(OSError):
        hook(ctl, storage, "File", temp / "missing.pp", temp / "b.gcode")
    ctl.click()

    assert [job.host for job in jobs] == ["File"]
    assert jobs[0].source_path is not None


def test_hook_without_a_recorded_slice_still_uploads(env):
    storage, temp, jobs, _, ctl, _ = env

    hook(ctl, storage, "OctoPrint", gcode(temp), "benchy.gcode")

    assert [job.host for job in jobs] == ["OctoPrint"]


def test_debug_sends_a_debug_folder_in_plugin_storage(env):
    storage, temp, jobs, _, ctl, _ = env

    hook(
        ctl,
        storage,
        "OctoPrint",
        gcode(temp),
        "x.gcode",
        controller.parse_settings('{"debug": true}'),
    )

    assert jobs[0].debug_dir == storage / "debug"


# --- Bambu ------------------------------------------------------------------------------------


def test_a_multi_machine_send_logs_the_slice_under_the_project_name(env):
    storage, temp, jobs, _, ctl, _ = env
    slice_(ctl, storage, temp, model="Benchy")

    bambu(ctl, "PrintJobStarted", name="Desk organizer")
    bambu(ctl, "PrintJobStarted", name="Desk organizer")

    (job,) = jobs
    assert (job.host, job.output_name) == ("Bambu", "Desk organizer.gcode")


def test_a_printer_starting_soon_after_the_slice_logs_it_once_under_the_model_name(env):
    storage, temp, jobs, clock, ctl, _ = env
    slice_(ctl, storage, temp, model="Benchy")
    clock.now += 120

    bambu(ctl, "PrintStateChanged", "PREPARE", name="dev-1")
    bambu(ctl, "PrintStateChanged", "RUNNING", name="dev-1")

    (job,) = jobs
    assert job.output_name == "Benchy.gcode"  # not plate_1.gcode, not the device id


def test_a_printer_starting_after_the_window_logs_nothing(env):
    storage, temp, jobs, clock, ctl, _ = env
    slice_(ctl, storage, temp)
    clock.now += 31 * 60

    bambu(ctl, "PrintStateChanged", "RUNNING")

    assert jobs == []


def test_the_window_follows_the_config(env):
    storage, temp, jobs, clock, ctl, _ = env
    slice_(ctl, storage, temp)
    clock.now += 45 * 60

    bambu(
        ctl,
        "PrintStateChanged",
        "RUNNING",
        settings=controller.parse_settings('{"bambu_send_window_minutes": 60}'),
    )

    assert len(jobs) == 1


def test_a_click_after_a_bambu_send_is_a_no_op(env):
    storage, temp, jobs, _, ctl, _ = env
    slice_(ctl, storage, temp)
    bambu(ctl, "PrintStateChanged", "RUNNING")

    ctl.click()

    assert len(jobs) == 1


def test_a_bambu_start_after_a_click_is_a_no_op(env):
    storage, temp, jobs, _, ctl, _ = env
    slice_(ctl, storage, temp)
    ctl.click()

    bambu(ctl, "PrintStateChanged", "RUNNING")

    assert [job.host for job in jobs] == ["File"]


def test_events_before_any_slice_do_nothing(env):
    *_, jobs, _, ctl, _ = env

    bambu(ctl, "PrintJobStarted")
    bambu(ctl, "PrintStateChanged", "RUNNING")

    assert jobs == []


def test_a_new_slice_can_be_logged_again(env):
    storage, temp, jobs, _, ctl, _ = env
    slice_(ctl, storage, temp)
    ctl.click()

    slice_(ctl, storage, temp)
    ctl.click()

    assert len(jobs) == 2


# --- the script -------------------------------------------------------------------------------


def test_log_last_slice_uploads_the_latest_slice(env):
    storage, temp, jobs, _, ctl, _ = env
    path = slice_(ctl, storage, temp, model="Benchy")

    assert ctl.log_last_slice() is True

    (job,) = jobs
    assert (job.source_path, job.output_name) == (path, "Benchy.gcode")


def test_log_last_slice_can_log_a_slice_again_on_purpose(env):
    storage, temp, jobs, _, ctl, _ = env
    slice_(ctl, storage, temp)
    ctl.click()

    assert ctl.log_last_slice() is True
    assert len(jobs) == 2


def test_log_last_slice_uses_the_latest_settings(env):
    storage, temp, jobs, _, ctl, _ = env
    settings = controller.parse_settings('{"api_base": "https://api.test", "open_browser": false}')
    slice_(ctl, storage, temp, settings=settings)

    ctl.log_last_slice()

    assert (jobs[0].api_base, jobs[0].open_browser) == ("https://api.test", False)


def test_log_last_slice_with_nothing_sliced(env):
    assert env[4].log_last_slice() is False


# --- config -----------------------------------------------------------------------------------


def test_parse_settings_defaults():
    settings = controller.parse_settings("{}")

    assert settings == controller.Settings(
        template="default",
        open_browser=True,
        api_base="https://api.3dprintlog.com",
        web_base="https://www.3dprintlog.com",
        bambu_send_window_minutes=30,
        slice_notification=True,
        debug=False,
    )


def test_default_config_matches_the_spec():
    assert DEFAULT_CONFIG == {
        "template": "default",
        "open_browser": True,
        "api_base": "https://api.3dprintlog.com",
        "web_base": "https://www.3dprintlog.com",
        "bambu_send_window_minutes": 30,
        "slice_notification": True,
        "debug": False,
    }


def test_parse_settings_reads_valid_values():
    raw = json.dumps(
        {
            "template": "full",
            "open_browser": False,
            "api_base": "http://localhost:5000/",
            "web_base": "https://localhost:4200",
            "bambu_send_window_minutes": 5,
            "slice_notification": False,
            "debug": True,
        }
    )

    assert controller.parse_settings(raw) == controller.Settings(
        "full", False, "http://localhost:5000", "https://localhost:4200", 5, False, True
    )


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        "null",
        '{"template": "fancy"}',
        '{"open_browser": "yes"}',
        '{"api_base": "ftp://x"}',
        '{"api_base": 5}',
        '{"bambu_send_window_minutes": -1}',
        '{"bambu_send_window_minutes": "30"}',
        '{"bambu_send_window_minutes": true}',
        '{"slice_notification": "no"}',
        '{"debug": 1}',
        None,
    ],
)
def test_parse_settings_falls_back_to_defaults_for_bad_values(raw):
    assert controller.parse_settings(raw) == controller.parse_settings("{}")
