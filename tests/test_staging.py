import os
import time

from printlog_orca import staging


def source(tmp_path, text="; plate 1\n", name="plate_1.gcode"):
    path = tmp_path / "orca-temp" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    return path


def test_new_job_paths_are_distinct_and_create_nothing(tmp_path):
    storage = tmp_path / "storage"

    first, second = staging.new_job_path(storage), staging.new_job_path(storage)

    assert first != second
    assert first.parent == staging.jobs_dir(storage) == storage / "uploads" / "jobs"
    assert not storage.exists()


def test_working_copy_copies_and_leaves_orcas_file(tmp_path):
    storage = tmp_path / "storage"
    gcode = source(tmp_path)

    copy = staging.working_copy(storage, gcode)

    assert copy.read_text() == "; plate 1\n"
    assert copy.parent == staging.jobs_dir(storage)
    assert gcode.exists(), "Orca's own file is never moved or deleted"


def test_working_copy_is_a_separate_file_per_call(tmp_path):
    storage = tmp_path / "storage"
    gcode = source(tmp_path)

    assert staging.working_copy(storage, gcode) != staging.working_copy(storage, gcode)


def test_remove_leftovers_keeps_copies_from_this_session(tmp_path):
    storage = tmp_path / "storage"
    old = staging.working_copy(storage, source(tmp_path))
    an_hour_ago = time.time() - 3600
    os.utime(old, (an_hour_ago, an_hour_ago))
    new = staging.working_copy(storage, source(tmp_path))

    removed = staging.remove_leftovers(storage, before=time.time() - 60)

    assert removed == 1
    assert not old.exists()
    assert new.exists()


def test_remove_leftovers_without_a_folder(tmp_path):
    assert staging.remove_leftovers(tmp_path / "nothing", before=time.time()) == 0
