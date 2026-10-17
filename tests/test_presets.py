import json
from pathlib import Path

import pytest

from printlog_orca import presets
from printlog_orca.presets import UserPreset


def write_preset(data_dir, kind, name, content, account="1234567"):
    folder = data_dir / "user" / account / kind
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return path


def find(data_dir, print_id=None, filament_ids=None, printer_id=None, log=None):
    return presets.find(data_dir, print_id, filament_ids, printer_id, log or (lambda _: None))


def by_name(found):
    return {(p.kind, p.name): json.loads(p.json) for p in found}


def test_finds_the_process_filament_and_machine_presets(tmp_path):
    write_preset(tmp_path, "process", "0.20mm Mine", {"name": "0.20mm Mine", "wall_loops": "3"})
    write_preset(tmp_path, "filament", "My PLA", {"name": "My PLA", "nozzle_temperature": ["215"]})
    write_preset(tmp_path, "machine", "My MK4S", {"name": "My MK4S", "nozzle_diameter": ["0.4"]})

    found = find(tmp_path, "0.20mm Mine", '"My PLA"', "My MK4S")

    assert by_name(found) == {
        ("process", "0.20mm Mine"): {"name": "0.20mm Mine", "wall_loops": "3"},
        ("filament", "My PLA"): {"name": "My PLA", "nozzle_temperature": ["215"]},
        ("machine", "My MK4S"): {"name": "My MK4S", "nozzle_diameter": ["0.4"]},
    }
    assert [p.kind for p in found] == ["process", "filament", "machine"]


def test_a_system_preset_has_no_user_file_and_is_left_out(tmp_path):
    (tmp_path / "user" / "1234567").mkdir(parents=True)

    assert find(tmp_path, "0.20mm Standard @MK4S", '"Prusa Generic PLA"', "Prusa MK4S") == []


def test_reads_every_filament_once(tmp_path):
    write_preset(tmp_path, "filament", "My PLA", {"name": "My PLA"})
    write_preset(tmp_path, "filament", "My PETG", {"name": "My PETG"})

    found = find(tmp_path, filament_ids='"My PLA";"My PETG";"My PLA";Generic')

    assert [p.name for p in found] == ["My PLA", "My PETG"]


def test_follows_inherits_while_the_parent_is_a_user_preset(tmp_path):
    write_preset(tmp_path, "process", "Child", {"name": "Child", "inherits": "Parent"})
    write_preset(tmp_path, "process", "Parent", {"name": "Parent", "inherits": "0.20mm Standard"})

    found = find(tmp_path, "Child")

    assert [p.name for p in found] == ["Child", "Parent"]


def test_stops_on_an_inherits_cycle(tmp_path):
    write_preset(tmp_path, "process", "A", {"inherits": "B"})
    write_preset(tmp_path, "process", "B", {"inherits": "A"})

    assert [p.name for p in find(tmp_path, "A")] == ["A", "B"]


def test_strips_credential_keys(tmp_path):
    write_preset(
        tmp_path,
        "machine",
        "My MK4S",
        {"name": "My MK4S", "print_host": "192.168.1.5", "printhost_apikey": "abc", "a": "1"},
    )

    assert by_name(find(tmp_path, printer_id="My MK4S")) == {
        ("machine", "My MK4S"): {"name": "My MK4S", "a": "1"}
    }


def test_keeps_numbers_as_written_and_the_first_of_duplicate_keys(tmp_path):
    write_preset(tmp_path, "process", "P", '{"a": 0.20, "b": 1e3, "a": "later", "c": true}')

    (preset,) = find(tmp_path, "P")

    assert preset.json == '{"a":0.20,"b":1e3,"c":true}'


def test_leaves_out_a_preset_found_under_two_accounts(tmp_path):
    write_preset(tmp_path, "process", "P", {"name": "P"}, account="111")
    write_preset(tmp_path, "process", "P", {"name": "P"}, account="222")
    log = []

    assert find(tmp_path, "P", log=log.append) == []
    assert any("2 files" in line for line in log)


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '{"a": NaN}'])
def test_leaves_out_a_preset_that_is_not_a_json_object(tmp_path, content):
    write_preset(tmp_path, "process", "P", content)

    assert find(tmp_path, "P") == []


@pytest.mark.parametrize(
    ("name", "safe"),
    [
        ("P", True),
        ("0.16 High Quality @U1 (0.4 nozzle) - 3DPrintLog", True),
        ("", False),
        (".", False),
        ("..", False),
        ("../P", False),
        ("..\\P", False),
        ("sub/P", False),
        ("sub\\P", False),
        ("C:\\P", False),
        ("P\0", False),
        ("P?", False),
    ],
)
def test_only_plain_file_names_are_looked_up(name, safe):
    assert presets.is_safe_preset_name(name) is safe


def test_an_unsafe_name_is_never_joined_onto_a_path(tmp_path):
    write_preset(tmp_path, "process", "secret", {"name": "outside"})

    assert find(tmp_path, "../process/secret") == []


def test_missing_user_folder_finds_nothing(tmp_path):
    assert find(tmp_path, "P", '"F"', "M") == []


@pytest.mark.parametrize(
    ("serialized", "expected"),
    [
        ("Generic PLA", ["Generic PLA"]),
        ('"My PLA";"My PETG"', ["My PLA", "My PETG"]),
        ('PLA;"My \\"best\\" PETG"', ["PLA", 'My "best" PETG']),
        ('"a\\\\b"', ["a\\b"]),
        ("", []),
    ],
)
def test_parse_strings_reads_orcas_serialized_string_lists(serialized, expected):
    assert presets.parse_strings(serialized) == expected


def test_parse_string_unescapes_a_single_value():
    assert presets.parse_string('My \\"best\\" profile') == 'My "best" profile'


def test_user_preset_is_a_plain_record():
    assert UserPreset("process", "P", "{}").kind == "process"


@pytest.mark.parametrize(
    ("storage", "data_dir"),
    [
        ("OrcaSlicer/orca_plugins/plugin_data/my_plugin", "OrcaSlicer"),
        ("OrcaSlicer/orca_plugins/plugin_data/_subscribed/42/uuid", "OrcaSlicer"),
        ("somewhere/else", None),
    ],
)
def test_data_dir_from_storage(tmp_path, storage, data_dir):
    expected = tmp_path / data_dir if data_dir else None

    assert presets.data_dir_from_storage(tmp_path / storage) == expected


def test_data_dir_from_storage_accepts_a_string(tmp_path):
    storage = str(tmp_path / "OrcaSlicer" / "orca_plugins" / "plugin_data" / "p")

    assert presets.data_dir_from_storage(storage) == Path(tmp_path / "OrcaSlicer")
