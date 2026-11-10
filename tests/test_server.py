import http.client
import json
import queue
import tempfile
import threading
import time
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from housemon.details import Details, Finish, Fixture, Opening, Roof
from housemon.hub import Hub
from housemon.layout import house_layout
from housemon.readings import Area, Plan, Reading
from housemon.server import HouseServer, house_json
from housemon.example_house import HOUSE
from housemon.store import Store


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "test.sqlite3")
        self.hub = Hub(self.store)
        self.sensors = [room.sensor for room in HOUSE]
        self.areas = [Area("Porch", "Main", (Plan(0, 46, 14, 6), Plan(14, 44, 6, 8)))]
        self.details = Details(
            lot=((-10, -10), (70, -10), (70, 60), (-10, 60)),
            grade=-0.5,
            finishes={"Office": Finish("carpet")},
            roofs=(Roof("Roof", 0, 0, 60, 50, eave=9, peak=14, ridge="x"),),
            openings=(Opening("Office door", "door", "Office", "front", 1, 3, 6.67),),
        )
        self.server = HouseServer(("127.0.0.1", 0), self.hub, self.store, self.sensors, self.areas, self.details)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.store.close()
        self.dir.cleanup()

    def get(self, path: str) -> tuple[int, str, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        conn.request("GET", path)
        response = conn.getresponse()
        result = response.status, response.getheader("Content-Type"), response.read()
        conn.close()
        return result

    def test_serves_the_page_and_its_assets(self):
        assets = [("/", "text/html"), ("/app.js", "text/javascript"), ("/model.js", "text/javascript"), ("/style.css", "text/css")]
        for path, kind in assets:
            status, content_type, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertTrue(content_type.startswith(kind), content_type)
            self.assertGreater(len(body), 100)

    def test_unknown_paths_are_not_found(self):
        self.assertEqual(self.get("/../housemon/server.py")[0], 404)
        self.assertEqual(self.get("/api/nothing")[0], 404)

    def test_sensors_include_the_latest_reading(self):
        reading = Reading(self.sensors[0].id, time.time(), 21.3, 36.5, 90, -60)
        self.hub.publish(reading)
        body = json.loads(self.get("/api/sensors")[2])
        sensors = body["sensors"]
        self.assertEqual([s["name"] for s in sensors], [s.name for s in self.sensors])
        self.assertEqual(sensors[0]["reading"], reading.to_json())
        self.assertIsNone(sensors[1]["reading"])
        # Each plan with its share of the wall on each side (layout.py): this one's outer walls, and half of one shared.
        self.assertEqual(sensors[0]["plan"], {**asdict(self.sensors[0].plan), "sides": {"back": 1, "front": 0.5, "left": 1, "right": 1}})
        self.assertEqual([s["heated"] for s in sensors], [s.heated for s in self.sensors])
        self.assertEqual([s["outside"] for s in sensors], [s.outside for s in self.sensors])
        family_room = next(s for s in self.sensors if s.name == "Family room")  # in pieces: a list of plans
        plans = sensors[self.sensors.index(family_room)]["plan"]
        self.assertEqual([{k: v for k, v in p.items() if k != "sides"} for p in plans], [asdict(plan) for plan in family_room.plan])
        self.assertEqual(body["areas"][0]["name"], "Porch")
        self.assertEqual([{k: v for k, v in p.items() if k != "sides"} for p in body["areas"][0]["plan"]], [asdict(p) for p in self.areas[0].plan])
        self.assertEqual(body["wall"], 0.33)
        details = body["details"]
        self.assertEqual((details["lot"], details["grade"]), ([[-10, -10], [70, -10], [70, 60], [-10, 60]], -0.5))
        self.assertEqual(details["finishes"], {"Office": {"floor": "carpet", "walls": "paint"}})
        self.assertEqual(details["roofs"][0]["ridge"], "x")
        self.assertEqual(details["openings"][0]["room"], "Office")
        self.assertEqual(details["trees"], [])

    def test_details_place_things_against_walls(self):
        office = next(s for s in self.sensors if s.name == "Office")
        details = replace(self.details, fixtures=(Fixture("Desk", "appliance", "Office", 4, 4, 3, 2, 2.5, against=("right",)),),
                          openings=(Opening("Opening", "opening", "Office", "back", None, None, 7),))
        body = house_json(self.sensors, self.areas, details)
        (desk,) = body["details"]["fixtures"]
        layout = house_layout(self.sensors, self.areas, details)
        self.assertAlmostEqual(desk["x"], office.plan.width - layout.inward(office.plan, "right") - 3)
        (opening,) = body["details"]["openings"]
        self.assertAlmostEqual(opening["offset"], layout.inward(office.plan, "left"))
        self.assertAlmostEqual(opening["offset"] + opening["width"], office.plan.width - layout.inward(office.plan, "right"))

    def test_the_house_is_refreshed_before_it_is_sent(self):
        calls = []
        server = HouseServer(("127.0.0.1", 0), self.hub, self.store, self.sensors, refresh=lambda: calls.append(1))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            conn.request("GET", "/api/sensors")
            conn.getresponse().read()
            conn.close()
            self.assertEqual(calls, [1])
        finally:
            server.shutdown()
            server.server_close()

    def test_details_are_null_for_a_source_without_them(self):
        server = HouseServer(("127.0.0.1", 0), self.hub, self.store, self.sensors)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            conn.request("GET", "/api/sensors")
            self.assertIsNone(json.loads(conn.getresponse().read())["details"])
            conn.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_history_returns_bucketed_series(self):
        now = time.time()
        self.store.add_many(Reading("sim-office", now - 60 * i, 18.0, 40.0) for i in range(30))
        history = json.loads(self.get("/api/history?hours=6")[2])
        self.assertEqual(history["bucket"], 60)
        self.assertEqual(len(history["series"]["sim-office"]), 30)
        self.assertEqual(json.loads(self.get("/api/history?hours=720")[2])["bucket"], 87 * 60)  # 500 points

    def test_history_can_leave_simulated_readings_out(self):
        now = time.time()
        self.store.add_many(Reading("sim-office", now - 60 * i, 18.0, 40.0, simulated=i >= 10) for i in range(30))
        self.assertEqual(len(json.loads(self.get("/api/history?hours=6")[2])["series"]["sim-office"]), 30)
        self.assertEqual(len(json.loads(self.get("/api/history?hours=6&simulated=0")[2])["series"]["sim-office"]), 10)

    def test_history_rejects_bad_ranges(self):
        for hours in ["0", "-1", "nan", "inf", "lots", str(24 * 400)]:
            self.assertEqual(self.get(f"/api/history?hours={hours}")[0], 400, hours)

    def test_stream_pushes_each_new_reading(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        conn.request("GET", "/api/stream")
        response = conn.getresponse()
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
        self.assertEqual(response.readline(), b"retry: 3000\n")
        reading = Reading(self.sensors[2].id, time.time(), 20.1, 33.0, 100, -72)
        self.hub.publish(reading)
        lines = [response.readline() for _ in range(2)]
        self.assertEqual(lines[1], b"data: " + json.dumps(reading.to_json()).encode() + b"\n")
        conn.close()


class HubTest(unittest.TestCase):
    def test_a_stalled_client_does_not_block_the_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "test.sqlite3")
            hub = Hub(store)
            stalled, healthy = hub.subscribe(maxsize=1), hub.subscribe()
            for i in range(3):
                hub.publish(Reading("a", 1000 + 60 * i, 20.0, 35.0))
            self.assertEqual(stalled.qsize(), 1)
            self.assertEqual(healthy.qsize(), 3)
            hub.unsubscribe(healthy)
            hub.publish(Reading("a", 2000, 20.0, 35.0))
            self.assertEqual(healthy.qsize(), 3)
            self.assertEqual(hub.latest()["a"].ts, 2000)
            store.close()


if __name__ == "__main__":
    unittest.main()
