import logging
import os
import sys
from pathlib import Path

import pytest

# The fake `orca` module stands in for OrcaSlicer's embedded one.
sys.path.insert(0, str(Path(__file__).parent / "fake_orca"))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


@pytest.fixture
def plugin_log():
    """The records the plugin logs. Its logger does not propagate (plugin.py sends it to Orca's
    stderr tee), so caplog does not see them."""
    records = []

    class ListHandler(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("printlog_orca")
    handler = ListHandler(logging.INFO)
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield records
    logger.removeHandler(handler)
    logger.setLevel(previous)
