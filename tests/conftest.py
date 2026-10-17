import logging
import os
import sys
import webbrowser
from pathlib import Path

import pytest

# The fake `orca` module stands in for OrcaSlicer's embedded one.
sys.path.insert(0, str(Path(__file__).parent / "fake_orca"))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import orca_driver  # noqa: E402
from orca import _audit  # noqa: E402


@pytest.fixture(autouse=True)
def browser(monkeypatch):
    """No test opens a real browser: webbrowser.open behaves as on Windows and records URLs."""
    fake = orca_driver.Browser()
    monkeypatch.setattr(webbrowser, "open", fake)
    return fake


@pytest.fixture(autouse=True)
def audit_guard():
    """Fails a test in which the plugin did I/O outside an audit context (a thread it started, a
    notification's on_click, import): that skips the user's consent."""
    _audit.reset()
    yield
    try:
        if _audit.strict:
            assert _audit.unaudited == [], (
                f"plugin I/O outside an audit context: {_audit.unaudited}"
            )
    finally:
        _audit.reset()


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
