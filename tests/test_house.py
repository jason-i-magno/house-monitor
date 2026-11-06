"""Checks every house file must pass: the example house, and your own in private/ if it's there.

Each test runs once per house, as a subtest named for it.
"""

import itertools
import math
import tempfile
import unittest
from pathlib import Path

from housemon import example_house
from housemon import house as houses
from housemon.details import Closet, Fixture, Opening, Roof

WALL = 0.4  # how far things in a room stay from its plan's edges: a wall's thickness, and a bit
DOORS = ("door", "sliding door", "garage door", "opening")


def pieces(plan):
    return plan if isinstance(plan, tuple) else (plan,)


def ceiling(p, x, y):
    """A piece's ceiling height above its floor at x, y, following its drop."""
    if not p.drop:
        return p.height
    side, feet = p.drop
    across = {
        "left": (p.x + p.width - x) / p.width,
        "right": (x - p.x) / p.width,
        "back": (p.y + p.depth - y) / p.depth,
        "front": (y - p.y) / p.depth,
    }[side]
    return p.height - feet * across


def inside(x, y, z, p):
    return p.x < x < p.x + p.width and p.y < y < p.y + p.depth and p.elevation < z < p.elevation + ceiling(p, x, y)


def squares(rect):  # the half-foot squares a rectangle covers, by their centers
    x, y, width, depth = rect
    return {
        ((i + 0.5) / 2, (j + 0.5) / 2)
        for i in range(round(2 * x), round(2 * (x + width)))
        for j in range(round(2 * y), round(2 * (y + depth)))
    }


def rect(thing):
    return (thing.x, thing.y, thing.width, thing.depth)


def overlap(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def corners(area):
    x, y, width, depth = area
    return [(x, y), (x + width, y), (x, y + depth), (x + width, y + depth)]


def in_polygon(x, y, points):
    hit = False
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def on_roof(r, x, y):
    in_rect = r.x < x < r.x + r.width and r.y < y < r.y + r.depth
    return in_rect and (r.outline is None or in_polygon(x, y, list(r.outline)))


class Layout:
    """A house file's places and details, and where things are in it."""

    def __init__(self, name, module):
        self.name = name
        self.rooms = module.HOUSE
        self.details = module.DETAILS
        self.places = [room.sensor for room in module.HOUSE if room.sensor.plan] + list(module.AREAS)
        self.pieces = [(place, plan) for place in self.places for plan in pieces(place.plan)]
        self.footprint = set().union(*(squares(rect(p)) for _, p in self.pieces))

    def place(self, name):
        (match,) = [p for p in self.places if p.name == name]  # one place, so it's clear which is meant
        return match

    def first(self, name):
        return pieces(self.place(name).plan)[0]

    def wall(self, opening: Opening):
        """The piece an opening's wall belongs to, the wall's axis and line, and the opening's span along it."""
        side = opening.wall
        key = {
            "left": lambda p: p.x,
            "right": lambda p: -(p.x + p.width),
            "back": lambda p: p.y,
            "front": lambda p: -(p.y + p.depth),
        }
        p = min(pieces(self.place(opening.room).plan), key=key[side])
        if side in ("left", "right"):
            line = p.x if side == "left" else p.x + p.width
            return p, "y", line, p.y + opening.offset, p.y + opening.offset + opening.width, p.depth
        line = p.y if side == "back" else p.y + p.depth
        return p, "x", line, p.x + opening.offset, p.x + opening.offset + opening.width, p.width

    def beyond(self, opening: Opening):
        """Points just past an opening's wall, across its width and height."""
        p, axis, line, a, b, _ = self.wall(opening)
        out = -0.01 if opening.wall in ("left", "back") else 0.01
        z0 = p.elevation + opening.sill
        for u, v in itertools.product((0.1, 0.5, 0.9), repeat=2):
            s, z = a + u * (b - a), z0 + v * opening.height
            yield (line + out, s, z) if axis == "y" else (s, line + out, z)

    def absolute(self, thing):
        """A closet's or fixture's rectangle on the plan, from its room's back-left corner."""
        p = self.first(thing.room)
        return (p.x + thing.x, p.y + thing.y, thing.width, thing.depth)

    def in_lot(self, x, y, slack=0.0):
        """Whether a point is inside the lot, or within slack of its edge."""
        lot = list(self.details.lot)
        for (x1, y1), (x2, y2) in zip(lot, lot[1:] + lot[:1]):
            t = max(0, min(1, ((x - x1) * (x2 - x1) + (y - y1) * (y2 - y1)) / ((x2 - x1) ** 2 + (y2 - y1) ** 2)))
            if math.hypot(x - x1 - t * (x2 - x1), y - y1 - t * (y2 - y1)) <= slack:
                return True
        return in_polygon(x, y, lot)

    def on_house(self, x, y):
        return any(p.x < x < p.x + p.width and p.y < y < p.y + p.depth for _, p in self.pieces)


LAYOUTS = [Layout("example", example_house)]
if houses.PRIVATE.exists():
    LAYOUTS.append(Layout("private", houses.load(houses.PRIVATE)))


class HouseTestCase(unittest.TestCase):
    def each(self):
        """Each house's layout, as a subtest of its own."""
        for layout in LAYOUTS:
            with self.subTest(house=layout.name):
                yield layout


class PlacesTest(HouseTestCase):
    def test_places_on_a_floor_do_not_overlap(self):
        for h in self.each():
            for i, (place, plan) in enumerate(h.pieces):
                for other, other_plan in h.pieces[i + 1 :]:
                    if place.floor == other.floor:
                        self.assertFalse(overlap(rect(plan), rect(other_plan)), (place.name, other.name))

    def test_each_floor_is_level_but_for_a_step(self):
        # The garage, say, a step down from the rest of the main floor.
        for h in self.each():
            for floor in {place.floor for place in h.places}:
                elevations = {plan.elevation for place, plan in h.pieces if place.floor == floor}
                self.assertLessEqual(max(elevations) - min(elevations), 1, floor)

    def test_every_room_named_is_in_the_house(self):
        for h in self.each():
            d = h.details
            names = [o.room for o in d.openings] + [c.room for c in d.closets] + [c.room for c in d.claddings]
            names += [f.room for f in d.fixtures if f.room] + [s.room for s in d.steps if s.room]
            for name in names:
                h.place(name)  # exactly one
            self.assertLessEqual(set(d.finishes), {q.name for q in h.places})


class OpeningTest(HouseTestCase):
    def test_fits_in_its_wall(self):
        for h in self.each():
            for opening in h.details.openings:
                p, _, _, a, b, length = h.wall(opening)
                self.assertTrue(0 <= opening.offset and opening.offset + opening.width <= length, opening.name)
                if opening.wall in ("left", "right"):
                    line = p.x if opening.wall == "left" else p.x + p.width
                    ceilings = [ceiling(p, line, s) for s in (a, b)]
                else:
                    line = p.y if opening.wall == "back" else p.y + p.depth
                    ceilings = [ceiling(p, s, line) for s in (a, b)]
                self.assertTrue(0 <= opening.sill and opening.sill + opening.height <= min(ceilings), opening.name)

    def test_windows_open_to_the_outside(self):
        for h in self.each():
            for opening in (o for o in h.details.openings if o.kind == "window"):
                for x, y, z in h.beyond(opening):
                    hit = [q.name for q, p in h.pieces if inside(x, y, z, p)]
                    self.assertEqual(hit, [], opening.name)

    def test_doors_lead_outside_or_into_one_place_at_their_sill(self):
        """A door's sill is the floor beyond it, or a little above it, as a crawlspace's hatch is."""
        for h in self.each():
            for opening in (o for o in h.details.openings if o.kind in DOORS):
                sill = h.wall(opening)[0].elevation + opening.sill
                hits = {
                    (q.name, p.elevation)
                    for x, y, z in h.beyond(opening)
                    for q, p in h.pieces
                    if q.name != opening.room and inside(x, y, z, p)
                }
                if hits:
                    (other,) = hits  # all of it into one place
                    self.assertTrue(0 <= sill - other[1] <= 1.5, opening.name)

    def test_openings_in_a_wall_do_not_overlap(self):
        for h in self.each():

            def extent(opening):
                p, axis, line, a, b, _ = h.wall(opening)
                z0 = p.elevation + opening.sill
                return axis, line, a, b, z0, z0 + opening.height

            for one, other in itertools.combinations(h.details.openings, 2):
                (axis, line, a, b, z0, z1), (axis2, line2, a2, b2, z02, z12) = extent(one), extent(other)
                same_wall = axis == axis2 and abs(line - line2) < 1e-6
                self.assertFalse(same_wall and a < b2 and a2 < b and z0 < z12 and z02 < z1, (one.name, other.name))


class ClosetTest(HouseTestCase):
    def test_sits_in_its_room_with_its_doors_facing_in(self):
        for h in self.each():
            for closet in h.details.closets:
                p = h.first(closet.room)
                self.assertTrue(0 <= closet.x and closet.x + closet.width <= p.width, closet.name)
                self.assertTrue(0 <= closet.y and closet.y + closet.depth <= p.depth, closet.name)
                on_wall = {
                    "left": closet.x == 0,
                    "right": closet.x + closet.width == p.width,
                    "back": closet.y == 0,
                    "front": closet.y + closet.depth == p.depth,
                }
                self.assertFalse(on_wall[closet.door], closet.name)

    def test_closets_do_not_overlap_or_cover_a_door_or_window(self):
        for h in self.each():
            for one, other in itertools.combinations(h.details.closets, 2):
                if h.place(one.room) is h.place(other.room):
                    self.assertFalse(overlap(h.absolute(one), h.absolute(other)), (one.name, other.name))
            for closet, opening in itertools.product(h.details.closets, h.details.openings):
                if opening.kind == "vent":
                    continue
                p, axis, line, a, b, _ = h.wall(opening)
                x, y, width, depth = h.absolute(closet)
                if axis == "x":
                    covers = line in (y, y + depth) and x < b and a < x + width
                else:
                    covers = line in (x, x + width) and y < b and a < y + depth
                same_floor = p.elevation == h.first(closet.room).elevation
                self.assertFalse(covers and same_floor, (closet.name, opening.name))


class FixtureTest(HouseTestCase):
    def test_fits_in_its_room_under_the_ceiling(self):
        for h in self.each():
            for fixture in (f for f in h.details.fixtures if f.room):
                p = h.first(fixture.room)
                self.assertTrue(WALL <= fixture.x and fixture.x + fixture.width <= p.width - WALL, fixture.name)
                self.assertTrue(WALL <= fixture.y and fixture.y + fixture.depth <= p.depth - WALL, fixture.name)
                x, y, width, depth = h.absolute(fixture)
                lowest = min(ceiling(p, x + u * width, y + v * depth) for u in (0, 1) for v in (0, 1))
                self.assertLessEqual(fixture.lift + fixture.height, lowest, fixture.name)

    def test_fireplaces_stand_against_a_wall(self):
        for h in self.each():
            for fireplace in (f for f in h.details.fixtures if f.kind == "fireplace"):
                p = h.first(fireplace.room)
                gaps = (fireplace.x, fireplace.y, p.width - fireplace.x - fireplace.width, p.depth - fireplace.y - fireplace.depth)
                self.assertLessEqual(min(gaps), WALL + 0.25, fireplace.name)

    def test_fixtures_are_in_a_closet_or_clear_of_them(self):
        for h in self.each():
            for fixture, closet in itertools.product((f for f in h.details.fixtures if f.room), h.details.closets):
                if h.place(fixture.room) is not h.place(closet.room):
                    continue
                (fx, fy, fw, fd), (cx, cy, cw, cd) = h.absolute(fixture), h.absolute(closet)
                within = cx + WALL <= fx and fx + fw <= cx + cw - WALL and cy + WALL <= fy and fy + fd <= cy + cd - WALL
                self.assertTrue(within or not overlap((fx, fy, fw, fd), (cx, cy, cw, cd)), (fixture.name, closet.name))

    def test_fixtures_do_not_overlap(self):
        for h in self.each():
            for one, other in itertools.combinations((f for f in h.details.fixtures if f.room), 2):
                if h.place(one.room) is h.place(other.room):
                    stacked = one.lift >= other.lift + other.height or other.lift >= one.lift + one.height
                    self.assertFalse(overlap(h.absolute(one), h.absolute(other)) and not stacked, (one.name, other.name))

    def test_nothing_blocks_a_door(self):
        """The floor 2 ft either side of each door is clear of fixtures and closets, a closet's own doors included."""
        for h in self.each():
            clear = []
            for opening in (o for o in h.details.openings if o.kind in DOORS):
                p, axis, line, a, b, _ = h.wall(opening)
                strip = (a, line - 2, b - a, 4) if axis == "x" else (line - 2, a, 4, b - a)
                clear.append((opening.name, p.elevation, opening.sill + opening.height, strip))
            for closet in h.details.closets:
                x, y, width, depth = h.absolute(closet)
                strip = {
                    "left": (x - 2, y, 2, depth),
                    "right": (x + width, y, 2, depth),
                    "back": (x, y - 2, width, 2),
                    "front": (x, y + depth, width, 2),
                }[closet.door]
                clear.append((closet.name, h.first(closet.room).elevation, 6.67, strip))
            for name, floor, top, strip in clear:
                for fixture in (f for f in h.details.fixtures if f.room):
                    blocks = fixture.lift < top and overlap(strip, h.absolute(fixture))
                    self.assertFalse(h.first(fixture.room).elevation == floor and blocks, (name, fixture.name))
                for closet in h.details.closets:
                    blocks = name != closet.name and overlap(strip, h.absolute(closet))
                    self.assertFalse(h.first(closet.room).elevation == floor and blocks, (name, closet.name))


class YardTest(HouseTestCase):
    def test_the_house_is_in_the_lot(self):
        for h in self.each():
            for place, p in h.pieces:
                self.assertTrue(all(h.in_lot(x, y) for x, y in corners(rect(p))), place.name)

    def test_things_outside_are_in_the_lot_off_the_house_and_apart(self):
        for h in self.each():
            d = h.details
            # A ground may run past the lot's edge, as a driveway does to the street, but starts in it.
            outside = [(g.name, rect(g)) for g in d.grounds]
            for name, (x, y, width, depth) in outside:
                self.assertTrue(h.in_lot(x + width / 2, y + depth / 2), name)
            built = [(f.name, rect(f)) for f in d.fixtures if not f.room] + [(s.name, rect(s)) for s in d.sheds]
            for name, area in built:
                self.assertTrue(all(h.in_lot(x, y) for x, y in corners(area)), name)
            for name, area in outside + built:
                self.assertFalse(squares(area) & h.footprint, name)
            sheds = [(s.name, rect(s)) for s in d.sheds]
            for (name, area), (other, area2) in itertools.combinations(outside + sheds, 2):
                self.assertFalse(squares(area) & squares(area2), (name, other))

    def test_trees_stand_in_the_lawn_or_a_cutout(self):
        def within(x, y, area):
            ax, ay, width, depth = area
            return ax <= x <= ax + width and ay <= y <= ay + depth

        for h in self.each():
            for tree in h.details.trees:
                self.assertTrue(h.in_lot(tree.x, tree.y), tree.name)
                self.assertFalse(h.on_house(tree.x, tree.y), tree.name)
                for thing in (*h.details.grounds, *h.details.sheds):
                    cut = any(within(tree.x, tree.y, c) for c in getattr(thing, "cutouts", ()))
                    self.assertFalse(within(tree.x, tree.y, rect(thing)) and not cut, (tree.name, thing.name))

    def test_cutouts_are_in_their_grounds(self):
        for h in self.each():
            for ground in h.details.grounds:
                for x, y, width, depth in ground.cutouts:
                    across = ground.x <= x and x + width <= ground.x + ground.width
                    self.assertTrue(across and ground.y <= y and y + depth <= ground.y + ground.depth, ground.name)

    def test_gates_are_on_their_fences(self):
        for h in self.each():
            for fence in h.details.fences:
                for gx, gy, width in fence.gates:
                    near = []
                    for (x1, y1), (x2, y2) in zip(fence.points, fence.points[1:]):
                        length = math.hypot(x2 - x1, y2 - y1)
                        t = ((gx - x1) * (x2 - x1) + (gy - y1) * (y2 - y1)) / length
                        off = abs((gx - x1) * (y2 - y1) - (gy - y1) * (x2 - x1)) / length
                        near.append(off < 0.5 and width / 2 <= t <= length - width / 2)
                    self.assertTrue(any(near), fence.name)

    def test_fences_run_in_the_lot_and_around_the_house(self):
        for h in self.each():
            for fence in h.details.fences:
                self.assertGreaterEqual(len(fence.points), 2, fence.name)
                for (x1, y1), (x2, y2) in zip(fence.points, fence.points[1:]):
                    for k in range(11):
                        x, y = x1 + (x2 - x1) * k / 10, y1 + (y2 - y1) * k / 10
                        self.assertTrue(h.in_lot(x, y, slack=0.5), (fence.name, x, y))
                        self.assertFalse(h.on_house(x, y), (fence.name, x, y))

    def test_the_lowest_floor_is_below_the_ground_and_the_main_floor_above_it(self):
        for h in self.each():
            lowest = min(p.elevation for _, p in h.pieces)
            self.assertTrue(lowest <= h.details.grade < 0)


class StepsTest(HouseTestCase):
    def test_steps_outside_are_in_the_lot_off_the_house(self):
        for h in self.each():
            for steps in (s for s in h.details.steps if not s.room):
                self.assertTrue(all(h.in_lot(x, y) for x, y in corners(rect(steps))), steps.name)
                self.assertFalse(squares(rect(steps)) & h.footprint, steps.name)
                self.assertEqual(steps.bottom, h.details.grade, steps.name)

    def test_steps_in_a_room_start_on_its_floor(self):
        for h in self.each():
            for steps in (s for s in h.details.steps if s.room):
                p = h.first(steps.room)
                x, y, width, depth = rect(steps)
                self.assertTrue(p.x <= x and x + width <= p.x + p.width and p.y <= y and y + depth <= p.y + p.depth, steps.name)
                self.assertEqual(steps.bottom, p.elevation, steps.name)

    def test_steps_climb(self):
        for h in self.each():
            for steps in h.details.steps:
                self.assertGreater(steps.top, steps.bottom, steps.name)


class RoofTest(HouseTestCase):
    def test_roofs_cover_the_house_down_to_its_walls(self):
        """Every room with nothing over it is under a roof, the lowest of which comes down to its ceiling."""
        for h in self.each():
            for q, p in h.pieces:
                if any(o.elevation > p.elevation and overlap(rect(o), rect(p)) for _, o in h.pieces):
                    continue
                for x, y in squares(rect(p)):
                    roofs = [r for r in h.details.roofs if on_roof(r, x, y)]
                    self.assertTrue(roofs, (q.name, x, y))
                    undersides = [r.eave - 0.6 if r.underside is None else r.underside for r in roofs]
                    self.assertLessEqual(min(undersides), p.elevation + ceiling(p, x, y) + 1e-6, (q.name, x, y))

    def test_roof_vents_stand_on_a_roof(self):
        for h in self.each():
            for vent in h.details.roof_vents:
                self.assertTrue([r for r in h.details.roofs if on_roof(r, vent.x, vent.y)], vent.name)

    def test_outlines_are_in_their_rectangles(self):
        for h in self.each():
            for roof in (r for r in h.details.roofs if r.outline):
                for x, y in roof.outline:
                    self.assertTrue(roof.x <= x <= roof.x + roof.width and roof.y <= y <= roof.y + roof.depth, roof.name)


class KindsTest(unittest.TestCase):
    def test_kinds_and_sides_are_checked(self):
        with self.assertRaises(ValueError):
            Opening("Hatch", "trapdoor", "Office", "left", 0, 2, 2)
        with self.assertRaises(ValueError):
            Closet("Closet", "Office", 0, 0, 2, 2, door="up")
        with self.assertRaises(ValueError):
            Fixture("Fire pit", "fireplace", None, 0, 0, 3, 3, 1)

    def test_a_roof_needs_a_ridge_or_a_drop(self):
        with self.assertRaises(ValueError):
            Roof("Flat", 0, 0, 10, 10, eave=9, peak=9)
        with self.assertRaises(ValueError):
            Roof("Both", 0, 0, 10, 10, eave=9, peak=12, ridge="x", drop="left")


class LoadTest(unittest.TestCase):
    def test_loads_a_house_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "house.py"
            path.write_text("from housemon.example_house import AREAS, DETAILS\nHOUSE = ()\n")
            house = houses.load(path)
            self.assertEqual(house.HOUSE, ())
            self.assertIs(house.DETAILS, example_house.DETAILS)

    def test_without_one_it_loads_the_private_house_or_the_example(self):
        expected = houses.PRIVATE if houses.PRIVATE.exists() else Path(example_house.__file__)
        self.assertEqual(Path(houses.load().__file__).resolve(), expected.resolve())


if __name__ == "__main__":
    unittest.main()
