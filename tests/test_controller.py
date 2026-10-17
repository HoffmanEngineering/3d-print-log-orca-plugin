"""The trigger table end to end, without Orca: what each hook call and event leads to."""

import json

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


IDS = {"print_settings_id": "P", "filament_settings_id": '"F"', "printer_settings_id": "M"}


@pytest.fixture
def env(tmp_path):
    storage = tmp_path / "OrcaSlicer" / "orca_plugins" / "plugin_data" / "printlog"
    storage.mkdir(parents=True)
    temp = tmp_path / "temp"
    temp.mkdir()
    jobs, clock = Jobs(), Clock()
    return storage, temp, jobs, clock, Controller(start_job=jobs, clock=clock)


SETTINGS = controller.parse_settings("{}")


def gcode(temp, name="plate_1.gcode", text="; G-code\n"):
    path = temp / name
    path.write_text(text)
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


def bambu_slice(ctl, storage, temp, text="; G-code\n"):
    path = gcode(temp, text=text)
    hook(ctl, storage, "File", path, path)
    return path


def event(ctl, name, msg="", settings=SETTINGS):
    return ctl.on_event(settings=settings, event_name=name, msg=msg)


def test_export_to_file_uploads_once(env):
    storage, temp, jobs, _, ctl = env
    working = gcode(temp, "plate_1.gcode.pp")

    hook(ctl, storage, "File", working, temp / "out" / "benchy.gcode")

    (job,) = jobs
    assert job.gcode_path.read_text() == "; G-code\n"
    assert job.gcode_path != working
    assert job.host == "File"
    assert job.output_name == str(temp / "out" / "benchy.gcode")
    assert job.orca_version == "2.5.0-alpha"
    assert job.preset_ids == IDS
    assert job.data_dir == storage.parents[2]
    assert job.debug_dir is None
    assert (job.template, job.api_base, job.web_base, job.open_browser) == (
        "default",
        "https://api.3dprintlog.com",
        "https://www.3dprintlog.com",
        True,
    )


def test_upload_to_a_print_host_uploads_once(env):
    storage, temp, jobs, _, ctl = env

    hook(ctl, storage, "OctoPrint", gcode(temp), "benchy.gcode")

    assert [job.host for job in jobs] == ["OctoPrint"]


def test_a_bambu_slice_alone_uploads_nothing(env):
    storage, temp, jobs, _, ctl = env

    bambu_slice(ctl, storage, temp)

    assert jobs == []


def test_a_multi_machine_send_uploads_the_staged_slice_once(env):
    storage, temp, jobs, _, ctl = env
    bambu_slice(ctl, storage, temp)

    event(ctl, "PrintJobStarted")
    event(ctl, "PrintJobStarted")

    assert len(jobs) == 1
    assert jobs[0].gcode_path.read_text() == "; G-code\n"


def test_a_printer_starting_soon_after_the_slice_uploads_it_once(env):
    storage, temp, jobs, clock, ctl = env
    bambu_slice(ctl, storage, temp)
    clock.now += 120

    event(ctl, "PrintStateChanged", "PREPARE")
    event(ctl, "PrintStateChanged", "RUNNING")

    assert len(jobs) == 1


def test_a_printer_starting_after_the_window_uploads_nothing(env):
    storage, temp, jobs, clock, ctl = env
    bambu_slice(ctl, storage, temp)
    clock.now += 31 * 60

    event(ctl, "PrintStateChanged", "RUNNING")

    assert jobs == []


def test_the_window_follows_the_config(env):
    storage, temp, jobs, clock, ctl = env
    settings = controller.parse_settings('{"bambu_send_window_minutes": 60}')
    bambu_slice(ctl, storage, temp)
    clock.now += 45 * 60

    event(ctl, "PrintStateChanged", "RUNNING", settings)

    assert len(jobs) == 1


def test_a_reslice_replaces_the_staged_slice(env):
    storage, temp, jobs, _, ctl = env
    bambu_slice(ctl, storage, temp, "; first\n")
    bambu_slice(ctl, storage, temp, "; second\n")

    event(ctl, "PrintJobStarted")

    assert [job.gcode_path.read_text() for job in jobs] == ["; second\n"]


def test_an_export_after_staging_uploads_the_export_and_clears_the_staged_slice(env):
    storage, temp, jobs, _, ctl = env
    bambu_slice(ctl, storage, temp)

    hook(ctl, storage, "File", gcode(temp, "plate_1.gcode.pp"), temp / "benchy.gcode")
    event(ctl, "PrintJobStarted")

    assert len(jobs) == 1
    assert jobs[0].output_name == str(temp / "benchy.gcode")


def test_events_before_any_slice_do_nothing(env):
    *_, jobs, _, ctl = env

    event(ctl, "PrintJobStarted")
    event(ctl, "PrintStateChanged", "RUNNING")

    assert jobs == []


def test_log_last_slice_uploads_the_staged_slice_once(env):
    storage, temp, jobs, _, ctl = env
    bambu_slice(ctl, storage, temp)

    assert ctl.log_last_slice() is True
    assert ctl.log_last_slice() is False
    assert len(jobs) == 1


def test_log_last_slice_uses_the_uploaders_settings(env):
    storage, temp, jobs, _, ctl = env
    settings = controller.parse_settings('{"api_base": "https://api.test", "open_browser": false}')
    path = gcode(temp)
    hook(ctl, storage, "File", path, path, settings)

    ctl.log_last_slice()

    assert (jobs[0].api_base, jobs[0].open_browser) == ("https://api.test", False)


def test_log_last_slice_with_nothing_staged(env):
    assert env[4].log_last_slice() is False


def test_debug_sends_a_debug_folder_in_plugin_storage(env):
    storage, temp, jobs, _, ctl = env

    hook(
        ctl,
        storage,
        "OctoPrint",
        gcode(temp),
        "x.gcode",
        controller.parse_settings('{"debug": true}'),
    )

    assert jobs[0].debug_dir == storage / "debug"


def test_parse_settings_defaults():
    settings = controller.parse_settings("{}")

    assert settings == controller.Settings(
        template="default",
        open_browser=True,
        api_base="https://api.3dprintlog.com",
        web_base="https://www.3dprintlog.com",
        bambu_send_window_minutes=30,
        debug=False,
    )


def test_default_config_matches_the_spec():
    assert DEFAULT_CONFIG == {
        "template": "default",
        "open_browser": True,
        "api_base": "https://api.3dprintlog.com",
        "web_base": "https://www.3dprintlog.com",
        "bambu_send_window_minutes": 30,
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
            "debug": True,
        }
    )

    assert controller.parse_settings(raw) == controller.Settings(
        "full", False, "http://localhost:5000", "https://localhost:4200", 5, True
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
        '{"debug": 1}',
        None,
    ],
)
def test_parse_settings_falls_back_to_defaults_for_bad_values(raw):
    assert controller.parse_settings(raw) == controller.parse_settings("{}")
