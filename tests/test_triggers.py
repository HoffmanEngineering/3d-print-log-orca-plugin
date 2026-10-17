"""Which hook calls record an exported file, and the name an upload gets."""

import pytest

from printlog_orca import triggers
from printlog_orca.triggers import Action


def test_upload_to_a_print_host_is_recorded():
    action = triggers.for_gcode("OctoPrint", "/tmp/plate_1.gcode", "benchy.gcode")

    assert action is Action.RECORD


def test_export_to_file_is_recorded():
    action = triggers.for_gcode("File", "/tmp/plate_1.gcode.pp", "/home/me/benchy.gcode")

    assert action is Action.RECORD


def test_bambu_after_slice_run_is_left_to_the_slice_event():
    action = triggers.for_gcode("File", "/tmp/plate_1.gcode", "/tmp/plate_1.gcode")

    assert action is Action.IGNORE


def test_bambu_after_slice_run_is_recognized_through_path_spelling(tmp_path):
    path = tmp_path / "plate_1.gcode"

    action = triggers.for_gcode("File", str(path), str(tmp_path / "." / "plate_1.gcode"))

    assert action is Action.IGNORE


@pytest.mark.parametrize(
    ("host", "gcode_path"), [("", "/tmp/a.gcode"), ("File", ""), ("OctoPrint", "")]
)
def test_a_call_without_a_host_or_file_is_ignored(host, gcode_path):
    assert triggers.for_gcode(host, gcode_path, "out.gcode") is Action.IGNORE


@pytest.mark.parametrize(
    ("candidate", "model", "expected"),
    [
        # UploadStarted names the upload's temp source file: never a print name.
        (".OrcaSlicer.upload.1a2b-3c4d-5e6f-7a8b", "Benchy", "Benchy.gcode"),
        (".1234.0.3mf", "Benchy", "Benchy.gcode"),
        # PrintJobStarted / SendJobStarted carry the project name.
        ("Desk organizer", "Benchy", "Desk organizer.gcode"),
        ("Desk organizer.3mf", "", "Desk organizer.gcode"),
        ("plate.gcode.3mf", "", "plate.gcode"),
        ("C:\\prints\\cube.gcode", "", "cube.gcode"),
        ("cube.bgcode", "", "cube.bgcode"),
        ("", "Benchy", "Benchy.gcode"),
        ("  ", "  ", ""),
        ("", "", ""),
    ],
)
def test_upload_name(candidate, model, expected):
    assert triggers.upload_name(candidate, model) == expected
