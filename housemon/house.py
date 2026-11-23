"""Which house the simulator models: your own, from a house file, or else the example.

A house file is a Python module like example_house.py: HOUSE, its rooms as
SimulatedRoom, with each sensor's plan; AREAS, the parts with no sensor; and
DETAILS, what the map's detailed view draws, or None; and if it likes,
VACUUM_NAMES, the names to show its robot vacuums by, and GARDEN, where its
garden is and its frost dates, for the garden planner (garden.py). Keep your own house in a
repo of its own cloned into private/, which this repo ignores, as
private/house.py, and it's used in place of the example.
"""

import importlib.util
import sys
import traceback
from pathlib import Path
from types import ModuleType

from . import garden as gardens

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


def vacuum_names(module: ModuleType) -> dict[str, str]:
    """A house file's VACUUM_NAMES, none if it has none: each robot vacuum's name, as MOVA gives it (the one its app
    shows, else its model's) and matched whatever its case, to the name the page shows it by. A vacuum it doesn't name
    keeps MOVA's."""
    names = getattr(module, "VACUUM_NAMES", {})
    if not isinstance(names, dict) or not all(isinstance(n, str) and n and isinstance(m, str) and m for n, m in names.items()):
        raise ValueError("VACUUM_NAMES maps each vacuum's name, as MOVA gives it, to the name to show it by, each a string, not empty")
    if len({name.casefold() for name in names}) < len(names):
        raise ValueError("VACUUM_NAMES names a vacuum twice, in different cases")
    return dict(names)


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
            vacuum_names(module)
            gardens.garden(module)
        except Exception:
            print(f"The house file {self.path} didn't load, so the last one stays:", file=sys.stderr)
            traceback.print_exc()
            return None
        self.module = module
        return module
