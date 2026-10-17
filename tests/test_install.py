import json
import re

from printlog_orca import install


def test_creates_a_random_128_bit_id_once_and_keeps_it(tmp_path):
    first = install.install_id(tmp_path / "storage")

    assert re.fullmatch(r"[0-9a-f]{32}", first)
    assert install.install_id(tmp_path / "storage") == first
    stored = json.loads((tmp_path / "storage" / install.FILE_NAME).read_text(encoding="utf-8"))
    assert stored == {"installId": first}


def test_each_install_gets_its_own_id(tmp_path):
    assert install.install_id(tmp_path / "a") != install.install_id(tmp_path / "b")


def test_a_damaged_file_is_replaced(tmp_path):
    (tmp_path / install.FILE_NAME).write_text("{not json", encoding="utf-8")

    replaced = install.install_id(tmp_path)

    assert re.fullmatch(r"[0-9a-f]{32}", replaced)
    assert install.install_id(tmp_path) == replaced


def test_an_id_the_api_would_reject_is_replaced(tmp_path):
    (tmp_path / install.FILE_NAME).write_text('{"installId": "ABC"}', encoding="utf-8")

    assert re.fullmatch(r"[0-9a-f]{32}", install.install_id(tmp_path))
