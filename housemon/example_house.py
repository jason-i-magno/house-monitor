"""The example house: a made-up two-story house over a basement, which the app
models unless it's given a house of your own (see house.py). Copy it to start
your own house file.

Its rooms are in feet, x to the right and y toward the street, each floor's
elevation above the main floor's. The main floor stands a foot above the
ground, with the garage a step down on the right and a sunroom off the back
of the kitchen. Upstairs, two bedrooms, a bathroom and a guest room open off a
hall; in the basement, an office and a utility room open off an L-shaped
family room. A thermostat on the main floor keeps it at 64 to 68 °F; upstairs
runs warm, the basement cold, the office has a space heater for the work day,
and the garage is unheated. The first eight rooms get the eight series
colors, and the garage, bathroom and outside share the gray.
"""

from housemon.details import Closet, Details, Fence, Finish, Fixture, Ground, Opening, Roof, RoofVent, Shed, Steps, Tree
from housemon.readings import Area, Plan, Sensor
from housemon.simulator import SimulatedRoom

UP, DOWN = 9.5, -9  # the upper floor's and the basement's elevations

HOUSE = (
    SimulatedRoom(
        Sensor("sim-upstairs-bedroom", "Upstairs bedroom", "Upper", Plan(0, 0, 16, 14, elevation=UP)),
        3.0, 0.06, 1.5, 29, battery=96, rssi=-84,
    ),
    SimulatedRoom(
        Sensor("sim-front-bedroom", "Front bedroom", "Upper", Plan(0, 14, 16, 14, elevation=UP)),
        2.5, 0.06, 1.0, 30, battery=88, rssi=-88,
    ),
    SimulatedRoom(Sensor("sim-kitchen", "Kitchen", "Main", Plan(18, 0, 18, 13)), 0.5, 0.05, 0.3, 33, cooking=True, rssi=-72),
    SimulatedRoom(Sensor("sim-dining-room", "Dining room", "Main", Plan(0, 0, 18, 13)), 0.0, 0.06, 0.5, 32, battery=92, rssi=-74),
    SimulatedRoom(Sensor("sim-living-room", "Living room", "Main", Plan(0, 13, 24, 15)), 0.0, 0.05, 0.8, 32, battery=74, rssi=-75),
    SimulatedRoom(
        Sensor("sim-office", "Office", "Basement", Plan(20, 0, 10, 16, elevation=DOWN)),
        -5.0, 0.03, 0.0, 38, radiator=True, battery=91, rssi=-48,
    ),
    SimulatedRoom(  # an L, in two pieces
        Sensor("sim-family-room", "Family room", "Basement", (Plan(0, 0, 20, 16, elevation=DOWN), Plan(0, 16, 36, 12, elevation=DOWN))),
        -4.0, 0.03, 0.0, 40, battery=63, rssi=-66,
    ),
    SimulatedRoom(  # its ceiling sloping down 2 ft to the back
        Sensor("sim-sunroom", "Sunroom", "Main", Plan(18, -10, 14, 10, drop=("back", 2))),
        -1.0, 0.08, 2.5, 31, battery=99, rssi=-80,
    ),
    SimulatedRoom(
        Sensor("sim-garage", "Garage", "Main", Plan(36, 6, 22, 22, elevation=-1), heated=False),
        -22.0, 0.5, 2.0, 35, thermostat=False, rssi=-86,
    ),
    SimulatedRoom(
        Sensor("sim-bathroom", "Bathroom", "Upper", Plan(16, 0, 9, 9, elevation=UP)),
        2.0, 0.04, 0.0, 38, showers=(6.75, 21.0), battery=95, rssi=-87,
    ),
    SimulatedRoom(  # in the shade, reading the weather itself
        Sensor("sim-outside", "Outside", "Outside", heated=False, outside=True),
        -30.0, 1.0, 0.0, 55, thermostat=False, rssi=-90,
    ),
)

AREAS = (
    Area("Entry", "Main", Plan(24, 13, 12, 15)),
    Area("Hall", "Upper", Plan(16, 9, 9, 19, elevation=UP)),
    Area("Guest room", "Upper", Plan(25, 0, 11, 28, elevation=UP)),
    Area("Utility room", "Basement", Plan(30, 0, 6, 16, elevation=DOWN)),
)

DETAILS = Details(
    lot=((-20, -45), (75, -45), (75, 60), (-20, 60)),
    grade=-1,
    example=True,
    finishes={
        "Upstairs bedroom": Finish("carpet"),
        "Front bedroom": Finish("carpet"),
        "Bathroom": Finish("tile"),
        "Hall": Finish("carpet"),
        "Guest room": Finish("carpet"),
        "Kitchen": Finish("tile"),
        "Dining room": Finish("wood"),
        "Living room": Finish("wood"),
        "Entry": Finish("tile"),
        "Sunroom": Finish("tile"),
        "Office": Finish("carpet"),
        "Family room": Finish("carpet"),
        "Utility room": Finish("concrete", "concrete"),
        "Garage": Finish("concrete", "drywall"),
    },
    roofs=(
        Roof("Main roof", 0, -1, 36, 30, eave=18.1, peak=25.6, ridge="x"),
        Roof("Garage roof", 36, 5, 22, 24, eave=7.6, peak=12.4, ridge="x"),
        Roof("Sunroom roof", 18, -11, 14, 11, eave=6.6, peak=8.8, drop="back"),
        Roof("Porch roof", 26, 28, 8, 6.5, eave=7.6, peak=8.4, drop="front"),
    ),
    roof_vents=(RoofVent("Roof vent", 8, 6), RoofVent("Roof vent", 20, 4)),
    openings=(
        # Outside doors
        Opening("Front door", "door", "Entry", "front", 4.5, 3, 6.67),
        Opening("Garage door", "garage door", "Garage", "front", 3, 16, 7),
        Opening("Sliding door", "sliding door", "Sunroom", "right", 3.5, 6, 6.67),
        # Inside doors and openings
        Opening("Door from the garage", "door", "Garage", "left", 10, 3, 6.67, sill=1),  # up steps
        Opening("Opening to the sunroom", "opening", "Sunroom", "front", 3, 8, 6.5),
        Opening("Opening to the kitchen", "opening", "Dining room", "right", 2, 8, 7),
        Opening("Opening to the dining room", "opening", "Living room", "back", 2, 10, 7),
        Opening("Opening to the entry", "opening", "Living room", "right", 3, 6, 7),
        Opening("Kitchen door", "door", "Kitchen", "front", 8, 3, 6.67),
        Opening("Upstairs bedroom door", "door", "Upstairs bedroom", "right", 10, 2.5, 6.67),
        Opening("Bathroom door", "door", "Bathroom", "front", 3, 2.5, 6.67),
        Opening("Front bedroom door", "door", "Front bedroom", "right", 2, 2.5, 6.67),
        Opening("Guest room door", "door", "Guest room", "left", 12, 2.5, 6.67),
        Opening("Office door", "door", "Office", "left", 10, 2.5, 6.67),
        Opening("Utility room door", "door", "Utility room", "front", 1.5, 3, 6.67),
        # Windows
        Opening("Dining room window", "window", "Dining room", "back", 5, 6, 4, sill=3),
        Opening("Dining room side window", "window", "Dining room", "left", 4, 4, 4, sill=3),
        Opening("Kitchen window", "window", "Kitchen", "back", 14.5, 3, 3.5, sill=3.5),
        Opening("Kitchen side window", "window", "Kitchen", "right", 1.5, 3, 3.5, sill=3.5),
        Opening("Living room window", "window", "Living room", "front", 4, 8, 5, sill=2),
        Opening("Living room side window", "window", "Living room", "left", 5, 4, 4, sill=3),
        Opening("Sunroom back window", "window", "Sunroom", "back", 2, 10, 3.5, sill=2),
        Opening("Sunroom side window", "window", "Sunroom", "left", 2, 6, 3.5, sill=2),
        Opening("Upstairs bedroom window", "window", "Upstairs bedroom", "back", 5, 5, 4, sill=3),
        Opening("Upstairs bedroom side window", "window", "Upstairs bedroom", "left", 5, 4, 4, sill=3),
        Opening("Bathroom window", "window", "Bathroom", "back", 3, 2.5, 2, sill=4.5),
        Opening("Front bedroom window", "window", "Front bedroom", "front", 5, 5, 4, sill=3),
        Opening("Front bedroom side window", "window", "Front bedroom", "left", 5, 4, 4, sill=3),
        Opening("Guest room back window", "window", "Guest room", "back", 3.5, 4, 4, sill=3),
        Opening("Guest room front window", "window", "Guest room", "front", 3.5, 4, 4, sill=3),
        Opening("Family room window", "window", "Family room", "left", 6, 3, 1.5, sill=6),  # in a window well
        Opening("Family room front window", "window", "Family room", "front", 6, 3, 1.5, sill=6),
        Opening("Garage window", "window", "Garage", "right", 8, 3, 3, sill=4),
        # Return air grilles
        Opening("Main floor return", "vent", "Living room", "right", 11, 2, 1, sill=0.5),
        Opening("Upstairs return", "vent", "Hall", "left", 15, 2, 1, sill=0.5),
        # The shed's
        Opening("Shed door", "door", "Shed", "front", 3.5, 3, 6.5),
        Opening("Shed window", "window", "Shed", "left", 2.5, 3, 2, sill=3.5),
    ),
    closets=(
        Closet("Upstairs bedroom closet", "Upstairs bedroom", 1.5, 11.5, 7, 2.5, door="back"),
        Closet("Front bedroom closet", "Front bedroom", 1.5, 0, 7, 2.5, door="front"),
        Closet("Coat closet", "Entry", 9, 0, 3, 3, door="left"),
    ),
    fixtures=(
        Fixture("Refrigerator", "appliance", "Kitchen", 15, 6.5, 2.5, 3, 5.75, finish="steel"),
        Fixture("Range", "appliance", "Kitchen", 1, 10.5, 2.5, 2, 3, finish="steel"),
        Fixture("Range hood", "vent", "Kitchen", 1.1, 10.5, 2.3, 2, 1, lift=5.5, finish="steel"),
        Fixture("Dishwasher", "appliance", "Kitchen", 6, 10.5, 2, 2, 2.8, finish="steel"),
        Fixture("Kitchen sink", "plumbing", "Kitchen", 14.5, 0.4, 3, 2, 3),  # under the window
        Fixture("Bathtub", "plumbing", "Bathroom", 0.4, 0.4, 5, 2.5, 1.75),
        Fixture("Toilet", "plumbing", "Bathroom", 6.2, 3, 2.4, 1.6, 1.3),
        Fixture("Bathroom sink", "plumbing", "Bathroom", 0.4, 3.5, 1.7, 2, 2.8),
        Fixture("Furnace", "appliance", "Utility room", 0.5, 0.5, 2.5, 2.5, 4.5, finish="steel"),
        Fixture("Water heater", "appliance", "Utility room", 3.5, 0.5, 1.5, 1.5, 4.8),
        Fixture("Washer", "appliance", "Utility room", 0.5, 6, 2.3, 2.3, 3.2),
        Fixture("Dryer", "appliance", "Utility room", 0.5, 8.5, 2.3, 2.3, 3.2),
        Fixture("Space heater", "appliance", "Office", 1, 1, 1, 1.5, 2, finish="black"),
        Fixture("Family room half wall", "half wall", "Family room", 14.55, 4, 5, 0.4, 3.5, finish="wood"),  # out from the office's wall
        Fixture("Fireplace", "fireplace", "Living room", 15, 12.8, 5, 1.75, 4.5, finish="brick"),  # beside the window
        Fixture("Air conditioner", "appliance", None, -4, 15, 2.75, 2.75, 2.8, lift=0.2, finish="steel"),
        # Supply registers in the floors, and the bathroom's ceiling fan
        Fixture("Upstairs bedroom register", "vent", "Upstairs bedroom", 7, 0.6, 1, 0.33, 0.05, finish="grille"),
        Fixture("Front bedroom register", "vent", "Front bedroom", 7, 13.1, 1, 0.33, 0.05, finish="grille"),
        Fixture("Bathroom register", "vent", "Bathroom", 6.5, 8.1, 1, 0.33, 0.05, finish="grille"),
        Fixture("Bathroom fan", "vent", "Bathroom", 4, 4, 1, 1, 0.25, lift=7.75, finish="grille"),
        Fixture("Living room register", "vent", "Living room", 8, 14.1, 1, 0.33, 0.05, finish="grille"),
        Fixture("Dining room register", "vent", "Dining room", 0.6, 6, 0.33, 1, 0.05, finish="grille"),
        Fixture("Kitchen register", "vent", "Kitchen", 11, 6, 1, 0.33, 0.05, finish="grille"),
        Fixture("Sunroom register", "vent", "Sunroom", 6, 0.6, 1, 0.33, 0.05, finish="grille"),
        Fixture("Family room register", "vent", "Family room", 8, 0.6, 1, 0.33, 0.05, finish="grille"),
        Fixture("Office register", "vent", "Office", 5, 0.6, 1, 0.33, 0.05, finish="grille"),
    ),
    grounds=(
        Ground("Front porch", "porch", 26, 28, 8, 6, height=0.9),
        Ground("Front walk", "walk", 28.5, 34, 3, 26),
        Ground("Driveway", "driveway", 38, 28, 18, 32),
        Ground("Deck", "deck", 32, -10, 12, 10, height=0.9, paving="wood", railings=("right",)),
        Ground("Patio", "patio", 0, -10, 14, 10, paving="flagstone"),
        Ground("Flower bed", "bed", 0, 28, 26, 3, height=0.75, paving="mulch", edging="stone"),  # raised, along the front
        # From the side gate to the patio.
        Ground("Gravel path", "walk", -12, -10, 12, 30, paving="gravel", outline=((-12, 20), (-8, 20), (0, -6), (0, -10))),
    ),
    trees=(
        Tree("Front yard tree", "deciduous", 14, 44, height=30, spread=24, ring=4),
        Tree("Backyard tree", "deciduous", 8, -30, height=32, spread=26),
        Tree("Spruce", "evergreen", 62, -22, height=24, spread=12),
    ),
    sheds=(Shed("Shed", 52, -42, 10, 8),),
    fences=(
        Fence("Left fence", ((0, 20), (-20, 20), (-20, -45)), gates=((-10, 20, 4),)),
        Fence("Back fence", ((-20, -45), (75, -45)), height=4, kind="chain link"),
        Fence("Right fence", ((75, -45), (75, 20), (58, 20))),
    ),
    steps=(
        Steps("Porch steps", 28.5, 34, 3, 2, bottom=-1, top=-0.1, up="back"),
        Steps("Deck steps", 36, -12, 4, 2, bottom=-1, top=-0.1, up="front", paving="wood"),
        Steps("Garage steps", 36.4, 16, 2, 3, bottom=-1, top=0, up="left", room="Garage"),
    ),
)
