"""Simulated sensors, so the app runs before real ones exist.

Every value is a pure function of the room and the time, so the history the
simulator backfills matches what it later reports live. Each room runs on the
main floor's thermostat, offset from it (upstairs warm, a basement cold), or
holds near 68 °F on its own; follows a share of the weather outside; and can
have a space heater, cooking or showers. The rooms themselves are a house
file's HOUSE: example_house.py, or your own. A litter box the house file
names a Litter-Robot for is simulated too, until Whisker reads it: a cat
uses it four times a day, and its drawer is emptied on Sunday mornings.
"""

import datetime
import functools
import math
import random
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from .details import Details
from .readings import Area, LitterBox, Reading, Sensor

LITTER_VISITS = (7.0, 12.5, 18.0, 22.5)  # about when, in hours, a cat uses a simulated litter box each day
LITTER_INSIDE = 2 * 60  # seconds the cat is in it, interrupting its cat sensor
LITTER_WAIT = 7 * 60  # seconds it waits after the cat sensor clears, as a Litter-Robot 3 does unless set otherwise
LITTER_CYCLE = 150  # seconds a clean cycle takes
LITTER_CAPACITY = 30  # cycles its waste drawer holds, as a Litter-Robot 3 first estimates
LITTER_EMPTIED = 10.0  # the hour on Sunday its drawer is emptied


@dataclass(frozen=True)
class SimulatedRoom:
    sensor: Sensor
    offset_f: float  # steady difference from the thermostat setpoint
    outdoor_gain: float  # share of the outdoor temperature swing that reaches the room
    sun_f: float  # afternoon warming from the sun, at its peak
    humidity: float  # typical relative humidity, percent
    cooking: bool = False  # humidity spikes at breakfast and dinner
    showers: tuple[float, ...] = ()  # hours someone showers: humidity spikes, then the fan clears it
    radiator: bool = False  # a space heater runs during the work day
    thermostat: bool = True  # on the main thermostat's schedule; if not, it holds near 68°F plus offset_f
    battery: int = 100
    rssi: int = -70
    # The name of the ecobee sensor in the room, as the ecobee app shows it: signed in to ecobee, the room's readings
    # come from it instead (ecobee.py).
    ecobee: str | None = None
    # For the outside, the name of an ecobee thermostat: signed in, its readings come from the weather ecobee reports
    # where the thermostat is instead.
    ecobee_weather: str | None = None
    # The name a Govee H5075 in the room broadcasts, as python3 -m housemon --govee-scan lists it: with bleak installed,
    # the room's readings come from it instead (govee.py). Beside an ecobee sensor or thermostat's weather, the Govee's
    # readings come while it's heard, and the ecobee's fill in while it isn't (govee.unless_heard).
    govee: str | None = None

    def __post_init__(self):
        if self.ecobee and self.ecobee_weather:
            raise ValueError(f"{self.sensor.name} reads an ecobee sensor or a thermostat's weather, not both")


def _smoothstep(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def setpoint_f(hour: float) -> float:
    """The main-floor thermostat: 68°F from 6 am to 10 pm, 64°F overnight."""
    if hour < 6:
        return 64.0
    if hour < 7:
        return 64.0 + 4.0 * _smoothstep(hour - 6)  # an hour to warm up
    if hour < 22:
        return 68.0
    return 68.0 - 4.0 * _smoothstep((hour - 22) / 2)  # two hours to cool down


def outdoor_f(hour: float) -> float:
    """A late-fall day: 26°F before dawn, 50°F mid-afternoon."""
    return 38.0 + 12.0 * math.sin(2 * math.pi * (hour - 9) / 24)


def radiator_f(hour: float, t: float) -> float:
    """The office space heater: on from 8 am to 6 pm, cycling on its own thermostat."""
    if hour < 8 or hour >= 19:
        return 0.0
    share = _smoothstep((hour - 8) / 0.75) if hour < 18 else 1 - _smoothstep(hour - 18)
    return share * (4.5 + 0.6 * math.sin(2 * math.pi * t / (35 * 60)))


@functools.cache
def _phases(sensor_id: str) -> tuple[float, ...]:
    rng = random.Random(sensor_id)
    return tuple(rng.uniform(0, 2 * math.pi) for _ in range(3))


def reading_at(room: SimulatedRoom, t: float) -> Reading:
    local = time.localtime(t)
    hour = local.tm_hour + local.tm_min / 60 + local.tm_sec / 3600
    phase = _phases(room.sensor.id)
    noise = random.Random(f"{room.sensor.id}:{int(t // 30)}")  # sensor noise, steady for 30 s

    temp_f = (
        (setpoint_f(hour) if room.thermostat else 68.0)
        + room.offset_f
        + room.outdoor_gain * (outdoor_f(hour) - 38.0)
        + 0.4 * math.sin(2 * math.pi * t / (3.3 * 3600) + phase[0])
        + 0.25 * math.sin(2 * math.pi * t / (7.9 * 3600) + phase[1])
        + noise.uniform(-0.1, 0.1)
    )
    if 10 <= hour <= 17:
        temp_f += room.sun_f * math.sin(math.pi * (hour - 10) / 7)
    if room.radiator:
        temp_f += radiator_f(hour, t)
    shower = sum(math.exp(-(((hour - h) / 0.3) ** 2)) for h in room.showers)  # about half an hour each
    temp_f += 1.5 * shower

    # Warming air without adding water lowers its relative humidity.
    humidity = (
        room.humidity
        - 1.2 * (temp_f - (68.0 + room.offset_f))
        + 1.0 * math.sin(2 * math.pi * t / (5.1 * 3600) + phase[2])
        + noise.uniform(-0.3, 0.3)
    )
    if room.cooking:
        humidity += 8 * math.exp(-(((hour - 7.5) / 0.5) ** 2)) + 10 * math.exp(-(((hour - 18.5) / 0.6) ** 2))
    humidity += 28 * shower

    return Reading(
        sensor_id=room.sensor.id,
        ts=t,
        temperature_c=round((temp_f - 32) * 5 / 9, 1),
        humidity=round(min(max(humidity, 5.0), 95.0), 1),
        battery=room.battery,
        rssi=room.rssi + round(noise.uniform(-4, 4)),
        simulated=True,
    )


def _local(day: datetime.date, hour: float) -> float:
    """The Unix time of an hour of a local day."""
    whole = int(hour)
    return time.mktime((day.year, day.month, day.day, whole, 0, 0, 0, 0, -1)) + (hour - whole) * 3600


def _visits(name: str, day: datetime.date) -> list[float]:
    """When a cat comes to the litter box `name` on a local day: about at LITTER_VISITS, each up to an hour off, as
    seeded from its name and the day."""
    rng = random.Random(f"{name}:{day.isoformat()}")
    return [_local(day, hour + rng.uniform(-1, 1)) for hour in LITTER_VISITS]


def litter_box_at(name: str, t: float) -> LitterBox:
    """A simulated Litter-Robot's state at t: its cat sensor interrupted while a cat is in it, waiting to clean after
    it leaves, then cleaning, a cycle more in its drawer for each, and ready between, its drawer almost full when it
    has two cycles left or fewer."""
    today = datetime.date.fromtimestamp(t)
    emptied_on = today - datetime.timedelta(days=(today.weekday() - 6) % 7)  # the last Sunday, or today
    emptied = _local(emptied_on, LITTER_EMPTIED)
    if emptied > t:
        emptied_on -= datetime.timedelta(days=7)
        emptied = _local(emptied_on, LITTER_EMPTIED)
    done = LITTER_INSIDE + LITTER_WAIT + LITTER_CYCLE  # from a visit to its cycle's end
    days = [emptied_on + datetime.timedelta(days=n) for n in range(-1, (today - emptied_on).days + 1)]
    visits = [v for day in days for v in _visits(name, day)]
    cycles = sum(1 for v in visits if emptied < v + done <= t)
    status = "RDY"
    if LITTER_CAPACITY - cycles <= 2:
        status = {2: "DF1", 1: "DF2"}.get(LITTER_CAPACITY - cycles, "DFS")
    for v in visits:
        if v <= t < v + done:
            status = "CSI" if t < v + LITTER_INSIDE else "CST" if t < v + LITTER_INSIDE + LITTER_WAIT else "CCP"
    drawer = round(min(cycles / LITTER_CAPACITY, 1) * 100, 1)
    return LitterBox(name, t, status, cycles, drawer, seen=t, simulated=True)


class SimulatedSource:
    """Reports each room every `interval` seconds, staggered like real broadcasts, but the rooms whose sensor ids are
    in `skip`, which another source reads."""

    def __init__(
        self,
        rooms: tuple[SimulatedRoom, ...],
        interval: float = 10.0,
        areas: tuple[Area, ...] = (),
        details: Details | None = None,
    ):
        self.rooms = rooms
        self.interval = interval
        self.areas = list(areas)
        self.details = details
        self.skip: set[str] = set()
        self.skip_boxes: set[str] = set()  # the litter boxes another source reads, by name

    @property
    def sensors(self) -> list[Sensor]:
        return [room.sensor for room in self.rooms]

    def simulated(self) -> list[SimulatedRoom]:
        """The rooms it reports, all but those another source reads."""
        return [room for room in self.rooms if room.sensor.id not in self.skip]

    def litter_boxes(self) -> list[str]:
        """The litter boxes it reports, by name: those naming a Litter-Robot, but those another source reads."""
        names = self.details.litter_robots().values() if self.details else ()
        return [name for name in names if name not in self.skip_boxes]

    def backfill(self, since: float, until: float, step: float) -> Iterator[Reading]:
        """The readings the simulator would have stored had it been running."""
        rooms = self.simulated()
        t = math.ceil(since / step) * step
        while t < until:
            for room in rooms:
                yield reading_at(room, t)
            t += step

    def run(self, publish: Callable[[Reading | LitterBox], None], stop: threading.Event) -> None:
        """Call publish(reading) for every new reading, and publish(state) for each litter box's state once a round,
        until stop is set. Each round goes through the rooms as they are then, so rooms swapped in while it runs, from
        a changed house file, get their turns."""
        due = time.time()
        while True:
            for name in self.litter_boxes():
                publish(litter_box_at(name, time.time()))
            rooms = self.simulated()
            step = self.interval / max(1, len(rooms))
            for room in rooms:
                if stop.wait(max(0.0, due - time.time())):
                    return
                publish(reading_at(room, time.time()))
                # After a suspend, carry on from now rather than catching up in a burst.
                due = max(due + step, time.time())
            if not rooms and stop.wait(self.interval):
                return
