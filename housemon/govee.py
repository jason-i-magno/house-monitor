"""Govee H5075 thermo-hygrometers, read from their Bluetooth broadcasts.

An H5075 needs no app, account or pairing: it keeps broadcasting its name,
GVH5075_ and the last four hex digits of its address, and its reading, in the
manufacturer data under Govee's company id. Listening takes bleak, the one
dependency beyond the standard library (requirements.txt), imported only when
a scan starts, so the rest of the app runs without it.
"""

import importlib.util
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .readings import Reading

COMPANY = 0xEC88  # Govee's company id, under which the H5075 broadcasts its reading
INTERVAL = 10.0  # seconds between each room's readings, at most
MISSING = 300.0  # seconds without a broadcast from a sensor the house file names before it's warned of
RETRY = 60.0  # seconds before scanning again after it fails, as with no Bluetooth adapter
SCAN = 15.0  # seconds --govee-scan listens

Heard = Callable[[str, bytes, int | None], None]  # a broadcast: the sensor's name, its manufacturer data, its signal


@dataclass(frozen=True)
class Broadcast:
    """An H5075's reading, as it broadcasts it, and when and how strongly it was heard."""

    name: str
    temperature_c: float
    humidity: float
    battery: int  # percent
    rssi: int | None  # dBm
    at: float  # Unix time


def available() -> bool:
    """Whether bleak is installed, so the sensors can be heard."""
    return importlib.util.find_spec("bleak") is not None


def decode(data: bytes) -> tuple[float, float, int] | None:
    """The temperature (°C), humidity (percent) and battery (percent) in an H5075's manufacturer data under COMPANY,
    or None for anything else. Its six bytes: a zero; three, big-endian, packing the temperature in tenths of a degree
    times 1000 plus the humidity in tenths of a percent, the top bit set for a temperature below zero; the battery,
    its top bit set for an error; and a zero."""
    if len(data) != 6 or data[4] & 0x80:
        return None
    packed = int.from_bytes(data[1:4], "big")
    value = packed & 0x7FFFFF
    temperature = value // 1000 / 10
    return (-temperature if packed & 0x800000 else temperature), value % 1000 / 10, data[4] & 0x7F


def scan_bluetooth(heard: Heard, stop: threading.Event) -> None:
    """Listen for Govee broadcasts with bleak until stop is set, calling heard for each. A broadcast that leaves out
    the sensor's name, as one can, takes the name the sensor last broadcast from its address."""
    import asyncio

    from bleak import BleakScanner

    names: dict[str, str] = {}

    def detected(device, advertisement) -> None:
        data = advertisement.manufacturer_data.get(COMPANY)
        name = advertisement.local_name or device.name
        if name:
            names[device.address] = name
        name = names.get(device.address)
        if data is not None and name:
            heard(name, bytes(data), advertisement.rssi)

    async def listen() -> None:
        # Every broadcast, not just those that differ from the last: BlueZ drops repeats unless told, so a sensor whose
        # reading held steady would seem to have gone quiet.
        async with BleakScanner(detection_callback=detected, bluez={"filters": {"DuplicateData": True}}):
            while not stop.is_set():
                await asyncio.sleep(0.5)

    asyncio.run(listen())


class GoveeSource:
    """Readings for the rooms Govee H5075s read, from their broadcasts. rooms() maps each sensor's name, as it
    broadcasts it, to the sensor id of the room it reads; it's looked up every `interval` seconds, so a changed house
    file takes effect, and a name matches whatever its case. Each room gets the newest broadcast heard from its sensor
    since its last reading, once every `interval` seconds at most, so a sensor out of range gives none. A name not heard
    from for `missing` seconds is warned about, with the names heard, once until it's heard again."""

    def __init__(
        self,
        rooms: Callable[[], dict[str, str]],
        interval: float = INTERVAL,
        missing: float = MISSING,
        warn: Callable[[str], None] | None = None,
        scan: Callable[[Heard, threading.Event], None] = scan_bluetooth,
        retry: float = RETRY,
    ):
        self.rooms = rooms
        self.interval = interval
        self.missing = missing
        self.warn = warn or (lambda message: print(f"govee: {message}", file=sys.stderr, flush=True))
        self.scan = scan
        self.retry = retry
        self.started = time.time()
        self._lock = threading.Lock()
        self._heard: dict[str, Broadcast] = {}  # the newest broadcast from each sensor, by its name casefolded
        self._read: dict[str, float] = {}  # when the broadcast each sensor's room last read was heard
        self.unheard: set[str] = set()  # the names warned about, casefolded

    def heard(self, name: str, data: bytes, rssi: int | None, now: float | None = None) -> None:
        """Note a broadcast; one that isn't an H5075's reading is let go."""
        reading = decode(data)
        if reading is None:
            return
        broadcast = Broadcast(name, *reading, rssi, time.time() if now is None else now)
        with self._lock:
            self._heard[name.casefold()] = broadcast

    def heard_from(self) -> list[Broadcast]:
        """The newest broadcast from each sensor heard, by name."""
        with self._lock:
            return sorted(self._heard.values(), key=lambda b: b.name.casefold())

    def due(self, now: float) -> list[Reading]:
        """Each room's reading from its sensor's newest broadcast, for the rooms heard from since their last."""
        names = self.rooms()
        with self._lock:
            heard = dict(self._heard)
        readings = []
        for name, sensor_id in names.items():
            b = heard.get(name.casefold())
            if b and b.at > self._read.get(name.casefold(), float("-inf")):
                self._read[name.casefold()] = b.at
                readings.append(Reading(sensor_id, b.at, b.temperature_c, b.humidity, b.battery, b.rssi))
        self._check(names, heard, now)
        return readings

    def _check(self, names: dict[str, str], heard: dict[str, Broadcast], now: float) -> None:
        """Warn of the names not heard from for `missing` seconds, but those warned of already."""
        since = now - self.missing
        if self.started > since:
            return  # not listening long enough to tell
        quiet = sorted(
            (name for name in names if (b := heard.get(name.casefold())) is None or b.at < since), key=str.casefold
        )
        new = [name for name in quiet if name.casefold() not in self.unheard]
        if new:
            near = ", ".join(b.name for b in sorted(heard.values(), key=lambda b: b.name.casefold()) if b.at >= since)
            self.warn(
                f"nothing heard from {', '.join(repr(name) for name in new)}, as the house file names it, for"
                f" {self.missing / 60:g} minutes; heard from {near or 'no Govee sensor'}"
            )
        self.unheard = {name.casefold() for name in quiet}

    def _listen(self, stop: threading.Event) -> None:
        """Scan until stop is set, starting again after a failure, as when Bluetooth is off."""
        while not stop.is_set():
            try:
                self.scan(self.heard, stop)
            except Exception as e:  # whatever stops the scan, the rest of the house carries on
                self.warn(f"couldn't listen for its sensors: {e or type(e).__name__}; trying again in {self.retry:g} s")
                stop.wait(self.retry)

    def run(self, publish: Callable[[Reading], None], stop: threading.Event) -> None:
        """Call publish(reading) for each room's new readings every `interval` seconds until stop is set, listening
        while any room names a sensor."""
        self.started = time.time()
        listener: threading.Thread | None = None
        while not stop.wait(self.interval):
            if listener is None and self.rooms():
                listener = threading.Thread(target=self._listen, args=(stop,), name="govee-scan", daemon=True)
                listener.start()
            for reading in self.due(time.time()):
                publish(reading)
        if listener:
            listener.join()


def scan_interactively(
    rooms: dict[str, str], seconds: float = SCAN, scan: Callable[[Heard, threading.Event], None] = scan_bluetooth
) -> None:
    """Listen for `seconds` and list the Govee sensors heard, with their readings and the room the house file gives
    each, for naming them in it. rooms maps each name the house file gives to its room's name. Raises what stops the
    scan, as with Bluetooth off."""
    source = GoveeSource(dict, scan=scan)
    stop = threading.Event()
    failed: list[Exception] = []

    def listen() -> None:
        try:
            scan(source.heard, stop)
        except Exception as e:
            failed.append(e)
            stop.set()

    listener = threading.Thread(target=listen, daemon=True)
    print(f"Listening for Govee sensors for {seconds:g} seconds...", flush=True)
    listener.start()
    stop.wait(seconds)
    stop.set()
    listener.join()
    if failed:
        raise failed[0]
    heard = source.heard_from()
    if not heard:
        print("Heard none. Is Bluetooth on, and a sensor nearby with its batteries in?")
        return
    named = {name.casefold(): room for name, room in rooms.items()}
    for b in heard:
        signal = "" if b.rssi is None else f", signal {b.rssi} dBm"
        room = named.get(b.name.casefold())
        where = f", the house file's {room}" if room else ""
        reading = f"{b.temperature_c * 9 / 5 + 32:.1f}°F, {b.humidity:.1f}% humidity, battery {b.battery}%"
        print(f"  {b.name!r}: {reading}{signal}{where}")
    print('Give a room in the house file govee="<name>" for its readings to come from that sensor. To tell them apart,')
    print("hold one in your hand a minute and scan again: its temperature rises.")
