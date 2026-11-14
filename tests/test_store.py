import sqlite3
import tempfile
import unittest
from pathlib import Path

from housemon.readings import Reading
from housemon.store import Store


def reading(sensor_id: str, ts: float, temperature_c: float = 20.0, humidity: float | None = 35.0) -> Reading:
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

    def test_a_reading_can_have_no_humidity(self):
        """A sensor that reads only temperature: its humidity is None, stored and given back as such."""
        self.store.add_many([reading("a", 1000, 20.0, None), reading("a", 1100, 22.0, None), reading("b", 1000, 19.0, 40.0)])
        self.assertIsNone(self.store.latest()["a"].humidity)
        self.assertEqual(self.store.history(since=900, bucket=200)["a"], [[1050.0, 21.0, None]])

    def test_a_database_from_before_no_humidity_was_allowed_keeps_its_readings(self):
        self.store.close()
        path = Path(self.dir.name) / "old.sqlite3"
        old = sqlite3.connect(path)
        old.executescript("""
            CREATE TABLE readings (sensor_id TEXT NOT NULL, ts REAL NOT NULL, temperature_c REAL NOT NULL,
                humidity REAL NOT NULL, battery INTEGER, rssi INTEGER, PRIMARY KEY (sensor_id, ts)) WITHOUT ROWID;
            CREATE INDEX readings_by_time ON readings (ts);
            INSERT INTO readings VALUES ('a', 1000, 20.0, 35.0, 90, -70), ('a', 1100, 21.5, 36.0, 90, -70),
                ('b', 1000, 19.0, 40.0, NULL, NULL);
        """)
        old.close()
        self.store = Store(path, min_interval=60)
        # Marked as the simulator's, having a battery, as only ecobee's readings came without one.
        self.assertEqual(self.store.latest()["a"], Reading("a", 1100, 21.5, 36.0, battery=90, rssi=-70, simulated=True))
        self.assertFalse(self.store.latest()["b"].simulated)
        self.assertFalse(self.store.add(reading("a", 1120)))  # its clock resumes too
        self.assertTrue(self.store.add(reading("a", 1200, 22.0, None)))
        self.store.close()
        self.store = Store(path, min_interval=60)  # and opens again as it is
        self.assertEqual(len(self.store.history(since=0, bucket=1)["a"]), 3)

    def test_unsent_readings_come_in_the_order_stored_until_marked_sent(self):
        url, other = "https://cloud.example/houses/home", "https://elsewhere.example/houses/home"
        self.store.add_many([reading("a", 2000), reading("b", 2000)])
        self.store.add(reading("c", 1000))  # older, but stored later, as an ecobee reading can be: it still comes
        unsent = self.store.unsent(url, limit=10)
        self.assertEqual([(r.sensor_id, r.ts) for _, r in unsent], [("a", 2000), ("b", 2000), ("c", 1000)])
        self.assertEqual(unsent[2][1], reading("c", 1000))
        self.assertEqual(self.store.unsent(url, limit=2), unsent[:2])

        self.store.mark_sent(url, unsent[1][0])
        self.assertEqual(self.store.unsent(url, limit=10), unsent[2:])
        self.store.mark_sent(url, unsent[0][0])  # an older mark doesn't take it back
        self.assertEqual(self.store.unsent(url, limit=10), unsent[2:])
        self.assertEqual(self.store.unsent(other, limit=10), unsent)  # each house in the cloud has its own place

        self.store.close()
        self.store = Store(self.path, min_interval=60)  # and it's kept
        self.assertEqual(self.store.unsent(url, limit=10), unsent[2:])
        self.store.add(reading("a", 2100))
        self.assertEqual([r.ts for _, r in self.store.unsent(url, limit=10)], [1000, 2100])

    def test_a_reading_stored_again_is_sent_again(self):
        """INSERT OR REPLACE gives it a new seq: the cloud service keeps the first it took, so sending it is harmless."""
        url = "https://cloud.example/houses/home"
        self.store.add_many([reading("a", 1000)])
        self.store.mark_sent(url, self.store.unsent(url, limit=10)[-1][0])
        self.store.add_many([reading("a", 1000, 21.0)])
        self.assertEqual([r.temperature_c for _, r in self.store.unsent(url, limit=10)], [21.0])

    def test_a_database_from_before_readings_were_numbered_numbers_them_in_time_order(self):
        self.store.close()
        path = Path(self.dir.name) / "unnumbered.sqlite3"
        old = sqlite3.connect(path)
        old.executescript("""
            CREATE TABLE readings (sensor_id TEXT NOT NULL, ts REAL NOT NULL, temperature_c REAL NOT NULL, humidity REAL,
                battery INTEGER, rssi INTEGER, simulated INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (sensor_id, ts)) WITHOUT ROWID;
            CREATE INDEX readings_by_time ON readings (ts);
            INSERT INTO readings VALUES ('b', 1100, 21.5, NULL, NULL, NULL, 0), ('a', 1000, 20.0, 35.0, 90, -70, 1),
                ('a', 1100, 21.0, 36.0, 90, -70, 1);
        """)
        old.close()
        self.store = Store(path, min_interval=60)
        unsent = self.store.unsent("https://cloud.example/houses/home", limit=10)
        self.assertEqual([(r.sensor_id, r.ts) for _, r in unsent], [("a", 1000), ("a", 1100), ("b", 1100)])
        self.assertEqual(unsent[2][1], Reading("b", 1100, 21.5, None))
        self.assertTrue(unsent[0][1].simulated)
        self.store.add(reading("b", 1200))  # numbered after them
        self.assertEqual(self.store.unsent("https://cloud.example/houses/home", limit=10)[-1][1].ts, 1200)
        check = sqlite3.connect(path)
        self.assertIn("readings_by_time", {row[1] for row in check.execute("PRAGMA index_list(readings)")})
        check.close()

    def test_history_can_leave_the_simulators_readings_out(self):
        self.store.add_many([
            Reading("a", 1000, 20.0, 30.0, simulated=True),
            Reading("a", 1100, 22.0, 40.0),  # read from a sensor, as the room's since
            Reading("b", 1050, 18.0, 45.0, simulated=True),
        ])
        self.assertTrue(self.store.latest()["b"].simulated)
        self.assertEqual(self.store.history(since=900, bucket=1000), {"a": [[1050.0, 21.0, 35.0]], "b": [[1050.0, 18.0, 45.0]]})
        self.assertEqual(self.store.history(since=900, bucket=1000, simulated=False), {"a": [[1100.0, 22.0, 40.0]]})


if __name__ == "__main__":
    unittest.main()
