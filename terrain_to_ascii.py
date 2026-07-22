#!/usr/bin/env python3
"""
Render terrain tile data for any map as an ASCII text file.
Reads from tiles_pokeemerald.json / tiles_pokeheartgold.json / tiles_pokeplatinum.json
(produced by extract_terrain_data.py).  Optionally reads maps_poke{game}.json
(produced by extract_maps.py) for building overlay and warp index.

Usage:
  python3 terrain_to_ascii.py <game> <map_name_or_constant> [output.txt]
  python3 terrain_to_ascii.py <game> --all [output_dir]

  game: emerald | heartgold | platinum

Examples:
  python3 terrain_to_ascii.py emerald LittlerootTown
  python3 terrain_to_ascii.py heartgold MAP_VIOLET
  python3 terrain_to_ascii.py platinum MAP_HEADER_JUBILIFE_CITY
  python3 terrain_to_ascii.py emerald --all
  python3 terrain_to_ascii.py platinum --all terrain_maps/
"""

import json
import re
import sys
from collections import deque
from pathlib import Path

BASE_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Symbol tables — behavior_name substring -> (char, description)
# Checked in order; first match wins.
# Metatile-label lookups (Emerald only) are checked BEFORE these tables.
# ---------------------------------------------------------------------------

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
    ("SHORT_GRASS",               "'", "Short grass"),
    ("INDOOR_ENCOUNTER",          "'", "Indoor encounter"),
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
    ("BUMPY_SLOPE",               "b", "Bumpy slope"),
    # Lava / hot (h frees H for House)
    ("HOT_SPRINGS",               "h", "Hot springs"),
    # Cave / mountain
    ("CAVE",                      "c", "Cave"),
    ("MOUNTAIN_TOP",              "^", "Mountain top"),
    # Vertical movement
    ("UP_ESCALATOR",              "E", "Stairs / escalator up"),
    ("DOWN_ESCALATOR",            "e", "Stairs / escalator down"),
    ("LADDER",                    "L", "Ladder"),
    ("CRACKED_FLOOR_HOLE",        "O", "Cracked floor hole"),
    # Jumps — diagonals before cardinals to avoid partial matches
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
    ("RAIL",                      "r", "Rail"),
    # Interactables (indoor)
    ("PC",                        "P", "PC"),
    ("COUNTER",                   "C", "Counter"),
    ("TELEVISION",                "T", "Television"),
    ("BOOKSHELF",                 "B", "Bookshelf"),
    # Doors (kept minimal per design)
    ("DOOR",                      "D", "Door"),
    # Catch-all impassable
    ("IMPASSABLE",                "█", "Impassable"),
    ("SECRET_BASE_WALL",          "█", "Secret base wall"),
]

GEN4_SYMBOLS = [
    # Water
    ("WATER_SEA",                 "≈", "Sea water"),
    ("WATER_RIVER",               "~", "River water"),
    ("WATERFALL",                 "↓", "Waterfall"),
    ("SHALLOW_WATER",             "~", "Shallow water"),
    ("PUDDLE_NO_SPLASHING",       "~", "Puddle"),
    ("PUDDLE",                    "~", "Puddle"),
    # Grass / encounters
    ("VERY_TALL_GRASS",           '"', "Very tall grass"),
    ("TALL_GRASS",                '"', "Tall grass"),
    ("MUD_WITH_GRASS",            '"', "Muddy grass"),
    ("MUD_DEEP_WITH_GRASS",       '"', "Deep muddy grass"),
    # Ice
    ("ICE",                       "i", "Ice"),
    # Sand / mud / snow
    ("SAND",                      ".", "Sand"),
    ("MUD_DEEP",                  ":", "Deep mud"),
    ("MUD",                       "m", "Mud"),
    ("SNOW_DEEPEST",              "*", "Deep snow"),
    ("SNOW_DEEPER",               "*", "Deep snow"),
    ("SNOW_DEEP",                 "*", "Deep snow"),
    ("SNOW_WITH_SHADOWS",         "s", "Snow"),
    ("SNOW_SHALLOW",              "s", "Shallow snow"),
    # Cave / mountain
    ("CAVE_FLOOR",                "c", "Cave floor"),
    ("MOUNTAIN_FLOOR",            "^", "Mountain floor"),
    ("OLD_CHATEAU_FLOOR",         "c", "Old Chateau floor"),
    # Warps / doors (minimal)
    ("WARP_ENTRANCE",             "W", "Warp entrance"),
    ("WARP_STAIRS",               "W", "Warp stairs"),
    ("WARP_PANEL",                "W", "Warp panel"),
    ("WARP_EAST",                 "W", "Warp E"),
    ("WARP_WEST",                 "W", "Warp W"),
    ("WARP_NORTH",                "W", "Warp N"),
    ("WARP_SOUTH",                "W", "Warp S"),
    ("DOOR",                      "D", "Door"),
    ("ESCALATOR",                 "E", "Escalator"),
    # Jumps — diagonals first
    ("JUMP_NORTH_TWICE",          "▲", "Jump N×2"),
    ("JUMP_SOUTH_TWICE",          "▼", "Jump S×2"),
    ("JUMP_EAST_TWICE",           "►", "Jump E×2"),
    ("JUMP_WEST_TWICE",           "◄", "Jump W×2"),
    ("JUMP_EAST",                 "►", "Jump E"),
    ("JUMP_WEST",                 "◄", "Jump W"),
    ("JUMP_NORTH",                "▲", "Jump N"),
    ("JUMP_SOUTH",                "▼", "Jump S"),
    # Directional blocks
    ("BLOCK_NORTH_AND_EAST",      "▐", "Block NE"),
    ("BLOCK_NORTH_AND_WEST",      "▌", "Block NW"),
    ("BLOCK_SOUTH_AND_EAST",      "▐", "Block SE"),
    ("BLOCK_SOUTH_AND_WEST",      "▌", "Block SW"),
    ("BLOCK_NORTH_AND_SOUTH",     "│", "Block N+S"),
    ("BLOCK_EAST_AND_WEST",       "─", "Block E+W"),
    ("BLOCK_EASTWARD",            "▐", "Block E"),
    ("BLOCK_WESTWARD",            "▌", "Block W"),
    ("BLOCK_NORTHWARD",           "▀", "Block N"),
    ("BLOCK_SOUTHWARD",           "▄", "Block S"),
    ("BLOCK_",                    "▒", "Partial block"),
    # Rock climb
    ("ROCK_CLIMB",                "R", "Rock climb"),
    # Bridges
    ("BIKE_BRIDGE",               "=", "Bike bridge"),
    ("BRIDGE",                    "=", "Bridge"),
    # Gym puzzles
    ("PASTORIA_GYM",              "G", "Gym tile"),
    # Berry / special
    ("BERRY_PATCH",               "b", "Berry patch"),
    # Interactables
    ("PC",                        "P", "PC"),
    ("TOWN_MAP",                  "M", "Town map"),
    ("TV",                        "T", "TV"),
    ("TABLE",                     "C", "Table/counter"),
    ("BOOKSHELF",                 "B", "Bookshelf"),
    ("MART_SHELF",                "B", "Mart shelf"),
    ("TRASH_CAN",                 "t", "Trash can"),
    # Bike
    ("BIKE_RAMP",                 "r", "Bike ramp"),
    ("BIKE_SLOPE",                "r", "Bike slope"),
    ("BIKE_PARKING",              "r", "Bike parking"),
    # Reflection
    ("REFLECTIVE",                "~", "Reflective floor"),
]

SYMBOL_TABLES = {
    "emerald":   EMERALD_SYMBOLS,
    "heartgold": GEN4_SYMBOLS,
    "platinum":  GEN4_SYMBOLS,
}

# Metatile-label substrings -> char (Emerald only, checked before behavior table)
METATILE_LABEL_SYMBOLS = [
    ("RockWall_GrassBase",  "¬", "Cliff base (grass)"),
    ("RockWall_RockBase",   "¬", "Cliff base (rock)"),
    ("RockWall_SandBase",   "¬", "Cliff base (sand)"),
    ("CaveEntrance_Bottom", "c", "Cave entrance"),
]

# Building type keyword -> char (applied via warp overlay)
BUILDING_KEYWORDS = [
    ("POKEMON_CENTER",  "C", "Pokemon Center"),
    ("MART",            "M", "PokeMart"),
    ("GYM",             "G", "Gym"),
    ("LAB",             "K", "Lab / Research"),
    ("INSTITUTE",       "K", "Lab / Research"),
    ("CORP",            "K", "Lab / Research"),
    ("BIRCH",           "K", "Lab / Research"),
    ("ELITE",           "F", "Elite Four"),
    ("POKEMON_LEAGUE",  "F", "Elite Four"),
    ("CHAMPION",        "F", "Pokemon League"),
    ("HOUSE",           "H", "House"),
    ("HOME",            "H", "House"),
    ("FLAT",            "H", "House"),
]

BUILDING_CHAR_DESC = {
    "C": "Pokemon Center",
    "M": "PokeMart",
    "G": "Gym",
    "K": "Lab / Research facility",
    "F": "Elite Four / Pokemon League",
    "H": "House",
    "▓": "Building wall",
}

ELEVATION_MULTI_LEVEL = 15  # bridge tiles in Emerald


# ---------------------------------------------------------------------------
# Tile character resolution
# ---------------------------------------------------------------------------

def tile_to_char(cell: dict, game: str) -> str:
    """Resolve a grid cell to a single display character."""
    # 1. Metatile label check (Emerald only)
    if game == "emerald":
        label = cell.get("metatile_label") or ""
        for substr, ch, _ in METATILE_LABEL_SYMBOLS:
            if substr in label:
                return ch

    # 2. Behavior name substring match
    bname = (cell.get("behavior_name") or "").upper()
    for keyword, ch, _ in SYMBOL_TABLES[game]:
        if keyword.upper() in bname:
            return ch

    # 3. Passability fallback
    return " " if cell.get("passable", True) else "█"


def _char_description(ch: str, game: str) -> str:
    """Human-readable description for a rendered character."""
    if ch == "█":
        return "Impassable / wall"
    if ch == " ":
        return "Normal / passable"
    if ch == "▓":
        return "Building wall"
    if ch == "¬":
        return "Cliff base / stair step"
    for _, tch, desc in METATILE_LABEL_SYMBOLS:
        if tch == ch:
            return desc
    for _, tch, desc in SYMBOL_TABLES.get(game, []):
        if tch == ch:
            return desc
    return "Unknown"


# ---------------------------------------------------------------------------
# Building overlay via warp cross-reference
# ---------------------------------------------------------------------------

def _classify_building(dest: str) -> tuple[str, str] | None:
    """Return (char, description) for a destination map name, or None."""
    dest_up = dest.upper()
    for keyword, ch, desc in BUILDING_KEYWORDS:
        if keyword in dest_up:
            return ch, desc
    return None


def _flood_fill_building(grid: list, start_row: int, start_col: int,
                          rows: int, cols: int) -> set:
    """
    BFS from (start_row, start_col) through adjacent impassable MB_NORMAL cells.
    Returns set of (row, col) in the building footprint (excluding the start tile).
    Capped at 200 cells to prevent leaking into cliffs or open terrain.
    """
    MAX_CELLS = 200
    visited = set()
    queue = deque()

    # Seed: check the 4 neighbours of the warp tile (warp tile itself is passable)
    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        nr, nc = start_row + dr, start_col + dc
        if 0 <= nr < rows and 0 <= nc < cols:
            cell = grid[nr][nc]
            bname = (cell.get("behavior_name") or "").upper()
            if cell.get("collision", 0) != 0 and ("MB_NORMAL" in bname or bname == "MB_0"):
                queue.append((nr, nc))
                visited.add((nr, nc))

    while queue and len(visited) < MAX_CELLS:
        r, c = queue.popleft()
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = r + dr, c + dc
            if (nr, nc) in visited:
                continue
            if not (0 <= nr < rows and 0 <= nc < cols):
                continue
            cell = grid[nr][nc]
            bname = (cell.get("behavior_name") or "").upper()
            if cell.get("collision", 0) != 0 and ("MB_NORMAL" in bname or bname == "MB_0"):
                visited.add((nr, nc))
                queue.append((nr, nc))

    return visited


def build_overlay(tile_entry: dict, maps_data: list | None, game: str) -> dict:
    """
    Build {(row, col): char} overlay for building footprints and warp positions.
    Returns empty dict if maps_data is None.
    """
    if maps_data is None:
        return {}

    grid = tile_entry.get("grid") or []
    rows = len(grid)
    cols = len(grid[0]) if rows else 0

    # Find matching map entries
    if game == "emerald":
        map_names = set(tile_entry.get("maps", []))
        matching = [m for m in maps_data if m.get("map_name") in map_names]
    else:
        const = tile_entry.get("constant", "")
        matching = [m for m in maps_data if m.get("constant") == const]

    overlay = {}

    for map_entry in matching:
        # Get warps per game
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

        for wx, wy, dest in warp_items:
            col, row = int(wx), int(wy)
            if not (0 <= row < rows and 0 <= col < cols):
                continue
            result = _classify_building(dest)
            if result is None:
                continue
            bchar, _ = result
            # Flood-fill building footprint
            footprint = _flood_fill_building(grid, row, col, rows, cols)
            for fr, fc in footprint:
                if (fr, fc) not in overlay:
                    overlay[(fr, fc)] = "▓"
            # Mark door tile with building char (overwrites ▓ if warp is on a wall tile)
            overlay[(row, col)] = bchar

    return overlay


# ---------------------------------------------------------------------------
# Tile data / map data loaders
# ---------------------------------------------------------------------------

def load_tiles(game: str) -> list:
    path = BASE_DIR / f"tiles_poke{game}.json"
    if not path.exists():
        sys.exit(f"ERROR: {path} not found. Run extract_terrain_data.py first.")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_maps_optional(game: str) -> list | None:
    """Load maps JSON if present; return None silently if absent."""
    path = BASE_DIR / f"maps_poke{game}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


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

    # Slug match
    slug = q_upper.replace("-", "_")
    for prefix in ("MAP_HEADER_", "MAP_", "LAYOUT_"):
        candidate = prefix + slug
        for e in tiles:
            if (e.get(const_field) or "").upper() == candidate:
                return e

    # Partial match — also check `maps` list for Emerald
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
    """Return all map entries that correspond to this tile entry."""
    if maps_data is None:
        return []
    if game == "emerald":
        map_names = set(tile_entry.get("maps", []))
        return [m for m in maps_data if m.get("map_name") in map_names]
    else:
        const = tile_entry.get("constant", "")
        return [m for m in maps_data if m.get("constant") == const]


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def render_terrain(entry: dict, game: str, maps_data: list | None,
                   max_width: int = 160) -> str:
    grid = entry.get("grid")
    if not grid:
        note = entry.get("note", "no grid data")
        return _header(entry, game) + f"\n  ({note})\n"

    rows = len(grid)
    cols = len(grid[0]) if rows else 0

    scale = 1
    if cols > max_width:
        scale = (cols + max_width - 1) // max_width

    grid_h = (rows + scale - 1) // scale
    grid_w = (cols + scale - 1) // scale

    # Build overlay (building footprints)
    overlay = build_overlay(entry, maps_data, game)

    # Build main char grid
    char_grid  = []
    seen_chars = {}

    for gr in range(grid_h):
        row_chars = []
        for gc in range(grid_w):
            best = " "
            for dr in range(scale):
                r = gr * scale + dr
                if r >= rows:
                    break
                for dc in range(scale):
                    c = gc * scale + dc
                    if c >= cols:
                        break
                    cell = grid[r][c]
                    if cell is None:
                        continue
                    ch = tile_to_char(cell, game)
                    if best == " " and ch != " ":
                        best = ch
            row_chars.append(best)
        char_grid.append(row_chars)

    # Apply overlay
    for (or_, oc), och in overlay.items():
        gr = or_ // scale
        gc = oc // scale
        if 0 <= gr < grid_h and 0 <= gc < grid_w:
            char_grid[gr][gc] = och

    # Collect legend chars
    for gr in range(grid_h):
        for gc in range(grid_w):
            ch = char_grid[gr][gc]
            if ch not in seen_chars:
                seen_chars[ch] = _char_description(ch, game)

    lines = [_header(entry, game)]

    # Column ruler (every 10 cells)
    ruler = "     "
    for gc in range(grid_w):
        actual_col = gc * scale
        ruler += str(actual_col)[0] if actual_col % 10 == 0 else " "
    lines.append(ruler)

    # Grid rows
    for gr in range(grid_h):
        actual_row = gr * scale
        lines.append(f"{actual_row:5d} " + "".join(char_grid[gr]))

    lines.append("")

    # Legend
    lines.append("  Legend:")
    lines.append(f"  {'Char':<6} Description")
    lines.append("  " + "-" * 40)
    for ch, desc in sorted(seen_chars.items(),
                            key=lambda x: ("\xff" if x[0] == " " else x[0])):
        display = repr(ch) if ch == " " else ch
        lines.append(f"  {display:<6} {desc}")

    if scale > 1:
        lines.append(f"\n  [Scale: 1 char = {scale}×{scale} tiles]")

    lines.append("")

    # Elevation addendum (bridge / multi-level)
    lines += _render_elevation_addendum(grid, rows, cols, scale, game)

    # Warp index
    map_entries = find_map_entries(maps_data, entry, game)
    lines += _render_warp_index(map_entries, game, maps_data)

    return "\n".join(lines)


def _render_elevation_addendum(grid: list, rows: int, cols: int,
                                scale: int, game: str) -> list[str]:
    """
    Report positions of bridge/multi-level tiles (elevation=15).
    Each position is elevated above ground — players can pass both on top and below.
    Only emitted if at least one such tile exists.
    """
    by_row: dict[int, list[tuple[int, str]]] = {}
    for r in range(rows):
        for c in range(cols):
            cell = grid[r][c] if grid[r] else None
            if not cell:
                continue
            if cell.get("elevation") != ELEVATION_MULTI_LEVEL:
                continue
            bname = (cell.get("behavior_name") or "").upper()
            if "BRIDGE" not in bname:
                continue  # skip MB_NORMAL with elevation=15 (stair approach tiles)
            ch = tile_to_char(cell, game)
            by_row.setdefault(r, []).append((c, ch))

    if not by_row:
        return []

    lines = [
        "=" * 70,
        "  Elevation Addendum  (bridge / multi-level tiles)",
        "=" * 70,
        "",
        "  Positions with bridge tiles (elevation=15). Players at ground level",
        "  can pass underneath; bridge walkers are at elevation 4 above.",
        "",
    ]
    for row in sorted(by_row):
        cols_str = "  ".join(f"col {c}" for c, _ in sorted(by_row[row]))
        lines.append(f"  Row {row:4d}:  {cols_str}")
    lines.append("")
    return lines


def _render_warp_index(map_entries: list, game: str,
                        maps_data: list | None) -> list[str]:
    if maps_data is None:
        return [
            "=" * 70,
            "  Warp Index",
            "=" * 70,
            "",
            "  (maps JSON not found — run extract_maps.py first to enable this section)",
            "",
        ]

    if not map_entries:
        return []

    lines = [
        "=" * 70,
        "  Warp Index",
        "=" * 70,
        "",
    ]

    for map_entry in map_entries:
        map_label = (map_entry.get("map_name")
                     or map_entry.get("constant")
                     or map_entry.get("name", "?"))
        if len(map_entries) > 1:
            lines.append(f"  [ {map_label} ]")
            lines.append("")

        # Warps (building/indoor destinations)
        if game == "emerald":
            warps = map_entry.get("warp_events", [])
            warp_lines = []
            for w in warps:
                dest = w.get("dest_map", "?")
                wid  = w.get("dest_warp_id", "0")
                warp_lines.append(
                    f"    (col={w.get('x','?'):>3}, row={w.get('y','?'):>3})"
                    f"  →  {dest}  [warp #{wid}]"
                )
        elif game == "heartgold":
            warps = map_entry.get("warps", [])
            warp_lines = []
            for w in warps:
                dest   = w.get("header", "?")
                anchor = w.get("anchor", 0)
                warp_lines.append(
                    f"    (x={w.get('x','?'):>6}, z={w.get('z','?'):>6})"
                    f"  →  {dest}  [anchor #{anchor}]"
                )
        else:  # platinum
            warps = map_entry.get("warp_events", [])
            warp_lines = []
            for w in warps:
                dest = w.get("dest_header_id", "?")
                wid  = w.get("dest_warp_id", 0)
                warp_lines.append(
                    f"    (x={w.get('x','?'):>6}, z={w.get('z','?'):>6})"
                    f"  →  {dest}  [warp #{wid}]"
                )

        if warp_lines:
            lines.append("  Warps (doors / building entrances):")
            lines.extend(warp_lines)
            lines.append("")

        # Connections (adjacent routes/areas) — Emerald only
        if game == "emerald":
            connections = map_entry.get("connections", [])
            if connections:
                lines.append("  Connections (adjacent areas):")
                for conn in connections:
                    direction = conn.get("direction", "?")
                    dest      = conn.get("map", "?")
                    offset    = conn.get("offset", 0)
                    lines.append(
                        f"    {direction:<8}  →  {dest}  (offset: {offset})"
                    )
                lines.append("")

        if not warp_lines and not (game == "emerald" and map_entry.get("connections")):
            lines.append("  (no warps or connections on this map)")
            lines.append("")

    return lines


def _header(entry: dict, game: str) -> str:
    if game == "emerald":
        name      = entry.get("layout_name", "?")
        constant  = entry.get("layout_id", "")
        maps_list = ", ".join(entry.get("maps", [])[:5])
        extra     = f"  Maps     : {maps_list}" if maps_list else ""
        ts_line   = (f"  Tilesets : {entry.get('primary_tileset', '')} / "
                     f"{entry.get('secondary_tileset', '')}")
        size_line = f"  Size     : {entry.get('width','?')} × {entry.get('height','?')} tiles"
    else:
        name      = entry.get("name", "?")
        constant  = entry.get("constant", "")
        extra     = f"  Matrix   : {entry.get('matrix_id', '?')}"
        ts_line   = ""
        size_line = (f"  Size     : {entry.get('grid_width','?')} × "
                     f"{entry.get('grid_height','?')} tiles")

    lines = [
        "=" * 70,
        f"  {name}",
        f"  Constant : {constant}",
        f"  Game     : {game}",
        size_line,
    ]
    if extra:
        lines.append(extra)
    if ts_line:
        lines.append(ts_line)
    lines += ["=" * 70, ""]
    return "\n".join(lines)


def safe_filename(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name).strip("_").lower()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)

    game = args[0].lower()
    if game not in ("emerald", "heartgold", "platinum"):
        sys.exit(f"Unknown game '{game}'. Choose: emerald | heartgold | platinum")

    tiles     = load_tiles(game)
    maps_data = load_maps_optional(game)

    const_field = "layout_id" if game == "emerald" else "constant"
    name_field  = "layout_name" if game == "emerald" else "name"

    # --all mode
    if args[1] == "--all":
        out_dir = Path(args[2]) if len(args) >= 3 else BASE_DIR / f"terrain_maps_{game}"
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"Rendering {len(tiles)} maps to {out_dir}/...")
        for i, entry in enumerate(tiles):
            fname = safe_filename(
                entry.get(const_field) or entry.get(name_field) or f"map_{i:04d}"
            )
            out_path = out_dir / f"{fname}.txt"
            text = render_terrain(entry, game, maps_data)
            out_path.write_text(text, encoding="utf-8")
        print("Done.")
        return

    # Single map mode
    query = args[1]
    entry = find_tile_entry(tiles, query, game)
    if entry is None:
        sys.exit(f"Map '{query}' not found in {game} tile data.")

    text = render_terrain(entry, game, maps_data)

    if len(args) >= 3:
        out_path = Path(args[2])
        out_path.write_text(text, encoding="utf-8")
        print(f"Written to {out_path}")
    else:
        print(text)


if __name__ == "__main__":
    main()
