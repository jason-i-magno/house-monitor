"""What a MOVA robot vacuum is doing, and its battery, from MOVA's cloud, as its MOVAhome app gets them.

MOVA has no public API, so this signs in as the MOVAhome app does, with the account's email and password, the way
the community Dreame Vacuum integration for Home Assistant (github.com/Tasshack/dreame-vacuum, MIT licensed) has
worked it out: with the app's own client id and secret and the salt it hashes every password with, which are the
same for every MOVAhome user, and its way of signing each request. The newest apps connect with a TLS handshake of
their own, which that integration copies byte for byte; this connects with Python's own, which MOVA's cloud may one
day stop taking. It keeps the refresh token sign-in gives, in a file of its own (data/mova.json unless told
otherwise), never the password, and asks each vacuum on the account what it's doing and its battery every minute.
"""

import base64
import gzip
import hashlib
import http.client
import json
import os
import secrets
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote, urlsplit

from .readings import Vacuum, VacuumPart

REGIONS = ("us", "eu", "cn", "sg", "ru", "in")  # MOVA's clouds, each with accounts of its own
PORT = 13267
CLIENT = "bW92YV9hcHA6VjdLb0NoTFc4dkhBQ3FHYg=="  # the MOVAhome app's client id and secret, for HTTP basic auth
SALT = "RAylYC%fmSKp7%Tq"  # the app adds this to every password before hashing it
KEY = b"gigxlmqwZ]7oWZUF"  # the app's key for signing requests, and for the header naming the account's region
APP_VERSION = "10605530"
TENANT = "000002"  # MOVA's, until signing in names it
USER_AGENT = "Dart/3.10 (dart:io)"
TIMEOUT = 15.0  # seconds, for each request
POLL = 60.0  # seconds between polls
DEVICES_EVERY = 3600.0  # seconds between listings of the account's vacuums, to take up one added or renamed
EXPIRES_SOON = 600.0  # seconds: an access token this close to expiring is refreshed first, as the app does
# The properties asked for, as (service, property) in the vacuum's spec, all in one request, as it takes up to 15.
STATE, ERROR, BATTERY = (2, 1), (2, 2), (3, 1)
# The parts it counts down, the percent of each one's life left: those to replace when they run out, and to clean.
PARTS = {
    (9, 2): ("main brush", True), (10, 2): ("side brush", True), (11, 1): ("filter", True), (18, 1): ("mop pads", True),
    (17, 1): ("water tank filter", True), (16, 1): ("sensors", False), (30, 2): ("wheels", False),
}
# Its dock's tanks and bag, and what each one's value means when it's a problem; another value is all right.
DOCK = {
    (27, 1): {1: "Clean water tank is out", 2: "Clean water tank is low"},
    (27, 2): {1: "Dirty water tank is full or out"},
    (27, 3): {1: "Dust bag is out", 2: "Check the dust bag"},
    (27, 4): {2: "Detergent is low"},
}
WANTED = [STATE, ERROR, BATTERY, *PARTS, *DOCK]
# What a vacuum is doing, by the number its state property gives.
STATES = {
    1: "Vacuuming", 2: "Idle", 3: "Paused", 4: "Error", 5: "Returning to its dock", 6: "Charging", 7: "Mopping",
    8: "Drying its mop", 9: "Washing its mop", 10: "Returning to wash its mop", 11: "Mapping", 12: "Vacuuming and mopping",
    13: "Charged", 14: "Updating", 15: "Called to clean", 16: "Resetting its dock", 17: "Returning to put on its mop",
    18: "Returning to take off its mop", 19: "Checking its water", 20: "Adding water", 21: "Washing paused",
    22: "Emptying its bin", 23: "Under remote control", 24: "Smart charging", 25: "Cleaning again", 26: "Following",
    27: "Spot cleaning", 28: "Returning to empty its bin", 29: "Waiting for a task", 30: "Cleaning its dock",
    31: "Returning to drain", 32: "Draining", 33: "Draining water", 34: "Emptying", 35: "Drying its dust bag",
    36: "Drying its dust bag paused", 37: "Heading to clean more", 38: "Cleaning more", 95: "Finding the pet paused",
    96: "Finding the pet", 97: "Running a shortcut", 98: "Monitoring",
}
ERRORS = {4}  # the states that are a problem
# What each error code means, in the words of the integration's English, from the app's. 0 is none.
ERROR_TEXT = {
    1: "Wheels are suspended", 2: "Cliff sensor error", 3: "Collision sensor is stuck", 4: "Robot is tilted",
    5: "Collision sensor is stuck", 6: "Wheels are suspended", 7: "Optical flow sensor error",
    8: "Dust bin not installed", 9: "Water tank not installed", 10: "Water tank is empty",
    11: "The filter not dry or blocked", 12: "The main brush wrapped", 13: "The side brush wrapped",
    14: "The filter not dry or blocked",
    15: "The robot is stuck, or its left wheel may be blocked by foreign objects",
    16: "The robot is stuck, or its right wheel may be blocked by foreign objects",
    17: "The robot is stuck, or cannot turn", 18: "The robot is stuck, or cannot go forward", 19: "Cannot find base",
    20: "Low battery", 21: "Charging error", 22: "Battery level error", 23: "Internal error",
    24: "Visual positioning sensor error", 25: "Move sensor error", 26: "Optical sensor error",
    27: "Infrared shielding error", 28: "The charging dock is not powered on", 29: "Battery error",
    30: "Fan speed sensor error", 31: "Left wheel may be blocked by foreign objects",
    32: "Right wheel may be blocked by foreign objects", 33: "Accelerometer error", 34: "Gyro error",
    35: "Gyro error", 36: "Left magnet sensor error", 37: "Right magnet sensor error", 38: "Flow sensor error",
    39: "Infrared error", 40: "Camera error", 41: "Strong magnetic field detected", 42: "Water pump error",
    43: "RTC error", 44: "Internal error", 45: "Internal error", 46: "Internal error",
    47: "Cleanup route is blocked, returning to the dock", 48: "Laser distance sensor error",
    49: "Laser distance sensor bumper error", 50: "Water pump error", 51: "The filter not dry or blocked",
    54: "Edge sensor error", 55: "Please start the robot in non-carpet area",
    56: "The 3D obstacle avoidance sensor is malfunctioning", 57: "Edge sensor error",
    58: "The ultrasonic sensor is malfunctioning", 59: "No-Go zone or virtual wall detected",
    61: "Cleanup route is blocked", 62: "Cleanup route is blocked",
    63: "Cleanup route is blocked, returning to the dock", 64: "Cleanup route is blocked, returning to the dock",
    65: "Detected that the vacuum-mop is in a restricted area",
    66: "Detected that the vacuum-mop is in a restricted area",
    67: "Detected that the vacuum-mop is in a restricted area",
    68: "Mopping completed. Please remove and clean the mop in time",
    69: "The mop pad came off during the cleaning task", 70: "The mop pad came off during the cleaning task",
    71: "The mop pad has stopped rotating", 72: "The mop pad has stopped rotating",
    74: "Mop pad installation failed", 75: "Low battery. Robot will shut down soon",
    76: "The used water tank of robot is not installed",
    78: "Hidden area. Please move the robot to the appropriate area and retry", 79: "Failed to lift LDS module",
    80: "LDS cannot be raised here for positioning", 81: "LDS cannot be raised here for positioning",
    82: "Slippery floor. Please try again later", 84: "Unknown error code",
    85: "Please check if the mop is installed properly", 86: "Abnormal water level in the robot's used water tank",
    88: "Please check if the retractable legs are tangled",
    89: "Malfunction due to an internal error. Try to restart the robot",
    90: "LDS cannot be raised here for positioning", 91: "Robot stuck among the tables and chairs",
    92: "Robot stuck in the narrow passage", 93: "Robot stuck at the step/threshold",
    94: "Robot stuck in a low-clearance area", 95: "Robot has detected Easy-to-Fall ramps on the path",
    96: "Robot has detected obstacles on the path", 97: "Robot has detected people or pets on the path",
    98: "Robot is stuck due to slipping", 99: "Robot slips on the carpet",
    101: "The dust collection bag is full, or the air duct is blocked",
    102: "The upper cover of auto-empty base is not closed, or the dust collection bag is not installed",
    103: "The upper cover of auto-empty base is not closed, or the dust collection bag is not installed",
    104: "The dust collection bag is full, or the air duct is blocked",
    105: "The clean water tank is not installed", 106: "The dirty water tank is full or not installed",
    107: "Low water level in the clean water tank, please fill with water timely",
    108: "The dirty water tank is full or not installed", 109: "Dirty water tank blocked",
    110: "Dirty water tank pump error", 111: "The washboard is not installed properly",
    112: "The water level of the washboard is abnormal, please clean the washboard timely",
    114: "The cleaning task is complete, please clean the mop pad washboard",
    116: "Check and fill the clean water tank", 117: "Base station not powered on",
    118: "The water level in the used water tank is too high", 119: "Water level in the washboard is too high",
    120: "Mop pad is not in the station", 121: "Dust bag is full or vents are blocked", 122: "Unknown error code",
    123: "Abnormal water discharge from the clean water tank of the upper and lower water modules",
    124: "Washboard stops working", 125: "Abnormal water drainage from used water tank", 126: "Mop not detected",
    127: "Mop holder quantity/placement error in the dock", 128: "Dock Error", 129: "Failed to wash dirty mop",
    200: "Robot slips in the curtain area", 201: "Edge mop stopped rotating", 202: "Edge mop detached",
    203: "Chassis lift malfunction", 207: "Malfunction due to an internal error. Try to restart the robot",
    209: "Check for any debris near the roller mop and mop cover",
    210: "Check for any debris near the roller mop and mop cover", 212: "Robotic arm stopped",
    213: "Low water level in robot\u2019s clean water box", 214: "Robot's used water box is full",
    215: "Mop Not Installed", 217: "Laser distance sensor error",
    218: "Check for any debris near the roller mop and mop cover", 222: "Fluffing Roller Error",
    223: "Check for any debris near the roller mop and mop cover",
    224: "Check for any debris near the roller mop and mop cover",
    225: "Check for any debris near the roller mop and mop cover", 226: "Robot blocked by obstacle",
    227: "The wastewater outlet filter of robot is clogged", 228: "Main wheels error",
    229: "Malfunction due to an internal error. Try to restart the robot",
    230: "Malfunction due to an internal error. Try to restart the robot", 1000: "Failed to return to charge",
}
# The codes that are only warnings, which it can carry on through.
WARNINGS = {9, 10, 20, 47, 51, 56, 68, 70, 71, 72, 75, 82, 85, 107, 114, 117, 121, 122, 123, 129, 213, 214}


class MovaError(Exception):
    """Something went wrong talking to MOVA."""


class SignInNeeded(MovaError):
    """The saved sign-in is gone or no longer good: sign in again."""


# --- AES ------------------------------------------------------------------
# AES-128, only to encrypt as the app does the header naming the account's region, language and country, which keeps
# nothing secret, as its key is the app's own; Python has no AES of its own. Checked against FIPS-197's example.

def _xtime(b: int) -> int:
    return ((b << 1) ^ 0x1B) & 0xFF if b & 0x80 else b << 1


def _sbox() -> bytes:
    """AES's S-box: each byte's inverse in GF(2^8), then the affine map, as FIPS-197 defines it."""
    box = [0x63] * 256
    p = q = 1
    while True:
        p ^= _xtime(p)  # times 3, through every nonzero byte
        q ^= q << 1  # divided by 3, so q is p's inverse
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        rotl = lambda x, n: ((x << n) | (x >> (8 - n))) & 0xFF
        box[p] = q ^ rotl(q, 1) ^ rotl(q, 2) ^ rotl(q, 3) ^ rotl(q, 4) ^ 0x63
        if p == 1:
            return bytes(box)


SBOX = _sbox()


def _round_keys(key: bytes) -> list[bytes]:
    words = [key[i:i + 4] for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        word = words[i - 1]
        if i % 4 == 0:
            word = bytes(SBOX[b] for b in word[1:] + word[:1])
            word = bytes([word[0] ^ rcon]) + word[1:]
            rcon = _xtime(rcon)
        words.append(bytes(a ^ b for a, b in zip(words[i - 4], word)))
    return [b"".join(words[i:i + 4]) for i in range(0, 44, 4)]


def _encrypt_block(keys: list[bytes], block: bytes) -> bytes:
    s = [b ^ k for b, k in zip(block, keys[0])]
    for n in range(1, 11):
        s = [SBOX[b] for b in s]
        s = [s[r + 4 * ((c + r) % 4)] for c in range(4) for r in range(4)]  # each row r shifts left by r
        if n < 10:
            mixed = []
            for c in range(0, 16, 4):
                a = s[c:c + 4]
                t = a[0] ^ a[1] ^ a[2] ^ a[3]
                mixed += [a[i] ^ t ^ _xtime(a[i] ^ a[(i + 1) % 4]) for i in range(4)]
            s = mixed
        s = [b ^ k for b, k in zip(s, keys[n])]
    return bytes(s)


def aes_ecb(key: bytes, data: bytes) -> bytes:
    """data, padded as PKCS #7 pads it, encrypted with AES-128 in ECB mode."""
    keys = _round_keys(key)
    pad = 16 - len(data) % 16
    data += bytes([pad]) * pad
    return b"".join(_encrypt_block(keys, data[i:i + 16]) for i in range(0, len(data), 16))


# --- Requests -------------------------------------------------------------

def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _spliced(params: dict, top: bool) -> str:
    """params as the app strings them together to sign them: by key, a dict's inside in brackets, and a list only at
    the top, as JSON."""
    parts = []
    for name in sorted(params):
        value = params[name]
        if isinstance(value, dict):
            inner = _spliced(value, False)
            parts.append(f"{name}=[{inner}]" if inner else f"{name}=]")
        elif isinstance(value, list):
            if top:
                parts.append(f"{name}={json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)}")
        elif isinstance(value, bool):
            parts.append(f"{name}={'true' if value else 'false'}")
        elif value is None:
            parts.append(f"{name}=null")
        elif top:
            parts.append(f"{name}={value}")
        else:
            parts.append(f"{name}={json.dumps(value, ensure_ascii=False)}")
    return "&".join(parts)


def signed(params: dict, ms: int) -> dict:
    """params with the app's signature and the time it signed them, in milliseconds."""
    return {**params, "sign": _md5(_spliced(params, True) + str(ms) + KEY.decode()), "timestamp": ms}


def post(url: str, headers: list[tuple[str, str]], body: bytes | None) -> tuple[int, bytes]:
    """POST body to url with exactly these headers, in this order, as the app sends them, and return the status and
    the body that comes back."""
    parts = urlsplit(url)
    kind = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = kind(parts.hostname, parts.port, timeout=TIMEOUT)
    try:
        conn.putrequest("POST", parts.path + (f"?{parts.query}" if parts.query else ""), skip_host=True, skip_accept_encoding=True)
        for name, value in headers:
            conn.putheader(name, value)
        conn.endheaders(body)
        response = conn.getresponse()
        content = response.read()
        if "gzip" in (response.getheader("content-encoding") or "").lower():
            content = gzip.decompress(content)
        return response.status, content
    finally:
        conn.close()


def api(region: str) -> str:
    return f"https://{region}.iot.mova-tech.com:{PORT}"


def _headers(url: str, saved: dict, content_type: str, body: bytes | None, access: str | None = None) -> list[tuple[str, str]]:
    """The app's headers, in its order: the host after the authorization, and the account's region, language and
    country, once signing in has named them, encrypted."""
    parts = urlsplit(url)
    headers = [("user-agent", USER_AGENT), ("dreame-meta", saved["meta"]), ("accept-encoding", "gzip")]
    if body is not None:
        headers.append(("content-length", str(len(body))))
    where = [saved.get(name) for name in ("region", "lang", "country")]
    if all(where):
        headers.append(("dreame-rlc", base64.b64encode(aes_ecb(KEY, "|".join(where).encode())).decode()))
    headers += [
        ("tenant-id", saved.get("tenant_id") or TENANT),
        ("authorization", f"Basic {CLIENT}"),
        ("host", parts.netloc if parts.port not in (None, 443) else parts.hostname),
        ("dreame-psd", "new"),
        ("content-type", content_type),
    ]
    if access:
        headers.append(("dreame-auth", access))
    return headers


def _token(url: str, saved: dict, form: str, refreshing: bool, send=post) -> dict:
    """saved with the tokens MOVA's token endpoint gives for form, a password or a refresh token, and what it says of
    the account: its region, language, country and tenant, which later requests name."""
    body = form.encode()
    status, content = send(url, _headers(url, saved, "application/x-www-form-urlencoded", body), body)
    try:
        answer = json.loads(content)
    except ValueError:
        answer = {}
    if status == 200 and "access_token" in answer:
        fresh = {**saved, "access_token": answer["access_token"], "expires_at": time.time() + float(answer.get("expires_in", 3600))}
        fresh["refresh_token"] = answer.get("refresh_token") or saved.get("refresh_token")  # kept unless MOVA gives a new one
        for name in ("region", "lang", "country", "tenant_id"):
            fresh[name] = answer.get(name) or saved.get(name)
        return fresh
    said = answer.get("error_description") or answer.get("msg") or answer.get("message") or ""
    if status in (400, 401, 403):
        if refreshing:
            raise SignInNeeded("MOVA no longer takes the saved sign-in")
        hint = " (if you sign in to MOVAhome with Apple or Google, set a password in the app first)"
        raise MovaError(f"MOVA didn't take that email and password{f': {said}' if said else ''}{hint}")
    raise MovaError(f"MOVA's sign-in answered {status}{f': {said}' if said else ''}")


def sign_in(email: str, password: str, region: str, send=post, base: str | None = None) -> dict:
    """Sign in to a MOVAhome account as its app does, and return what to save: the tokens, the cloud it's in, and the
    hashes the app sends to tell its installs apart, made from the email and at random, so the email isn't kept."""
    if region not in REGIONS:
        raise MovaError(f"MOVA's regions are {', '.join(REGIONS)}, not {region!r}")
    saved = {
        "cloud": region,
        "meta": f"cv=a_{APP_VERSION};canvasHash={_md5(email + 'c')[:8]};webglHash={_md5(email + 'w')[:8]};"
        f"visitorIdHash={hashlib.md5(secrets.token_bytes(16)).hexdigest()}",
    }
    form = f"grant_type=password&scope=all&platform=ANDROID&type=account&username={quote(email, safe='')}&password={_md5(password + SALT)}"
    return _token(f"{base or api(region)}/dreame-auth/oauth/token", saved, form, False, send)


def vacuum_name(device: dict) -> str:
    return device.get("customName") or (device.get("deviceInfo") or {}).get("displayName") or device.get("model", "Vacuum")


class Account:
    """A signed-in MOVAhome account: its tokens, kept in a file and refreshed as they expire, and its vacuums. base, if
    given, is the cloud to talk to in place of MOVA's, as in tests."""

    def __init__(self, path: Path, send=post, base: str | None = None):
        self.path = Path(path)
        self.send = send
        self.base = base
        self.saved: dict | None = None
        self.mtime = None
        self.id = secrets.randbelow(100) + 1  # each command's, counting up, as the app's do

    def save(self, saved: dict) -> None:
        """Keep the sign-in, readable only by its owner, replacing the file whole."""
        self.saved = saved
        self.path.parent.mkdir(parents=True, exist_ok=True)
        part = self.path.with_name(self.path.name + ".part")
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(saved, file)
        os.replace(part, self.path)
        self.mtime = self.path.stat().st_mtime

    def _load(self) -> dict:
        """The saved sign-in, read again whenever the file changes, as when signing in again while it runs."""
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            raise SignInNeeded(f"no MOVA sign-in at {self.path}") from None
        if self.saved is None or mtime != self.mtime:
            self.saved, self.mtime = json.loads(self.path.read_text()), mtime
        return self.saved

    def _url(self, path: str) -> str:
        return f"{self.base or api(self._load()['cloud'])}/{path}"

    def _refresh(self) -> dict:
        saved = self._load()
        if not saved.get("refresh_token"):
            raise SignInNeeded("the MOVA sign-in has no refresh token")
        form = f"grant_type=refresh_token&scope=all&platform=ANDROID&type=account&refresh_token={saved['refresh_token']}"
        fresh = _token(self._url("dreame-auth/oauth/token"), saved, form, True, self.send)
        self.save(fresh)
        return fresh

    def call(self, path: str, params: dict | None = None) -> dict:
        """What MOVA's cloud answers at path, posting params signed, or nothing, refreshing the access token first
        when it's about to expire, or when MOVA says it has."""
        saved = self._load()
        if saved.get("expires_at", 0) < time.time() + EXPIRES_SOON:
            saved = self._refresh()
        url = self._url(path)
        for attempt in (1, 2):
            body = None if params is None else json.dumps(signed(params, int(time.time() * 1000)), separators=(",", ":")).encode()
            status, content = self.send(url, _headers(url, saved, "application/json", body, saved["access_token"]), body)
            if status == 401 and attempt == 1:
                saved = self._refresh()
                continue
            if status == 401:
                raise SignInNeeded("MOVA no longer takes the saved sign-in")
            if status != 200:
                raise MovaError(f"MOVA's cloud answered {status}")
            try:
                return json.loads(content)
            except ValueError:
                raise MovaError("MOVA's cloud answered with something other than JSON") from None
        raise MovaError("MOVA kept saying the access token had expired")

    def vacuums(self) -> list[dict]:
        """The account's vacuums, as MOVA lists its devices."""
        answer = self.call("dreame-user-iot/iotuserbind/device/listV2")
        if answer.get("code") != 0:
            raise MovaError(f"MOVA didn't list the account's devices: {answer.get('msg') or answer.get('code')}")
        records = ((answer.get("data") or {}).get("page") or {}).get("records") or []
        return [device for device in records if ".vacuum." in str(device.get("model", ""))]

    def properties(self, device: dict, wanted: list[tuple[int, int]]) -> dict[tuple[int, int], object]:
        """The vacuum's properties, each (service, property) its value, asked of it through MOVA's cloud, as its app
        does; none when the cloud can't reach it."""
        did = str(device["did"])
        domain = str(device.get("bindDomain") or "").split(".")[0]  # the cloud's server for it
        for attempt in (1, 2):
            self.id += 1
            items = [{"did": str(i), "siid": siid, "piid": piid} for i, (siid, piid) in enumerate(wanted)]
            data = {"did": did, "id": self.id, "method": "get_properties", "params": items}
            answer = self.call(f"dreame-iot-com{f'-{domain}' if domain else ''}/device/sendCommand", {"did": did, "id": self.id, "data": data})
            result = (answer.get("data") or {}).get("result")
            if isinstance(result, list):
                return {(p.get("siid"), p.get("piid")): p["value"] for p in result if isinstance(p, dict) and p.get("code") == 0 and "value" in p}
            if answer.get("success") is not True:  # answered, but with nothing from the vacuum, it's asked once more
                break
        return {}

    def status(self, device: dict, now: float) -> Vacuum:
        """What the vacuum is doing, its battery, any error or warning, what's wrong at its dock, and its parts."""
        name, key = vacuum_name(device), f"mova-{device['did']}"
        values = {} if device.get("online") is False else self.properties(device, WANTED)
        if STATE not in values:
            return Vacuum(key, name, "Offline", None, now)
        state, battery, code = values[STATE], values.get(BATTERY), values.get(ERROR)
        label = STATES.get(state, f"State {state}") if isinstance(state, int) else "Unknown"
        problem = ERROR_TEXT.get(code, f"Error {code}") if isinstance(code, int) and code > 0 else None
        dock = tuple(DOCK[prop][values[prop]] for prop in DOCK if values.get(prop) in DOCK[prop])
        parts = tuple(
            VacuumPart(part, values[prop], replace) for prop, (part, replace) in PARTS.items() if isinstance(values.get(prop), int)
        )
        error = state in ERRORS or (problem is not None and code not in WARNINGS)
        return Vacuum(key, name, label, battery if isinstance(battery, int) else None, now, error, problem, dock, parts)


class MovaSource:
    """What each vacuum on a MOVAhome account is doing, polled every `interval` seconds, the account's vacuums listed
    again every hour, or after a poll fails, to take up one added, renamed or moved."""

    def __init__(self, account: Account, interval: float = POLL):
        self.account = account
        self.interval = interval
        self.devices: list[dict] | None = None
        self.listed = 0.0

    def poll(self, now: float) -> list[Vacuum]:
        if self.devices is None or now - self.listed >= DEVICES_EVERY:
            self.devices, self.listed = self.account.vacuums(), now
        return [self.account.status(device, now) for device in self.devices]

    def run(self, publish: Callable[[Vacuum], None], stop: threading.Event) -> None:
        """Call publish(vacuum) for each vacuum every poll until stop is set. A failed poll is reported and tried again
        next time, so a network outage or a lapsed sign-in doesn't stop the rest of the house."""
        while not stop.is_set():
            try:
                for vacuum in self.poll(time.time()):
                    publish(vacuum)
            except SignInNeeded as e:
                self.devices = None
                print(f"MOVA: {e}; sign in again with python3 -m housemon --mova-sign-in", file=sys.stderr, flush=True)
            except MovaError as e:
                self.devices = None
                print(f"MOVA: {e}", file=sys.stderr, flush=True)
            except (OSError, ValueError, KeyError, TypeError) as e:
                self.devices = None
                print(f"MOVA: couldn't get its vacuums: {e!r}", file=sys.stderr, flush=True)
            stop.wait(self.interval)


def sign_in_interactively(path: Path, ask: Callable[[str], str] = input, ask_secret: Callable[[str], str] | None = None, **kwargs) -> None:
    """Ask for the account's region, email and password, sign in, save the sign-in to path and list the vacuums found
    with what they're doing."""
    import getpass

    ask_secret = ask_secret or getpass.getpass
    region = ask(f"MOVAhome region ({', '.join(REGIONS)}) [us]: ").strip().lower() or "us"
    email = ask("MOVAhome email: ").strip()
    password = ask_secret("MOVAhome password (not saved): ")
    saved = sign_in(email, password, region, **kwargs)
    account = Account(path, **kwargs)
    account.save(saved)
    print(f"Signed in; saved to {path}, readable only by you. Its vacuums:")
    devices = account.vacuums()
    for device in devices:
        vacuum = account.status(device, time.time())
        battery = "" if vacuum.battery is None else f", {vacuum.battery}% battery"
        print(f"  {vacuum.name!r} ({device.get('model')}): {vacuum.state}{battery}")
        for note in ([vacuum.problem] if vacuum.problem else []) + list(vacuum.dock):
            print(f"    {note}")
        if vacuum.parts:
            print(f"    life left: {', '.join(f'{part.name} {part.left}%' for part in vacuum.parts)}")
    if not devices:
        print("  none")
    print("While the server runs, the page shows what each one is doing, its battery, any error, what's wrong at its")
    print("dock, and its parts running low.")
