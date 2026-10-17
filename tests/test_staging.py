import threading

import pytest

from printlog_orca.staging import Staging


class Clock:
    def __init__(self, now=1_000.0):
        self.now = now

    def __call__(self):
        return self.now


IDS = {"print_settings_id": "P", "filament_settings_id": "F", "printer_settings_id": "M"}


def source(tmp_path, text="; plate 1\n", name="plate_1.gcode"):
    path = tmp_path / "orca-temp" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    return path


def stage(staging, path, **overrides):
    fields = {"host": "File", "output_name": str(path), "orca_version": "2.5.0", "preset_ids": IDS}
    fields.update(overrides)
    return staging.stage(path, **fields)


@pytest.fixture
def storage(tmp_path):
    return tmp_path / "storage"


def test_stage_copies_the_gcode_and_remembers_the_slice(tmp_path, storage):
    clock = Clock()
    staging = Staging(storage, clock)
    gcode = source(tmp_path)

    stage(staging, gcode)
    staged = staging.staged()

    assert staged.path.read_text() == "; plate 1\n"
    assert staged.path.parent.parent == storage
    assert (staged.host, staged.output_name, staged.orca_version) == ("File", str(gcode), "2.5.0")
    assert staged.preset_ids == IDS
    assert staged.staged_at == 1_000.0
    assert gcode.exists(), "Orca's own file is never moved or deleted"


def test_a_new_slice_replaces_the_staged_one(tmp_path, storage):
    clock = Clock()
    staging = Staging(storage, clock)
    stage(staging, source(tmp_path, "; first\n"))
    clock.now = 2_000.0

    stage(staging, source(tmp_path, "; second\n"))

    assert staging.staged().path.read_text() == "; second\n"
    assert staging.staged().staged_at == 2_000.0
    assert len(list(storage.rglob("*.gcode"))) == 1


def test_take_hands_over_the_slice_once(tmp_path, storage):
    staging = Staging(storage, Clock())
    stage(staging, source(tmp_path))

    taken = staging.take()

    assert taken.path.read_text() == "; plate 1\n"
    assert staging.staged() is None
    assert staging.take() is None


def test_a_taken_file_survives_the_next_slice(tmp_path, storage):
    staging = Staging(storage, Clock())
    stage(staging, source(tmp_path, "; first\n"))
    taken = staging.take()

    stage(staging, source(tmp_path, "; second\n"))

    assert taken.path.read_text() == "; first\n"


def test_take_is_once_only_across_threads(tmp_path, storage):
    staging = Staging(storage, Clock())
    stage(staging, source(tmp_path))
    results = []
    threads = [threading.Thread(target=lambda: results.append(staging.take())) for _ in range(8)]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len([r for r in results if r is not None]) == 1


def test_clear_drops_the_staged_slice_and_its_file(tmp_path, storage):
    staging = Staging(storage, Clock())
    staged = stage(staging, source(tmp_path))

    staging.clear()

    assert staging.staged() is None
    assert not staged.path.exists()


def test_working_copy_is_a_separate_file_per_call(tmp_path, storage):
    staging = Staging(storage, Clock())
    gcode = source(tmp_path)

    first = staging.working_copy(gcode)
    second = staging.working_copy(gcode)

    assert first != second
    assert first.read_text() == second.read_text() == "; plate 1\n"


def test_leftovers_from_an_earlier_session_are_removed(tmp_path, storage):
    Staging(storage, Clock()).working_copy(source(tmp_path))
    stage(Staging(storage, Clock()), source(tmp_path))

    Staging(storage, Clock())

    assert list(storage.rglob("*.gcode")) == []


def test_a_failed_copy_leaves_the_previous_slice_staged(tmp_path, storage):
    staging = Staging(storage, Clock())
    stage(staging, source(tmp_path, "; first\n"))

    with pytest.raises(OSError):
        stage(staging, tmp_path / "missing.gcode")

    assert staging.staged().path.read_text() == "; first\n"
