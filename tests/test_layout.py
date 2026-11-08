"""Where walls stand, on small made-up layouts, and things placed against them (housemon/layout.py)."""

import unittest

from housemon.details import Details, Fixture, Opening, Steps
from housemon.layout import WALL, HouseLayout
from housemon.readings import Area, Plan

LOT = ((-50, -50), (50, -50), (50, 50), (-50, 50))


def house(*plans, grade=-1):
    places = [Area(f"Room {i}", "Main", plan) for i, plan in enumerate(plans)]
    return HouseLayout(places, grade), places


class WallsTest(unittest.TestCase):
    def test_outer_walls_stand_inside_the_plan(self):
        h, (a,) = house(Plan(0, 0, 10, 10))
        self.assertEqual(h.sides(a.plan), {"back": 1, "front": 1, "left": 1, "right": 1})

    def test_a_wall_shared_all_along_is_half_on_each_side(self):
        h, (a, b) = house(Plan(0, 0, 10, 10), Plan(10, 0, 10, 10))
        self.assertEqual((h.sides(a.plan)["right"], h.sides(b.plan)["left"]), (0.5, 0.5))

    def test_a_wall_stands_on_the_side_facing_the_outside(self):
        # The first room's right wall faces the outside past the second room's front, so it stands it all.
        h, (a, b) = house(Plan(0, 0, 10, 10), Plan(10, 0, 10, 6))
        self.assertEqual((h.sides(a.plan)["right"], h.sides(b.plan)["left"]), (1, 0))

    def test_facing_the_outside_both_sides_the_higher_floor_stands_it(self):
        # Each faces the outside past the other's end; the first is a level up, so its outer walls stand.
        h, (a, b) = house(Plan(0, 0, 10, 10, elevation=4), Plan(10, 2, 10, 10))
        self.assertEqual((h.sides(a.plan)["right"], h.sides(b.plan)["left"]), (1, 0))

    def test_over_a_lower_ceiling_the_taller_room_stands_it(self):
        h, (a, b) = house(Plan(0, 0, 10, 10, height=12), Plan(10, 0, 10, 10))
        self.assertEqual((h.sides(a.plan)["right"], h.sides(b.plan)["left"]), (1, 0))

    def test_walls_in_line_stand_on_one_side(self):
        # Two rooms side by side behind one long one: the long one's back wall faces the outside past their ends, so the
        # line is its, all along, the two rooms' fronts standing none.
        h, (a, b, c) = house(Plan(0, 10, 30, 10), Plan(5, 0, 10, 10), Plan(15, 0, 10, 10))
        self.assertEqual(h.sides(a.plan)["back"], 1)
        self.assertEqual((h.sides(b.plan)["front"], h.sides(c.plan)["front"]), (0, 0))


class PlacingTest(unittest.TestCase):
    def setUp(self):
        self.h, (self.a, self.b) = house(Plan(0, 0, 10, 10), Plan(10, 0, 10, 6))

    def test_a_fixture_stands_flush_against_the_walls_it_is_against(self):
        details = Details(lot=LOT, grade=-1, fixtures=(
            Fixture("Cabinet", "appliance", "Room 0", 3, 3, 2, 1, 3, against=("right", "back")),
            Fixture("Shelf", "appliance", "Room 1", 3, 3, 2, 1, 3, against=("left",)),
        ))
        cabinet, shelf = self.h.resolve(details).fixtures
        self.assertAlmostEqual(cabinet.x, 10 - WALL - 2)  # its room's right wall stands all of it
        self.assertAlmostEqual(cabinet.y, WALL)
        self.assertEqual((shelf.x, shelf.y), (0, 3))  # the wall on its left stands past it, in the room beyond

    def test_steps_stand_flush_against_the_walls_they_are_against(self):
        details = Details(lot=LOT, grade=-1, steps=(Steps("Steps", 12, 2, 4, 3, bottom=0, top=3, up="right", room="Room 1", against=("left",)),))
        (steps,) = self.h.resolve(details).steps
        self.assertEqual(steps.x, 10)

    def test_an_opening_runs_from_and_to_walls(self):
        details = Details(lot=LOT, grade=-1, openings=(
            Opening("Wall to wall", "opening", "Room 0", "front", None, None, 7),
            Opening("From the wall", "opening", "Room 0", "back", None, 4, 7),
        ))
        whole, part = self.h.resolve(details).openings
        self.assertAlmostEqual(whole.offset, WALL)
        self.assertAlmostEqual(whole.offset + whole.width, 10 - WALL)
        self.assertEqual((part.offset, part.width), (WALL, 4))

    def test_against_is_checked(self):
        with self.assertRaises(ValueError):
            Fixture("Cabinet", "appliance", "Room 0", 0, 0, 2, 1, 3, against="right")  # a tuple of sides
        with self.assertRaises(ValueError):
            Fixture("Cabinet", "appliance", "Room 0", 0, 0, 2, 1, 3, against=("left", "right"))
        with self.assertRaises(ValueError):
            Fixture("Bench", "appliance", None, 0, 0, 2, 1, 3, against=("left",))
        with self.assertRaises(ValueError):
            Fixture("Cabinet", "appliance", "Room 0", 0, 0, 2, 1, 3, against=("up",))


if __name__ == "__main__":
    unittest.main()
