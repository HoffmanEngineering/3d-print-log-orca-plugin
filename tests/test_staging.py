from printlog_orca import staging


def source(tmp_path, text="; plate 1\n", name="plate_1.gcode.pp"):
    path = tmp_path / "orca-temp" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    return path


def test_export_copy_copies_into_storage_and_leaves_orcas_file(tmp_path):
    storage = tmp_path / "storage"
    gcode = source(tmp_path)

    copy = staging.export_copy(storage, gcode)

    assert copy.read_text() == "; plate 1\n"
    assert copy.parent == staging.exports_dir(storage) == storage / "uploads" / "exports"
    assert gcode.exists(), "Orca's own file is never moved or deleted"


def test_each_copy_is_a_separate_file(tmp_path):
    storage = tmp_path / "storage"
    gcode = source(tmp_path)

    assert staging.export_copy(storage, gcode) != staging.export_copy(storage, gcode)


def test_remove_unreferenced_keeps_the_copies_still_listed(tmp_path):
    storage = tmp_path / "storage"
    keep = staging.export_copy(storage, source(tmp_path))
    old = staging.export_copy(storage, source(tmp_path))

    removed = staging.remove_unreferenced(storage, keep=[keep])

    assert removed == 1
    assert keep.exists()
    assert not old.exists()


def test_remove_unreferenced_without_a_folder(tmp_path):
    assert staging.remove_unreferenced(tmp_path / "nothing", keep=[]) == 0
