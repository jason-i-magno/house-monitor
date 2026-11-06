import tempfile
import unittest
from pathlib import Path

from housemon.readings import Reading
from housemon.store import Store


def reading(sensor_id: str, ts: float, temperature_c: float = 20.0, humidity: float = 35.0) -> Reading:
    return Reading(sensor_id, ts, temperature_c, humidity, battery=90, rssi=-70)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "test.sqlite3"
        self.store = Store(self.path, min_interval=60)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_add_keeps_one_reading_per_sensor_per_interval(self):
        stored = [self.store.add(reading("a", 1000 + 10 * i)) for i in range(13)]
        self.assertEqual(stored, [True, False, False, False, False, False] * 2 + [True])
        self.assertTrue(self.store.add(reading("b", 1010)))  # each sensor has its own clock

    def test_latest_survives_reopening(self):
        self.store.add(reading("a", 1000, 19.0))
        self.store.add(reading("a", 1100, 21.5))
        self.store.add(reading("b", 1050, 18.0))
        self.store.close()
        self.store = Store(self.path, min_interval=60)
        latest = self.store.latest()
        self.assertEqual(latest["a"], reading("a", 1100, 21.5))
        self.assertEqual(latest["b"].ts, 1050)
        self.assertFalse(self.store.add(reading("a", 1120)))  # thinning resumes where it left off

    def test_add_many_stores_everything(self):
        self.store.add_many(reading("a", 1000 + 10 * i) for i in range(5))
        self.assertEqual(len(self.store.history(0, 1)["a"]), 5)
        self.assertFalse(self.store.is_empty())

    def test_history_averages_each_bucket(self):
        self.store.add_many([
            reading("a", 1000, 20.0, 30.0),
            reading("a", 1100, 22.0, 40.0),
            reading("a", 1300, 25.0, 50.0),
            reading("a", 400, 10.0, 10.0),  # before `since`
            reading("b", 1250, 18.0, 45.0),
        ])
        history = self.store.history(since=900, bucket=200)
        self.assertEqual(history["a"], [[1050.0, 21.0, 35.0], [1300.0, 25.0, 50.0]])
        self.assertEqual(history["b"], [[1250.0, 18.0, 45.0]])


if __name__ == "__main__":
    unittest.main()
