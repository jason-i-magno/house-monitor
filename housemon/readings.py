"""The records every sensor source produces."""

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
