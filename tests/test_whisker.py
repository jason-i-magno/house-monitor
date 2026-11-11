import base64
import datetime
import hashlib
import hmac
import io
import json
import secrets
import stat
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError

from housemon import whisker
from housemon.readings import LitterBox

EMAIL, PASSWORD, USER = "cat@example.com", "litter", "4f1c-user"


def id_token(user: str = "1234567", expires: float | None = None) -> str:
    """An id token as Cognito gives one, with the Whisker user in its "mid" claim; its signature isn't checked."""
    part = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'RS256'})}.{part({'mid': user, 'exp': expires or time.time() + 3600})}.c2ln"


def robot(name: str = "Litter-Robot", **data) -> dict:
    """A Litter-Robot 3 as Whisker's API gives one, its numbers as strings."""
    return {
        "litterRobotId": "a0123b4567cd8e", "litterRobotSerial": "LR3C012345", "litterRobotNickname": name,
        "unitStatus": "RDY", "powerStatus": "AC", "cycleCount": "15", "cycleCapacity": "30", "cyclesAfterDrawerFull": "0",
        "lastSeen": "2026-11-09T15:30:00.000000", "isDFITriggered": "0", "nightLightActive": "1", **data,
    }


class FakeWhisker:
    """Answers as Cognito's identity provider, checking the password by the server's half of SRP, and as Whisker's
    robots endpoint. mfa, if set, is the second factor Cognito asks a code from after the password."""

    def __init__(self, mfa: str | None = None, robots: list[dict] | None = None):
        self.mfa = mfa
        self.robots = [robot()] if robots is None else robots
        self.calls: list[tuple[str, dict, dict]] = []  # (action or URL, body, headers)
        self.refresh_token = "R1"
        self.id_token = id_token()
        self.expired: set[str] = set()  # id tokens the robots endpoint no longer takes
        self.refresh_fails = False
        # What Cognito keeps of the password: its salt, and the verifier g^x, x from the salt and the password.
        self.salt = secrets.token_hex(16)
        pool = whisker.USER_POOL.split("_")[1]
        inner = hashlib.sha256(f"{pool}{USER}:{PASSWORD}".encode()).hexdigest()
        self.verifier = pow(whisker.G, int(whisker._hash(whisker._pad(self.salt) + inner), 16), whisker.N)

    def open(self, request, timeout=None):
        headers = {k.lower(): v for k, v in request.header_items()}
        body = json.loads(request.data) if request.data else {}
        if request.full_url == whisker.COGNITO:
            action = headers["x-amz-target"].rsplit(".", 1)[1]
            self.calls.append((action, body, headers))
            return self._json(getattr(self, action)(body))
        self.calls.append((request.full_url, body, headers))
        token = headers.get("authorization", "").removeprefix("Bearer ")
        if headers.get("x-api-key") != whisker.API_KEY or token in self.expired or token != self.id_token:
            raise HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))
        if request.full_url != f"{whisker.API}/users/1234567/robots":
            raise HTTPError(request.full_url, 404, "Not found", {}, io.BytesIO(b"{}"))
        return self._json(self.robots)

    @staticmethod
    def _json(value):
        return io.BytesIO(json.dumps(value).encode())

    @staticmethod
    def _error(kind: str):
        raise HTTPError(whisker.COGNITO, 400, "Bad request", {}, io.BytesIO(json.dumps({"__type": kind, "message": kind}).encode()))

    def _tokens(self, refresh: bool = True) -> dict:
        self.id_token = id_token()
        result = {"IdToken": self.id_token, "AccessToken": "A", "ExpiresIn": 3600, "TokenType": "Bearer"}
        return {"AuthenticationResult": {**result, "RefreshToken": self.refresh_token} if refresh else result}

    def InitiateAuth(self, body):
        if body["AuthFlow"] == "REFRESH_TOKEN_AUTH":
            if self.refresh_fails or body["AuthParameters"]["REFRESH_TOKEN"] != self.refresh_token:
                self._error("NotAuthorizedException")
            return self._tokens(refresh=False)  # Cognito keeps the refresh token
        self.big_a = int(body["AuthParameters"]["SRP_A"], 16)
        if body["AuthParameters"]["USERNAME"] != EMAIL:
            self._error("UserNotFoundException")
        self.b = secrets.randbits(1024)
        self.big_b = (whisker.K * self.verifier + pow(whisker.G, self.b, whisker.N)) % whisker.N
        self.block = base64.b64encode(secrets.token_bytes(64)).decode()
        return {"ChallengeName": "PASSWORD_VERIFIER", "ChallengeParameters": {
            "SALT": self.salt, "SRP_B": f"{self.big_b:x}", "SECRET_BLOCK": self.block, "USER_ID_FOR_SRP": USER, "USERNAME": USER,
        }}

    def RespondToAuthChallenge(self, body):
        answer = body["ChallengeResponses"]
        if body["ChallengeName"] == "PASSWORD_VERIFIER":
            u = int(whisker._hash(whisker._pad(self.big_a) + whisker._pad(self.big_b)), 16)
            s = pow(self.big_a * pow(self.verifier, u, whisker.N), self.b, whisker.N)  # the server's side of the key
            prk = hmac.new(bytes.fromhex(whisker._pad(u)), bytes.fromhex(whisker._pad(s)), hashlib.sha256).digest()
            key = hmac.new(prk, b"Caldera Derived Key\x01", hashlib.sha256).digest()[:16]
            message = b"rjhNnZVAm" + USER.encode() + base64.b64decode(self.block) + answer["TIMESTAMP"].encode()
            signature = base64.b64encode(hmac.new(key, message, hashlib.sha256).digest()).decode()
            if answer["PASSWORD_CLAIM_SIGNATURE"] != signature or answer["USERNAME"] != USER:
                self._error("NotAuthorizedException")
            if self.mfa:
                return {"ChallengeName": self.mfa, "Session": "S1", "ChallengeParameters": {}}
            return self._tokens()
        field = whisker.FACTORS[body["ChallengeName"]][0]
        if body["Session"] != "S1" or answer[field] != "123456":
            self._error("CodeMismatchException")
        return self._tokens()


class SignInTest(unittest.TestCase):
    def test_signs_in_with_a_proof_of_the_password_not_the_password(self):
        fake = FakeWhisker()
        tokens = whisker.sign_in(EMAIL, PASSWORD, lambda kind: self.fail("no second factor"), fake)
        self.assertEqual(tokens["refresh_token"], "R1")
        self.assertEqual(whisker.user_id(tokens["id_token"]), "1234567")
        self.assertGreater(tokens["expires_at"], time.time() + 3000)
        self.assertEqual([c[0] for c in fake.calls], ["InitiateAuth", "RespondToAuthChallenge"])
        self.assertEqual(fake.calls[0][1]["AuthFlow"], "USER_SRP_AUTH")
        self.assertEqual(fake.calls[0][1]["ClientId"], whisker.CLIENT_ID)
        self.assertFalse(any(PASSWORD in json.dumps(c[1]) for c in fake.calls))

    def test_asks_for_the_code_from_the_second_factor(self):
        for challenge, kind in (("SOFTWARE_TOKEN_MFA", "an authenticator app"), ("SMS_MFA", "a text message"), ("EMAIL_OTP", "an email")):
            asked = []
            tokens = whisker.sign_in(EMAIL, PASSWORD, lambda k: asked.append(k) or " 123456 ", FakeWhisker(mfa=challenge))
            self.assertEqual((asked, tokens["refresh_token"]), ([kind], "R1"))
        with self.assertRaisesRegex(whisker.WhiskerError, "didn't take that code"):
            whisker.sign_in(EMAIL, PASSWORD, lambda kind: "000000", FakeWhisker(mfa="SMS_MFA"))

    def test_says_what_it_wouldnt_take(self):
        with self.assertRaisesRegex(whisker.WhiskerError, "didn't take that email and password"):
            whisker.sign_in(EMAIL, "wrong", str, FakeWhisker())
        with self.assertRaisesRegex(whisker.WhiskerError, "didn't take that email and password"):
            whisker.sign_in("dog@example.com", PASSWORD, str, FakeWhisker())

    def test_dates_its_claim_as_cognito_wants(self):
        self.assertEqual(whisker._timestamp(datetime.datetime(2026, 11, 3, 7, 5, 9)), "Tue Nov 3 07:05:09 UTC 2026")
        self.assertEqual(whisker._timestamp(datetime.datetime(2026, 12, 20, 23, 0, 0)), "Sun Dec 20 23:00:00 UTC 2026")

    def test_pads_hex_as_cognito_hashes_it(self):
        self.assertEqual(whisker._pad(0x7F), "7f")
        self.assertEqual(whisker._pad(0x80), "0080")  # its top bit clear
        self.assertEqual(whisker._pad(0xABC), "0abc")
        self.assertEqual(whisker._pad("ABCD"), "00ABCD")


class AccountTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "data" / "whisker.json"
        self.fake = FakeWhisker()
        self.account = whisker.Account(self.path, self.fake)

    def tearDown(self):
        self.dir.cleanup()

    def test_keeps_its_tokens_readable_only_by_their_owner(self):
        self.account.save({"id_token": self.fake.id_token, "refresh_token": "R1", "expires_at": time.time() + 3600})
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(self.path.read_text())["refresh_token"], "R1")

    def test_lists_its_robots_but_one_not_set_up(self):
        self.fake.robots = [robot(), robot("New", litterRobotSerial=None)]
        self.account.save({"id_token": self.fake.id_token, "refresh_token": "R1", "expires_at": time.time() + 3600})
        self.assertEqual([r["litterRobotNickname"] for r in self.account.robots()], ["Litter-Robot"])
        url, _, headers = self.fake.calls[-1]
        self.assertEqual(url, f"{whisker.API}/users/1234567/robots")
        self.assertEqual(headers["authorization"], f"Bearer {self.fake.id_token}")

    def test_refreshes_an_expired_token_and_keeps_the_refresh_token(self):
        self.account.save({"id_token": "old", "refresh_token": "R1", "expires_at": time.time() - 10})
        self.assertEqual(len(self.account.robots()), 1)
        saved = json.loads(self.path.read_text())
        self.assertEqual((saved["id_token"], saved["refresh_token"]), (self.fake.id_token, "R1"))
        self.assertEqual(self.fake.calls[0][1]["AuthFlow"], "REFRESH_TOKEN_AUTH")

    def test_refreshes_when_whisker_says_the_token_has_expired(self):
        self.account.save({"id_token": self.fake.id_token, "refresh_token": "R1", "expires_at": time.time() + 3600})
        self.fake.expired.add(self.fake.id_token)
        self.assertEqual(len(self.account.robots()), 1)
        self.assertEqual([c[0] for c in self.fake.calls][1:], ["InitiateAuth", f"{whisker.API}/users/1234567/robots"])

    def test_a_lapsed_sign_in_says_to_sign_in_again(self):
        self.account.save({"id_token": "old", "refresh_token": "R1", "expires_at": time.time() - 10})
        self.fake.refresh_fails = True
        with self.assertRaises(whisker.SignInNeeded):
            self.account.robots()
        with self.assertRaises(whisker.SignInNeeded):
            whisker.Account(Path(self.dir.name) / "none.json", self.fake).robots()


class WhiskerSourceTest(unittest.TestCase):
    def source(self, robots: list[dict], boxes: dict[str, str] | None = None, warned: list | None = None):
        fake = FakeWhisker(robots=robots)
        account = whisker.Account(Path(tempfile.mkdtemp()) / "whisker.json", fake)
        account.save({"id_token": fake.id_token, "refresh_token": "R1", "expires_at": time.time() + 3600})
        source = whisker.WhiskerSource(account, lambda: boxes or {"litter-robot": "Litter box"}, warn=(warned if warned is not None else []).append)
        return fake, source

    def test_reads_the_litter_boxes_named_for_its_robots(self):
        _, source = self.source([robot(), robot("Other", litterRobotId="b1")])
        [state] = source.poll(1000.0)
        seen = datetime.datetime(2026, 11, 9, 15, 30, tzinfo=datetime.timezone.utc).timestamp()
        self.assertEqual(state, LitterBox("Litter box", 1000.0, "RDY", 15, 50.0, seen=seen))
        self.assertEqual(state.to_json()["text"], "Ready")
        self.assertFalse(state.to_json()["attention"])

    def test_a_drawer_fills_when_its_status_says_only_a_few_cycles_are_left(self):
        fake, source = self.source([robot(unitStatus="DF1", cycleCount="20")])
        state = source.poll(1000.0)[0]
        self.assertEqual((state.status, state.drawer), ("DF1", 90.9))  # 20 of 22
        self.assertTrue(state.to_json()["attention"])
        fake.robots = [robot(unitStatus="CST", cycleCount="20")]  # a cat's been in, and it's still nearly full
        self.assertEqual(source.poll(1100.0)[0].drawer, 90.9)
        fake.robots = [robot(unitStatus="RDY", cycleCount="0")]  # emptied
        self.assertEqual(source.poll(1200.0)[0].drawer, 0.0)
        fake.robots = [robot(unitStatus="DFS", cycleCount="31")]
        self.assertEqual(source.poll(1300.0)[0].drawer, 100.0)

    def test_a_robot_without_power_or_its_connection_is_offline(self):
        for data in ({"powerStatus": "NC"}, {"unitStatus": "OFFLINE"}):
            _, source = self.source([robot(**data)])
            state = source.poll(1000.0)[0]
            self.assertEqual((state.status, state.to_json()["text"], state.to_json()["attention"]), ("OFFLINE", "Offline", True))

    def test_warns_once_of_a_name_the_account_has_no_robot_by(self):
        warned = []
        fake, source = self.source([robot()], {"Litter-Robot": "Litter box", "Upstairs robot": "Upstairs box"}, warned)
        self.assertEqual(len(source.poll(1000.0)), 1)
        source.poll(1100.0)
        self.assertEqual(warned, ["no Litter-Robot named 'Upstairs robot', as the house file has; the account has 'Litter-Robot'"])
        fake.robots = [robot(), robot("Upstairs robot", litterRobotId="b1")]
        self.assertEqual(len(source.poll(1200.0)), 2)
        fake.robots = [robot()]
        source.poll(1300.0)
        self.assertEqual(len(warned), 2)  # gone again, so warned again

    def test_an_unknown_status_says_so_and_needs_seeing_to(self):
        state = LitterBox("Litter box", 1000.0, "XYZ", 3, 10.0).to_json()
        self.assertEqual((state["text"], state["attention"]), ("Unknown status XYZ", True))


if __name__ == "__main__":
    unittest.main()
