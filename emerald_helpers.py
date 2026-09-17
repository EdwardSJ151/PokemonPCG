import functools
import re
import sys

# ===========================================================================
# EMERALD
# Behavior-name substring → (char, description). First match wins.
# Metatile-label lookups and metatile-ID lookups are checked before this.
# ===========================================================================

EMERALD_SYMBOLS = [
    # Water
    ("DEEP_WATER",                "≈", "Deep water"),
    ("OCEAN_WATER",               "≈", "Ocean water"),
    ("INTERIOR_DEEP_WATER",       "≈", "Interior deep water"),
    ("SOOTOPOLIS_DEEP_WATER",     "≈", "Sootopolis deep water"),
    ("POND_WATER",                "~", "Pond/shallow water"),
    ("SHALLOW_WATER",             "~", "Pond/shallow water"),
    ("PUDDLE",                    "~", "Puddle"),
    ("REFLECTION_UNDER_BRIDGE",   "~", "Under-bridge reflection"),
    ("WATERFALL",                 "↓", "Waterfall"),
    ("EASTWARD_CURRENT",          "→", "Current E"),
    ("WESTWARD_CURRENT",          "←", "Current W"),
    ("NORTHWARD_CURRENT",         "↑", "Current N"),
    ("SOUTHWARD_CURRENT",         "↓", "Current S"),
    ("SEAWEED",                   "ψ", "Seaweed"),
    # Grass / encounters
    ("TALL_GRASS",                '"', "Tall grass"),
    ("LONG_GRASS",                '"', "Long grass"),
    # Short grass is just walkable ground with a decal — left out so it falls
    # through to the passable default (" "). Restore the line to mark it again.
    # ("SHORT_GRASS",             "'", "Short grass"),
    # MB_INDOOR_ENCOUNTER is deliberately ABSENT, and MB_CAVE below with it.
    # metatile_behavior.c gives both exactly TILE_FLAG_HAS_ENCOUNTERS and
    # nothing else: they say "wild Pokemon spawn here", not what the tile is.
    # Drawing them as surfaces made every cave floor 'c' and every Abandoned
    # Ship room short grass, and — because this table returns on first match —
    # painted 8927 impassable cave WALLS as floor too. Same trap as rule 8.
    # Ice
    ("CRACKED_ICE",               "x", "Cracked ice"),
    ("THIN_ICE",                  "i", "Ice"),
    ("ICE",                       "i", "Ice"),
    # Sand / dirt
    ("DEEP_SAND",                 ":", "Deep sand"),
    ("SAND",                      ".", "Sand"),
    ("ASHGRASS",                  "a", "Ash grass"),
    ("FOOTPRINTS",                "f", "Footprints"),
    ("MUDDY_SLOPE",               "m", "Muddy slope"),
    ("BUMPY_SLOPE",               "k", "Bumpy slope (bike required)"),
    # Lava / hot
    ("HOT_SPRINGS",               "♨", "Hot springs"),
    # Cave / mountain — 'c' marks a warp INTO a cave, never the floor of one.
    # These held 'c' only because "CAVE" matched them as a substring; listed
    # explicitly so that removing MB_CAVE does not take them along with it.
    ("SHOAL_CAVE_ENTRANCE",        "c", "Cave entrance"),
    # The secret base spots stay OFF: they are the wall art you Secret Power a
    # hole into, and they render as the wall they are. Uncomment to mark them.
    # ("SECRET_BASE_SPOT_RED_CAVE",   "c", "Secret base spot"),
    # ("SECRET_BASE_SPOT_BROWN_CAVE", "c", "Secret base spot"),
    # ("SECRET_BASE_SPOT_YELLOW_CAVE","c", "Secret base spot"),
    # ("SECRET_BASE_SPOT_BLUE_CAVE",  "c", "Secret base spot"),
    # MOUNTAIN_TOP is NOT here — it splits on collision, see resolve_cell().
    # Vertical movement
    ("UP_ESCALATOR",              "E", "Stairs / escalator up"),
    ("DOWN_ESCALATOR",            "e", "Stairs / escalator down"),
    ("LADDER",                    "L", "Ladder"),
    ("CRACKED_FLOOR_HOLE",        "O", "Cracked floor hole"),
    ("CRACKED_FLOOR",            "⌀", "Cracked floor (breaks on step)"),
    ("LAVARIDGE_GYM_1F_WARP",    "O", "Lavaridge Gym sand hole (1F)"),
    ("LAVARIDGE_GYM_B1F_WARP",   "O", "Lavaridge Gym sand hole (B1F)"),
    # Jumps — diagonals before cardinals
    ("JUMP_NORTHEAST",            "↗", "Jump NE"),
    ("JUMP_NORTHWEST",            "↖", "Jump NW"),
    ("JUMP_SOUTHEAST",            "↘", "Jump SE"),
    ("JUMP_SOUTHWEST",            "↙", "Jump SW"),
    ("JUMP_EAST",                 "►", "Jump E"),
    ("JUMP_WEST",                 "◄", "Jump W"),
    ("JUMP_NORTH",                "▲", "Jump N"),
    ("JUMP_SOUTH",                "▼", "Jump S"),
    # Impassable directional walls — diagonals first
    ("IMPASSABLE_NORTHEAST",      "▐", "Wall (NE)"),
    ("IMPASSABLE_NORTHWEST",      "▌", "Wall (NW)"),
    ("IMPASSABLE_SOUTHEAST",      "▐", "Wall (SE)"),
    ("IMPASSABLE_SOUTHWEST",      "▌", "Wall (SW)"),
    ("IMPASSABLE_EAST",           "▐", "Wall (E)"),
    ("IMPASSABLE_WEST",           "▌", "Wall (W)"),
    ("IMPASSABLE_NORTH",          "▀", "Wall (N)"),
    ("IMPASSABLE_SOUTH",          "▄", "Wall (S)"),
    ("IMPASSABLE_SOUTH_AND_NORTH","│", "Wall (N+S)"),
    ("IMPASSABLE_WEST_AND_EAST",  "─", "Wall (W+E)"),
    # Slides / forced movement
    ("SLIDE",                     "s", "Slide"),
    ("WALK",                      "w", "Forced walk"),
    # Bridges
    ("BRIDGE",                    "=", "Bridge"),
    # Berry / rail
    ("BERRY_TREE_SOIL",           "b", "Berry soil"),
    ("RAIL",                      "n", "Rail"),
    # Interactables (indoor)
    ("PC",                        "p", "PC"),
    ("COUNTER",                   "q", "Counter"),
    ("TELEVISION",                "v", "Television"),
    ("BOOKSHELF",                 "B", "Bookshelf"),
    # Doors
    ("DOOR",                      "D", "Door"),
    # Catch-all impassable
    ("IMPASSABLE",                "█", "Impassable"),
    ("SECRET_BASE_WALL",          "█", "Secret base wall"),
]

# Metatile-label substrings → char (Emerald only, checked before behavior table)
METATILE_LABEL_SYMBOLS = [
    # Grass-based cliff base renders as plain wall for now; the rock and sand
    # bases keep '▄'. Restore the '▄' line to distinguish it again.
    ("RockWall_GrassBase",  "█", "Cliff wall base (grass)"),
    # ("RockWall_GrassBase",  "▄", "Cliff wall base (grass)"),
    ("RockWall_RockBase",   "█", "Cliff wall base (rock)"),
    ("RockWall_SandBase",   "█", "Cliff wall base (sand)"),
    # ("RockWall_RockBase", "▄", "Cliff wall base (rock)"),
    # ("RockWall_SandBase", "▄", "Cliff wall base (sand)"),
    ("CaveEntrance_Bottom", "c", "Cave entrance"),
]

# Known stair metatile IDs (Emerald, primary tileset gTileset_General).
# 0x0AF and 0x0CF are stair tiles; 0x0AF can appear alone.
STAIR_METATILE_IDS = {0x0AF, 0x0CF}

# Stair metatile IDs for secondary tilesets (tileset-scoped, like CAVE_BRIDGE_METATILE_IDS).
# 0x204 in gTileset_Cave is the cave step-down landing tile; appears exactly 12 times
# in LAYOUT_ARTISAN_CAVE_B1F and functions as a stair in all cave layouts that use it.
CAVE_STAIR_METATILE_IDS: dict[str, frozenset] = {
    "gTileset_Cave": frozenset({0x204}),
}

# Cycling-bridge plank metatile IDs (Emerald, gTileset_Fortree only).
# 0x210-0x21F are bridge deck planks; same IDs are lamp posts in other secondary tilesets.
CYCLING_BRIDGE_METATILE_IDS = set(range(0x210, 0x220))

# Cave bridge deck IDs whose tileset carries no bridge behaviour at all, so
# _bridge_art_twins() cannot find them (deck is empty — no source to compare
# against).  Meteor Falls 1F 1R has a 2×5 rope bridge at rows 18-19 cols 18-22:
#   top row  0x260 (left edge), 0x261 (×3 middle), 0x262 (right edge)
#   bottom row 0x268/0x269/0x26A in the same order
# All six use MB_NORMAL (0x00), identical to regular cave floor.
CAVE_BRIDGE_METATILE_IDS: dict[str, frozenset] = {
    "gTileset_MeteorFalls": frozenset({0x260, 0x261, 0x262, 0x268, 0x269, 0x26A}),
    # Route114's rope bridge — gTileset_Fallarbor has no bridge behaviours, so
    # _bridge_art_twins() returns empty and cannot find these.  All six are
    # MB_NORMAL at elevation=3 (same as surrounding mountain path).
    #   top row  0x2C0 (left edge), 0x28B (×2 middle), 0x2C1 (right edge)
    #   body rows 0x2C8 (left edge), 0x293 (×2 middle), 0x2C9 (right edge)
    # Only appear in LAYOUT_ROUTE114; absent from the four other Fallarbor layouts.
    "gTileset_Fallarbor": frozenset({0x2C0, 0x28B, 0x2C1, 0x2C8, 0x293, 0x2C9}),
}

# Secondary tilesets whose layouts are genuine caves/tunnels.  Only these get
# 'c' placed on passable floor warp tiles that lead to outdoor destinations.
# (Wall-tile warps via is_plain_wall() are safe regardless — building interiors
# never have plain-wall warps, so no tileset guard is needed there.)
_EMERALD_CAVE_SECONDARY_TILESETS = frozenset({
    "gTileset_Cave",         # standard caves (Victory Road, Seafloor Cavern, …)
    "gTileset_MeteorFalls",  # Meteor Falls
    "gTileset_MirageTower",  # Mirage Tower
    "gTileset_NavelRock",    # Navel Rock (event island)
    "gTileset_RusturfTunnel",# Rusturf Tunnel
})

# Tree metatile IDs (Emerald, primary gTileset_General).
#
# Emerald has no tree *behavior* — a tree is MB_NORMAL with collision set, i.e.
# indistinguishable from a cliff or a wall in the map data. What does
# distinguish it is the art: the metatile draws tree tile-graphics. This set was
# derived from the tileset, not guessed:
#
#   1. seed from the metatiles the decomp itself names as trees
#      (METATILE_General_Grass_TreeUp/Left/Right, TallGrass_Tree*) plus the
#      canopy and trunk metatiles those seeds' graphics identify;
#   2. take the 8x8 tile-graphic indices those metatiles draw, minus the shared
#      ground quads (grass 0x02/0x03, tall grass 0x10/0x11/0x20/0x21) and the
#      tree *shadow* decal 0xCC/0xCD/0xAE/0xAF, which is painted on the tile
#      NEXT to a tree and so appears over water, sand and cliffs too;
#   3. every metatile drawing any remaining tree graphic is a tree.
#
# That closure yields exactly these 37 and nothing else; all 37 were confirmed
# by rendering the tileset. They account for 17980 impassable tiles, a third of
# every impassable primary-tileset tile in the outdoor maps. Secondary tilesets
# contribute no trees: no secondary tileset reuses any of these tile graphics
# (checked pixel-wise across all of them), and their impassable metatiles are
# cave rock, coral and buildings.
#
# The IDs are only meaningful under gTileset_General, but the check needs no
# tileset guard: across all 203 layouts whose primary is gTileset_Building or
# gTileset_SecretBase, exactly zero impassable cells carry one of these IDs.
TREE_METATILE_IDS = frozenset({
    0x00E, 0x00F, 0x016, 0x017, 0x01D, 0x01E, 0x01F, 0x025, 0x02D, 0x02E,
    0x02F, 0x035, 0x040, 0x0C6, 0x0C7, 0x0CE, 0x193, 0x1D4, 0x1D5, 0x1D6,
    0x1D7, 0x1DC, 0x1DD, 0x1DE, 0x1DF, 0x1E4, 0x1E5, 0x1E6, 0x1E7, 0x1EC,
    0x1ED, 0x1F2, 0x1F3, 0x1F4, 0x1F5, 0x1FC, 0x1FD,
})

# Trees drawn by a SECONDARY tileset, so keyed by tileset name: every id here is
# >= 0x200 and therefore tileset-scoped. Flattening this to a plain set would be
# a bug, not a simplification — 0x310 alone is also used by Lavaridge (50x),
# Cave, Lilycove, Facility, Shop and a dozen others, where it is not a tree.
#
# These are trees the primary-tileset closure behind TREE_METATILE_IDS cannot
# reach by construction: it derives from gTileset_General's tile graphics, and a
# secondary tileset draws its own art with its own tile indices.
#
# Regenerate with tools/derive_secondary_tree_metatiles.py, which explains the
# derivation. In short: a metatile is background + art, and one tree is many
# metatiles because the same canopy and trunk are composited over sand, grass,
# rock, dirt and the shadow decal. The ART is what is constant, so the closure
# keys on the layer-1 tile graphics, and the impassable uses are the trunks and
# canopy walls while the passable ones are canopy overhanging walkable ground.
SECONDARY_TREE_METATILE_IDS: dict[str, frozenset] = {
    # 12 of the 28 metatiles that draw Mossdeep's tree art; the other 16 are
    # only ever walked on. Verified against layout_mossdeep_city_manual.txt —
    # every hand-marked tile predicted, none missed.
    "gTileset_Mossdeep": frozenset({
        0x2F8, 0x2F9,  # trunk over rock
        0x310, 0x311,  # trunk over sand
        0x31C, 0x31D,  # canopy over dirt
        0x322, 0x323,  # alt trunk over grass
        0x324, 0x325,  # alt canopy over rock
        0x355, 0x356,  # alt canopy over grass
    }),
    # The tree line along the north edge, where the hedge around Wally's House
    # is drawn over the tree instead of over grass. These are the mirror image
    # of TREE_BACKED_METATILE_IDS: there, building art sits on top of canopy;
    # here, hedge art does. The tree is on the MIDDLE layer (tiles 0x1A0-0x1A5,
    # which are primary graphics already in the set behind TREE_METATILE_IDS)
    # with the hedge tiles on top. Compare 0x23C-0x23E, the same hedge over
    # plain grass. All four are impassable in every use.
    "gTileset_Petalburg": frozenset({
        0x24D, 0x24E,  # hedge over tree, run
        0x255, 0x256,  # hedge over tree, west and east corner
    }),
    # Dewford's palms — three art groups, so the closure needs three seeds
    # (0x243 canopy, 0x23A its shadowed base, 0x211 the second palm). 19
    # metatiles draw that art; these 5 are the ones the maps use impassably,
    # the other 3 (0x20B-0x20D) are walkable overhang. None is used both ways.
    # Verified against layout_dewford_town_manual.txt: all 58 hand-marked
    # cells predicted, none missed, nothing extra.
    #
    # 0x211/0x212/0x218 were also in BUILDING_METATILE_IDS and rendered ▓ —
    # tree art next to the Gym that the door-proximity derivation swept up.
    # Listing them here settles it without touching that table: the art mask
    # runs through is_plain_wall(), so a cell that already reads ♣ is no
    # longer eligible.
    "gTileset_Dewford": frozenset({
        0x211, 0x212, 0x218,  # second palm
        0x23A,                # shadowed base
        0x243,                # canopy
    }),
    # Mauville's leafy tree. Reached from no primary seed: its canopy is tiles
    # 0x26/0x36, which are primary tile indices that NO primary metatile draws,
    # so TREE_METATILE_IDS cannot see them however it is seeded. Seeding 0x2C9
    # closes to 8 metatiles — the same tree over grass, sand, dirt and road —
    # of which 6 are used, all impassably, none both ways.
    #
    # 0x3A8/0x3A9 are the other two and are deliberately LEFT OUT: they are half
    # canopy, half house window, i.e. building edge art over a tree, which is
    # what TREE_BACKED_METATILE_IDS is for. Calling them trees would draw ♣ over
    # a wall. That is a judgement on the art, not something the closure decides.
    "gTileset_Mauville": frozenset({
        0x226,         # tree over grass
        0x2C9,         # tree over sand
        0x376, 0x377,  # tree over dirt, west and east
    }),
    # Fortree. 0x22A is a tree by construction: its art, on BOTH layers, is
    # nothing but tiles 0x4/0x14, and in the primary tileset those two tiles
    # belong to exactly 0xC6 and 0xC7, both already in TREE_METATILE_IDS. It is
    # also the only Fortree metatile whose art is those tiles and nothing else —
    # the city is built on the canopy, so canopy-as-background is everywhere and
    # means nothing; canopy and nothing else is the tree.
    #
    # 0x253 and 0x273 do NOT follow from that. Both are rock on the middle layer
    # (0x302 0x303 0x312 0x313) with 0x26/0x36 and 0x6/0x7/0x16 on top, and no
    # primary metatile uses 0x26/0x36 at all. They are here because the ground
    # truth marks them, not because the art derives them. Note 0x24B carries the
    # same top-layer art as 0x273 and is walkable in all 25 of its uses.
    # The trunk band (rows 5 and 15 of Fortree City) is invisible to
    # TREE_METATILE_IDS for a structural reason: that table closes over
    # gTileset_General art, and Fortree draws its trunk with 0x320/0x330, which
    # are SECONDARY tiles. No primary tile appears in the band at all, so the
    # primary closure can never reach it however it is seeded. 0x22A escaped only
    # because its art is the primary canopy pair 0x4/0x14.
    #
    # 0x244 passes the same test as 0x22A — its art, both layers, is nothing but
    # trunk and canopy. 0x243/0x246/0x247 add one decal tile each, 0x222 or
    # 0x223, and those two tiles are used by exactly these three metatiles and
    # nothing else in the tileset, so they are branch stubs on the trunk rather
    # than building art. Derived for 0x244, argued for the other three.
    "gTileset_Fortree": frozenset({
        0x22A,                       # canopy on both layers
        0x244,                       # trunk over canopy, no other art
        0x243, 0x246, 0x247,         # same, plus a branch-stub decal
        0x253, 0x273,                # marked by hand, not derived — see above
    }),
}

# Crowns — leaf-mass, tree or roof. Rendered ♣, kept as its own category.
#
# Fortree draws one piece of art for the leafy mass at the top of a tree and for
# the thatched roof of a tree house, so the two are the same tiles and the map
# data cannot tell them apart. They read ♣ because that is what they look like,
# but they are NOT folded into TREE_METATILE_IDS / SECONDARY_TREE_METATILE_IDS:
# those tables assert "this is a tree", which is exactly the claim that cannot be
# made here. Keeping the table separate keeps the distinction available to
# anything that later needs it, at no cost to the render.
#
# Derived, not hand-listed. The crown block is the contiguous sheet region
# 0x230-0x232 / 0x240-0x243 / 0x245-0x246 / 0x250-0x251 / 0x253-0x254, none of
# which is drawn by any primary metatile. A Fortree metatile whose art is only
# crown tiles and tree tiles is a crown, which yields exactly these ten plus
# 0x241 — and 0x241 is the same art walked on, so the impassable-only filter in
# tile_to_char() removes it. 51 cells, all in Fortree City; the other three
# gTileset_Fortree layouts use none of them.
CROWN_METATILE_IDS: dict[str, frozenset] = {
    "gTileset_Fortree": frozenset({
        0x224, 0x225, 0x226,  # crown, full tile
        0x248, 0x249, 0x24A,  # crown, lower half over canopy
        0x250, 0x251, 0x252,  # crown over trunk
        0x25C,                # crown with primary canopy on top
    }),
}

MB_HOT_SPRINGS = 0x28


@functools.lru_cache(maxsize=None)
def _hot_springs_art_twins(secondary: str) -> frozenset:
    """Metatiles whose art is byte-identical to a hot spring's, under another
    behaviour. The one place the renderer draws art instead of behaviour.

    Lavaridge's 0x31D is a clone of the spring tile 0x2A3 set to MB_NORMAL, so
    the bathing NPCs standing on it do not loop the splash field effect. The
    picture is unchanged, so rendering it as floor holes the pool. Identical art
    is the whole claim — anything looser would be a heuristic.
    """
    import struct
    from emerald_data import TILESETS_DIR

    folder = re.sub(r"^gTileset_", "", secondary)
    folder = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", folder).lower()
    d = TILESETS_DIR / "secondary" / folder
    mt, at = d / "metatiles.bin", d / "metatile_attributes.bin"
    if not (mt.exists() and at.exists()):
        return frozenset()
    tiles, attrs = mt.read_bytes(), at.read_bytes()

    def art(i):
        return tuple(struct.unpack_from("<H", tiles, i * 16 + 2 * k)[0]
                     for k in range(8))

    n = min(len(tiles) // 16, len(attrs) // 2)
    behavior = [struct.unpack_from("<H", attrs, i * 2)[0] & 0xFF for i in range(n)]
    springs = {art(i) for i in range(n) if behavior[i] == MB_HOT_SPRINGS}
    if not springs:
        return frozenset()
    return frozenset(0x200 + i for i in range(n)
                     if behavior[i] != MB_HOT_SPRINGS and art(i) in springs)


MB_NORMAL = 0x00


@functools.lru_cache(maxsize=None)
def _bridge_art_twins(secondary: str) -> frozenset:
    """Metatiles whose art is byte-identical to a bridge's, behaviour cleared.

    The second instance of the hot-springs pattern, and read the same way: the
    picture is the bridge, only the behaviour byte was blanked to MB_NORMAL, so
    following the behaviour punches a hole in a bridge the player can see and
    walk on. Route 120's 0x2F5 is 0x297 METATILE_Fortree_WoodBridge1_Top exactly,
    down to all eight tiles; Route 119's 0x2FE / 0x306 are the same for the ends
    of its span. Between them they are 5 cells in the whole game.

    Byte-identical art is the entire claim, and the twin's behaviour must be
    MB_NORMAL — *cleared*, not merely different. Dropping that second condition
    is not a stricter version of the same idea, it is a different rule: it also
    matches Shoal Cave's 68 MB_OCEAN_WATER cells and Route 110's 6
    MB_BIKE_BRIDGE_OVER_BARRIER cells, painting water and bike bridge as "=".

    REFLECTION_UNDER_BRIDGE and BIKE_BRIDGE are excluded from the source set on
    purpose: they are bridges that already draw their own chars (~ and =), so a
    twin of theirs must not be handed the plain bridge "=".
    """
    import struct
    from emerald_data import TILESETS_DIR, _behavior_names

    folder = re.sub(r"^gTileset_", "", secondary)
    folder = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", folder).lower()
    d = TILESETS_DIR / "secondary" / folder
    mt, at = d / "metatiles.bin", d / "metatile_attributes.bin"
    if not (mt.exists() and at.exists()):
        return frozenset()
    tiles, attrs = mt.read_bytes(), at.read_bytes()

    def art(i):
        return tuple(struct.unpack_from("<H", tiles, i * 16 + 2 * k)[0]
                     for k in range(8))

    bridges = {v for v, name in _behavior_names().items()
               if "BRIDGE" in name and "REFLECTION" not in name
               and "BIKE" not in name}
    n = min(len(tiles) // 16, len(attrs) // 2)
    behavior = [struct.unpack_from("<H", attrs, i * 2)[0] & 0xFF for i in range(n)]
    deck = {art(i) for i in range(n) if behavior[i] in bridges}
    if not deck:
        return frozenset()
    return frozenset(0x200 + i for i in range(n)
                     if behavior[i] == MB_NORMAL and art(i) in deck)


def _visible_art(words):
    """The four tiles a metatile actually shows.

    A metatile is two 2x2 layers, words 0-3 bottom and 4-7 top, and tile 0 of
    the top layer is transparent. Comparing the raw eight words therefore calls
    two metatiles different when they draw the identical picture — which is the
    whole of the Route 120 row 17 bug: pond water 0x0A1 is bottom 111E with a
    transparent top, and 0x2F4 is bottom 111E with 111E laid over it. Same
    picture, and the behaviour byte is the only thing that differs.
    """
    return tuple(words[4 + q] if (words[4 + q] & 0x3FF) else words[q]
                 for q in range(4))


@functools.lru_cache(maxsize=None)
def _water_art_bridges(secondary: str) -> dict:
    """Metatiles that claim a bridge behaviour but draw nothing but water.

    `{metatile_id: behaviour name of the water it depicts}`. The inverse of
    _bridge_art_twins() and the other half of the same defect: there the picture
    is a bridge and the behaviour was cleared, here the behaviour says bridge
    and the picture is open water. Rendering "=" puts a plank in the middle of a
    river the player surfs straight through.

    Whole-game reach is ONE cell, Route 120 (17,13) = 0x2F4. It is deliberately
    this narrow. Two things keep it there:

    - the comparison is on _visible_art(), so a metatile drawing any structure
      at all — even one plank — is not water and is not claimed;
    - art that a real bridge metatile also draws is excluded outright.

    Both matter for Route 110, whose four free-floating BRIDGE_OVER_OCEAN cells
    at rows 33-34 look like the same bug and are not: 0x315 draws a wooden
    support pillar with an X cross-brace. Visible structure, correctly left as
    "=". Elevation was the wrong idea here and is recorded as such — it groups
    that pillar with real deck.

    Needs the primary tileset too: the water this matches, 0x0A1, is primary,
    so a secondary-only scan finds no twin and concludes there is no mechanism.
    """
    import struct
    from emerald_data import TILESETS_DIR, _behavior_names

    def read(path):
        mt, at = path / "metatiles.bin", path / "metatile_attributes.bin"
        if not (mt.exists() and at.exists()):
            return [], []
        tiles, attrs = mt.read_bytes(), at.read_bytes()
        n = min(len(tiles) // 16, len(attrs) // 2)
        vis = [_visible_art([struct.unpack_from("<H", tiles, i * 16 + 2 * k)[0]
                             for k in range(8)]) for i in range(n)]
        beh = [struct.unpack_from("<H", attrs, i * 2)[0] & 0xFF for i in range(n)]
        return vis, beh

    folder = re.sub(r"^gTileset_", "", secondary)
    folder = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", folder).lower()
    pv, pb = read(TILESETS_DIR / "primary" / "general")
    sv, sb = read(TILESETS_DIR / "secondary" / folder)
    vis = {i: v for i, v in enumerate(pv)}
    beh = {i: b for i, b in enumerate(pb)}
    vis.update({0x200 + i: v for i, v in enumerate(sv)})
    beh.update({0x200 + i: b for i, b in enumerate(sb)})

    names = _behavior_names()
    water = {v for v, nm in names.items()
             if ("WATER" in nm or "PUDDLE" in nm) and "BRIDGE" not in nm}
    bridge = {v for v, nm in names.items()
              if "BRIDGE" in nm and "REFLECTION" not in nm}

    # min() rather than last-wins: tile 111E is drawn by both MB_POND_WATER and
    # MB_PUDDLE, so the art cannot say which, and dict order must not decide it.
    # Deterministic pick, and the caller labels it "Water" rather than naming a
    # kind the picture does not actually distinguish. Both draw "~" regardless.
    water_art = {}
    for m in vis:
        if beh[m] in water:
            water_art[vis[m]] = min(beh[m], water_art.get(vis[m], 0xFF))
    deck_art = {vis[m] for m in vis if beh[m] in bridge and vis[m] not in water_art}
    return {m: names.get(water_art[vis[m]]) for m in vis
            if beh[m] in bridge and vis[m] in water_art and vis[m] not in deck_art}


MB_BRIDGE_OVER_OCEAN = 0x70

# Tile indices that are exclusively the rope/chain/pylon visual elements of the
# Route 110 cycling bridge supports. No deck, cable-transition, or underside
# metatile in gTileset_Mauville uses any of these tiles; every structural post
# metatile uses only these tiles. The disjoint vocabularies were verified across
# the full secondary tileset — there are no false positives or false negatives.
_MAUVILLE_POST_TILE_VOCAB = frozenset({
    454, 457, 674, 675, 683, 687, 691, 699, 703, 707, 709, 710, 723,
})


@functools.lru_cache(maxsize=None)
def _mauville_bridge_posts(secondary: str) -> frozenset:
    """Cycling bridge support posts in gTileset_Mauville — should render as █.

    All bridge cells (deck AND posts) share MB_BRIDGE_OVER_OCEAN with identical
    attrs. The only distinguishing property is the art: post metatiles draw
    exclusively from a 13-tile vocabulary of rope, chain, and pylon elements that
    no deck, cable-transition, or underside metatile uses. Any bridge metatile
    whose visible tiles are a subset of that vocabulary is a structural post, not
    a walkable surface.
    """
    if "Mauville" not in secondary:
        return frozenset()
    import struct
    from emerald_data import TILESETS_DIR
    folder = re.sub(r"^gTileset_", "", secondary)
    folder = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", folder).lower()
    d = TILESETS_DIR / "secondary" / folder
    mt, at = d / "metatiles.bin", d / "metatile_attributes.bin"
    if not (mt.exists() and at.exists()):
        return frozenset()
    tiles, attrs = mt.read_bytes(), at.read_bytes()
    n = min(len(tiles) // 16, len(attrs) // 2)

    def visible(i):
        words = [struct.unpack_from("<H", tiles, i * 16 + 2 * k)[0] for k in range(8)]
        return frozenset(
            (words[4 + q] if (words[4 + q] & 0x3FF) else words[q]) & 0x3FF
            for q in range(4)
        )

    def beh(i):
        return struct.unpack_from("<H", attrs, i * 2)[0] & 0xFF

    return frozenset(
        0x200 + i for i in range(n)
        if beh(i) == MB_BRIDGE_OVER_OCEAN and visible(i) <= _MAUVILLE_POST_TILE_VOCAB
    )


# Building metatile IDs (Emerald), keyed by the layout's SECONDARY tileset.
#
# Emerald has no notion of a building — a wall is MB_NORMAL with collision, the
# same as a cliff or a fence. As with TREE_METATILE_IDS the signal is the art,
# but building art is not one vocabulary: the shared Pokemon Center / Mart /
# generic-house pieces live in gTileset_General (ids < 0x200) while each town
# supplies its own walls, roofs and windows from its secondary tileset, so the
# table is per tileset and each entry holds both.
#
# Derived by tools/derive_building_metatiles (kept in the session scratchpad):
#   1. SEED from every warp whose destination passes _classify_building — the
#      door tile plus the impassable run above it, five columns wide. That
#      classifier is why its keyword list has to be complete: a building it does
#      not recognise has no door, so its art is never seeded AND its map counts
#      as building-free in step 3.
#   2. CLOSE over tile graphics: take what the seeds draw, minus what any
#      walkable metatile used far from a door draws (ground). The "far from a
#      door" part is load-bearing — a building's own doorway and archway are
#      walkable but ARE building art, and treating them as ground banned the
#      roof above them (Mauville's Gym lost its top and bottom rows that way).
#      Every metatile drawing a surviving graphic is a candidate.
#   3. FILTER by where the candidates are actually used: keep only those whose
#      impassable cells sit within 5 tiles of a building door in at least 85% of
#      cases. A building's wall is always near the door it belongs to; a fence
#      runs the length of the map and a cliff is everywhere. This is what keeps
#      Mauville's post-and-rail fence (0x288/0x28A/0x290-0x292) out — the earlier
#      graphics-only closure pulled it in, because the fence abuts the buildings
#      it fronts. Maps with no building door at all are skipped when counting:
#      the cutscene duplicates (Sootopolis LegendsBattle) are whole cities with
#      every building and no warps, and would brand their own art as terrain.
#
# 1733 ids over 17 tilesets. Fences, cliffs and bushes are excluded; a few cliff
# faces that ONLY ever appear as a building's backdrop still qualify, which is
# arguably right and in any case indistinguishable from the wall they abut.
#: Hand-corrected overrides applied on top of the derived vocabulary above.
#: The derivation scores a metatile by how close its uses sit to a door, and
#: that statistic genuinely works for most art — but it cannot see two cases,
#: and no amount of retuning the radius will make it:
#:
#:   NEVER — a fence sprite laid in a long straight run. Rustboro's west and
#:   south fences score ratio 1.00, identical to real building walls, purely
#:   because the run happens to pass near a door. The same sprite continues as
#:   plain fence for another twenty tiles; there is no point along it where it
#:   becomes a building, so proximity is answering the wrong question.
#:
#:   ALWAYS — a building whose art the graphics closure only partly reached.
#:   Devon Corp's upper storey is the contiguous block 0x288-0x297, used by
#:   that one building on that one layout; the closure caught 6 of the 16 and
#:   left the roof's interior stranded as bare wall.
#:
#: Both are keyed by tileset, like every id table here — 0x2xx ids are
#: secondary-scoped and mean different metatiles in different tilesets.
#: Ground truth: rustboro_terrain_manual.txt.
NEVER_BUILDING_METATILE_IDS: dict[str, frozenset] = {
    # The long fences: west edge, south edge, and the plaza rails.
    "gTileset_Rustboro": frozenset({
        0x220, 0x2C7, 0x2CA, 0x2EC, 0x2FC, 0x318, 0x31B,
    }),
    # The ridge row of the generic house — roof art on the TOP layer, which
    # draws in front of the player, over a middle layer byte-identical to plain
    # grass (0x001). Nothing solid stands at player level, so it is overhang,
    # not body. The map data cannot settle it: Petalburg's two houses use this
    # asset identically, and one encodes the ridge walkable (elev 3 / coll 0)
    # while the other blocks it (elev 0 / coll 1), so the same house renders
    # 3 rows tall in one place and 4 in the other.
    #
    # Scoped here only. 5 of 18 tilesets carry these ids, but of the 6 layouts
    # on gTileset_Petalburg only Petalburg City has an affected cell — 9 of
    # them, and with this the render matches petalburg_city_manual exactly.
    "gTileset_Petalburg": frozenset({
        0x008, 0x009, 0x00A,
    }),
    # Fallarbor's three roof ridges. Same shape as the Petalburg entry above,
    # with bare cliff behind instead of grass: all twelve have a middle layer
    # byte-identical to 0x269, the plain rock metatile that fills the hillside,
    # and carry the roof entirely on the TOP layer, which draws in front of the
    # player. So the tile is the cliff, with a roof overhanging it.
    #
    # The map data cannot settle it — rows 2 through 7 are uniformly elevation 0
    # / collision 1, ridge and wall alike. Nor can the layer test be made into a
    # rule: Fallarbor is built INTO the cliff, so the whole middle house
    # (0x352-0x356, 0x35A-0x35E, 0x362-0x366, 0x36A-0x36E) has bare rock behind
    # it too, and a general "middle layer is bare wall" rule deletes 35 cells
    # here and 845 across 36 layouts.
    #
    # These 12 ids are used 13 times in the whole decomp, every one in Fallarbor
    # Town; the other four gTileset_Fallarbor layouts use none. Renders equal to
    # layout_fallarbor_town_manual.txt.
    "gTileset_Fallarbor": frozenset({
        0x2A0, 0x2A1, 0x2A2, 0x2A3,        # Pokecenter roof top strip
        0x322, 0x323, 0x324,               # west house ridge
        0x34A, 0x34B, 0x34C, 0x34D, 0x34E,  # middle house ridge
    }),
    # Slateport, two unrelated things that fail the same way.
    #
    # The rail fence (0x140/0x141/0x148/0x149/0x14A) is the Rustboro case in the
    # header comment, verbatim: a fence in a long straight run whose near-door
    # ratio is perfect because the run passes the Oceanic Museum and the pier.
    # These are PRIMARY ids used across 8 tilesets, and only 2 of the 18 building
    # vocabularies contain them — the derivation is already inconsistent about
    # them, and the ones that abut no door were never seeded.
    #
    # The quay wall (0x308-0x30C) is the Harbor's stone dock retaining wall, the
    # row directly above the building. No statistic can reach it: 6 uses in the
    # whole decomp, all in that one row, and the only neighbour that could vote
    # is the roof whose membership is the question. Same shape as the Mauville
    # entry below.
    "gTileset_Slateport": frozenset({
        0x140, 0x141, 0x148, 0x149, 0x14A,  # rail fence
        0x308, 0x309, 0x30A, 0x30B, 0x30C,  # harbor quay wall
    }),
    # The cliff face behind Verdanturf's Pokemon Center and Mart. The buildings
    # are rows 1-3; row 0 is bare rock, and the door-seeded flood grew up into it
    # because cliff and roof are both impassable MB_NORMAL.
    #
    # Nothing in the map data separates them: 8 ids, one use each, all in this
    # row, all in Verdanturf. Behaviour is identical, layer_type does not split
    # them (the cliff row is type 1 and so is the door row), and the only
    # adjacency available is the roof itself.
    #
    # NOTE: layout_verdanturf_town_manual.txt marks this row as building, so it
    # now differs from the render by these 8 cells. The markup is the older
    # judgement — the tiles are the mountain the town is built against, drawn as
    # rock, and the PNG of rows 0-4 shows the roofs starting one row lower.
    "gTileset_Mauville": frozenset({
        0x23B, 0x23C, 0x23D, 0x23E, 0x23F, 0x245, 0x260,
        # The wall strip between the cycling road buildings and the bridge approach
        # (north: rows 9-13 cols 17-20, south: rows 87-91 cols 21-22). The
        # component mechanism pulls these into the warp-adjacent building body, but
        # the player never approaches from the bridge side. 0x2A7 and 0x27F appear
        # in Mauville City and Route 111 as passable floor, so NEVER_BUILDING has
        # no effect there. 0x369 and 0x37B are Route 110 exclusive. All approved
        # 2026-08-11.
        0x360, 0x368, 0x370, 0x371,
        0x2A7, 0x27F, 0x369, 0x37B,
    }),
    # Ever Grande's Pokemon League building has a two-tile-wide entrance corridor
    # (rows 6-9, cols 14-15 left wall and 21-22 right wall). 0x220 is the gate
    # column tile and 0x23F is the inner corridor wall — both appear adjacent to the
    # building body and were pulled in by the component mechanism, but they are the
    # corridor walls, not the building face. Only this one layout uses gTileset_EverGrande.
    "gTileset_EverGrande": frozenset({
        0x220, 0x23F,
    }),
    # Sootopolis is built inside an ancient volcanic caldera; the buildings sit on
    # stepped ledges of caldera rock that ring the crater lake. The door-proximity
    # flood spreads from building warps into the adjacent rock cliff tiles, which
    # are MB_NORMAL impassable and therefore indistinguishable from building walls
    # by behaviour alone.
    #
    # These 12 ids are the caldera rock tiles at the cliff faces and the stepped
    # shelves (cols 6-11 left wall, cols 41-46 right wall, row-28 horizontal shelf).
    # None of them appear at a correct building position in either gTileset_Sootopolis
    # layout. Verified against layout_sootopolis_city_fixed.txt.
    "gTileset_Sootopolis": frozenset({
        0x254, 0x255, 0x259, 0x25A, 0x25C, 0x25D,
        0x25E, 0x25F, 0x26B, 0x26C, 0x26D, 0x27B,
        0x27C, 0x27D, 0x27E,
    }),
}

# Metatiles that are a building corner in one place and bridge furniture in
# another, mapped to the direction the platform continues in.
#
# Fortree's tree houses stand on walkable platforms, and one corner piece draws
# both the platform's edge and the side rail of a rope-bridge span. The id alone
# cannot separate them and neither can the art, but the tile it abuts can: next
# to more platform it is at ground level, next to a span it flanks the elevation-4
# approach. This is map data, not geometry — 18/18 against
# layout_fortree_city_manual.txt, and it flips 6 cells in the whole decomp, all
# of them in Fortree City.
PLATFORM_CORNER_METATILE_IDS: dict[str, dict[int, int]] = {
    "gTileset_Fortree": {
        0x233: +1,  # left corner — platform continues east
        0x237: -1,  # right corner — platform continues west
    },
}

# The elevation Emerald gives a raised bridge span.
BRIDGE_SPAN_ELEVATION = 4


def _terrain_feature_rim(grid: list, rows: int, cols: int) -> set:
    """Impassable cells bounding a hot spring — the feature's edge, not a wall.

    A hot spring is stamped with its own rock rim, and the rim is impassable
    MB_NORMAL, indistinguishable from building art by id or by graphics. The
    door-proximity derivation behind BUILDING_METATILE_IDS therefore swallows it
    whole wherever a building stands nearby, and a warp then paints whichever
    side of the rim happens to touch the structure — in Lavaridge Town the
    Pokemon Center claimed the spring's right rim while the identical left rim,
    connected to no door, stayed █.

    The behaviour is what settles it: a tile bounding an MB_HOT_SPRINGS region is
    that region's edge. Map data, not geometry. Lavaridge is the only layout in
    the game with a hot spring, so this drops 12 cells there and none anywhere
    else.
    """
    spring = {(r, c) for r in range(rows) for c in range(cols)
              if grid[r][c] is not None
              and grid[r][c].get("behavior_name") == "MB_HOT_SPRINGS"}
    if not spring:
        return set()
    rim = set()
    for r, c in spring:
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                rr, cc = r + dr, c + dc
                if (0 <= rr < rows and 0 <= cc < cols
                        and (rr, cc) not in spring
                        and grid[rr][cc] is not None
                        and not grid[rr][cc].get("passable", True)):
                    rim.add((rr, cc))
    return rim


def _is_bridge_side_rail(grid: list, row: int, col: int, secondary: str) -> bool:
    """True if this platform corner is flanking a bridge span, not a platform."""
    corners = PLATFORM_CORNER_METATILE_IDS.get(secondary)
    if not corners:
        return False
    cell = grid[row][col]
    step = corners.get(cell.get("metatile_id")) if cell else None
    if step is None:
        return False
    flank_col = col + step
    if not 0 <= flank_col < len(grid[row]):
        return False
    flank = grid[row][flank_col]
    return bool(flank) and flank.get("elevation") == BRIDGE_SPAN_ELEVATION

ALWAYS_BUILDING_METATILE_IDS: dict[str, frozenset] = {
    # Devon Corp's upper storey — one 4x4 asset, this layout only.
    "gTileset_Rustboro": frozenset(range(0x288, 0x298)),
    # The left (col 13) and right (col 23) edge columns of the Pokemon League
    # building, rows 1-5. These are the outermost wall tiles on each side and
    # were not seeded by the derivation because no warp door abuts them directly —
    # they connect to the body only through corner tiles already in the vocabulary.
    # IDs: 0x200/0x210/0x218/0x242 on the left edge, 0x229/0x239/0x241/0x243 on right.
    "gTileset_EverGrande": frozenset({
        0x200, 0x210, 0x218, 0x242,  # left edge (col 13, rows 2/4/5/1)
        0x229, 0x239, 0x241, 0x243,  # right edge (col 23, rows 2/4/5/1)
    }),
}

BUILDING_METATILE_IDS: dict[str, frozenset] = {
    "gTileset_BattleFrontierOutsideEast": frozenset({
        0x003, 0x005, 0x006, 0x007, 0x03E, 0x047, 0x04F, 0x050,
        0x051, 0x052, 0x053, 0x058, 0x059, 0x05A, 0x05B, 0x05D,
        0x060, 0x061, 0x062, 0x063, 0x083, 0x084, 0x087, 0x08B,
        0x08C, 0x0A3, 0x0A4, 0x0AB, 0x0AC, 0x142, 0x149, 0x1E6,
        0x203, 0x210, 0x211, 0x212, 0x213, 0x214, 0x215, 0x216,
        0x217, 0x218, 0x219, 0x21A, 0x21B, 0x21C, 0x21D, 0x21E,
        0x21F, 0x220, 0x221, 0x222, 0x223, 0x224, 0x225, 0x226,
        0x227, 0x228, 0x229, 0x22A, 0x22B, 0x22C, 0x22D, 0x22E,
        0x22F, 0x234, 0x235, 0x236, 0x23C, 0x23E, 0x240, 0x241,
        0x242, 0x248, 0x249, 0x24A, 0x24B, 0x24D, 0x24E, 0x24F,
        0x250, 0x251, 0x252, 0x253, 0x254, 0x255, 0x256, 0x257,
        0x258, 0x259, 0x25A, 0x25B, 0x25C, 0x25D, 0x25E, 0x25F,
        0x26D, 0x26E, 0x26F, 0x276, 0x27D, 0x27E, 0x281, 0x282,
        0x285, 0x289, 0x28A, 0x28C, 0x28E, 0x291, 0x295, 0x297,
        0x299, 0x29A, 0x29B, 0x29C, 0x29D, 0x2A0, 0x2A1, 0x2A2,
        0x2A3, 0x2A4, 0x2A9, 0x2AA, 0x2AB, 0x2AC, 0x2B0, 0x2B1,
        0x2B2, 0x2B3, 0x2B4, 0x2BB, 0x2BD, 0x2BF, 0x2C3, 0x2C4,
        0x2C5, 0x2C6, 0x2C7, 0x2C8, 0x2C9, 0x2CB, 0x2CC, 0x2CD,
        0x2D0, 0x2D2, 0x2D3, 0x2D4, 0x2D5, 0x2D6, 0x2D8, 0x2D9,
        0x2DA, 0x2DB, 0x2DC, 0x2DD, 0x2DE, 0x2E1, 0x2E2, 0x2E3,
        0x2E4, 0x2E5, 0x2E6, 0x2EB, 0x2EC, 0x2EE, 0x2F7, 0x2FF,
        0x300, 0x301, 0x302, 0x303, 0x304, 0x305, 0x306, 0x307,
        0x308, 0x30A, 0x30B, 0x30C, 0x30D, 0x30E, 0x310, 0x311,
        0x312, 0x313, 0x315, 0x316, 0x321, 0x322, 0x323, 0x325,
        0x326, 0x327, 0x329, 0x32A, 0x32B, 0x32C, 0x32D, 0x32E,
        0x32F, 0x330, 0x331, 0x332, 0x333, 0x334, 0x336, 0x337,
        0x338, 0x33C, 0x33D, 0x33E, 0x33F, 0x341, 0x342, 0x348,
        0x34A, 0x34B, 0x34C, 0x34D, 0x34E, 0x34F, 0x350, 0x352,
        0x353, 0x355, 0x356, 0x357, 0x365, 0x366, 0x367, 0x36A,
        0x36D, 0x36E, 0x36F, 0x370, 0x371, 0x372, 0x375, 0x376,
        0x377, 0x378, 0x37A, 0x37B, 0x380, 0x381, 0x382, 0x383,
        0x384, 0x385, 0x386, 0x387, 0x389, 0x38A, 0x38C, 0x38D,
        0x38E, 0x38F, 0x392, 0x394, 0x395, 0x396, 0x397, 0x3A0,
        0x3A1, 0x3AB, 0x3AC, 0x3B3, 0x3C0, 0x3CA, 0x3CB, 0x3CC,
        0x3D2, 0x3EC, 0x3F4, 0x3F5, 0x3F6, 0x3FC,
    }),
    "gTileset_BattleFrontierOutsideWest": frozenset({
        0x003, 0x005, 0x006, 0x007, 0x030, 0x031, 0x032, 0x033,
        0x038, 0x039, 0x03A, 0x03B, 0x041, 0x042, 0x043, 0x047,
        0x04D, 0x04E, 0x057, 0x060, 0x067, 0x0E0, 0x21D, 0x22F,
        0x240, 0x241, 0x242, 0x243, 0x244, 0x245, 0x246, 0x248,
        0x249, 0x24A, 0x24B, 0x24C, 0x24D, 0x24E, 0x251, 0x252,
        0x254, 0x255, 0x256, 0x259, 0x25A, 0x25B, 0x261, 0x263,
        0x265, 0x26F, 0x282, 0x28A, 0x293, 0x294, 0x29B, 0x29C,
        0x29D, 0x2A3, 0x2A4, 0x2A5, 0x2BA, 0x2BB, 0x2BC, 0x2BD,
        0x2BE, 0x2C0, 0x2C1, 0x2C2, 0x2C3, 0x2C4, 0x2C5, 0x2C6,
        0x2C7, 0x2C8, 0x2C9, 0x2CA, 0x2CB, 0x2CC, 0x2CD, 0x2CE,
        0x2CF, 0x2D0, 0x2D1, 0x2D2, 0x2D3, 0x2D5, 0x2D6, 0x2D7,
        0x2DB, 0x2DD, 0x2E4, 0x2EC, 0x2F6, 0x2F7, 0x2FE, 0x2FF,
        0x318, 0x320, 0x324, 0x325, 0x326, 0x328, 0x329, 0x32A,
        0x32B, 0x32C, 0x32D, 0x32E, 0x32F, 0x330, 0x331, 0x332,
        0x333, 0x334, 0x335, 0x336, 0x337, 0x338, 0x339, 0x33A,
        0x33B, 0x33C, 0x33D, 0x33E, 0x33F, 0x340, 0x341, 0x342,
        0x343, 0x344, 0x345, 0x346, 0x347, 0x348, 0x349, 0x34A,
        0x34B, 0x34C, 0x34E, 0x34F, 0x354, 0x356, 0x360, 0x361,
        0x362, 0x363, 0x364, 0x365, 0x366, 0x368, 0x369, 0x36A,
        0x36B, 0x36C, 0x36D, 0x36E, 0x36F, 0x377, 0x382, 0x383,
        0x384, 0x385, 0x386, 0x387, 0x38A, 0x38C, 0x38D, 0x38E,
        0x38F, 0x392, 0x394, 0x395, 0x396, 0x397, 0x398, 0x399,
        0x39A, 0x39B, 0x39C, 0x39D, 0x3A0, 0x3A1, 0x3A2, 0x3A3,
        0x3A4, 0x3A5, 0x3A6, 0x3B9, 0x3BA, 0x3BB, 0x3C0, 0x3C1,
        0x3C2, 0x3C3, 0x3C4, 0x3C8, 0x3C9, 0x3CA, 0x3CB, 0x3CC,
        0x3D0, 0x3D1, 0x3D2, 0x3D3, 0x3D4, 0x3D8, 0x3D9, 0x3DA,
        0x3DB, 0x3DC, 0x3E0, 0x3E1, 0x3E2, 0x3E3, 0x3E4, 0x3E9,
        0x3EB, 0x3F4, 0x3FC,
    }),
    "gTileset_BattlePalace": frozenset({
        0x209, 0x20A, 0x20B, 0x20C, 0x20D, 0x20E, 0x210, 0x212,
        0x218, 0x21A, 0x21F, 0x227,
    }),
    "gTileset_Cave": frozenset({
    }),
    "gTileset_Dewford": frozenset({
        0x003, 0x050, 0x051, 0x052, 0x053, 0x058, 0x059, 0x05A,
        0x05B, 0x061, 0x062, 0x079, 0x091, 0x17A, 0x182, 0x1B3,
        0x1C5, 0x1CD, 0x209, 0x20A, 0x20E, 0x20F, 0x211, 0x212,
        0x213, 0x214, 0x215, 0x216, 0x217, 0x218, 0x21D, 0x21E,
        0x21F, 0x222, 0x223, 0x224, 0x225, 0x226, 0x227, 0x22B,
        0x22C, 0x22D, 0x22E, 0x22F, 0x233, 0x234, 0x235, 0x236,
        0x237, 0x23D, 0x23E, 0x334, 0x335, 0x33C, 0x33D, 0x352,
        0x356, 0x359, 0x35A, 0x35B, 0x35C, 0x35D, 0x35E, 0x35F,
        0x361, 0x362, 0x363, 0x364, 0x365, 0x366, 0x367, 0x369,
        0x36A, 0x36B, 0x36C, 0x36D, 0x36E, 0x36F, 0x371, 0x372,
        0x373, 0x374, 0x375, 0x376, 0x377,
    }),
    "gTileset_EverGrande": frozenset({
        0x050, 0x051, 0x052, 0x053, 0x058, 0x059, 0x05A, 0x05B,
        0x060, 0x061, 0x062, 0x063, 0x201, 0x202, 0x208, 0x209,
        0x20A, 0x20B, 0x20C, 0x20D, 0x20E, 0x20F, 0x211, 0x212,
        0x213, 0x214, 0x215, 0x216, 0x217, 0x219, 0x21A, 0x21B,
        0x21C, 0x21D, 0x21E, 0x21F, 0x220, 0x228, 0x230, 0x231,
        0x238, 0x23F, 0x240, 0x26A,
    }),
    "gTileset_Facility": frozenset({
        0x20E, 0x20F, 0x230, 0x231, 0x232, 0x233, 0x234, 0x23B,
        0x23C, 0x243, 0x244, 0x247, 0x24C, 0x255, 0x25B, 0x25C,
        0x263, 0x264, 0x2B3, 0x2B4, 0x2B5, 0x2BB, 0x2BD, 0x2C3,
        0x2C4, 0x2C5, 0x2CB, 0x2CD, 0x388, 0x389, 0x38A, 0x38B,
        0x38C, 0x390, 0x391, 0x392, 0x394, 0x395, 0x397, 0x39A,
        0x39B, 0x39D, 0x3A0, 0x3A1, 0x3AB, 0x3AC,
    }),
    "gTileset_Fallarbor": frozenset({
        0x00B, 0x010, 0x011, 0x012, 0x013, 0x018, 0x019, 0x01A,
        0x020, 0x021, 0x022, 0x030, 0x031, 0x032, 0x033, 0x038,
        0x039, 0x03A, 0x03B, 0x041, 0x042, 0x050, 0x051, 0x052,
        0x053, 0x058, 0x059, 0x05A, 0x05B, 0x061, 0x062, 0x10C,
        0x225, 0x228, 0x285, 0x286, 0x287, 0x28D, 0x28F, 0x295,
        0x297, 0x29D, 0x29E, 0x2A0, 0x2A1, 0x2A2, 0x2A3, 0x2A5,
        0x2A6, 0x2A9, 0x2AA, 0x2AB, 0x2E6, 0x2E7, 0x2EE, 0x2EF,
        0x2F6, 0x2F7, 0x2FE, 0x306, 0x307, 0x30E, 0x30F, 0x31D,
        0x322, 0x323, 0x324, 0x32C, 0x33A, 0x344, 0x345, 0x346,
        0x34A, 0x34B, 0x34C, 0x34D, 0x34E, 0x352, 0x353, 0x354,
        0x355, 0x356, 0x35A, 0x35B, 0x35C, 0x35D, 0x35E, 0x362,
        0x363, 0x364, 0x365, 0x366, 0x36A, 0x36B, 0x36C, 0x36D,
        0x36E,
    }),
    "gTileset_Fortree": frozenset({
        0x008, 0x009, 0x00A, 0x00B, 0x010, 0x011, 0x012, 0x013,
        0x018, 0x019, 0x01A, 0x020, 0x021, 0x022, 0x030, 0x031,
        0x032, 0x033, 0x038, 0x039, 0x03A, 0x03B, 0x041, 0x042,
        0x043, 0x048, 0x049, 0x04A, 0x04B, 0x050, 0x051, 0x052,
        0x053, 0x058, 0x059, 0x05A, 0x05B, 0x060, 0x061, 0x062,
        0x063, 0x0A0, 0x0A8, 0x0AA, 0x1B2, 0x1B3, 0x1B4, 0x1B8,
        0x1B9, 0x1BA, 0x1BB, 0x1BC, 0x1C0, 0x1C1, 0x1C2, 0x1C3,
        0x1C4, 0x1C5, 0x1CA, 0x1CB, 0x1CD, 0x224, 0x225, 0x226,
        0x22A, 0x22B, 0x22C, 0x22D, 0x22E, 0x22F, 0x233, 0x234,
        0x236, 0x237, 0x243, 0x246, 0x247, 0x248, 0x249, 0x24A,
        0x250, 0x251, 0x252, 0x253, 0x25C, 0x263, 0x264, 0x265,
        0x273, 0x2A0, 0x2A1, 0x2A2, 0x2A5, 0x2A7, 0x2A8, 0x2AB,
        0x2AC, 0x2B0, 0x2B1, 0x2B2, 0x2B3, 0x2B8, 0x2B9, 0x2BA,
        0x2C0, 0x2C1, 0x2C2, 0x2C8, 0x2C9, 0x2DC, 0x2DE, 0x2E3,
        0x2EB,
    }),
    "gTileset_Lavaridge": frozenset({
        0x00B, 0x010, 0x011, 0x012, 0x013, 0x018, 0x019, 0x01A,
        0x020, 0x021, 0x022, 0x030, 0x031, 0x032, 0x033, 0x038,
        0x039, 0x03A, 0x03B, 0x041, 0x042, 0x043, 0x050, 0x051,
        0x052, 0x053, 0x058, 0x059, 0x05A, 0x05B, 0x060, 0x061,
        0x062, 0x063, 0x1B2, 0x1B3, 0x1B4, 0x1B8, 0x1B9, 0x1BA,
        0x1BB, 0x1BC, 0x1C0, 0x1C1, 0x1C2, 0x1C3, 0x1C5, 0x1CA,
        0x1CB, 0x1CD, 0x221, 0x225, 0x228, 0x229, 0x22A, 0x22B,
        0x22D, 0x22E, 0x22F, 0x230, 0x231, 0x232, 0x233, 0x234,
        0x236, 0x237, 0x238, 0x239, 0x23A, 0x23B, 0x23C, 0x23D,
        0x23E, 0x23F, 0x240, 0x243, 0x244, 0x246, 0x247, 0x248,
        0x249, 0x24A, 0x24B, 0x24C, 0x24D, 0x24E, 0x24F, 0x257,
        0x28F, 0x293, 0x299, 0x29B, 0x29D, 0x29F, 0x2A1, 0x2A5,
        0x2A7, 0x2A9, 0x2AD, 0x2B1, 0x2B3, 0x2B5, 0x314, 0x315,
    }),
    "gTileset_Lilycove": frozenset({
        0x008, 0x009, 0x00A, 0x00B, 0x010, 0x011, 0x012, 0x013,
        0x018, 0x019, 0x01A, 0x020, 0x021, 0x022, 0x050, 0x051,
        0x052, 0x053, 0x058, 0x059, 0x05A, 0x05B, 0x060, 0x061,
        0x062, 0x063, 0x0E2, 0x132, 0x1D3, 0x1DB, 0x1E8, 0x200,
        0x201, 0x202, 0x203, 0x204, 0x205, 0x206, 0x207, 0x208,
        0x209, 0x20C, 0x20D, 0x20E, 0x20F, 0x215, 0x21D, 0x21E,
        0x21F, 0x225, 0x226, 0x235, 0x236, 0x237, 0x23D, 0x23E,
        0x23F, 0x243, 0x244, 0x245, 0x246, 0x247, 0x24A, 0x24B,
        0x24C, 0x24D, 0x253, 0x254, 0x255, 0x25B, 0x25C, 0x25D,
        0x25E, 0x25F, 0x263, 0x265, 0x266, 0x267, 0x268, 0x269,
        0x26A, 0x26B, 0x26C, 0x26D, 0x26E, 0x270, 0x271, 0x272,
        0x278, 0x279, 0x280, 0x281, 0x286, 0x28E, 0x293, 0x295,
        0x296, 0x297, 0x29B, 0x29C, 0x29D, 0x29E, 0x29F, 0x2A3,
        0x2A5, 0x2A6, 0x2AB, 0x2AD, 0x2AE, 0x2AF, 0x2B5, 0x2B7,
        0x2BF, 0x2C8, 0x2C9, 0x2CA, 0x2D0, 0x2D1, 0x2D2, 0x2D6,
        0x2D7, 0x2D8, 0x2D9, 0x2DA, 0x2DB, 0x2DC, 0x2E0, 0x2E1,
        0x2E2, 0x2E3, 0x2E4, 0x2ED, 0x2EE, 0x2F5, 0x2F6, 0x2F7,
        0x2FB, 0x2FC, 0x2FD, 0x2FE, 0x2FF, 0x300, 0x301, 0x302,
        0x303, 0x304, 0x305, 0x306, 0x307, 0x30B, 0x30C, 0x30D,
        0x30E, 0x30F, 0x31B, 0x31C, 0x324, 0x325, 0x326, 0x329,
        0x32D, 0x32F, 0x330, 0x331, 0x332, 0x333, 0x335, 0x336,
        0x337, 0x338, 0x339, 0x33A, 0x342, 0x343, 0x344, 0x345,
        0x34B, 0x34C, 0x34D, 0x34E, 0x34F, 0x350, 0x351, 0x352,
        0x353, 0x354, 0x356, 0x358, 0x359, 0x35A, 0x35B, 0x35C,
        0x35D, 0x35E,
    }),
    "gTileset_Mauville": frozenset({
        0x001, 0x008, 0x009, 0x00B, 0x010, 0x011, 0x012, 0x013,
        0x018, 0x019, 0x01A, 0x020, 0x021, 0x022, 0x030, 0x031,
        0x032, 0x033, 0x038, 0x039, 0x03A, 0x03B, 0x041, 0x042,
        0x043, 0x048, 0x049, 0x04A, 0x04B, 0x050, 0x051, 0x052,
        0x053, 0x058, 0x059, 0x05A, 0x05B, 0x060, 0x061, 0x062,
        0x063, 0x133, 0x134, 0x139, 0x1B2, 0x1B3, 0x1B4, 0x1B8,
        0x1B9, 0x1BA, 0x1BB, 0x1BC, 0x1C0, 0x1C1, 0x1C2, 0x1C3,
        0x1C4, 0x1C5, 0x1CA, 0x1CB, 0x1CD, 0x1D7, 0x23B, 0x23C,
        # 0x265: MB_OCEAN_WATER behavior, but it is the building foundation at
        # water level on Route 110's cycling road buildings — the same tile art
        # as the adjacent building walls. Appears once in the whole decomp
        # ((85,20) in LAYOUT_ROUTE110), always impassable. The impassable-water
        # rule now resolves it to █, which is correct for is_plain_wall, but
        # it was not in the vocabulary so the component mechanism could not
        # claim it deterministically. Added 2026-08-11.
        0x265,
        0x23D, 0x23E, 0x23F, 0x245, 0x260, 0x262, 0x26F, 0x272,
        0x273, 0x274, 0x275, 0x277, 0x27F, 0x283, 0x284, 0x285,
        0x286, 0x287, 0x289, 0x28B, 0x28C, 0x28D, 0x28E, 0x28F,
        0x292, 0x293, 0x295, 0x296, 0x297, 0x298, 0x299, 0x29A,
        0x29B, 0x29C, 0x29D, 0x29E, 0x29F, 0x2A0, 0x2A1, 0x2A2,
        0x2A3, 0x2A4, 0x2A5, 0x2A6, 0x2A7, 0x2A8, 0x2A9, 0x2AA,
        0x2AB, 0x2AC, 0x2AD, 0x2AF, 0x2B2, 0x2B3, 0x2B7, 0x2C9,
        0x2D1, 0x2D2, 0x2DA, 0x2DB, 0x2E1, 0x2E2, 0x2E3, 0x2E8,
        0x2E9, 0x2EA, 0x2EB, 0x2EC, 0x306, 0x309, 0x323, 0x334,
        0x360, 0x368, 0x369, 0x370, 0x371, 0x374, 0x37B, 0x38B,
        0x38C, 0x390, 0x391, 0x392, 0x393, 0x394, 0x398, 0x399,
        0x3A0, 0x3A1, 0x3AD, 0x3B0, 0x3B2, 0x3B3, 0x3B4, 0x3B5,
        0x3B6, 0x3B7, 0x3BA, 0x3BB, 0x3BC, 0x3BD, 0x3BE, 0x3BF,
        0x3C2, 0x3C3, 0x3C4, 0x3C5, 0x3C6, 0x3C7, 0x3CA, 0x3CB,
        0x3CC, 0x3CD, 0x3CE, 0x3CF, 0x3D0, 0x3D2, 0x3D3, 0x3D4,
        0x3D5, 0x3D6, 0x3D7, 0x3DE, 0x3DF, 0x3E7, 0x3EF, 0x3F8,
        0x3F9, 0x3FA, 0x3FB, 0x3FC, 0x3FD,
    }),
    "gTileset_Mossdeep": frozenset({
        0x003, 0x00B, 0x010, 0x011, 0x012, 0x013, 0x019, 0x021,
        0x030, 0x031, 0x032, 0x033, 0x038, 0x039, 0x03A, 0x03B,
        0x041, 0x042, 0x043, 0x050, 0x051, 0x052, 0x053, 0x058,
        0x059, 0x05A, 0x05B, 0x060, 0x061, 0x062, 0x063, 0x068,
        0x07A, 0x194, 0x1B2, 0x1B3, 0x1B4, 0x1B8, 0x1B9, 0x1BA,
        0x1BB, 0x1BC, 0x1C0, 0x1C1, 0x1C2, 0x1C3, 0x1C4, 0x1C5,
        0x1CA, 0x1CB, 0x1CD, 0x290, 0x291, 0x292, 0x293, 0x295,
        0x296, 0x298, 0x299, 0x29A, 0x29B, 0x29C, 0x29D, 0x29E,
        0x29F, 0x2A0, 0x2A1, 0x2A2, 0x2A3, 0x2A7, 0x2C0, 0x2C7,
        0x2CF, 0x2D7, 0x2DA, 0x2DB, 0x2DC, 0x2DD, 0x2DE, 0x2DF,
        0x2E0, 0x2E1, 0x2E2, 0x2E3, 0x2E4, 0x2E5, 0x2E6, 0x2E7,
        0x2E8, 0x2E9, 0x2EA, 0x2EB, 0x2EC, 0x2ED, 0x2EE, 0x2EF,
        0x355, 0x360, 0x361, 0x362, 0x363, 0x364, 0x365, 0x366,
        0x368, 0x369, 0x36A, 0x36B, 0x36C, 0x36D, 0x36E, 0x3B8,
        0x3B9, 0x3BA, 0x3BB, 0x3BC, 0x3BD, 0x3C0, 0x3C1, 0x3C2,
        0x3C3, 0x3C4, 0x3C5,
    }),
    "gTileset_Pacifidlog": frozenset({
        0x050, 0x051, 0x052, 0x053, 0x058, 0x059, 0x05A, 0x05B,
        0x061, 0x062, 0x06D, 0x079, 0x07B, 0x089, 0x0A9, 0x175,
        0x176, 0x17D, 0x17E, 0x211, 0x212, 0x213, 0x219, 0x21A,
        0x21B, 0x248, 0x24B,
    }),
    "gTileset_Petalburg": frozenset({
        0x003, 0x007, 0x008, 0x009, 0x00A, 0x00B, 0x010, 0x011,
        0x012, 0x013, 0x018, 0x019, 0x01A, 0x020, 0x021, 0x022,
        0x030, 0x031, 0x032, 0x033, 0x038, 0x039, 0x03A, 0x03B,
        0x041, 0x042, 0x043, 0x050, 0x051, 0x052, 0x053, 0x058,
        0x059, 0x05A, 0x05B, 0x060, 0x061, 0x062, 0x063, 0x1AA,
        0x1AB, 0x1AC, 0x1B2, 0x1B3, 0x1B4, 0x1B8, 0x1B9, 0x1BA,
        0x1BB, 0x1BC, 0x1C0, 0x1C1, 0x1C2, 0x1C3, 0x1C4, 0x1C5,
        0x1CA, 0x1CB, 0x1CD, 0x1D6, 0x1D7, 0x1E4, 0x1E6, 0x1E7,
        0x1FC, 0x1FD, 0x20F, 0x210, 0x211, 0x212, 0x214, 0x215,
        0x216, 0x217, 0x218, 0x219, 0x21A, 0x21E, 0x21F, 0x220,
        0x221, 0x222, 0x223, 0x226, 0x227, 0x228, 0x229, 0x22A,
        0x22B, 0x22C, 0x22D, 0x230, 0x231, 0x232, 0x234, 0x235,
        0x238, 0x239, 0x23A, 0x23F, 0x240, 0x241, 0x244, 0x245,
        0x246, 0x248, 0x249, 0x24A, 0x24B, 0x24C, 0x24D, 0x24E,
        0x254, 0x255, 0x256, 0x258, 0x259, 0x264, 0x265, 0x266,
        0x274, 0x275, 0x276, 0x27C, 0x27D, 0x27E, 0x27F, 0x284,
        0x285, 0x286, 0x287, 0x28F,
    }),
    "gTileset_Rustboro": frozenset({
        0x008, 0x009, 0x00A, 0x00B, 0x010, 0x011, 0x012, 0x013,
        0x018, 0x019, 0x01A, 0x020, 0x021, 0x022, 0x030, 0x031,
        0x032, 0x033, 0x038, 0x039, 0x03A, 0x03B, 0x041, 0x042,
        0x050, 0x051, 0x052, 0x053, 0x058, 0x059, 0x05A, 0x05B,
        0x061, 0x062, 0x10C, 0x1B2, 0x1B3, 0x1B4, 0x1B9, 0x1BA,
        0x1BB, 0x1C1, 0x1C2, 0x1C3, 0x1C5, 0x1CD, 0x214, 0x215,
        0x216, 0x217, 0x21A, 0x21C, 0x21D, 0x21E, 0x21F, 0x220,
        0x227, 0x228, 0x229, 0x22A, 0x22B, 0x22C, 0x22D, 0x22E,
        0x22F, 0x230, 0x231, 0x233, 0x236, 0x237, 0x238, 0x239,
        0x23A, 0x23B, 0x23E, 0x240, 0x241, 0x242, 0x243, 0x247,
        0x249, 0x24A, 0x24B, 0x24C, 0x24D, 0x250, 0x251, 0x252,
        0x253, 0x254, 0x255, 0x258, 0x259, 0x25A, 0x25B, 0x25C,
        0x25D, 0x260, 0x261, 0x263, 0x264, 0x265, 0x266, 0x270,
        0x271, 0x272, 0x273, 0x274, 0x278, 0x279, 0x27A, 0x27B,
        0x27C, 0x27E, 0x27F, 0x280, 0x281, 0x283, 0x284, 0x289,
        0x28C, 0x291, 0x294, 0x298, 0x299, 0x29A, 0x29B, 0x29C,
        0x29D, 0x29E, 0x2A0, 0x2A1, 0x2A2, 0x2A3, 0x2A4, 0x2A5,
        0x2A6, 0x2A8, 0x2A9, 0x2AA, 0x2AB, 0x2AC, 0x2AD, 0x2AE,
        0x2B0, 0x2B1, 0x2B2, 0x2B3, 0x2B4, 0x2B6, 0x2BF, 0x2C7,
        0x2C8, 0x2C9, 0x2CA, 0x2D0, 0x2D1, 0x2D2, 0x2D3, 0x2DF,
        0x2EC, 0x2FC, 0x318, 0x31A, 0x31B, 0x31D, 0x33F,
    }),
    "gTileset_Slateport": frozenset({
        0x003, 0x013, 0x014, 0x020, 0x022, 0x030, 0x031, 0x032,
        0x033, 0x038, 0x039, 0x03A, 0x03B, 0x041, 0x042, 0x043,
        0x04E, 0x050, 0x051, 0x052, 0x053, 0x058, 0x059, 0x05A,
        0x05B, 0x060, 0x061, 0x062, 0x063, 0x139, 0x13A, 0x140,
        0x141, 0x148, 0x149, 0x14A, 0x15A, 0x15B, 0x164, 0x16C,
        0x23F, 0x240, 0x241, 0x242, 0x243, 0x248, 0x249, 0x24A,
        0x250, 0x251, 0x252, 0x253, 0x254, 0x255, 0x258, 0x259,
        0x25B, 0x25C, 0x263, 0x264, 0x268, 0x269, 0x295, 0x296,
        0x297, 0x298, 0x299, 0x29A, 0x29B, 0x29D, 0x29E, 0x29F,
        0x2A0, 0x2A1, 0x2A2, 0x2A5, 0x2A6, 0x2A7, 0x2A8, 0x2A9,
        0x2AA, 0x2AB, 0x2AD, 0x2AF, 0x2B0, 0x2B1, 0x2B2, 0x2B5,
        0x2B6, 0x2B7, 0x2BD, 0x2BE, 0x2BF, 0x2C5, 0x2C8, 0x2C9,
        0x2CA, 0x2CB, 0x2CD, 0x2CE, 0x2CF, 0x2D0, 0x2D1, 0x2D2,
        0x2D3, 0x2D4, 0x2D7, 0x2D8, 0x2DA, 0x2DC, 0x2FB, 0x2FE,
        0x300, 0x301, 0x302, 0x303, 0x308, 0x309, 0x30A, 0x30B,
        0x30C, 0x310, 0x311, 0x312, 0x313, 0x314, 0x318, 0x319,
        0x31A, 0x31B, 0x31C, 0x320, 0x321, 0x322, 0x323, 0x324,
        0x328, 0x329, 0x32B, 0x32C, 0x371, 0x372, 0x373, 0x374,
        0x375, 0x379, 0x37A, 0x37B, 0x37C, 0x37D, 0x381, 0x382,
        0x383, 0x384, 0x385, 0x389, 0x38A, 0x38B, 0x38C, 0x38D,
        0x391, 0x392, 0x393, 0x394, 0x395,
    }),
    "gTileset_Sootopolis": frozenset({
        0x030, 0x031, 0x032, 0x033, 0x038, 0x039, 0x03A, 0x03B,
        0x041, 0x042, 0x050, 0x051, 0x052, 0x053, 0x058, 0x059,
        0x05A, 0x05B, 0x061, 0x062, 0x073, 0x074, 0x075, 0x078,
        0x079, 0x07A, 0x07B, 0x07C, 0x07D, 0x089, 0x1B3, 0x1B8,
        0x1B9, 0x1BA, 0x1BB, 0x1BC, 0x1C0, 0x1C1, 0x1C2, 0x1C3,
        0x1C4, 0x1C5, 0x1CA, 0x1CB, 0x1CD, 0x201, 0x202, 0x203,
        0x205, 0x206, 0x214, 0x215, 0x216, 0x217, 0x21C, 0x21D,
        0x21E, 0x21F, 0x223, 0x224, 0x22B, 0x22C, 0x236, 0x23B,
        0x23E, 0x24E, 0x254, 0x255, 0x259, 0x25A, 0x25C, 0x25D,
        0x25E, 0x25F, 0x26B, 0x26C, 0x26D, 0x276, 0x277, 0x27B,
        0x27C, 0x27D, 0x27E, 0x27F, 0x28A, 0x28B, 0x28C, 0x2CD,
        0x2CE, 0x2D6, 0x2DE, 0x2DF,
    }),
}

# Which of those metatiles is a building's LEFT BORDER — the piece that starts a
# new structure. Emerald has no object layer, so two buildings drawn side by side
# form one run of impassable art and a warp in either used to paint both (the
# Mauville house leaking one column into its warpless neighbour). Gen 4 gets
# instance identity from its meshes; here it is recovered from how the maps were
# authored. Buildings are stamped, so a bond INSIDE one is fixed — 0x2A1 follows
# 0x2A4 in every occurrence in the game — while the bond BETWEEN two varies with
# whatever stands alongside. A metatile that is ever the first impassable cell of
# a horizontal run has open ground to its left somewhere, so it opens a building
# rather than continuing one. build_overlay refuses to join a component across
# into one of these. Generated by tools/derive_building_borders.py.
#
# The table is keyed by secondary tileset, but the PRIMARY ids in it (< 0x200)
# must not be read that way. A primary id is literally the same metatile in every
# tileset, so whether it opens a building is a property of the shared asset, not
# of whichever town it was stamped into — and scoping it per tileset makes the
# derivation self-blocking wherever a tileset holds too few samples of the asset.
# Lavaridge is the case that exposed it: the Pokemon Center's left column
# (0x50/0x58/0x60) is a border in six tilesets, but inside gTileset_Lavaridge it
# is first-in-run 0 times out of 1, because the only thing ever standing to its
# left is the very column the missing seam then failed to cut off.
#
# So _left_borders() unions the primary ids across tilesets. Ids >= 0x200 stay
# scoped, as everywhere else. Measured over 441 layouts: 4 layouts move, 8 cells,
# every one ▓ -> █ — the change can only split a component, never invent one.
BUILDING_LEFT_BORDER_IDS: dict[str, frozenset] = {
    "gTileset_BattleFrontierOutsideEast": frozenset({
        0x003, 0x005, 0x006, 0x007, 0x03E, 0x047, 0x058, 0x210,
        0x218, 0x220, 0x228, 0x234, 0x23E, 0x2EE, 0x315, 0x336,
        0x355, 0x36D, 0x385, 0x392, 0x395, 0x3AB,
    }),
    "gTileset_BattleFrontierOutsideWest": frozenset({
        0x030, 0x2DB, 0x354, 0x382, 0x385, 0x395, 0x3A0, 0x3A3,
        0x3C0, 0x3C8, 0x3D0, 0x3D8, 0x3E0, 0x3E9, 0x3EB,
    }),
    "gTileset_BattlePalace": frozenset({
        0x210, 0x212, 0x21A,
    }),
    "gTileset_Dewford": frozenset({
        0x050, 0x058, 0x209, 0x20E, 0x212, 0x213, 0x216, 0x222,
        0x22B, 0x233, 0x23D, 0x356,
    }),
    "gTileset_Facility": frozenset({
        0x20E, 0x230, 0x2BD, 0x2C3, 0x2CB, 0x2CD, 0x38C, 0x394,
        0x397, 0x39A, 0x3A0, 0x3A1,
    }),
    "gTileset_Fallarbor": frozenset({
        0x010, 0x018, 0x030, 0x038, 0x050, 0x058, 0x285, 0x28D,
        0x295, 0x2A9, 0x2EE, 0x2FE, 0x30E, 0x322, 0x35A,
    }),
    "gTileset_Fortree": frozenset({
        0x008, 0x010, 0x018, 0x020, 0x0A0, 0x0A8, 0x1C0, 0x1CA,
        0x224, 0x22B, 0x236, 0x237, 0x246, 0x2C8,
    }),
    "gTileset_Lavaridge": frozenset({
        0x010, 0x018, 0x020, 0x038, 0x1CA, 0x221, 0x228, 0x22E,
        0x230, 0x238, 0x243, 0x257, 0x29D, 0x2A5, 0x2AD,
    }),
    "gTileset_Lilycove": frozenset({
        0x010, 0x018, 0x020, 0x058, 0x200, 0x208, 0x20C, 0x21D,
        0x225, 0x235, 0x23D, 0x243, 0x245, 0x25B, 0x25F, 0x293,
        0x29B, 0x2A3, 0x2C8, 0x306, 0x330, 0x333, 0x338, 0x342,
        0x350, 0x356, 0x358, 0x35B, 0x35D,
    }),
    "gTileset_Mauville": frozenset({
        0x010, 0x018, 0x060, 0x139, 0x1B2, 0x1CA, 0x283, 0x286,
        0x28B, 0x28E, 0x293, 0x296, 0x29B, 0x29E, 0x2A0, 0x2A8,
        0x2D1, 0x2DA, 0x2E8, 0x323, 0x34E, 0x370, 0x390, 0x398,
        0x3A0, 0x3B0, 0x3CF, 0x3FC,
    }),
    "gTileset_Mossdeep": frozenset({
        0x010, 0x030, 0x038, 0x058, 0x060, 0x1B8, 0x1C0, 0x1CA,
        0x290, 0x298, 0x29E, 0x2A0, 0x2DA, 0x2E0, 0x2E2, 0x2E8,
        0x2EA, 0x360, 0x363, 0x368, 0x36B, 0x3B8, 0x3C0,
    }),
    "gTileset_Pacifidlog": frozenset({
        0x050, 0x058, 0x211, 0x219, 0x248,
    }),
    "gTileset_Petalburg": frozenset({
        0x003, 0x020, 0x030, 0x038, 0x050, 0x058, 0x060, 0x1CA,
        0x210, 0x214, 0x218, 0x21E, 0x220, 0x222, 0x226, 0x22A,
        0x244, 0x24C, 0x254, 0x258, 0x266, 0x274, 0x27C, 0x284,
        0x285,
    }),
    "gTileset_Rustboro": frozenset({
        0x030, 0x038, 0x050, 0x058, 0x1B2, 0x214, 0x21C, 0x220,
        0x228, 0x230, 0x238, 0x240, 0x249, 0x250, 0x258, 0x260,
        0x263, 0x266, 0x283, 0x2B4, 0x2B6, 0x2C8, 0x2CA, 0x2D0,
        0x2D2,
    }),
    "gTileset_Slateport": frozenset({
        0x020, 0x030, 0x038, 0x050, 0x058, 0x060, 0x25B, 0x298,
        0x29D, 0x2A5, 0x2A8, 0x2A9, 0x2AD, 0x2B1, 0x2C8, 0x2CD,
        0x2D0, 0x2D7, 0x2D8, 0x2FE, 0x310, 0x318, 0x32B, 0x379,
    }),
    "gTileset_Sootopolis": frozenset({
        0x1B8, 0x1C0, 0x1CA, 0x215, 0x21D, 0x223, 0x22B, 0x25D,
        0x28A,
    }),
}


@functools.lru_cache(maxsize=None)
def _left_borders(secondary: str) -> frozenset:
    """Left-border ids for a layout: this tileset's, plus every primary one."""
    primary = frozenset(
        i for s in BUILDING_LEFT_BORDER_IDS.values() for i in s if i < 0x200)
    return BUILDING_LEFT_BORDER_IDS.get(secondary, frozenset()) | primary


# Foliage that the building derivation wrongly swallowed. Hedges and ornamental
# shrubs are landscaping — they stand against the buildings they decorate, so the
# near-door usage filter cannot reject them (ratio a clean 1.0) and the graphics
# closure walks into them from the wall they abut. Petalburg's hedge 0x24C runs
# between the house and the Gym and painted the two as one structure.
# Colour separates them by a wide margin: plant art is 0.94-1.00 green over the
# composed metatile, walls and roofs 0.00-0.12. Generated by
# tools/derive_plant_metatiles.py; build_overlay drops these from the art mask.
PLANT_METATILE_IDS: dict[str, frozenset] = {
    "gTileset_Fortree": frozenset({
        0x224, 0x225, 0x226, 0x243, 0x247,
    }),
    "gTileset_Petalburg": frozenset({
        0x23F, 0x244, 0x245, 0x246, 0x24C, 0x24D, 0x24E, 0x254, 0x255,
        0x256, 0x264, 0x265, 0x266,
        # The vegetable plots in the walled garden east of the house on rows
        # 22-23. 0x007 is a PRIMARY id, so this entry scopes it to layouts on
        # the Petalburg tileset — the colour cut cannot reach it (0.47 green:
        # rows of plants on dark tilled soil), unlike the hedge around it.
        0x007,
    }),
    "gTileset_Slateport": frozenset({
        # The ten round shrubs flanking the Oceanic Museum and the Harbor —
        # 0.953 green, the derivation's own answer, and it had simply gone
        # missing from this table.
        0x243,
        # 0x240/0x248 are NOT here on purpose, though the colour cut returns
        # them at 0.844 and 0.801. They are the left column of the Name Rater's
        # green striped awning — a roof, not foliage — and the two lowest scores
        # the cut has ever passed. Ground truth: layout_slateport_city_manual.txt
        # marks both as building. Same false positive removed from
        # TREE_BACKED_METATILE_IDS below, which read the stripes as canopy.
    }),
}

# Building art drawn OVER a tree's canopy. Game Freak drew a building's edge
# tiles twice — once on grass, once on the foliage of whatever tree stands
# behind it — and both variants landed in the vocabulary. Petalburg's Mart is
# the clear case: 0x250/0x251 are its top roof edge on grass, 0x258/0x259 the
# same roof overlay on canopy, so the Mart painted a row taller than it is and
# ate the bottom of the tree beside it.
# PLANT_METATILE_IDS cannot see these: it measures colour over the whole
# metatile, and a half-roof/half-canopy cell averages out to nothing. The tile
# is the right unit — an 8x8 tile is uniform enough to classify outright — and a
# tile counts as foliage only if it is (a) drawn by a confirmed foliage
# metatile, (b) never drawn by a walkable metatile, and (c) >=80% green. Each
# test kills a failure the others miss: without (a) the Mauville Mart's flat
# teal wall scores 0.81, without (b) roof corners over grass score 0.61, without
# (c) the rock a trunk stands on brings in brown cliff backdrops. The 0.45 cut
# sits in an empty band — over 1732 vocabulary metatiles nothing scores 0.30-0.40.
# Generated by tools/derive_tree_backed_metatiles.py.
TREE_BACKED_METATILE_IDS: dict[str, frozenset] = {
    "gTileset_Fortree": frozenset({
        0x224, 0x225, 0x226,
    }),
    "gTileset_Petalburg": frozenset({
        0x23F, 0x244, 0x246, 0x24C, 0x24D, 0x24E, 0x254, 0x255,
        0x256, 0x258, 0x259, 0x264, 0x265, 0x266, 0x285,
    }),
    # Empty on purpose. 0x240/0x248 were here and are the Name Rater's green
    # striped awning on plain grass — no canopy anywhere near it. The tile
    # classifier read the stripes as foliage; see PLANT_METATILE_IDS above.
    "gTileset_Slateport": frozenset(),
}



# Stepping-stone / rock-in-water metatile IDs (Emerald, primary gTileset_General).
# Range between METATILE_General_RoughDeepWater (0x14F) and METATILE_General_CalmWater (0x170).
STEPPING_STONE_METATILE_RANGE = range(0x150, 0x170)

# Building type keyword substrings → char (applied via warp overlay for Emerald)
BUILDING_KEYWORDS_EMERALD = [
    ("POKEMON_CENTER",  "P", "Pokemon Center"),
    ("MART",            "$", "PokeMart"),
    ("GYM",             "G", "Gym"),
    ("LAB",             "K", "Lab / Research"),
    ("INSTITUTE",       "K", "Lab / Research"),
    ("CORP",            "K", "Lab / Research"),
    ("BIRCH",           "K", "Lab / Research"),
    ("ELITE",           "F", "Elite Four"),
    ("POKEMON_LEAGUE",  "F", "Elite Four"),
    ("CHAMPION",        "F", "Pokemon League"),
    ("SCHOOL",          "S", "School"),
    # Everything below was missing, so these buildings were invisible to the
    # renderer: no entrance char, no ▓ body, and — because "has a building
    # door" is what marks a layout as having buildings at all — they also
    # polluted the terrain sample the building vocabulary is derived from.
    ("GAME_CORNER",     "X", "Game Corner"),
    ("DEPARTMENT_STORE","M", "Department Store"),
    ("MUSEUM",          "W", "Museum / exhibit"),
    ("SPACE_CENTER",    "W", "Museum / exhibit"),
    ("CONTEST",         "C", "Contest hall"),
    ("BATTLE_TENT",     "Z", "Battle facility"),
    ("BATTLE_FRONTIER", "Z", "Battle facility"),
    ("TRAINER_HILL",    "Z", "Battle facility"),
    ("HARBOR",          "u", "Harbor"),
    ("SHIPYARD",        "u", "Harbor"),
    ("CABLE_CAR_STATION", "⌁", "Cable car station"),
    ("SHOP",            "$", "Shop"),
    ("WORKSHOP",        "$", "Shop"),
    ("DAY_CARE",        "d", "Day Care"),
    ("SAFARI_ZONE_ENTRANCE", "g", "Gate / entrance building"),
    ("CYCLING_ROAD_NORTH_ENTRANCE", "g", "Gate / entrance building"),
    ("CYCLING_ROAD_SOUTH_ENTRANCE", "g", "Gate / entrance building"),
    ("FOSSIL_MANIACS_TUNNEL", "g", "Gate / entrance building"),
    ("TOWN_HALL",       "C", "Contest hall"),
    ("FAN_CLUB",        "C", "Contest hall"),
    ("MOTEL",           "h", "House"),
    ("REST_STOP",       "h", "House"),
    ("HOUSE",           "h", "House"),
    ("HOME",            "h", "House"),
    ("FLAT",            "h", "House"),
    ("COTTAGE",         "h", "House"),
    ("CABIN",           "h", "House"),
]


# ===========================================================================
# Bulk loaders (whole-game, from the decomp)
#
# Only the derivation tools in tools/ still want every layout at once. The
# renderer loads one layout at a time via emerald_data.layout_tile_entry(), so
# it never pays for these.
# ===========================================================================

def load_tiles(game: str) -> list:
    """Every layout with its grid. Emerald only — the Gen 4 games are per-map."""
    if game != "emerald":
        sys.exit(f"load_tiles: {game} reads the decomp per map, not in bulk.")
    from emerald_data import layout_registry, layout_tile_entry
    return [layout_tile_entry(lid) for lid in layout_registry()]


def load_maps_optional(game: str) -> list | None:
    """Every map entry. Emerald only; returns None for the Gen 4 games."""
    if game != "emerald":
        return None
    from emerald_data import layout_registry, map_entries
    return [m for lid in layout_registry() for m in map_entries(lid)]


#: Rooms with no static warp to them, excluded from full sweeps at the user's
#: request: the Contest Halls (the five rank halls and their base — NOT
#: Lilycove's Contest Hall, which is the real building and keeps its links),
#: Inside of Truck, and every battle facility room that the warp data cannot
#: reach. Battle maps that DO have a link are deliberately kept — every lobby,
#: the Frontier grounds, Battle Palace's rooms.
#:
#: Isolation is a graph property and `skip_map` only sees names, so the lists
#: are frozen here rather than recomputed. They were derived from the graph
#: (degree 0) and are asserted against it in the check below; if a map ever
#: gains a warp, that assertion is what will catch it.
_DISCONNECTED_MAPS = frozenset({
    "MAP_ROUTE104_PROTOTYPE", "MAP_ROUTE104_PROTOTYPE_PRETTY_PETAL_FLOWER_SHOP",
    "MAP_BATTLE_COLOSSEUM_2P", "MAP_BATTLE_COLOSSEUM_4P",
    "MAP_BATTLE_FRONTIER_BATTLE_ARENA_BATTLE_ROOM", "MAP_BATTLE_FRONTIER_BATTLE_ARENA_CORRIDOR",
    "MAP_BATTLE_FRONTIER_BATTLE_DOME_BATTLE_ROOM", "MAP_BATTLE_FRONTIER_BATTLE_FACTORY_BATTLE_ROOM",
    "MAP_BATTLE_FRONTIER_BATTLE_FACTORY_PRE_BATTLE_ROOM", "MAP_BATTLE_FRONTIER_BATTLE_PIKE_CORRIDOR",
    "MAP_BATTLE_FRONTIER_BATTLE_PIKE_ROOM_FINAL", "MAP_BATTLE_FRONTIER_BATTLE_PIKE_ROOM_NORMAL",
    "MAP_BATTLE_FRONTIER_BATTLE_PIKE_ROOM_WILD_MONS", "MAP_BATTLE_FRONTIER_BATTLE_PIKE_THREE_PATH_ROOM",
    "MAP_BATTLE_FRONTIER_BATTLE_PYRAMID_FLOOR", "MAP_BATTLE_FRONTIER_BATTLE_PYRAMID_TOP",
    "MAP_BATTLE_FRONTIER_BATTLE_TOWER_CORRIDOR", "MAP_BATTLE_FRONTIER_BATTLE_TOWER_ELEVATOR",
    "MAP_BATTLE_FRONTIER_BATTLE_TOWER_MULTI_BATTLE_ROOM", "MAP_BATTLE_FRONTIER_BATTLE_TOWER_MULTI_CORRIDOR",
    "MAP_BATTLE_FRONTIER_BATTLE_TOWER_MULTI_PARTNER_ROOM", "MAP_BATTLE_PYRAMID_SQUARE01",
    "MAP_BATTLE_PYRAMID_SQUARE02", "MAP_BATTLE_PYRAMID_SQUARE03",
    "MAP_BATTLE_PYRAMID_SQUARE04", "MAP_BATTLE_PYRAMID_SQUARE05",
    "MAP_BATTLE_PYRAMID_SQUARE06", "MAP_BATTLE_PYRAMID_SQUARE07",
    "MAP_BATTLE_PYRAMID_SQUARE08", "MAP_BATTLE_PYRAMID_SQUARE09",
    "MAP_BATTLE_PYRAMID_SQUARE10", "MAP_BATTLE_PYRAMID_SQUARE11",
    "MAP_BATTLE_PYRAMID_SQUARE12", "MAP_BATTLE_PYRAMID_SQUARE13",
    "MAP_BATTLE_PYRAMID_SQUARE14", "MAP_BATTLE_PYRAMID_SQUARE15",
    "MAP_BATTLE_PYRAMID_SQUARE16", "MAP_CONTEST_HALL",
    "MAP_CONTEST_HALL_BEAUTY", "MAP_CONTEST_HALL_COOL",
    "MAP_CONTEST_HALL_CUTE", "MAP_CONTEST_HALL_SMART",
    "MAP_CONTEST_HALL_TOUGH", "MAP_FALLARBOR_TOWN_BATTLE_TENT_BATTLE_ROOM",
    "MAP_FALLARBOR_TOWN_BATTLE_TENT_CORRIDOR", "MAP_INSIDE_OF_TRUCK",
    "MAP_SLATEPORT_CITY_BATTLE_TENT_BATTLE_ROOM", "MAP_SLATEPORT_CITY_BATTLE_TENT_CORRIDOR",
    "MAP_VERDANTURF_TOWN_BATTLE_TENT_BATTLE_ROOM", "MAP_VERDANTURF_TOWN_BATTLE_TENT_CORRIDOR",
})

_DISCONNECTED_LAYOUTS = frozenset({
    "LAYOUT_ROUTE104_PROTOTYPE",
    "LAYOUT_BATTLE_COLOSSEUM_2P", "LAYOUT_BATTLE_COLOSSEUM_4P",
    "LAYOUT_BATTLE_FRONTIER_BATTLE_ARENA_BATTLE_ROOM", "LAYOUT_BATTLE_FRONTIER_BATTLE_ARENA_CORRIDOR",
    "LAYOUT_BATTLE_FRONTIER_BATTLE_DOME_BATTLE_ROOM", "LAYOUT_BATTLE_FRONTIER_BATTLE_FACTORY_BATTLE_ROOM",
    "LAYOUT_BATTLE_FRONTIER_BATTLE_FACTORY_PRE_BATTLE_ROOM", "LAYOUT_BATTLE_FRONTIER_BATTLE_PIKE_CORRIDOR",
    "LAYOUT_BATTLE_FRONTIER_BATTLE_PIKE_ROOM_FINAL", "LAYOUT_BATTLE_FRONTIER_BATTLE_PIKE_ROOM_NORMAL",
    "LAYOUT_BATTLE_FRONTIER_BATTLE_PIKE_ROOM_WILD_MONS", "LAYOUT_BATTLE_FRONTIER_BATTLE_PIKE_THREE_PATH_ROOM",
    "LAYOUT_BATTLE_FRONTIER_BATTLE_PYRAMID_FLOOR", "LAYOUT_BATTLE_FRONTIER_BATTLE_PYRAMID_TOP",
    "LAYOUT_BATTLE_FRONTIER_BATTLE_TOWER_CORRIDOR", "LAYOUT_BATTLE_FRONTIER_BATTLE_TOWER_MULTI_CORRIDOR",
    "LAYOUT_BATTLE_FRONTIER_BATTLE_TOWER_MULTI_PARTNER_ROOM", "LAYOUT_BATTLE_PYRAMID_SQUARE01",
    "LAYOUT_BATTLE_PYRAMID_SQUARE02", "LAYOUT_BATTLE_PYRAMID_SQUARE03",
    "LAYOUT_BATTLE_PYRAMID_SQUARE04", "LAYOUT_BATTLE_PYRAMID_SQUARE05",
    "LAYOUT_BATTLE_PYRAMID_SQUARE06", "LAYOUT_BATTLE_PYRAMID_SQUARE07",
    "LAYOUT_BATTLE_PYRAMID_SQUARE08", "LAYOUT_BATTLE_PYRAMID_SQUARE09",
    "LAYOUT_BATTLE_PYRAMID_SQUARE10", "LAYOUT_BATTLE_PYRAMID_SQUARE11",
    "LAYOUT_BATTLE_PYRAMID_SQUARE12", "LAYOUT_BATTLE_PYRAMID_SQUARE13",
    "LAYOUT_BATTLE_PYRAMID_SQUARE14", "LAYOUT_BATTLE_PYRAMID_SQUARE15",
    "LAYOUT_BATTLE_PYRAMID_SQUARE16", "LAYOUT_BATTLE_TENT_BATTLE_ROOM",
    "LAYOUT_BATTLE_TENT_CORRIDOR", "LAYOUT_CONTEST_HALL",
    "LAYOUT_CONTEST_HALL_BEAUTY", "LAYOUT_CONTEST_HALL_COOL",
    "LAYOUT_CONTEST_HALL_CUTE", "LAYOUT_CONTEST_HALL_SMART",
    "LAYOUT_CONTEST_HALL_TOUGH", "LAYOUT_INSIDE_OF_TRUCK",
    "LAYOUT_VERDANTURF_TOWN_BATTLE_TENT_BATTLE_ROOM",
})


def is_disconnected_room(*names: str | None) -> bool:
    """True for the battle/contest rooms no warp reaches — see `_DISCONNECTED_MAPS`.

    Both the map constants and the layout ids are listed, because the Emerald
    sweep iterates layouts while `map_graph.py` iterates maps. Only layouts used
    *exclusively* by dropped maps are listed, so a layout a kept map still needs
    is never pulled out from under it.
    """
    for n in names:
        if not n:
            continue
        u = n.strip().upper()
        if u in _DISCONNECTED_MAPS or u in _DISCONNECTED_LAYOUTS:
            return True
    return False
