"""Where a house's walls stand, and so where things placed against them go.

Every wall is WALL thick, and a run of walls in line, of the places either side of
a line end to end at the same heights, is one straight wall standing wholly on one
side of it, so each side's faces are flat. It stands on the side facing the outside
beside its rooms, above the ground, as a house's outer walls stand inside its plan;
where both sides do, on the higher floor's, as a taller part of a house stands its
own outer walls and lower parts are built against them, so a lower part's outer
walls in line with it stand just outside its plan; where neither does, on the side
facing the outside at all, as over a lower room's ceiling; else half on each.

The server sends each piece's share of its walls with the house, for the map's
detailed view to stand them, and resolves the things a house file places against
a wall or from wall to wall; the house tests check things against the same walls.
"""

import itertools
from dataclasses import replace

from .details import Details

WALL = 0.33  # feet: every wall's thickness
SIDES = ("back", "front", "left", "right")


def pieces(plan):
    """A room's or area's plans: its one, or each of its pieces'."""
    return plan if isinstance(plan, tuple) else (plan,)


def top(p, x, y):
    """How high a piece's ceiling is at x, y above the main floor, following its drop."""
    if not p.drop:
        return p.elevation + p.height
    side, feet = p.drop
    across = {
        "left": (p.x + p.width - x) / p.width,
        "right": (x - p.x) / p.width,
        "back": (p.y + p.depth - y) / p.depth,
        "front": (y - p.y) / p.depth,
    }[side]
    return p.elevation + p.height - feet * across


def side_line(p, side):
    """A side of a piece as a line: the axis it runs along, where it is across that axis, which way is inward, and
    where it starts and ends."""
    return {
        "back": ("x", p.y, 1, p.x, p.x + p.width),
        "front": ("x", p.y + p.depth, -1, p.x, p.x + p.width),
        "left": ("y", p.x, 1, p.y, p.y + p.depth),
        "right": ("y", p.x + p.width, -1, p.y, p.y + p.depth),
    }[side]


def point(axis, line, s, d):
    """The point d feet across a line from it, toward greater x or y, at s along it, as x, y."""
    return (s, line + d) if axis == "x" else (line + d, s)


def _overlap(a, b):
    return a.x < b.x + b.width and b.x < a.x + a.width and a.y < b.y + b.depth and b.y < a.y + a.depth


class HouseLayout:
    """A house's places (its rooms with plans and its areas, each with a name and plan) and the walls between them.

    share[(id(piece), side)] is how much of the wall on that side of a piece stands in its plan: 1, all of it; 0,
    none, standing just past it, in the place beyond or outside; 0.5, half.
    """

    def __init__(self, places, grade: float):
        self.places = list(places)
        self.grade = grade
        self.pieces = [(place, p) for place in self.places for p in pieces(place.plan)]
        # Each piece's base, where its walls start: the top of whatever's under it, or its foundation's footing.
        self.bases = {}
        for place, p in self.pieces:
            under = [b.elevation + b.height for q, b in self.pieces if q is not place and _overlap(b, p) and b.elevation + b.height <= p.elevation + 1e-6]
            self.bases[id(p)] = max(under) if under else min(p.elevation - 1, grade - 0.5)
        self.share = self._shares()

    def walls(self, place, p, side):
        """The stretches of a piece's side with walls: all of it but where another piece of its place adjoins it."""
        axis, line, k, a, b = side_line(p, side)
        spans = [(a, b)]
        for o in pieces(place.plan):
            if o is p:
                continue
            lo, hi = (o.x, o.x + o.width) if axis == "x" else (o.y, o.y + o.depth)
            across_lo, across_hi = (o.y, o.y + o.depth) if axis == "x" else (o.x, o.x + o.width)
            if across_lo < line - k * 0.005 < across_hi:  # just past the side
                spans = [part for u, v in spans for part in ((u, min(v, lo)), (max(u, hi), v)) if part[1] - part[0] > 1e-6]
        return spans

    def occupied(self, x, y, z, place):
        """Whether another place's piece is at x, y, z, from its base up to its ceiling, as its walls stand."""
        return any(
            q is not place and b.x < x < b.x + b.width and b.y < y < b.y + b.depth and self.bases[id(b)] - 1e-6 <= z <= top(b, x, y) + 1e-6
            for q, b in self.pieces
        )

    def inward(self, p, side):
        """How far a piece's wall on a side stands into its plan."""
        return WALL * self.share.get((id(p), side), 1)

    def sides(self, p):
        """A piece's share of its wall on each side."""
        return {side: self.share.get((id(p), side), 1) for side in SIDES}

    def _cells(self, place, p, side):
        """The stretches of a piece's walls on a side between the edges of the other places' pieces along it."""
        axis = side_line(p, side)[0]
        edges = [e for q, o in self.pieces if q is not place for e in ((o.x, o.x + o.width) if axis == "x" else (o.y, o.y + o.depth))]
        for a, b in self.walls(place, p, side):
            cuts = sorted({a, b, *(e for e in edges if a < e < b)})
            yield from zip(cuts, cuts[1:])

    def _beside_outside(self, place, p, side):
        """How far along a piece's walls on a side the outside is beyond them beside it, 2 ft over its floor, above the
        ground."""
        axis, line, k, _, _ = side_line(p, side)
        z = p.elevation + 2
        if z <= self.grade or z < self.bases[id(p)]:
            return 0.0
        return sum(s1 - s0 for s0, s1 in self._cells(place, p, side) if not self.occupied(*point(axis, line, (s0 + s1) / 2, -k * 0.01), z, place))

    def _faces_outside(self, place, p, side):
        """Whether the outside is beyond any of a piece's walls on a side, along them or up them."""
        axis, line, k, _, _ = side_line(p, side)
        base = self.bases[id(p)]
        for s0, s1 in self._cells(place, p, side):
            x, y = point(axis, line, (s0 + s1) / 2, -k * 0.01)
            high = top(p, *point(axis, line, (s0 + s1) / 2, 0))
            heights = {base, high}
            for q, o in self.pieces:
                if q is not place and o.x < x < o.x + o.width and o.y < y < o.y + o.depth:
                    heights |= {self.bases[id(o)], top(o, x, y)}
            zs = sorted(z for z in heights if base - 1e-9 <= z <= high + 1e-9)
            if any(z1 - z0 > 1e-6 and not self.occupied(x, y, (z0 + z1) / 2, place) for z0, z1 in zip(zs, zs[1:])):
                return True
        return False

    def _shares(self):
        runs = [(place, p, side) for place, p in self.pieces for side in SIDES if self.walls(place, p, side)]
        root = list(range(len(runs)))

        def find(i):
            while root[i] != i:
                i = root[i]
            return i

        for i, j in itertools.combinations(range(len(runs)), 2):
            (place, p, side), (other, o, its) = runs[i], runs[j]
            (axis, line, *_), (axis2, line2, *_) = side_line(p, side), side_line(o, its)
            if axis != axis2 or abs(line - line2) > 1e-6:
                continue
            touch = any(a <= d + 1e-6 and c <= b + 1e-6 for a, b in self.walls(place, p, side) for c, d in self.walls(other, o, its))
            if touch and max(p.elevation, o.elevation) < min(p.elevation + p.height, o.elevation + o.height) - 1e-6:
                root[find(i)] = find(j)
        beside = [self._beside_outside(*run) for run in runs]
        outside = [beside[i] > 0 or self._faces_outside(*run) for i, run in enumerate(runs)]
        share = {}
        for group in {find(i) for i in range(len(runs))}:
            members = [i for i in range(len(runs)) if find(i) == group]
            sides = []
            for k in (1, -1):
                of = [i for i in members if side_line(runs[i][1], runs[i][2])[2] == k]
                sides.append({
                    "k": k,
                    "beside": sum(beside[i] for i in of),
                    "outside": any(outside[i] for i in of),
                    "floor": max((runs[i][1].elevation for i in of), default=float("-inf")),
                })
            facing = [s for s in sides if s["beside"]] or [s for s in sides if s["outside"]]
            owner = max(facing, key=lambda s: (s["floor"], s["beside"], s["k"]))["k"] if facing else None
            for i in members:
                k = side_line(runs[i][1], runs[i][2])[2]
                share[(id(runs[i][1]), runs[i][2])] = 0.5 if owner is None else 1.0 if owner == k else 0.0
        return share

    # --- Things a house file places against its walls

    def place(self, name):
        return next((q for q in self.places if q.name == name), None)

    def piece_of(self, place, x, y, width, depth):
        """The piece of a place a rectangle on the plan stands in: the one it's in, else the first."""
        within = lambda p: p.x - 1e-9 <= x and x + width <= p.x + p.width + 1e-9 and p.y - 1e-9 <= y and y + depth <= p.y + p.depth + 1e-9
        return next((p for p in pieces(place.plan) if within(p)), pieces(place.plan)[0])

    def against(self, p, sides, x, y, width, depth):
        """A rectangle on the plan, in piece p, moved flush with the faces of its walls on the sides given."""
        for side in sides:
            if side == "left":
                x = p.x + self.inward(p, side)
            elif side == "right":
                x = p.x + p.width - self.inward(p, side) - width
            elif side == "back":
                y = p.y + self.inward(p, side)
            else:
                y = p.y + p.depth - self.inward(p, side) - depth
        return x, y

    def resolve(self, details: Details) -> Details:
        """The details with everything placed against a wall moved flush with it, and every opening running from or to
        a wall given its offset or width."""
        fixtures = []
        for f in details.fixtures:
            place = self.place(f.room) if f.room else None
            if f.against and place:
                first = pieces(place.plan)[0]
                x0, y0 = first.x + f.x, first.y + f.y
                p = self.piece_of(place, x0, y0, f.width, f.depth)
                x, y = self.against(p, f.against, x0, y0, f.width, f.depth)
                f = replace(f, x=round(x - first.x, 6), y=round(y - first.y, 6))
            fixtures.append(f)
        steps = []
        for t in details.steps:
            place = self.place(t.room) if t.room else None
            if t.against and place:
                p = self.piece_of(place, t.x, t.y, t.width, t.depth)
                x, y = self.against(p, t.against, t.x, t.y, t.width, t.depth)
                t = replace(t, x=round(x, 6), y=round(y, 6))
            steps.append(t)
        openings = []
        for o in details.openings:
            place = self.place(o.room)
            if (o.offset is None or o.width is None) and place:
                key = {"left": lambda p: p.x, "right": lambda p: -(p.x + p.width), "back": lambda p: p.y, "front": lambda p: -(p.y + p.depth)}[o.wall]
                p = min(pieces(place.plan), key=key)
                start, end, length = ("back", "front", p.depth) if o.wall in ("left", "right") else ("left", "right", p.width)
                offset = self.inward(p, start) if o.offset is None else o.offset
                width = length - self.inward(p, end) - offset if o.width is None else o.width
                o = replace(o, offset=round(offset, 6), width=round(width, 6))
            openings.append(o)
        return replace(details, fixtures=tuple(fixtures), steps=tuple(steps), openings=tuple(openings))


def house_layout(sensors, areas, details: Details | None) -> HouseLayout:
    """The layout of a house's rooms with plans, inside, and its areas."""
    places = [s for s in sensors if s.plan and not s.outside] + list(areas)
    grade = details.grade if details else min((p.elevation for q in places for p in pieces(q.plan)), default=0.0)
    return HouseLayout(places, grade)
