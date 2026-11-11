"""The records every sensor source produces, a litter box's state, and what a robot vacuum is doing."""

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


# A Litter-Robot's status codes, as it reports them: what each says, and whether it needs someone to see to it.
LITTER_STATUSES = {
    "RDY": ("Ready", False),
    "CSI": ("Cat sensor interrupted", False),  # a cat in it, say
    "CD": ("Cat detected", False),
    "CST": ("Waiting to clean", False),  # timing the wait after the cat sensor clears
    "CCP": ("Cleaning", False),
    "CCC": ("Clean cycle complete", False),
    "EC": ("Empty cycle", False),
    "PWRU": ("Powering up", False),
    "PWRD": ("Powering down", False),
    "DF1": ("Drawer almost full, 2 cycles left", True),
    "DF2": ("Drawer almost full, 1 cycle left", True),
    "DFS": ("Drawer full", True),
    "SDF": ("Drawer full at startup", True),
    "BR": ("Bonnet removed", True),
    "P": ("Clean cycle paused", True),
    "PD": ("Pinch detected", True),
    "SPF": ("Pinch detected at startup", True),
    "CSF": ("Cat sensor fault", True),
    "SCF": ("Cat sensor fault at startup", True),
    "DPF": ("Dump position fault", True),
    "HPF": ("Home position fault", True),
    "DHF": ("Dump and home position fault", True),
    "OTF": ("Over torque fault", True),
    "OFF": ("Off", True),
    "OFFLINE": ("Offline", True),
}


@dataclass(frozen=True)
class LitterBox:
    """A self-cleaning litter box's latest state: a Litter-Robot's, as Whisker's cloud reports it, or the simulator's."""

    name: str  # the litter box in the house file's details it is
    ts: float  # Unix time, seconds, when it was read
    status: str  # its status code, as the robot reports it (LITTER_STATUSES)
    cycles: int  # clean cycles since its waste drawer was last emptied
    drawer: float  # how full its waste drawer is, percent, as the robot estimates from its cycles
    seen: float | None = None  # when the robot was last heard from, Unix time
    simulated: bool = False  # made up by the simulator, not read from a robot

    def to_json(self) -> dict:
        """Its state, with what its status says and whether it needs seeing to."""
        text, attention = LITTER_STATUSES.get(self.status, (f"Unknown status {self.status or '(none)'}", True))
        return {**asdict(self), "text": text, "attention": attention}


@dataclass(frozen=True)
class Vacuum:
    """What a robot vacuum is doing, as its maker's cloud last said: not a reading of a room, so not stored."""

    id: str
    name: str  # as its app shows it
    state: str  # what it's doing, as "Charging"; "Offline" when its cloud can't reach it
    battery: int | None  # percent
    ts: float  # Unix time, seconds
    error: bool = False  # stopped by a problem, which its app says more about

    def to_json(self) -> dict:
        return asdict(self)
