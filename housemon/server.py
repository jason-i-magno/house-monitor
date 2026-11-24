"""The web app: static files, a JSON API, and a Server-Sent Events stream of live readings, litter boxes' states
and vacuums; and the home manual's page and API (manual.py)."""

import base64
import ipaddress
import json
import math
import queue
import socket
import time
from dataclasses import asdict
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlsplit

from .details import Details
from .garden import MAX_BYTES, Garden, Plans
from .hub import Hub
from .layout import WALL, house_layout
from .manual import KINDS, STARTER_TASKS, Manual, ManualError, check_file
from .readings import Area, LitterBox, Reading, Sensor, Vacuum
from .store import Store

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/model.js": ("model.js", "text/javascript; charset=utf-8"),
    "/garden.js": ("garden.js", "text/javascript; charset=utf-8"),
    "/plants.json": ("plants.json", "application/json"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/manual.html": ("manual.html", "text/html; charset=utf-8"),
    "/manual.js": ("manual.js", "text/javascript; charset=utf-8"),
}
KEEPALIVE = 15.0  # seconds between comments on an idle stream, so nothing in between closes it
MAX_HISTORY_HOURS = 24 * 366
HISTORY_POINTS = 500  # per sensor, so a chart stays light whatever the range
MAX_MANUAL_BODY = 45 * 1024 * 1024  # a file the manual takes, as base64, and the JSON around it


def known_host(header: str, allowed=()) -> bool:
    """Whether a request's Host is this computer's: an IP address, localhost, its own name (or that .local), or one
    of `allowed` (--allow-host). Anything else could be a site that has pointed its name at this computer to reach
    the manual from a browser here (DNS rebinding), so the manual's API refuses it."""
    host = header.strip().lower()
    if host.startswith("[") and "]" in host:
        host = host[1 : host.index("]")]
    elif host.count(":") == 1:
        host = host.split(":")[0]
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    name = socket.gethostname().lower()
    return host == "localhost" or host.endswith(".localhost") or host in (name, f"{name}.local") or host in allowed


def house_json(
    sensors: list[Sensor],
    areas: list[Area],
    details: Details | None,
    latest: dict[str, Reading] = {},
    boxes: dict[str, LitterBox] = {},
    garden: Garden | None = None,
) -> dict:
    """The house as /api/sensors sends it: its sensors, with their latest readings, areas and details, each plan with
    its share of the wall on each side (layout.py), the details with what's placed against a wall resolved, the
    latest state of each litter box in them that names a Litter-Robot, by its name, and where its garden is, if the
    house file says (garden.py)."""
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
                "beside": sensor.beside,
                "warn_below_c": sensor.warn_below_c,
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
        "garden": asdict(garden) if garden else None,
    }


class HouseServer(ThreadingHTTPServer):
    """Serves the page and API for a house's sensors, areas, details and garden, and the garden's plans, if it's given
    somewhere to keep them. refresh, if given, is called before each /api/sensors to bring them up to date, as when
    the house file changes. With a `manual` (manual.Manual), it serves the home manual too, and with an `assistant`
    (manual.Assistant), Claude for it, or `assistant_problem` says why not; `allowed_hosts` are names besides its own
    the manual answers on (known_host)."""

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
        garden: Garden | None = None,
        plans: Plans | None = None,
        manual: Manual | None = None,
        assistant=None,
        assistant_problem: str | None = None,
        allowed_hosts=(),
    ):
        super().__init__(address, Handler)
        self.hub = hub
        self.store = store
        self.sensors = sensors
        self.areas = list(areas)
        self.details = details
        self.refresh = refresh
        self.garden = garden
        self.plans = plans
        self.manual = manual
        self.assistant = assistant
        self.assistant_problem = assistant_problem
        self.allowed_hosts = {h.lower() for h in allowed_hosts}


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
        elif url.path == "/api/garden" and self.server.plans:
            self._garden_plans()
        elif url.path == "/api/manual" and self.server.manual:
            self._manual_contents()
        elif url.path == "/api/manual/file" and self.server.manual:
            self._manual_file(parse_qs(url.query))
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:
        if urlsplit(self.path).path == "/api/garden" and self.server.plans:
            self._save_bed()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        url = urlsplit(self.path)
        if not url.path.startswith("/api/manual/") or not self.server.manual:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            self._check_manual_request(write=True)
            result = self._manual_action(url.path.removeprefix("/api/manual/"), self._read_json())
        except ManualError as e:
            self._json({"error": str(e)}, e.status)
            return
        self._json(result)

    # The home manual

    def _refused(self, write: bool) -> tuple[int, str] | None:
        """Why a request to an API that keeps the owner's things (the home manual, the garden's plans) is refused, if it
        is, as a status and words: one from another site's page in a browser here, whose Host isn't this computer's
        (known_host), as when a site points its name here (DNS rebinding), or whose Origin isn't this server; or, to
        change something, one that isn't JSON, which a page elsewhere can't send without asking first (and this server
        never answers that ask)."""
        host = self.headers.get("Host", "")
        if not known_host(host, self.server.allowed_hosts):
            return HTTPStatus.FORBIDDEN, "This answers only at this computer's own names and addresses (see --allow-host)."
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc.lower() != host.lower():
            return HTTPStatus.FORBIDDEN, "Requests from other sites are refused."
        if write and self.headers.get_content_type() != "application/json":
            return HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Send JSON."
        return None

    def _check_manual_request(self, write: bool) -> None:
        """Refuses a request to the manual's API as _refused says, in the manual's way: a ManualError, answered as JSON."""
        refused = self._refused(write)
        if refused:
            raise ManualError(refused[1], refused[0])

    def _refuse_garden(self, write: bool) -> bool:
        """Refuses a request for the garden's plans as _refused says, with an error page as the garden's API answers;
        whether it did."""
        refused = self._refused(write)
        if refused:
            self.send_error(refused[0], explain=refused[1])
        return refused is not None

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ManualError("Bad Content-Length.") from None
        if length > MAX_MANUAL_BODY:
            raise ManualError("That's too large.", 413)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            raise ManualError("The body isn't JSON.") from None
        if not isinstance(body, dict):
            raise ManualError("Send a JSON object.")
        return body

    def _manual_contents(self) -> None:
        try:
            self._check_manual_request(write=False)
        except ManualError as e:
            self._json({"error": str(e)}, e.status)
            return
        if self.server.refresh:
            self.server.refresh()
        rooms = [s.name for s in self.server.sensors if not s.outside and not s.beside] + [a.name for a in self.server.areas]
        self._json(
            {
                **self.server.manual.contents(date.today()),
                "kinds": KINDS,
                "starter_tasks": {kind: [{"title": t, "every_months": m} for t, m in tasks] for kind, tasks in STARTER_TASKS.items()},
                "rooms": rooms,
                "claude": {"available": self.server.assistant is not None, "problem": self.server.assistant_problem},
            }
        )

    def _manual_file(self, query: dict[str, list[str]]) -> None:
        try:
            self._check_manual_request(write=False)
            file = self.server.manual.file(int(query.get("id", ["0"])[0]))
        except ManualError as e:
            self._json({"error": str(e)}, e.status)
            return
        except ValueError:
            self.send_error(HTTPStatus.BAD_REQUEST, "id must be a number")
            return
        if not file:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._send(file["data"], file["mime"])

    def _manual_action(self, action: str, body: dict) -> dict:
        """Does one of the manual's changes, or asks Claude; gives what to answer with."""
        manual, assistant = self.server.manual, self.server.assistant

        def number(key: str = "id") -> int:
            value = body.get(key)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ManualError(f"{key} must be a number.")
            return value

        if action == "equipment":  # added, or changed when it has an id
            id = number() if body.get("id") is not None else None
            return {"id": manual.save_equipment(body.get("fields") or {}, id, starter=bool(body.get("starter")))}
        if action == "equipment-delete":
            manual.delete_equipment(number())
            return {"ok": True}
        def data() -> bytes:
            try:
                raw = base64.b64decode(body.get("data") or "", validate=True)
            except (ValueError, TypeError):
                raise ManualError("data isn't base64.") from None
            if not raw:
                raise ManualError("data (the file as base64) is required.")
            return raw

        if action == "file":
            return {"id": manual.add_file(number("equipment_id"), body.get("kind"), body.get("name") or "", body.get("mime"), data())}
        if action == "file-delete":
            manual.delete_file(number())
            return {"ok": True}
        if action == "task":  # added, or changed when it has an id
            id = number() if body.get("id") is not None else None
            return {"id": manual.save_task(body.get("fields") or {}, id)}
        if action == "task-done":
            manual.mark_done(number(), body.get("day") or date.today().isoformat())
            return {"ok": True}
        if action == "task-delete":
            manual.delete_task(number())
            return {"ok": True}
        if action not in ("read-label", "suggest-tasks", "ask"):
            raise ManualError(f"Unknown action {action!r}.", 404)
        if assistant is None:
            raise ManualError(self.server.assistant_problem or "Claude isn't set up.")
        if action == "read-label":  # a stored photo, or one not saved yet, as when adding equipment from its label
            if body.get("file_id") is None:
                photo = {"name": body.get("name") or "label", "mime": body.get("mime"), "data": data()}
                check_file(photo["mime"], photo["data"])
                return assistant.read_label(photo)
            file = manual.file(number("file_id"))
            if not file:
                raise ManualError("That file isn't in the manual.", 404)
            return assistant.read_label(file)
        if action == "suggest-tasks":
            id = number()
            item = next((e for e in manual.contents(date.today())["equipment"] if e["id"] == id), None)
            if not item:
                raise ManualError("That equipment isn't in the manual.", 404)
            return {"tasks": assistant.suggest_tasks(item, manual.files_of(id, ("manual",)))}
        question = body.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ManualError("Ask a question.")
        documents = manual.files_of(number("equipment_id"), ("label", "manual")) if body.get("equipment_id") is not None else []
        return {"answer": assistant.ask(question.strip()[:2000], manual.inventory(date.today()), documents)}

    def _garden_plans(self) -> None:
        if self._refuse_garden(write=False):
            return
        try:
            plans = self.server.plans.load()
        except (OSError, ValueError) as e:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, explain=f"Couldn't read the garden's plans: {e}")
            return
        self._json(plans)

    def _save_bed(self) -> None:
        """One bed's plantings, as {"bed": name, "plants": [...]}, saved in place of what it had (garden.Plans)."""
        if self._refuse_garden(write=True):
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.send_error(HTTPStatus.LENGTH_REQUIRED)
            return
        if not 0 < length <= MAX_BYTES:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"A bed's plan is at most {MAX_BYTES} bytes")
            return
        self.connection.settimeout(10)  # a client that stops partway doesn't hold the thread
        try:
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict) or set(body) != {"bed", "plants"}:
                raise ValueError('A bed\'s plan is {"bed": its name, "plants": [...]}')
            plans = self.server.plans.save_bed(body["bed"], body["plants"])
        except (socket.timeout, ConnectionError):
            return
        except ValueError as e:  # JSONDecodeError is one too
            self.send_error(HTTPStatus.BAD_REQUEST, explain=str(e))  # in the body: it can quote what was sent
            return
        except OSError as e:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, explain=f"Couldn't save the garden's plans: {e}")
            return
        self._json(plans)

    def _static(self, name: str, content_type: str) -> None:
        # Read on every request, so edits to the page show up on reload.
        self._send(resources.files(__package__).joinpath("web", name).read_bytes(), content_type)

    def _sensors(self) -> dict:
        if self.server.refresh:
            self.server.refresh()
        hub = self.server.hub
        house = house_json(self.server.sensors, self.server.areas, self.server.details, hub.latest(), hub.litter_boxes(), self.server.garden)
        return {**house, "vacuums": [vacuum.to_json() for vacuum in hub.vacuums()]}

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
                    # A reading is the stream's own kind of message, and a litter box's state and a vacuum each one of its own.
                    kind = "event: litter-box\n" if isinstance(item, LitterBox) else "event: vacuum\n" if isinstance(item, Vacuum) else ""
                    message = f"{kind}data: {json.dumps(item.to_json())}\n\n"
                except queue.Empty:
                    message = ": keepalive\n\n"
                self.wfile.write(message.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the browser went away
        finally:
            self.server.hub.unsubscribe(client)

    def _json(self, value: object, status: int = HTTPStatus.OK) -> None:
        self._send(json.dumps(value).encode(), "application/json", status)

    def _send(self, body: bytes, content_type: str, status: int = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        pass  # a line per request is noise; errors still go to stderr through log_error
