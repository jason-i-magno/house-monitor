"""The garden planner's settings and plans.

A house file can say where its garden is with GARDEN, a Garden: how far north it is and which way its plan faces,
for the sun's path, and its frost dates, which planting goes by. The page plans each of the house's planted beds
(details' grounds of the kind "bed", in soil or mulch) a square foot at a time, from the plants in web/plants.json,
and saves what's planted in each, by its name, a bed at a time, to a JSON file (data/garden.json), which Plans
keeps.
"""

import datetime
import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

PLANT_ID = re.compile(r"[a-z][a-z0-9-]{0,39}")  # as plants.json names each plant
MAX_PLANTINGS = 2000  # in a bed, far more than one has square feet
MAX_SQUARE = 500  # squares along a bed's side, from 0
MAX_BYTES = 256 * 1024  # the most a bed's plan may take, as the page sends it


@dataclass(frozen=True)
class Garden:
    """Where a house's garden is, for the planner. latitude is how far north it is, in degrees (south below 0), and
    facing is the compass bearing its plan's front (y) faces, 0 for north and 90 for east: together they give the
    sun's path over its beds, and the shade the house, trees and fences cast on them. last_frost and first_frost are
    the average dates, each (month, day), of the last frost in spring and the first in fall, which planting and
    harvest go by; the planner's calendar runs January to December, so the last comes before the first."""

    latitude: float
    facing: float
    last_frost: tuple[int, int]
    first_frost: tuple[int, int]

    def __post_init__(self):
        if not -66 < self.latitude < 66:
            raise ValueError(f"A garden's latitude is in degrees, between the polar circles, not {self.latitude}")
        if not 0 <= self.facing < 360:
            raise ValueError(f"Which way a garden's plan faces is a compass bearing, from 0 up to 360, not {self.facing}")
        days = []
        for name in ("last_frost", "first_frost"):
            value = getattr(self, name)
            try:
                month, day = value
                days.append(datetime.date(2001, month, day))  # a year with no February 29
            except (TypeError, ValueError):
                raise ValueError(f"A garden's {name} is a date as (month, day), not {value!r}") from None
        if days[0] >= days[1]:
            raise ValueError("A garden's last frost in spring comes before its first frost in fall")


def garden(module: ModuleType) -> Garden | None:
    """A house file's GARDEN, or None if it has none."""
    value = getattr(module, "GARDEN", None)
    if value is not None and not isinstance(value, Garden):
        raise ValueError("GARDEN is a Garden, saying where the garden is and its frost dates")
    return value


def check_bed(bed: object, plants: object) -> None:
    """Raises ValueError unless bed is a bed's name and plants what's planted in it: each a plant's id, as plants.json
    has it, and the square foot it's in, x along the bed from its left and y from its back, both from 0, and for a
    plant that takes several squares, the back-left one of them."""
    if not isinstance(bed, str) or not 0 < len(bed) <= 200:
        raise ValueError("A bed is named, in at most 200 characters")
    if not isinstance(plants, list) or len(plants) > MAX_PLANTINGS:
        raise ValueError(f"A bed's plants are a list of at most {MAX_PLANTINGS}")
    for planting in plants:
        if not isinstance(planting, dict) or set(planting) != {"plant", "x", "y"}:
            raise ValueError("Each planting is a plant's id and its square, x and y")
        if not isinstance(planting["plant"], str) or not PLANT_ID.fullmatch(planting["plant"]):
            raise ValueError(f"{planting['plant']!r} isn't a plant's id")
        for axis in ("x", "y"):
            value = planting[axis]
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < MAX_SQUARE:
                raise ValueError(f"A planting's {axis} is a square from 0 to {MAX_SQUARE - 1}, not {value!r}")


class Plans:
    """What's planted in each bed, by the bed's name, kept in a JSON file as {"beds": {name: [planting, ...]}}, each
    planting as check_bed has it. A bed is saved on its own, so a page left open a while changes only the bed it
    edits, not the others; saving a bed empty leaves it out. Each save writes a new file and moves it into place, so
    the file is always whole."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> dict:
        """Every bed's plantings, none for a file that isn't there yet."""
        try:
            text = self.path.read_text()
        except FileNotFoundError:
            return {"beds": {}}
        plans = json.loads(text)
        if not isinstance(plans, dict) or not isinstance(plans.get("beds"), dict):
            raise ValueError(f"{self.path} isn't the garden's plans")
        return plans

    def save_bed(self, bed: str, plants: list) -> dict:
        """Saves one bed's plantings in place of what it had, and returns every bed's."""
        check_bed(bed, plants)
        with self._lock:
            plans = self.load()
            if plants:
                plans["beds"][bed] = plants
            else:
                plans["beds"].pop(bed, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.")
            try:
                with os.fdopen(fd, "w") as file:
                    json.dump(plans, file, indent=1)
                os.replace(temporary, self.path)
            except BaseException:
                os.unlink(temporary)
                raise
            return plans
