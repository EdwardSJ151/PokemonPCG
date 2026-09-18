import functools
import hashlib
import json
import math
import re
import sys
from collections import deque
from pathlib import Path

BASE_DIR = Path(__file__).parent


def _fmt_movement(raw: str) -> str:
    """'MOVEMENT_TYPE_LOOK_SOUTH' → 'Look South'; NONE/empty → 'Stationary'."""
    s = raw.removeprefix("MOVEMENT_TYPE_").replace("_", " ").title()
    return s if s and s != "None" else "Stationary"


# ---------------------------------------------------------------------------
# PS-canonical move name lookup  (loaded from pokeplatinum/res/moves/*/data.json)
# ---------------------------------------------------------------------------

_PT_MOVES_DIR = BASE_DIR / "pokeplatinum/res/moves"
_MOVE_DISPLAY_CACHE: dict[str, str] = {}
_MOVE_DISPLAY_LOADED = False

# Gen 4 in-game names that differ from PS after CamelCase normalisation
_PS_MOVE_CORRECTIONS: dict[str, str] = {
    "Smelling Salt":  "Smelling Salts",
    "Smoke Screen":   "Smokescreen",
    "Thunder Shock":  "Thundershock",
    "Vice Grip":      "Vise Grip",
    "Hi Jump Kick":   "High Jump Kick",
    "Selfdestruct":   "Self-Destruct",
    "Sand-Attack":    "Sand Attack",
}


def _load_move_display() -> None:
    global _MOVE_DISPLAY_LOADED
    if _MOVE_DISPLAY_LOADED:
        return
    _MOVE_DISPLAY_LOADED = True
    if not _PT_MOVES_DIR.exists():
        return
    for d in _PT_MOVES_DIR.iterdir():
        f = d / "data.json"
        if not f.exists():
            continue
        try:
            name = json.loads(f.read_text(encoding="utf-8")).get("name", "")
            if not name:
                continue
            name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)  # CamelCase → spaces
            name = _PS_MOVE_CORRECTIONS.get(name, name)
            _MOVE_DISPLAY_CACHE[d.name] = name          # key: snake_case dir name
        except Exception:
            pass


def move_display(constant: str) -> str:
    """MOVE_ANCIENT_POWER / ANCIENT_POWER / SMELLING_SALT → PS canonical name."""
    _load_move_display()
    key = constant.removeprefix("MOVE_").lower()
    return _MOVE_DISPLAY_CACHE.get(key) or key.replace("_", " ").title()


from emerald_helpers import *  # noqa: F401,F403
from emerald_helpers import (  # private names skipped by import *
    _hot_springs_art_twins, _bridge_art_twins, _water_art_bridges,
    _mauville_bridge_posts, _terrain_feature_rim, _is_bridge_side_rail,
    _left_borders, _EMERALD_CAVE_SECONDARY_TILESETS,
)
from hg_helpers import *       # noqa: F401,F403
from pt_helpers import *       # noqa: F401,F403




# ---------------------------------------------------------------------------
# Shared building char descriptions (for legend)
# ---------------------------------------------------------------------------

BUILDING_CHAR_DESC = {
    "C": "Contest Hall",
    "⊞": "Town map",
    "G": "Gym",
    "K": "Lab / Research facility",
    "F": "Elite Four / Pokemon League",
    "S": "School",
    "P": "Pokémon Center (entrance)",
    "$": "PokéMart (entrance)",
    "M": "Department Store / specialty shop (entrance)",
    "T": "TV Station (entrance)",
    "Q": "Global Terminal (entrance)",
    "h": "House (entrance)",
    "l": "Lighthouse (entrance)",
    "j": "Library (entrance)",
    "u": "Harbor (entrance)",
    "L": "Ladder",
    "X": "Game Corner (entrance)",
    "V": "Tower / shrine (entrance)",
    "J": "Snowpoint Temple (entrance)",
    "Z": "Battle facility (entrance)",
    "W": "Museum / exhibit (entrance)",
    "U": "Pastoria Gym puzzle ground",
    "V": "Vending machine",
    "p": "PC",
    "v": "Television",
    "n": "Rail",
    "k": "Bumpy slope (bike required)",
    "o": "Whirlpool",
    "q": "Counter / table",
    "y": "Trash can",
    "z": "Magma",
    # Item chars
    "⊙": "Item ball (visible)",
    "*": "Hidden item (press A)",
    # Building body
    "▓": "Building wall / body",
    # Field obstacles
    "♣": "Cut tree",
    "⊗": "Rock Smash rock",
    "▣": "Strength boulder",
    "◻": "Ice block (pushable)",
    "○": "Fall hole (drops to lower floor)",
    "♠": "Apricorn tree",
    "◈": "Ice Rock (Glaceon evolution)",
    "✿": "Moss Rock (Leafeon evolution)",
    "⌁": "Radio Tower / Cable Car Station (entrance)",
    "Y": "Honey Tree (slather with honey to encounter Pokémon)",
}


# ===========================================================================
# Tile character resolution
# ===========================================================================

def _behavior_matches(keyword: str, bname: str) -> bool:
    """
    True if `keyword` names whole underscore-separated segments of `bname`.

    A plain `in` test reads across segment boundaries and silently mismatches:
    MB_INDOOR_ENCOUNTER ends in the letters of COUNTER, so an indoor encounter
    tile rendered as a kitchen counter. Padding both sides with the separator
    makes the boundaries explicit — "_COUNTER_" is not in
    "_MB_INDOOR_ENCOUNTER_", while "_ENCOUNTER_" is.
    """
    return f"_{keyword}_" in f"_{bname}_"


def tile_to_char(cell: dict, game: str, secondary: str = "") -> str:
    """Resolve a grid cell to a single display character."""
    return resolve_cell(cell, game, secondary)[0]


def resolve_cell(cell: dict, game: str, secondary: str = "") -> tuple:
    """Resolve a grid cell to `(char, description)`.

    The description names the ELEMENT, not the char. Two elements may legitimately
    draw the same character — a tree and a crown are both ♣ — and the legend has
    to say which is which, so every return here carries its own wording rather
    than letting the reader look the char up afterwards.

    `secondary` is the layout's secondary tileset name. Everything id-keyed at
    `>= 0x200` needs it: those ids are tileset-scoped, so `0x310` is a Mossdeep
    tree and five other things elsewhere.
    """
    if game == "emerald":
        label = cell.get("metatile_label") or ""
        for substr, ch, desc in METATILE_LABEL_SYMBOLS:
            if substr in label:
                return ch, desc
        if cell.get("metatile_id") in STAIR_METATILE_IDS:
            return "¿", "Stairs / slope"
        if cell.get("metatile_id") in CAVE_STAIR_METATILE_IDS.get(secondary, frozenset()):
            return "¿", "Stairs / slope"
        if "Fortree" in secondary and cell.get("metatile_id") in CYCLING_BRIDGE_METATILE_IDS:
            return "=", "Cycling bridge deck"
        if cell.get("collision", 0) and cell.get("metatile_id", 0) in STEPPING_STONE_METATILE_RANGE:
            return "∘", "Stone in water"
        # Ahead of the behaviour table on purpose — see _hot_springs_art_twins().
        if (cell.get("metatile_id", 0) >= 0x200
                and cell.get("metatile_id") in _hot_springs_art_twins(secondary)):
            return "♨", "Hot springs (NPC tile, behaviour cleared)"
        # Same reason, same mechanism — see _bridge_art_twins(). MB_NORMAL would
        # otherwise fall through to blank and hole the bridge at that cell.
        if (cell.get("metatile_id", 0) >= 0x200
                and cell.get("metatile_id") in _bridge_art_twins(secondary)):
            return "=", "Bridge (behaviour cleared)"
        # Cave bridges whose tileset has no bridge behaviour at all — _bridge_art_twins
        # returns empty because there is no source deck to compare art against.
        if cell.get("metatile_id") in CAVE_BRIDGE_METATILE_IDS.get(secondary, frozenset()):
            return "=", "Bridge deck"
        # And the inverse — bridge behaviour over water art. Must beat the
        # behaviour table, which would answer "=" for a cell drawing open water.
        _wb = _water_art_bridges(secondary).get(cell.get("metatile_id"))
        if _wb:
            for keyword, ch, desc in EMERALD_SYMBOLS:
                if _behavior_matches(keyword.upper(), _wb):
                    # Not desc: the art is shared between pond and puddle, so
                    # naming one would claim more than the picture supports.
                    return ch, "Water (bridge behaviour, water art)"
        # gTileset_Mauville bridge posts: same MB_BRIDGE_OVER_OCEAN as the deck,
        # but their visible art is drawn exclusively from rope/chain/pylon tiles
        # that no walkable metatile uses. Identified by art, not collision —
        # both passable and impassable post cells exist.
        if (cell.get("metatile_id", 0) >= 0x200
                and cell.get("metatile_id") in _mauville_bridge_posts(secondary)):
            return "█", "Bridge support post"
        bname = (cell.get("behavior_name") or "").upper()
        # MB_MOUNTAIN_TOP tags the whole mountainside, walkable path included —
        # its only use in-game is picking the battle backdrop
        # (battle_setup.c:677), so it says nothing about passability. It cannot
        # sit in EMERALD_SYMBOLS: that loop returns on first match, before the
        # passability fallback, so one char would answer for both halves (rule
        # 9). It was "█" and drew Mt Chimney's 427-tile ash path as solid rock.
        if _behavior_matches("MOUNTAIN_TOP", bname):
            if cell.get("passable", True):
                return " ", "Mountain path"
            return "█", "Mountain top"
        for keyword, ch, desc in EMERALD_SYMBOLS:
            if _behavior_matches(keyword.upper(), bname):
                if ch in {"≈", "~", "="} and not cell.get("passable", True):
                    return "█", desc + " (Impassable)"
                return ch, desc
        # Trees come last and only claim tiles that would otherwise render as
        # plain wall █ — never behaviors, stairs or walkable ground. Same rule
        # as the Gen 4 games, and it matters here: 1376 tree metatiles are
        # walkable grass with a canopy overhanging from the tile above.
        if (not cell.get("passable", True)
                and (cell.get("metatile_id") in TREE_METATILE_IDS
                     or cell.get("metatile_id") in SECONDARY_TREE_METATILE_IDS.get(
                         secondary, frozenset()))):
            return "♣", "Tree"
        # Crowns draw ♣ but are kept as their own category, not merged into the
        # tree tables — see CROWN_METATILE_IDS. Same impassable-only filter, and
        # for the same reason: 0x241 is this art walked on.
        if (not cell.get("passable", True)
                and cell.get("metatile_id") in CROWN_METATILE_IDS.get(
                    secondary, frozenset())):
            return "♣", "Crown — treetop or thatched roof"

    elif game == "heartgold":
        # A headbutt tree is still a tree wall (behaviour 6); headbutt.json is
        # the only thing that distinguishes the ~75 usable ones from the rest.
        if cell.get("headbutt_tree"):
            return "H", "Headbutt tree"
        bname = cell.get("behavior_name") or ""
        entry = HEARTGOLD_SYMBOLS.get(bname)
        if entry:
            ch, desc = entry
            # "L" (ladder) shares its behavior code with jump-ramp corner tiles
            # that are impassable; those are structural walls, not ladders.
            if ch in {"≈", "~", "L"} and not cell.get("passable", True):
                return "█", desc + " (Impassable)"
            return ch, desc
        # Bridge decks and stairs carry no behavior of their own — they are
        # plain walkable tiles that the elevation data lifts above whatever
        # they cross. Behavior always wins, same priority as platinum.
        # Stairs first: a ramp onto a bridge satisfies both tests, and the
        # tilted-plate signal is the specific one (exact on Route 27's 30).
        if cell.get("stair") and cell.get("passable", True):
            return "¿", "Stairs / slope"
        if cell.get("bridge_mesh") and cell.get("passable", True):
            return "=", "Bridge deck"
        # A bridge tile the deck test rejected: what is drawn there is the
        # water the span crosses, not the deck.
        wentry = HEARTGOLD_SYMBOLS.get(cell.get("bridge_water") or "")
        if wentry:
            ch, desc = wentry
            if ch in {"≈", "~"} and not cell.get("passable", True):
                return "█", desc + " (Impassable)"
            return ch, desc

    elif game == "platinum":
        bname = (cell.get("behavior_name") or "").upper()
        bchar = None
        bdesc = None
        for keyword, ch, desc in PLATINUM_SYMBOLS:
            if keyword.upper() in bname:
                bchar, bdesc = ch, desc
                break
        # Sea rocks sit at water level; the walkable top layer (bridges,
        # plank paths, any passable tile) takes priority — a rock only
        # renders where it actually blocks movement.
        if bchar:
            if bchar in {"≈", "~"} and not cell.get("passable", True):
                return "█", bdesc + " (Impassable)"
            return bchar, bdesc
        # Bridge decks (tile-center mesh rule) complete spans whose tiles
        # carry no behavior value. Behavior chars always take priority.
        if cell.get("bridge_mesh") and cell.get("passable", True):
            return "=", "Bridge deck"
        # Stairs only where walkable — impassable tilted BDHC plates are
        # cliffsides, not stairs.
        if cell.get("stair") and cell.get("passable", True):
            return "¿", "Stairs / slope"
        # Trees come last: they may only claim tiles that would otherwise
        # render as plain wall █ — never behaviors, stairs, or walkable ground.
        if cell.get("tree") and not cell.get("passable", True):
            return "♣", "Tree"

    if cell.get("passable", True):
        return " ", "Normal / passable"
    return "█", "Impassable / wall"


# Elements that an overlay may never claim, even though they render as █.
#
# Rule 1 protects a tile that "already resolved to a meaningful char", but the
# char is only a proxy — what the rule is really about is the tile carrying an
# element of its own. Deciding to *draw* a mountain top as plain wall is a
# display choice; it does not turn the mountain into blank scenery a building
# may be stamped over. So the guard asks what the tile IS, and these entries
# stay protected for as long as the element exists, whatever char it draws.
OVERLAY_PROTECTED_BEHAVIORS = ("MOUNTAIN_TOP",)
OVERLAY_PROTECTED_LABELS = (
    "RockWall_GrassBase", "RockWall_RockBase", "RockWall_SandBase",
)


def is_plain_wall(cell: dict | None, game: str,
                  secondary: str = "") -> bool:
    """True if an overlay may claim this cell — plain █ and nothing else."""
    if cell is None:
        return False
    if tile_to_char(cell, game, secondary) != "█":
        return False
    if game == "emerald":
        label = cell.get("metatile_label") or ""
        if any(s in label for s in OVERLAY_PROTECTED_LABELS):
            return False
        bname = (cell.get("behavior_name") or "").upper()
        if any(_behavior_matches(k, bname) for k in OVERLAY_PROTECTED_BEHAVIORS):
            return False
    return True


def _char_description(ch: str, game: str) -> str:
    """Best-effort description for a char when the cell behind it is not to hand.

    Lossy by construction: given `█` it cannot know whether that tile was a wall,
    a mountain top or a secret base wall, because the char has already discarded
    the distinction. **The legend does not use this** — it takes the description
    from `resolve_cell()`, which reports the element it actually matched.

    Two callers legitimately hold only a char: the elevation addendum (stack
    labels) and the under-bridge section (an inferred neighbour). Those are why
    the terrain lookups below still exist. It also serves the overlay chars —
    entrances, building bodies, signs, items — which never reach the resolver.

    **The order of these tests is load-bearing.** The generic chars come first
    precisely because they are ambiguous: `█` must answer "Impassable / wall"
    rather than the first flattened element that happens to draw it (several
    cliff bases and MOUNTAIN_TOP all render `█`), and `♣` must answer "Tree"
    before BUILDING_CHAR_DESC offers "Cut tree". Per-game tables come next
    because the same letter differs by game, and BUILDING_CHAR_DESC last as the
    catch-all. Reordering this silently rewrites every label in both sections.
    """
    if ch == "█":
        return "Impassable / wall"
    if ch == " ":
        return "Normal / passable"
    if ch == "¿":
        return "Stairs / slope"
    if ch == "∘":
        return "Stone in water"
    if ch == "♣":
        return "Tree"
    if ch == "§":
        return "Sign"
    if ch == "@":
        return "Static encounter"
    if ch == "V":
        return "Vending machine"
    if ch == "I":
        return "Item ball"
    if game == "platinum":
        from platinum_data import _BUILDING_WARP_TYPES  # type: ignore
        for _, pch, label in _BUILDING_WARP_TYPES:
            if pch == ch:
                return f"{label} (entrance)"
    if game == "heartgold":
        if ch == "H":
            return "Headbutt tree"
        # HG behaviour chars take priority over the building-overlay table:
        # they share letters ('H', 'P', 'M', 'T'...) with different meanings.
        for bname, (tch, desc) in HEARTGOLD_SYMBOLS.items():
            if tch == ch:
                return desc
    if game == "emerald":
        # Emerald's own building chars first: BUILDING_CHAR_DESC is the Gen 4
        # table and reuses letters for other things ('Q' is Platinum's Global
        # Terminal, Emerald's is a shop).
        for _, tch, desc in BUILDING_KEYWORDS_EMERALD:
            if tch == ch:
                return f"{desc} (entrance)"
    if ch in BUILDING_CHAR_DESC:
        return BUILDING_CHAR_DESC[ch]
    if game == "emerald":
        for _, tch, desc in METATILE_LABEL_SYMBOLS:
            if tch == ch:
                return desc
        for _, tch, desc in EMERALD_SYMBOLS:
            if tch == ch:
                return desc
    elif game == "heartgold":
        for bname, (tch, desc) in HEARTGOLD_SYMBOLS.items():
            if tch == ch:
                return desc
    elif game == "platinum":
        for _, tch, desc in PLATINUM_SYMBOLS:
            if tch == ch:
                return desc
    return "Unknown"


# ===========================================================================
# Building overlay via warp cross-reference
# ===========================================================================

def _classify_building(dest: str, game: str) -> tuple[str, str] | None:
    """Return (char, description) for a destination map name, or None."""
    dest_up = dest.upper()
    if game == "emerald":
        # Outdoor destinations that a keyword would otherwise claim: the two
        # Battle Frontier exteriors are maps you walk onto, not buildings.
        if "_OUTSIDE" in dest_up:
            return None
        keywords = BUILDING_KEYWORDS_EMERALD
    elif game == "heartgold":
        keywords = BUILDING_KEYWORDS_HG
    else:
        keywords = BUILDING_KEYWORDS_PLATINUM
    for keyword, ch, desc in keywords:
        if keyword in dest_up:
            return ch, desc
    return None


def _is_building_wall(cell: dict, grid: list, rr: int, cc: int, rows: int) -> bool:
    """
    Return True if this impassable MB_NORMAL cell should count as a building wall.
    Excludes fences and tree-body tiles.
    """
    label = cell.get("metatile_label") or ""
    if "Fence" in label:
        return False
    if rr + 1 < rows and grid[rr + 1]:
        below_row = grid[rr + 1]
        below = below_row[cc] if cc < len(below_row) else None
        if below and "Grass_Tree" in (below.get("metatile_label") or ""):
            return False
    return True


def _flood_fill_building(grid: list, start_row: int, start_col: int,
                          rows: int, cols: int,
                          bbox: tuple | None = None,
                          unrestricted: bool = False,
                          game: str = "emerald",
                          secondary: str = "") -> set:
    """
    BFS from (start_row, start_col) through adjacent impassable MB_NORMAL cells.
    Returns set of (row, col) in the building footprint (excluding the start tile).
    Capped at 100 cells to prevent leaking into cliffs or open terrain.
    """
    MAX_CELLS = 100
    visited: set = set()
    queue: deque = deque()

    r_min, r_max, c_min, c_max = bbox if bbox else (0, rows - 1, 0, cols - 1)

    def _bname(cell: dict) -> str:
        return (cell.get("behavior_name") or "").upper()

    secondary_adj = False
    secret_wall_adj = False
    if not unrestricted:
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = start_row + dr, start_col + dc
            if 0 <= nr < rows and 0 <= nc < cols:
                adj = grid[nr][nc]
                bn = _bname(adj)
                if adj.get("collision", 0) == 0:
                    continue
                if adj.get("metatile_id", 0) >= 0x200 and ("MB_NORMAL" in bn or bn == "MB_0"):
                    secondary_adj = True
                elif "SECRET_BASE_WALL" in bn:
                    secret_wall_adj = True

    def _eligible(nr: int, nc: int) -> bool:
        if not (r_min <= nr <= r_max and c_min <= nc <= c_max):
            return False
        cell = grid[nr][nc]
        if cell.get("collision", 0) == 0:
            return False
        bn = _bname(cell)
        mid = cell.get("metatile_id", 0)
        if unrestricted:
            if not ("MB_NORMAL" in bn or bn == "MB_0" or "SECRET_BASE_WALL" in bn):
                return False
        elif secondary_adj:
            if mid < 0x200 or not ("MB_NORMAL" in bn or bn == "MB_0"):
                return False
        elif secret_wall_adj:
            if "SECRET_BASE_WALL" not in bn:
                return False
        else:
            if not ("MB_NORMAL" in bn or bn == "MB_0" or "SECRET_BASE_WALL" in bn):
                return False
        if not is_plain_wall(cell, game, secondary):
            return False
        return _is_building_wall(cell, grid, nr, nc, rows)

    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        nr, nc = start_row + dr, start_col + dc
        if 0 <= nr < rows and 0 <= nc < cols and _eligible(nr, nc):
            queue.append((nr, nc))
            visited.add((nr, nc))

    while queue and len(visited) < MAX_CELLS:
        r, c = queue.popleft()
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = r + dr, c + dc
            if (nr, nc) in visited or not (0 <= nr < rows and 0 <= nc < cols):
                continue
            if _eligible(nr, nc):
                visited.add((nr, nc))
                queue.append((nr, nc))

    return visited


def _build_interior_dims(maps_data: list, tiles_data: list) -> dict:
    """Build {dest_map_id: (interior_width, interior_height)} from maps + tiles data."""
    name_to_dims: dict = {}
    for e in tiles_data:
        w, h = e.get("width", 0), e.get("height", 0)
        for mn in (e.get("maps") or []):
            name_to_dims[mn] = (w, h)
    result: dict = {}
    for m in maps_data:
        mid = m.get("id") or m.get("constant") or ""
        mn = m.get("map_name", "")
        if mid and mn in name_to_dims:
            result[mid] = name_to_dims[mn]
    return result


#: Chars a stamp hole may be walled in by. `D` counts: a door IS the building's
#: wall, just a walkable one, and the Kanto marts' lower gap opens onto theirs.
_HOLE_WALLS = frozenset("█▓D")

#: Anything bigger than this is a courtyard or a mis-sized footprint, not a
#: stamp defect — the largest real one in HeartGold is 7 tiles (Lavender).
_MAX_HOLE_TILES = 60


def _stamp_holes(grid: list, rows: int, cols: int,
                 structures: list, game: str) -> set:
    """
    Passable tiles that a building model punched into its own interior.

    A tile qualifies only when its blank region is (1) walled in on all eight
    sides, and (2) contained entirely in one structure's footprint. Both parts
    are load-bearing. The footprint alone is too loose — a model's bbox runs
    about a row past the collision it paints (it includes the base and steps),
    so it can reach into whatever sits beside the building; that is how Ruins
    of Alph's `ar_shrine3` swallowed a gap in the rubble field below it. The
    eight-way test is what separates the two cases: a hole punched in a wall is
    sealed in every direction, while a gap in scattered terrain leaks out
    diagonally. Across HeartGold that split is exact — 16 model holes kept,
    the single Ruins tile dropped.
    """
    if not structures:
        return set()

    # Footprints snapped out to whole tiles. The containment test runs on tile
    # indices, and a bbox edge like r=18.69 still belongs to row 18.
    rects = [(max(0, math.floor(ps["c0"])), min(cols - 1, math.ceil(ps["c1"]) - 1),
              max(0, math.floor(ps["r0"])), min(rows - 1, math.ceil(ps["r1"]) - 1))
             for ps in structures]

    def char_at(r, c):
        cell = grid[r][c] if 0 <= r < rows and 0 <= c < cols else None
        return tile_to_char(cell, game) if cell is not None else None

    holes: set = set()
    seen: set = set()
    for c0, c1, r0, r1 in rects:
        for sr in range(r0, r1 + 1):
            for sc in range(c0, c1 + 1):
                if (sr, sc) in seen or char_at(sr, sc) != " ":
                    continue
                # The region may run outside the rect — that is exactly how a
                # gap open to the street is caught, so never clip the walk.
                region, queue, open_edge = set(), [(sr, sc)], False
                seen.add((sr, sc))
                while queue and len(region) <= _MAX_HOLE_TILES:
                    r, c = queue.pop()
                    region.add((r, c))
                    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nr, nc = r + dr, c + dc
                        nch = char_at(nr, nc)
                        if nch == " ":
                            if (nr, nc) not in seen:
                                seen.add((nr, nc))
                                queue.append((nr, nc))
                        elif nch not in _HOLE_WALLS:
                            open_edge = True
                if open_edge or queue:
                    continue
                # Diagonals too — a stamp hole is sealed on all eight sides.
                if any(char_at(r + dr, c + dc) not in _HOLE_WALLS
                       for r, c in region
                       for dr in (-1, 0, 1) for dc in (-1, 0, 1)
                       if (r + dr, c + dc) not in region):
                    continue
                if any(all(rc0 <= c <= rc1 and rr0 <= r <= rr1
                           for r, c in region)
                       for rc0, rc1, rr0, rr1 in rects):
                    holes |= region
    return holes


def build_overlay(tile_entry: dict, maps_data: list | None, game: str,
                  interior_dims: dict | None = None) -> dict:
    """
    Build {(row, col): char} overlay for building footprints and warp positions.
    All three games take their map entries from the decomp when maps_data is
    None; it is only consulted for Emerald when a caller supplies it.
    interior_dims: optional {dest_map_id: (width, height)} to bound BFS.
    """
    grid = tile_entry.get("grid") or []
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    # Every id-keyed table at >= 0x200 is scoped to this; pass it to anything
    # that resolves a cell to a char.
    sec_tileset = tile_entry.get("secondary_tileset") or ""

    matching = find_map_entries(maps_data, tile_entry, game)

    overlay: dict = {}

    # Gen 4 building bodies come from actual 3D prop placements (props section
    # of the land-data block + each model's vertex footprint) — no warp
    # flood-fill. Only structures that contain a warp (enterable buildings) are
    # drawn as ▓; warpless structures (fences, fountains, decorations) keep █.
    # Entrance chars are placed by the event renderer.
    # Interior maps have no outdoor building props; skip entirely.
    _g4_interior_types = {"MAP_TYPE_INTERIOR", "MAP_TYPE_INDOORS"}
    if game in ("platinum", "heartgold") and tile_entry.get("map_type") in _g4_interior_types:
        return overlay
    if game in ("platinum", "heartgold"):
        col_off = tile_entry.get("_tile_col_min", 0)
        row_off = tile_entry.get("_tile_row_min", 0)
        # HG supplies its warps directly, already grid-local: they come from
        # the decomp's zone_event files because maps_pokeheartgold.json's
        # `warps` are misaligned.
        warp_pts = tile_entry.get("warp_tiles")
        if warp_pts is None:
            warp_pts = {
                (w.get("x", 0) - col_off, w.get("z", 0) - row_off)
                for m in matching
                for w in m.get("warp_events", [])
            }
        # Enterable check: a warp within the rect (1-tile slack for doors on
        # the footprint edge). Gate models and cross-block props use 2.5-tile
        # slack: the WARP trigger tile sits up to ~2.24 tiles outside the
        # building's bounding box edge (gate_a min_dr=-4.256, lr=3.0 → gap of
        # 1.256 tiles above r0; kn_gate_a same geometry → warp at r0-2.244).
        _GATE_SLACK = 2.5
        _GATE_NAMES = {
            "kn_gate_a", "kn_gate_b", "kn_gate_l", "kn_gate_r",
            "gate_a", "gate_b", "en_gate01",
        }
        # Prop models that are cave/dungeon entrances, not buildings. They have
        # a warp inside their footprint (so they'd normally enter `gated`), but
        # the warp destination is a cave — the tile renders `c` via the event
        # overlay. Marking the whole footprint ▓ would make it look like a
        # building interior when it's a solid-stone well rim with one hole.
        _CAVE_MOUTH_PROPS = {"hw_ido"}   # hw_ido = Azalea Slowpoke Well
        gated = []
        for ps in tile_entry.get("prop_structures", []):
            if ps["name"] in _CAVE_MOUTH_PROPS:
                continue
            slack = _GATE_SLACK if (ps.get("cross_block") or ps["name"] in _GATE_NAMES) else 1
            if any(ps["c0"] - slack <= wc + 0.5 < ps["c1"] + slack
                   and ps["r0"] - slack <= wr + 0.5 < ps["r1"] + slack
                   for wc, wr in warp_pts):
                gated.append(ps)

        # Some HeartGold building models stamp passable tiles into their own
        # interior — the shared Kanto mart `fs` leaves the same two-tile gap at
        # all eleven of its placements, and `kn_power` / `ya_gym` do the same.
        # Those tiles are byte-identical to open street, so they draw blank and
        # punch holes in the building. Seal them; the ▓ pass below then treats
        # them as wall.
        #
        # HeartGold only, deliberately. Platinum has no such stamp holes, but it
        # does lay buildings out tightly enough that six zones have a sealed
        # one-tile alley BETWEEN two neighbours (Jubilife's col 26, Veilstone,
        # Eterna, Survival Area, Routes 212-S and 225). A footprint bbox snapped
        # out to whole tiles cannot tell that alley from an interior hole, and
        # those gaps are correct as drawn — Platinum is the verified base and
        # its output must not move.
        sealed = (_stamp_holes(grid, rows, cols, gated, game)
                  if game == "heartgold" else set())
        overlay.update(((fr, fc), "█") for fr, fc in sealed)

        for ps in gated:
            c0b, c1b, r0b, r1b = ps["c0"], ps["c1"], ps["r0"], ps["r1"]
            if "c0g" in ps and any(
                ps["c0g"] <= wc + 0.5 < ps["c1g"] and ps["r0g"] <= wr + 0.5 < ps["r1g"]
                for wc, wr in warp_pts
            ):
                c0b, c1b, r0b, r1b = ps["c0g"], ps["c1g"], ps["r0g"], ps["r1g"]
            # Gate models have an approach corridor whose plain-wall tiles fall
            # above the model's r0 (the warp trigger sits 1-2 rows north of the
            # bounding box). Extend painting to warp_row+1 so the corridor is
            # also painted ▓ rather than left ██.
            if ps.get("name") in _GATE_NAMES:
                entry_wr = [wr for wc, wr in warp_pts
                            if c0b - _GATE_SLACK <= wc + 0.5 < c1b + _GATE_SLACK
                            and wr + 0.5 < r0b]
                if entry_wr:
                    r0b = max(entry_wr) + 1
            for fr in range(max(0, int(r0b)), min(rows, int(r1b) + 1)):
                for fc in range(max(0, int(c0b)), min(cols, int(c1b) + 1)):
                    if not (c0b <= fc + 0.5 < c1b and r0b <= fr + 0.5 < r1b):
                        continue
                    if (fc, fr) in warp_pts:
                        continue  # never bury a door tile
                    cell = grid[fr][fc]
                    # Claim only plain-wall tiles: doors, stairs, and other
                    # behavior chars inside the rect keep their symbols.
                    if (fr, fc) in sealed or is_plain_wall(cell, game, sec_tileset):
                        overlay[(fr, fc)] = "▓"
        return overlay

    # Emerald buildings come from the art their metatiles draw, not from a fill
    # out of a warp. BUILDING_METATILE_IDS is the per-tileset vocabulary, so a
    # connected run of those tiles already IS one structure — no flooding
    # through unknown impassables, no hardcoded PC/Mart/Gym rectangles, and
    # nothing to cap at 100 cells. The only thing left to decide is which
    # structures are enterable: ▓ marks a building you can walk into, and a
    # warpless one (scenery, background houses) stays █.
    art_ids = BUILDING_METATILE_IDS.get(tile_entry.get("secondary_tileset") or "")
    if game == "emerald":
        # Hand corrections win over the derived vocabulary, in both directions.
        _sec = tile_entry.get("secondary_tileset") or ""
        _always = ALWAYS_BUILDING_METATILE_IDS.get(_sec, frozenset())
        _never = NEVER_BUILDING_METATILE_IDS.get(_sec, frozenset())
        if _always or _never:
            art_ids = ((art_ids or frozenset()) | _always) - _never
    if game == "emerald" and tile_entry.get("primary_tileset") == "gTileset_Building":
        art_ids = None  # interior layout — no building bodies possible
    if game == "emerald" and art_ids:
        # Foliage sitting in the vocabulary, by two separate mechanisms:
        # landscaping the derivation swallowed whole, and building edge art
        # drawn over a canopy backdrop. Neither is part of a structure.
        secondary = tile_entry.get("secondary_tileset") or ""
        plants = (PLANT_METATILE_IDS.get(secondary, frozenset())
                  | TREE_BACKED_METATILE_IDS.get(secondary, frozenset()))
        # The rim of a terrain feature is the feature, not a structure.
        feature_rim = _terrain_feature_rim(grid, rows, cols)
        # A building may only be drawn over a tile that is *nothing but* flat
        # impassable — a cell that already resolved to a meaningful char is not
        # available to it. Membership in the vocabulary is not enough on its
        # own: the id tables are recovered from art and usage, so a metatile
        # that is genuinely part of a building elsewhere can still land on a
        # mountain top, a bridge deck, a ledge, open water or a tree here, and
        # those tiles are already telling the truth about themselves.
        # Testing the rendered char rather than blacklisting behaviours in the
        # derivation keeps the guard at the layer the rule belongs to, so a
        # regenerated table cannot reintroduce the same class of bug.
        mask = {
            (r, c)
            for r in range(rows) for c in range(cols)
            if grid[r][c] is not None
            and not grid[r][c].get("passable", True)
            and grid[r][c].get("metatile_id") in art_ids
            and grid[r][c].get("metatile_id") not in plants
            and (r, c) not in feature_rim
            and not _is_bridge_side_rail(grid, r, c, secondary)
            and is_plain_wall(grid[r][c], "emerald", secondary)
        }
        # A tree-backed cell is mostly canopy, so it reads as the tree it
        # belongs to rather than as a wall. Dropping it from the mask alone
        # would leave it █, breaking the tree column it continues. Warps are
        # written after this and still win, so a door is never buried.
        # Restricted to ids the plant table does not already hold: those are
        # hedges and shrubs, and whether a hedge should read ♣ is a separate
        # question from whether a canopy should.
        canopy = (TREE_BACKED_METATILE_IDS.get(secondary, frozenset())
                  - PLANT_METATILE_IDS.get(secondary, frozenset()))
        for r in range(rows):
            for c in range(cols):
                cell = grid[r][c]
                if cell is not None and cell.get("metatile_id") in canopy:
                    overlay[(r, c)] = "♣"

        # Two buildings standing side by side share no gap, so plain
        # 4-connectivity fuses them into one structure. A left-border metatile
        # opens a new building, so the bond into it from the left is a seam
        # between instances, not part of either — see BUILDING_LEFT_BORDER_IDS.
        borders = _left_borders(tile_entry.get("secondary_tileset") or "")

        def joined(a, b):
            """Is there a bond between horizontally/vertically adjacent a, b?"""
            (ar, ac), (br, bc) = a, b
            if ar != br:
                return True                      # vertical bonds are never seams
            left = a if ac < bc else b
            right = b if ac < bc else a
            return grid[right[0]][right[1]].get("metatile_id") not in borders

        comp_of: dict = {}
        comps: list = []
        for start in mask:
            if start in comp_of:
                continue
            idx = len(comps)
            cells = set()
            queue = deque([start])
            comp_of[start] = idx
            while queue:
                r, c = queue.popleft()
                cells.add((r, c))
                for nr, nc in ((r-1, c), (r+1, c), (r, c-1), (r, c+1)):
                    if ((nr, nc) in mask and (nr, nc) not in comp_of
                            and joined((r, c), (nr, nc))):
                        comp_of[(nr, nc)] = idx
                        queue.append((nr, nc))
            comps.append(cells)

        for map_entry in matching:
            for w in map_entry.get("warp_events", []):
                col, row = int(w.get("x", 0)), int(w.get("y", 0))
                if not (0 <= row < rows and 0 <= col < cols):
                    continue
                result = _classify_building(w.get("dest_map", ""), game)
                if result is None:
                    cell = grid[row][col]
                    ch = tile_to_char(cell, game, sec_tileset)
                    if is_plain_wall(cell, game, sec_tileset) or ch == " ":
                        if sec_tileset in _EMERALD_CAVE_SECONDARY_TILESETS:
                            overlay.setdefault((row, col), "c")
                        else:
                            overlay.setdefault((row, col), "D")
                    continue
                # A door is walkable, so it is not in the mask itself; the
                # structure is whatever building art it is touching.
                touching = {comp_of[(row + dr, col + dc)]
                            for dr in (-1, 0, 1) for dc in (-1, 0, 1)
                            if (row + dr, col + dc) in comp_of}
                if touching:
                    for idx in touching:
                        for cell_rc in comps[idx]:
                            overlay.setdefault(cell_rc, "▓")
                else:
                    # No art here: the vocabulary is derived from the graphics
                    # around doors, and a few buildings are drawn entirely from
                    # metatiles that never appear next to one (Mauville's Gym).
                    # Rather than lose them, fall back to the old fill for that
                    # door alone — the only place it still runs.
                    for fr, fc in _flood_fill_building(grid, row, col, rows, cols,
                                                       None, unrestricted=False,
                                                       game=game,
                                                       secondary=sec_tileset):
                        overlay.setdefault((fr, fc), "▓")
                overlay[(row, col)] = result[0]
        return overlay

    for map_entry in matching:
        if game == "emerald":
            warps = map_entry.get("warp_events", [])
            warp_items = [(w.get("x", 0), w.get("y", 0),
                           w.get("dest_map", "")) for w in warps]
        elif game == "heartgold":
            warps = map_entry.get("warps", [])
            warp_items = [(w.get("x", 0), w.get("z", 0),
                           w.get("header", "")) for w in warps]
        else:  # platinum
            warps = map_entry.get("warp_events", [])
            warp_items = [(w.get("x", 0), w.get("z", 0),
                           w.get("dest_header_id", "")) for w in warps]

        is_interior = (game == "emerald"
                       and tile_entry.get("primary_tileset") == "gTileset_Building")

        for wx, wy, dest in warp_items:
            col, row = int(wx), int(wy)
            if not (0 <= row < rows and 0 <= col < cols):
                continue
            result = _classify_building(dest, game)
            if result is None:
                # Warp to a non-building destination on the general overlay path.
                cell = grid[row][col]
                ch = tile_to_char(cell, "emerald", sec_tileset)
                if not (is_plain_wall(cell, "emerald", sec_tileset) or ch == " "):
                    continue
                if sec_tileset in _EMERALD_CAVE_SECONDARY_TILESETS:
                    overlay.setdefault((row, col), "c")
                else:
                    overlay.setdefault((row, col), "D")
                continue
            if is_interior:
                # Inside a building: no ▓ flood fill, but mark the warp tile
                # itself with D if the terrain doesn't already carry a char.
                cell = grid[row][col]
                ch = tile_to_char(cell, "emerald", sec_tileset)
                if is_plain_wall(cell, "emerald", sec_tileset) or ch == " ":
                    overlay.setdefault((row, col), "D")
                continue
            bchar, _ = result

            # Cave tilesets cannot have building bodies — the metatile
            # vocabulary is structurally empty and ▓ would land on rock walls.
            if sec_tileset in _EMERALD_CAVE_SECONDARY_TILESETS:
                overlay[(row, col)] = bchar
                continue

            bbox = None
            dest_up = dest.upper()
            is_house = any(k in dest_up for k in ("_HOUSE", "_HOME", "_FLAT",
                                                   "_COTTAGE", "_CABIN",
                                                   "_CONDOMINIUM", "_VILLA"))
            if "POKEMON_CENTER" in dest_up or "POKECENTER" in dest_up:
                for dr in range(-2, 1):
                    for dc in range(-1, 3):
                        fr, fc = row + dr, col + dc
                        if 0 <= fr < rows and 0 <= fc < cols and (fr, fc) not in overlay:
                            overlay[(fr, fc)] = "▓"
                overlay[(row, col)] = bchar
                continue
            elif "POKEMART" in dest_up or ("MART" in dest_up and "DEPARTMENT" not in dest_up and "DEPT" not in dest_up):
                for dr in range(-2, 1):
                    for dc in range(-1, 3):
                        fr, fc = row + dr, col + dc
                        if 0 <= fr < rows and 0 <= fc < cols and (fr, fc) not in overlay:
                            overlay[(fr, fc)] = "▓"
                overlay[(row, col)] = bchar
                continue
            elif "GYM" in dest_up:
                for dr in range(-3, 0):
                    for dc in range(-3, 3):
                        fr, fc = row + dr, col + dc
                        if 0 <= fr < rows and 0 <= fc < cols and (fr, fc) not in overlay:
                            overlay[(fr, fc)] = "▓"
                for dc in range(-1, 2):
                    fr, fc = row, col + dc
                    if 0 <= fr < rows and 0 <= fc < cols and (fr, fc) not in overlay:
                        overlay[(fr, fc)] = "▓"
                overlay[(row, col)] = bchar
                continue
            elif is_house:
                bbox = (max(0, row - 4), row,
                        max(0, col - 4), min(cols - 1, col + 4))
            elif interior_dims and dest in interior_dims:
                iw, ih = interior_dims[dest]
                if iw > 0 and ih > 0:
                    v_slack = min(ih, 12) + 3
                    h_slack = iw // 2 + 2
                    r_min = max(0, row - v_slack)
                    r_max = min(rows - 1, row + 1)
                    c_min = max(0, col - h_slack)
                    c_max = min(cols - 1, col + h_slack)
                    bbox = (r_min, r_max, c_min, c_max)

            footprint = _flood_fill_building(grid, row, col, rows, cols, bbox,
                                             unrestricted=is_house, game=game,
                                             secondary=sec_tileset)
            for fr, fc in footprint:
                if (fr, fc) not in overlay:
                    overlay[(fr, fc)] = "▓"
            overlay[(row, col)] = bchar

    # Only tiles that are nothing but flat impassable may be drawn as building.
    # This branch is reached by layouts whose tileset has no art vocabulary —
    # interiors, mostly — where the hardcoded Center/Mart/Gym rectangles are
    # stamped blind and land on whatever is under them: walkable floor, a
    # doorway, a counter. The two branches above enforce the same rule at the
    # point they paint; this one has too many write sites to guard each, so it
    # is enforced once here, on the way out.
    #
    # Gen 4 must not reach this. Its sealed stamp holes are deliberately ▓ over
    # tiles that render blank, and re-testing them would undo that fix — which
    # is why both branches above return early rather than falling through.
    for rc in [rc for rc, ch in overlay.items() if ch == "▓"]:
        cell = grid[rc[0]][rc[1]]
        if not is_plain_wall(cell, game, sec_tileset):
            del overlay[rc]

    return overlay


#: Maps the decomps themselves mark as leftovers.  The naming differs per game:
#: Emerald tags the *layout* (`UnusedCave1_Layout`), both Gen 4 games tag the map
#: constant, and Platinum additionally has `UNKNOWN_<id>` headers — slots nobody has
#: identified.  Those proved to be either duplicate revisions of the map in the very
#: next slot (`UNKNOWN_255` holds Floaroma Meadow's grid) or blank scratch maps, so
#: they belong here too.  `NOTHING` is each game's null map.
_UNUSED_PATTERNS = {
    "emerald":   (re.compile(r"Unused", re.I),),
    "heartgold": (re.compile(r"UNUSED"), re.compile(r"^MAP_NOTHING$")),
    "platinum":  (re.compile(r"UNUSED"),
                  re.compile(r"UNKNOWN_\d+"),
                  re.compile(r"NOTHING")),
}


def is_unused_map(game: str, *names: str | None) -> bool:
    """True if the decomp marks this map as a leftover the renderer should skip.

    Pass whatever identifiers are to hand — Emerald keys off the layout name, the
    Gen 4 games off the map constant — and any one of them matching is enough.
    Shared with the map-connection graph so both cover exactly the same map set.
    """
    patterns = _UNUSED_PATTERNS.get(game, ())
    return any(p.search(n) for n in names if n for p in patterns)


#: Header names that are not places at all. Both Gen 4 registries carry an
#: `EVERYWHERE` entry naming the shared world matrix itself, and it owns every
#: cell of that matrix no real map claims — 598 of HeartGold's 799, 731 of
#: Platinum's 900. It is a container, and rendering it produced the single
#: largest file in the sweep: a 1504 × 544 tile, 1.7 MB dump of the filler
#: between the real maps. In the map graph it was worse than useless, becoming a
#: node that bordered half the region.
_NON_MAPS = {"MAP_EVERYWHERE", "MAP_HEADER_EVERYWHERE"}


def is_non_map(*names: str | None) -> bool:
    """True if this header names something that is not a place — see `_NON_MAPS`.

    Kept separate from `is_unused_map` because it is a different claim: an unused
    map is real content the devs abandoned, whereas this was never a map at all.
    """
    return any(n.strip().upper() in _NON_MAPS for n in names if n)


#: Real content, but not anywhere on the map — excluded from full sweeps at the
#: user's request. Matched with underscores stripped, because the layout id and
#: the layout name disagree on them: `LAYOUT_SECRET_BASE_RED_CAVE1` against
#: `SecretBase_RedCave1_Layout`, `LAYOUT_SS_TIDAL_ROOMS` against
#: `SSTidalRooms_Layout`.
#: `map_type` -> output category for `--all`'s subdirectories. One table per
#: game, because no two of them spell these the same: a route is
#: MAP_TYPE_ROUTE in Emerald and HeartGold, but Platinum has no route type at
#: all and files its routes under MAP_TYPE_OUTDOORS. A cave is
#: MAP_TYPE_UNDERGROUND in Emerald and MAP_TYPE_CAVE in both Gen 4 games.
#:
#: Anything absent — interiors, underwater, the odd MAP_TYPE_NONE — lands in
#: `all/` only. Emerald's ocean routes count as routes; its underwater maps do
#: not, being a separate layer rather than a stretch of road.
MAP_CATEGORIES: dict[str, dict[str, str]] = {
    "emerald": {
        "MAP_TYPE_ROUTE": "routes",
        "MAP_TYPE_OCEAN_ROUTE": "routes",
        "MAP_TYPE_TOWN": "towns",
        "MAP_TYPE_CITY": "towns",
        "MAP_TYPE_UNDERGROUND": "caves",
    },
    "heartgold": {
        "MAP_TYPE_ROUTE": "routes",
        "MAP_TYPE_CITY_TOWN": "towns",
        "MAP_TYPE_CAVE": "caves",
    },
    "platinum": {
        "MAP_TYPE_OUTDOORS": "routes",
        "MAP_TYPE_TOWN_CITY": "towns",
        "MAP_TYPE_CAVE": "caves",
    },
}


def map_category(game: str, map_type: str | None) -> str | None:
    """Which `--all` subdirectory this map belongs in, or None for `all/` only."""
    return MAP_CATEGORIES.get(game, {}).get((map_type or "").strip().upper())


#: Matched exactly, not as a substring: "UNDERGROUND" appears in seven HG map
#: names and two PT ones, but only these two ARE the Underground. Ruins of Alph
#: Underground Hall, Route 5/6 Underground Path and Eterna's Underground Man's
#: House are ordinary places that happen to share the word.
_PLACELESS_EXACT = frozenset({
    "MAP_UNDERGROUND",         # HeartGold
    "MAP_HEADER_UNDERGROUND",  # Platinum
})

_PLACELESS = (
    "SECRETBASE",   # 24 empty player-decorated shells, one per entrance sprite
    "SSTIDAL",      # the ferry's interior — it has no fixed location
)


def is_secret_base(*names: str | None) -> bool:
    """True for content that is real but sits nowhere — see `_PLACELESS`.

    A third claim again, distinct from both of the above: these are neither
    abandoned nor non-places. They are rooms with no position in Hoenn, so a
    sweep of *places* has nothing to say about them.
    """
    for n in names:
        if n and n.strip().upper() in _PLACELESS_EXACT:
            return True
    return any(p in n.replace("_", "").upper() for n in names if n
               for p in _PLACELESS)


def skip_map(game: str, *names: str | None) -> bool:
    """Should this be left out of a full sweep — unused, or not a map at all?

    The single entry point both the renderer's `--all` and `map_graph.py` call,
    so the two cannot drift apart on which maps exist.
    """
    return (is_non_map(*names) or is_unused_map(game, *names)
            or is_secret_base(*names) or is_disconnected_room(*names))


def terrain_signature(tile_entry: dict) -> str:
    """Hash of a map's terrain grid — equal iff two maps have the same terrain.

    All three games are covered by one definition: Emerald tiles carry
    `metatile_id`, both Gen 4 games carry the raw land-data u16 in `raw`.
    Dimensions are folded in so a grid cannot alias a differently-shaped one.
    Events are deliberately *excluded* — see `--keep-duplicates` in
    terrain_to_ascii.py for why that distinction matters.
    """
    grid = tile_entry.get("grid") or []
    h = hashlib.blake2b(digest_size=16)
    h.update(f"{len(grid)}x{len(grid[0]) if grid else 0}|".encode())
    for row in grid:
        h.update(",".join(
            "-" if not t else str(t.get("metatile_id", t.get("raw", 0)))
            for t in row).encode())
        h.update(b"\n")
    return h.hexdigest()


def find_tile_entry(tiles: list, query: str, game: str) -> dict | None:
    q = query.strip()
    q_upper = q.upper()

    const_field = "layout_id" if game == "emerald" else "constant"
    name_field  = "layout_name" if game == "emerald" else "name"

    for e in tiles:
        if (e.get(const_field) or "").upper() == q_upper:
            return e
    for e in tiles:
        if (e.get(name_field) or "").upper() == q_upper:
            return e

    slug = q_upper.replace("-", "_")
    for prefix in ("MAP_HEADER_", "MAP_", "LAYOUT_"):
        candidate = prefix + slug
        for e in tiles:
            if (e.get(const_field) or "").upper() == candidate:
                return e

    q_lower = q.lower()
    for e in tiles:
        if q_lower in (e.get(const_field) or "").lower():
            return e
        if q_lower in (e.get(name_field) or "").lower():
            return e
        if game == "emerald":
            for mn in e.get("maps", []):
                if q_lower == mn.lower():
                    return e

    return None


def find_map_entries(maps_data: list | None, tile_entry: dict, game: str) -> list:
    """
    Return all map entries that correspond to this tile entry.

    Emerald reads the merged maps JSON (a layout is shared by several maps, so
    this can return more than one). Both Gen 4 games build the entry from the
    decomp instead and always yield exactly one, so `maps_data` is unused there.
    """
    if game == "emerald":
        if maps_data is None:
            from emerald_data import map_entries as em_map_entries
            return em_map_entries(tile_entry.get("layout_id", ""))
        map_names = set(tile_entry.get("maps", []))
        return [m for m in maps_data if m.get("map_name") in map_names]

    const = tile_entry.get("constant", "")
    if game == "heartgold":
        from heartgold_data import map_entry as hg_map_entry
        entry = hg_map_entry(const)
    else:  # platinum
        from platinum_data import map_entry as pt_map_entry
        entry = pt_map_entry(const)
    return [entry] if entry else []
