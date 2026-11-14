"""Sending the house and its stored readings to the cloud service (cloud/ in this repo), for the house's page there.

The uplink sends the readings the store has, in batches, in the order they were stored, and remembers how far the
house in the cloud has taken them (Store.unsent, Store.mark_sent), so a restart, or an outage of the network or the
cloud, loses nothing: what's waiting goes up once it can. It marks a batch sent only once the cloud says it has it,
so a batch whose reply was lost is sent again. The cloud keeps each reading once, by its sensor and time, so that's
harmless: delivery is at least once, and the cloud makes it count once.

It sends the house too, as `--dump` prints it, at first and whenever the house file changes, so the page there can
draw it. It sends readings only: not a litter box's state or a vacuum's, as yet.

The house's URL in the cloud and its ingest token are kept in a file of their own (data/uplink.json unless told
otherwise), readable only by their owner, as `--uplink-sign-in` saves them.
"""

import http.client
import json
import math
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, OpenerDirector, Request, build_opener

from .polling import poll_gently
from .readings import Reading
from .store import Store

BATCH = 1000  # readings a request, the most the cloud service takes (MaxBatch in cloud/api/api.go)
INTERVAL = 10.0  # seconds between looks for readings to send, once all have gone
TIMEOUT = 30.0  # seconds, for each request


class UplinkError(Exception):
    """Something went wrong sending to the cloud service."""


class TokenRefused(UplinkError):
    """The cloud service won't take the saved token, or there's none: set the uplink up again."""


@dataclass(frozen=True)
class Target:
    """A house in the cloud service: its URL, as https://example.com/houses/home, and its ingest token."""

    url: str
    token: str

    def save(self, path: Path) -> None:
        """Keep it, readable only by its owner, replacing the file whole."""
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump({"url": self.url, "token": self.token}, file)
        os.replace(part, path)

    @staticmethod
    def load(path: Path) -> "Target":
        try:
            saved = json.loads(path.read_text())
            return Target(saved["url"], saved["token"])
        except FileNotFoundError:
            raise TokenRefused(f"no uplink set up at {path}") from None
        except (ValueError, KeyError, TypeError):
            raise TokenRefused(f"{path} isn't an uplink as --uplink-sign-in saves one") from None


class _NoRedirects(HTTPRedirectHandler):
    """Leaves a redirect unfollowed, to fail as an answer it didn't expect: urllib would follow a POST's as a GET,
    without its readings."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def new_opener() -> OpenerDirector:
    return build_opener(_NoRedirects())


def request(opener: OpenerDirector, target: Target, method: str, path: str, body: object) -> dict | None:
    """Send body, as JSON, to the house's path in the cloud, and return what it answers, if anything."""
    data = json.dumps(body, allow_nan=False).encode()  # NaN and infinity aren't JSON, and the cloud refuses them
    headers = {"Authorization": f"Bearer {target.token}", "Content-Type": "application/json"}
    try:
        with opener.open(Request(target.url + path, data=data, headers=headers, method=method), timeout=TIMEOUT) as response:
            answer = response.read()
    except HTTPError as e:
        try:
            error = json.load(e).get("error")
        except (ValueError, AttributeError):
            error = None
        e.close()
        if e.code in (401, 403):
            raise TokenRefused(f"the cloud service refused the uplink's token for {target.url}") from None
        raise UplinkError(f"the cloud service answered {e.code}" + (f": {error}" if error else "")) from None
    except (URLError, OSError, http.client.HTTPException) as e:  # as the network down, or the reply lost
        reason = e.reason if isinstance(e, URLError) else e
        raise UplinkError(f"couldn't reach {urlparse(target.url).netloc}: {str(reason) or type(reason).__name__}") from None
    try:
        return json.loads(answer) if answer else None
    except ValueError:
        raise UplinkError("the cloud service's answer wasn't JSON") from None


def _sendable(reading: Reading) -> bool:
    """Whether the reading can go as JSON: an infinite value, which SQLite keeps, can't."""
    return all(math.isfinite(v) for v in (reading.ts, reading.temperature_c, reading.humidity or 0.0))


class Uplink:
    """Sends the house, and the readings in store, to the house in the cloud service the file at path names.

    house() is the house as `--dump` prints it, looked at each round so a changed house file is sent again."""

    def __init__(
        self,
        path: Path,
        store: Store,
        house: Callable[[], dict],
        opener: OpenerDirector | None = None,
        batch: int = BATCH,
        interval: float = INTERVAL,
        log: Callable[[str], None] | None = None,
    ):
        self.path = Path(path)
        self.store = store
        self.house = house
        self.opener = opener or new_opener()
        self.batch = batch
        self.interval = interval
        self.log = log or (lambda message: print(f"uplink: {message}", file=sys.stderr, flush=True))
        self.stop = threading.Event()  # run's, so a long catch-up stops when the server does
        self._house_sent: dict[str, str] = {}  # the house as last sent to each URL, as JSON

    def poll(self, now: float) -> list:
        """One round: the house, if it's changed since it was sent, then every reading not yet sent, a batch at a
        time. It gives nothing to publish, as poll_gently's poll would."""
        target = Target.load(self.path)  # read again each round, so setting it up again takes effect
        try:
            house = self.house()
        except Exception as e:  # as a house file that loads but can't be laid out: tried again, not the thread ended
            raise UplinkError(f"couldn't work out the house to send: {e!r}") from None
        sent = json.dumps(house, sort_keys=True)
        if self._house_sent.get(target.url) != sent:
            request(self.opener, target, "PUT", "/api/house", house)
            self._house_sent[target.url] = sent
        while not self.stop.is_set():
            unsent = self.store.unsent(target.url, self.batch)
            if not unsent:
                break
            self._send(target, unsent)
            if len(unsent) < self.batch:
                break  # caught up
        return []

    def _send(self, target: Target, unsent: list[tuple[int, Reading]]) -> None:
        readings = [reading for _, reading in unsent if _sendable(reading)]
        if len(readings) < len(unsent):
            self.log(f"left out {len(unsent) - len(readings)} readings with a value that isn't a number, which can't be sent")
        answer = request(self.opener, target, "POST", "/api/readings", {"readings": [r.to_json() for r in readings]})
        rejected = (answer.get("rejected") if isinstance(answer, dict) else None) or []
        if rejected:
            # Rejected for good, as one with a value the cloud won't take: sending it again would be rejected again.
            first = rejected[0]
            r = readings[first["index"]] if 0 <= first.get("index", -1) < len(readings) else None
            which = f"{r.sensor_id}'s at {r.ts}" if r else "one"
            self.log(f"the cloud service rejected {len(rejected)} of {len(readings)} readings, {which} as {first.get('error')!r}")
        self.store.mark_sent(target.url, unsent[-1][0])

    def run(self, stop: threading.Event) -> None:
        """Send the house and its readings until stop is set, gently (polling.py): backing off while it can't, as in
        a network outage, and waiting while the token is refused until the uplink is set up again."""
        self.stop = stop
        poll_gently(
            self.poll, lambda _: None, stop, self.interval,
            sign_in=self.path, lapsed=TokenRefused, failed=(UplinkError,),
            sign_in_again="python3 -m housemon --uplink-sign-in",
            log=self.log,
        )


def sign_in_interactively(path: Path, ask: Callable[[str], str] = input, ask_secret: Callable[[str], str] | None = None, opener: OpenerDirector | None = None) -> Target:
    """Ask for the house's URL in the cloud service and its ingest token, check the cloud takes the token, and save
    them to path."""
    import getpass

    ask_secret = ask_secret or getpass.getpass
    url = ask("The house's URL in the cloud service, as https://example.com/houses/home: ").strip().rstrip("/")
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or not parts.path.startswith("/houses/"):
        raise UplinkError(f"{url!r} isn't a house's URL, as https://example.com/houses/home")
    if parts.scheme == "http" and parts.hostname not in ("localhost", "127.0.0.1", "::1"):
        print("Over http, not https, the token and readings cross the network unencrypted.", file=sys.stderr)
    token = ask_secret("Its ingest token, as housecloud house add printed it (saved, readable only by you): ").strip()
    target = Target(url, token)
    request(opener or new_opener(), target, "POST", "/api/readings", {"readings": []})  # stores nothing
    target.save(path)
    print(f"The cloud service takes the token; saved to {path}, readable only by you.")
    print("Start the app and it sends the house there, and its readings, those it has already first.")
    return target
