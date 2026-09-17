# ===========================================================================
# HEARTGOLD
# Keyed by the TILE_BEHAVIOR enum names that heartgold_data parses out of
# pokeheartgold/include/constants/metatile_behavior.h. Lookup is an EXACT
# dict match (not a substring scan like Emerald/Platinum), so unnamed enum
# values keep their numeric form ("TILE_BEHAVIOR_96") and match exactly.
# ===========================================================================

HEARTGOLD_SYMBOLS: dict[str, tuple[str, str]] = {
    # Keyed by the real TILE_BEHAVIOR enum names parsed from
    # pokeheartgold/include/constants/metatile_behavior.h.
    "TILE_BEHAVIOR_WATER_RIVER": ("~", "River water"),
    "TILE_BEHAVIOR_WATERFALL": ("↓", "Waterfall"),
    "TILE_BEHAVIOR_WHIRLPOOL": ("o", "Whirlpool"),
    "TILE_BEHAVIOR_WATER_SEA": ("≈", "Sea water"),
    "TILE_BEHAVIOR_PUDDLE": ("~", "Puddle"),
    "TILE_BEHAVIOR_SHALLOW_WATER": ("~", "Shallow water"),
    "TILE_BEHAVIOR_PUDDLE_NO_SPLASHING": ("~", "Puddle (no splash)"),
    "TILE_BEHAVIOR_VERY_TALL_GRASS": ('"', "Very tall grass"),
    "TILE_BEHAVIOR_TALL_GRASS": ('"', "Tall grass"),
    # TILE_BEHAVIOR_CAVE_FLOOR is deliberately ABSENT. It is ordinary walkable
    # floor and must fall through to the stair/bridge tests before landing on
    # the default ' ', exactly like outdoor behaviour-0 ground — mapping it to
    # a char here (it was 'c') returns early and swallows every staircase and
    # bridge in every cave. 'c' marks a warp INTO a cave, not the floor of one.
    "TILE_BEHAVIOR_ICE": ("i", "Ice"),
    "TILE_BEHAVIOR_SAND": (".", "Sand"),
    "TILE_BEHAVIOR_MAGMA": ("z", "Magma"),
    "TILE_BEHAVIOR_REFLECTIVE": ("~", "Reflective floor"),
    "TILE_BEHAVIOR_MUD": ("m", "Mud"),
    "TILE_BEHAVIOR_SNOW": (".", "Snow"),
    "TILE_BEHAVIOR_ROCK_CLIMB_NORTH_SOUTH": ("R", "Rock climb (N/S)"),
    "TILE_BEHAVIOR_ROCK_CLIMB_EAST_WEST": ("R", "Rock climb (E/W)"),
    # Behaviour 6 is HG's GENERIC tree wall, not "a headbutt tree": Route 30 has
    # 1119 of them but headbutt.json lists only 75 real headbutt trees there.
    # The 75 get overlaid with 'H' from headbutt.json; the rest stay ♣.
    "TILE_BEHAVIOR_HEADBUTT": ("♣", "Tree"),
    "TILE_BEHAVIOR_JUMP_EAST": ("►", "Jump E"),
    "TILE_BEHAVIOR_JUMP_WEST": ("◄", "Jump W"),
    "TILE_BEHAVIOR_JUMP_NORTH": ("▲", "Jump N"),
    "TILE_BEHAVIOR_JUMP_SOUTH": ("▼", "Jump S"),
    "TILE_BEHAVIOR_LADDER_NORTH": ("L", "Ladder (N)"),
    "TILE_BEHAVIOR_LADDER_SOUTH": ("L", "Ladder (S)"),
    "TILE_BEHAVIOR_LADDER_DOWN": ("L", "Ladder (down)"),
    "TILE_BEHAVIOR_SLIDE_EAST": ("s", "Slide E"),
    "TILE_BEHAVIOR_SLIDE_WEST": ("s", "Slide W"),
    "TILE_BEHAVIOR_SLIDE_NORTH": ("s", "Slide N"),
    "TILE_BEHAVIOR_SLIDE_SOUTH": ("s", "Slide S"),
    "TILE_BEHAVIOR_WARP_STAIRS_EAST": ("D", "Warp stairs (E)"),
    "TILE_BEHAVIOR_WARP_STAIRS_WEST": ("D", "Warp stairs (W)"),
    "TILE_BEHAVIOR_96": ("D", "Warp stairs"),
    "TILE_BEHAVIOR_WARP_ENTRANCE_EAST": ("D", "Warp entrance (E)"),
    "TILE_BEHAVIOR_WARP_ENTRANCE_WEST": ("D", "Warp entrance (W)"),
    "TILE_BEHAVIOR_WARP_ENTRANCE_NORTH": ("D", "Warp entrance (N)"),
    "TILE_BEHAVIOR_WARP_ENTRANCE_SOUTH": ("D", "Warp entrance (S)"),
    "TILE_BEHAVIOR_WARP_PANEL": ("D", "Warp panel"),
    "TILE_BEHAVIOR_104": ("D", "Warp (misc)"),
    "TILE_BEHAVIOR_DOOR": ("D", "Door"),
    "TILE_BEHAVIOR_ESCALATOR_FLIP_FACE": ("e", "Escalator (down)"),
    "TILE_BEHAVIOR_ESCALATOR": ("E", "Escalator (up)"),
    "TILE_BEHAVIOR_WARP_EAST": ("D", "Warp E"),
    "TILE_BEHAVIOR_WARP_WEST": ("D", "Warp W"),
    "TILE_BEHAVIOR_WARP_NORTH": ("D", "Warp N"),
    "TILE_BEHAVIOR_WARP_SOUTH": ("D", "Warp S"),
    "TILE_BEHAVIOR_PC": ("p", "PC"),
    "TILE_BEHAVIOR_TOWN_MAP": ("⊞", "Town map"),
    "TILE_BEHAVIOR_TV": ("v", "TV"),
    "TILE_BEHAVIOR_128": ("q", "Table / counter"),
    "TILE_BEHAVIOR_SMALL_BOOKSHELF_1": ("B", "Small bookshelf"),
    "TILE_BEHAVIOR_BOOKSHELF_1": ("B", "Bookshelf"),
    "TILE_BEHAVIOR_BOOKSHELF_2": ("B", "Bookshelf"),
    "TILE_BEHAVIOR_EMPTY_TRASH_CAN": ("y", "Trash can"),
    "TILE_BEHAVIOR_MART_SHELF_1": ("B", "Mart shelf"),
}

# Building keywords for HG warp `header` values (e.g. "MAP_GOLDENROD_POKECENTER_1F")
BUILDING_KEYWORDS_HG = [
    ("POKEMON_CENTER",   "P", "Pokemon Center"),
    ("POKECENTER",       "P", "Pokemon Center"),
    ("DEPT",             "M", "Department store"),
    ("DEPARTMENT",       "M", "Department store"),
    ("MART",             "$", "PokeMart"),
    ("GYM",              "G", "Gym"),
    ("RADIO_TOWER",      "⌁", "Radio Tower"),
    ("GLOBAL_TERMINAL",  "Q", "Global Terminal"),
    ("MAGNET_TRAIN",     "T", "Magnet Train Station"),
    ("SCHOOL",           "S", "School"),
    ("LAB",              "K", "Lab / Research"),
    ("INSTITUTE",        "K", "Institute"),
    ("HOUSE",            "h", "House"),
    ("HOME",             "h", "House"),
    ("GAME_CORNER",      "X", "Game Corner"),
]



# model name → polygon index (0-based) to use for footprint calculation.
# Only polygon meshes whose decorative geometry inflates the bounding box
# beyond the building body are listed here.
HG_POLYGON_OVERRIDES: dict[str, int | list[int] | dict] = {
    "en_suzu":  2,  # Bellchime Tower: base ring (poly2) covers rows 11-16 incl entrance
    "en_sekib": 0,  # Bellchime entrance: poly4 adds a spurious column at row 12
    "gate_b":   0,  # Azalea (and others): poly1 extends c1 by 0.25 tile, claims one extra col
    "en_yake":  0,  # Ecruteak shrine: global bbox overclaims cols 21-28; poly0 narrows to 22-26
    "sk_gym":   0,  # Fuchsia gym: global r0=51.41 leaks into row 51; poly0 starts at r0=52.24
    "sk_h01":   0,  # Fuchsia house (adj. to gym): global r0=51.38 leaks into row 51; poly0 at r0=52.24
    "ko_depart": 1, # Goldenrod dept store: global c1=80.66 claims col 80; poly1 ends at c1=79.69
    "si_radio":  1, # Lavender radio tower: global c1=25.58 claims col 25; poly1 ends at c1=24.93 (loses row 3)
    "si_temp":   0, # Lavender house (House of Memories): global c1=26.58 claims col 26; poly0 ends at c1=26.05
    "ta_depart":     0, # Celadon dept store: global c1=21.91 claims col 21; poly0 ends at c1=21.25 (same rows)
    "ta_restaurant": 0, # Celadon restaurant: global c1=44.79 claims col 44; poly0 ends at c1=44.20 (same rows)
    # Pokémart: global bbox includes the sign post column (poly1, dc 2.48–2.77).
    # poly1 is the sign alone, but poly2 also reaches max_dc=3.0 (roof surface),
    # so excluding poly1 and unioning the rest still yields max_dc=3.0. Cap
    # max_dc at poly0's body edge (2.125) while keeping the global max_dr so
    # the entrance row is not lost.
    "fs": {"max_dc": 2.125},
    # Pokemon Center: global max_dc=3.0 includes a decorative sign post column
    # (same pattern as "fs"). poly0 is symmetric (max_dc=2.625, same rows as
    # global), so a plain polygon=0 override would work, but the dict form is
    # used for consistency with "fs" and to be explicit about what is trimmed.
    "pc": 0,
    # Silph Co: global bbox is 12 tiles wide (min_dc=-5.5, max_dc=6.0), but the
    # actual building body is 10 tiles wide (cols 24-33 relative to map origin).
    # poly2 has the correct column bounds (min_dc=-5.0, max_dc=5.0) but its
    # r0=30.80 excludes row 30, which the building visibly occupies (global
    # r0=30.25 correctly covers it). Use a dict override to apply poly2's column
    # bounds while retaining the global row range.
    "ybc_slf": {"min_dc": -5.0, "max_dc": 5.0},
    # Saffron generic house: global max_dc=2.5 puts c1=16.625, only 0.875 tiles
    # from the adjacent Copycat House warp (col=17, center=17.5). The ±1 slack
    # in the enterable check gates this building even though it has no warp.
    # poly1 (max_dc=1.8828) shrinks c1 to 16.005, 1.5 tiles from that warp —
    # safely beyond the slack, so the building correctly stays █.
    "ybc_h02": 1,
    # Olivine flag/banner building: global c1=29.863 is just 0.363 tiles past
    # the Pokémart warp at col=29 (center=29.5), which gates it even though the
    # building has no warp of its own. poly3 (c1=27.919) puts c1+1=28.919 below
    # 29.5, breaking the false gating so the building correctly stays █.
    "as_hata": 3,
    # Saffron gym-area building: the building body is L-shaped — a wide section
    # at rows 13-15 (cols 4-12, min_dc=-6.5) above a narrower bottom row
    # (cols 7-12, min_dc=-3.5). The fence tiles at cols 4-6 on row 16 must stay
    # █ rather than being painted ▓ as part of the building. Two footprints:
    #   main body  — poly0 column range, global rows (includes row 16)
    #   left wing  — poly7 column range and rows (stops before the fence row)
    "ybc_lnr": [
        {"min_dc": -3.5, "max_dc": 2.5},
        {"min_dc": -6.5, "max_dc": 2.5, "min_dr": -1.5, "max_dr": 1.5},
    ],
    # Kanto route gatehouses: global max_dr=3.625 puts r1=14.62 (center_r=11),
    # ceil=15 → row 14 is painted even though it is road/open terrain, not the
    # building body. No polygon brings max_dr below 3.0 (best is poly0 at 3.188).
    # Cap at 2.875 → r1=13.875, row 13 is the last painted row. Warps at rows
    # 10-11 remain well within the ±1 enterable slack (r1+1=14.875 > 11.5).
    "kn_gate_l": {"max_dr": 2.875},
    "kn_gate_r": {"max_dr": 2.875},
}
