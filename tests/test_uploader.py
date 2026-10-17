import gzip
import json
from email import message_from_bytes
from email.policy import HTTP
from pathlib import Path

import pytest
from orca import _audit, _callback

from printlog_orca import uploader
from printlog_orca.client import UploadResult

FIXTURES = Path(__file__).parent / "fixtures"
SETTING_ID = "6f9619ff-8b86-d011-b42d-00c04fc964ff"
INSTALL_ID = "0123456789abcdef0123456789abcdef"


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
    path = tmp_path / "Temp" / "orcaslicer_model" / ".1234.0.gcode"
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
        "install_id": INSTALL_ID,
    }
    fields.update(overrides)
    return uploader.Job(**fields)


def run(job, api=None):
    api = api or FakeApi()
    return api, uploader.run(job, upload=api)


def test_uploads_the_scrubbed_window_and_the_metadata(gcode):
    api, outcome = run(make_job(gcode))

    text, metadata = api.sent()
    assert text.startswith("; HEADER_BLOCK_START")
    assert "hunter2" not in text
    assert "; printhost_apikey =" in text
    assert metadata["pluginVersion"] == uploader.__version__
    assert metadata["slicerVersion"] == "2.5.0-alpha"
    assert metadata["outputName"] == "benchy x16.gcode"
    assert metadata["host"] == "File"
    assert metadata["template"] == "default"
    assert metadata["installId"] == INSTALL_ID
    assert len(metadata["models"]) == 16
    assert metadata["userPresets"] == []
    assert api.calls[0][0] == "https://api.example"
    assert api.calls[0][3] == 30
    assert outcome.ok


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


def test_a_success_returns_the_claim_url(gcode):
    _, outcome = run(make_job(gcode))

    assert outcome.setting_id == SETTING_ID
    assert outcome.claim_url == (
        "https://www.example/prints/new/cura?cura_version=2.5.0-alpha"
        f"&plugin_version={uploader.__version__}&settingId={SETTING_ID}"
    )


def test_a_failed_upload_returns_the_apis_message(gcode):
    api = FakeApi(UploadResult(False, 429, "HTTP 429, too many uploads"))

    _, outcome = run(make_job(gcode), api)

    assert not outcome.ok
    assert not outcome.declined
    assert "HTTP 429, too many uploads" in outcome.message
    assert outcome.claim_url is None


def test_an_unexpected_error_is_returned_not_raised(gcode):
    _, outcome = run(make_job(gcode), FakeApi(error=RuntimeError("boom")))

    assert not outcome.ok
    assert "boom" in outcome.message


def test_a_slice_that_is_gone_says_to_slice_again(tmp_path):
    api, outcome = run(make_job(tmp_path / "gone.gcode"))

    assert api.calls == []
    assert not outcome.ok
    assert "slice again" in outcome.message.lower()


def test_a_declined_file_prompt_is_reported_as_declined(gcode):
    _audit.answer = lambda category, target: False

    with _callback.inside("ui"):
        api, outcome = run(make_job(gcode))

    assert api.calls == []
    assert outcome.declined
    assert outcome.message == uploader.DECLINED
    assert _audit.prompts == [("file", str(gcode), "ui", False)]


def test_a_declined_network_prompt_is_reported_as_declined(gcode):
    _, outcome = run(make_job(gcode), FakeApi(error=PermissionError("declined")))

    assert outcome.declined
    assert outcome.message == uploader.DECLINED


def test_reading_the_temp_gcode_asks_once_on_the_ui_thread(gcode):
    with _callback.inside("ui"):
        _, outcome = run(make_job(gcode))

    assert outcome.ok
    assert _audit.prompts == [("file", str(gcode), "ui", True)]


def test_a_failed_scan_still_uploads_without_models(gcode, monkeypatch):
    def broken(path, log):
        raise ValueError("bad line")

    monkeypatch.setattr(uploader.object_bounds, "scan_file", broken)

    api, outcome = run(make_job(gcode))

    assert outcome.ok
    assert api.sent()[1]["models"] == []


def test_a_failed_preset_lookup_still_uploads_without_presets(gcode, monkeypatch, tmp_path):
    def broken(*args):
        raise RuntimeError("bad preset")

    monkeypatch.setattr(uploader.presets, "find", broken)

    api, _ = run(make_job(gcode, data_dir=tmp_path))

    assert api.sent()[1]["userPresets"] == []


def test_debug_writes_the_metadata_and_the_response(gcode, tmp_path):
    debug_dir = tmp_path / "storage" / "debug"

    run(make_job(gcode, debug_dir=debug_dir))

    record = json.loads((debug_dir / "last-upload.json").read_text(encoding="utf-8"))
    assert record["metadata"]["outputName"] == "benchy x16.gcode"
    assert record["result"]["status"] == 200
    assert record["result"]["setting_id"] == SETTING_ID
    assert record["gcodeChars"] > 0
    assert record["url"].endswith(SETTING_ID)


def test_orcas_file_is_left_in_place(gcode):
    run(make_job(gcode))

    assert gcode.exists()


def test_without_hook_values_the_gcode_supplies_them(gcode):
    job = make_job(gcode, output_name="", orca_version="", preset_ids=None)

    api, outcome = run(job)

    _, metadata = api.sent()
    assert metadata["slicerVersion"] == "2.4.0"
    assert metadata["outputName"] == "3DBenchy.gcode"
    assert "cura_version=2.4.0" in outcome.claim_url


def test_without_hook_values_the_presets_come_from_the_gcode(gcode, tmp_path):
    data_dir = tmp_path / "OrcaSlicer"
    folder = data_dir / "user" / "42" / "process"
    folder.mkdir(parents=True)
    name = "0.10mm FAST DETAIL @MK4S 0.4 - 3DPrintLog"
    (folder / f"{name}.json").write_text('{"name": "x"}', encoding="utf-8")

    api, _ = run(make_job(gcode, data_dir=data_dir, preset_ids=None))

    assert [p["name"] for p in api.sent()[1]["userPresets"]] == [name]
