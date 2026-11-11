"""Readings from ecobee sensors, a thermostat and its SmartSensors, from ecobee's cloud.

ecobee no longer gives out developer API keys, so this signs in as ecobee's own
web app does, with the account's email and password, and a code from its second
factor if it has one, as Home Assistant's ecobee integration has since 2026.3.
It asks only to read. It keeps the refresh token sign-in gives, in a file of its
own (data/ecobee.json unless told otherwise), never the password, and polls the
thermostat at most every three minutes. Each sensor reports its temperature in
tenths of a degree Fahrenheit; the thermostat reports humidity too, a SmartSensor
none. A room names the sensor that reads it by the name the ecobee app shows,
and the outside can name a thermostat, to read the weather ecobee's weather
service reports where it is: the temperature and humidity now, in the same
units.
"""

import base64
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, OpenerDirector, Request, build_opener

from .polling import poll_gently
from .readings import Reading

AUTH = "https://auth.ecobee.com"
API = "https://api.ecobee.com/1/thermostat"
CLIENT_ID = "183eORFPlXyz9BbDZwqexHPBQoVjgadh"  # ecobee's web app
REDIRECT_URI = "https://www.ecobee.com/home/authCallback"
AUDIENCE = "https://prod.ecobee.com/api/v1"
SCOPE = "openid offline_access smartRead"
TIMEOUT = 30.0  # seconds, for each request
POLL = 180.0  # seconds between polls: ecobee's sensors report every few minutes
UNKNOWN = {"unknown", "-5002", "-5003"}  # a capability's value when the sensor has none yet, or is calibrating


class EcobeeError(Exception):
    """Something went wrong talking to ecobee."""


class SignInNeeded(EcobeeError):
    """The saved sign-in is gone or no longer good: sign in again."""


@dataclass(frozen=True)
class SensorReading:
    """One ecobee sensor's latest values: its name in the ecobee app, its thermostat's, and what it reads."""

    name: str
    thermostat: str
    temperature_c: float
    humidity: float | None


class _Manual(HTTPRedirectHandler):
    """Leaves a redirect to the caller, which reads where it points without following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def openers() -> tuple[OpenerDirector, OpenerDirector]:
    """Two openers sharing one cookie jar, as a browser signing in keeps its session: one that follows redirects,
    and one that leaves them to the caller."""
    jar = CookieJar()
    return build_opener(HTTPCookieProcessor(jar)), build_opener(HTTPCookieProcessor(jar), _Manual())


def _param(url: str, name: str) -> str | None:
    match = re.search(rf"[?&]{name}=([^&#]+)", url)
    return match.group(1) if match else None


def _form(form: dict) -> bytes:
    return urlencode(form).encode()


def _walk(manual: OpenerDirector, url: str, form: dict | None = None) -> str:
    """Where a step of signing in lands: posting the form to url, or getting it, and following its redirects while
    they stay on ecobee's sign-in site, the first one leaving it unfollowed. A page it rejects, as a wrong password
    or code is, lands back on url."""
    data = None if form is None else _form(form)
    for _ in range(10):
        try:
            with manual.open(Request(url, data=data), timeout=TIMEOUT) as response:
                return response.geturl()
        except HTTPError as e:
            location = e.headers.get("Location")
            e.close()
            if e.code in (400, 401, 403) and data is not None:
                return url
            if e.code not in (301, 302, 303, 307, 308) or not location:
                raise EcobeeError(f"ecobee's sign-in answered {e.code}") from None
        location = urljoin(url, location)
        if urlparse(location).netloc != urlparse(AUTH).netloc:
            return location
        url, data = location, None
    raise EcobeeError("ecobee's sign-in kept redirecting")


def _token(opener: OpenerDirector, form: dict) -> dict:
    """Tokens from ecobee's token endpoint, for a code from signing in or a refresh token."""
    request = Request(f"{AUTH}/oauth/token", data=_form(form), headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            tokens = json.load(response)
    except HTTPError as e:
        try:
            error = json.load(e).get("error")
        except ValueError:
            error = None
        e.close()
        if error == "invalid_grant":
            raise SignInNeeded("ecobee no longer takes the saved sign-in") from None
        raise EcobeeError(f"ecobee's token endpoint answered {e.code}") from None
    if "access_token" not in tokens:
        raise EcobeeError("ecobee gave no access token")
    return tokens


def sign_in(email: str, password: str, code_for: Callable[[str], str], openers=openers) -> dict:
    """Sign in with an ecobee account, as its web app does, and return the tokens: access_token, refresh_token and
    expires_in. code_for(kind) asks for the code from the account's second factor, "an authenticator app" or "a text
    message", when it has one."""
    follow, manual = openers()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    query = urlencode({
        "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI, "audience": AUDIENCE,
        "scope": SCOPE, "code_challenge": challenge, "code_challenge_method": "S256",
    })
    try:
        with follow.open(f"{AUTH}/authorize?{query}", timeout=TIMEOUT) as response:
            url = response.geturl()
        state = _param(url, "state")
        if "/u/login/identifier" not in url or not state:
            raise EcobeeError("ecobee's sign-in page isn't where it was")
        with follow.open(Request(url, data=_form({"state": state, "username": email})), timeout=TIMEOUT) as response:
            url = response.geturl()
    except HTTPError as e:
        e.close()
        raise EcobeeError(f"ecobee's sign-in answered {e.code}") from None
    if "/u/login/password" not in url:
        raise EcobeeError("ecobee didn't take that email")
    landing = _walk(manual, url, {"state": state, "username": email, "password": password})
    if "/u/login/password" in landing:
        raise EcobeeError("ecobee didn't take that password")
    kind = "an authenticator app" if "/u/mfa-otp-challenge" in landing else "a text message" if "/u/mfa-sms-challenge" in landing else None
    if kind:
        landing = _walk(manual, landing, {"state": _param(landing, "state") or state, "code": code_for(kind).strip()})
    if "/u/mfa-" in landing:
        raise EcobeeError("ecobee didn't take that code" if kind else "ecobee asks for a second factor this can't give")
    if "/u/custom-prompt" in landing:
        raise EcobeeError("ecobee has something to ask first: sign in at ecobee.com once, then try again")
    code = _param(landing, "code")
    if not code:
        raise EcobeeError("ecobee's sign-in ended somewhere unexpected")
    tokens = _token(follow, {
        "grant_type": "authorization_code", "code": code, "code_verifier": verifier, "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
    })
    if "refresh_token" not in tokens:
        raise EcobeeError("ecobee gave no refresh token")
    return tokens


def _number(value) -> float | None:
    if value is None or str(value) in UNKNOWN:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def sensor_readings(thermostats: list[dict]) -> Iterator[SensorReading]:
    """Each sensor's values, as the thermostat endpoint gives them with includeSensors, but for a thermostat that's
    offline, whose values are old, and a sensor with no temperature yet."""
    for thermostat in thermostats:
        if not thermostat.get("runtime", {}).get("connected", True):
            continue
        for sensor in thermostat.get("remoteSensors", []):
            values = {c.get("type"): c.get("value") for c in sensor.get("capability", [])}
            tenths = _number(values.get("temperature"))
            if tenths is None:
                continue
            humidity = _number(values.get("humidity"))
            yield SensorReading(sensor.get("name", ""), thermostat.get("name", ""), round((tenths / 10 - 32) * 5 / 9, 2), humidity)


def weather_reports(thermostats: list[dict]) -> Iterator[SensorReading]:
    """Each thermostat's weather, its area's from ecobee's weather service, as the thermostat endpoint gives it with
    includeWeather: the first forecast, which is the conditions now, named for the thermostat. Not a sensor at the
    house, so it can differ a little from what's outside it."""
    for thermostat in thermostats:
        forecasts = thermostat.get("weather", {}).get("forecasts") or []
        tenths = _number(forecasts[0].get("temperature")) if forecasts else None
        if tenths is None:
            continue
        name = thermostat.get("name", "")
        yield SensorReading(name, name, round((tenths / 10 - 32) * 5 / 9, 2), _number(forecasts[0].get("relativeHumidity")))


class Account:
    """A signed-in ecobee account: its tokens, kept in a file, refreshed as they expire."""

    def __init__(self, path: Path, opener: OpenerDirector | None = None):
        self.path = Path(path)
        self.opener = opener or build_opener()
        self.tokens: dict | None = None
        self.mtime = None

    def save(self, tokens: dict) -> None:
        """Keep the tokens, readable only by their owner, replacing the file whole."""
        self.tokens = tokens
        self.path.parent.mkdir(parents=True, exist_ok=True)
        part = self.path.with_name(self.path.name + ".part")
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(tokens, file)
        os.replace(part, self.path)
        self.mtime = self.path.stat().st_mtime

    def _load(self) -> dict:
        """The saved tokens, read again whenever the file changes, as when signing in again while it runs."""
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            raise SignInNeeded(f"no ecobee sign-in at {self.path}") from None
        if self.tokens is None or mtime != self.mtime:
            self.tokens, self.mtime = json.loads(self.path.read_text()), mtime
        return self.tokens

    def _refresh(self) -> dict:
        tokens = self._load()
        if "refresh_token" not in tokens:
            raise SignInNeeded("the ecobee sign-in has no refresh token")
        fresh = _token(self.opener, {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": CLIENT_ID})
        fresh.setdefault("refresh_token", tokens["refresh_token"])  # kept unless ecobee gives a new one
        fresh["expires_at"] = time.time() + float(fresh.get("expires_in", 3600))
        self.save(fresh)
        return fresh

    def thermostats(self) -> list[dict]:
        """The account's thermostats with their sensors, refreshing the access token first when it's expired or
        about to, or when ecobee says it has."""
        tokens = self._load()
        if "access_token" not in tokens or tokens.get("expires_at", 0) < time.time() + 60:
            tokens = self._refresh()
        for attempt in (1, 2):
            selection = {"selection": {
                "selectionType": "registered", "selectionMatch": "", "includeSensors": True, "includeRuntime": True, "includeWeather": True,
            }}
            request = Request(
                f"{API}?{urlencode({'json': json.dumps(selection)})}",
                headers={"Authorization": f"Bearer {tokens['access_token']}", "Content-Type": "application/json;charset=UTF-8"},
            )
            try:
                with self.opener.open(request, timeout=TIMEOUT) as response:
                    return json.load(response).get("thermostatList", [])
            except HTTPError as e:
                try:
                    status = json.load(e).get("status", {}).get("code")
                except ValueError:
                    status = None
                e.close()
                if status == 14 and attempt == 1:  # the access token expired
                    tokens = self._refresh()
                    continue
                if status in (1, 16):
                    raise SignInNeeded("ecobee no longer takes the saved sign-in") from None
                raise EcobeeError(f"ecobee's thermostat endpoint answered {e.code}") from None
        raise EcobeeError("ecobee kept saying the access token had expired")


class EcobeeSource:
    """Readings for the rooms ecobee sensors read, polled every `interval` seconds. rooms() maps each ecobee sensor's
    name, as the ecobee app shows it, to the sensor id of the room it reads, and weather() each thermostat's name to
    the sensor id of the place its weather reads, as the outside; both are looked up each poll, so a changed house
    file takes effect, and a name matches whatever its case. A name the account has no sensor or thermostat by is
    warned about, with the names it has, once until it's matched again."""

    def __init__(
        self,
        account: Account,
        rooms: Callable[[], dict[str, str]],
        interval: float = POLL,
        warn: Callable[[str], None] | None = None,
        weather: Callable[[], dict[str, str]] = dict,
    ):
        self.account = account
        self.rooms = rooms
        self.weather = weather
        self.interval = interval
        self.warn = warn or (lambda message: print(f"ecobee: {message}", file=sys.stderr, flush=True))
        self.unmatched: set[tuple[str, str]] = set()  # the names warned about: ("sensor" or "thermostat", casefolded)

    def poll(self, now: float) -> list[Reading]:
        sensors, weather = self.rooms(), self.weather()
        if not sensors and not weather:
            return []
        thermostats = self.account.thermostats()
        readings = self._read(now, sensors, sensor_readings(thermostats)) + self._read(now, weather, weather_reports(thermostats))
        # Every sensor's and thermostat's name, one with no reading for now, as when it's offline, included, so it isn't
        # taken for a name that's wrong.
        unmatched: set[tuple[str, str]] = set()
        self._check("sensor", sensors, [s.get("name", "") for t in thermostats for s in t.get("remoteSensors", [])], unmatched)
        self._check("thermostat", weather, [t.get("name", "") for t in thermostats], unmatched)
        self.unmatched = unmatched
        return readings

    @staticmethod
    def _read(now: float, names: dict[str, str], values: Iterator[SensorReading]) -> list[Reading]:
        rooms = {name.casefold(): sensor_id for name, sensor_id in names.items()}
        return [Reading(rooms[v.name.casefold()], now, v.temperature_c, v.humidity) for v in values if v.name.casefold() in rooms]

    def _check(self, kind: str, names: dict[str, str], has: list[str], unmatched: set[tuple[str, str]]) -> None:
        """Warn of the names the account has no `kind` by, but those warned of already, and note them in unmatched."""
        known = {name.casefold() for name in has}
        missing = sorted((name for name in names if name.casefold() not in known), key=str.casefold)
        new = [name for name in missing if (kind, name.casefold()) not in self.unmatched]
        if new:
            listed = ", ".join(repr(name) for name in has) or "none"
            self.warn(f"no {kind} named {', '.join(repr(name) for name in new)}, as the house file has; the account has {listed}")
        unmatched.update((kind, name.casefold()) for name in missing)

    def run(self, publish: Callable[[Reading], None], stop: threading.Event) -> None:
        """Call publish(reading) for each room's reading every poll until stop is set, gently (polling.py): backing off
        while polls fail, as in a network outage, and waiting while the sign-in has lapsed until it's signed in again,
        so neither stops the rest of the house."""
        poll_gently(
            self.poll, publish, stop, self.interval,
            sign_in=self.account.path, lapsed=SignInNeeded, failed=(EcobeeError, URLError, OSError, ValueError),
            sign_in_again="python3 -m housemon --ecobee-sign-in",
            log=lambda message: print(f"ecobee: {message}", file=sys.stderr, flush=True),
            describe=lambda e: str(e) if isinstance(e, EcobeeError) else f"couldn't get its readings: {e}",
        )


def sign_in_interactively(path: Path, ask: Callable[[str], str] = input, ask_secret: Callable[[str], str] | None = None) -> None:
    """Ask for the account's email, password and any code, sign in, save the tokens to path and list the sensors
    found, for naming them in the house file."""
    import getpass

    ask_secret = ask_secret or getpass.getpass
    email = ask("ecobee email: ").strip()
    password = ask_secret("ecobee password (not saved): ")
    tokens = sign_in(email, password, lambda kind: ask(f"Code from {kind}: "))
    tokens["expires_at"] = time.time() + float(tokens.get("expires_in", 3600))
    account = Account(path)
    account.save(tokens)
    print(f"Signed in; saved to {path}, readable only by you. Its sensors:")
    thermostats = account.thermostats()
    for sensor in sensor_readings(thermostats):
        humidity = "" if sensor.humidity is None else f", {sensor.humidity:.0f}% humidity"
        print(f"  {sensor.name!r} on {sensor.thermostat!r}: {sensor.temperature_c * 9 / 5 + 32:.1f}°F{humidity}")
    for report in weather_reports(thermostats):
        humidity = "" if report.humidity is None else f", {report.humidity:.0f}% humidity"
        print(f"  the weather where {report.name!r} is: {report.temperature_c * 9 / 5 + 32:.1f}°F{humidity}")
    print('Give a room in the house file ecobee="<sensor>" for its readings to come from that sensor, and the outside')
    print('ecobee_weather="<thermostat>" for its readings to come from that thermostat\'s weather.')
