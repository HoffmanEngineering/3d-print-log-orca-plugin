import gzip
from pathlib import Path

from printlog_orca import gcode_info
from printlog_orca.object_bounds import ObjectBounds

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return gzip.decompress((FIXTURES / name).read_bytes()).decode("utf-8")


def box(name):
    return ObjectBounds(name, 0, 1, 0, 1, 1)


def test_reads_the_slicer_version_from_the_header():
    text = fixture("orcaslicer-2.4.0-benchy-x16.gcode.gz")

    assert gcode_info.slicer_version(text) == "2.4.0"


def test_a_header_without_a_generator_line_has_no_version():
    assert gcode_info.slicer_version("G28\n") == ""


def test_reads_the_preset_names_as_the_config_serializes_them():
    text = fixture("orcaslicer-2.4.0-benchy-x16.gcode.gz")

    assert gcode_info.preset_ids(text) == {
        "print_settings_id": "0.10mm FAST DETAIL @MK4S 0.4 - 3DPrintLog",
        "filament_settings_id": '"Prusa Generic PLA @MK4S"',
        "printer_settings_id": "Prusa MK4S 0.4 nozzle",
    }


def test_the_last_line_of_a_key_wins_and_missing_keys_are_none():
    text = "; print_settings_id = early\r\nG1\n; print_settings_id = late\r\n"

    assert gcode_info.preset_ids(text) == {
        "print_settings_id": "late",
        "filament_settings_id": None,
        "printer_settings_id": None,
    }


def test_output_name_from_the_first_object():
    assert gcode_info.output_name_from_objects([box("Calibration Cube.stl"), box("b")]) == (
        "Calibration Cube.gcode"
    )
    assert gcode_info.output_name_from_objects([box(""), box("Part")]) == "Part.gcode"


def test_output_name_without_objects():
    assert gcode_info.output_name_from_objects([]) == gcode_info.FALLBACK_NAME
