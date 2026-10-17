import os
import sys
from pathlib import Path

# The fake `orca` module stands in for OrcaSlicer's embedded one.
sys.path.insert(0, str(Path(__file__).parent / "fake_orca"))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
