"""The garden planner's settings (GARDEN in a house file), its saved plans, and the plants it knows (plants.json)."""

import contextlib
import io
import itertools
import json
import os
import tempfile
import unittest
from importlib import resources
from pathlib import Path

from housemon import example_house
from housemon import house as houses
from housemon.garden import MAX_PLANTINGS, MAX_SQUARE, PLANT_ID, Garden, Plans, check_bed, garden

PLANTS = json.loads(resources.files("housemon").joinpath("web", "plants.json").read_text())["plants"]


class GardenTest(unittest.TestCase):
    def test_a_garden_says_where_it_is_and_its_frost_dates(self):
        site = Garden(latitude=45, facing=90, last_frost=(5, 1), first_frost=(10, 1))
        self.assertEqual(site.last_frost, (5, 1))
        for wrong in (
            {"latitude": 70},  # past the polar circle, where the sun's path is another matter
            {"facing": 360},
            {"facing": -10},
            {"last_frost": (2, 30)},
            {"last_frost": (5,)},
            {"first_frost": "Oct 5"},
            {"last_frost": (10, 1), "first_frost": (5, 1)},  # the calendar runs January to December
        ):
            with self.subTest(wrong), self.assertRaises(ValueError):
                Garden(**{"latitude": 45, "facing": 90, "last_frost": (5, 1), "first_frost": (10, 1), **wrong})

    def test_a_house_file_says_where_its_garden_is_and_a_bad_one_leaves_the_last(self):
        start = "from housemon.example_house import AREAS, DETAILS, HOUSE\nfrom housemon.garden import Garden\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "house.py"
            path.write_text(start)
            house = houses.HouseFile(path)
            self.assertIsNone(garden(house.module))  # none unless it says
            path.write_text(start + "GARDEN = Garden(40, 0, (4, 30), (10, 15))\n")
            os.utime(path, (house.mtime + 1, house.mtime + 1))
            self.assertEqual(garden(house.changed()), Garden(40, 0, (4, 30), (10, 15)))
            for bad in ("{'latitude': 40}", "Garden(40, 0, (4, 31), (10, 15))"):
                path.write_text(start + f"GARDEN = {bad}\n")
                os.utime(path, (house.mtime + 1, house.mtime + 1))
                with self.subTest(bad), contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertIsNone(house.changed())
                    self.assertIn("didn't load", err.getvalue())
            self.assertEqual(garden(house.module), Garden(40, 0, (4, 30), (10, 15)))

    def test_every_house_files_garden_is_a_garden(self):
        self.assertIsNotNone(garden(example_house))
        if houses.PRIVATE.exists():
            garden(houses.load(houses.PRIVATE))  # raises if not


class PlansTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "data" / "garden.json"
        self.plans = Plans(self.path)

    def tearDown(self):
        self.dir.cleanup()

    def test_each_bed_is_saved_on_its_own(self):
        self.assertEqual(self.plans.load(), {"beds": {}})  # nothing saved yet
        tomato = [{"plant": "tomato", "x": 0, "y": 0}]
        carrots = [{"plant": "carrot", "x": 2, "y": 1}, {"plant": "carrot", "x": 3, "y": 1}]
        self.plans.save_bed("North bed", tomato)
        self.assertEqual(self.plans.save_bed("South bed", carrots), {"beds": {"North bed": tomato, "South bed": carrots}})
        self.assertEqual(Plans(self.path).load(), {"beds": {"North bed": tomato, "South bed": carrots}})  # as kept
        self.assertEqual(self.plans.save_bed("North bed", []), {"beds": {"South bed": carrots}})  # emptied, it goes
        self.assertEqual(os.listdir(self.path.parent), ["garden.json"])  # each written whole and moved into place

    def test_a_bad_plan_is_refused_and_changes_nothing(self):
        self.plans.save_bed("Bed", [{"plant": "tomato", "x": 0, "y": 0}])
        for bed, plants in [
            ("", []),
            ("x" * 201, []),
            (3, []),
            ("Bed", {"plant": "tomato"}),
            ("Bed", [{"plant": "Tomato", "x": 0, "y": 0}]),  # an id, as plants.json has it
            ("Bed", [{"plant": "tomato", "x": 0}]),
            ("Bed", [{"plant": "tomato", "x": 0, "y": 0, "note": "staked"}]),
            ("Bed", [{"plant": "tomato", "x": -1, "y": 0}]),
            ("Bed", [{"plant": "tomato", "x": MAX_SQUARE, "y": 0}]),
            ("Bed", [{"plant": "tomato", "x": 1.5, "y": 0}]),
            ("Bed", [{"plant": "tomato", "x": True, "y": 0}]),
            ("Bed", [{"plant": "carrot", "x": 0, "y": 0}] * (MAX_PLANTINGS + 1)),
        ]:
            with self.subTest(bed=bed, plants=str(plants)[:60]), self.assertRaises(ValueError):
                self.plans.save_bed(bed, plants)
        self.assertEqual(self.plans.load(), {"beds": {"Bed": [{"plant": "tomato", "x": 0, "y": 0}]}})

    def test_a_file_that_isnt_plans_is_left_alone(self):
        self.path.parent.mkdir()
        self.path.write_text("[1, 2]")
        with self.assertRaises(ValueError):
            self.plans.load()
        with self.assertRaises(ValueError):
            self.plans.save_bed("Bed", [])
        self.assertEqual(self.path.read_text(), "[1, 2]")


class PlantsTest(unittest.TestCase):
    """plants.json, as the planner (garden.js) reads it."""

    def test_each_plant_is_whole(self):
        kinds = {
            "id": str, "name": str, "kind": str, "family": str, "food": bool, "perennial": bool, "perSquare": int,
            "size": int, "height": int, "sun": list, "water": str, "season": str, "frost": str, "start": (list, type(None)),
            "plant": (list, type(None)), "fall": (list, type(None)), "how": str, "days": list, "harvest": int,
            "trellis": bool, "companions": list, "avoid": list, "tips": str,
        }
        ids = [p["id"] for p in PLANTS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len({p["name"] for p in PLANTS}), len(PLANTS))
        for p in PLANTS:
            with self.subTest(p["id"]):
                self.assertEqual(set(p), set(kinds))
                for key, kind in kinds.items():
                    self.assertIsInstance(p[key], kind, key)
                self.assertRegex(p["id"], PLANT_ID)  # as a saved plan names it
                self.assertIn(p["kind"], ("vegetable", "herb", "fruit", "flower"))
                self.assertIn(p["water"], ("low", "medium", "high"))
                self.assertIn(p["season"], ("cool", "warm"))
                self.assertIn(p["frost"], ("tender", "light", "hard"))
                self.assertIn(p["how"], ("seed", "transplant", "either", "set"))
                # One plant over size × size squares, or several to a square.
                self.assertTrue(p["size"] == 1 and p["perSquare"] in (1, 2, 4, 8, 9, 16) or p["size"] > 1 and p["perSquare"] == 1)
                self.assertTrue(0 < p["sun"][0] <= p["sun"][1] <= 12)
                self.assertTrue(0 < p["days"][0] <= p["days"][1])
                self.assertGreater(p["harvest"], 0)
                self.assertGreater(p["height"], 0)
                for window in ("start", "plant", "fall"):
                    if p[window]:
                        self.assertEqual(len(p[window]), 2)
                        self.assertLessEqual(*p[window])
                self.assertTrue(p["plant"] or p["fall"], "it's planted sometime")
                if p["start"] and p["plant"]:
                    self.assertLess(p["start"][1], p["plant"][1], "started indoors before it's set out")
                self.assertTrue(p["tips"].endswith("."))

    def test_neighbors_are_plants_it_knows_and_never_both_good_and_bad(self):
        by_id = {p["id"]: p for p in PLANTS}
        for p in PLANTS:
            with self.subTest(p["id"]):
                for key in ("companions", "avoid"):
                    self.assertEqual(len(p[key]), len(set(p[key])), key)
                    self.assertNotIn(p["id"], p[key])
                    self.assertLessEqual(set(p[key]), set(by_id), key)
        # The planner takes either plant's word for a pair, so neither may call it good while the other calls it bad.
        for a, b in itertools.permutations(PLANTS, 2):
            good = b["id"] in a["companions"] or a["id"] in b["companions"]
            bad = b["id"] in a["avoid"] or a["id"] in b["avoid"]
            self.assertFalse(good and bad, (a["id"], b["id"]))


class CheckBedTest(unittest.TestCase):
    def test_a_plan_is_a_beds_name_and_its_plantings(self):
        check_bed("North bed", [])
        check_bed("North bed", [{"plant": "bean-bush", "x": 11, "y": 3}])
        with self.assertRaises(ValueError):
            check_bed("North bed", [{"plant": "bean bush", "x": 0, "y": 0}])


if __name__ == "__main__":
    unittest.main()
