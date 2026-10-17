"""The spec's trigger table: which hook calls and events upload, and under what name."""

import pytest

from printlog_orca import triggers
from printlog_orca.triggers import Action

WINDOW = 30
SLICED_AT = 1_000_000.0


def test_upload_to_a_print_host_uploads_now():
    action = triggers.for_gcode("OctoPrint", "/tmp/plate_1.gcode", "benchy.gcode")

    assert action is Action.UPLOAD_NOW


def test_export_to_file_uploads_now():
    action = triggers.for_gcode("File", "/tmp/plate_1.gcode.pp", "/home/me/benchy.gcode")

    assert action is Action.UPLOAD_NOW


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


def test_print_job_started_uploads_the_latest_slice():
    action = triggers.for_bambu_event("PrintJobStarted", "", SLICED_AT, SLICED_AT + 3600, WINDOW)

    assert action is Action.UPLOAD_LATEST


@pytest.mark.parametrize("status", ["PREPARE", "RUNNING", "running", " Prepare "])
def test_printer_starting_within_the_window_uploads_the_latest_slice(status):
    action = triggers.for_bambu_event(
        "PrintStateChanged", status, SLICED_AT, SLICED_AT + 60, WINDOW
    )

    assert action is Action.UPLOAD_LATEST


@pytest.mark.parametrize("status", ["IDLE", "FINISH", "FAILED", "PAUSE", ""])
def test_other_printer_states_are_ignored(status):
    action = triggers.for_bambu_event(
        "PrintStateChanged", status, SLICED_AT, SLICED_AT + 60, WINDOW
    )

    assert action is Action.IGNORE


def test_printer_starting_after_the_window_is_ignored():
    late = SLICED_AT + WINDOW * 60 + 1

    assert triggers.for_bambu_event("PrintStateChanged", "RUNNING", SLICED_AT, late, WINDOW) is (
        Action.IGNORE
    )


def test_the_window_includes_its_last_second():
    edge = SLICED_AT + WINDOW * 60

    assert triggers.for_bambu_event("PrintStateChanged", "RUNNING", SLICED_AT, edge, WINDOW) is (
        Action.UPLOAD_LATEST
    )


def test_a_clock_that_went_backwards_is_outside_the_window():
    assert triggers.for_bambu_event(
        "PrintStateChanged", "RUNNING", SLICED_AT, SLICED_AT - 5, WINDOW
    ) is (Action.IGNORE)


@pytest.mark.parametrize("event", ["PrintJobStarted", "PrintStateChanged"])
def test_no_slice_means_nothing_to_upload(event):
    assert triggers.for_bambu_event(event, "RUNNING", None, SLICED_AT, WINDOW) is Action.IGNORE


@pytest.mark.parametrize(
    "event",
    [
        "SendJobStarted",
        "UploadFinished",
        "GCodeExportFinished",
        "SlicingJobComplete",
        "SliceStarted",
    ],
)
def test_other_events_are_ignored(event):
    assert triggers.for_bambu_event(event, "RUNNING", SLICED_AT, SLICED_AT, WINDOW) is Action.IGNORE


@pytest.mark.parametrize(
    ("candidate", "model", "expected"),
    [
        # UploadStarted names the upload's temp source file: never a print name.
        (".OrcaSlicer.upload.1a2b-3c4d-5e6f-7a8b", "Benchy", "Benchy.gcode"),
        (".1234.0.3mf", "Benchy", "Benchy.gcode"),
        # PrintJobStarted carries the project name.
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
