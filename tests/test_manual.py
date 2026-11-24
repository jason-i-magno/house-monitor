import base64
import http.client
import json
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from housemon.example_house import HOUSE
from housemon.hub import Hub
from housemon.manual import Assistant, Manual, ManualError, add_months, schedule
from housemon.readings import Area, Plan
from housemon.server import HouseServer, known_host
from housemon.store import Store

PNG = b"\x89PNG\r\n\x1a\n" + b"a made-up label"
PDF = b"%PDF-1.7\n" + b"a made-up manual"
TODAY = date(2026, 11, 22)


class ScheduleTest(unittest.TestCase):
    def test_months_later_keep_the_day_or_take_the_months_last(self):
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2026, 11, 15), 3), date(2027, 2, 15))
        self.assertEqual(add_months(date(2026, 3, 1), 120), date(2036, 3, 1))

    def test_a_task_is_overdue_due_soon_later_or_never_done(self):
        task = lambda last: {"last_done": last, "every_months": 3}
        self.assertEqual(schedule(task("2026-08-01"), TODAY), {"due": "2026-11-01", "status": "overdue"})
        self.assertEqual(schedule(task("2026-09-10"), TODAY), {"due": "2026-12-10", "status": "soon"})
        self.assertEqual(schedule(task("2026-11-01"), TODAY), {"due": "2027-02-01", "status": "later"})
        self.assertEqual(schedule(task(""), TODAY), {"due": "2026-11-22", "status": "never"})


class ManualTest(unittest.TestCase):
    def setUp(self):
        self.manual = Manual(":memory:")

    def tearDown(self):
        self.manual.close()

    def test_equipment_with_its_kinds_usual_maintenance(self):
        id = self.manual.save_equipment(
            {"name": "Furnace", "kind": "furnace", "room": "Utility room", "brand": "Example", "installed": "2019-06",
             "specs": [{"name": "Filter size", "value": "16x25x1"}, {"name": "", "value": "dropped"}]},
            starter=True,
        )
        [item] = self.manual.contents(TODAY)["equipment"]
        self.assertEqual((item["id"], item["name"], item["room"], item["installed"]), (id, "Furnace", "Utility room", "2019-06"))
        self.assertEqual(item["specs"], [{"name": "Filter size", "value": "16x25x1"}])
        self.assertEqual([(t["title"], t["every_months"], t["status"]) for t in item["tasks"]],
                         [("Replace the air filter", 3, "never"), ("Have it serviced", 12, "never")])

    def test_equipment_is_checked_and_changed_in_place(self):
        with self.assertRaises(ManualError):
            self.manual.save_equipment({"name": "", "kind": "furnace"})
        with self.assertRaises(ManualError):
            self.manual.save_equipment({"name": "Thing", "kind": "spaceship"})
        with self.assertRaises(ManualError):
            self.manual.save_equipment({"name": "Thing", "kind": "other", "installed": "last spring"})
        id = self.manual.save_equipment({"name": "Water heater", "kind": "water heater"})
        self.manual.save_equipment({"name": "Water heater (gas)", "kind": "water heater", "model": "WH-40"}, id)
        self.assertEqual(self.manual.equipment(id)["name"], "Water heater (gas)")
        self.assertEqual(self.manual.contents(TODAY)["equipment"][0]["tasks"], [])  # no starter tasks unless asked
        with self.assertRaises(ManualError) as missing:
            self.manual.save_equipment({"name": "Ghost", "kind": "other"}, 999)
        self.assertEqual(missing.exception.status, 404)

    def test_files_are_checked_and_kept_with_their_equipment(self):
        id = self.manual.save_equipment({"name": "Dryer", "kind": "dryer"})
        photo = self.manual.add_file(id, "label", "label.png", "image/png", PNG)
        manual = self.manual.add_file(id, "manual", "Dryer manual.pdf", "application/pdf", PDF)
        self.assertEqual(self.manual.file(photo)["data"], PNG)
        self.assertEqual([f["name"] for f in self.manual.files_of(id, ("manual",))], ["Dryer manual.pdf"])
        self.assertEqual([f["id"] for f in self.manual.contents(TODAY)["equipment"][0]["files"]], [photo, manual])
        self.assertNotIn("data", self.manual.contents(TODAY)["equipment"][0]["files"][0])
        for kind, mime, data, status in [("label", "image/png", PDF, 415), ("label", "text/plain", b"hi", 415), ("deed", "image/png", PNG, 400)]:
            with self.assertRaises(ManualError) as refused:
                self.manual.add_file(id, kind, "x", mime, data)
            self.assertEqual(refused.exception.status, status)
        with self.assertRaises(ManualError):
            self.manual.add_file(999, "label", "x", "image/png", PNG)
        self.manual.delete_equipment(id)  # its files and tasks go with it
        self.assertIsNone(self.manual.file(photo))

    def test_tasks_for_equipment_or_the_whole_house_and_marking_them_done(self):
        id = self.manual.save_equipment({"name": "Fridge", "kind": "refrigerator"})
        task = self.manual.save_task({"title": "Replace the water filter", "every_months": 6, "equipment_id": id, "last_done": "2026-04-01"})
        house = self.manual.save_task({"title": "Clean the gutters", "every_months": 6})
        contents = self.manual.contents(TODAY)
        self.assertEqual(contents["equipment"][0]["tasks"][0]["status"], "overdue")
        self.assertEqual([t["id"] for t in contents["tasks"]], [house])
        self.manual.mark_done(task, "2026-11-20")
        self.assertEqual(self.manual.contents(TODAY)["equipment"][0]["tasks"][0]["due"], "2027-05-20")
        for bad in [{"title": "", "every_months": 6}, {"title": "x", "every_months": 0}, {"title": "x", "every_months": "6"},
                    {"title": "x", "every_months": 6, "last_done": "soon"}]:
            with self.assertRaises(ManualError):
                self.manual.save_task(bad)
        with self.assertRaises(ManualError):
            self.manual.mark_done(999, "2026-11-20")

    def test_the_inventory_tells_claude_what_is_where(self):
        id = self.manual.save_equipment({"name": "Furnace", "kind": "furnace", "room": "Utility room",
                                         "specs": [{"name": "Filter size", "value": "16x25x1"}]}, starter=True)
        self.manual.add_file(id, "manual", "Furnace manual.pdf", "application/pdf", PDF)
        self.manual.save_task({"title": "Clean the gutters", "every_months": 6, "last_done": "2026-10-01"})
        furnace, house = self.manual.inventory(TODAY)
        self.assertEqual(furnace["specs"], [{"name": "Filter size", "value": "16x25x1"}])
        self.assertEqual(furnace["files"], ["manual: Furnace manual.pdf"])
        self.assertNotIn("serial", furnace)  # empty fields are left out
        self.assertEqual(house["maintenance"][0]["due"], "2027-04-01")


class FakeClient:
    """A stand-in for anthropic.Anthropic: records each request and answers with the text given."""

    def __init__(self, text: str, stop_reason: str = "end_turn"):
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)
        self.text, self.stop_reason = text, stop_reason

    def _create(self, **params):
        self.calls.append(params)
        return SimpleNamespace(stop_reason=self.stop_reason, content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=self.text)])


LABEL = {"kind": "furnace", "name": "Furnace", "brand": "Example", "model": "EX-80", "serial": "X123", "manufactured": "2019-06",
         "specs": [{"name": "Input", "value": "80,000 BTU/h"}], "notes": ""}


class AssistantTest(unittest.TestCase):
    def test_reads_a_label_from_a_photo_with_a_json_schema(self):
        client = FakeClient(json.dumps(LABEL))
        self.assertEqual(Assistant(client).read_label({"mime": "image/png", "data": PNG, "name": "label.png"}), LABEL)
        call = client.calls[0]
        self.assertEqual(call["model"], "claude-opus-5-5")
        self.assertEqual(call["output_config"]["effort"], "low")
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")
        image = call["messages"][0]["content"][0]
        self.assertEqual(image, {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(PNG).decode()}})

    def test_suggests_maintenance_from_the_manual_beyond_what_it_has(self):
        reply = {"tasks": [{"title": "Replace the filter", "every_months": 900, "why": "The manual says so."}, {"title": "  ", "every_months": 3, "why": ""}]}
        client = FakeClient(json.dumps(reply))
        equipment = {"name": "Furnace", "kind": "furnace", "tasks": [{"title": "Have it serviced", "every_months": 12}]}
        tasks = Assistant(client).suggest_tasks(equipment, [{"mime": "application/pdf", "data": PDF, "name": "Furnace manual.pdf"}])
        self.assertEqual(tasks, [{"title": "Replace the filter", "every_months": 240, "why": "The manual says so."}])
        document, text = client.calls[0]["messages"][0]["content"]
        self.assertEqual((document["type"], document["title"]), ("document", "Furnace manual.pdf"))
        self.assertIn("Have it serviced", text["text"])

    def test_answers_from_the_manuals_caching_them(self):
        client = FakeClient("Use a 16x25x1 filter (Furnace manual, page 12).")
        manuals = [{"mime": "image/png", "data": PNG, "name": "label"}, {"mime": "application/pdf", "data": PDF, "name": "Furnace manual.pdf"}]
        answer = Assistant(client).ask("What filter does the furnace take?", [{"name": "Furnace"}], manuals)
        self.assertEqual(answer, "Use a 16x25x1 filter (Furnace manual, page 12).")
        content = client.calls[0]["messages"][0]["content"]
        self.assertNotIn("cache_control", content[0])
        self.assertEqual(content[1]["cache_control"], {"type": "ephemeral"})
        self.assertIn("What filter does the furnace take?", content[2]["text"])

    def test_a_refusal_or_unreadable_answer_is_an_error_for_the_page(self):
        with self.assertRaises(ManualError) as refused:
            Assistant(FakeClient("", stop_reason="refusal")).read_label({"mime": "image/png", "data": PNG})
        self.assertEqual(refused.exception.status, 422)
        with self.assertRaises(ManualError):
            Assistant(FakeClient("not json")).read_label({"mime": "image/png", "data": PNG})
        with self.assertRaises(ManualError):
            Assistant(FakeClient("   ")).ask("Anything?", [], [])


class FakeAssistant:
    def read_label(self, file):
        return {**LABEL, "read": file["name"]}

    def suggest_tasks(self, equipment, manuals):
        return [{"title": f"Check the {equipment['name'].lower()}", "every_months": 12, "why": f"{len(manuals)} manual"}]

    def ask(self, question, inventory, documents):
        return f"{question} ({len(inventory)} things, {len(documents)} documents)"


class ManualServerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "test.sqlite3")
        self.manual = Manual(Path(self.dir.name) / "manual.sqlite3")
        self.areas = [Area("Hall", "Upstairs", Plan(0, 0, 4, 10))]
        self.start(assistant=None, problem="Claude needs an API key.")

    def start(self, assistant, problem=None):
        self.server = HouseServer(("127.0.0.1", 0), Hub(self.store), self.store, [r.sensor for r in HOUSE], self.areas,
                                  manual=self.manual, assistant=assistant, assistant_problem=problem, allowed_hosts=["box.example.ts.net"])
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.store.close()
        self.manual.close()
        self.dir.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        payload = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, payload, {"Content-Type": "application/json", **(headers or {})})
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        kind = response.getheader("Content-Type")
        return response.status, json.loads(raw) if kind == "application/json" else (kind, raw)

    def post(self, action, body, **kw):
        return self.request("POST", f"/api/manual/{action}", body, **kw)

    def test_serves_the_manuals_page(self):
        for path in ("/manual.html", "/manual.js"):
            status, (kind, body) = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertGreater(len(body), 100)

    def test_the_manual_with_the_houses_rooms_and_whether_claude_is_set_up(self):
        status, body = self.request("GET", "/api/manual")
        self.assertEqual(status, 200)
        self.assertEqual(body["equipment"], [])
        self.assertIn("Kitchen", body["rooms"])
        self.assertIn("Hall", body["rooms"])
        self.assertNotIn("Outside", body["rooms"])
        self.assertEqual(body["claude"], {"available": False, "problem": "Claude needs an API key."})
        self.assertEqual(body["kinds"]["water heater"], "Water heater")
        self.assertEqual(body["starter_tasks"]["dryer"], [{"title": "Clean the vent duct", "every_months": 12}])

    def test_equipment_files_and_tasks_through_the_api(self):
        status, saved = self.post("equipment", {"fields": {"name": "Furnace", "kind": "furnace", "room": "Basement"}, "starter": True})
        self.assertEqual(status, 200)
        id = saved["id"]
        status, file = self.post("file", {"equipment_id": id, "kind": "label", "name": "label.png", "mime": "image/png", "data": base64.b64encode(PNG).decode()})
        self.assertEqual(status, 200)
        status, (kind, raw) = self.request("GET", f"/api/manual/file?id={file['id']}")
        self.assertEqual((status, kind, raw), (200, "image/png", PNG))
        _, contents = self.request("GET", "/api/manual")
        [item] = contents["equipment"]
        task = item["tasks"][0]["id"]
        self.assertEqual(self.post("task-done", {"id": task, "day": "2026-11-01"})[0], 200)
        _, contents = self.request("GET", "/api/manual")
        self.assertEqual(contents["equipment"][0]["tasks"][0]["last_done"], "2026-11-01")
        self.assertEqual(self.post("task", {"fields": {"title": "Clean the gutters", "every_months": 6}})[0], 200)
        self.assertEqual(self.post("equipment-delete", {"id": id})[0], 200)
        _, contents = self.request("GET", "/api/manual")
        self.assertEqual((contents["equipment"], [t["title"] for t in contents["tasks"]]), ([], ["Clean the gutters"]))

    def test_bad_requests_say_why(self):
        self.assertEqual(self.post("equipment", {"fields": {"name": "x", "kind": "spaceship"}}), (400, {"error": "Unknown kind 'spaceship'."}))
        self.assertEqual(self.post("file", {"equipment_id": 1, "kind": "label", "mime": "image/png", "data": "%%%"})[0], 400)
        self.assertEqual(self.post("equipment-delete", {"id": "1"})[0], 400)
        self.assertEqual(self.post("launch", {})[0], 404)
        self.assertEqual(self.request("GET", "/api/manual/file?id=999")[0], 404)

    def test_claude_actions_say_how_to_set_it_up_when_it_isnt(self):
        self.assertEqual(self.post("ask", {"question": "Anything?"}), (400, {"error": "Claude needs an API key."}))

    def test_claude_reads_labels_suggests_tasks_and_answers(self):
        self.server.shutdown()
        self.server.server_close()
        self.start(FakeAssistant())
        id = self.post("equipment", {"fields": {"name": "Furnace", "kind": "furnace"}})[1]["id"]
        file = self.post("file", {"equipment_id": id, "kind": "label", "name": "label.png", "mime": "image/png", "data": base64.b64encode(PNG).decode()})[1]["id"]
        self.post("file", {"equipment_id": id, "kind": "manual", "name": "manual.pdf", "mime": "application/pdf", "data": base64.b64encode(PDF).decode()})
        self.assertEqual(self.post("read-label", {"file_id": file})[1]["read"], "label.png")
        unsaved = {"name": "new.png", "mime": "image/png", "data": base64.b64encode(PNG).decode()}
        self.assertEqual(self.post("read-label", unsaved)[1]["read"], "new.png")
        self.assertEqual(self.post("read-label", {**unsaved, "mime": "application/pdf"})[0], 415)
        self.assertEqual(self.post("suggest-tasks", {"id": id})[1], {"tasks": [{"title": "Check the furnace", "every_months": 12, "why": "1 manual"}]})
        self.assertEqual(self.post("ask", {"question": "What filter?", "equipment_id": id})[1], {"answer": "What filter? (1 things, 2 documents)"})
        self.assertEqual(self.post("ask", {"question": "Anything due?"})[1], {"answer": "Anything due? (1 things, 0 documents)"})
        self.assertEqual(self.post("ask", {"question": "  "})[0], 400)

    def test_refuses_other_sites_and_hosts_and_forms(self):
        body = {"fields": {"name": "Furnace", "kind": "furnace"}}
        self.assertEqual(self.post("equipment", body, headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.post("equipment", body, headers={"Host": "evil.example"})[0], 403)  # DNS rebinding
        self.assertEqual(self.request("GET", "/api/manual", headers={"Host": "evil.example:8000"})[0], 403)
        self.assertEqual(self.post("equipment", body, headers={"Content-Type": "text/plain"})[0], 415)
        tailnet = {"Host": "box.example.ts.net:8443", "Origin": "https://box.example.ts.net:8443"}
        self.assertEqual(self.post("equipment", body, headers=tailnet)[0], 200)

    def test_known_hosts(self):
        for host in ("127.0.0.1:8000", "192.168.1.20", "[::1]:8000", "localhost:8000", "box.example.ts.net"):
            self.assertTrue(known_host(host, {"box.example.ts.net"}), host)
        for host in ("evil.example", "", "localhost.evil.example"):
            self.assertFalse(known_host(host), host)


if __name__ == "__main__":
    unittest.main()
