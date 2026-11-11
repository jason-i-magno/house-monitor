"""Simulated sensors, so the app runs before real ones exist.

Every value is a pure function of the room and the time, so the history the
simulator backfills matches what it later reports live. Each room runs on the
main floor's thermostat, offset from it (upstairs warm, a basement cold), or
holds near 68 °F on its own; follows a share of the weather outside; and can
have a space heater, cooking or showers. The rooms themselves are a house
file's HOUSE: example_house.py, or your own.
"""

import functools
import math
import random
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from .details import Details
from .readings import Area, Reading, Sensor


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
    # the room's readings come from it instead (govee.py).
    govee: str | None = None

    def __post_init__(self):
        named = [field for field in ("ecobee", "ecobee_weather", "govee") if getattr(self, field)]
        if len(named) > 1:
            raise ValueError(
                f"{self.sensor.name} reads from one sensor, so it names one of ecobee, ecobee_weather and govee, not"
                f" {' and '.join(named)}"
            )


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

    @property
    def sensors(self) -> list[Sensor]:
        return [room.sensor for room in self.rooms]

    def simulated(self) -> list[SimulatedRoom]:
        """The rooms it reports, all but those another source reads."""
        return [room for room in self.rooms if room.sensor.id not in self.skip]

    def backfill(self, since: float, until: float, step: float) -> Iterator[Reading]:
        """The readings the simulator would have stored had it been running."""
        rooms = self.simulated()
        t = math.ceil(since / step) * step
        while t < until:
            for room in rooms:
                yield reading_at(room, t)
            t += step

    def run(self, publish: Callable[[Reading], None], stop: threading.Event) -> None:
        """Call publish(reading) for every new reading until stop is set. Each round goes through the rooms as they
        are then, so rooms swapped in while it runs, from a changed house file, get their turns."""
        due = time.time()
        while True:
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
