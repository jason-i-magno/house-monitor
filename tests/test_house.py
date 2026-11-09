"""Checks every house file must pass: the example house, and your own in private/ if it's there.

Each test runs once per house, as a subtest named for it.
"""

import contextlib
import io
import itertools
import math
import os
import tempfile
import unittest
from pathlib import Path

from housemon import example_house
from housemon import house as houses
from housemon import layout
from housemon.details import Closet, Fence, Fixture, Ground, Opening, Roof, Sprinklers, Steps, Tree

WALL = 0.4  # how far things in a room stay from its plan's edges: a wall's thickness, and a bit; half that from a
# side where half the wall stands, and none where it stands past the side, in the place beyond (layout.py)
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


def covered(thing):
    """The half-foot squares a thing covers: its rectangle's, or those within its outline, if it has one."""
    outline = getattr(thing, "outline", None)
    return {(x, y) for x, y in squares(rect(thing)) if not outline or in_polygon(x, y, list(outline))}


def overlap(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def within_rect(x, y, area):
    ax, ay, width, depth = area
    return ax <= x <= ax + width and ay <= y <= ay + depth


def closet_door(h, closet, opening):
    """Whether an opening is the door of a closet set into its room's wall: a door in that wall, across the closet."""
    if opening.room != closet.room or opening.wall != closet.door or opening.kind != "door":
        return False
    x, y, width, depth = h.absolute(closet)
    _, axis, line, a, b, _ = h.wall(opening)
    edge = {"back": y, "front": y + depth, "left": x, "right": x + width}[closet.door]
    lo, hi = (x, x + width) if axis == "x" else (y, y + depth)
    return abs(edge - line) < 1e-6 and lo <= a and b <= hi


def banisters(steps):
    """The rectangles of a flight's banisters, just outside it along the sides it lists."""
    x, y, width, depth, b = steps.x, steps.y, steps.width, steps.depth, steps.banister
    sides = {"back": (x, y - b, width, b), "front": (x, y + depth, width, b), "left": (x - b, y, b, depth), "right": (x + width, y, b, depth)}
    return [sides[side] for side in steps.banisters]


def corners(area):
    x, y, width, depth = area
    return [(x, y), (x + width, y), (x, y + depth), (x + width, y + depth)]


def in_polygon(x, y, points):
    hit = False
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def stands_on(x, y, thing):
    """Whether a point on the plan is on a ground or shed: in its rectangle and any outline, but not in a cutout."""
    if any(within_rect(x, y, c) for c in getattr(thing, "cutouts", ())):
        return False
    outline = getattr(thing, "outline", None)
    return within_rect(x, y, rect(thing)) and (not outline or in_polygon(x, y, list(outline)))


def on_roof(r, x, y):
    in_rect = r.x < x < r.x + r.width and r.y < y < r.y + r.depth
    return in_rect and (r.outline is None or in_polygon(x, y, list(r.outline)))


class Layout:
    """A house file's places and details, and where things are in it."""

    def __init__(self, name, module):
        self.name = name
        self.rooms = module.HOUSE
        self.places = [room.sensor for room in module.HOUSE if room.sensor.plan] + list(module.AREAS)
        self.pieces = [(place, plan) for place in self.places for plan in pieces(place.plan)]
        # Its walls as the detailed view stands them, and its details with what's placed against them resolved, as the
        # server sends them.
        self.walls = layout.house_layout([room.sensor for room in module.HOUSE], module.AREAS, module.DETAILS)
        self.share = self.walls.share
        self.details = self.walls.resolve(module.DETAILS)
        # The openings in the house's walls; the rest are in a shed's, naming it.
        self.openings = [o for o in self.details.openings if o.room not in {s.name for s in self.details.sheds}]
        self.footprint = set().union(*(squares(rect(p)) for _, p in self.pieces))

    def place(self, name):
        (match,) = [p for p in self.places if p.name == name]  # one place, so it's clear which is meant
        return match

    def first(self, name):
        return pieces(self.place(name).plan)[0]

    def piece(self, thing):
        """The piece of its room a closet or fixture stands in: the one its rectangle is in, else the first."""
        x, y, width, depth = self.absolute(thing)
        for p in pieces(self.place(thing.room).plan):
            if p.x - 1e-9 <= x and x + width <= p.x + p.width + 1e-9 and p.y - 1e-9 <= y and y + depth <= p.y + p.depth + 1e-9:
                return p
        return self.first(thing.room)

    def parts(self, thing):
        """A closet's or fixture's rectangle cut by the pieces of its room it stands across: each piece and the part
        of the rectangle in it, or its own piece and all of it, for one with no area."""
        x, y, width, depth = self.absolute(thing)
        cut = []
        for p in pieces(self.place(thing.room).plan):
            x0, y0, x1, y1 = max(x, p.x), max(y, p.y), min(x + width, p.x + p.width), min(y + depth, p.y + p.depth)
            if x1 - x0 > 1e-9 and y1 - y0 > 1e-9:
                cut.append((p, (x0, y0, x1 - x0, y1 - y0)))
        return cut or [(self.piece(thing), (x, y, width, depth))]

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

    def clearance(self, place, p, side, span, heights):
        """How far a thing in a place's piece p stays from a side, along span and over heights: WALL where the wall on
        that side stands in its plan, half that where half of it does, and none where it stands past the side, or an
        open wall, or another piece of its place carrying on past it, leaves no wall there."""
        if self.open(p, side, span, heights) or self.adjoined(place, p, side, span):
            return 0.0
        return WALL * self.share.get((id(p), side), 1)

    def adjoined(self, place, p, side, span):
        """Whether other pieces of a place carry on past a side of its piece p all along span, leaving no wall."""
        line = {"left": p.x, "right": p.x + p.width, "back": p.y, "front": p.y + p.depth}[side]
        left = [span]
        for o in pieces(place.plan):
            if o is p:
                continue
            if side in ("left", "right"):
                beyond, lo, hi = (o.x + o.width if side == "left" else o.x), o.y, o.y + o.depth
            else:
                beyond, lo, hi = (o.y + o.depth if side == "back" else o.y), o.x, o.x + o.width
            if abs(beyond - line) < 1e-6:
                left = [(u, v) for a, b in left for u, v in ((a, min(b, lo)), (max(a, hi), b)) if v - u > 1e-6]
        return not left

    def open(self, p, side, span, heights):
        """Whether an opening with no door, an open wall, leaves no wall on a piece's side along span and over heights."""
        (lo, hi), (z0, z1) = span, heights
        axis = "y" if side in ("left", "right") else "x"
        line = {"left": p.x, "right": p.x + p.width, "back": p.y, "front": p.y + p.depth}[side]
        for o in (o for o in self.openings if o.kind == "opening"):
            op, o_axis, o_line, a, b, _ = self.wall(o)
            bottom = op.elevation + o.sill
            if o_axis == axis and abs(o_line - line) < 1e-6 and a <= lo + 1e-6 and hi <= b + 1e-6 and bottom <= z0 + 1e-6 and z1 <= bottom + o.height + 1e-6:
                return True
        return False

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
            names = [o.room for o in h.openings] + [c.room for c in d.closets] + [c.room for c in d.claddings]
            names += [f.room for f in d.fixtures if f.room] + [s.room for s in d.steps if s.room]
            for name in names:
                h.place(name)  # exactly one
            self.assertLessEqual(set(d.finishes), {q.name for q in h.places})
            for name in {s.name for s in d.sheds}:
                self.assertEqual([s.name for s in d.sheds].count(name), 1, name)
                self.assertFalse(any(q.name == name for q in h.places), name)


class OpeningTest(HouseTestCase):
    def test_fits_in_its_wall(self):
        for h in self.each():
            for opening in h.openings:
                p, _, _, a, b, length = h.wall(opening)
                self.assertTrue(0 <= opening.offset and opening.offset + opening.width <= length, opening.name)
                if opening.wall in ("left", "right"):
                    line = p.x if opening.wall == "left" else p.x + p.width
                    ceilings = [ceiling(p, line, s) for s in (a, b)]
                else:
                    line = p.y if opening.wall == "back" else p.y + p.depth
                    ceilings = [ceiling(p, s, line) for s in (a, b)]
                self.assertTrue(0 <= opening.sill and opening.sill + opening.height <= min(ceilings), opening.name)

    def test_openings_clear_the_walls_meeting_theirs(self):
        """A door, window or vent stays clear of the walls meeting its wall: its room's back and front walls, which run
        past a left or right wall's ends, and the walls of a place beyond meeting it from the other side, half of a
        wall shared between them standing on that side. One running into them would show through their tops where a
        level is cut away."""
        for h in self.each():
            for opening in (o for o in h.openings if o.kind != "opening"):
                p, axis, line, a, b, length = h.wall(opening)
                place = h.place(opening.room)
                heights = (p.elevation + opening.sill, p.elevation + opening.sill + opening.height)
                if opening.wall in ("left", "right"):
                    across = (line, line + WALL) if opening.wall == "left" else (line - WALL, line)  # in its room, as they meet
                    back = h.clearance(place, p, "back", across, heights) if opening.offset < WALL else WALL
                    front = h.clearance(place, p, "front", across, heights) if opening.offset + opening.width > length - WALL else WALL
                    self.assertTrue(back <= opening.offset and opening.offset + opening.width <= length - front, opening.name)
                # Where a piece of a place beyond the wall starts or ends along it, the walls there meet it: as much of
                # them as stands in it and past it, none where they're open.
                out = -0.01 if opening.wall in ("left", "back") else 0.01
                near = (line - WALL, line) if out < 0 else (line, line + WALL)  # the stretch of them meeting it
                for q, o in h.pieces:
                    if q.name == place.name:
                        continue
                    lo, hi = (o.y, o.y + o.depth) if axis == "y" else (o.x, o.x + o.width)
                    across = (o.x < line + out < o.x + o.width) if axis == "y" else (o.y < line + out < o.y + o.depth)
                    over = o.elevation < heights[1] and heights[0] < o.elevation + o.height
                    sides = ("left", "right") if axis == "x" else ("back", "front")  # the walls across this one
                    for edge, side, inward in ((lo, sides[0], 1), (hi, sides[1], -1)):
                        if h.open(o, side, near, heights):
                            continue
                        share = h.share.get((id(o), side), 1)
                        start, end = sorted((edge - inward * WALL * (1 - share), edge + inward * WALL * share))
                        if end - start < 1e-9:
                            continue
                        if across and over and start < b and a < end:
                            self.assertTrue(b <= start + 1e-9 or a >= end - 1e-9, (opening.name, q.name))

    def test_windows_open_to_the_outside(self):
        for h in self.each():
            for opening in (o for o in h.openings if o.kind == "window"):
                for x, y, z in h.beyond(opening):
                    hit = [q.name for q, p in h.pieces if inside(x, y, z, p)]
                    self.assertEqual(hit, [], opening.name)

    def test_doors_lead_outside_or_into_one_place_at_their_sill(self):
        """A door's sill is the floor beyond it, or a little above it, as a crawlspace's hatch is."""
        for h in self.each():
            for opening in (o for o in h.openings if o.kind in DOORS):
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

            def face(opening):
                """Which of its wall's faces a vent's grille is on; a door or window goes through, so both."""
                return {"left": 1, "back": 1, "right": -1, "front": -1}[opening.wall] if opening.kind == "vent" else 0

            for one, other in itertools.combinations(h.openings, 2):
                (axis, line, a, b, z0, z1), (axis2, line2, a2, b2, z02, z12) = extent(one), extent(other)
                same_wall = axis == axis2 and abs(line - line2) < 1e-6
                back_to_back = face(one) * face(other) < 0  # two grilles on its two faces, as a return through it
                overlap = a < b2 and a2 < b and z0 < z12 and z02 < z1
                self.assertFalse(same_wall and overlap and not back_to_back, (one.name, other.name))


class ClosetTest(HouseTestCase):
    def test_sits_in_its_room_with_its_doors_facing_in_or_set_into_its_wall(self):
        for h in self.each():
            for closet in h.details.closets:
                place, parts = h.place(closet.room), h.parts(closet)
                x, y, width, depth = h.absolute(closet)
                # In one of its room's pieces, or across them.
                self.assertAlmostEqual(sum(w * d for _, (_, _, w, d) in parts), width * depth, msg=closet.name)
                # Its doors' side on a wall of its room: a piece's side with no other piece of it carrying on past.
                edge = {"left": x, "right": x + width, "back": y, "front": y + depth}[closet.door]
                on_wall = False
                for p, (px, py, pw, pd) in parts:
                    line = {"left": p.x, "right": p.x + p.width, "back": p.y, "front": p.y + p.depth}[closet.door]
                    span = (py, py + pd) if closet.door in ("left", "right") else (px, px + pw)
                    on_wall |= abs(edge - line) < 1e-6 and not h.adjoined(place, p, closet.door, span)
                set_in = any(closet_door(h, closet, o) for o in h.openings)
                self.assertFalse(on_wall and not set_in, closet.name)

    def test_closets_do_not_overlap_or_cover_a_door_or_window(self):
        for h in self.each():
            for one, other in itertools.combinations(h.details.closets, 2):
                if h.place(one.room) is h.place(other.room):
                    self.assertFalse(overlap(h.absolute(one), h.absolute(other)), (one.name, other.name))
            for closet, opening in itertools.product(h.details.closets, h.openings):
                if opening.kind == "vent" or closet_door(h, closet, opening):
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
                place, parts = h.place(fixture.room), h.parts(fixture)
                _, _, width, depth = h.absolute(fixture)
                # In one of its room's pieces, or across them, each part clear of its piece's walls, but right at the
                # face of one it stands against.
                self.assertAlmostEqual(sum(w * d for _, (_, _, w, d) in parts), width * depth, msg=fixture.name)
                for p, (x, y, w, d) in parts:
                    heights = (p.elevation + fixture.lift, p.elevation + fixture.lift + max(fixture.height, 0.1))
                    flush = lambda side, span: side in fixture.against and not h.adjoined(place, p, side, span)
                    clear = lambda side, span: h.walls.inward(p, side) if flush(side, span) else h.clearance(place, p, side, span, heights)
                    left, right = (clear(side, (y, y + d)) for side in ("left", "right"))
                    back, front = (clear(side, (x, x + w)) for side in ("back", "front"))
                    self.assertTrue(p.x + left <= x + 1e-9 and x + w <= p.x + p.width - right + 1e-9, fixture.name)
                    self.assertTrue(p.y + back <= y + 1e-9 and y + d <= p.y + p.depth - front + 1e-9, fixture.name)
                    lowest = min(ceiling(p, x + u * w, y + v * d) for u in (0, 1) for v in (0, 1))
                    self.assertLessEqual(fixture.lift + fixture.height, lowest, fixture.name)

    def test_fireplaces_and_half_walls_stand_against_a_wall(self):
        for h in self.each():
            for fireplace in (f for f in h.details.fixtures if f.kind in ("fireplace", "half wall")):
                p = h.piece(fireplace)
                x, y, width, depth = h.absolute(fireplace)
                gaps = (x - p.x, y - p.y, p.x + p.width - x - width, p.y + p.depth - y - depth)
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
        """The floor 2 ft either side of each door is clear of fixtures and closets, a closet's own doors included,
        which stand half a foot in from its ends, inside its walls or its room's. An opening, a doorway with no door
        or an open wall, needs only a way through it: 2.5 ft of it clear."""
        for h in self.each():
            clear = []
            for opening in (o for o in h.openings if o.kind in DOORS):
                p, axis, line, a, b, _ = h.wall(opening)
                strip = (a, line - 2, b - a, 4) if axis == "x" else (line - 2, a, 4, b - a)
                owner = next((c for c in h.details.closets if closet_door(h, c, opening)), None)  # a closet's own door
                way = 2.5 if opening.kind == "opening" else None
                clear.append((opening.name, owner, p.elevation, opening.sill + opening.height, strip, axis, way))
            for closet in h.details.closets:
                x, y, width, depth = h.absolute(closet)
                e = WALL / 2 + 0.3  # its doors' ends from its own: a wall, half a shared one at least, then a frame
                strip = {
                    "left": (x - 2, y + e, 2, depth - 2 * e),
                    "right": (x + width, y + e, 2, depth - 2 * e),
                    "back": (x + e, y - 2, width - 2 * e, 2),
                    "front": (x + e, y + depth, width - 2 * e, 2),
                }[closet.door]
                clear.append((closet.name, closet, h.first(closet.room).elevation, 6.67, strip, None, None))
            for name, owner, floor, top, strip, axis, way in clear:
                fixtures = [f for f in h.details.fixtures if f.room and h.first(f.room).elevation == floor and f.lift < top]
                closets = [c for c in h.details.closets if c is not owner and h.first(c.room).elevation == floor]
                blocking = [(t.name, h.absolute(t)) for t in fixtures + closets if overlap(strip, h.absolute(t))]
                if way is None:
                    self.assertFalse(blocking, (name, [n for n, _ in blocking]))
                    continue
                # The stretches of the opening nothing stands in front of, along its wall.
                lo, hi = (strip[0], strip[0] + strip[2]) if axis == "x" else (strip[1], strip[1] + strip[3])
                spans = sorted((r[0], r[0] + r[2]) if axis == "x" else (r[1], r[1] + r[3]) for _, r in blocking)
                widest, at = 0.0, lo
                for s0, s1 in spans:
                    widest, at = max(widest, s0 - at), max(at, s1)
                self.assertGreaterEqual(max(widest, hi - at), way, (name, [n for n, _ in blocking]))


class YardTest(HouseTestCase):
    def test_the_house_is_in_the_lot(self):
        for h in self.each():
            for place, p in h.pieces:
                self.assertTrue(all(h.in_lot(x, y) for x, y in corners(rect(p))), place.name)

    def test_things_outside_are_in_the_lot_off_the_house_and_apart(self):
        for h in self.each():
            d = h.details
            # A ground may run past the lot's edge, as a driveway does to the street, but starts in it; one with
            # an outline stays in it.
            for g in d.grounds:
                if g.outline:
                    self.assertTrue(all(h.in_lot(x, y, slack=0.5) for x, y in g.outline), g.name)
                else:
                    self.assertTrue(h.in_lot(g.x + g.width / 2, g.y + g.depth / 2), g.name)
            built = [(f.name, rect(f)) for f in d.fixtures if not f.room] + [(s.name, rect(s)) for s in d.sheds]
            for name, area in built:
                self.assertTrue(all(h.in_lot(x, y) for x, y in corners(area)), name)
            grounds = [(g.name, covered(g)) for g in d.grounds]
            for name, cover in grounds + [(name, squares(area)) for name, area in built]:
                self.assertFalse(cover & h.footprint, name)
            sheds = [(s.name, squares(rect(s))) for s in d.sheds]
            for (name, cover), (other, cover2) in itertools.combinations(grounds + sheds, 2):
                self.assertFalse(cover & cover2, (name, other))

    def test_trees_stand_in_the_lawn_or_a_cutout(self):
        for h in self.each():
            for tree in h.details.trees:
                self.assertTrue(h.in_lot(tree.x, tree.y), tree.name)
                self.assertFalse(h.on_house(tree.x, tree.y), tree.name)
                for thing in (*h.details.grounds, *h.details.sheds):
                    self.assertFalse(stands_on(tree.x, tree.y, thing), (tree.name, thing.name))

    def test_sprinklers_and_valve_boxes_are_in_the_lawn_or_a_bed(self):
        """Not on paving, a shed or the house, and no two heads in one spot."""
        for h in self.each():
            d = h.details
            spots = [(z.name, x, y) for z in d.sprinklers for x, y in z.heads]
            spots += [(f.name, f.x + f.width / 2, f.y + f.depth / 2) for f in d.fixtures if f.kind == "valve box"]
            for name, x, y in spots:
                self.assertTrue(h.in_lot(x, y), (name, x, y))
                self.assertFalse(h.on_house(x, y), (name, x, y))
                for thing in (*(g for g in d.grounds if g.kind != "bed"), *d.sheds):
                    self.assertFalse(stands_on(x, y, thing), (name, x, y, thing.name))
            heads = [(z.name, x, y) for z in d.sprinklers for x, y in z.heads]
            for (name, x1, y1), (other, x2, y2) in itertools.combinations(heads, 2):
                self.assertGreater(math.hypot(x2 - x1, y2 - y1), 0.5, (name, x1, y1, other))

    def test_outlines_are_in_their_grounds(self):
        for h in self.each():
            for ground in (g for g in h.details.grounds if g.outline):
                for x, y in ground.outline:
                    self.assertTrue(within_rect(x, y, rect(ground)), ground.name)

    def test_cutouts_are_in_their_grounds(self):
        for h in self.each():
            for ground in h.details.grounds:
                for x, y, width, depth in ground.cutouts:
                    across = ground.x <= x and x + width <= ground.x + ground.width
                    self.assertTrue(across and ground.y <= y and y + depth <= ground.y + ground.depth, ground.name)

    def test_a_sheds_doors_and_windows_fit_its_walls_apart(self):
        """Up to its eaves, from the ground."""
        for h in self.each():
            for shed in h.details.sheds:
                openings = [o for o in h.details.openings if o.room == shed.name]
                for o in openings:
                    length = shed.width if o.wall in ("back", "front") else shed.depth
                    self.assertTrue(0 <= o.offset and o.offset + o.width <= length, o.name)
                    self.assertTrue(0 <= o.sill and o.sill + o.height <= shed.wall, o.name)
                for one, other in itertools.combinations(openings, 2):
                    along = one.offset < other.offset + other.width and other.offset < one.offset + one.width
                    up = one.sill < other.sill + other.height and other.sill < one.sill + one.height
                    self.assertFalse(one.wall == other.wall and along and up, (one.name, other.name))

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

    def test_steps_in_a_room_are_in_a_piece_of_it_and_start_on_its_floor(self):
        for h in self.each():
            for steps in (s for s in h.details.steps if s.room):
                x, y, width, depth = rect(steps)
                fits = [p for p in pieces(h.place(steps.room).plan) if within_rect(x, y, rect(p)) and within_rect(x + width, y + depth, rect(p))]
                self.assertTrue(fits, steps.name)
                self.assertEqual(steps.bottom, fits[0].elevation, steps.name)

    def test_steps_climb(self):
        for h in self.each():
            for steps in h.details.steps:
                self.assertGreater(steps.top, steps.bottom, steps.name)

    def test_banisters_stand_in_their_flights_room(self):
        for h in self.each():
            for steps in (s for s in h.details.steps if s.room):
                for x, y, width, depth in banisters(steps):
                    fits = [p for p in pieces(h.place(steps.room).plan) if within_rect(x, y, rect(p)) and within_rect(x + width, y + depth, rect(p))]
                    self.assertTrue(fits, steps.name)

    def test_nothing_stands_on_a_flight_or_its_banisters(self):
        """No fixture or closet stands on a flight of steps, or its banisters, from its bottom to 3 ft over its top."""
        for h in self.each():
            for steps in (s for s in h.details.steps if s.room):
                spots = [rect(steps), *banisters(steps)]
                things = [t for t in h.details.fixtures if t.room] + list(h.details.closets)
                for thing in things:
                    p = h.piece(thing)
                    z0 = p.elevation + getattr(thing, "lift", 0)
                    z1 = z0 + getattr(thing, "height", 8)
                    if z1 <= steps.bottom or steps.top + 3 <= z0:
                        continue
                    self.assertFalse(any(overlap(spot, h.absolute(thing)) for spot in spots), (steps.name, thing.name))


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
        with self.assertRaises(ValueError):
            Fixture("Garden wall", "half wall", None, 0, 0, 6, 0.5, 3)
        with self.assertRaises(ValueError):
            Fixture("Valve box", "valve box", "Garage", 0, 0, 1.4, 1, 0.05)  # sunk in the ground outside
        with self.assertRaises(ValueError):
            Sprinklers("Zone 1", ((0, 0),), kind="drip")
        with self.assertRaises(ValueError):
            Sprinklers("Zone 1", ())
        with self.assertRaises(ValueError):
            Fence("Fence", ((0, 0), (10, 0)), kind="stone")
        with self.assertRaises(ValueError):
            Ground("Bed", "bed", 0, 0, 10, 10, outline=((0, 0), (10, 0), (5, 10)), cutouts=((4, 1, 2, 2),))
        with self.assertRaises(ValueError):
            Ground("Bed", "bed", 0, 0, 10, 3, height=1, edging="wood")
        with self.assertRaises(ValueError):
            Ground("Bed", "bed", 0, 0, 10, 3, edging="brick")  # edging holds in a raised one
        with self.assertRaises(ValueError):
            Tree("Tree", "deciduous", 0, 0, height=30, spread=20, ring=3, edging="wood")
        Tree("Tree", "deciduous", 0, 0, height=30, spread=20, ring=3, edging=None)  # a flat ring of mulch
        for wrong in ({"crown": "cone"}, {"leaves": "red"}, {"bark": "black"}, {"stems": 0}, {"stems": 2.5}):
            with self.assertRaises(ValueError, msg=wrong):
                Tree("Tree", "deciduous", 0, 0, height=30, spread=20, **wrong)
        with self.assertRaises(ValueError):
            Tree("Tree", "deciduous", 0, 0, height=30, spread=20, fork=15)  # up in its crown
        with self.assertRaises(ValueError):
            Tree("Tree", "deciduous", 0, 0, height=30, spread=20, trunk=10)
        Tree("Tree", "deciduous", 0, 0, height=30, spread=20, crown="pyramid", clearance=15, lean=(-2, 3), fork=6)
        for wrong in ({"clearance": 6}, {"clearance": 20}, {"lean": (5, 0)}, {"lean": (1,)}):
            with self.assertRaises(ValueError, msg=wrong):  # below its fork, too high, off its crown, not x and y
                Tree("Tree", "deciduous", 0, 0, height=30, spread=20, fork=6, **wrong)
        for wrong in ({"stems": 2}, {"clearance": 10}, {"lean": (1, 0)}):
            with self.assertRaises(ValueError, msg=wrong):
                Tree("Spruce", "evergreen", 0, 0, height=30, spread=12, **wrong)
        with self.assertRaises(ValueError):
            Ground("Deck", "deck", 0, 0, 10, 10, height=1, railings=("up",))
        with self.assertRaises(ValueError):
            Steps("Steps", 0, 0, 3, 3, bottom=0, top=1, up="back", count=0)
        with self.assertRaises(ValueError):
            Steps("Steps", 0, 0, 3, 3, bottom=0, top=1, up="back", banisters=("front",))  # across the climb

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

    def test_a_house_file_loads_again_when_it_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "house.py"
            path.write_text("from housemon.example_house import AREAS, DETAILS, HOUSE\n")
            house = houses.HouseFile(path)
            self.assertIsNone(house.changed())  # not since it loaded
            path.write_text("from housemon.example_house import AREAS, DETAILS, HOUSE\nHOUSE = HOUSE[:2]\n")
            os.utime(path, (house.mtime + 1, house.mtime + 1))
            self.assertEqual(len(house.changed().HOUSE), 2)
            self.assertIsNone(house.changed())

    def test_a_house_file_that_does_not_load_leaves_the_last(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "house.py"
            path.write_text("from housemon.example_house import AREAS, DETAILS, HOUSE\n")
            house = houses.HouseFile(path)
            path.write_text("HOUSE = (\n")  # half edited
            os.utime(path, (house.mtime + 1, house.mtime + 1))
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertIsNone(house.changed())
            self.assertIn("didn't load", err.getvalue())
            self.assertIs(house.module.HOUSE, example_house.HOUSE)


if __name__ == "__main__":
    unittest.main()
