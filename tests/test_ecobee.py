import base64
import hashlib
import io
import json
import stat
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, parse_qsl, urljoin, urlparse
from urllib.request import Request

from housemon import ecobee
from housemon.readings import Reading

CALLBACK = "https://www.ecobee.com/home/authCallback?code=C0DE"


class Response(io.BytesIO):
    def __init__(self, url: str, body: bytes):
        super().__init__(body)
        self.url = url

    def geturl(self) -> str:
        return self.url


class FakeEcobee:
    """Answers requests as ecobee's sign-in site and API do, from route(method, url, form), which returns ("page",),
    ("redirect", location), ("error", code, body) or ("json", body). An opener of it follows redirects or not."""

    def __init__(self, route):
        self.route = route
        self.calls = []

    def opener(self, follow: bool):
        fake = self

        class Opener:
            def open(self, request, timeout=None):
                request = Request(request) if isinstance(request, str) else request
                url, data = request.full_url, request.data
                form = dict(parse_qsl(data.decode())) if data else None
                fake.calls.append(("POST" if data else "GET", url, form, dict(request.header_items())))
                answer = fake.route("POST" if data else "GET", url, form)
                while answer[0] == "redirect" and follow:
                    url = urljoin(url, answer[1])
                    fake.calls.append(("GET", url, None, {}))
                    answer = fake.route("GET", url, None)
                if answer[0] == "redirect":
                    raise HTTPError(url, 302, "Found", {"Location": answer[1]}, io.BytesIO(b""))
                if answer[0] == "error":
                    raise HTTPError(url, answer[1], "Error", {}, io.BytesIO(json.dumps(answer[2]).encode()))
                return Response(url, json.dumps(answer[1]).encode() if answer[0] == "json" else b"<html></html>")

        return Opener()

    def openers(self):
        return self.opener(True), self.opener(False)


def sign_in_route(mfa: str | None = None, password: str = "secret", code: str = "123456"):
    """ecobee's sign-in: the identifier page, the password page, and a challenge for the second factor if `mfa`."""
    seen = {}

    def route(method, url, form):
        path = urlparse(url).path
        if path == "/authorize":
            seen["challenge"] = parse_qs(urlparse(url).query)["code_challenge"][0]
            return ("redirect", "/u/login/identifier?state=S1")
        if path == "/u/login/identifier":
            return ("page",) if method == "GET" else ("redirect", "/u/login/password?state=S1")
        if path == "/u/login/password":
            if method == "GET":
                return ("page",)
            if form["password"] != password:
                return ("error", 400, {})
            return ("redirect", f"/u/mfa-{mfa}-challenge?state=S2") if mfa else ("redirect", "/authorize/resume?state=S1")
        if path.startswith("/u/mfa-"):
            if method == "GET":
                return ("page",)
            return ("redirect", "/authorize/resume?state=S2") if form["code"] == code and form["state"] == "S2" else ("error", 400, {})
        if path == "/authorize/resume":
            return ("redirect", CALLBACK)
        if path == "/oauth/token":
            verifier = form["code_verifier"]
            expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if form["code"] != "C0DE" or expected != seen["challenge"]:
                return ("error", 403, {"error": "invalid_grant"})
            return ("json", {"access_token": "A1", "refresh_token": "R1", "expires_in": 3600})
        raise AssertionError(f"unexpected {method} {url}")

    return route


THERMOSTATS = [
    {
        "name": "Hallway",
        "runtime": {"connected": True},
        "remoteSensors": [
            {"name": "Hallway", "type": "ecobee3", "capability": [{"type": "temperature", "value": "702"}, {"type": "humidity", "value": "41"}, {"type": "occupancy", "value": "true"}]},
            {"name": "Dining Room", "type": "ecobee3_remote_sensor", "capability": [{"type": "temperature", "value": "687"}, {"type": "occupancy", "value": "false"}]},
            {"name": "Bedroom", "type": "ecobee3_remote_sensor", "capability": [{"type": "temperature", "value": "unknown"}]},
            {"name": "Den", "type": "ecobee3_remote_sensor", "capability": [{"type": "temperature", "value": -5002}]},
        ],
    },
    {"name": "Offline", "runtime": {"connected": False}, "remoteSensors": [{"name": "Garage", "capability": [{"type": "temperature", "value": "500"}]}]},
]


class SignInTest(unittest.TestCase):
    def test_signs_in_with_an_email_and_password(self):
        fake = FakeEcobee(sign_in_route())
        tokens = ecobee.sign_in("me@example.com", "secret", lambda kind: self.fail("no second factor"), fake.openers)
        self.assertEqual((tokens["access_token"], tokens["refresh_token"]), ("A1", "R1"))
        # The code is read from where the sign-in sends the browser, which isn't fetched.
        self.assertNotIn(CALLBACK, [url for _, url, _, _ in fake.calls])
        authorize = parse_qs(urlparse(fake.calls[0][1]).query)
        self.assertEqual(authorize["client_id"], [ecobee.CLIENT_ID])
        self.assertEqual(authorize["scope"], ["openid offline_access smartRead"])  # it only reads

    def test_asks_for_the_code_from_the_second_factor(self):
        for mfa, kind in (("otp", "an authenticator app"), ("sms", "a text message")):
            asked = []
            tokens = ecobee.sign_in("me@example.com", "secret", lambda k: asked.append(k) or " 123456 ", FakeEcobee(sign_in_route(mfa)).openers)
            self.assertEqual(asked, [kind])
            self.assertEqual(tokens["refresh_token"], "R1")

    def test_says_what_it_wouldnt_take(self):
        with self.assertRaisesRegex(ecobee.EcobeeError, "password"):
            ecobee.sign_in("me@example.com", "wrong", lambda kind: "", FakeEcobee(sign_in_route()).openers)
        with self.assertRaisesRegex(ecobee.EcobeeError, "code"):
            ecobee.sign_in("me@example.com", "secret", lambda kind: "000000", FakeEcobee(sign_in_route("otp")).openers)


class SensorReadingsTest(unittest.TestCase):
    def test_reads_each_sensor_in_celsius(self):
        readings = list(ecobee.sensor_readings(THERMOSTATS))
        self.assertEqual(readings, [
            ecobee.SensorReading("Hallway", "Hallway", 21.22, 41.0),  # 70.2 °F
            ecobee.SensorReading("Dining Room", "Hallway", 20.39, None),  # a SmartSensor reads no humidity
        ])  # but not one with no temperature yet, nor an offline thermostat's


class AccountTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "ecobee.json"

    def tearDown(self):
        self.dir.cleanup()

    def account(self, route, tokens):
        self.path.write_text(json.dumps(tokens))
        self.fake = FakeEcobee(route)
        return ecobee.Account(self.path, self.fake.opener(True))

    def test_refreshes_an_expired_token_and_keeps_the_new_one(self):
        def route(method, url, form):
            if url.endswith("/oauth/token"):
                self.assertEqual((form["grant_type"], form["refresh_token"]), ("refresh_token", "R1"))
                return ("json", {"access_token": "A2", "refresh_token": "R2", "expires_in": 3600})
            return ("json", {"thermostatList": THERMOSTATS})

        account = self.account(route, {"access_token": "A1", "refresh_token": "R1", "expires_at": time.time() - 10})
        self.assertEqual(len(account.thermostats()), 2)
        self.assertEqual(self.fake.calls[-1][3]["Authorization"], "Bearer A2")
        saved = json.loads(self.path.read_text())
        self.assertEqual((saved["access_token"], saved["refresh_token"]), ("A2", "R2"))
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)  # readable only by its owner
        selection = json.loads(parse_qs(urlparse(self.fake.calls[-1][1]).query)["json"][0])["selection"]
        self.assertTrue(selection["includeSensors"])

    def test_refreshes_when_ecobee_says_the_token_expired(self):
        answers = iter([("error", 500, {"status": {"code": 14}}), ("json", {"thermostatList": THERMOSTATS})])

        def route(method, url, form):
            if url.endswith("/oauth/token"):
                return ("json", {"access_token": "A2", "expires_in": 3600})
            return next(answers)

        account = self.account(route, {"access_token": "A1", "refresh_token": "R1", "expires_at": time.time() + 3600})
        self.assertEqual(len(account.thermostats()), 2)
        self.assertEqual(json.loads(self.path.read_text())["refresh_token"], "R1")  # kept, as none came back

    def test_a_lapsed_sign_in_says_to_sign_in_again(self):
        account = self.account(lambda method, url, form: ("error", 400, {"error": "invalid_grant"}), {"refresh_token": "R1"})
        with self.assertRaises(ecobee.SignInNeeded):
            account.thermostats()
        with self.assertRaises(ecobee.SignInNeeded):
            ecobee.Account(Path(self.dir.name) / "none.json").thermostats()


class EcobeeSourceTest(unittest.TestCase):
    def test_reads_the_rooms_named_for_its_sensors(self):
        account = unittest.mock.Mock(thermostats=lambda: THERMOSTATS)
        source = ecobee.EcobeeSource(account, lambda: {"dining room": "dining", "HALLWAY": "hall", "Attic": "attic"}, warn=[].append)
        self.assertEqual(source.poll(1000.0), [Reading("hall", 1000.0, 21.22, 41.0), Reading("dining", 1000.0, 20.39, None)])
        self.assertEqual(ecobee.EcobeeSource(account, dict).poll(1000.0), [])  # nothing to read, nothing asked

    def test_warns_once_of_a_name_the_account_has_no_sensor_by(self):
        account = unittest.mock.Mock(thermostats=lambda: THERMOSTATS)
        names = {"Dining Room": "dining", "Attic": "attic", "bedroom": "bedroom", "Garage": "garage"}
        warnings = []
        source = ecobee.EcobeeSource(account, lambda: names, warn=warnings.append)
        source.poll(1000.0)
        # Not a sensor with no reading yet, nor an offline thermostat's: their names are right.
        self.assertEqual(warnings, ["no sensor named 'Attic', as the house file has; the account has 'Hallway', 'Dining Room', 'Bedroom', 'Den', 'Garage'"])
        source.poll(1180.0)
        self.assertEqual(len(warnings), 1)  # once, not every poll
        del names["Attic"]
        source.poll(1360.0)
        names["ATTIC"] = "attic"
        source.poll(1540.0)
        self.assertEqual(len(warnings), 2)  # and again once it's put back after being fixed


if __name__ == "__main__":
    unittest.main()
