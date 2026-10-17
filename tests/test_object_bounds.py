"""Ported from ObjectBoundsScannerTests, plus the uploader's own output on real Orca G-code."""

import gzip
import io
from pathlib import Path

import pytest

from printlog_orca import object_bounds
from printlog_orca.object_bounds import BUFFER_SIZE

FIXTURES = Path(__file__).parent / "fixtures"

SQUARE = ";TYPE:Outer wall\nG1 X10 Y10 E0.5\nG1 X20 Y10 E0.5\nG1 X20 Y20 E0.5\nG1 X10 Y20 E0.5\n"


def scan(gcode, log=None):
    return object_bounds.scan(io.BytesIO(gcode.encode("utf-8")), log or (lambda _: None))


def instance(name, number, body):
    return (
        f"; printing object {name} id:{number} copy 0\n{body}"
        f"; stop printing object {name} id:{number} copy 0\n"
    )


def layer(z, body):
    return f";Z:{z}\n" + instance("Cube", 0, body)


def assert_box(box, min_x, max_x, min_y, max_y, max_z, tolerance=0.0001):
    assert box.min_x == pytest.approx(min_x, abs=tolerance), "min_x"
    assert box.max_x == pytest.approx(max_x, abs=tolerance), "max_x"
    assert box.min_y == pytest.approx(min_y, abs=tolerance), "min_y"
    assert box.max_y == pytest.approx(max_y, abs=tolerance), "max_y"
    assert box.max_z == pytest.approx(max_z, abs=tolerance), "max_z"


class OneByteAtATime(io.RawIOBase):
    def __init__(self, data):
        self._data = io.BytesIO(data)

    def readable(self):
        return True

    def read(self, size=-1):
        return self._data.read(1)


class Failing(io.RawIOBase):
    def readable(self):
        return True

    def read(self, size=-1):
        raise OSError("disk on fire")


def test_boxes_the_extrusions_of_one_object():
    result = scan(layer("0.2", SQUARE))

    assert len(result) == 1
    assert result[0].name == "Cube"
    assert_box(result[0], 10, 20, 10, 20, 0.2)


def test_accumulates_the_same_instance_across_layers_including_an_empty_span():
    gcode = (
        layer("0.2", SQUARE)
        + layer("0.4", "")
        + layer("0.6", ";TYPE:Outer wall\nG1 X25 Y5 E0.5\n")
        + layer("0.8", "")
    )

    result = scan(gcode)

    assert len(result) == 1
    assert_box(result[0], 10, 25, 5, 20, 0.6)


def test_keeps_copies_of_an_object_apart():
    gcode = (
        ";Z:0.2\n"
        "; printing object Cube id:0 copy 0\n"
        + SQUARE
        + "; stop printing object Cube id:0 copy 0\n"
        "; printing object Cube id:1 copy 0\n;TYPE:Outer wall\nG1 X50 Y50 E0.5\nG1 X60 Y60 E0.5\n"
        "; stop printing object Cube id:1 copy 0\n"
    )

    result = scan(gcode)

    assert len(result) == 2
    assert_box(result[0], 10, 20, 10, 20, 0.2)
    assert_box(result[1], 50, 60, 50, 60, 0.2)
    assert result[1].name == "Cube"


def test_switches_accumulators_when_a_start_follows_a_start_without_a_stop():
    gcode = (
        ";Z:0.2\n; printing object A id:0 copy 0\n" + SQUARE + "; printing object B id:1 copy 0\n"
        ";TYPE:Outer wall\nG1 X50 Y50 E0.5\nG1 X60 Y60 E0.5\n"
    )

    result = scan(gcode)

    assert len(result) == 2
    assert_box(result[0], 10, 20, 10, 20, 0.2)
    assert_box(result[1], 50, 60, 50, 60, 0.2)


@pytest.mark.parametrize(
    "role", ["Brim", "Skirt", "Support", "Support interface", "Prime tower", "Wipe tower", "Custom"]
)
def test_ignores_runs_that_are_not_part_of_the_model(role):
    body = f";TYPE:{role}\nG1 X0 Y0 E0.5\nG1 X100 Y100 E0.5\n" + SQUARE

    assert_box(scan(layer("0.2", body))[0], 10, 20, 10, 20, 0.2)


def test_ignores_moves_outside_any_object():
    gcode = (
        ";Z:0.2\n;TYPE:Outer wall\nG1 X0 Y0 E0.5\nG1 X100 Y100 E0.5\n"
        + layer("0.2", SQUARE)
        + "G1 X200 Y200 E0.5\n"
    )

    assert_box(scan(gcode)[0], 10, 20, 10, 20, 0.2)


def test_uses_modal_coordinates_for_single_axis_moves():
    body = ";TYPE:Outer wall\nG1 X10 Y10 E0.5\nG1 X30 E0.5\nG1 Y40 E0.5\n"

    assert_box(scan(layer("0.2", body))[0], 10, 30, 10, 40, 0.2)


def test_ignores_travels_retractions_and_zero_extrusion():
    body = SQUARE + "G1 X90 Y90\nG1 X91 Y91 E-0.8\nG1 X92 Y92 E0\nG0 X93 Y93\n"

    assert_box(scan(layer("0.2", body))[0], 10, 20, 10, 20, 0.2)


def test_detects_extrusion_in_absolute_e_mode():
    body = (
        "M82\nG92 E0\n;TYPE:Outer wall\nG1 X10 Y10 E1\nG1 X20 Y20 E2\nG1 X90 Y90 E1.2\n"
        "G1 X91 Y91 E1.2\nG92 E0\nG1 X30 Y30 E0.5\nM83\nG1 X40 Y40 E0.1\nG1 X95 Y95 E-0.1\n"
    )

    # E1.2 after E2 is a retraction, E1.2 again is a travel, G92 resets, M83 returns to relative.
    assert_box(scan(layer("0.2", body))[0], 10, 40, 10, 40, 0.2)


def test_includes_arc_extrema_between_the_endpoints():
    # Quarter circle around (0,0), r=10, 45° to 135° counter-clockwise: the top is no endpoint.
    body = ";TYPE:Outer wall\nG1 X7.0711 Y7.0711 E0.1\nG3 X-7.0711 Y7.0711 I-7.0711 J-7.0711 E0.5\n"

    assert_box(scan(layer("0.2", body))[0], -7.0711, 7.0711, 7.0711, 10, 0.2, tolerance=0.001)


def test_includes_clockwise_arc_extrema():
    # Same points, clockwise: the long way round through 0°, -90° and 180°.
    body = ";TYPE:Outer wall\nG1 X7.0711 Y7.0711 E0.1\nG2 X-7.0711 Y7.0711 I-7.0711 J-7.0711 E0.5\n"

    assert_box(scan(layer("0.2", body))[0], -10, 10, -10, 7.0711, 0.2, tolerance=0.001)


def test_does_not_include_arc_extrema_outside_the_sweep():
    body = ";TYPE:Outer wall\nG1 X7.0711 Y7.0711 E0.1\nG3 X6.4279 Y7.6604 I-7.0711 J-7.0711 E0.1\n"

    assert_box(scan(layer("0.2", body))[0], 6.4279, 7.0711, 7.0711, 7.6604, 0.2, tolerance=0.001)


def test_handles_an_arc_crossing_zero_degrees():
    body = ";TYPE:Outer wall\nG1 X7.0711 Y-7.0711 E0.1\nG3 X7.0711 Y7.0711 I-7.0711 J7.0711 E0.5\n"

    assert_box(scan(layer("0.2", body))[0], 7.0711, 10, -7.0711, 7.0711, 0.2, tolerance=0.001)


def test_treats_a_coincident_endpoint_arc_as_a_full_circle():
    body = ";TYPE:Outer wall\nG1 X10 Y0 E0.1\nG3 X10 Y0 I-10 J0 E1\n"

    assert_box(scan(layer("0.2", body))[0], -10, 10, -10, 10, 0.2, tolerance=0.001)


def test_keeps_object_names_with_spaces_and_large_ids():
    gcode = (
        ";Z:0.2\n; printing object Calibration Cube.stl id:4706452076321832960 copy 0\n" + SQUARE
    )

    assert scan(gcode)[0].name == "Calibration Cube.stl"


def test_ignores_malformed_start_markers():
    assert scan(";Z:0.2\n; printing object nonsense\n" + SQUARE) == []


def test_returns_nothing_without_object_markers():
    assert scan(";Z:0.2\n" + SQUARE) == []


def test_drops_instances_that_never_extrude():
    assert scan(layer("0.2", ";TYPE:Brim\nG1 X1 Y1 E0.5\n")) == []


def test_accepts_crlf_a_bom_and_a_missing_final_newline():
    gcode = "﻿" + (";Z:0.2\n; printing object Cube id:0 copy 0\n" + SQUARE).replace(
        "\n", "\r\n"
    ).rstrip("\r\n")

    assert_box(scan(gcode)[0], 10, 20, 10, 20, 0.2)


def test_decodes_non_ascii_names_split_across_buffer_boundaries():
    filler = "; padding line\n" * ((BUFFER_SIZE - 20) // 15 + 1)
    gcode = filler + ";Z:0.2\n; printing object Würfel Ünïcode id:0 copy 0\n" + SQUARE

    result = scan(gcode)

    assert len(result) == 1
    assert result[0].name == "Würfel Ünïcode"
    assert_box(result[0], 10, 20, 10, 20, 0.2)


def test_copes_with_streams_that_return_short_reads():
    stream = OneByteAtATime(layer("0.2", SQUARE).encode())

    assert_box(object_bounds.scan(stream, lambda _: None)[0], 10, 20, 10, 20, 0.2)


def test_strips_the_bom_even_when_the_first_read_is_short():
    stream = OneByteAtATime(("﻿" + layer("0.2", SQUARE)).encode())

    assert_box(object_bounds.scan(stream, lambda _: None)[0], 10, 20, 10, 20, 0.2)


def test_skips_a_line_longer_than_the_buffer_and_carries_on():
    log = []
    gcode = (
        ";Z:0.2\n; " + "x" * (BUFFER_SIZE + 10) + "\n; printing object Cube id:0 copy 0\n" + SQUARE
    )

    result = scan(gcode, log.append)

    assert_box(result[0], 10, 20, 10, 20, 0.2)
    assert log


def test_propagates_stream_failures():
    with pytest.raises(OSError):
        object_bounds.scan(Failing(), lambda _: None)


def test_does_not_add_a_point_for_an_extrusion_only_move():
    # Orca de-retracts (G1 E.7) at the travel destination before the next ;TYPE:.
    gcode = (
        ";Z:0.2\n; printing object A id:0 copy 0\n"
        + SQUARE
        + "; stop printing object A id:0 copy 0\n"
        "; printing object B id:1 copy 0\nG1 X150 Y150 F18000\nG1 E.7 F1500\n;TYPE:Brim\n"
        "G1 X151 Y151 E0.5\n;TYPE:Outer wall\nG1 X50 Y50 E0.5\nG1 X60 Y60 E0.5\n"
    )

    assert_box(scan(gcode)[1], 50, 60, 50, 60, 0.2)


def test_keeps_the_e_position_across_a_relative_to_absolute_switch():
    body = "G92 E0\nM83\n;TYPE:Outer wall\nG1 X10 Y10 E5\nM82\nG1 X90 Y90 E4\nG1 X20 Y20 E6\n"

    assert_box(scan(layer("0.2", body))[0], 10, 20, 10, 20, 0.2)


@pytest.mark.parametrize("layer_line", [";Z:12junk", ";Z:NaN", ";Z:", ";Z:Infinity"])
def test_ignores_malformed_layer_heights(layer_line):
    gcode = ";Z:0.2\n" + layer_line + "\n; printing object Cube id:0 copy 0\n" + SQUARE

    assert_box(scan(gcode)[0], 10, 20, 10, 20, 0.2)


@pytest.mark.parametrize("value", [".5", "+.5", "5.", "5e-1", "5E-1", "0.5"])
def test_reads_the_number_forms_net_reads(value):
    body = f";TYPE:Outer wall\nG1 X10 Y10 E{value}\nG1 X20 Y20 E{value}\n"

    assert_box(scan(layer("0.2", body))[0], 10, 20, 10, 20, 0.2)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "1_0", "0x1"])
def test_ignores_coordinates_that_are_not_finite_numbers(value):
    body = SQUARE + f"G1 X{value} Y15 E0.5\n"

    result = scan(layer("0.2", body))

    assert result[0].max_x == 20


def read_fixture(name):
    return gzip.decompress((FIXTURES / f"{name}.gcode.gz").read_bytes())


def outer_size(box, line_width):
    def rounded(value):
        # ModelsPlaceholder rounds half away from zero; sizes here are positive.
        return float(int(value * 10 + 0.5)) / 10

    return (
        box.name,
        rounded(box.max_x - box.min_x + line_width),
        rounded(box.max_y - box.min_y + line_width),
        rounded(box.max_z),
    )


# The uploader's {{models}} output for each fixture: see tests/fixtures/README.md.
@pytest.mark.parametrize(
    ("fixture", "line_width", "expected"),
    [
        ("orcaslicer-2.4.0-benchy-x16", 0.45, [("3DBenchy.drc", 55.6, 30.9, 48.0)] * 16),
        ("orcaslicer-2.2.0-rc-calibration-cube", 0.4, [("Calibration Cube.stl", 20.0, 20.0, 20.0)]),
        (
            "orcaslicer-2.2.0-rc-calibration-cube-two-filament",
            0.4,
            [("Calibration Cube.stl", 51.6, 23.4, 20.0), ("Cube", 23.0, 23.0, 23.0)],
        ),
    ],
)
def test_matches_the_uploader_on_real_orca_gcode(fixture, line_width, expected):
    result = object_bounds.scan(io.BytesIO(read_fixture(fixture)), lambda _: None)

    assert sorted(outer_size(box, line_width) for box in result) == sorted(expected)


def test_scan_file_reads_from_a_path(tmp_path):
    path = tmp_path / "print.gcode"
    path.write_bytes(read_fixture("orcaslicer-2.4.0-benchy-x16"))

    assert len(object_bounds.scan_file(path)) == 16


def test_matches_whole_commands_only():
    body = SQUARE + "G10 X90 Y90 E0.5\nG11\nM820\nG1 X15 Y15 E0.5\n"

    assert_box(scan(layer("0.2", body))[0], 10, 20, 10, 20, 0.2)
