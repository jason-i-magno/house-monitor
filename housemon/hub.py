"""Fans each new reading out to the store and to every live client."""

import queue
import threading

from .readings import Reading
from .store import Store


class Hub:
    def __init__(self, store: Store):
        self._store = store
        self._lock = threading.Lock()
        self._latest = store.latest()
        self._clients: set[queue.Queue] = set()

    def publish(self, reading: Reading) -> None:
        self._store.add(reading)
        with self._lock:
            self._latest[reading.sensor_id] = reading
            clients = list(self._clients)
        for client in clients:
            try:
                client.put_nowait(reading)
            except queue.Full:
                pass  # a stalled client misses readings rather than holding up the rest

    def latest(self) -> dict[str, Reading]:
        """The newest reading from each sensor, stored or not."""
        with self._lock:
            return dict(self._latest)

    def subscribe(self, maxsize: int = 1000) -> queue.Queue:
        client: queue.Queue = queue.Queue(maxsize)
        with self._lock:
            self._clients.add(client)
        return client

    def unsubscribe(self, client: queue.Queue) -> None:
        with self._lock:
            self._clients.discard(client)
