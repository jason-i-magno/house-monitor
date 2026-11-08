"""Which house the simulator models: your own, from a house file, or else the example.

A house file is a Python module like example_house.py: HOUSE, its rooms as
SimulatedRoom, with each sensor's plan; AREAS, the parts with no sensor; and
DETAILS, what the map's detailed view draws, or None. Keep your own house in a
repo of its own cloned into private/, which this repo ignores, as
private/house.py, and it's used in place of the example.
"""

import importlib.util
import sys
import traceback
from pathlib import Path
from types import ModuleType

PRIVATE = Path(__file__).resolve().parent.parent / "private" / "house.py"


def load(path: Path | None = None) -> ModuleType:
    """The house file at path; else private/house.py, if it's there; else the example house."""
    if path is None and PRIVATE.exists():
        path = PRIVATE
    if path is None:
        from . import example_house

        return example_house
    spec = importlib.util.spec_from_file_location("house", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HouseFile:
    """A house file, as load() picks it, loaded again when it changes on disk, so edits to it show up on the page's
    next reload without restarting the server."""

    def __init__(self, path: Path | None = None):
        self.module = load(path)
        self.path = Path(self.module.__file__).resolve()
        self.mtime = self._mtime()

    def _mtime(self) -> float | None:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None

    def changed(self) -> ModuleType | None:
        """The house file loaded again if it's changed since it was last loaded, else None. If it doesn't load, as
        while it's half edited, the error goes to stderr and the last house stays."""
        mtime = self._mtime()
        if mtime is None or mtime == self.mtime:
            return None
        self.mtime = mtime
        try:
            module = load(self.path)
            for name in ("HOUSE", "AREAS", "DETAILS"):
                getattr(module, name)
        except Exception:
            print(f"The house file {self.path} didn't load, so the last one stays:", file=sys.stderr)
            traceback.print_exc()
            return None
        self.module = module
        return module
