"""The records every sensor source produces, and what a robot vacuum is doing."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Plan:
    """Where a room is in the house, in feet, for the house map.

    x and y place the room's back-left corner on one plan shared by every floor
    (x to the right, y toward the front), elevation is the height of its floor
    above the main floor's, and height is from its floor to its ceiling at the
    ceiling's highest. A sloped ceiling has a drop: the wall it slopes down to,
    and how much lower it is there. A room that isn't a rectangle, such as an
    L, has a tuple of plans, one per piece.
    """

    x: float
    y: float
    width: float
    depth: float
    elevation: float = 0.0
    height: float = 8.0
    drop: tuple[str, float] | None = None  # ("left", "right", "back" or "front", feet)

    def __post_init__(self):
        if self.drop and self.drop[0] not in ("left", "right", "back", "front"):
            raise ValueError(f"a ceiling drops to the left, right, back or front, not {self.drop[0]!r}")


@dataclass(frozen=True)
class Sensor:
    """A sensor and the room it sits in."""

    id: str
    name: str
    floor: str
    plan: Plan | tuple[Plan, ...] | None = None  # without one, the map lays the floor's rooms out in a row
    heated: bool = True  # False for a space like the garage, whose temperature the page charts apart
    outside: bool = False  # outdoors: no plan, and the map shows its reading apart from the rooms


@dataclass(frozen=True)
class Area:
    """A part of the house with no sensor, such as the garage, drawn on the map so the house looks whole."""

    name: str
    floor: str
    plan: Plan | tuple[Plan, ...]


@dataclass(frozen=True)
class Reading:
    """One measurement, in the units the sensors themselves report."""

    sensor_id: str
    ts: float  # Unix time, seconds
    temperature_c: float
    humidity: float | None  # relative humidity, percent; None from a sensor that reads only temperature
    battery: int | None = None  # percent
    rssi: int | None = None  # Bluetooth signal strength, dBm
    simulated: bool = False  # made up by the simulator, not read from a sensor

    def to_json(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class VacuumPart:
    """A part of a robot vacuum it counts down the life of: one to replace, as a brush, or to clean, as its sensors."""

    name: str  # as "filter" or "sensors"
    left: int  # percent of its life left
    replace: bool  # replaced when it runs out, else cleaned


@dataclass(frozen=True)
class Vacuum:
    """What a robot vacuum is doing, as its maker's cloud last said: not a reading of a room, so not stored."""

    id: str
    name: str  # as its app shows it
    state: str  # what it's doing, as "Charging"; "Offline" when its cloud can't reach it
    battery: int | None  # percent
    ts: float  # Unix time, seconds
    error: bool = False  # stopped by a problem
    problem: str | None = None  # the problem, or a warning, in words, as "Wheels are suspended"
    dock: tuple[str, ...] = ()  # what's wrong at its dock, in words, as "Clean water tank is low"
    parts: tuple[VacuumPart, ...] = ()  # the parts it counts down, those it reports

    def to_json(self) -> dict:
        return asdict(self)
