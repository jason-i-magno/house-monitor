import statistics
import threading
import time
import unittest

from housemon import example_house
from housemon import house as houses
from housemon.readings import Plan
from housemon.simulator import SimulatedSource, reading_at

HOUSE = example_house.HOUSE
# Every house file's rooms, for the checks that hold of any: the example's, and your own if it's there.
EVERY_ROOM = list(HOUSE) + (list(houses.load(houses.PRIVATE).HOUSE) if houses.PRIVATE.exists() else [])


def local_time(hour: float, days_ago: int = 1) -> float:
    """A Unix time at `hour` o'clock local time, a day or more in the past."""
    t = time.localtime(time.time() - days_ago * 86400)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)) + hour * 3600


def fahrenheit(celsius: float) -> float:
    return celsius * 9 / 5 + 32


ROOMS = {room.sensor.name: room for room in HOUSE}


def day_mean_f(name: str) -> float:
    return statistics.mean(fahrenheit(reading_at(ROOMS[name], local_time(h / 4)).temperature_c) for h in range(24 * 4))


class ReadingAtTest(unittest.TestCase):
    def test_is_a_pure_function_of_room_and_time(self):
        t = local_time(14.5)
        for room in EVERY_ROOM:
            self.assertEqual(reading_at(room, t), reading_at(room, t))

    def test_values_stay_plausible_all_day(self):
        for room in EVERY_ROOM:
            # Unheated spaces, like a garage, stay above freezing; outside needn't.
            coldest = 55 if room.sensor.heated else -20 if room.sensor.outside else 32
            for minute in range(0, 24 * 60, 5):
                reading = reading_at(room, local_time(minute / 60))
                self.assertTrue(coldest <= fahrenheit(reading.temperature_c) <= 80, (room.sensor.name, minute, reading))
                wettest = 80 if room.showers or room.sensor.outside else 60
                self.assertTrue(10 <= reading.humidity <= wettest, (room.sensor.name, minute, reading))

    def test_reports_at_sensor_resolution(self):
        reading = reading_at(HOUSE[0], local_time(9.25))
        self.assertEqual(reading.temperature_c, round(reading.temperature_c, 1))
        self.assertEqual(reading.humidity, round(reading.humidity, 1))

    def test_basement_runs_colder_than_upstairs(self):
        self.assertLess(day_mean_f("Family room"), day_mean_f("Living room"))
        self.assertLess(day_mean_f("Living room"), day_mean_f("Upstairs bedroom"))

    def test_the_garage_runs_between_the_rooms_and_outside(self):
        unheated = [name for name, room in ROOMS.items() if not room.sensor.heated and not room.sensor.outside]
        self.assertEqual(unheated, ["Garage"])
        coldest_heated = min(day_mean_f(name) for name, room in ROOMS.items() if room.sensor.heated)
        self.assertLess(day_mean_f("Garage"), coldest_heated)
        self.assertLess(day_mean_f("Outside"), day_mean_f("Garage"))

    def test_space_heater_warms_the_office_during_the_day(self):
        office, family = ROOMS["Office"], ROOMS["Family room"]
        afternoon, night = local_time(14), local_time(2)
        self.assertGreater(reading_at(office, afternoon).temperature_c, reading_at(family, afternoon).temperature_c)
        self.assertLess(reading_at(office, night).temperature_c, reading_at(family, night).temperature_c)

    def test_showers_steam_up_the_bathroom_for_a_while(self):
        room = ROOMS["Bathroom"]
        during, later = (reading_at(room, local_time(room.showers[0] + h)).humidity for h in (0, 1.5))
        self.assertGreater(during - later, 20)

    def test_a_ceiling_drops_only_to_a_wall(self):
        with self.assertRaises(ValueError):
            Plan(0, 0, 10, 10, drop=("rear", 2))


class SimulatedSourceTest(unittest.TestCase):
    def test_backfill_covers_each_room_at_each_step(self):
        source = SimulatedSource(HOUSE)
        since = 1_800_000_000
        readings = list(source.backfill(since, since + 600, 60))
        self.assertEqual(len(readings), 10 * len(HOUSE))
        self.assertEqual({r.ts for r in readings}, {since + 60 * i for i in range(10)})

    def test_run_publishes_every_room_in_turn_until_stopped(self):
        source = SimulatedSource(HOUSE, interval=0.06)
        readings, stop = [], threading.Event()
        worker = threading.Thread(target=source.run, args=(readings.append, stop))
        worker.start()
        time.sleep(0.2)
        stop.set()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        ids = [r.sensor_id for r in readings]
        self.assertGreaterEqual(len(ids), 2 * len(HOUSE))
        self.assertEqual(ids[: len(HOUSE)], [room.sensor.id for room in HOUSE])

    def test_run_takes_up_rooms_swapped_in(self):
        source = SimulatedSource(HOUSE, interval=0.06)
        readings, stop = [], threading.Event()
        worker = threading.Thread(target=source.run, args=(readings.append, stop))
        worker.start()
        time.sleep(0.03)
        source.rooms = HOUSE[:1]  # as from a changed house file
        time.sleep(0.25)
        stop.set()
        worker.join(timeout=1)
        self.assertEqual({r.sensor_id for r in readings[-3:]}, {HOUSE[0].sensor.id})


if __name__ == "__main__":
    unittest.main()
