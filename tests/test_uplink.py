import io
import json
import math
import stat
import tempfile
import threading
import time
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from housemon import uplink
from housemon.readings import Reading
from housemon.store import Store

TOKEN = "hm_TESTTOKEN"
HOUSE = {"sensors": [{"id": "office", "name": "Office"}], "areas": [], "details": None, "litter_boxes": {}, "wall": 0.5}


class FakeCloud:
    """The cloud service's ingest, as cloud/api does it, for one house, example: its token, the house it was sent, and
    its readings, each kept once by its sensor and time, the first that came. outages lists what the next requests
    for readings meet, in turn: "down" (503, nothing kept), or "lost" (kept, but the connection drops before the
    reply, as when the reply is lost)."""

    def __init__(self):
        self.house = None
        self.readings: dict[tuple[str, float], dict] = {}
        self.batches: list[int] = []  # each batch's size, as it came, kept or not
        self.duplicates = 0
        self.outages: list[str] = []
        self.lock = threading.Lock()
        cloud = self

        class Handler(BaseHTTPRequestHandler):
            def do_PUT(self):
                cloud.handle(self, "PUT")

            def do_POST(self):
                cloud.handle(self, "POST")

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/houses/example"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def handle(self, request: BaseHTTPRequestHandler, method: str) -> None:
        body = json.loads(request.rfile.read(int(request.headers["Content-Length"])))
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self.reply(request, 401, {"error": "not this house's token"})
        if (method, request.path) == ("PUT", "/houses/example/api/house"):
            self.house = body
            request.send_response(204)
            request.end_headers()
            return
        if (method, request.path) != ("POST", "/houses/example/api/readings"):
            return self.reply(request, 404, {"error": "no such house"})
        with self.lock:
            outage = self.outages.pop(0) if self.outages else None
            if outage == "down":
                return self.reply(request, 503, {"error": "unavailable"})
            self.batches.append(len(body["readings"]))
            stored, rejected = 0, []
            for i, r in enumerate(body["readings"]):
                if not -100 <= r["temperature_c"] <= 100:
                    rejected.append({"index": i, "error": "temperature_c is outside -100 to 100"})
                elif (r["sensor_id"], r["ts"]) in self.readings:
                    self.duplicates += 1
                else:
                    self.readings[r["sensor_id"], r["ts"]] = r
                    stored += 1
        if outage == "lost":
            request.close_connection = True  # no reply at all: the uplink can't tell whether they were kept
            return
        self.reply(request, 200, {"stored": stored, "duplicates": len(body["readings"]) - stored - len(rejected), "rejected": rejected})

    @staticmethod
    def reply(request: BaseHTTPRequestHandler, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        request.send_response(code)
        request.send_header("Content-Type", "application/json")
        request.send_header("Content-Length", str(len(data)))
        request.end_headers()
        request.wfile.write(data)


def reading(sensor_id: str, ts: float, temperature_c: float = 20.0) -> Reading:
    return Reading(sensor_id, ts, temperature_c, 40.0, battery=90, rssi=-60, simulated=True)


class UplinkTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "house.sqlite3")
        self.cloud = FakeCloud()
        self.path = Path(self.dir.name) / "uplink.json"
        uplink.Target(self.cloud.url, TOKEN).save(self.path)
        self.house = dict(HOUSE)
        self.logged: list[str] = []
        self.uplink = uplink.Uplink(self.path, self.store, lambda: self.house, batch=100, log=self.logged.append)

    def tearDown(self):
        self.cloud.close()
        self.store.close()
        self.dir.cleanup()

    def add(self, count: int, start: float = 1_800_000_000) -> list[Reading]:
        readings = [reading(f"sensor-{i % 7}", start + 60 * (i // 7)) for i in range(count)]
        self.store.add_many(readings)
        return readings

    def test_sends_the_house_then_every_reading_in_batches(self):
        readings = self.add(250)
        self.uplink.poll(time.time())
        self.assertEqual(self.cloud.house, HOUSE)
        self.assertEqual(self.cloud.batches, [100, 100, 50])
        self.assertEqual(self.cloud.readings[readings[0].sensor_id, readings[0].ts], readings[0].to_json())
        self.assertEqual(len(self.cloud.readings), 250)

        self.cloud.house = None
        self.uplink.poll(time.time())  # nothing new, so nothing sent
        self.assertEqual(self.cloud.batches, [100, 100, 50])
        self.assertIsNone(self.cloud.house)

        self.add(3, start=1_900_000_000)
        self.uplink.poll(time.time())
        self.assertEqual(self.cloud.batches[-1], 3)

    def test_sends_the_house_again_when_it_changes(self):
        self.uplink.poll(time.time())
        self.house = {**HOUSE, "sensors": HOUSE["sensors"] + [{"id": "garage", "name": "Garage"}]}
        self.uplink.poll(time.time())
        self.assertEqual(len(self.cloud.house["sensors"]), 2)

    def test_a_batch_whose_reply_is_lost_is_sent_again_and_kept_once(self):
        readings = self.add(150)
        self.cloud.outages = ["lost"]
        with self.assertRaises(uplink.UplinkError):
            self.uplink.poll(time.time())
        self.assertEqual(len(self.cloud.readings), 100)  # kept, though the uplink couldn't know
        self.uplink.poll(time.time())
        self.assertEqual(self.cloud.batches, [100, 100, 50])  # the first batch again, then the rest
        self.assertEqual(self.cloud.duplicates, 100)
        self.assertEqual(set(self.cloud.readings), {(r.sensor_id, r.ts) for r in readings})

    def test_readings_kept_while_the_cloud_cant_be_reached_go_once_it_can(self):
        """The network down a while, as the roadmap's ten minutes unplugged: nothing lost, nothing twice."""
        sent = self.add(30)
        self.uplink.poll(time.time())
        self.cloud.outages = ["down", "lost", "down"]
        for round in range(3):  # each round, more readings kept at home, and the cloud out of reach
            sent += self.add(40, start=1_810_000_000 + 10_000 * round)
            with self.assertRaises(uplink.UplinkError):
                self.uplink.poll(time.time())
        self.uplink.poll(time.time())
        self.assertEqual(sorted(self.cloud.readings), sorted((r.sensor_id, r.ts) for r in sent))
        self.assertEqual(len(self.cloud.readings), 150)
        self.assertEqual(self.store.unsent(self.cloud.url, 1), [])

    def test_a_cloud_that_isnt_there_is_a_failure_to_try_again(self):
        uplink.Target("http://127.0.0.1:9/houses/example", TOKEN).save(self.path)  # discard: nothing listens
        self.add(5)
        with self.assertRaisesRegex(uplink.UplinkError, "couldn't reach 127.0.0.1:9"):
            self.uplink.poll(time.time())

    def test_a_reading_stored_after_a_newer_one_still_goes(self):
        """As an ecobee sensor's, polled every 3 minutes, is stored after the simulator's newer readings."""
        self.store.add(reading("simulated", 2_000_000_000))
        self.uplink.poll(time.time())
        self.store.add(reading("ecobee", 1_999_999_900))
        self.uplink.poll(time.time())
        self.assertIn(("ecobee", 1_999_999_900), self.cloud.readings)

    def test_rejected_readings_are_logged_and_passed_over(self):
        self.store.add_many([reading("office", 1_800_000_000), reading("office", 1_800_000_060, 150.0)])
        self.uplink.poll(time.time())
        self.assertEqual(len(self.cloud.readings), 1)
        self.assertEqual(len(self.logged), 1)
        self.assertIn("rejected 1 of 2 readings, office's at 1800000060.0", self.logged[0])
        self.uplink.poll(time.time())  # not sent again: it would only be rejected again
        self.assertEqual(self.cloud.batches, [2])

    def test_values_that_arent_numbers_stay_home(self):
        self.store.add_many([reading("office", 1_800_000_000), reading("office", 1_800_000_060, math.inf)])
        self.uplink.poll(time.time())
        self.assertEqual(list(self.cloud.readings), [("office", 1_800_000_000)])
        self.assertIn("left out 1", self.logged[0])

    def test_a_house_that_cant_be_worked_out_is_a_failure_to_try_again(self):
        """Not an error that ends the uplink's thread, as poll_gently would let one it wasn't given end it."""
        self.add(5)

        def broken():
            raise ValueError("walls overlap")

        self.uplink.house = broken
        with self.assertRaisesRegex(uplink.UplinkError, "couldn't work out the house.*walls overlap"):
            self.uplink.poll(time.time())
        self.uplink.house = lambda: HOUSE  # fixed: everything goes
        self.uplink.poll(time.time())
        self.assertEqual(len(self.cloud.readings), 5)

    def test_a_refused_token_lapses(self):
        uplink.Target(self.cloud.url, "hm_WRONG").save(self.path)
        with self.assertRaisesRegex(uplink.TokenRefused, "refused the uplink's token"):
            self.uplink.poll(time.time())
        self.path.unlink()
        with self.assertRaisesRegex(uplink.TokenRefused, "no uplink set up"):
            self.uplink.poll(time.time())

    def test_another_house_in_the_cloud_gets_everything(self):
        self.add(10)
        self.uplink.poll(time.time())
        other = FakeCloud()
        self.addCleanup(other.close)
        uplink.Target(other.url, TOKEN).save(self.path)
        self.uplink.poll(time.time())
        self.assertEqual(len(other.readings), 10)
        self.assertEqual(other.house, HOUSE)

    def test_run_sends_until_stopped(self):
        stop = threading.Event()
        sender = uplink.Uplink(self.path, self.store, lambda: HOUSE, interval=0.01, log=self.logged.append)
        thread = threading.Thread(target=sender.run, args=(stop,))
        thread.start()
        try:
            self.add(20)
            deadline = time.monotonic() + 5
            while len(self.cloud.readings) < 20 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(self.cloud.readings), 20)
        finally:
            stop.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())


class SignInTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "data" / "uplink.json"
        self.cloud = FakeCloud()

    def tearDown(self):
        self.cloud.close()
        self.dir.cleanup()

    def sign_in(self, url: str, token: str) -> uplink.Target:
        return uplink.sign_in_interactively(self.path, ask=lambda _: url, ask_secret=lambda _: token)

    def test_checks_the_token_and_saves_it_for_its_owner_only(self):
        with unittest.mock.patch("sys.stdout", io.StringIO()):
            target = self.sign_in(self.cloud.url + "/", f" {TOKEN} ")
        self.assertEqual(target, uplink.Target(self.cloud.url, TOKEN))
        self.assertEqual(uplink.Target.load(self.path), target)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(self.cloud.readings, {})  # checking it stored nothing

    def test_a_refused_token_isnt_saved(self):
        with self.assertRaises(uplink.TokenRefused):
            self.sign_in(self.cloud.url, "hm_WRONG")
        self.assertFalse(self.path.exists())

    def test_a_url_that_isnt_a_house(self):
        for url in ("example.com/houses/home", "ftp://example.com/houses/home", "https://example.com/"):
            with self.assertRaisesRegex(uplink.UplinkError, "isn't a house's URL"):
                self.sign_in(url, TOKEN)


if __name__ == "__main__":
    unittest.main()
