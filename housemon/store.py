"""SQLite storage for readings, numbered in the order they're stored, for the uplink to send them in."""

import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path

from .readings import Reading

TABLE = """
CREATE TABLE IF NOT EXISTS readings (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    sensor_id TEXT NOT NULL,
    ts REAL NOT NULL,
    temperature_c REAL NOT NULL,
    humidity REAL,
    battery INTEGER,
    rssi INTEGER,
    simulated INTEGER NOT NULL DEFAULT 0,
    UNIQUE (sensor_id, ts)
)
"""
INDEX = "CREATE INDEX IF NOT EXISTS readings_by_time ON readings (ts)"
# How far each house in the cloud service the uplink sends to has got: the seq of the last reading it took.
UPLINK = "CREATE TABLE IF NOT EXISTS uplink (url TEXT PRIMARY KEY, sent INTEGER NOT NULL)"

COLUMNS = "sensor_id, ts, temperature_c, humidity, battery, rssi, simulated"


class Store:
    """Keeps at most one reading per sensor every `min_interval` seconds.

    Sensors broadcast far more often than room temperatures change, so the
    live view gets every reading and the database a thinned-out history.

    Each reading stored gets the next seq, never reused (AUTOINCREMENT), so the
    uplink can send them in the order they were stored and remember how far it
    got as one number. Not by their time: a reading can be stored after a newer
    one, as an ecobee sensor's, polled every 3 minutes, is after the
    simulator's, and a cursor of times would pass it by. One connection writes,
    so seqs are committed in order, and a reading can't turn up later behind
    the cursor.
    """

    def __init__(self, path: str | Path, min_interval: float = 60.0):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(TABLE)
        self._db.execute(INDEX)
        self._db.execute(UPLINK)
        notnull = {name: notnull for _, name, _, notnull, *_ in self._db.execute("PRAGMA table_info(readings)")}
        if "simulated" not in notnull:
            self._mark_simulated()
        if notnull["humidity"] or "seq" not in notnull:
            self._rebuild()
        self._last_stored = dict(self._db.execute("SELECT sensor_id, MAX(ts) FROM readings GROUP BY sensor_id"))

    def _mark_simulated(self) -> None:
        """Mark which readings the simulator made, in a database from before they were marked: every one with a
        battery, as only ecobee's came without one, all or nothing."""
        self._db.execute("BEGIN")
        try:
            self._db.execute("ALTER TABLE readings ADD COLUMN simulated INTEGER NOT NULL DEFAULT 0")
            self._db.execute("UPDATE readings SET simulated = 1 WHERE battery IS NOT NULL")
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    def _rebuild(self) -> None:
        """Rebuild the table as TABLE has it, every reading kept, all or nothing, in a database from before a reading
        could have no humidity, as a sensor that reads only temperature gives, or before readings were numbered: those
        it has numbered in time order."""
        self._db.execute("BEGIN")
        try:
            self._db.execute("ALTER TABLE readings RENAME TO readings_before")
            self._db.execute(TABLE)
            self._db.execute(f"INSERT INTO readings ({COLUMNS}) SELECT {COLUMNS} FROM readings_before ORDER BY ts, sensor_id")
            self._db.execute("DROP TABLE readings_before")  # and its index, which went with it
            self._db.execute(INDEX)
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        # The old table's pages are free now, but still in the file, which would look twice its size until new
        # readings filled them.
        self._db.execute("VACUUM")

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
        rows = [(r.sensor_id, r.ts, r.temperature_c, r.humidity, r.battery, r.rssi, int(r.simulated)) for r in readings]
        with self._db:
            self._db.executemany(f"INSERT OR REPLACE INTO readings ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
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
        return {row[0]: Reading(*row[:6], simulated=bool(row[6])) for row in rows}

    def unsent(self, url: str, limit: int) -> list[tuple[int, Reading]]:
        """Up to `limit` readings the house at url hasn't taken yet, in the order they were stored, each with its seq,
        to mark it sent with (mark_sent)."""
        with self._lock:
            rows = self._db.execute(
                f"SELECT seq, {COLUMNS} FROM readings"
                " WHERE seq > (SELECT coalesce(max(sent), 0) FROM uplink WHERE url = ?) ORDER BY seq LIMIT ?",
                (url, limit),
            ).fetchall()
        return [(row[0], Reading(*row[1:7], simulated=bool(row[7]))) for row in rows]

    def mark_sent(self, url: str, seq: int) -> None:
        """Note that the house at url has taken every reading up to seq, so they aren't sent to it again."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO uplink (url, sent) VALUES (?, ?) ON CONFLICT (url) DO UPDATE SET sent = max(sent, excluded.sent)",
                (url, seq),
            )

    def history(self, since: float, bucket: float, simulated: bool = True) -> dict[str, list[list[float]]]:
        """Readings since `since`, averaged into `bucket`-second buckets, but the simulator's unless `simulated`.

        Returns {sensor_id: [[ts, temperature_c, humidity], ...]} in time order,
        where ts is the mean time of the readings in the bucket, and humidity is
        None for a bucket with none.
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT sensor_id, AVG(ts), AVG(temperature_c), AVG(humidity) FROM readings WHERE ts >= ?"
                + ("" if simulated else " AND NOT simulated")
                + " GROUP BY sensor_id, CAST(ts / ? AS INTEGER) ORDER BY sensor_id, 2",
                (since, bucket),
            ).fetchall()
        series: dict[str, list[list[float]]] = {}
        for sensor_id, ts, temperature_c, humidity in rows:
            humidity = None if humidity is None else round(humidity, 2)
            series.setdefault(sensor_id, []).append([round(ts, 1), round(temperature_c, 2), humidity])
        return series
