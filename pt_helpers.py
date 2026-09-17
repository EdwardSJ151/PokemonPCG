# ===========================================================================
# PLATINUM
# Platinum behavior_name values ARE symbolic (e.g. "TILE_BEHAVIOR_TALL_GRASS"),
# so substring matching works the same way as Emerald.
# BIKE_BRIDGE before BRIDGE (substring subset). VERY_TALL_GRASS before TALL_GRASS.
# MUD_DEEP_WITH_GRASS and MUD_WITH_GRASS before MUD_DEEP and MUD.
# SNOW_DEEPEST/DEEPER/DEEP before SNOW_WITH_SHADOWS/SHALLOW (all have SNOW).
# ESCALATOR_FLIP_FACE before ESCALATOR.
# JUMP_* before ICE: "TWICE" ends with "ICE", so JUMP_EAST_TWICE etc. would
# otherwise match ICE first and render as 'i' instead of their jump char.
# ===========================================================================

PLATINUM_SYMBOLS = [
    # Water
    ("WATER_SEA",                  "≈", "Sea water"),
    ("WATER_RIVER",                "~", "River water"),
    ("WATERFALL",                  "↓", "Waterfall"),
    ("SHALLOW_WATER",              "~", "Shallow water"),
    ("PUDDLE_NO_SPLASHING",        "~", "Puddle"),
    ("PUDDLE",                     "~", "Puddle"),
    # Grass
    ("VERY_TALL_GRASS",            '"', "Very tall grass"),
    ("TALL_GRASS",                 '"', "Tall grass"),
    # Snow — deepest first so "DEEP" doesn't short-circuit them
    ("SNOW_DEEPEST",               ":", "Deepest snow"),
    ("SNOW_DEEPER",                ":", "Deeper snow"),
    ("SNOW_DEEP",                  ":", "Deep snow"),
    ("SNOW_WITH_SHADOWS",          ".", "Snow (shadow)"),
    ("SNOW_SHALLOW",               ".", "Shallow snow"),
    # Mud — compound before simple
    ("MUD_DEEP_WITH_GRASS",        '"', "Deep muddy grass"),
    ("MUD_WITH_GRASS",             '"', "Muddy grass"),
    ("MUD_DEEP",                   "m", "Deep mud"),
    ("MUD",                        "m", "Mud"),
    # Jumps — before ICE: "TWICE" ends with "ICE", matching it first
    ("JUMP_EAST",                  "►", "Jump E"),
    ("JUMP_WEST",                  "◄", "Jump W"),
    ("JUMP_NORTH",                 "▲", "Jump N"),
    ("JUMP_SOUTH",                 "▼", "Jump S"),
    # Ice / sand
    ("ICE",                        "i", "Ice"),
    ("SAND",                       ".", "Sand"),
    # Cave / mountain
    # TILE_BEHAVIOR_CAVE_FLOOR is deliberately ABSENT — same reason as in
    # HEARTGOLD_SYMBOLS. It is ordinary walkable floor, so it must render blank
    # like any other walkable ground and must fall through to the bridge/stair
    # tests below; a behavior char here returns early and swallows every
    # staircase and bridge in every cave. 'c' marks a warp INTO a cave.
    # OLD_CHATEAU_FLOOR is deliberately ABSENT for the same reason, removed
    # 2026-08-07. It was 'c', painting all 1254 walkable tiles of the Old
    # Chateau — a mansion, not a cave — and swallowing the 6 staircases in
    # MAP_HEADER_OLD_CHATEAU before the stair test could see them.
    ("MOUNTAIN_FLOOR",             "^", "Mountain floor"),
    # Berry patch
    ("BERRY_PATCH",                "b", "Berry patch"),
    # Rock climb
    ("ROCK_CLIMB_N_S",             "R", "Rock climb (N/S)"),
    ("ROCK_CLIMB_E_W",             "R", "Rock climb (E/W)"),
    # Bridges — bike bridge before generic bridge
    ("BIKE_BRIDGE",                "≡", "Bike bridge"),
    ("BRIDGE",                     "=", "Bridge"),
    # Directional blocks
    ("BLOCK_NORTH_AND_EAST",       "▐", "Block NE"),
    ("BLOCK_NORTH_AND_WEST",       "▌", "Block NW"),
    ("BLOCK_SOUTH_AND_EAST",       "▐", "Block SE"),
    ("BLOCK_SOUTH_AND_WEST",       "▌", "Block SW"),
    ("BLOCK_NORTH_AND_SOUTH",      "│", "Block N+S"),
    ("BLOCK_EAST_AND_WEST",        "─", "Block E+W"),
    ("BLOCK_EASTWARD",             "▐", "Block E"),
    ("BLOCK_WESTWARD",             "▌", "Block W"),
    ("BLOCK_NORTHWARD",            "▀", "Block N"),
    ("BLOCK_SOUTHWARD",            "▄", "Block S"),
    # Slides
    ("SLIDE_EASTWARD",             "s", "Slide E"),
    ("SLIDE_WESTWARD",             "s", "Slide W"),
    ("SLIDE_NORTHWARD",            "s", "Slide N"),
    ("SLIDE_SOUTHWARD",            "s", "Slide S"),
    # Bike
    ("BIKE_RAMP_EASTWARD",         "r", "Bike ramp E"),
    ("BIKE_RAMP_WESTWARD",         "r", "Bike ramp W"),
    ("BIKE_SLOPE_BOTTOM",          "r", "Bike slope bottom"),
    ("BIKE_SLOPE_TOP",             "r", "Bike slope top"),
    ("BIKE_PARKING",               "r", "Bike parking"),
    # Warps / doors — ESCALATOR_FLIP_FACE before ESCALATOR
    ("WARP_STAIRS_EAST",           "D", "Warp stairs (E)"),
    ("WARP_STAIRS_WEST",           "D", "Warp stairs (W)"),
    ("WARP_ENTRANCE_EAST",         "D", "Warp entrance (E)"),
    ("WARP_ENTRANCE_WEST",         "D", "Warp entrance (W)"),
    ("WARP_ENTRANCE_NORTH",        "D", "Warp entrance (N)"),
    ("WARP_ENTRANCE_SOUTH",        "D", "Warp entrance (S)"),
    ("WARP_PANEL",                 "D", "Warp panel"),
    ("WARP_EAST",                  "D", "Warp E"),
    ("WARP_WEST",                  "D", "Warp W"),
    ("WARP_NORTH",                 "D", "Warp N"),
    ("WARP_SOUTH",                 "D", "Warp S"),
    ("DOOR",                       "D", "Door"),
    ("ESCALATOR_FLIP_FACE",        "e", "Escalator (down)"),
    ("ESCALATOR",                  "E", "Escalator (up)"),
    # Gym puzzles
    # The three tiers of Pastoria Gym's water-level puzzle, and the one place in
    # the game where passability has no static answer: each tier is walkable
    # only at its own water height (gym_features.c:463), which the buttons move
    # at runtime, so the land data reports all 26 as passable. One bare
    # "PASTORIA_GYM" keyword used to match all three by substring and collapse
    # them into a single legend line.
    ("PASTORIA_GYM_H_GROUND",      "U", "Pastoria Gym high ground (walkable at low water)"),
    ("PASTORIA_GYM_M_GROUND",      "U", "Pastoria Gym middle ground"),
    ("PASTORIA_GYM_L_GROUND",      "U", "Pastoria Gym low ground (walkable at high water)"),
    # Interactables
    ("PC",                         "p", "PC"),
    ("TOWN_MAP",                   "⊞", "Town map"),
    ("TV",                         "v", "TV"),
    ("TABLE",                      "q", "Table / counter"),
    ("BOOKSHELF_1",                "B", "Bookshelf"),
    ("BOOKSHELF_2",                "B", "Bookshelf"),
    ("SMALL_BOOKSHELF",            "B", "Small bookshelf"),
    ("MART_SHELF",                 "B", "Mart shelf"),
    ("TRASH_CAN",                  "y", "Trash can"),
    # Reflection
    ("REFLECTIVE",                 "~", "Reflective floor"),
]

# Building keywords for Platinum warp `dest_header_id` values
# (e.g. "MAP_HEADER_JUBILIFE_CITY_POKECENTER_1F")
BUILDING_KEYWORDS_PLATINUM = [
    ("POKECENTER",        "P", "Pokemon Center"),
    ("POKEMON_CENTER",    "P", "Pokemon Center"),
    ("POKEMART",          "$", "PokeMart"),
    ("MART",              "$", "PokeMart"),
    ("GYM",               "G", "Gym"),
    ("JUBILIFE_TV",       "T", "Jubilife TV"),
    ("GLOBAL_TERMINAL",   "Q", "Global Terminal"),
    ("POKETCH_CO",        "K", "Poketch Company"),
    ("SNOWPOINT_TEMPLE",  "J", "Snowpoint Temple"),
    ("TRAINERS_SCHOOL",   "S", "Trainers School"),
    ("SCHOOL",            "S", "School"),
    ("LAB",               "K", "Lab / Research"),
    ("INSTITUTE",         "K", "Institute"),
    ("HOUSE",             "h", "House"),
    ("HOME",              "h", "House"),
    ("CONDOMINIUM",       "h", "Condominium"),
    ("VILLA",             "h", "Villa"),
]
