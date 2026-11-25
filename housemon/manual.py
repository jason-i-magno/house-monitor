"""The home manual: the house's equipment (the furnace, the water heater, appliances), photos of their labels and
their manuals, and a maintenance schedule, in a SQLite database of its own (data/manual.sqlite3). With the optional
anthropic package and an API key, Claude reads a label's photo, suggests maintenance and answers questions from the
manuals; without it, everything else works."""

import base64
import calendar
import json
import os
import re
import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path

# What a piece of equipment can be, by id, with the name the page shows
KINDS = {
    "furnace": "Furnace",
    "air conditioner": "Air conditioner",
    "heat pump": "Heat pump",
    "water heater": "Water heater",
    "tankless water heater": "Tankless water heater",
    "water softener": "Water softener",
    "humidifier": "Humidifier",
    "dehumidifier": "Dehumidifier",
    "sump pump": "Sump pump",
    "refrigerator": "Refrigerator",
    "dishwasher": "Dishwasher",
    "washer": "Washer",
    "dryer": "Dryer",
    "range": "Range or oven",
    "range hood": "Range hood",
    "microwave": "Microwave",
    "garbage disposal": "Garbage disposal",
    "garage door opener": "Garage door opener",
    "smoke alarm": "Smoke or CO alarms",
    "thermostat": "Thermostat",
    "sprinklers": "Sprinkler system",
    "roof": "Roof and gutters",
    "other": "Other",
}

# The usual maintenance for each kind, every so many months: a start, which a manual or Claude can refine
STARTER_TASKS = {
    "furnace": [("Replace the air filter", 3), ("Have it serviced", 12)],
    "air conditioner": [("Have it serviced before summer", 12), ("Clear around the outdoor unit and rinse its coil", 12)],
    "heat pump": [("Replace the air filter", 3), ("Have it serviced", 12)],
    "water heater": [("Flush the tank", 12), ("Test the pressure relief valve", 12), ("Check the anode rod", 36)],
    "tankless water heater": [("Descale it", 12), ("Clean the inlet water filter", 6)],
    "water softener": [("Check the salt", 1), ("Clean the brine tank", 12)],
    "humidifier": [("Replace the water panel", 12)],
    "dehumidifier": [("Clean the filter", 1)],
    "sump pump": [("Test it with a bucket of water in the pit", 3)],
    "refrigerator": [("Clean the condenser coils", 12), ("Replace the water filter", 6)],
    "dishwasher": [("Clean the filter", 1)],
    "washer": [("Run a cleaning cycle", 1)],
    "dryer": [("Clean the vent duct", 12)],
    "range hood": [("Clean the grease filters", 3)],
    "garage door opener": [("Lubricate it and test the auto-reverse", 6)],
    "smoke alarm": [("Test the alarms", 1), ("Replace the batteries", 12), ("Replace the alarms", 120)],
    "sprinklers": [("Start it up and check the heads", 12), ("Winterize it", 12)],
    "roof": [("Clean the gutters", 6), ("Look over the roof", 12)],
}

FILE_KINDS = ("label", "manual", "photo", "receipt")
FILE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif", "application/pdf")
MAX_FILE_BYTES = 32 * 1024 * 1024  # a long manual's PDF; Claude reads PDFs up to this size
MAX_SPECS = 40
TEXT_LIMIT = 2000  # characters in a note; names and the like are shorter

_SIGNATURES = {
    "image/jpeg": lambda b: b[:3] == b"\xff\xd8\xff",
    "image/png": lambda b: b[:8] == b"\x89PNG\r\n\x1a\n",
    "image/gif": lambda b: b[:4] == b"GIF8",
    "image/webp": lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP",
    "application/pdf": lambda b: b[:5] == b"%PDF-",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS equipment (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    room TEXT NOT NULL DEFAULT '',
    brand TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    serial TEXT NOT NULL DEFAULT '',
    installed TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    specs TEXT NOT NULL DEFAULT '[]',
    created TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    data BLOB NOT NULL,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    equipment_id INTEGER REFERENCES equipment(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    every_months INTEGER NOT NULL,
    last_done TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL
);
"""

SOON_DAYS = 30  # a task due within this many days is coming up


class ManualError(Exception):
    """A request the manual can't do, with what to tell the page and the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value, limit: int = 200) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _day(value) -> str:
    """A date as YYYY-MM-DD, or '' for none; anything else is an error."""
    if value in (None, ""):
        return ""
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise ManualError(f"{value!r} isn't a date (YYYY-MM-DD).") from None


def add_months(day: date, months: int) -> date:
    """The same day `months` later, or the month's last day when it's shorter."""
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def schedule(task: dict, today: date) -> dict:
    """When a task is next due, and how it stands on `today`: 'overdue', 'soon' (within SOON_DAYS), 'later', or
    'never' for one not done yet, which is due now but isn't counted overdue, since the manual doesn't know when it
    was last done."""
    if not task["last_done"]:
        return {"due": today.isoformat(), "status": "never"}
    due = add_months(date.fromisoformat(task["last_done"]), task["every_months"])
    status = "overdue" if due < today else "soon" if (due - today).days <= SOON_DAYS else "later"
    return {"due": due.isoformat(), "status": status}


def check_file(mime: str, data: bytes) -> None:
    """Refuses a file the manual doesn't take: not a photo or PDF, too large, or not what its type says."""
    if mime not in FILE_TYPES:
        raise ManualError("Files can be JPEG, PNG, WebP or GIF photos, or PDFs.", 415)
    if len(data) > MAX_FILE_BYTES:
        raise ManualError(f"Files can be up to {MAX_FILE_BYTES // (1024 * 1024)} MB.", 413)
    if not _SIGNATURES[mime](data):
        raise ManualError(f"That file isn't a {mime.split('/')[1].upper()}.", 415)


def _owner_only(path: Path) -> None:
    """Makes the manual's database file readable only by its owner, as the sign-ins are (it holds serial numbers,
    manuals and receipts): created that way, or one made before tightened, with SQLite's files beside it. SQLite gives
    the files it makes later (-wal, -shm) the database's own permissions."""
    if not path.exists():
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
    for file in (path, path.with_name(f"{path.name}-wal"), path.with_name(f"{path.name}-shm")):
        if file.exists() and file.stat().st_mode & 0o077:
            file.chmod(0o600)


class Manual:
    """The home manual's database. One connection shared by the server's threads, guarded by a lock, as the readings'
    store is. Its file is readable only by its owner (_owner_only)."""

    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            _owner_only(Path(path))
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self.db.execute("PRAGMA foreign_keys = ON")
            if str(path) != ":memory:":
                self.db.execute("PRAGMA journal_mode = WAL")
            self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # Equipment

    def save_equipment(self, fields: dict, id: int | None = None, starter: bool = False) -> int:
        """Adds a piece of equipment, or changes one, and gives its id. A new one with `starter` gets its kind's usual
        maintenance (STARTER_TASKS)."""
        name = _text(fields.get("name"))
        kind = fields.get("kind")
        if not name:
            raise ManualError("A piece of equipment needs a name.")
        if kind not in KINDS:
            raise ManualError(f"Unknown kind {kind!r}.")
        installed = _text(fields.get("installed"), 10)
        if installed and not re.fullmatch(r"\d{4}(-\d{2}){0,2}", installed):
            raise ManualError(f"{installed!r} isn't a year, month or date (YYYY, YYYY-MM or YYYY-MM-DD).")
        specs = fields.get("specs") or []
        if not isinstance(specs, list):
            raise ManualError("specs must be a list.")
        specs = [
            {"name": _text(s.get("name"), 80), "value": _text(s.get("value"), 200)}
            for s in specs[:MAX_SPECS]
            if isinstance(s, dict) and _text(s.get("name"), 80)
        ]
        values = {
            "name": name,
            "kind": kind,
            "room": _text(fields.get("room")),
            "brand": _text(fields.get("brand")),
            "model": _text(fields.get("model")),
            "serial": _text(fields.get("serial")),
            "installed": installed,
            "notes": _text(fields.get("notes"), TEXT_LIMIT),
            "specs": json.dumps(specs),
        }
        now = _now()
        with self._lock, self.db:
            if id is None:
                cur = self.db.execute(
                    "INSERT INTO equipment (name, kind, room, brand, model, serial, installed, notes, specs, created, updated)"
                    " VALUES (:name, :kind, :room, :brand, :model, :serial, :installed, :notes, :specs, :now, :now)",
                    {**values, "now": now},
                )
                id = cur.lastrowid
                for title, every in STARTER_TASKS.get(kind, []) if starter else []:
                    self.db.execute(
                        "INSERT INTO tasks (equipment_id, title, every_months, created) VALUES (?, ?, ?, ?)", (id, title, every, now)
                    )
            else:
                cur = self.db.execute(
                    "UPDATE equipment SET name = :name, kind = :kind, room = :room, brand = :brand, model = :model,"
                    " serial = :serial, installed = :installed, notes = :notes, specs = :specs, updated = :now WHERE id = :id",
                    {**values, "now": now, "id": id},
                )
                if not cur.rowcount:
                    raise ManualError("That equipment isn't in the manual.", 404)
        return id

    def delete_equipment(self, id: int) -> None:
        with self._lock, self.db:
            self.db.execute("DELETE FROM equipment WHERE id = ?", (id,))

    def equipment(self, id: int) -> dict | None:
        with self._lock:
            row = self.db.execute("SELECT * FROM equipment WHERE id = ?", (id,)).fetchone()
        return self._equipment_json(row) if row else None

    @staticmethod
    def _equipment_json(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["specs"] = json.loads(item["specs"])
        return item

    # Files

    def add_file(self, equipment_id: int, kind: str, name: str, mime: str, data: bytes) -> int:
        """Stores a file for a piece of equipment: a photo of its label, its manual, a photo of it, or a receipt."""
        if kind not in FILE_KINDS:
            raise ManualError(f"Unknown kind of file {kind!r}.")
        check_file(mime, data)
        with self._lock, self.db:
            if not self.db.execute("SELECT 1 FROM equipment WHERE id = ?", (equipment_id,)).fetchone():
                raise ManualError("That equipment isn't in the manual.", 404)
            cur = self.db.execute(
                "INSERT INTO files (equipment_id, kind, name, mime, size, data, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (equipment_id, kind, _text(name) or kind, mime, len(data), data, _now()),
            )
        return cur.lastrowid

    def file(self, id: int) -> dict | None:
        """A stored file, with its bytes."""
        with self._lock:
            row = self.db.execute("SELECT id, equipment_id, kind, name, mime, data FROM files WHERE id = ?", (id,)).fetchone()
        return dict(row) if row else None

    def files_of(self, equipment_id: int, kinds: tuple[str, ...] = FILE_KINDS) -> list[dict]:
        """A piece of equipment's files of the given kinds, with their bytes, oldest first."""
        marks = ",".join("?" * len(kinds))
        with self._lock:
            rows = self.db.execute(
                f"SELECT id, kind, name, mime, data FROM files WHERE equipment_id = ? AND kind IN ({marks}) ORDER BY id",
                (equipment_id, *kinds),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_file(self, id: int) -> None:
        with self._lock, self.db:
            self.db.execute("DELETE FROM files WHERE id = ?", (id,))

    # Maintenance

    def save_task(self, fields: dict, id: int | None = None) -> int:
        """Adds a maintenance task, or changes one: something to do every so many months, for a piece of equipment or
        the house as a whole (no equipment_id)."""
        title = _text(fields.get("title"))
        every = fields.get("every_months")
        if not title:
            raise ManualError("A task needs a title.")
        if not isinstance(every, int) or isinstance(every, bool) or not 1 <= every <= 240:
            raise ManualError("every_months must be a whole number of months from 1 to 240.")
        equipment_id = fields.get("equipment_id")
        values = (equipment_id, title, every, _day(fields.get("last_done")), _text(fields.get("notes"), TEXT_LIMIT))
        with self._lock, self.db:
            if equipment_id is not None and not self.db.execute("SELECT 1 FROM equipment WHERE id = ?", (equipment_id,)).fetchone():
                raise ManualError("That equipment isn't in the manual.", 404)
            if id is None:
                cur = self.db.execute(
                    "INSERT INTO tasks (equipment_id, title, every_months, last_done, notes, created) VALUES (?, ?, ?, ?, ?, ?)",
                    (*values, _now()),
                )
                return cur.lastrowid
            cur = self.db.execute(
                "UPDATE tasks SET equipment_id = ?, title = ?, every_months = ?, last_done = ?, notes = ? WHERE id = ?", (*values, id)
            )
            if not cur.rowcount:
                raise ManualError("That task isn't in the manual.", 404)
        return id

    def mark_done(self, id: int, day: str) -> None:
        with self._lock, self.db:
            if not self.db.execute("UPDATE tasks SET last_done = ? WHERE id = ?", (_day(day), id)).rowcount:
                raise ManualError("That task isn't in the manual.", 404)

    def delete_task(self, id: int) -> None:
        with self._lock, self.db:
            self.db.execute("DELETE FROM tasks WHERE id = ?", (id,))

    # The whole manual

    def contents(self, today: date) -> dict:
        """Everything the page shows: each piece of equipment with its files (without their bytes) and its tasks, and
        the tasks for the house as a whole, each task with when it's due and how it stands on `today` (schedule)."""
        with self._lock:
            equipment = [self._equipment_json(r) for r in self.db.execute("SELECT * FROM equipment ORDER BY name COLLATE NOCASE, id")]
            files = [dict(r) for r in self.db.execute("SELECT id, equipment_id, kind, name, mime, size FROM files ORDER BY id")]
            tasks = [dict(r) for r in self.db.execute("SELECT * FROM tasks ORDER BY id")]
        for task in tasks:
            task.update(schedule(task, today))
        for item in equipment:
            item["files"] = [f for f in files if f["equipment_id"] == item["id"]]
            item["tasks"] = [t for t in tasks if t["equipment_id"] == item["id"]]
        return {"equipment": equipment, "tasks": [t for t in tasks if t["equipment_id"] is None]}

    def inventory(self, today: date) -> list[dict]:
        """The equipment as Claude is told about it for a question about the house: what each is and where, its label's
        details, notes, files by name, and its maintenance and when it's due."""
        contents = self.contents(today)
        keep = ("name", "kind", "room", "brand", "model", "serial", "installed", "notes", "specs")
        items = [
            {
                **{k: item[k] for k in keep if item[k]},
                "files": [f"{f['kind']}: {f['name']}" for f in item["files"]],
                "maintenance": [{k: t[k] for k in ("title", "every_months", "last_done", "due", "status")} for t in item["tasks"]],
            }
            for item in contents["equipment"]
        ]
        house = [{k: t[k] for k in ("title", "every_months", "last_done", "due", "status")} for t in contents["tasks"]]
        return items + ([{"name": "The house as a whole", "maintenance": house}] if house else [])


# Claude: reading a label, suggesting maintenance, answering questions

MODEL = "claude-opus-5-5"

LABEL_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(KINDS)},
        "name": {"type": "string"},
        "brand": {"type": "string"},
        "model": {"type": "string"},
        "serial": {"type": "string"},
        "manufactured": {"type": "string"},
        "specs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                "required": ["name", "value"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["kind", "name", "brand", "model", "serial", "manufactured", "specs", "notes"],
    "additionalProperties": False,
}

TASKS_SCHEMA = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "every_months": {"type": "integer"}, "why": {"type": "string"}},
                "required": ["title", "every_months", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["tasks"],
    "additionalProperties": False,
}

LABEL_SYSTEM = """You read the rating plate or label of a piece of household equipment (a furnace, water heater, air
conditioner, appliance) from a photo, for the owner's home manual.

- kind: what the equipment is, one of the given kinds; "other" if none fits.
- name: a short name for it, as the owner would say it ("Furnace", "Water heater", "Upstairs AC").
- brand, model, serial: exactly as printed. manufactured: the manufacture date as YYYY-MM or YYYY when the label gives
  it or the serial number encodes it in a way you're sure of; else an empty string.
- specs: what the owner may need later, each a short name and its value as printed: capacity, BTU or tonnage, fuel,
  voltage and amps, refrigerant, filter size, and so on. Leave out legal text and certifications.
- notes: anything to check, as text you couldn't read. Empty when there's nothing.
- Use empty strings for what the label doesn't show; don't guess."""

TASKS_SYSTEM = """You suggest the regular maintenance for one piece of household equipment, for the owner's home
manual, from what it is, its label's details, and its manual when one is attached (follow the manual over general
advice, and say so in "why").

- Each task: a short imperative title ("Replace the air filter"), how often in whole months (1 to 240), and a sentence
  on why or what it involves, including a part number or size when the manual or label gives one.
- Only tasks an owner does or arranges regularly; leave out one-time setup, repairs and anything already in the
  existing tasks given. At most 8, the most important first."""

ASK_SYSTEM = """You answer a homeowner's question about their house's equipment and its upkeep, for their home manual.

- Answer from the manuals attached and the inventory given, and say which manual a fact comes from. When neither has
  the answer, say so plainly, then give general guidance clearly marked as such.
- Be practical and short: the answer first, then any steps. Mention safety where it matters (gas, electricity, water).
- Plain text with simple line breaks; no markdown headings or tables."""


def _block(item: dict) -> dict:
    """A stored file as a content block: a PDF as a document, a photo as an image."""
    source = {"type": "base64", "media_type": item["mime"], "data": base64.b64encode(item["data"]).decode()}
    if item["mime"] == "application/pdf":
        return {"type": "document", "source": source, "title": item.get("name") or "manual"}
    return {"type": "image", "source": source}


class Assistant:
    """Claude for the home manual. `client` is an anthropic.Anthropic, or a stand-in with its messages.create."""

    def __init__(self, client):
        self.client = client

    def _json(self, system: str, content: list, schema: dict) -> dict:
        response = self._call(
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        )
        text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        try:
            return json.loads(text)
        except ValueError:
            raise ManualError("Claude's answer couldn't be read. Try again.", 502) from None

    def _call(self, **params):
        try:
            response = self.client.messages.create(model=MODEL, max_tokens=16000, **params)
        except Exception as err:  # the SDK's errors, as words for the page
            raise ManualError(*_describe(err)) from None
        if response.stop_reason == "refusal":
            raise ManualError("Claude declined to answer that.", 422)
        return response

    def read_label(self, photo: dict) -> dict:
        """The fields a label's photo (or a PDF of it) gives, for the owner to check before saving."""
        content = [_block(photo), {"type": "text", "text": "Read this label."}]
        return self._json(LABEL_SYSTEM, content, LABEL_SCHEMA)

    def suggest_tasks(self, equipment: dict, manuals: list[dict]) -> list[dict]:
        """Maintenance to add for a piece of equipment, beyond the tasks it has."""
        facts = {k: equipment.get(k) for k in ("name", "kind", "brand", "model", "installed", "specs", "notes") if equipment.get(k)}
        facts["existing tasks"] = [{"title": t["title"], "every_months": t["every_months"]} for t in equipment.get("tasks", [])]
        content = [*(_block(m) for m in manuals), {"type": "text", "text": f"The equipment:\n{json.dumps(facts, indent=1)}"}]
        tasks = self._json(TASKS_SYSTEM, content, TASKS_SCHEMA)["tasks"]
        return [
            {"title": _text(t["title"]), "every_months": min(max(int(t["every_months"]), 1), 240), "why": _text(t["why"], 500)}
            for t in tasks
            if _text(t["title"])
        ]

    def ask(self, question: str, inventory: list[dict], manuals: list[dict]) -> str:
        """An answer to a question about the house, from the inventory and the manuals attached. The manuals are
        cached, so follow-up questions about the same equipment soon after cost much less."""
        blocks = [_block(m) for m in manuals]
        if blocks:
            blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
        text = f"The house's equipment:\n{json.dumps(inventory, indent=1)}\n\nThe question: {question}"
        response = self._call(
            system=ASK_SYSTEM, messages=[{"role": "user", "content": [*blocks, {"type": "text", "text": text}]}], output_config={"effort": "medium"}
        )
        answer = "".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()
        if not answer:
            raise ManualError("Claude gave no answer. Try again.", 502)
        return answer


def _describe(err: Exception) -> tuple[str, int]:
    """An error from the Anthropic SDK as words and a status for the page."""
    try:
        import anthropic
    except ImportError:
        return str(err), 502
    if isinstance(err, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return "Anthropic rejected the API key. Check ANTHROPIC_API_KEY.", 502
    if isinstance(err, anthropic.RateLimitError):
        return "Claude is busy right now. Try again in a minute.", 503
    if isinstance(err, anthropic.APIConnectionError):
        return "Couldn't reach Claude. Check the internet connection.", 502
    if isinstance(err, anthropic.APIStatusError):
        return f"Claude returned an error: {err.message}", 502
    return str(err), 502


def saved_key(path: Path | None) -> str | None:
    """The API key --anthropic-sign-in saved (save_key), or None."""
    if not path:
        return None
    try:
        key = json.loads(Path(path).read_text()).get("api_key")
    except (OSError, ValueError, AttributeError):
        return None
    return key.strip() if isinstance(key, str) and key.strip() else None


def save_key(path: Path, key: str) -> None:
    """Keeps an API key in `path`, readable only by its owner, replacing the file whole, as the sign-ins are kept."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump({"api_key": key}, file)
    os.replace(part, path)


def check_key(key: str) -> None:
    """Asks Anthropic whether it takes the key and has the model the manual uses (free: no tokens)."""
    try:
        import anthropic
    except ImportError:
        raise ManualError("Install the anthropic package first: pip install anthropic") from None
    try:
        anthropic.Anthropic(api_key=key).models.retrieve(MODEL)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        raise ManualError("Anthropic didn't accept that key.") from None
    except anthropic.NotFoundError:
        raise ManualError(f"Anthropic took the key, but {MODEL} isn't available to it.") from None
    except anthropic.APIConnectionError:
        raise ManualError("Couldn't reach Anthropic to check the key. Check the internet connection and try again.") from None
    except anthropic.APIStatusError as e:
        raise ManualError(f"Anthropic couldn't check the key: {e.message}") from None


def sign_in_interactively(path: Path, ask_secret=None, check=check_key) -> None:
    """Asks for an Anthropic API key, hidden as it's typed, checks Anthropic takes it, and saves it to `path`."""
    import getpass

    key = (ask_secret or getpass.getpass)("Anthropic API key (from platform.claude.com; hidden as you type or paste): ").strip()
    if not key:
        raise ManualError("No key given.")
    check(key)
    save_key(Path(path), key)
    print(f"Saved to {path}, readable only by you. Restart the house monitor for the home manual to use Claude.")


def assistant_from_environment(key_file: Path | None = None) -> tuple[Assistant | None, str | None]:
    """Claude, when the anthropic package is installed and there's an API key: ANTHROPIC_API_KEY, else the one
    --anthropic-sign-in saved to `key_file`, else an `ant auth login` profile, which the SDK finds itself; else None and
    why, for the page."""
    try:
        import anthropic
    except ImportError:
        return None, "Claude needs the anthropic package (pip install anthropic) and an API key (python3 -m housemon --anthropic-sign-in)."
    key = os.environ.get("ANTHROPIC_API_KEY") or saved_key(key_file)
    profile = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "anthropic"
    if not key and not os.environ.get("ANTHROPIC_AUTH_TOKEN") and not profile.exists():
        return None, "Claude needs an API key: run python3 -m housemon --anthropic-sign-in, then restart the house monitor."
    return Assistant(anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()), None
