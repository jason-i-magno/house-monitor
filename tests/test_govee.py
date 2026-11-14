import contextlib
import io
import sys
import threading
import time
import types
import unittest
import unittest.mock

from housemon import govee
from housemon.readings import Reading

KITCHEN = "GVH5075_1A2B"
OTHER = "GVH5075_FFFF"


def broadcast(temperature_c: float, humidity: float, battery: int) -> bytes:
    """An H5075's manufacturer data for a reading."""
    packed = round(abs(temperature_c) * 10) * 1000 + round(humidity * 10) | (0x800000 if temperature_c < 0 else 0)
    return bytes([0]) + packed.to_bytes(3, "big") + bytes([battery, 0])


class DecodeTest(unittest.TestCase):
    def test_decodes_the_temperature_humidity_and_battery(self):
        self.assertEqual(govee.decode(bytes.fromhex("000418a95e00")), (26.8, 45.7, 94))

    def test_decodes_a_temperature_below_zero(self):
        self.assertEqual(govee.decode(bytes.fromhex("0080ce416400")), (-5.2, 80.1, 100))
        self.assertEqual(govee.decode(broadcast(-0.3, 99.9, 7)), (-0.3, 99.9, 7))

    def test_lets_go_of_an_error_or_anything_else(self):
        self.assertIsNone(govee.decode(bytes.fromhex("000418a9de00")))  # the battery's top bit: an error
        self.assertIsNone(govee.decode(bytes.fromhex("0000e1076401006400")))  # another model's
        self.assertIsNone(govee.decode(b""))


class GoveeSourceTest(unittest.TestCase):
    def source(self, rooms: dict[str, str], **kwargs) -> govee.GoveeSource:
        self.warnings: list[str] = []
        source = govee.GoveeSource(lambda: rooms, warn=self.warnings.append, **kwargs)
        source.started = 0.0
        return source

    def test_reads_each_room_from_its_sensors_newest_broadcast(self):
        source = self.source({KITCHEN: "sim-kitchen"})
        source.heard(KITCHEN, broadcast(21.5, 40.2, 88), -70, now=100)
        source.heard(KITCHEN, broadcast(21.6, 40.1, 88), -72, now=105)
        self.assertEqual(source.due(110), [Reading("sim-kitchen", 105, 21.6, 40.1, 88, -72)])
        self.assertEqual(source.due(120), [])  # nothing new heard
        source.heard(KITCHEN, broadcast(21.6, 40.1, 88), -71, now=125)
        self.assertEqual(source.due(130), [Reading("sim-kitchen", 125, 21.6, 40.1, 88, -71)])

    def test_matches_a_name_whatever_its_case_and_leaves_the_rest(self):
        source = self.source({KITCHEN.lower(): "sim-kitchen"})
        source.heard(KITCHEN, broadcast(21.5, 40.2, 88), -70, now=100)
        source.heard(OTHER, broadcast(30.0, 20.0, 50), -90, now=100)
        source.heard(KITCHEN, bytes.fromhex("000418a9de00"), -70, now=101)  # an error, let go
        self.assertEqual([r.sensor_id for r in source.due(110)], ["sim-kitchen"])
        self.assertEqual([b.name for b in source.heard_from()], [KITCHEN, OTHER])

    def test_takes_up_a_changed_house_file(self):
        rooms = {KITCHEN: "sim-kitchen"}
        source = self.source(rooms)
        source.heard(OTHER, broadcast(19.0, 35.0, 60), -80, now=100)
        self.assertEqual(source.due(110), [])
        rooms[OTHER] = "sim-office"
        self.assertEqual([r.sensor_id for r in source.due(120)], ["sim-office"])

    def test_warns_once_of_a_sensor_not_heard_from(self):
        source = self.source({KITCHEN: "sim-kitchen"}, missing=300)
        source.due(0)  # named from the start
        source.heard(OTHER, broadcast(19.0, 35.0, 60), -80, now=290)
        source.due(310)
        self.assertEqual(len(self.warnings), 1)
        self.assertIn(repr(KITCHEN), self.warnings[0])
        self.assertIn(f"heard from {OTHER}", self.warnings[0])
        source.due(320)
        self.assertEqual(len(self.warnings), 1)  # warned of already
        source.heard(KITCHEN, broadcast(21.5, 40.2, 88), -70, now=330)
        source.due(340)
        self.assertEqual(len(self.warnings), 1)
        source.due(700)  # quiet again
        self.assertEqual(len(self.warnings), 2)
        self.assertIn("heard from no Govee sensor", self.warnings[1])

    def test_waits_to_warn_until_it_has_listened_long_enough(self):
        source = self.source({KITCHEN: "sim-kitchen"}, missing=300)
        source.started = None  # not listening yet
        source.due(0)
        source.due(1000)
        self.assertEqual(self.warnings, [])
        source.started = 1000.0
        source.due(1299)
        self.assertEqual(self.warnings, [])
        source.due(1300)
        self.assertEqual(len(self.warnings), 1)

    def test_gives_a_sensor_added_to_the_house_file_its_full_time(self):
        rooms = {KITCHEN: "sim-kitchen"}
        source = self.source(rooms, missing=300)
        source.due(0)
        for t in range(0, 1000, 10):
            source.heard(KITCHEN, broadcast(21.5, 40.2, 88), -70, now=t)
        rooms[OTHER] = "sim-office"  # added long after listening started
        source.due(400)
        source.due(699)
        self.assertEqual(self.warnings, [])
        source.due(700)
        self.assertEqual(len(self.warnings), 1)
        self.assertIn(repr(OTHER), self.warnings[0])
        self.assertNotIn(repr(KITCHEN), self.warnings[0])
        del rooms[OTHER]  # taken out, then named again: its time starts over
        source.due(710)
        rooms[OTHER] = "sim-office"
        source.due(720)
        source.due(1019)
        self.assertEqual(len(self.warnings), 1)

    def test_knows_whether_a_rooms_sensor_was_heard_lately(self):
        source = self.source({KITCHEN.lower(): "sim-kitchen"})
        self.assertFalse(source.hears("sim-kitchen", 100))
        source.heard(KITCHEN, broadcast(21.5, 40.2, 88), -70, now=100)
        self.assertTrue(source.hears("sim-kitchen", 100 + govee.BACKUP))
        self.assertFalse(source.hears("sim-kitchen", 101 + govee.BACKUP))
        self.assertFalse(source.hears("sim-office", 100))  # names no Govee sensor

    def test_another_source_for_a_room_fills_in_only_while_its_govee_is_quiet(self):
        source = self.source({KITCHEN: "sim-kitchen"})
        published: list[Reading] = []
        publish = govee.unless_heard(published.append, source, within=120)
        source.heard(KITCHEN, broadcast(21.5, 40.2, 88), -70, now=100)
        ecobee = [Reading("sim-kitchen", t, 21.0, None) for t in (150, 220, 230)] + [Reading("sim-office", 150, 19.0, None)]
        for reading in ecobee:
            publish(reading)
        self.assertEqual([(r.sensor_id, r.ts) for r in published], [("sim-kitchen", 230), ("sim-office", 150)])

    def test_publishes_what_it_hears_and_listens_again_after_a_failure(self):
        calls = []

        def scan(heard, stop):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("Bluetooth is off")
            heard(KITCHEN, broadcast(21.5, 40.2, 88), -70)
            stop.wait()

        source = self.source({KITCHEN: "sim-kitchen"}, interval=0.01, retry=0.01, scan=scan)
        published: list[Reading] = []
        stop = threading.Event()
        runner = threading.Thread(target=source.run, args=(published.append, stop))
        runner.start()
        deadline = time.time() + 5
        while not published and time.time() < deadline:
            time.sleep(0.01)
        stop.set()
        runner.join(5)
        self.assertFalse(runner.is_alive())
        self.assertEqual([(r.sensor_id, r.temperature_c, r.humidity, r.simulated) for r in published], [("sim-kitchen", 21.5, 40.2, False)])
        self.assertEqual(len(calls), 2)
        self.assertIn("Bluetooth is off", self.warnings[0])

    def test_doesnt_listen_while_no_room_names_a_sensor(self):
        calls = []
        source = self.source({}, interval=0.01, scan=lambda heard, stop: calls.append(1))
        stop = threading.Event()
        runner = threading.Thread(target=source.run, args=(lambda reading: None, stop))
        runner.start()
        time.sleep(0.1)
        stop.set()
        runner.join(5)
        self.assertEqual(calls, [])


class QuietScanTest(unittest.TestCase):
    """A scan whose adapter went away, as when it's unplugged for a moment, stops hearing without an error."""

    def fake_bleak(self, adverts, fail_stop=False):
        """A stand-in for bleak whose scanner, once started, hears these (name or None, address, data) and no more."""
        scanners = []

        class Scanner:
            def __init__(self, detection_callback, bluez):
                self.callback, self.bluez, self.stopped = detection_callback, bluez, False
                scanners.append(self)

            async def start(self):
                for name, address, data in adverts:
                    device = types.SimpleNamespace(address=address, name=None)
                    ad = types.SimpleNamespace(manufacturer_data={govee.COMPANY: data}, local_name=name, rssi=-70)
                    self.callback(device, ad)

            async def stop(self):
                self.stopped = True
                if fail_stop:
                    raise OSError("the adapter is gone")

        return unittest.mock.patch.dict(sys.modules, {"bleak": types.SimpleNamespace(BleakScanner=Scanner)}), scanners

    def test_hears_every_broadcast_and_remembers_a_sensors_name(self):
        patch, scanners = self.fake_bleak([(KITCHEN, "AA", broadcast(21.5, 40.2, 88)), (None, "AA", broadcast(21.6, 40.0, 88))])
        heard, stop = [], threading.Event()
        threading.Timer(0.2, stop.set).start()
        with patch:
            govee.scan_bluetooth(lambda *b: heard.append(b), stop, quiet=5)
        self.assertEqual([(name, govee.decode(data)[0]) for name, data, _ in heard], [(KITCHEN, 21.5), (KITCHEN, 21.6)])
        self.assertEqual(scanners[0].bluez, {"filters": {"DuplicateData": True}})
        self.assertTrue(scanners[0].stopped)

    def test_raises_quiet_when_it_hears_nothing_at_all(self):
        patch, scanners = self.fake_bleak([], fail_stop=True)  # and stopping it fails, its adapter gone
        with patch, self.assertRaises(govee.Quiet):
            govee.scan_bluetooth(lambda *b: None, threading.Event(), quiet=0.2)
        self.assertTrue(scanners[0].stopped)

    def test_starts_a_quiet_scan_again_at_once_warning_once_until_a_sensor_is_heard(self):
        calls, warnings = [], []
        rooms = {KITCHEN: "sim-kitchen"}

        def scan(heard, stop):
            calls.append(1)
            if len(calls) in (3, 5):  # heard in between the quiet spells
                heard(KITCHEN, broadcast(21.5, 40.2, 88), -70)
            if len(calls) < 6:
                raise govee.Quiet("heard no Bluetooth device at all for 30 s")
            stop.set()

        source = govee.GoveeSource(lambda: rooms, warn=warnings.append, scan=scan, retry=60)
        stop = threading.Event()
        started = time.time()
        with unittest.mock.patch.object(stop, "wait", lambda seconds: stop.is_set()):
            source._listen(stop)
        self.assertEqual(len(calls), 6)
        self.assertLess(time.time() - started, 5)  # not the 60 s wait after another failure
        # Quiet at calls 1 and 2 (warned once), heard at 3 then quiet (warned again), quiet at 4 (not), heard at 5 then
        # quiet (warned again).
        self.assertEqual(len(warnings), 3)
        self.assertIn("starting the scan again", warnings[0])


class ScanInteractivelyTest(unittest.TestCase):
    def test_lists_the_sensors_heard_and_the_rooms_they_read(self):
        def scan(heard, stop):
            heard(OTHER, broadcast(-1.0, 70.0, 40), None)
            heard(KITCHEN, broadcast(20.0, 45.5, 88), -70)

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            govee.scan_interactively({KITCHEN.lower(): "Kitchen"}, seconds=0.05, scan=scan)
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[1], f"  {KITCHEN!r}: 68.0°F, 45.5% humidity, battery 88%, signal -70 dBm, the house file's Kitchen")
        self.assertEqual(lines[2], f"  {OTHER!r}: 30.2°F, 70.0% humidity, battery 40%")

    def test_says_when_it_hears_none(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            govee.scan_interactively({}, seconds=0.01, scan=lambda heard, stop: None)
        self.assertIn("Heard none", out.getvalue())

    def test_raises_what_stops_the_scan(self):
        def scan(heard, stop):
            raise OSError("no Bluetooth adapter")

        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(OSError, "no Bluetooth adapter"):
            govee.scan_interactively({}, seconds=5, scan=scan)


if __name__ == "__main__":
    unittest.main()
