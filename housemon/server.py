"""The web app: static files, a JSON API, and a Server-Sent Events stream of live readings and litter boxes' states."""

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
from .layout import WALL, house_layout
from .readings import Area, LitterBox, Reading, Sensor
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


def house_json(
    sensors: list[Sensor], areas: list[Area], details: Details | None, latest: dict[str, Reading] = {}, boxes: dict[str, LitterBox] = {}
) -> dict:
    """The house as /api/sensors sends it: its sensors, with their latest readings, areas and details, each plan with
    its share of the wall on each side (layout.py), the details with what's placed against a wall resolved, and the
    latest state of each litter box in them that names a Litter-Robot, by its name."""
    layout = house_layout(sensors, areas, details)

    def plan(plan):  # a dict, a list of them for a place in pieces, or None
        with_sides = lambda p: {**asdict(p), "sides": layout.sides(p)}
        return [with_sides(p) for p in plan] if isinstance(plan, tuple) else with_sides(plan) if plan else None

    return {
        "sensors": [
            {
                "id": sensor.id,
                "name": sensor.name,
                "floor": sensor.floor,
                "plan": plan(sensor.plan),
                "heated": sensor.heated,
                "outside": sensor.outside,
                "reading": latest[sensor.id].to_json() if sensor.id in latest else None,
            }
            for sensor in sensors
        ],
        "areas": [{**asdict(area), "plan": plan(area.plan)} for area in areas],
        "details": asdict(layout.resolve(details)) if details else None,
        "litter_boxes": {
            name: boxes[name].to_json() if name in boxes else None for name in (details.litter_robots().values() if details else ())
        },
        "wall": WALL,
    }


class HouseServer(ThreadingHTTPServer):
    """Serves the page and API for a house's sensors, areas and details. refresh, if given, is called before each
    /api/sensors to bring them up to date, as when the house file changes."""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        hub: Hub,
        store: Store,
        sensors: list[Sensor],
        areas: list[Area] = (),
        details: Details | None = None,
        refresh=None,
    ):
        super().__init__(address, Handler)
        self.hub = hub
        self.store = store
        self.sensors = sensors
        self.areas = list(areas)
        self.details = details
        self.refresh = refresh


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
        if self.server.refresh:
            self.server.refresh()
        hub = self.server.hub
        return house_json(self.server.sensors, self.server.areas, self.server.details, hub.latest(), hub.litter_boxes())

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
        simulated = query.get("simulated", ["1"])[0] != "0"  # simulated=0 leaves the simulator's readings out
        self._json({"since": since, "until": until, "bucket": bucket, "series": store.history(since, bucket, simulated)})

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
                    item = client.get(timeout=KEEPALIVE)
                    # A litter box's state is an event of its own, so a reading is the stream's plain message.
                    event = "event: litter-box\n" if isinstance(item, LitterBox) else ""
                    message = f"{event}data: {json.dumps(item.to_json())}\n\n"
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
