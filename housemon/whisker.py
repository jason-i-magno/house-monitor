"""A Litter-Robot 3 Connect's state, from Whisker's cloud.

Whisker, which makes the Litter-Robot, has no public API, so this signs in as
its app does, with the account's email and password, as Home Assistant's
Litter-Robot integration does through pylitterbot. Whisker's accounts are kept
by Amazon Cognito, which checks a password by SRP, so the password itself
never leaves this computer, only a proof of it, and asks for a code from a
second factor if the account has one. It only reads. It keeps the refresh
token sign-in gives, in a file of its own (data/whisker.json unless told
otherwise), never the password, and polls the account's robots every two
minutes. A litter box in the house file names the robot that's it by the name
the Whisker app shows; its state is its status (ready, cleaning, its drawer
full, a fault), how many clean cycles it's run since its waste drawer was
emptied, and how full that drawer is, which the robot estimates from them.
"""

import base64
import datetime
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import OpenerDirector, Request, build_opener

from .polling import poll_gently
from .readings import LitterBox

# Whisker's app's, as pylitterbot has them
USER_POOL = "us-east-1_rjhNnZVAm"
CLIENT_ID = "4552ujeu3aic90nf8qn53levmn"
COGNITO = "https://cognito-idp.us-east-1.amazonaws.com/"
API = "https://v2.api.whisker.iothings.site"
API_KEY = "p7ndMoj61npRZP5CVz9v4Uj0bG769xy6758QRBPb"
TIMEOUT = 30.0  # seconds, for each request
POLL = 120.0  # seconds between polls: a clean cycle takes two or three, after a wait of 3 to 15
AGENT = "housemon"
# Cycles a Litter-Robot 3 is still sure it has left before its drawer is full, unless its status says fewer, as
# pylitterbot reckons them
LEFT = 3
LEFT_BY_STATUS = {"DF1": 2, "DF2": 1, "DFS": 0, "SDF": 0}

# Amazon Cognito's SRP group and generator (amazon-cognito-identity-js's AuthenticationHelper)
N_HEX = (
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AAAC42DAD33170D04507A33A85521ABDF1CBA64ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7"
    "ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6BF12FFA06D98A0864D87602733EC86A64521F2B18177B200C"
    "BBE117577A615D6C770988C0BAD946E208E24FA074E5AB3143DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF"
)
N = int(N_HEX, 16)
G = 2
# A code from each second factor Cognito can ask for, and how to ask for it
FACTORS = {
    "SOFTWARE_TOKEN_MFA": ("SOFTWARE_TOKEN_MFA_CODE", "an authenticator app"),
    "SMS_MFA": ("SMS_MFA_CODE", "a text message"),
    "EMAIL_OTP": ("EMAIL_OTP_CODE", "an email"),
}


class WhiskerError(Exception):
    """Something went wrong talking to Whisker."""


class SignInNeeded(WhiskerError):
    """The saved sign-in is gone or no longer good: sign in again."""


# --- Signing in, by SRP ----------------------------------------------------


def _pad(value: int | str) -> str:
    """A number, or hex, as hex of whole bytes with its top bit clear, as Cognito hashes it."""
    digits = value if isinstance(value, str) else f"{value:x}"
    if len(digits) % 2:
        return "0" + digits
    return "00" + digits if digits[0] in "89abcdefABCDEF" else digits


def _hash(hex_digits: str) -> str:
    return hashlib.sha256(bytes.fromhex(hex_digits)).hexdigest()


K = int(_hash(_pad(N) + _pad(G)), 16)


def _timestamp(now: datetime.datetime) -> str:
    """The time as Cognito wants it in a password claim, in English whatever the locale: "Tue Nov 3 07:05:09 UTC 2026"."""
    day = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[now.weekday()]
    month = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")[now.month - 1]
    return f"{day} {month} {now.day} {now:%H:%M:%S} UTC {now.year}"


def password_claim(a: int, params: dict, email: str, password: str, now: datetime.datetime, pool: str = USER_POOL) -> dict:
    """The answer to Cognito's PASSWORD_VERIFIER challenge for the account email signed in to, from the secret a
    whose g^a was sent as SRP_A: a signature, with the key only the password and a give, of the pool, the user, the
    challenge's secret block and the time."""
    big_a, big_b = pow(G, a, N), int(params["SRP_B"], 16)
    if big_b % N == 0:
        raise WhiskerError("Whisker's sign-in sent a bad challenge")
    u = int(_hash(_pad(big_a) + _pad(big_b)), 16)
    if u == 0:
        raise WhiskerError("Whisker's sign-in sent a bad challenge")
    pool_name, user = pool.split("_")[1], params["USER_ID_FOR_SRP"]
    x = int(_hash(_pad(params["SALT"]) + hashlib.sha256(f"{pool_name}{user}:{password}".encode()).hexdigest()), 16)
    s = pow(big_b - K * pow(G, x, N), a + u * x, N)
    prk = hmac.new(bytes.fromhex(_pad(u)), bytes.fromhex(_pad(s)), hashlib.sha256).digest()
    key = hmac.new(prk, b"Caldera Derived Key\x01", hashlib.sha256).digest()[:16]
    stamp = _timestamp(now)
    message = pool_name.encode() + user.encode() + base64.b64decode(params["SECRET_BLOCK"]) + stamp.encode()
    return {
        "TIMESTAMP": stamp,
        "USERNAME": params.get("USERNAME", email),
        "PASSWORD_CLAIM_SECRET_BLOCK": params["SECRET_BLOCK"],
        "PASSWORD_CLAIM_SIGNATURE": base64.b64encode(hmac.new(key, message, hashlib.sha256).digest()).decode(),
    }


class CognitoError(WhiskerError):
    """An error Cognito gave, by its kind: NotAuthorizedException for a wrong password or a lapsed refresh token."""

    def __init__(self, code: int, kind: str, message: str):
        super().__init__(f"Whisker's sign-in answered {code}{f' ({kind})' if kind else ''}")
        self.kind, self.message = kind, message


def _cognito(opener: OpenerDirector, action: str, body: dict) -> dict:
    """An answer from Cognito's identity provider, or a WhiskerError naming the kind of error it gave."""
    request = Request(
        COGNITO,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": f"AWSCognitoIdentityProviderService.{action}",
            "User-Agent": AGENT,
        },
    )
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            return json.load(response)
    except HTTPError as e:
        try:
            error = json.load(e)
        except ValueError:
            error = {}
        e.close()
        kind = str(error.get("__type", "")).rsplit("#", 1)[-1]
        raise CognitoError(e.code, kind, error.get("message", "")) from None


def _tokens(result: dict, refresh_token: str | None = None) -> dict:
    """The tokens Cognito gave, with when they expire, and the refresh token they came from if it gave no new one."""
    tokens = {
        "id_token": result["IdToken"],
        "access_token": result.get("AccessToken"),
        "refresh_token": result.get("RefreshToken", refresh_token),
        "expires_at": time.time() + float(result.get("ExpiresIn", 3600)),
    }
    if not tokens["refresh_token"]:
        raise WhiskerError("Whisker gave no refresh token")
    return tokens


def sign_in(email: str, password: str, code_for: Callable[[str], str], opener: OpenerDirector | None = None) -> dict:
    """Sign in to a Whisker account, as its app does, and return the tokens: id_token, access_token, refresh_token
    and expires_at. code_for(kind) asks for a code from the account's second factor, "an authenticator app", "a text
    message" or "an email", when it has one."""
    opener = opener or build_opener()
    a = secrets.randbits(1024) % N
    try:
        answer = _cognito(opener, "InitiateAuth", {
            "AuthFlow": "USER_SRP_AUTH", "ClientId": CLIENT_ID, "AuthParameters": {"USERNAME": email, "SRP_A": f"{pow(G, a, N):x}"},
        })
        if answer.get("ChallengeName") != "PASSWORD_VERIFIER":
            raise WhiskerError(f"Whisker's sign-in asked for {answer.get('ChallengeName') or 'nothing'}, not a password")
        params = answer["ChallengeParameters"]
        claim = password_claim(a, params, email, password, datetime.datetime.now(datetime.timezone.utc))
        step = {"ChallengeName": "PASSWORD_VERIFIER", "ClientId": CLIENT_ID, "ChallengeResponses": claim}
        if answer.get("Session"):
            step["Session"] = answer["Session"]
        answer = _cognito(opener, "RespondToAuthChallenge", step)
    except CognitoError as e:
        if e.kind in ("NotAuthorizedException", "UserNotFoundException"):
            raise WhiskerError("Whisker didn't take that email and password") from None
        raise
    challenge = answer.get("ChallengeName")
    if challenge in FACTORS:
        field, kind = FACTORS[challenge]
        code = code_for(kind).strip()
        try:
            answer = _cognito(opener, "RespondToAuthChallenge", {
                "ChallengeName": challenge, "ClientId": CLIENT_ID, "Session": answer.get("Session"),
                "ChallengeResponses": {"USERNAME": claim["USERNAME"], field: code},
            })
        except CognitoError as e:
            if e.kind in ("CodeMismatchException", "ExpiredCodeException", "NotAuthorizedException"):
                raise WhiskerError("Whisker didn't take that code") from None
            raise
        challenge = answer.get("ChallengeName")
    if challenge == "NEW_PASSWORD_REQUIRED":
        raise WhiskerError("Whisker wants a new password first: set one in its app, then try again")
    if challenge:
        raise WhiskerError(f"Whisker's sign-in asks for {challenge}, which this can't give")
    if "AuthenticationResult" not in answer:
        raise WhiskerError("Whisker's sign-in ended somewhere unexpected")
    return _tokens(answer["AuthenticationResult"])


def user_id(id_token: str) -> str:
    """The Whisker user an id token is for, its robots under it in the API: the token's "mid" claim. Its signature
    goes unchecked, since it came straight from Cognito."""
    try:
        payload = id_token.split(".")[1]
        return str(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["mid"])
    except (IndexError, KeyError, ValueError) as e:
        raise WhiskerError("Whisker's sign-in gave a token with no user in it") from e


# --- The account and its robots -------------------------------------------


class Account:
    """A signed-in Whisker account: its tokens, kept in a file, refreshed as they expire."""

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
            raise SignInNeeded(f"no Whisker sign-in at {self.path}") from None
        if self.tokens is None or mtime != self.mtime:
            self.tokens, self.mtime = json.loads(self.path.read_text()), mtime
        return self.tokens

    def _refresh(self) -> dict:
        tokens = self._load()
        if not tokens.get("refresh_token"):
            raise SignInNeeded("the Whisker sign-in has no refresh token")
        try:
            answer = _cognito(self.opener, "InitiateAuth", {
                "AuthFlow": "REFRESH_TOKEN_AUTH", "ClientId": CLIENT_ID, "AuthParameters": {"REFRESH_TOKEN": tokens["refresh_token"]},
            })
        except CognitoError as e:
            if e.kind == "NotAuthorizedException":
                raise SignInNeeded("Whisker no longer takes the saved sign-in") from None
            raise
        if "AuthenticationResult" not in answer:
            raise WhiskerError("Whisker gave no new token")
        fresh = _tokens(answer["AuthenticationResult"], tokens["refresh_token"])  # kept unless Whisker gives a new one
        self.save(fresh)
        return fresh

    def robots(self) -> list[dict]:
        """The account's Litter-Robot 3s, as Whisker's API gives them, refreshing the id token first when it's expired
        or about to, or when Whisker says it has."""
        tokens = self._load()
        if not tokens.get("id_token") or tokens.get("expires_at", 0) < time.time() + 60:
            tokens = self._refresh()
        for attempt in (1, 2):
            request = Request(
                f"{API}/users/{user_id(tokens['id_token'])}/robots",
                headers={"Authorization": f"Bearer {tokens['id_token']}", "x-api-key": API_KEY, "User-Agent": AGENT},
            )
            try:
                with self.opener.open(request, timeout=TIMEOUT) as response:
                    robots = json.load(response)
            except HTTPError as e:
                e.close()
                if e.code in (401, 403) and attempt == 1:
                    tokens = self._refresh()
                    continue
                if e.code in (401, 403):
                    raise SignInNeeded("Whisker no longer takes the saved sign-in") from None
                raise WhiskerError(f"Whisker's robots endpoint answered {e.code}") from None
            # one with no serial isn't set up, as pylitterbot leaves those out
            return [r for r in robots if isinstance(r, dict) and r.get("litterRobotSerial")] if isinstance(robots, list) else []
        raise WhiskerError("Whisker kept saying the token had expired")


def _count(value, default: int = 0) -> int:
    try:
        return int(str(value))
    except ValueError:
        return default


def _seen(value) -> float | None:
    """When Whisker last heard from a robot, as Unix time: its lastSeen, in UTC with or without a zone."""
    if not value:
        return None
    try:
        seen = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (seen if seen.tzinfo else seen.replace(tzinfo=datetime.timezone.utc)).timestamp()


class WhiskerSource:
    """The litter boxes Litter-Robots are, polled every `interval` seconds. boxes() maps each robot's name, as the
    Whisker app shows it, to the litter box in the house file it is; it's looked up each poll, so a changed house
    file takes effect, and a name matches whatever its case. A name the account has no robot by is warned about,
    with the names it has, once until it's matched again."""

    def __init__(self, account: Account, boxes: Callable[[], dict[str, str]], interval: float = POLL, warn: Callable[[str], None] | None = None):
        self.account = account
        self.boxes = boxes
        self.interval = interval
        self.warn = warn or (lambda message: print(f"whisker: {message}", file=sys.stderr, flush=True))
        self.unmatched: set[str] = set()  # the names warned about, casefolded
        self.left: dict[str, int] = {}  # by robot id: the cycles each is sure it has left, as its statuses have said

    def state(self, robot: dict, name: str, now: float) -> LitterBox:
        """A robot's state as the litter box `name`. Its drawer is as full as its cycles are of the capacity it
        estimates, or fuller once its status says it has only a few cycles left, until it's next ready, as the
        Whisker app has it."""
        online = robot.get("powerStatus", "NC") != "NC" and robot.get("unitStatus") != "OFFLINE"
        status = str(robot.get("unitStatus") or "") if online else "OFFLINE"
        cycles, capacity = _count(robot.get("cycleCount")), _count(robot.get("cycleCapacity"), 30)
        key = str(robot.get("litterRobotId", name))
        least = LEFT_BY_STATUS.get(status, LEFT)
        left = self.left.get(key, LEFT)
        if status == "RDY" or least < left:
            left = least
        self.left[key] = left
        capacity = cycles + left if left < LEFT else max(capacity, cycles + left)
        drawer = 100.0 if capacity <= 0 else round(min(cycles / capacity, 1) * 100, 1)
        return LitterBox(name, now, status, cycles, drawer, seen=_seen(robot.get("lastSeen")))

    def poll(self, now: float) -> list[LitterBox]:
        names = self.boxes()
        if not names:
            return []
        robots = self.account.robots()
        boxes = {name.casefold(): box for name, box in names.items()}
        states = [
            self.state(r, boxes[str(r.get("litterRobotNickname", "")).casefold()], now)
            for r in robots
            if str(r.get("litterRobotNickname", "")).casefold() in boxes
        ]
        has = [str(r.get("litterRobotNickname", "")) for r in robots]
        known = {name.casefold() for name in has}
        missing = sorted((name for name in names if name.casefold() not in known), key=str.casefold)
        new = [name for name in missing if name.casefold() not in self.unmatched]
        if new:
            listed = ", ".join(repr(name) for name in has) or "none"
            self.warn(f"no Litter-Robot named {', '.join(repr(name) for name in new)}, as the house file has; the account has {listed}")
        self.unmatched = {name.casefold() for name in missing}
        return states

    def run(self, publish: Callable[[LitterBox], None], stop: threading.Event) -> None:
        """Call publish(state) for each litter box every poll until stop is set, gently (polling.py): backing off
        while polls fail, as in a network outage, and waiting while the sign-in has lapsed until it's signed in again,
        so neither stops the rest of the house."""
        poll_gently(
            self.poll, publish, stop, self.interval,
            sign_in=self.account.path, lapsed=SignInNeeded, failed=(WhiskerError, URLError, OSError, ValueError),
            sign_in_again="python3 -m housemon --whisker-sign-in",
            log=lambda message: print(f"whisker: {message}", file=sys.stderr, flush=True),
            describe=lambda e: str(e) if isinstance(e, WhiskerError) else f"couldn't get its robots: {e}",
        )


def sign_in_interactively(path: Path, ask: Callable[[str], str] = input, ask_secret: Callable[[str], str] | None = None) -> None:
    """Ask for the account's email, password and any code, sign in, save the tokens to path and list the robots
    found, for naming them in the house file."""
    import getpass

    ask_secret = ask_secret or getpass.getpass
    email = ask("Whisker email: ").strip()
    password = ask_secret("Whisker password (not saved): ")
    tokens = sign_in(email, password, lambda kind: ask(f"Code from {kind}: "))
    account = Account(path)
    account.save(tokens)
    print(f"Signed in; saved to {path}, readable only by you. Its Litter-Robots:")
    robots = account.robots()
    source = WhiskerSource(account, dict)
    for robot in robots:
        name = str(robot.get("litterRobotNickname", ""))
        state = source.state(robot, name, time.time()).to_json()
        print(f"  {name!r}: {state['text']}, its drawer {state['drawer']:.0f}% full after {state['cycles']} cycles")
    if not robots:
        print("  none")
    print('Give a litter box in the house file whisker="<robot>" for its state to come from that Litter-Robot.')
