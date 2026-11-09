"""SQLite storage for readings."""

import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path

from .readings import Reading

TABLE = """
CREATE TABLE IF NOT EXISTS readings (
    sensor_id TEXT NOT NULL,
    ts REAL NOT NULL,
    temperature_c REAL NOT NULL,
    humidity REAL,
    battery INTEGER,
    rssi INTEGER,
    PRIMARY KEY (sensor_id, ts)
) WITHOUT ROWID
"""
INDEX = "CREATE INDEX IF NOT EXISTS readings_by_time ON readings (ts)"

COLUMNS = "sensor_id, ts, temperature_c, humidity, battery, rssi"


class Store:
    """Keeps at most one reading per sensor every `min_interval` seconds.

    Sensors broadcast far more often than room temperatures change, so the
    live view gets every reading and the database a thinned-out history.
    """

    def __init__(self, path: str | Path, min_interval: float = 60.0):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(TABLE)
        self._db.execute(INDEX)
        if any(name == "humidity" and notnull for _, name, _, notnull, *_ in self._db.execute("PRAGMA table_info(readings)")):
            self._allow_no_humidity()
        self._last_stored = dict(self._db.execute("SELECT sensor_id, MAX(ts) FROM readings GROUP BY sensor_id"))

    def _allow_no_humidity(self) -> None:
        """Let a reading have no humidity, as a sensor that reads only temperature gives, in a database from before
        that was allowed: its table rebuilt with the column nullable, every reading kept, all or nothing."""
        self._db.execute("BEGIN")
        try:
            self._db.execute("ALTER TABLE readings RENAME TO readings_before")
            self._db.execute(TABLE)
            self._db.execute(f"INSERT INTO readings ({COLUMNS}) SELECT {COLUMNS} FROM readings_before")
            self._db.execute("DROP TABLE readings_before")  # and its index, which went with it
            self._db.execute(INDEX)
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def add(self, reading: Reading) -> bool:
        """Store the reading unless this sensor was stored too recently."""
        with self._lock:
            last = self._last_stored.get(reading.sensor_id)
            if last is not None and reading.ts - last < self.min_interval:
                return False
            self._insert([reading])
            return True

    def add_many(self, readings: Iterable[Reading]) -> None:
        """Store every reading, without thinning (for imports and backfills)."""
        with self._lock:
            self._insert(readings)

    def _insert(self, readings: Iterable[Reading]) -> None:
        rows = [(r.sensor_id, r.ts, r.temperature_c, r.humidity, r.battery, r.rssi) for r in readings]
        with self._db:
            self._db.executemany(f"INSERT OR REPLACE INTO readings ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)", rows)
        for sensor_id, ts, *_ in rows:
            self._last_stored[sensor_id] = max(ts, self._last_stored.get(sensor_id, ts))

    def is_empty(self) -> bool:
        with self._lock:
            return not self._last_stored

    def latest(self) -> dict[str, Reading]:
        """The newest stored reading from each sensor."""
        with self._lock:
            rows = self._db.execute(
                f"SELECT {COLUMNS} FROM readings"
                " JOIN (SELECT sensor_id, MAX(ts) AS ts FROM readings GROUP BY sensor_id) USING (sensor_id, ts)"
            ).fetchall()
        return {row[0]: Reading(*row) for row in rows}

    def history(self, since: float, bucket: float) -> dict[str, list[list[float]]]:
        """Readings since `since`, averaged into `bucket`-second buckets.

        Returns {sensor_id: [[ts, temperature_c, humidity], ...]} in time order,
        where ts is the mean time of the readings in the bucket, and humidity is
        None for a bucket with none.
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT sensor_id, AVG(ts), AVG(temperature_c), AVG(humidity) FROM readings WHERE ts >= ?"
                " GROUP BY sensor_id, CAST(ts / ? AS INTEGER) ORDER BY sensor_id, 2",
                (since, bucket),
            ).fetchall()
        series: dict[str, list[list[float]]] = {}
        for sensor_id, ts, temperature_c, humidity in rows:
            humidity = None if humidity is None else round(humidity, 2)
            series.setdefault(sensor_id, []).append([round(ts, 1), round(temperature_c, 2), humidity])
        return series
