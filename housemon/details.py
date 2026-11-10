"""What the house map's detailed view draws besides the rooms: the doors, windows,
vents, appliances, fireplaces and closets in them, the roof over them, and the yard around them, with its
sprinklers.

A house file (example_house.py, or your own) gives its details as a Details.
Like the rooms' plans, they're in feet, with x to the right, y toward the front
(the street) and heights above the main floor. Rooms are named as the house
names them, and things in a room are placed from its plan's back-left corner
(its first piece's, for a room in pieces), standing in any of its pieces or
across them.
Each thing is either known, from photos of the house or someone who knows it,
or a guess to correct.
"""

from dataclasses import dataclass, field

SIDES = ("left", "right", "back", "front")
OPENINGS = ("door", "window", "sliding door", "garage door", "opening", "vent")
FIXTURES = ("appliance", "vent", "plumbing", "fireplace", "half wall", "railing", "backflow", "valve box", "controller")
GROUNDS = ("porch", "patio", "deck", "driveway", "walk", "bed")
PAVINGS = ("concrete", "flagstone", "wood", "gravel", "mulch", "carpet", "dirt")
TREES = ("deciduous", "evergreen")
CROWNS = ("round", "vase", "spreading", "pyramid")
LEAVES = ("green", "light", "dark")
FALLS = ("yellow", "orange", "red")
FLOWERS = ("white", "pink")
BLOOMS = ("spring", "summer")
BARKS = ("brown", "gray", "white")
SPRINKLERS = ("rotor", "spray")
THROWS = {"rotor": 30, "spray": 12}  # feet a head of each kind throws, unless it or its zone says (MODEL_THROW in model.js)
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
    """An appliance, vent, plumbing (a tub, toilet, sink or spigot), fireplace, half wall or railing, or a part of a
    sprinkler system, drawn as a box: in a room, or outside (room None) at x and y on the plan.

    lift is how high its bottom is above the floor, or outside above the ground, or the top of a raised ground
    it's in, as a bed's. against lists the sides of its room it stands flush against the walls of, wherever their
    faces are, in place of x or y on that side's axis. A fireplace is in a room, against a wall, its firebox
    opening on the side away from that wall. A half wall is in a room too, out from one of its walls, in the
    room's wall color with a cap in its finish. A railing runs along its longer side, in its finish: posts at its
    ends, a handrail as wide as it on top, and poles between, as along the open edge of a floor. A vent on the
    floor standing against one wall is a register set in the corner, its grille sloping from the floor out in the
    room up to the wall, as high as its height.

    A sprinkler system's parts: a backflow, its backflow preventer, the pipe its water comes up out of the ground
    through, past a valve on either side, and goes back down, along the fixture's longer side and as high as its
    height; a valve box, outside, sunk in the ground with its lid `height` above it, holding the valves of some of
    its zones; and a controller, the box on a wall that runs them.
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
        if self.kind == "valve box" and self.room is not None:
            raise ValueError(f"{self.name} is a valve box, sunk in the ground outside, so its room is None")
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
    in its paving: a raised bed's. Its paving can be bare dirt, as a path worn along a fence, where trees and
    sprinkler heads stand as they do in the lawn.
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
    """A tree, its trunk at x and y on the plan, height to its top; spread is its crown's width.

    A deciduous tree's crown is round, widest in the middle (an oval or a column, as its height and spread make
    it), a vase, narrow below and widest high up with a broad top, spreading, widest low under a dome, or a
    pyramid, widest at its bottom and narrowing to a rounded top. clearance is how high its lowest leaves are,
    if not where its crown's shape puts them, as for a crown down low or held up high, and lean how far its
    crown's middle stands off its trunk, along x and y, as a tree grows out away from a house. The detailed view
    keeps a crown clear of the house and sheds, standing off their walls under the eaves, so one can reach over
    a roof without touching it. Its leaves are green, light (a yellower green) or dark, and in the fall turn
    yellow, orange or red (fall); the detailed view shows them fresher in spring, and the tree bare in winter, its
    branches reaching out to where its leaves would be. A flowering tree's flowers are white or pink, out over its
    crown in the season it blooms, spring or summer. stems is how many trunks rise from the ground, as a
    clump's do, and fork how high each splits into limbs, or 0 for a trunk running on up into the crown. trunk is
    each stem's width at the ground, by default from the tree's spread, and bark its color: brown, gray or white.
    ring is the radius of a bed of mulch around the trunk, if it has one: raised and edged in stone or brick
    (edging), or flat on the lawn with no edging (None). An evergreen is the same all year.
    """

    name: str
    kind: str
    x: float
    y: float
    height: float
    spread: float
    ring: float = 0.0
    edging: str | None = "stone"
    crown: str = "round"
    clearance: float | None = None
    lean: tuple[float, float] = (0.0, 0.0)
    leaves: str = "green"
    fall: str = "yellow"
    flowers: str | None = None
    blooms: str = "spring"
    stems: int = 1
    fork: float = 0.0
    trunk: float | None = None
    bark: str = "brown"
    known: bool = False

    def __post_init__(self):
        _check(self.kind, TREES, "a tree's kind")
        if self.edging is not None:
            _check(self.edging, EDGINGS, "an edging")
        _check(self.crown, CROWNS, "a crown")
        _check(self.leaves, LEAVES, "a tree's leaves")
        _check(self.fall, FALLS, "a tree's fall color")
        if self.flowers is not None:
            _check(self.flowers, FLOWERS, "a tree's flowers")
        _check(self.blooms, BLOOMS, "the season a tree blooms")
        if self.flowers is None and self.blooms != "spring":
            raise ValueError(f"{self.name} blooms in {self.blooms}, so it has flowers")
        _check(self.bark, BARKS, "a bark")
        if not isinstance(self.stems, int) or self.stems < 1:
            raise ValueError(f"{self.name} has at least one stem, as a whole number")
        if not 0 <= self.fork < self.height / 2:
            raise ValueError(f"{self.name} forks below its crown, under half its height")
        if self.trunk is not None and not 0 < self.trunk < self.spread / 2:
            raise ValueError(f"{self.name}'s trunk is narrower than half its spread")
        if self.clearance is not None and not self.fork < self.clearance <= self.height * 0.6:
            raise ValueError(f"{self.name}'s crown starts above its fork, at most 60% of the way up")
        if len(self.lean) != 2 or (self.lean[0] ** 2 + self.lean[1] ** 2) ** 0.5 >= self.spread / 4:
            raise ValueError(f"{self.name}'s crown leans off its trunk along x and y, under a quarter of its spread")
        if self.kind == "evergreen" and (
            self.crown, self.clearance, tuple(self.lean), self.leaves, self.fall, self.flowers, self.stems, self.fork
        ) != ("round", None, (0, 0), "green", "yellow", None, 1, 0):
            raise ValueError(f"{self.name} is an evergreen, its needles in tiers on one trunk, the same all year")


@dataclass(frozen=True)
class Head:
    """A sprinkler head at x and y on the plan. arc is the directions it throws between, in degrees on the plan, 0
    to the right, 90 to the front, 180 to the left and 270 to the back, counting up from the first to the second:
    (0, 90) is the quarter between the right and the front, and (270, 90) the half to the right. None, the default,
    throws all the way round. radius is how far it throws, in feet, if not as far as its zone's heads do."""

    x: float
    y: float
    arc: tuple[float, float] | None = None
    radius: float | None = None

    def __post_init__(self):
        if self.arc is not None and (len(self.arc) != 2 or (self.arc[1] - self.arc[0]) % 360 == 0):
            raise ValueError(f"The head at {self.x}, {self.y} throws between two directions, or all the way round (None)")
        if self.radius is not None and self.radius <= 0:
            raise ValueError(f"The head at {self.x}, {self.y} needs to throw some way")


@dataclass(frozen=True)
class Hedge:
    """A hedge along these points on the plan, in order: a row of shrubs, or of a tree's shoots, width across and
    height tall. Its leaves are green, light or dark, and turn yellow, orange or red in the fall (fall), as a tree's
    do; the detailed view shows them fresher in spring, and the hedge bare in winter, a wall of stems."""

    name: str
    points: tuple[tuple[float, float], ...]
    height: float
    width: float
    leaves: str = "green"
    fall: str = "yellow"
    known: bool = False

    def __post_init__(self):
        if len(self.points) < 2:
            raise ValueError(f"{self.name} runs along at least two points")
        if not (self.height > 0 and self.width > 0):
            raise ValueError(f"{self.name} has a height and a width")
        _check(self.leaves, LEAVES, "a hedge's leaves")
        _check(self.fall, FALLS, "a hedge's fall color")


@dataclass(frozen=True)
class Sprinklers:
    """One zone of a sprinkler system: its heads, in the lawn or a bed, standing in whichever they're in, each a Head,
    or for one throwing all the way round, its point (x, y) on the plan. kind is what they are: rotors, throwing far
    across a lawn, or sprays, smaller and closer together. radius is how far its heads throw, in feet, but for one
    that says, if not as far as that kind does by default (THROWS: 30 for rotors, 12 for sprays). Its valve is in a
    valve box, a fixture, and so is the controller that runs it."""

    name: str
    heads: tuple[Head | tuple[float, float], ...]
    kind: str = "rotor"
    radius: float | None = None
    known: bool = False

    def __post_init__(self):
        _check(self.kind, SPRINKLERS, "a sprinkler's kind")
        if not self.heads:
            raise ValueError(f"{self.name} needs a head or more")
        if self.radius is not None and self.radius <= 0:
            raise ValueError(f"{self.name}'s heads need to throw some way")
        object.__setattr__(self, "heads", tuple(h if isinstance(h, Head) else Head(*h) for h in self.heads))


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
class Slope:
    """Where the ground slopes, as a front yard does down to the street: across the whole lot, between two lines
    across the plan at `between`, the y's (for down "back" or "front") or x's (for "left" or "right") they're at,
    falling evenly by `fall` ft from the one to the other, toward the side `down`. Before it the ground is as high as at
    its top, and past it as low as at its foot. Slopes add up, so a yard can fall two ways. The house stands on level
    ground, at the grade, so it's before each slope's top; what stands outside stands on the ground where it is, a raised
    ground (a porch, or a bed) and a shed level, up from the highest ground under them."""

    name: str
    down: str
    between: tuple[float, float]
    fall: float
    known: bool = False

    def __post_init__(self):
        _check(self.down, SIDES, "the way a slope falls")
        if len(self.between) != 2 or not self.between[0] < self.between[1]:
            raise ValueError(f"{self.name} is between two lines, the lower first")
        if self.fall <= 0:
            raise ValueError(f"{self.name} falls some way")

    def share(self, x, y):
        """How far down it the ground at x, y on the plan is, from 0 at its top to 1 at its foot."""
        a, b = self.between
        v = y if self.down in ("back", "front") else x
        f = min(max((v - a) / (b - a), 0.0), 1.0)
        return f if self.down in ("front", "right") else 1 - f


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
    floor, but where slopes fall from it. finishes is by room name; a room without one gets wood floors and
    painted walls. colors replaces the detailed view's colors, the --model-*
    tokens in style.css, by name, each as light and dark: the house's siding
    and roof, say. example marks a made-up house, with nothing in it to check,
    so the view calls none of it a guess. sprinklers are the sprinkler
    system's zones.
    """

    lot: tuple[tuple[float, float], ...]
    grade: float
    slopes: tuple[Slope, ...] = ()
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
    hedges: tuple[Hedge, ...] = ()
    sheds: tuple[Shed, ...] = ()
    fences: tuple[Fence, ...] = ()
    steps: tuple[Steps, ...] = ()
    sprinklers: tuple[Sprinklers, ...] = ()

    def ground(self, x, y):
        """How high the ground is at x, y on the plan, above the main floor: the grade, less what the slopes fall by
        there (groundAt in model.js)."""
        return self.grade - sum(s.fall * s.share(x, y) for s in self.slopes)
