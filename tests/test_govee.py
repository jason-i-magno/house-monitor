import contextlib
import io
import threading
import time
import unittest

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
        source.started = 100.0
        source.due(200)
        self.assertEqual(self.warnings, [])

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
