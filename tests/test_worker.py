import gzip
import json
import logging
import threading
from email import message_from_bytes
from email.policy import HTTP
from pathlib import Path

import pytest

from printlog_orca import worker
from printlog_orca.client import UploadResult

FIXTURES = Path(__file__).parent / "fixtures"
SETTING_ID = "6f9619ff-8b86-d011-b42d-00c04fc964ff"


class FakeApi:
    def __init__(self, result=None, error=None):
        self.result = result or UploadResult(True, 200, "uploaded", SETTING_ID)
        self.error = error
        self.calls = []

    def __call__(self, api_base, body, content_type, timeout=30):
        self.calls.append((api_base, body, content_type, timeout))
        if self.error:
            raise self.error
        return self.result

    def sent(self):
        _, body, content_type, _ = self.calls[0]
        message = message_from_bytes(
            f"Content-Type: {content_type}\r\n\r\n".encode() + body, policy=HTTP
        )
        parts = {p.get_param("name", header="content-disposition"): p for p in message.iter_parts()}
        text = gzip.decompress(parts["gcode"].get_payload(decode=True)).decode("utf-8")
        return text, json.loads(parts["metadata"].get_payload(decode=True))


@pytest.fixture
def gcode(tmp_path):
    path = tmp_path / "storage" / "jobs" / "job.gcode"
    path.parent.mkdir(parents=True)
    data = gzip.decompress((FIXTURES / "orcaslicer-2.4.0-benchy-x16.gcode.gz").read_bytes())
    path.write_bytes(data + b"; printhost_apikey = hunter2\n")
    return path


def make_job(gcode, **overrides):
    fields = {
        "gcode_path": gcode,
        "output_name": "C:\\Users\\chris\\Desktop\\benchy x16.gcode",
        "host": "File",
        "orca_version": "2.5.0-alpha",
        "preset_ids": {
            "print_settings_id": "0.10mm FAST DETAIL @MK4S 0.4 - 3DPrintLog",
            "filament_settings_id": '"Prusa Generic PLA @MK4S"',
            "printer_settings_id": "Prusa MK4S 0.4 nozzle",
        },
        "data_dir": None,
        "debug_dir": None,
        "template": "default",
        "api_base": "https://api.example",
        "web_base": "https://www.example",
        "open_browser": True,
    }
    fields.update(overrides)
    return worker.Job(**fields)


def run(job, api=None, opened=None):
    api = api or FakeApi()
    opened = [] if opened is None else opened
    worker.run(job, upload=api, open_url=opened.append)
    return api, opened


def test_uploads_the_scrubbed_window_and_the_metadata(gcode):
    api, _ = run(make_job(gcode))

    text, metadata = api.sent()
    assert text.startswith("; HEADER_BLOCK_START")
    assert "hunter2" not in text
    assert "; printhost_apikey =" in text
    assert metadata["pluginVersion"] == worker.__version__
    assert metadata["slicerVersion"] == "2.5.0-alpha"
    assert metadata["outputName"] == "benchy x16.gcode"
    assert metadata["host"] == "File"
    assert metadata["template"] == "default"
    assert len(metadata["models"]) == 16
    assert metadata["userPresets"] == []
    assert api.calls[0][0] == "https://api.example"
    assert api.calls[0][3] == 30


def test_sends_the_user_presets_found_under_data_dir(gcode, tmp_path):
    data_dir = tmp_path / "OrcaSlicer"
    folder = data_dir / "user" / "42" / "process"
    folder.mkdir(parents=True)
    name = "0.10mm FAST DETAIL @MK4S 0.4 - 3DPrintLog"
    (folder / f"{name}.json").write_text('{"name": "x", "wall_loops": "3"}', encoding="utf-8")

    api, _ = run(make_job(gcode, data_dir=data_dir))

    _, metadata = api.sent()
    assert metadata["userPresets"] == [
        {"kind": "process", "name": name, "json": '{"name":"x","wall_loops":"3"}'}
    ]


def test_opens_the_new_print_page_after_a_successful_upload(gcode):
    _, opened = run(make_job(gcode))

    assert opened == [
        "https://www.example/prints/new/cura?cura_version=2.5.0-alpha"
        f"&plugin_version={worker.__version__}&settingId={SETTING_ID}"
    ]


def test_does_not_open_the_browser_when_turned_off(gcode):
    _, opened = run(make_job(gcode, open_browser=False))

    assert opened == []


def test_a_failed_upload_is_logged_and_opens_nothing(gcode, caplog):
    api = FakeApi(UploadResult(False, 429, "HTTP 429, too many uploads"))

    with caplog.at_level(logging.INFO, logger="printlog_orca"):
        _, opened = run(make_job(gcode), api)

    assert opened == []
    assert "HTTP 429, too many uploads" in caplog.text


def test_the_working_copy_is_deleted_whatever_happens(gcode):
    run(make_job(gcode))
    assert not gcode.exists()


def test_the_working_copy_is_deleted_when_the_upload_raises(gcode, caplog):
    with caplog.at_level(logging.ERROR, logger="printlog_orca"):
        run(make_job(gcode), FakeApi(error=RuntimeError("boom")))

    assert not gcode.exists()
    assert "boom" in caplog.text


def test_a_missing_file_is_logged_not_raised(tmp_path, caplog):
    with caplog.at_level(logging.ERROR, logger="printlog_orca"):
        api, _ = run(make_job(tmp_path / "gone.gcode"))

    assert api.calls == []
    assert "gone.gcode" in caplog.text


def test_a_failed_scan_still_uploads_without_models(gcode, monkeypatch):
    def broken(path, log):
        raise OSError("disk on fire")

    monkeypatch.setattr(worker.object_bounds, "scan_file", broken)

    api, _ = run(make_job(gcode))

    assert api.sent()[1]["models"] == []


def test_a_failed_preset_lookup_still_uploads_without_presets(gcode, monkeypatch, tmp_path):
    def broken(*args):
        raise RuntimeError("bad preset")

    monkeypatch.setattr(worker.presets, "find", broken)

    api, _ = run(make_job(gcode, data_dir=tmp_path))

    assert api.sent()[1]["userPresets"] == []


def test_a_failed_browser_open_is_logged(gcode, caplog):
    def broken(url):
        raise OSError("no browser")

    with caplog.at_level(logging.WARNING, logger="printlog_orca"):
        worker.run(make_job(gcode), upload=FakeApi(), open_url=broken)

    assert "no browser" in caplog.text


def test_debug_writes_the_metadata_and_the_response(gcode, tmp_path):
    debug_dir = tmp_path / "storage" / "debug"

    run(make_job(gcode, debug_dir=debug_dir))

    record = json.loads((debug_dir / "last-upload.json").read_text(encoding="utf-8"))
    assert record["metadata"]["outputName"] == "benchy x16.gcode"
    assert record["result"]["status"] == 200
    assert record["result"]["setting_id"] == SETTING_ID
    assert record["gcodeChars"] > 0
    assert record["url"].endswith(SETTING_ID)


def test_start_runs_the_job_on_a_background_thread(gcode):
    done = threading.Event()
    threads = []

    def upload(*args, **kwargs):
        threads.append(threading.current_thread())
        done.set()
        return UploadResult(False, 500, "server error")

    thread = worker.start(make_job(gcode), upload=upload, open_url=lambda url: None)
    thread.join(10)

    assert done.is_set()
    assert threads[0] is not threading.main_thread()
    assert thread.daemon
