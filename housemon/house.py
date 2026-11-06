"""Which house the simulator models: your own, from a house file, or else the example.

A house file is a Python module like example_house.py: HOUSE, its rooms as
SimulatedRoom, with each sensor's plan; AREAS, the parts with no sensor; and
DETAILS, what the map's detailed view draws, or None. Keep your own house in a
repo of its own cloned into private/, which this repo ignores, as
private/house.py, and it's used in place of the example.
"""

import importlib.util
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
