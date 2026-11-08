"""What the house map's detailed view draws besides the rooms: the doors, windows,
vents, appliances, fireplaces and closets in them, the roof over them, and the yard around them.

A house file (example_house.py, or your own) gives its details as a Details.
Like the rooms' plans, they're in feet, with x to the right, y toward the front
(the street) and heights above the main floor. Rooms are named as the house
names them, and things in a room are placed from its plan's back-left corner
(its first piece's, for a room in pieces), standing in any of its pieces.
Each thing is either known, from photos of the house or someone who knows it,
or a guess to correct.
"""

from dataclasses import dataclass, field

SIDES = ("left", "right", "back", "front")
OPENINGS = ("door", "window", "sliding door", "garage door", "opening", "vent")
FIXTURES = ("appliance", "vent", "plumbing", "fireplace", "half wall", "railing")
GROUNDS = ("porch", "patio", "deck", "driveway", "walk", "bed")
PAVINGS = ("concrete", "flagstone", "wood", "gravel", "mulch", "carpet")
TREES = ("deciduous", "evergreen")
FENCES = ("wood", "chain link")
EDGINGS = ("stone", "brick")
CLADDINGS = ("siding", "paneling", "stone")
FLOORINGS = ("carpet", "wood", "tile", "concrete", "dirt")
WALLINGS = ("paint", "drywall", "concrete")
FINISHES = ("white", "steel", "black", "grille", "brick", "stone", "wood")


def _check(value, allowed, what):
    if value not in allowed:
        raise ValueError(f"{what} is one of {', '.join(allowed)}, not {value!r}")


def _check_against(name, against, room):
    if isinstance(against, str):
        raise ValueError(f"{name}'s against is a tuple of sides, like ({against!r},)")
    for side in against:
        _check(side, SIDES, "a side it's against")
    if against and room is None:
        raise ValueError(f"{name} stands against a room's walls, so it's in a room")
    if len({"left", "right"} & set(against)) > 1 or len({"back", "front"} & set(against)) > 1:
        raise ValueError(f"{name} can't stand against both of its room's opposite walls")


@dataclass(frozen=True)
class Opening:
    """A door, window or vent in one of a room's walls, or a shed's, named as its room.

    wall is the room's side it's in, and offset how far along that wall it
    starts: from the left end of a back or front wall, from the back end of a
    left or right wall. A room in pieces uses its piece furthest out on that
    side. An offset of None starts it at the face of the wall across that end,
    and a width of None runs it to the face of the wall across the other, so
    an opening from wall to wall has both. sill is how high its bottom is
    above the room's floor, or the ground for a shed's. A door, window or
    opening goes through the wall to whatever is on the other side; an
    opening has no door in it. A vent is a grille on the wall.
    """

    name: str
    kind: str
    room: str
    wall: str
    offset: float | None
    width: float | None
    height: float
    sill: float = 0.0
    known: bool = False

    def __post_init__(self):
        _check(self.kind, OPENINGS, "an opening's kind")
        _check(self.wall, SIDES, "a wall")


@dataclass(frozen=True)
class Closet:
    """A closet in a room, walled to the room's ceiling, at most 8 ft, with its doors on one side: into its room,
    or, set into the room's wall on that side, through a door in that wall into the room beyond."""

    name: str
    room: str
    x: float
    y: float
    width: float
    depth: float
    door: str
    known: bool = False

    def __post_init__(self):
        _check(self.door, SIDES, "a closet's door side")


@dataclass(frozen=True)
class Fixture:
    """An appliance, vent, plumbing (a tub, toilet or sink), fireplace, half wall or railing, drawn as a box: in a
    room, or outside (room None) at x and y on the plan.

    lift is how high its bottom is above the floor, or outside above the ground. against lists the sides of its
    room it stands flush against the walls of, wherever their faces are, in place of x or y on that side's axis.
    A fireplace is in a room, against a wall, its firebox opening on the side away from that wall. A half wall
    is in a room too, out from one of its walls, in the room's wall color with a cap in its finish. A railing
    runs along its longer side, in its finish: posts at its ends, a handrail as wide as it on top, and poles
    between, as along the open edge of a floor.
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
    against: tuple[str, ...] = ()

    def __post_init__(self):
        _check(self.kind, FIXTURES, "a fixture's kind")
        _check(self.finish, FINISHES, "a fixture's finish")
        if self.kind in ("fireplace", "half wall") and self.room is None:
            raise ValueError(f"{self.name} is a {self.kind}, so it's in a room")
        _check_against(self.name, self.against, self.room)


@dataclass(frozen=True)
class Ground:
    """A porch, patio, deck, driveway, walk or bed (of flowers, or gravel), on the plan; height is how far its
    top stands above the ground.

    Where it runs past the lot, as a driveway may to the street, it's cut off at the lot's edge. A flat one
    can have cutouts, each x, y, width and depth, left as bare ground: around a tree, say. Any can instead
    have an outline, its corners on the plan in order, that cuts it to that shape within its rectangle and
    the lot: along a slanting fence, say. railings are the sides it has a railing along, 3 ft over its top: a
    deck's. edging, for a raised one, is a low wall of stone or brick along its edges, up to its top, holding
    in its paving: a raised bed's.
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
    outline: tuple[tuple[float, float], ...] | None = None
    railings: tuple[str, ...] = ()
    edging: str | None = None
    known: bool = False

    def __post_init__(self):
        _check(self.kind, GROUNDS, "a ground's kind")
        for side in self.railings:
            _check(side, SIDES, "a railing's side")
        if self.cutouts and self.height:
            raise ValueError(f"{self.name} stands above the ground, so it can't have cutouts")
        if self.outline and self.cutouts:
            raise ValueError(f"{self.name} has an outline, so it has no cutouts")
        if self.edging is not None:
            _check(self.edging, EDGINGS, "an edging")
            if not self.height:
                raise ValueError(f"{self.name} has edging, so it stands above the ground")
        _check(self.paving, PAVINGS, "a paving")


@dataclass(frozen=True)
class Tree:
    """A tree, its trunk at x and y on the plan; spread is its canopy's width, and ring the radius of a
    raised bed around its trunk, if it has one, edged in stone or brick (edging) and filled with mulch."""

    name: str
    kind: str
    x: float
    y: float
    height: float
    spread: float
    ring: float = 0.0
    edging: str = "stone"
    known: bool = False

    def __post_init__(self):
        _check(self.kind, TREES, "a tree's kind")
        _check(self.edging, EDGINGS, "an edging")


@dataclass(frozen=True)
class Shed:
    """A shed or other outbuilding on the plan: walls `wall` ft high under a gable roof up to `peak`,
    its ridge along x or y. Its doors and windows are openings that name it as their room."""

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
    """A fence through these points on the plan, in order, with gates: each its middle, on the fence, and width.

    kind is what it's made of: wood boards, or chain link.
    """

    name: str
    points: tuple[tuple[float, float], ...]
    height: float = 6.0
    gates: tuple[tuple[float, float, float], ...] = ()
    kind: str = "wood"
    known: bool = False

    def __post_init__(self):
        _check(self.kind, FENCES, "a fence's kind")


@dataclass(frozen=True)
class Steps:
    """A flight of steps over x, y, width and depth on the plan, climbing from bottom to top, heights above the
    main floor, toward the side `up`. They're in a room, in any piece of it, for what shows when the house is cut
    away, or outside. A flight in a room that climbs to the floor of a room above opens a stairwell in it. count is
    how many steps, if not one for every 7 in or so of rise.

    banisters are the sides along its climb it has a banister on, just outside it: posts at its ends, a handrail
    `banister` ft wide 3 ft over its treads, and poles between. A flight under a floor, in the stairwell it opens,
    has its banisters level, 3 ft over that floor, over the stairwell's side. against, for a flight in a room,
    lists the sides of the room it stands flush against the walls of, in place of x or y on that side's axis.
    """

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
    count: int | None = None
    banisters: tuple[str, ...] = ()
    banister: float = 0.46
    known: bool = False
    against: tuple[str, ...] = ()

    def __post_init__(self):
        _check_against(self.name, self.against, self.room)
        _check(self.up, SIDES, "the way steps go up")
        _check(self.paving, PAVINGS, "a paving")
        if self.count is not None and self.count < 1:
            raise ValueError(f"{self.name} needs a step or more, not {self.count}")
        along = ("back", "front") if self.up in ("left", "right") else ("left", "right")
        for side in self.banisters:
            _check(side, along, "a banister's side, along the climb")


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
