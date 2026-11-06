"""The web app: static files, a JSON API, and a Server-Sent Events stream of live readings."""

import json
import math
import queue
import time
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlsplit

from .details import Details
from .hub import Hub
from .readings import Area, Sensor
from .store import Store

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/model.js": ("model.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
KEEPALIVE = 15.0  # seconds between comments on an idle stream, so nothing in between closes it
MAX_HISTORY_HOURS = 24 * 366
HISTORY_POINTS = 500  # per sensor, so a chart stays light whatever the range


class HouseServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        hub: Hub,
        store: Store,
        sensors: list[Sensor],
        areas: list[Area] = (),
        details: Details | None = None,
    ):
        super().__init__(address, Handler)
        self.hub = hub
        self.store = store
        self.sensors = sensors
        self.areas = list(areas)
        self.details = details


class Handler(BaseHTTPRequestHandler):
    server: HouseServer

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if url.path in STATIC:
            self._static(*STATIC[url.path])
        elif url.path == "/api/sensors":
            self._json(self._sensors())
        elif url.path == "/api/history":
            self._history(parse_qs(url.query))
        elif url.path == "/api/stream":
            self._stream()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _static(self, name: str, content_type: str) -> None:
        # Read on every request, so edits to the page show up on reload.
        self._send(resources.files(__package__).joinpath("web", name).read_bytes(), content_type)

    def _sensors(self) -> dict:
        latest = self.server.hub.latest()
        return {
            "sensors": [
                {
                    "id": sensor.id,
                    "name": sensor.name,
                    "floor": sensor.floor,
                    "plan": asdict(sensor)["plan"],  # a dict, a list of them for a room in pieces, or None
                    "heated": sensor.heated,
                    "outside": sensor.outside,
                    "reading": latest[sensor.id].to_json() if sensor.id in latest else None,
                }
                for sensor in self.server.sensors
            ],
            "areas": [asdict(area) for area in self.server.areas],
            "details": asdict(self.server.details) if self.server.details else None,
        }

    def _history(self, query: dict[str, list[str]]) -> None:
        try:
            hours = float(query.get("hours", ["24"])[0])
        except ValueError:
            hours = math.nan
        if not 0 < hours <= MAX_HISTORY_HOURS:
            self.send_error(HTTPStatus.BAD_REQUEST, f"hours must be a number from 0 to {MAX_HISTORY_HOURS}")
            return
        until = time.time()
        since = until - hours * 3600
        store = self.server.store
        bucket = max(store.min_interval, math.ceil(hours * 3600 / HISTORY_POINTS / 60) * 60)
        self._json({"since": since, "until": until, "bucket": bucket, "series": store.history(since, bucket)})

    def _stream(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        client = self.server.hub.subscribe()
        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            while True:
                try:
                    message = f"data: {json.dumps(client.get(timeout=KEEPALIVE).to_json())}\n\n"
                except queue.Empty:
                    message = ": keepalive\n\n"
                self.wfile.write(message.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the browser went away
        finally:
            self.server.hub.unsubscribe(client)

    def _json(self, value: object) -> None:
        self._send(json.dumps(value).encode(), "application/json")

    def _send(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        pass  # a line per request is noise; errors still go to stderr through log_error
