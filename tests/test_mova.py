import base64
import gzip
import hashlib
import io
import json
import socket
import stat
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from housemon import mova
from housemon.readings import Vacuum, VacuumClean, VacuumPart

EMAIL, PASSWORD = "someone@example.com", "hunter2"
BASE = "http://mova.test:13267"
VACUUM = {"did": "1234", "model": "mova.vacuum.r2491a", "customName": "", "deviceInfo": {"displayName": "P10 Pro Ultra"},
          "bindDomain": "20000.mt.us.iot.mova-tech.com:19974"}


def md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


class FakeMova:
    """Answers as MOVA's cloud does: its token endpoint, the device list and commands to the vacuums, checking that each
    request is signed in and signed. values maps a vacuum's did to its properties, each (siid, piid) its value."""

    def __init__(self, devices=(VACUUM,), values=None):
        self.devices = list(devices)
        self.values = values if values is not None else {"1234": {(2, 1): 6, (3, 1): 87}}
        self.calls = []
        self.access = None
        self.refresh = "R1"
        self.revoked = False
        self.expire_access = False

    def tokens(self) -> dict:
        self.access = f"A{len(self.calls)}"
        return {"access_token": self.access, "refresh_token": self.refresh, "expires_in": 7200, "region": "us", "lang": "en",
                "country": "US", "tenant_id": "000002", "uid": "U1"}

    def send(self, url, headers, body):
        names = [name for name, _ in headers]
        header = dict(headers)
        self.calls.append((urlsplit(url).path, header, body))
        assert names[:3] == ["user-agent", "dreame-meta", "accept-encoding"], names
        assert header["authorization"] == f"Basic {mova.CLIENT}" and header["dreame-psd"] == "new"
        assert names.index("host") == names.index("authorization") + 1, names
        path = urlsplit(url).path
        if path == "/dreame-auth/oauth/token":
            form = dict(parse_qsl(body.decode()))
            if form["grant_type"] == "password":
                if form["username"] != EMAIL or form["password"] != md5(PASSWORD + mova.SALT):
                    return 400, json.dumps({"error": "invalid_grant", "error_description": "Bad credentials"}).encode()
                return 200, json.dumps(self.tokens()).encode()
            if self.revoked or form["refresh_token"] != self.refresh:
                return 401, json.dumps({"error": "invalid_grant", "error_description": "Invalid refresh token"}).encode()
            self.refresh = self.refresh + "'"  # MOVA hands out a new one each time
            return 200, json.dumps(self.tokens()).encode()
        if header.get("dreame-auth") != self.access or self.expire_access:
            self.expire_access = False
            return 401, json.dumps({"code": 401, "msg": "token expired"}).encode()
        if path == "/dreame-user-iot/iotuserbind/device/listV2":
            assert body is None
            records = self.devices + [{"did": "9", "model": "mova.mower.v250", "customName": "Mower"}]
            return 200, json.dumps({"code": 0, "success": True, "data": {"page": {"records": records}}}).encode()
        if path == "/dreame-iot-com-20000/device/sendCommand":
            params = json.loads(body)
            sign, ms = params.pop("sign"), params.pop("timestamp")
            assert sign == md5(mova._spliced(params, True) + str(ms) + mova.KEY.decode()), "unsigned"
            values = self.values.get(params["did"])
            if values is None:
                return 200, json.dumps({"code": 80001, "success": False, "msg": "device offline"}).encode()
            # A property the vacuum hasn't, as most have only some, comes back with an error code and no value.
            result = [
                {**p, "code": 0, "value": values[(p["siid"], p["piid"])]} if (p["siid"], p["piid"]) in values else {**p, "code": -1}
                for p in params["data"]["params"]
            ]
            return 200, json.dumps({"code": 0, "success": True, "data": {"id": params["id"], "result": result}}).encode()
        return 404, b"{}"


class AesTest(unittest.TestCase):
    def test_it_encrypts_fips_197s_example(self):
        keys = mova._round_keys(bytes(range(16)))
        block = mova._encrypt_block(keys, bytes.fromhex("00112233445566778899aabbccddeeff"))
        self.assertEqual(block.hex(), "69c4e0d86a7b0430d8cdb78070b4c55a")

    def test_it_pads_to_whole_blocks(self):
        key = bytes(range(16))
        self.assertEqual(len(mova.aes_ecb(key, b"")), 16)
        self.assertEqual(len(mova.aes_ecb(key, b"us|en|US")), 16)
        self.assertEqual(len(mova.aes_ecb(key, bytes(16))), 32)
        # A whole block of padding is the 16-byte block of 16s, encrypted.
        keys = mova._round_keys(key)
        self.assertEqual(mova.aes_ecb(key, bytes(16))[16:], mova._encrypt_block(keys, bytes([16]) * 16))


class SigningTest(unittest.TestCase):
    def test_params_are_strung_together_as_the_app_does(self):
        params = {"did": "42", "id": 7, "data": {"did": "42", "id": 7, "method": "get_properties", "params": [{"siid": 2}]}}
        self.assertEqual(mova._spliced(params, True), 'data=[did="42"&id=7&method="get_properties"]&did=42&id=7')
        self.assertEqual(mova._spliced({"s": "x", "n": None, "b": True, "l": [{"z": 1, "a": 2}]}, True), 'b=true&l=[{"a":2,"z":1}]&n=null&s=x')
        self.assertEqual(mova._spliced({"e": {}}, True), "e=]")

    def test_a_signature_covers_the_params_the_time_and_the_apps_key(self):
        params = {"did": "42", "id": 7}
        self.assertEqual(
            mova.signed(params, 1700000000000),
            {"did": "42", "id": 7, "sign": md5("did=42&id=71700000000000" + mova.KEY.decode()), "timestamp": 1700000000000},
        )


class SignInTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "mova.json"
        self.cloud = FakeMova()

    def tearDown(self):
        self.dir.cleanup()

    def sign_in(self, password=PASSWORD) -> mova.Account:
        saved = mova.sign_in(EMAIL, password, "us", send=self.cloud.send, base=BASE)
        account = mova.Account(self.path, send=self.cloud.send, base=BASE)
        account.save(saved)
        return account

    def test_signing_in_sends_the_password_only_hashed_and_keeps_neither(self):
        self.sign_in()
        path, headers, body = self.cloud.calls[0]
        form = dict(parse_qsl(body.decode()))
        self.assertEqual(path, "/dreame-auth/oauth/token")
        self.assertEqual(form["password"], md5(PASSWORD + mova.SALT))
        self.assertNotIn(PASSWORD, body.decode())
        self.assertEqual(headers["content-type"], "application/x-www-form-urlencoded")
        self.assertEqual(headers["tenant-id"], mova.TENANT)
        self.assertNotIn("dreame-rlc", headers)  # not until the account's region is known
        self.assertTrue(headers["dreame-meta"].startswith(f"cv=a_{mova.APP_VERSION};canvasHash={md5(EMAIL + 'c')[:8]};"))
        saved = self.path.read_text()
        self.assertNotIn(PASSWORD, saved)
        self.assertNotIn(EMAIL, saved)
        self.assertEqual(json.loads(saved)["cloud"], "us")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_a_wrong_password_says_so(self):
        with self.assertRaisesRegex(mova.MovaError, "didn't take that email and password: Bad credentials"):
            self.sign_in("wrong")

    def test_an_unknown_region_is_refused(self):
        with self.assertRaisesRegex(mova.MovaError, "regions are"):
            mova.sign_in(EMAIL, PASSWORD, "mars", send=self.cloud.send)

    def test_requests_are_signed_in_and_name_the_accounts_region(self):
        account = self.sign_in()
        self.assertEqual([v["did"] for v in account.vacuums()], ["1234"])  # the mower left out
        _, headers, _ = self.cloud.calls[-1]
        self.assertEqual(headers["dreame-auth"], self.cloud.access)
        self.assertEqual(headers["dreame-rlc"], base64.b64encode(mova.aes_ecb(mova.KEY, b"us|en|US")).decode())
        self.assertEqual(headers["host"], "mova.test:13267")

    def test_status_is_what_the_vacuum_is_doing_and_its_battery(self):
        account = self.sign_in()
        self.assertEqual(account.status(VACUUM, 1000.0), Vacuum("mova-1234", "P10 Pro Ultra", "Charging", 87, 1000.0, False))
        self.cloud.values["1234"] = {(2, 1): 4, (3, 1): 40}
        self.assertEqual(account.status({**VACUUM, "customName": "Robbie"}, 1000.0), Vacuum("mova-1234", "Robbie", "Error", 40, 1000.0, True))
        self.cloud.values["1234"] = {(2, 1): 99, (3, 1): 40}
        self.assertEqual(account.status(VACUUM, 1000.0).state, "State 99")

    def test_status_says_what_the_error_is_whats_wrong_at_the_dock_and_how_its_parts_are(self):
        account = self.sign_in()
        self.cloud.values["1234"] = {
            (2, 1): 4, (2, 2): 1, (3, 1): 40, (9, 2): 56, (10, 2): 35, (11, 1): 13, (16, 1): 0, (30, 2): 0,
            (27, 1): 2, (27, 2): 1, (27, 3): 0, (27, 4): 1,
        }
        vacuum = account.status(VACUUM, 1000.0)
        self.assertEqual((vacuum.state, vacuum.error, vacuum.problem), ("Error", True, "Wheels are suspended"))
        self.assertEqual(vacuum.dock, ("Clean water tank is low", "Dirty water tank is full or out"))  # the bag's in, detergent off
        self.assertEqual(vacuum.parts, (
            VacuumPart("main brush", 56, True), VacuumPart("side brush", 35, True), VacuumPart("filter", 13, True),
            VacuumPart("sensors", 0, False), VacuumPart("wheels", 0, False),
        ))  # no mop pads or water tank filter, which it doesn't count
        self.assertEqual(self.cloud.calls[-1][0], "/dreame-iot-com-20000/device/sendCommand")
        asked = json.loads(self.cloud.calls[-1][2])["data"]["params"]
        self.assertEqual(len(asked), len(mova.WANTED))
        self.assertLessEqual(len(asked), 15)  # one request, as a vacuum takes up to 15 properties at a time

    def test_a_clean_underway_says_how_far_along_it_is(self):
        account = self.sign_in()
        self.cloud.values["1234"] = {(2, 1): 1, (3, 1): 80, (4, 7): 1, (4, 63): 42, (4, 2): 12, (4, 3): 17}
        before = len(self.cloud.calls)
        self.assertEqual(account.status(VACUUM, 1000.0).clean, VacuumClean(42, 12, 17))
        commands = [json.loads(c[2])["data"]["params"] for c in self.cloud.calls[before:]]
        self.assertEqual(len(commands), 2)  # the usual request, then one for how far along it is
        self.assertEqual([(p["siid"], p["piid"]) for p in commands[1]], [(4, 63), (4, 2), (4, 3)])
        # Paused partway is still a clean underway, and a vacuum that doesn't say how far along it is says the rest.
        self.cloud.values["1234"] = {(2, 1): 3, (3, 1): 80, (4, 7): 6, (4, 2): 12, (4, 3): 17}
        self.assertEqual(account.status(VACUUM, 1000.0).clean, VacuumClean(None, 12, 17))

    def test_no_clean_underway_asks_nothing_more(self):
        account = self.sign_in()
        for task in (0, 20):  # none, or patrolling, which isn't cleaning
            self.cloud.values["1234"] = {(2, 1): 13, (3, 1): 100, (4, 7): task, (4, 63): 0}
            before = len(self.cloud.calls)
            self.assertIsNone(account.status(VACUUM, 1000.0).clean)
            self.assertEqual(len(self.cloud.calls) - before, 1)

    def test_a_warning_is_said_but_isnt_an_error(self):
        account = self.sign_in()
        self.cloud.values["1234"] = {(2, 1): 6, (2, 2): 121, (3, 1): 90}
        vacuum = account.status(VACUUM, 1000.0)
        self.assertEqual((vacuum.state, vacuum.error, vacuum.problem), ("Charging", False, "Dust bag is full or vents are blocked"))
        self.cloud.values["1234"] = {(2, 1): 2, (2, 2): 9999, (3, 1): 90}
        self.assertEqual(account.status(VACUUM, 1000.0).problem, "Error 9999")
        self.assertTrue(account.status(VACUUM, 1000.0).error)

    def test_a_vacuum_the_cloud_cant_reach_is_offline(self):
        account = self.sign_in()
        self.cloud.values = {}
        self.assertEqual(account.status(VACUUM, 1000.0), Vacuum("mova-1234", "P10 Pro Ultra", "Offline", None, 1000.0))
        before = len(self.cloud.calls)
        self.assertEqual(account.status({**VACUUM, "online": False}, 1000.0).state, "Offline")
        self.assertEqual(len(self.cloud.calls), before)  # not asked, as the cloud knows it's offline

    def test_an_expiring_token_is_refreshed_first_and_the_new_refresh_token_kept(self):
        account = self.sign_in()
        saved = json.loads(self.path.read_text())
        account.save({**saved, "expires_at": time.time() + 60})
        account.vacuums()
        self.assertEqual([c[0] for c in self.cloud.calls[-2:]], ["/dreame-auth/oauth/token", "/dreame-user-iot/iotuserbind/device/listV2"])
        self.assertEqual(json.loads(self.path.read_text())["refresh_token"], "R1'")

    def test_a_token_the_cloud_says_expired_is_refreshed_and_the_request_tried_again(self):
        account = self.sign_in()
        self.cloud.expire_access = True
        self.assertEqual(len(account.vacuums()), 1)
        self.assertEqual([c[0] for c in self.cloud.calls[-3:]], [
            "/dreame-user-iot/iotuserbind/device/listV2", "/dreame-auth/oauth/token", "/dreame-user-iot/iotuserbind/device/listV2",
        ])

    def test_a_refused_refresh_means_signing_in_again(self):
        account = self.sign_in()
        self.cloud.revoked = True
        self.cloud.expire_access = True
        with self.assertRaises(mova.SignInNeeded):
            account.vacuums()

    def test_no_sign_in_means_signing_in(self):
        with self.assertRaises(mova.SignInNeeded):
            mova.Account(self.path, send=self.cloud.send, base=BASE).vacuums()

    def test_signing_in_again_while_it_runs_is_taken_up(self):
        account = self.sign_in()
        account.vacuums()
        other = mova.Account(self.path, send=self.cloud.send, base=BASE)
        other.save({**json.loads(self.path.read_text()), "access_token": "elsewhere"})
        self.cloud.access = "elsewhere"
        account.vacuums()  # with the file's new token, not its own old one
        self.assertEqual(self.cloud.calls[-1][1]["dreame-auth"], "elsewhere")

    def test_signing_in_interactively_lists_the_vacuums(self):
        answers = iter(["", EMAIL])
        out = io.StringIO()
        with unittest.mock.patch("sys.stdout", out):
            mova.sign_in_interactively(self.path, ask=lambda prompt: next(answers), ask_secret=lambda prompt: PASSWORD,
                                       send=self.cloud.send, base=BASE)
        self.assertIn("'P10 Pro Ultra' (mova.vacuum.r2491a): Charging, 87% battery", out.getvalue())
        self.cloud.values["1234"].update({(11, 1): 13, (27, 1): 2})
        out = io.StringIO()
        answers = iter(["", EMAIL])
        with unittest.mock.patch("sys.stdout", out):
            mova.sign_in_interactively(self.path, ask=lambda prompt: next(answers), ask_secret=lambda prompt: PASSWORD,
                                       send=self.cloud.send, base=BASE)
        self.assertIn("    Clean water tank is low\n    life left: filter 13%\n", out.getvalue())
        self.cloud.values["1234"] = {(2, 1): 1, (3, 1): 80, (4, 7): 1, (4, 63): 42, (4, 2): 12, (4, 3): 17}
        out = io.StringIO()
        answers = iter(["", EMAIL])
        with unittest.mock.patch("sys.stdout", out):
            mova.sign_in_interactively(self.path, ask=lambda prompt: next(answers), ask_secret=lambda prompt: PASSWORD,
                                       send=self.cloud.send, base=BASE)
        self.assertIn("    cleaning: 42% done, 12 min, 183 sq ft\n", out.getvalue())
        self.assertEqual(json.loads(self.path.read_text())["cloud"], "us")


class SourceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.cloud = FakeMova()
        path = Path(self.dir.name) / "mova.json"
        self.account = mova.Account(path, send=self.cloud.send, base=BASE)
        self.account.save(mova.sign_in(EMAIL, PASSWORD, "us", send=self.cloud.send, base=BASE))

    def tearDown(self):
        self.dir.cleanup()

    def test_each_poll_asks_each_vacuum_and_lists_them_hourly(self):
        source = mova.MovaSource(self.account)
        self.assertEqual(source.poll(1000.0), [Vacuum("mova-1234", "P10 Pro Ultra", "Charging", 87, 1000.0)])
        source.poll(1060.0)
        lists = lambda: sum(c[0].endswith("listV2") for c in self.cloud.calls)
        self.assertEqual(lists(), 1)
        source.poll(1000.0 + mova.DEVICES_EVERY)
        self.assertEqual(lists(), 2)

    def test_a_failed_poll_is_reported_and_tried_again_later(self):
        source = mova.MovaSource(self.account)
        source.poll(1000.0)
        published, err, waits = [], io.StringIO(), []
        failures = iter([OSError("network is unreachable"), mova.MovaError("MOVA's cloud answered 502")])
        listed = []

        def poll(now):
            listed.append(source.devices is not None)
            failure = next(failures, None)
            if failure:
                raise failure
            return [Vacuum("mova-1234", "P10 Pro Ultra", "Idle", 50, now)]

        class Stop:  # waits for nothing, and stops after three
            def is_set(self):
                return len(waits) >= 3

            def wait(self, seconds):
                waits.append(seconds)
                return self.is_set()

        source.poll = poll
        with unittest.mock.patch("sys.stderr", err):
            source.run(published.append, Stop())
        self.assertIn("MOVA: couldn't get its vacuums: OSError('network is unreachable'); trying again in 2 min", err.getvalue())
        self.assertIn("MOVA: MOVA's cloud answered 502; trying again in 5 min", err.getvalue())
        self.assertEqual(waits, [120.0, 300.0, mova.POLL])
        # A network error forgets nothing of the account's vacuums; an error from MOVA has them listed again.
        self.assertEqual(listed, [True, True, False])
        self.assertEqual([v.state for v in published], ["Idle"])


class WireTest(unittest.TestCase):
    def test_a_request_goes_out_with_the_apps_headers_in_its_order(self):
        """As the bytes on the wire: the headers lower case and in the order given, and nothing added, and a gzipped
        answer unzipped."""
        listener = socket.create_server(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        received = []

        def serve():
            conn, _ = listener.accept()
            with conn:
                data = b""
                while b"\r\n\r\n" not in data:
                    data += conn.recv(4096)
                head, _, body = data.partition(b"\r\n\r\n")
                length = int(dict(line.split(b": ", 1) for line in head.split(b"\r\n")[1:])[b"content-length"])
                while len(body) < length:
                    body += conn.recv(4096)
                received.append((head, body))
                answer = gzip.compress(b'{"code":0}')
                conn.sendall(b"HTTP/1.1 200 OK\r\ncontent-encoding: gzip\r\ncontent-length: %d\r\n\r\n%s" % (len(answer), answer))

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{port}/dreame-user-iot/x"
        body = b'{"did":"1"}'
        headers = mova._headers(url, {"meta": "cv=a_1", "region": "us", "lang": "en", "country": "US"}, "application/json", body, "TOKEN")
        self.assertEqual(mova.post(url, headers, body), (200, b'{"code":0}'))
        thread.join(5)
        listener.close()
        head, sent = received[0]
        lines = head.decode().split("\r\n")
        self.assertEqual(lines[0], "POST /dreame-user-iot/x HTTP/1.1")
        self.assertEqual([line.split(":")[0] for line in lines[1:]], [
            "user-agent", "dreame-meta", "accept-encoding", "content-length", "dreame-rlc", "tenant-id", "authorization",
            "host", "dreame-psd", "content-type", "dreame-auth",
        ])
        self.assertIn(f"host: 127.0.0.1:{port}", lines)
        self.assertEqual(sent, body)


if __name__ == "__main__":
    unittest.main()
