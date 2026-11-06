"""What the house map's detailed view draws besides the rooms: the doors, windows,
vents, appliances and closets in them, the roof over them, and the yard around them.

A house file (example_house.py, or your own) gives its details as a Details.
Like the rooms' plans, they're in feet, with x to the right, y toward the front
(the street) and heights above the main floor. Rooms are named as the house
names them, and things in a room are placed from its plan's back-left corner.
Each thing is either known, from photos of the house or someone who knows it,
or a guess to correct.
"""

from dataclasses import dataclass, field

SIDES = ("left", "right", "back", "front")
OPENINGS = ("door", "window", "sliding door", "garage door", "opening", "vent")
FIXTURES = ("appliance", "vent")
GROUNDS = ("porch", "patio", "deck", "driveway", "walk")
PAVINGS = ("concrete", "flagstone", "wood")
TREES = ("deciduous", "evergreen")
CLADDINGS = ("siding", "paneling")
FLOORINGS = ("carpet", "wood", "tile", "concrete", "dirt")
WALLINGS = ("paint", "drywall", "concrete")
FINISHES = ("white", "steel", "black", "grille")


def _check(value, allowed, what):
    if value not in allowed:
        raise ValueError(f"{what} is one of {', '.join(allowed)}, not {value!r}")


@dataclass(frozen=True)
class Opening:
    """A door, window or vent in one of a room's walls.

    wall is the room's side it's in, and offset how far along that wall it
    starts: from the left end of a back or front wall, from the back end of a
    left or right wall. A room in pieces uses its piece furthest out on that
    side. sill is how high its bottom is above the room's floor. A door, window
    or opening goes through the wall to whatever is on the other side; an
    opening has no door in it. A vent is a grille on the wall.
    """

    name: str
    kind: str
    room: str
    wall: str
    offset: float
    width: float
    height: float
    sill: float = 0.0
    known: bool = False

    def __post_init__(self):
        _check(self.kind, OPENINGS, "an opening's kind")
        _check(self.wall, SIDES, "a wall")


@dataclass(frozen=True)
class Closet:
    """A closet in a room, walled to the room's ceiling, at most 8 ft, with its doors on one side."""

    name: str
    room: str
    x: float
    y: float
    width: float
    depth: float
    door: str

    def __post_init__(self):
        _check(self.door, SIDES, "a closet's door side")


@dataclass(frozen=True)
class Fixture:
    """An appliance or vent, drawn as a box: in a room, or outside (room None) at x and y on the plan.

    lift is how high its bottom is above the floor, or outside above the ground.
    """

    name: str
    kind: str
    room: str | None
    x: float
    y: float
    width: float
    depth: float
    height: float
    lift: float = 0.0
    finish: str = "white"
    known: bool = False

    def __post_init__(self):
        _check(self.kind, FIXTURES, "a fixture's kind")
        _check(self.finish, FINISHES, "a fixture's finish")


@dataclass(frozen=True)
class Ground:
    """A porch, patio, deck, driveway or walk, on the plan; height is how far its top stands above the ground.

    Where it runs past the lot, as a driveway may to the street, it's cut off at the lot's edge. A flat one
    can have cutouts, each x, y, width and depth, left as bare ground: around a tree, say.
    """

    name: str
    kind: str
    x: float
    y: float
    width: float
    depth: float
    height: float = 0.0
    paving: str = "concrete"
    cutouts: tuple[tuple[float, float, float, float], ...] = ()
    known: bool = False

    def __post_init__(self):
        _check(self.kind, GROUNDS, "a ground's kind")
        if self.cutouts and self.height:
            raise ValueError(f"{self.name} stands above the ground, so it can't have cutouts")
        _check(self.paving, PAVINGS, "a paving")


@dataclass(frozen=True)
class Tree:
    """A tree, its trunk at x and y on the plan; spread is its canopy's width, and ring the radius of a
    raised stone ring around its trunk, if it has one."""

    name: str
    kind: str
    x: float
    y: float
    height: float
    spread: float
    ring: float = 0.0
    known: bool = False

    def __post_init__(self):
        _check(self.kind, TREES, "a tree's kind")


@dataclass(frozen=True)
class Shed:
    """A shed or other outbuilding on the plan: walls `wall` ft high under a gable roof up to `peak`,
    its ridge along x or y."""

    name: str
    x: float
    y: float
    width: float
    depth: float
    wall: float = 7.0
    peak: float = 9.0
    ridge: str = "x"
    known: bool = False

    def __post_init__(self):
        _check(self.ridge, ("x", "y"), "a ridge")


@dataclass(frozen=True)
class Fence:
    """A fence through these points on the plan, in order, with gates: each its middle, on the fence, and width."""

    name: str
    points: tuple[tuple[float, float], ...]
    height: float = 6.0
    gates: tuple[tuple[float, float, float], ...] = ()
    known: bool = False


@dataclass(frozen=True)
class Steps:
    """A flight of steps over x, y, width and depth on the plan, climbing from bottom to top, heights above the
    main floor, toward the side `up`. They're in a room, for what shows when the house is cut away, or outside."""

    name: str
    x: float
    y: float
    width: float
    depth: float
    bottom: float
    top: float
    up: str
    room: str | None = None
    paving: str = "concrete"
    known: bool = False

    def __post_init__(self):
        _check(self.up, SIDES, "the way steps go up")
        _check(self.paving, PAVINGS, "a paving")


@dataclass(frozen=True)
class RoofVent:
    """A vent pipe or cap through the roof at x, y on the plan, standing on whichever roof is there."""

    name: str
    x: float
    y: float
    known: bool = False


@dataclass(frozen=True)
class Cladding:
    """What the outside of a room's wall is covered in, where it isn't the siding."""

    room: str
    wall: str
    finish: str

    def __post_init__(self):
        _check(self.wall, SIDES, "a wall")
        _check(self.finish, CLADDINGS, "a cladding")


@dataclass(frozen=True)
class Roof:
    """One section of roof over x, y, width and depth on the plan, its overhang included.

    A gable has a ridge along its middle, running along x or y, and slopes down
    to both sides; a shed slopes down from one side to the side it drops to.
    eave is the height of its lowest edges above the main floor, and peak of its
    ridge or high side. It's drawn solid down to its underside, 0.6 ft below its
    eaves unless given: lower for a section of a longer slope, to reach the
    walls under it. An outline, its corners on the plan in order, cuts it to
    that shape within its rectangle, such as where it meets another section in
    a valley.
    """

    name: str
    x: float
    y: float
    width: float
    depth: float
    eave: float
    peak: float
    ridge: str | None = None
    drop: str | None = None
    underside: float | None = None
    outline: tuple[tuple[float, float], ...] | None = None
    known: bool = False

    def __post_init__(self):
        if (self.ridge is None) == (self.drop is None):
            raise ValueError(f"{self.name} needs a ridge (a gable) or a drop (a shed), not both")
        if self.ridge is not None:
            _check(self.ridge, ("x", "y"), "a ridge")
        else:
            _check(self.drop, SIDES, "a roof's drop")


@dataclass(frozen=True)
class Finish:
    """What a room's floor and walls are made of, for their colors."""

    floor: str
    walls: str = "paint"

    def __post_init__(self):
        _check(self.floor, FLOORINGS, "a floor")
        _check(self.walls, WALLINGS, "walls")


@dataclass(frozen=True)
class Details:
    """Everything the detailed view adds to the rooms.

    The yard fills lot, the corners of its outline in order, with lawn
    wherever nothing else is, and grade is the ground's height above the main
    floor. finishes is by room name; a room without one gets wood floors and
    painted walls. colors replaces the detailed view's colors, the --model-*
    tokens in style.css, by name, each as light and dark: the house's siding
    and roof, say. example marks a made-up house, with nothing in it to check,
    so the view calls none of it a guess.
    """

    lot: tuple[tuple[float, float], ...]
    grade: float
    example: bool = False
    finishes: dict[str, Finish] = field(default_factory=dict)
    colors: dict[str, tuple[str, str]] = field(default_factory=dict)
    claddings: tuple[Cladding, ...] = ()
    roofs: tuple[Roof, ...] = ()
    roof_vents: tuple[RoofVent, ...] = ()
    openings: tuple[Opening, ...] = ()
    closets: tuple[Closet, ...] = ()
    fixtures: tuple[Fixture, ...] = ()
    grounds: tuple[Ground, ...] = ()
    trees: tuple[Tree, ...] = ()
    sheds: tuple[Shed, ...] = ()
    fences: tuple[Fence, ...] = ()
    steps: tuple[Steps, ...] = ()
