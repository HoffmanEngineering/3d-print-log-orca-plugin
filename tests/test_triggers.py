"""The spec's trigger table: one export or one send gives one upload; slicing alone gives none."""

import pytest

from printlog_orca import triggers
from printlog_orca.triggers import Action

WINDOW = 30
STAGED_AT = 1_000_000.0


def test_upload_to_a_print_host_uploads_now():
    action = triggers.for_gcode("OctoPrint", "/tmp/plate_1.gcode", "benchy.gcode")

    assert action is Action.UPLOAD_NOW


def test_export_to_file_uploads_now():
    action = triggers.for_gcode("File", "/tmp/plate_1.gcode.pp", "/home/me/benchy.gcode")

    assert action is Action.UPLOAD_NOW


def test_bambu_after_slice_run_only_stages():
    action = triggers.for_gcode("File", "/tmp/plate_1.gcode", "/tmp/plate_1.gcode")

    assert action is Action.STAGE


def test_bambu_after_slice_run_is_recognized_through_path_spelling(tmp_path):
    path = tmp_path / "plate_1.gcode"

    action = triggers.for_gcode("File", str(path), str(tmp_path / "." / "plate_1.gcode"))

    assert action is Action.STAGE


@pytest.mark.parametrize(
    ("host", "gcode_path"), [("", "/tmp/a.gcode"), ("File", ""), ("OctoPrint", "")]
)
def test_a_call_without_a_host_or_file_is_ignored(host, gcode_path):
    assert triggers.for_gcode(host, gcode_path, "out.gcode") is Action.IGNORE


def test_print_job_started_uploads_the_staged_slice():
    action = triggers.for_event("PrintJobStarted", "", STAGED_AT, STAGED_AT + 3600, WINDOW)

    assert action is Action.UPLOAD_STAGED


@pytest.mark.parametrize("status", ["PREPARE", "RUNNING", "running", " Prepare "])
def test_printer_starting_within_the_window_uploads_the_staged_slice(status):
    action = triggers.for_event("PrintStateChanged", status, STAGED_AT, STAGED_AT + 60, WINDOW)

    assert action is Action.UPLOAD_STAGED


@pytest.mark.parametrize("status", ["IDLE", "FINISH", "FAILED", "PAUSE", ""])
def test_other_printer_states_are_ignored(status):
    action = triggers.for_event("PrintStateChanged", status, STAGED_AT, STAGED_AT + 60, WINDOW)

    assert action is Action.IGNORE


def test_printer_starting_after_the_window_is_ignored():
    late = STAGED_AT + WINDOW * 60 + 1

    assert triggers.for_event("PrintStateChanged", "RUNNING", STAGED_AT, late, WINDOW) is (
        Action.IGNORE
    )


def test_the_window_includes_its_last_second():
    edge = STAGED_AT + WINDOW * 60

    assert triggers.for_event("PrintStateChanged", "RUNNING", STAGED_AT, edge, WINDOW) is (
        Action.UPLOAD_STAGED
    )


def test_a_clock_that_went_backwards_is_outside_the_window():
    assert triggers.for_event("PrintStateChanged", "RUNNING", STAGED_AT, STAGED_AT - 5, WINDOW) is (
        Action.IGNORE
    )


@pytest.mark.parametrize("event", ["PrintJobStarted", "PrintStateChanged"])
def test_nothing_staged_means_nothing_to_upload(event):
    assert triggers.for_event(event, "RUNNING", None, STAGED_AT, WINDOW) is Action.IGNORE


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
    assert triggers.for_event(event, "RUNNING", STAGED_AT, STAGED_AT, WINDOW) is Action.IGNORE
