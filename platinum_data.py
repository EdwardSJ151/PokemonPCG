"""
Platinum-specific terrain data helpers.

Every map's tile data is loaded directly from map_data_NNN.bin binary files,
using the map matrix JSON to find which blocks belong to it. Map identity comes
from the decomp's own tables (see _map_registry), so tiles_pokeplatinum.json is
not read at all — it is 4.8 GB, its index mappings are wrong for outdoor maps,
and it is missing 88 of the 593 maps.

Encounter data: pokeplatinum/res/field/encounters/{wildEncountersArchiveID}.json
Trainer data:   pokeplatinum/res/trainers/data/{trainer_name_lower}.json
Honey trees:    pokeplatinum/res/field/encounters/encounters_honey_tree.json
"""

import json
import math
import re
import struct
from pathlib import Path

from terrain_helpers import BASE_DIR, _fmt_movement, move_display
from gen4_data import (
    _s16, _s10, _parse_nds_faces, _parse_namelist, _all_material_mesh_faces,
    _cluster_levels, _parse_bdhc_stair_tiles, _parse_bdhc_flat_plates,
    _NDS_PARAM_COUNTS, _RENDER_CMD_PARAMS, _LEVEL_GAP_FP,
    parse_terrain_grid, parse_prop_entries, land_material_tile_ys,
    land_center_rule_tiles, prop_model_footprint_from_bytes,

)

MATRIX_DIR        = BASE_DIR / "pokeplatinum/res/field/matrices"
MAP_DATA_DIR      = BASE_DIR / "pokeplatinum/res/field/maps/data"
#: Per-map event archives, named by the header table's eventsArchiveID. All 593
#: maps resolve here, so nothing needs maps_pokeplatinum.json (which had 522).
EVENTS_DIR        = BASE_DIR / "pokeplatinum/res/field/events"
#: The two tables that between them describe every map. The generated list is
#: ordered, so a constant's line number is its map id; the header table names
#: each map's matrix, event and encounter archive symbolically.
MAP_HEADERS_TXT   = BASE_DIR / "pokeplatinum/generated/map_headers.txt"
MAP_HEADERS_H     = BASE_DIR / "pokeplatinum/include/data/map_headers.h"
BLOCK_TILES       = 32
VISIBLE_ITEMS_S   = BASE_DIR / "pokeplatinum/res/field/scripts/scripts_visible_items.s"
HIDDEN_ITEMS_H    = BASE_DIR / "pokeplatinum/include/data/field/hidden_items.h"
_VISIBLE_ITEM_OFFSET = 7000
_HIDDEN_ITEM_OFFSET  = 8000

# ---------------------------------------------------------------------------
# Map registry — map identity straight from the decomp
# ---------------------------------------------------------------------------

_REGISTRY: dict | None = None

#: `[MAP_HEADER_ROUTE_203] = { ... }` in sMapHeaders[].
_HEADER_ENTRY_RE = re.compile(r"\[(MAP_HEADER_\w+)\] = \{(.*?)\n    \},", re.S)
_HEADER_FIELD_RE = re.compile(r"\.(\w+)\s*=\s*([^,\n]+)")
#: Sentinels at the end of the generated list; they are not maps.
_NOT_A_MAP = ("MAP_HEADER_COUNT", "MAP_HEADER_INVALID", "MAP_HEADER_DYNAMIC")


def _pretty_map_name(constant: str) -> str:
    """MAP_HEADER_MT_CORONET_B1F -> 'Mt Coronet B1F'. Floor and player-count
    tokens keep their case, matching the names in maps_pokeplatinum.json."""
    out = []
    for tok in constant[len("MAP_HEADER_"):].split("_"):
        out.append(tok if re.fullmatch(r"B?\d+[FP]", tok) or tok.isdigit()
                   else tok.capitalize())
    return " ".join(out)


def _map_registry() -> dict:
    """
    Every map, keyed by its MAP_HEADER_* constant.

    Built from the decomp's own two tables so nothing here depends on
    tiles_pokeplatinum.json. Verified against maps_pokeplatinum.json: the
    mapMatrixID agrees on all 534 shared entries and the derived display names
    match. The registry additionally covers the 88 maps the JSON never had.
    """
    global _REGISTRY
    if _REGISTRY is not None:
        return _REGISTRY

    order = [ln.strip() for ln in
             MAP_HEADERS_TXT.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.startswith(_NOT_A_MAP)]
    ids = {c: i for i, c in enumerate(order)}

    reg: dict = {}
    text = MAP_HEADERS_H.read_text(encoding="utf-8", errors="replace")
    for const, body in _HEADER_ENTRY_RE.findall(text):
        fields = dict(_HEADER_FIELD_RE.findall(body))
        matrix = fields.get("mapMatrixID", "").strip()
        reg[const] = {
            "constant":    const,
            "name":        _pretty_map_name(const),
            "zone_id":     ids.get(const),
            "map_type":    fields.get("mapType", "").strip(),
            "matrix_id":   matrix,
            "matrix_file": MATRIX_DIR / f"{matrix}.json",
            # Only map_matrix_000 is the shared world matrix; every other map
            # owns its matrix outright, so the whole matrix is that map.
            "own_matrix":  matrix != "map_matrix_000",
            "events_id":   fields.get("eventsArchiveID", "").strip(),
            "enc_id":      fields.get("wildEncountersArchiveID", "").strip(),
        }
    _REGISTRY = reg
    return _REGISTRY


def map_registry_entries() -> list:
    """Registry rows in map-id order — the map list for `--all`."""
    return sorted(_map_registry().values(),
                  key=lambda e: (e["zone_id"] is None, e["zone_id"]))


def map_entry(constant: str) -> dict | None:
    """
    The map entry shape load_events() expects, assembled from the decomp.

    Replaces the lookup into maps_pokeplatinum.json: the registry supplies
    `constant` and `mapMatrixID`, and the event archive supplies the rest
    (object_events / bg_events / warp_events), field-for-field identical to
    what the merged JSON carried.
    """
    reg = _map_registry().get(constant)
    if reg is None:
        return None
    entry = {"constant":     constant,
             "name":         reg["name"],
             "mapMatrixID":  reg["matrix_id"],
             "eventsArchiveID":         reg["events_id"],
             "wildEncountersArchiveID": reg["enc_id"]}
    path = EVENTS_DIR / f"{reg['events_id']}.json"
    if path.exists():
        entry.update(json.loads(path.read_text(encoding="utf-8")))
    return entry


def _find_zone(query: str) -> dict | None:
    """Match a user query against the registry by display name or constant."""
    q_slug = re.sub(r"[^a-z0-9]", "", (query or "").strip().lower())
    if not q_slug:
        return None

    def slug(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", (s or "").lower())

    reg = _map_registry()
    for key in ("name", "constant"):
        for entry in reg.values():
            if slug(entry.get(key, "")) == q_slug:
                return entry
    for entry in reg.values():                       # partial, name only
        if q_slug in slug(entry.get("name", "")):
            return entry
    return None


# ---------------------------------------------------------------------------
# Behavior name lookup
# ---------------------------------------------------------------------------

_BEHAVIOR_NAMES: dict | None = None

def _get_behavior_names() -> dict:
    global _BEHAVIOR_NAMES
    if _BEHAVIOR_NAMES is not None:
        return _BEHAVIOR_NAMES
    h_file = BASE_DIR / "pokeplatinum/include/constants/field/map_tile_behaviors.h"
    names: dict = {}
    if not h_file.exists():
        _BEHAVIOR_NAMES = names
        return names
    enum_re = re.compile(r"^\s*(TILE_BEHAVIOR_\w+)")
    idx = 0
    in_enum = False
    for line in h_file.read_text(encoding="utf-8").splitlines():
        if "enum" in line and "{" in line:
            in_enum = True
            idx = 0
            continue
        if in_enum:
            if "}" in line:
                break
            m = enum_re.match(line)
            if m:
                if "=" in line:
                    val_m = re.search(r"=\s*(\d+)", line)
                    if val_m:
                        idx = int(val_m.group(1))
                names[idx] = m.group(1)
                idx += 1
    _BEHAVIOR_NAMES = names
    return names


# ---------------------------------------------------------------------------
# Visible item lookup  (script - 7000 → item name)
# ---------------------------------------------------------------------------

_VISIBLE_ITEMS_CACHE: list[str] | None = None

_PT_ITEM_VAR_RE = re.compile(r"SetVar\s+VAR_0x8004\s*,\s*(ITEM_\w+)", re.I)


def _pt_item_display(constant: str) -> str:
    """ITEM_TM80 → 'TM80 (Rock Slide)', ITEM_POKE_BALL → 'Poke Ball'."""
    move = _pt_tm_move_name(constant)
    if move:
        num = constant.removeprefix("ITEM_TM")
        return f"TM{num} ({move})"
    return _item_name(constant)


def _get_visible_item_name(script_id: int) -> str:
    global _VISIBLE_ITEMS_CACHE
    if _VISIBLE_ITEMS_CACHE is None:
        entries: list[str] = []
        if VISIBLE_ITEMS_S.exists():
            for line in VISIBLE_ITEMS_S.read_text(encoding="utf-8").splitlines():
                m = re.search(r"ScriptEntry\s+(VisibleItems_\S+)", line)
                if m:
                    label = m.group(1)
                    # "VisibleItems_Route203_PokeBall" → "Poke Ball"
                    parts = label.split("_", 2)
                    raw = parts[2] if len(parts) > 2 else label
                    # "TM80" → "TM80 (Rock Slide)" via the move table
                    tm_m = re.fullmatch(r"TM(\d+)", raw)
                    if tm_m:
                        num = int(tm_m.group(1))
                        move = _pt_tm_move_name(f"ITEM_TM{num:02d}")
                        name = f"TM{num:02d} ({move})" if move else f"TM{num:02d}"
                    else:
                        name = re.sub(r"([A-Z])", r" \1", raw).strip()
                    entries.append(name)
        _VISIBLE_ITEMS_CACHE = entries
    idx = script_id - _VISIBLE_ITEM_OFFSET
    if 0 <= idx < len(_VISIBLE_ITEMS_CACHE):
        return _VISIBLE_ITEMS_CACHE[idx]
    return f"Item#{script_id}"


def _resolve_item_ball(map_name: str, script_id: int) -> str:
    """Resolve an item ball script_id to one or more item names.

    For normal item balls the visible-items table gives a single name.  When
    that lookup fails (script_id < _VISIBLE_ITEM_OFFSET), the map script body
    is BFS'd for SetVar VAR_0x8004 assignments — all reachable ITEM_ constants
    are returned joined with ' / ' so conditional drops are fully visible.
    """
    name = _get_visible_item_name(script_id)
    if not name.startswith("Item#"):
        return name
    table = _load_script_table(map_name)
    body_dict = _load_script_body(map_name)
    idx = script_id - 1
    if idx < 0 or idx >= len(table):
        return name
    label = table[idx]
    bfs_lines = _pt_bfs_body(label, body_dict)
    body = "\n".join(bfs_lines)
    items = list(dict.fromkeys(
        _pt_item_display(c) for c in _PT_ITEM_VAR_RE.findall(body)
    ))
    return " / ".join(items) if items else name


# ---------------------------------------------------------------------------
# Hidden item lookup  (bg_event type=2, script - 8000 → item name, qty)
# ---------------------------------------------------------------------------

_HIDDEN_ITEMS_CACHE: list[tuple[str, int]] | None = None

def _get_hidden_item(script_id: int) -> tuple[str, int] | None:
    global _HIDDEN_ITEMS_CACHE
    if _HIDDEN_ITEMS_CACHE is None:
        entries: list[tuple[str, int]] = []
        if HIDDEN_ITEMS_H.exists():
            entry_re = re.compile(
                r"HIDDEN_ITEM_ENTRY\(\s*(ITEM_\w+)\s*,\s*(\d+)"
            )
            for m in entry_re.finditer(HIDDEN_ITEMS_H.read_text(encoding="utf-8")):
                entries.append((_pt_item_display(m.group(1)), int(m.group(2))))
        _HIDDEN_ITEMS_CACHE = entries
    idx = script_id - _HIDDEN_ITEM_OFFSET
    if 0 <= idx < len(_HIDDEN_ITEMS_CACHE):
        return _HIDDEN_ITEMS_CACHE[idx]
    return None


# ---------------------------------------------------------------------------
# Building classification from warp dest_header_id
# ---------------------------------------------------------------------------

# Keywords in dest_header_id → (map char, display label)
_BUILDING_WARP_TYPES: list[tuple[str, str, str]] = [
    ("POKECENTER",     "P",  "Pokémon Center"),
    ("POKEMON_CENTER", "P",  "Pokémon Center"),
    ("MART",           "$",  "Pokémart"),
    ("GYM",            "G",  "Gym"),
    ("TRAINERS_SCHOOL","S",  "Trainers' School"),
    ("JUBILIFE_TV",    "T",  "Jubilife TV"),
    ("POKETCH",        "K",  "Pokétch Co."),
    ("GLOBAL_TERMINAL","Q",  "Global Terminal"),
    ("CONTEST",        "C",  "Contest Hall"),
    ("POKEMON_MANSION","M",  "Pokémon Mansion"),
    ("GAME_CORNER",    "X",  "Game Corner"),
    ("HOTEL",          "h",  "Hotel"),
    ("HOSPITAL",       "h",  "Hospital"),
    ("RESORT_AREA",    "Z",  "Resort Area"),
    ("CANALAVE_LIBRARY","j", "Library"),
    ("SNOWPOINT_TEMPLE","J", "Snowpoint Temple"),
    ("HOUSE",          "h",  "House"),
    ("CAVE",           "c",  "Cave"),
    ("TUNNEL",         "t",  "Tunnel"),
]

def _classify_warp(dest_header_id: str) -> tuple[str, str] | None:
    """Return (char, label) for a warp destination, or None for generic doors."""
    uhdr = dest_header_id.upper()
    for keyword, char, label in _BUILDING_WARP_TYPES:
        if keyword in uhdr:
            return char, label
    return None


# ---------------------------------------------------------------------------
# Binary land_data parsing
# ---------------------------------------------------------------------------

def _parse_map_data_bin(raw: bytes) -> list[list[dict]] | None:
    """Parse a map_data_NNN.bin file into a 32×32 grid of behavior dicts."""
    return parse_terrain_grid(raw, "platinum", _get_behavior_names())


_MAP_DATA_CACHE: dict = {}
_STAIR_CACHE: dict = {}
_MATERIAL_TILE_CACHE: dict = {}
_FLAT_PLATE_CACHE: dict = {}


def _load_flat_plates(numeric_id: int) -> list[tuple[float, float, float, float]]:
    """Flat BDHC plate rects for map_data_NNN.bin, cached."""
    if numeric_id in _FLAT_PLATE_CACHE:
        return _FLAT_PLATE_CACHE[numeric_id]
    path = MAP_DATA_DIR / f"map_data_{numeric_id:03d}.bin"
    result = _parse_bdhc_flat_plates(path.read_bytes(), "platinum") if path.exists() else []
    _FLAT_PLATE_CACHE[numeric_id] = result
    return result

# Platinum tileset material vocabulary (per-game — HeartGold gets its own).
# Tree materials vary per tileset; conttree*_b/_t are the trunk/top halves of
# the dense border-forest trees. tshadow (flat ground decal) is excluded.
_TREE_MATERIAL_NAMES = {
    "tree01", "tree2_01", "tree3_02",
    "conttree_b", "conttree_t", "conttree2_b", "conttree2_t",
}

_TREE_MATERIALS_FS   = frozenset(_TREE_MATERIAL_NAMES)
_BRIDGE_MATERIALS_FS = frozenset({"bridge"})

_CENTER_TILE_CACHE: dict = {}


def _load_block_material_ys(numeric_id: int) -> dict[str, dict[tuple[int, int], tuple[int, ...]]]:
    """Every material's footprint + height levels for a block, cached."""
    if numeric_id in _MATERIAL_TILE_CACHE:
        return _MATERIAL_TILE_CACHE[numeric_id]
    path = MAP_DATA_DIR / f"map_data_{numeric_id:03d}.bin"
    result = land_material_tile_ys(path.read_bytes(), "platinum") if path.exists() else {}
    _MATERIAL_TILE_CACHE[numeric_id] = result
    return result


def _load_center_rule_tiles(numeric_id: int, materials: frozenset[str]) -> set[tuple[int, int]]:
    """Tile-center-rule tiles for the given materials in a block, cached."""
    key = (numeric_id, materials)
    if key in _CENTER_TILE_CACHE:
        return _CENTER_TILE_CACHE[key]
    path = MAP_DATA_DIR / f"map_data_{numeric_id:03d}.bin"
    result = (land_center_rule_tiles(path.read_bytes(), "platinum", materials)
              if path.exists() else set())
    _CENTER_TILE_CACHE[key] = result
    return result


# ---------------------------------------------------------------------------
# Prop buildings — placements from the props section, footprints from each
# prop model's own NSBMD vertices (formula verified against Jubilife collision)
# ---------------------------------------------------------------------------

PROP_MODELS_DIR  = BASE_DIR / "pokeplatinum/res/field/props/models"
PROP_ORDER_FILE  = PROP_MODELS_DIR / "map_prop_models.order"

_PROP_ORDER_CACHE: list[str] | None = None
_PROP_FOOTPRINT_CACHE: dict = {}
_BLOCK_PROPS_CACHE: dict = {}


def _prop_model_file(model_id: int) -> Path | None:
    """Resolve a props-section modelID to its .nsbmd file via the order file."""
    global _PROP_ORDER_CACHE
    if _PROP_ORDER_CACHE is None:
        _PROP_ORDER_CACHE = (
            PROP_ORDER_FILE.read_text(encoding="utf-8").split()
            if PROP_ORDER_FILE.exists() else []
        )
    if 0 <= model_id < len(_PROP_ORDER_CACHE):
        return PROP_MODELS_DIR / _PROP_ORDER_CACHE[model_id]
    return None


def _prop_model_footprint(model_id: int) -> tuple[str, float, float, float, float, float, float] | None:
    """
    Return (internal_name, min_dc, max_dc, min_dr, max_dr, min_dy, max_dy)
    for a prop model: the footprint extent in tiles relative to the placement
    position, plus the vertical (y) extent — used for waterfall face height.
    world_units = vertex/4096 × up_scale (model header word 7), 16 units/tile.
    Returns None for missing/unparseable models.
    """
    if model_id in _PROP_FOOTPRINT_CACHE:
        return _PROP_FOOTPRINT_CACHE[model_id]
    path = _prop_model_file(model_id)
    result = (prop_model_footprint_from_bytes(path.read_bytes())
              if path is not None and path.exists() else None)
    _PROP_FOOTPRINT_CACHE[model_id] = result
    return result



def _load_block_props(numeric_id: int) -> list[tuple[int, float, float]]:
    """
    Return (model_id, local_col, local_row) placements from the props section
    of map_data_NNN.bin. Entry format (decomp MapPropFile, 48 bytes):
    int modelID; VecFx32 position, rotation, scale; int dummy[2].
    Position: 1 tile = 16 fx32 world units, block center at tile (16, 16).
    """
    if numeric_id in _BLOCK_PROPS_CACHE:
        return _BLOCK_PROPS_CACHE[numeric_id]
    path = MAP_DATA_DIR / f"map_data_{numeric_id:03d}.bin"
    result = parse_prop_entries(path.read_bytes(), "platinum") if path.exists() else []
    _BLOCK_PROPS_CACHE[numeric_id] = result
    return result


def _load_map_data_block(numeric_id: int) -> list[list[dict]] | None:
    """Load and parse map_data_NNN.bin, with caching."""
    if numeric_id in _MAP_DATA_CACHE:
        return _MAP_DATA_CACHE[numeric_id]
    path = MAP_DATA_DIR / f"map_data_{numeric_id:03d}.bin"
    result = _parse_map_data_bin(path.read_bytes()) if path.exists() else None
    _MAP_DATA_CACHE[numeric_id] = result
    return result


def _load_stair_tiles(numeric_id: int) -> set[tuple[int, int]]:
    """Return stair (col, row) set for map_data_NNN.bin block, cached."""
    if numeric_id in _STAIR_CACHE:
        return _STAIR_CACHE[numeric_id]
    path = MAP_DATA_DIR / f"map_data_{numeric_id:03d}.bin"
    result = _parse_bdhc_stair_tiles(path.read_bytes(), "platinum") if path.exists() else set()
    _STAIR_CACHE[numeric_id] = result
    return result


# ---------------------------------------------------------------------------
# Map tile entry — outdoor maps load from binaries, indoor use JSON
# ---------------------------------------------------------------------------

def get_zone_tile_entry(tiles: list, query: str, maps_data: list | None = None) -> dict | None:
    """
    Assemble a map's grid from the blocks its matrix names.

    `tiles` and `maps_data` are accepted for signature compatibility and
    ignored — map identity and matrix now come from the decomp, see
    _map_registry. Indoor maps take the same path as outdoor ones: they simply
    own their matrix, so every block in it is theirs.
    """
    entry = _find_zone(query)
    if entry is None:
        return None

    matrix_path = entry["matrix_file"]
    if not matrix_path.exists():
        return None
    with open(matrix_path, encoding="utf-8") as f:
        matrix_json = json.load(f)

    headers = matrix_json.get("headers", [])  # named constants per block
    maps    = matrix_json.get("maps", [])      # numeric IDs per block (MAP_NNN)
    if not maps:
        return None

    if entry["own_matrix"]:
        # The matrix holds this map and nothing else, so the whole matrix is
        # ours and its origin is (0, 0).
        r_min, c_min = 0, 0
        r_max, c_max = len(maps) - 1, len(maps[0]) - 1
    else:
        blocks = [
            (r, c)
            for r, row in enumerate(headers)
            for c, cell in enumerate(row)
            if cell == entry["constant"]
        ]
        if not blocks:
            return None
        r_min = min(r for r, c in blocks)
        r_max = max(r for r, c in blocks)
        c_min = min(c for r, c in blocks)
        c_max = max(c for r, c in blocks)
    n_rows = r_max - r_min + 1
    n_cols = c_max - c_min + 1

    # Sea rocks and trees are face footprints in each block's 3D model; they
    # can overhang into neighboring blocks, so collect them in combined-grid
    # coordinates first and apply after assembly.
    # {(col, row): {material: (y levels...)}} in combined-grid coordinates
    layers_global: dict[tuple[int, int], dict[str, tuple[int, ...]]] = {}
    tree_global: set[tuple[int, int]] = set()
    bridge_global: set[tuple[int, int]] = set()
    # Prop structures (buildings, fountains, ...): float tile-rects in
    # combined-grid coordinates, rasterized by the overlay builder.
    prop_structures: list[dict] = []

    # Build combined grid from binary files
    combined: list[list] = []
    for br in range(r_min, r_max + 1):
        block_rows: list[list] = [[] for _ in range(BLOCK_TILES)]
        for bc in range(c_min, c_max + 1):
            # Get numeric ID from maps field
            map_cell = maps[br][bc] if br < len(maps) and bc < len(maps[br]) else None
            num_m = re.match(r"MAP_(\d+)$", map_cell or "")
            num_id = int(num_m.group(1)) if num_m else None
            block_grid = _load_map_data_block(num_id) if num_id is not None else None
            stair_set  = _load_stair_tiles(num_id)   if num_id is not None else set()
            if num_id is not None:
                off_c = (bc - c_min) * BLOCK_TILES
                off_r = (br - r_min) * BLOCK_TILES
                for material, tile_ys in _load_block_material_ys(num_id).items():
                    for (tc, tr), levels in tile_ys.items():
                        pos = (off_c + tc, off_r + tr)
                        mats = layers_global.setdefault(pos, {})
                        if material in mats:  # overhang from a neighboring block
                            mats[material] = _cluster_levels(list(mats[material]) + list(levels))
                        else:
                            mats[material] = levels
                for tc, tr in _load_center_rule_tiles(num_id, _TREE_MATERIALS_FS):
                    tree_global.add((off_c + tc, off_r + tr))
                for tc, tr in _load_center_rule_tiles(num_id, _BRIDGE_MATERIALS_FS):
                    bridge_global.add((off_c + tc, off_r + tr))
                for model_id, lc, lr in _load_block_props(num_id):
                    mfile = _prop_model_file(model_id)
                    if mfile is None or mfile.name.endswith("_door.nsbmd"):
                        continue  # doors sit on warp tiles; not building bodies
                    fp = _prop_model_footprint(model_id)
                    if fp is None:
                        continue
                    name, min_dc, max_dc, min_dr, max_dr, _min_dy, _max_dy = fp
                    if name.startswith("wfall"):
                        continue  # vertical sheet; the lip row's behavior tiles already render ↓
                    if max_dc - min_dc < 1.5 or max_dr - min_dr < 1.5:
                        continue  # signs, small clutter — not structures
                    prop_structures.append({
                        "model_id": model_id,
                        "name":     name,
                        "file":     mfile.stem,
                        "c0": off_c + lc + min_dc, "c1": off_c + lc + max_dc,
                        "r0": off_r + lr + min_dr, "r1": off_r + lr + max_dr,
                    })
            for tr in range(BLOCK_TILES):
                if block_grid:
                    row_tiles = []
                    for tc in range(BLOCK_TILES):
                        cell = dict(block_grid[tr][tc])  # copy so we don't mutate cache
                        if (tc, tr) in stair_set:
                            cell["stair"] = True
                        row_tiles.append(cell)
                    block_rows[tr].extend(row_tiles)
                else:
                    block_rows[tr].extend([None] * BLOCK_TILES)
        combined.extend(block_rows)

    grid_w = n_cols * BLOCK_TILES
    grid_h = n_rows * BLOCK_TILES

    for (gc, gr), mats in layers_global.items():
        if not (0 <= gc < grid_w and 0 <= gr < grid_h):
            continue
        cell = combined[gr][gc]
        if cell is None:
            continue
        # layer_ys is for the elevation addendum ONLY — never grid rendering.
        # Meshes may render exactly three things: searock, trees, buildings.
        cell["layer_ys"] = mats
        if "searock" in mats:
            cell["searock"] = True

    # Trees and bridge decks use the tile-center rule, not the bbox rule.
    for gc, gr in tree_global:
        if 0 <= gc < grid_w and 0 <= gr < grid_h and combined[gr][gc] is not None:
            combined[gr][gc]["tree"] = True
    for gc, gr in bridge_global:
        if 0 <= gc < grid_w and 0 <= gr < grid_h and combined[gr][gc] is not None:
            combined[gr][gc]["bridge_mesh"] = True


    return {
        **{k: v for k, v in entry.items() if k not in ("grid", "matrix_file")},
        "grid":            combined,
        "grid_width":      grid_w,
        "grid_height":     grid_h,
        "_tile_col_min":   c_min * BLOCK_TILES,
        "_tile_row_min":   r_min * BLOCK_TILES,
        "prop_structures": prop_structures,
    }


ENC_DIR      = BASE_DIR / "pokeplatinum/res/field/encounters"
TRAINER_DIR  = BASE_DIR / "pokeplatinum/res/trainers/data"
HONEY_TREE_FILE = ENC_DIR / "encounters_honey_tree.json"

# Honey tree timing (real game: 6h wait, 24h total window).
# Set in minutes so the simulator can run at a faster pace by default.
HONEY_TREE_WAIT_MINUTES  = 6   # minutes after slathering before encounter appears
HONEY_TREE_TOTAL_MINUTES = 24  # minutes after slathering before tree goes bare again

_PRETTY_SPECIES_RE = re.compile(r"SPECIES_", re.IGNORECASE)


def _pretty_species(sp: str) -> str:
    name = _PRETTY_SPECIES_RE.sub("", sp).replace("_", " ").title()
    name = (name.replace("Mr ", "Mr. ")
               .replace("Mime Jr ", "Mime Jr. ")
               .replace("Ho Oh", "Ho-Oh")
               .replace("Porygon Z", "Porygon-Z")
               .replace("Jangmo O", "Jangmo-O"))
    return name


def _agg_mons(mons: list) -> list[tuple[str, int, int, int]]:
    """[(species, min_lv, max_lv, count)] sorted by count desc."""
    agg: dict = {}
    for m in mons:
        sp = _pretty_species(m.get("species", "?"))
        if "level_min" in m:
            lv_lo, lv_hi = m["level_min"], m["level_max"]
        else:
            lv = m.get("level", 0)
            lv_lo = lv_hi = lv
        if sp not in agg:
            agg[sp] = [lv_lo, lv_hi, 0]
        agg[sp][0] = min(agg[sp][0], lv_lo)
        agg[sp][1] = max(agg[sp][1], lv_hi)
        agg[sp][2] += 1
    return sorted(
        [(sp, d[0], d[1], d[2]) for sp, d in agg.items()],
        key=lambda x: -x[3],
    )


def _land_slot_levels(species_list: list[str], land_mons: list,
                      slot_indices: list[int]) -> list[dict]:
    """Build encounter dicts using species list + levels from corresponding base land slots."""
    result = []
    for i, sp in enumerate(species_list):
        if not sp or sp == "SPECIES_NONE":
            continue
        idx = slot_indices[i] if i < len(slot_indices) else 0
        lv  = land_mons[idx]["level"] if idx < len(land_mons) else 0
        result.append({"species": sp, "level": lv})
    return result


def _render_table(label: str, mons: list, total: int, rate: int | None = None) -> list[str]:
    if not mons:
        return []
    rate_note = f"  (rate: {rate})" if rate else ""
    lines = [f"  {label}{rate_note}"]
    for sp, mn, mx, cnt in _agg_mons(mons):
        lv_str  = f"Lv {mn}" if mn == mx else f"Lv {mn}-{mx}"
        pct     = cnt * 100 // total if total else 0
        lines.append(f"    {sp:<22} {lv_str:<12} {pct:>3}%")
    return lines


# ---------------------------------------------------------------------------
# Encounter rendering
# ---------------------------------------------------------------------------

def render_encounter_section(wild_enc_id: str, zone_name: str = "") -> list[str]:
    """Return formatted encounter lines for the given wildEncountersArchiveID."""
    if not wild_enc_id or wild_enc_id == "ENCOUNTERS_NONE":
        return []
    path = ENC_DIR / f"{wild_enc_id}.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        enc = json.load(f)

    SEP = "=" * 70
    lines = ["", SEP, "  Wild Encounters", SEP, ""]

    # Weather note
    weather_note = ""
    name_up = zone_name.upper()
    if "228" in zone_name or "ROUTE_228" in name_up:
        weather_note = "  [Permanent sandstorm weather]"
    elif "212_SOUTH" in name_up or "212 SOUTH" in zone_name.upper():
        weather_note = "  [Permanent rain weather]"
    if weather_note:
        lines.append(weather_note)
        lines.append("")

    # Detect maps where slots 6-7 are replaced at runtime by daily Pokémon.
    # Trophy Garden: daily_encounters pool in the enc file itself.
    # Great Marsh areas 1-6: daily species from encounters_great_marsh_lookout.json.
    is_trophy_garden = "daily_encounters" in enc
    is_great_marsh = (
        wild_enc_id.startswith("encounters_great_marsh_")
        and not wild_enc_id.endswith("lookout")
    )
    has_daily_slots = is_trophy_garden or is_great_marsh

    land_rate = enc.get("land_rate", 0)
    land_mons = enc.get("land_encounters", [])
    if land_rate > 0 and land_mons:
        # For maps with daily slots, render only the 10 fixed slots so the
        # daily pool is not folded into the base percentages (slots 6-7 are
        # overwritten at runtime; their JSON values are fallback defaults only).
        if has_daily_slots and len(land_mons) >= 8:
            base_land = land_mons[:6] + land_mons[8:]
        else:
            base_land = land_mons
        lines += _render_table("Tall Grass", base_land, len(base_land), land_rate)
        lines.append("")

        if is_trophy_garden:
            daily_pool = enc.get("daily_encounters", [])
            s6, s7 = land_mons[6], land_mons[7]
            lv_lo = min(s6["level"], s7["level"])
            lv_hi = max(s6["level"], s7["level"])
            lv_str = f"Lv {lv_lo}" if lv_lo == lv_hi else f"Lv {lv_lo}-{lv_hi}"
            lines.append(f"  Daily (Mr. Backlot — 2 active at once, 1 new each day, {lv_str}):")
            pool_names = ", ".join(_pretty_species(s) for s in daily_pool)
            lines.append(f"    {pool_names}")
            lines.append("")

        if is_great_marsh:
            lookout_path = ENC_DIR / "encounters_great_marsh_lookout.json"
            if lookout_path.exists():
                with open(lookout_path, encoding="utf-8") as _f:
                    lookout = json.load(_f)
                s6, s7 = land_mons[6], land_mons[7]
                lv_lo = min(s6["level"], s7["level"])
                lv_hi = max(s6["level"], s7["level"])
                lv_str = f"Lv {lv_lo}" if lv_lo == lv_hi else f"Lv {lv_lo}-{lv_hi}"
                lines.append(f"  Daily (binoculars — 2 active at once, {lv_str}):")
                before = sorted(set(_pretty_species(s) for s in lookout.get("before_national_dex", [])))
                after  = sorted(set(_pretty_species(s) for s in lookout.get("after_national_dex", [])))
                if before:
                    lines.append(f"    Pre-Natl Dex:  {', '.join(before)}")
                if after:
                    lines.append(f"    Post-Natl Dex: {', '.join(after)}")
                lines.append("")

        # Time-of-day overrides (replace base slots 2 and 3)
        day_mons   = enc.get("day", [])
        night_mons = enc.get("night", [])
        if day_mons:
            day_list = _land_slot_levels(day_mons, land_mons, [2, 3])
            lines += _render_table("  Day-only slots", day_list, len(day_list))
            lines.append("")
        if night_mons:
            night_list = _land_slot_levels(night_mons, land_mons, [2, 3])
            lines += _render_table("  Night-only slots", night_list, len(night_list))
            lines.append("")

        # Swarms (replace slots 0 and 1)
        swarms = enc.get("swarms", [])
        if swarms:
            swarm_list = _land_slot_levels(swarms, land_mons, [0, 1])
            lines += _render_table("  Swarm", swarm_list, len(swarm_list))
            lines.append("")

        # PokéRadar (replaces slots 4, 5, 10, 11)
        radar = enc.get("radar", [])
        if radar:
            radar_list = _land_slot_levels(radar, land_mons, [4, 5, 10, 11])
            lines += _render_table("  PokéRadar exclusive", radar_list, len(radar_list))
            lines.append("")

        # GBA slots (Ruby/Sapphire/Emerald → 8,9 · FireRed/LeafGreen → 10,11)
        gba_slots = [
            ("Ruby",      enc.get("ruby", []),      [8, 9]),
            ("Sapphire",  enc.get("sapphire", []),  [8, 9]),
            ("Emerald",   enc.get("emerald", []),   [8, 9]),
            ("FireRed",   enc.get("firered", []),   [10, 11]),
            ("LeafGreen", enc.get("leafgreen", []), [10, 11]),
        ]
        for cart_name, mons_list, slot_idxs in gba_slots:
            valid = [s for s in mons_list if s and s != "SPECIES_NONE"]
            if valid:
                slot_list = _land_slot_levels(valid, land_mons, slot_idxs)
                lines += _render_table(f"  {cart_name} inserted", slot_list, len(slot_list))
                lines.append("")

    surf_rate = enc.get("surf_rate", 0)
    surf_mons = enc.get("surf_encounters", [])
    if surf_rate > 0 and surf_mons:
        lines += _render_table("Surfing", surf_mons, len(surf_mons), surf_rate)
        lines.append("")

    for rod, key in (("Old Rod", "old_rod"), ("Good Rod", "good_rod"), ("Super Rod", "super_rod")):
        rod_rate = enc.get(f"{key}_rate", 0)
        rod_mons = enc.get(f"{key}_encounters", [])
        if rod_rate > 0 and rod_mons:
            lines += _render_table(f"Fishing — {rod}", rod_mons, len(rod_mons), rod_rate)
            lines.append("")

    return lines


def render_honey_tree_section() -> list[str]:
    """Return honey tree encounter list (same for all trees in Sinnoh)."""
    if not HONEY_TREE_FILE.exists():
        return []
    with open(HONEY_TREE_FILE, encoding="utf-8") as f:
        ht = json.load(f)
    SEP = "=" * 70
    lines = ["", SEP, "  Honey Tree Encounters  (any tree in Sinnoh)", SEP, ""]
    for tier in ("common", "uncommon", "rare"):
        mons = ht.get(tier, [])
        if mons:
            lines.append(f"  {tier.title()} slots:")
            for sp in set(mons):
                lines.append(f"    {_pretty_species(sp)}")
            lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Trainer loading
# ---------------------------------------------------------------------------

def _trainer_script_to_filename(script_key: str) -> str | None:
    """
    Convert trainer script key to JSON filename.
    "TRAINER_ACE_TRAINER_JOSE" → "ace_trainer_jose.json"
    """
    m = re.match(r"TRAINER_(.+)", script_key)
    if not m:
        return None
    name = m.group(1).lower()
    return f"{name}.json"


SCRIPTS_DIR = BASE_DIR / "pokeplatinum/res/field/scripts"
TEXT_DIR    = BASE_DIR / "pokeplatinum/res/text"

# ---------------------------------------------------------------------------
# NPC script + text resolution
# ---------------------------------------------------------------------------

_SCRIPT_TABLE_CACHE: dict = {}
_SCRIPT_BODY_CACHE:  dict = {}
_TEXT_CACHE:         dict = {}

# ---------------------------------------------------------------------------
# Common-script resolution (CallCommonScript / PokeMartCommonWithGreeting / …)
# ---------------------------------------------------------------------------

_PT_COMMON_TEXT_CACHE:   dict | None = None
_PT_COMMON_BLOCKS_CACHE: dict | None = None
_PT_COMMON_ENTRY_CACHE:  dict | None = None
_PT_MACRO_COMMON_CACHE:  dict | None = None


def _pt_common_text() -> dict[str, str]:
    """Text from common_strings.json keyed by id string."""
    global _PT_COMMON_TEXT_CACHE
    if _PT_COMMON_TEXT_CACHE is None:
        _PT_COMMON_TEXT_CACHE = {}
        p = TEXT_DIR / "common_strings.json"
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            for m in data.get("messages", []):
                val = "".join(m.get("en_US", [])).replace("\n", " ").replace("\r", "")
                _PT_COMMON_TEXT_CACHE[m["id"]] = val
    return _PT_COMMON_TEXT_CACHE


def _pt_common_entry_table() -> dict[int, str]:
    """ScriptEntry index → label, from scripts_common.s."""
    global _PT_COMMON_ENTRY_CACHE
    if _PT_COMMON_ENTRY_CACHE is None:
        _PT_COMMON_ENTRY_CACHE = {}
        p = SCRIPTS_DIR / "scripts_common.s"
        if p.exists():
            for m in re.finditer(r"ScriptEntry\s+(\w+)\s*@\s*(0x[0-9A-Fa-f]+)",
                                 p.read_text(encoding="utf-8")):
                _PT_COMMON_ENTRY_CACHE[int(m.group(2), 16)] = m.group(1)
    return _PT_COMMON_ENTRY_CACHE


def _pt_common_blocks() -> dict[str, list[str]]:
    """Label → stripped lines, from scripts_common.s."""
    global _PT_COMMON_BLOCKS_CACHE
    if _PT_COMMON_BLOCKS_CACHE is None:
        _PT_COMMON_BLOCKS_CACHE = {}
        p = SCRIPTS_DIR / "scripts_common.s"
        if p.exists():
            text = p.read_text(encoding="utf-8")
            for m in re.finditer(r"^(\w+):\n((?:(?!^\w+:).*\n)*)", text, re.M):
                lines = [l.strip() for l in m.group(2).splitlines() if l.strip()]
                _PT_COMMON_BLOCKS_CACHE.setdefault(m.group(1), lines)
    return _PT_COMMON_BLOCKS_CACHE


def _pt_macro_to_common() -> dict[str, int]:
    """Macro name → CallCommonScript id (one or two levels), from scrcmd.inc."""
    global _PT_MACRO_COMMON_CACHE
    if _PT_MACRO_COMMON_CACHE is not None:
        return _PT_MACRO_COMMON_CACHE
    _PT_MACRO_COMMON_CACHE = {}
    inc_path = BASE_DIR / "pokeplatinum/asm/macros/scrcmd.inc"
    if not inc_path.exists():
        return _PT_MACRO_COMMON_CACHE
    inc = inc_path.read_text(encoding="utf-8")
    # Collect all macro bodies
    macro_bodies: dict[str, list[str]] = {}
    for m in re.finditer(
            r"[ \t]*\.macro\s+(\w+)[^\n]*\n((?:(?![ \t]*\.(?:macro|endm)).*\n)*?)[ \t]*\.endm", inc):
        name = m.group(1)
        lines = [l.strip() for l in m.group(2).splitlines() if l.strip()]
        macro_bodies[name] = lines
    # First pass: macros with a direct CallCommonScript line
    for name, lines in macro_bodies.items():
        for line in lines:
            cm = re.match(r"CallCommonScript\s+(0x[0-9A-Fa-f]+)", line)
            if cm:
                _PT_MACRO_COMMON_CACHE[name] = int(cm.group(1), 16)
                break
    # Second pass: macros that call another already-resolved macro
    for name, lines in macro_bodies.items():
        if name in _PT_MACRO_COMMON_CACHE:
            continue
        for line in lines:
            tok = line.split()[0] if line else ""
            if tok in _PT_MACRO_COMMON_CACHE:
                _PT_MACRO_COMMON_CACHE[name] = _PT_MACRO_COMMON_CACHE[tok]
                break
    return _PT_MACRO_COMMON_CACHE


def _pt_resolve_common(common_id: int) -> str | None:
    """Return the opening text for a common script by its 0xNNN id."""
    label = _pt_common_entry_table().get(common_id)
    if not label:
        return None
    blocks = _pt_common_blocks()
    common_text = _pt_common_text()
    seen: set = set()
    queue = [label]
    var_state: dict[str, str] = {}
    while queue:
        lbl = queue.pop(0)
        if lbl in seen:
            continue
        seen.add(lbl)
        for line in blocks.get(lbl, []):
            # SetVar VAR_X, TEXT_KEY — track for MessageVar
            sv = re.match(r"SetVar\s+(\w+)\s*,\s*(\w+)", line)
            if sv:
                var_state[sv.group(1)] = sv.group(2)
                continue
            # MessageVar VAR_X — look up tracked key in common_text
            mv = re.match(r"MessageVar\s+(\w+)", line)
            if mv:
                key = var_state.get(mv.group(1))
                if key:
                    text = common_text.get(key, "")
                    if text:
                        return text
                continue
            # Direct Message / NPCMessage
            for pfx in ("Message ", "NPCMessage "):
                if line.startswith(pfx):
                    key = line[len(pfx):].strip()
                    text = common_text.get(key, "")
                    if text:
                        return text
                    break
            # Follow control-flow targets within common script
            jm = re.search(r"\b(CommonScript_\w+)\s*$", line)
            if jm and jm.group(1) not in seen:
                queue.append(jm.group(1))
    return None


# Script commands that carry NPC/sign dialogue
_DIALOGUE_CMDS = {
    "Message",
    "NPCMessage", "NPCMessageDecide", "NPCMessageNotice",
    "ShowMapSign", "ShowLandmarkSign", "ShowArrowSign",
    "YesNoMessage", "TrainerMessage",
}


def _map_name_from_constant(constant: str) -> str:
    """MAP_HEADER_JUBILIFE_CITY -> jubilife_city"""
    return constant.replace("MAP_HEADER_", "").lower()


def _load_script_table(map_name: str) -> list[str]:
    """Return ordered list of script labels from ScriptEntry table."""
    if map_name in _SCRIPT_TABLE_CACHE:
        return _SCRIPT_TABLE_CACHE[map_name]
    path = SCRIPTS_DIR / f"scripts_{map_name.lower()}.s"
    labels: list[str] = []
    if path.exists():
        entry_re = re.compile(r"^\s+ScriptEntry\s+(\S+)")
        for line in path.read_text(encoding="utf-8").splitlines():
            m = entry_re.match(line)
            if m:
                labels.append(m.group(1))
    _SCRIPT_TABLE_CACHE[map_name] = labels
    return labels


def _load_script_body(map_name: str) -> dict[str, list[str]]:
    """Return {label: [body lines]} for all labels in the script file."""
    if map_name in _SCRIPT_BODY_CACHE:
        return _SCRIPT_BODY_CACHE[map_name]
    path = SCRIPTS_DIR / f"scripts_{map_name.lower()}.s"
    result: dict[str, list[str]] = {}
    if path.exists():
        current: str | None = None
        label_re = re.compile(r"^(\w+):$")
        for line in path.read_text(encoding="utf-8").splitlines():
            lm = label_re.match(line)
            if lm:
                current = lm.group(1)
                result[current] = []
            elif current is not None:
                result[current].append(line.strip())
    _SCRIPT_BODY_CACHE[map_name] = result
    return result


def _load_text(map_name: str) -> dict[str, str]:
    """Return {text_id: joined_text} from res/text/{map_name}.json."""
    if map_name in _TEXT_CACHE:
        return _TEXT_CACHE[map_name]
    path = TEXT_DIR / f"{map_name.lower()}.json"
    result: dict[str, str] = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for msg in data.get("messages", []):
            tid  = msg.get("id", "")
            text = msg.get("en_US", "")
            if isinstance(text, list):
                text = " ".join(t.strip() for t in text if t.strip())
            result[tid] = text
    _TEXT_CACHE[map_name] = result
    return result


_PT_SHOW_YESNO = re.compile(r"^ShowYesNoMenu\b")
_PT_GOTOIFEQ_YES = re.compile(r"^GoToIfEq\s+VAR_RESULT\s*,\s*MENU_YES\s*,\s*(\w+)")
_PT_GOTOIFEQ_NO  = re.compile(r"^GoToIfEq\s+VAR_RESULT\s*,\s*MENU_NO\s*,\s*(\w+)")
_PT_GOTO = re.compile(r"^GoTo\s+(\w+)")

_PT_GIVE_VAR_RE  = re.compile(r"^SetVar\s+VAR_0x8004,\s*(ITEM_\w+)")
_PT_QTY_VAR_RE   = re.compile(r"^SetVar\s+VAR_0x8005,\s*(\d+)")
_PT_GIVE_CMD_RE  = re.compile(r"^Common_GiveItemQuantity(?:NoLineFeed)?$")
_PT_FLAG_GATE_RE = re.compile(r"^(GoToIfSet|GoToIfUnset|GoToIfBadgeAcquired)\s+(\w+),\s*(\w+)")


def _pt_first_text_from_label(label: str, texts: dict, script_body: dict) -> str:
    """Return the first dialogue text found in a Platinum script label."""
    body = script_body.get(label, [])
    for line in body:
        for cmd in _DIALOGUE_CMDS:
            if line.startswith(cmd):
                parts = line.split(None, 1)
                if len(parts) >= 2:
                    t = texts.get(parts[1].strip().rstrip(","), "")
                    if t:
                        return t
                break
        if line.startswith("Call "):
            sub = line.split(None, 1)[-1].strip().rstrip(",")
            sub_body = script_body.get(sub, [])
            for sl in sub_body:
                for cmd in _DIALOGUE_CMDS:
                    if sl.startswith(cmd):
                        parts = sl.split(None, 1)
                        if len(parts) >= 2:
                            t = texts.get(parts[1].strip().rstrip(","), "")
                            if t:
                                return t
                        break
    return ""


def _first_message_in_body(body: list[str], texts: dict,
                            script_body: dict) -> str | dict | None:
    """Scan a list of script lines for the first dialogue text, following one Call.

    Returns a {"prompt": ..., "yes": ..., "no": ...} dict when a YESNO menu is
    found, a plain str for ordinary dialogue, or None.
    """
    first_text: str | None = None   # the first message found (returned for plain dialogue)
    prompt_text: str | None = None  # most-recent message before ShowYesNoMenu (YESNO candidate)
    for i, line in enumerate(body):
        # Follow Call to a sub-label (one level only)
        if line.startswith("Call "):
            parts = line.split(None, 1)
            if len(parts) == 2:
                sub_label = parts[1].strip().rstrip(",")
                sub_body  = script_body.get(sub_label, [])
                result = _first_message_in_body(sub_body, texts, {})
                if result:
                    return result
            continue

        # CallCommonScript 0xNNN or a macro that calls one.
        # Tokens that are also in _DIALOGUE_CMDS (e.g. ShowLandmarkSign) carry
        # their text key as a direct argument and must reach the dialogue check.
        first_tok = line.split()[0] if line else ""
        if first_tok == "CallCommonScript":
            parts = line.split()
            if len(parts) >= 2:
                try:
                    cid = int(parts[1], 16)
                except ValueError:
                    cid = None
                if cid is not None:
                    ct = _pt_resolve_common(cid)
                    if ct:
                        if first_text is None:
                            first_text = ct
                        prompt_text = ct
            continue
        macro_table = _pt_macro_to_common()
        if first_tok in macro_table and first_tok not in _DIALOGUE_CMDS:
            ct = _pt_resolve_common(macro_table[first_tok])
            if ct:
                if first_text is None:
                    first_text = ct
                prompt_text = ct
            continue

        # YESNO marker — preceding Message line was the prompt
        if _PT_SHOW_YESNO.match(line) and prompt_text is not None:
            yes_label = no_label = None
            for later in body[i + 1:]:
                m = _PT_GOTOIFEQ_YES.match(later)
                if m and yes_label is None:
                    yes_label = m.group(1)
                m = _PT_GOTOIFEQ_NO.match(later)
                if m and no_label is None:
                    no_label = m.group(1)
                # Unconditional GoTo after YES jump → NO target
                m = _PT_GOTO.match(later)
                if m and yes_label is not None and no_label is None:
                    no_label = m.group(1)
                    break
            yes_text = _pt_first_text_from_label(yes_label, texts, script_body) if yes_label else ""
            no_text  = _pt_first_text_from_label(no_label,  texts, script_body) if no_label  else ""
            return {"prompt": prompt_text, "yes": yes_text, "no": no_text}

        for cmd in _DIALOGUE_CMDS:
            if line.startswith(cmd):
                parts = line.split(None, 1)
                if len(parts) < 2:
                    prompt_text = None
                    break
                text_key = parts[1].strip().rstrip(",")
                text = texts.get(text_key)
                if text:
                    if first_text is None:
                        first_text = text
                    # Track as YESNO candidate — may be followed by ShowYesNoMenu
                    prompt_text = text
                break
        else:
            # Non-dialogue line: reset the YESNO candidate but keep first_text
            if not line.startswith(("ShowYesNoMenu", "CloseMessage", "WaitButton",
                                    "WaitMessage", "WaitAction", "ReleaseAll",
                                    "GoToIfEq", "GoTo ", "FacePlayer", "LockAll",
                                    "GetPlayerDir", "CallIfEq", "PlaySE",
                                    "End", "Return", "ExitScript")):
                prompt_text = None

    return first_text


def _resolve_npc_dialogue(map_name: str, script_idx: int) -> str | dict | None:
    """
    Given a map name and script table index (integer), find the NPC's
    dialogue text, or None if it can't be resolved.

    Returns a plain str, a {"prompt": ..., "yes": ..., "no": ...} dict for
    YESNO scripts, or None.
    """
    table = _load_script_table(map_name)
    idx = script_idx - 1  # script integers are 1-based
    if idx < 0 or idx >= len(table):
        return None
    label       = table[idx]
    script_body = _load_script_body(map_name)
    body        = script_body.get(label, [])
    texts       = _load_text(map_name)
    return _first_message_in_body(body, texts, script_body)


def _pt_msg_from_line(line: str, texts: dict) -> str | None:
    """Extract English text from a Platinum dialogue command line, or None."""
    for cmd in _DIALOGUE_CMDS:
        if line.startswith(cmd + " ") or line == cmd:
            parts = line.split(None, 1)
            if len(parts) >= 2:
                return texts.get(parts[1].strip().rstrip(",")) or None
            break
    return None


def _pt_msgs_from_lines(lines: list[str], texts: dict, script_body: dict,
                         follow_call: bool = True) -> list[str]:
    """Collect all dialogue texts from a list of Platinum script lines."""
    out = []
    for line in lines:
        t = _pt_msg_from_line(line, texts)
        if t:
            out.append(t)
        elif follow_call and line.startswith("Call "):
            sub_label = line.split(None, 1)[-1].strip().rstrip(",")
            for sl in script_body.get(sub_label, []):
                t2 = _pt_msg_from_line(sl, texts)
                if t2:
                    out.append(t2)
    return out


def _pt_collect_npc_script(map_name: str, script_idx: int) -> dict | None:
    """
    Analyse a Platinum NPC script and return structured dialogue + item-give data.
    Returns {"states": [...]} with the same shape as _hg_collect_npc_script, or None.
    """
    table = _load_script_table(map_name)
    idx = script_idx - 1
    if idx < 0 or idx >= len(table):
        return None
    label       = table[idx]
    script_body = _load_script_body(map_name)
    texts       = _load_text(map_name)
    root        = script_body.get(label, [])

    # Phase 1: flag gates before first content (message / give opcode / yes-no)
    gates: list[tuple[str, str, str]] = []
    fallthrough_start = len(root)
    for i, line in enumerate(root):
        m = _PT_FLAG_GATE_RE.match(line)
        if m:
            opcode   = m.group(1)
            polarity = "post" if opcode in ("GoToIfSet", "GoToIfBadgeAcquired") else "pre"
            gates.append((m.group(2), m.group(3), polarity))
            continue
        if (_pt_msg_from_line(line, texts) is not None
                or _PT_SHOW_YESNO.match(line)
                or _PT_GIVE_VAR_RE.match(line)
                or _PT_GIVE_CMD_RE.match(line)):
            fallthrough_start = i
            break

    fallthrough = root[fallthrough_start:]

    # Phase 2: locate the give opcode or yes/no gate in the fallthrough
    item_const: str | None = None
    item_qty   = 1
    give_idx:   int | None = None
    yesno_idx:  int | None = None
    pending_item: str | None = None
    pending_qty = 1

    for i, line in enumerate(fallthrough):
        m = _PT_GIVE_VAR_RE.match(line)
        if m:
            pending_item = m.group(1)
            pending_qty  = 1
            continue
        m = _PT_QTY_VAR_RE.match(line)
        if m:
            pending_qty = int(m.group(1))
            continue
        if _PT_GIVE_CMD_RE.match(line) and pending_item:
            item_const, item_qty, give_idx = pending_item, pending_qty, i
            break
        if _PT_SHOW_YESNO.match(line):
            yesno_idx = i
            break
        if _PT_GOTO.match(line):
            pending_item = None

    # Phase 3: collect messages and build the default state
    declined_msg: str | None = None
    yesno_give = False

    if give_idx is not None:
        pre_msgs  = _pt_msgs_from_lines(fallthrough[:give_idx], texts, script_body)
        post_msgs = _pt_msgs_from_lines(fallthrough[give_idx + 1:], texts, script_body)
        gives     = [{"item": item_const, "qty": item_qty, "post_msgs": post_msgs}]

    elif yesno_idx is not None:
        yesno_give = True
        pre_msgs   = _pt_msgs_from_lines(fallthrough[:yesno_idx], texts, script_body)
        yes_label = no_label = None
        for later in fallthrough[yesno_idx + 1:]:
            yes_m  = _PT_GOTOIFEQ_YES.match(later)
            no_m   = _PT_GOTOIFEQ_NO.match(later)
            goto_m = _PT_GOTO.match(later)
            if yes_m and yes_label is None:
                yes_label = yes_m.group(1)
            if no_m and no_label is None:
                no_label = no_m.group(1)
            if goto_m and yes_label and not no_label:
                no_label = goto_m.group(1)
                break
        # Scan yes-branch for give
        yes_body   = script_body.get(yes_label or "", [])
        gives      = []
        p_item: str | None = None
        p_qty  = 1
        y_give_idx: int | None = None
        for i, line in enumerate(yes_body):
            m = _PT_GIVE_VAR_RE.match(line)
            if m:
                p_item = m.group(1); p_qty = 1; continue
            m = _PT_QTY_VAR_RE.match(line)
            if m:
                p_qty = int(m.group(1)); continue
            if _PT_GIVE_CMD_RE.match(line) and p_item:
                y_give_idx = i; break
        if y_give_idx is not None:
            yes_post = _pt_msgs_from_lines(yes_body[y_give_idx + 1:], texts, script_body,
                                           follow_call=False)
            gives = [{"item": p_item, "qty": p_qty, "post_msgs": yes_post}]
        # Decline text from no-branch
        no_body = script_body.get(no_label or "", [])
        no_texts = _pt_msgs_from_lines(no_body, texts, script_body, follow_call=False)
        declined_msg = no_texts[0] if no_texts else None

    else:
        pre_msgs = _pt_msgs_from_lines(fallthrough, texts, script_body)
        gives    = []

    default_state: dict = {
        "flag": None, "polarity": "post",
        "pre_msgs": pre_msgs, "gives": gives,
    }
    if yesno_give:
        default_state["yesno"]       = True
        default_state["declined_msg"] = declined_msg

    # Phase 4: gate states (script order = latest-first → reversed = earliest-first)
    gate_states: list[dict] = []
    for flag_name, target_label, polarity in reversed(gates):
        target_msgs = _pt_msgs_from_lines(
            script_body.get(target_label, []), texts, script_body
        )[:1]
        gate_states.append({
            "flag": flag_name, "polarity": polarity,
            "pre_msgs": target_msgs, "gives": [],
        })

    return {"states": [default_state] + gate_states}


# ---------------------------------------------------------------------------
# Prize money
# ---------------------------------------------------------------------------

_PRIZE_MUL_CACHE: dict | None = None
_PRIZE_MUL_PATH = BASE_DIR / "pokeplatinum/include/data/trainer_class_prize_mul.h"


def _get_prize_mul() -> dict[str, int]:
    global _PRIZE_MUL_CACHE
    if _PRIZE_MUL_CACHE is not None:
        return _PRIZE_MUL_CACHE
    result: dict[str, int] = {}
    if _PRIZE_MUL_PATH.exists():
        entry_re = re.compile(r'\[(\w+)\]\s*=\s*(\d+)')
        for line in _PRIZE_MUL_PATH.read_text(encoding="utf-8").splitlines():
            m = entry_re.search(line)
            if m:
                result[m.group(1)] = int(m.group(2))
    _PRIZE_MUL_CACHE = result
    return result


def _calc_prize(trainer_class: str, party: list) -> int | None:
    muls = _get_prize_mul()
    mul = muls.get(trainer_class)
    if mul is None or not party:
        return None
    max_lvl = max((p.get("lvl", 0) for p in party), default=0)
    if max_lvl == 0:
        return None
    return mul * max_lvl * 2


# ---------------------------------------------------------------------------
# VS Seeker rematch data
# ---------------------------------------------------------------------------

_VS_SEEKER_C     = BASE_DIR / "pokeplatinum/src/overlay005/vs_seeker.c"
_VS_SEEKER_CACHE: dict | None = None


def _parse_vs_seeker_rematches() -> dict[str, list[str]]:
    """Return {initial_trainer_const: [rematch_const, ...]}.
    Empty list  = NoUniqueRematches (same party on every rematch).
    Not in dict = trainer has no VS Seeker rematch."""
    global _VS_SEEKER_CACHE
    if _VS_SEEKER_CACHE is not None:
        return _VS_SEEKER_CACHE

    result: dict[str, list[str]] = {}
    if not _VS_SEEKER_C.exists():
        _VS_SEEKER_CACHE = result
        return result

    text = _VS_SEEKER_C.read_text(encoding="utf-8")

    array_start = text.find("gVsSeekerRematchData[]")
    if array_start == -1:
        _VS_SEEKER_CACHE = result
        return result

    open_brace = text.find("{", array_start)
    if open_brace == -1:
        _VS_SEEKER_CACHE = result
        return result

    # Extract the outer array body
    depth = 0
    array_text = ""
    for i in range(open_brace, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
            if depth == 1:
                continue
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        if depth >= 1:
            array_text += ch

    # NoUniqueRematches(X) → same party every time → empty list
    for m in re.finditer(r"NoUniqueRematches\((\w+)\)", array_text):
        result[m.group(1)] = []

    # Explicit { X, R1, _, R2, 0, ... } entries
    _SKIP = {"_", "VS_SEEKER_REMATCH_DATA_NONE"}
    _END  = {"0", "VS_SEEKER_REMATCH_DATA_END"}
    for m in re.finditer(r"\{([^{}]+)\}", array_text):
        tokens = [t.strip() for t in m.group(1).split(",") if t.strip()]
        if not tokens or not tokens[0].startswith("TRAINER_"):
            continue
        initial = tokens[0]
        rematches: list[str] = []
        for tok in tokens[1:]:
            if tok in _SKIP:
                continue
            if tok in _END:
                break
            if tok.startswith("TRAINER_"):
                rematches.append(tok)
        result[initial] = rematches

    _VS_SEEKER_CACHE = result
    return result


# ---------------------------------------------------------------------------
# Trainer loading
# ---------------------------------------------------------------------------

_TRAINER_CACHE: dict = {}


def _load_trainer(script_key: str) -> dict | None:
    if script_key in _TRAINER_CACHE:
        return _TRAINER_CACHE[script_key]
    fname = _trainer_script_to_filename(script_key)
    if not fname:
        _TRAINER_CACHE[script_key] = None
        return None
    path = TRAINER_DIR / fname
    if not path.exists():
        _TRAINER_CACHE[script_key] = None
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    _TRAINER_CACHE[script_key] = data
    return data


# ---------------------------------------------------------------------------
# Event loading (NPC / trainer / item / sign)
# ---------------------------------------------------------------------------

_SIGN_GFX = ("SIGNPOST", "SIGNBOARD")

_FIELD_OBSTACLE_GFX: dict[str, tuple[str, str]] = {
    "OBJ_EVENT_GFX_CUT_TREE":         ("♣", "Cut tree"),
    "OBJ_EVENT_GFX_ROCK_SMASH":       ("⊗", "Rock Smash rock"),
    "OBJ_EVENT_GFX_STRENGTH_BOULDER": ("▣", "Strength boulder"),
    "OBJ_EVENT_GFX_ICE_ROCK":         ("◈", "Ice Rock (Glaceon)"),
    "OBJ_EVENT_GFX_MOSS_ROCK":        ("✿", "Moss Rock (Leafeon)"),
    "OBJ_EVENT_GFX_BOLLARD":          ("█", "Bollard"),
    "OBJ_EVENT_GFX_SNOWBALL":         ("●", "Snowball"),
}

# Purely decorative object_events — skip entirely regardless of script.
_PT_SKIP_GFX: frozenset[str] = frozenset({
    "OBJ_EVENT_GFX_STARLY",                       # decorative event Pokémon
    "OBJ_EVENT_GFX_CROAGUNK",                     # Crasher Wake companion (scene-only)
    "OBJ_EVENT_GFX_MAGIKARP",                     # Lake Valor drained-lake decoration
    "OBJ_EVENT_GFX_ELITE_FOUR_ROOM_DOOR",         # redundant: warp_event exists at same coords
    "OBJ_EVENT_GFX_WALL_BLOCKING_ROTOMS_ROOM",    # barrier prop in Eterna HQ
    "OBJ_EVENT_GFX_VENT",                         # decorative vent (65 instances)
    "OBJ_EVENT_GFX_CAVE_PAINTING",                # Oreburgh Mine decorative artwork
    "OBJ_EVENT_GFX_CAVE_PAINTING_SHARDS_LEFT",    # same, broken variant
    "OBJ_EVENT_GFX_CAVE_PAINTING_SHARDS_RIGHT",   # same, broken variant
    "OBJ_EVENT_GFX_GALACTIC_HQ_DOOR",             # door prop, same logic as ELITE_FOUR_ROOM_DOOR
    "OBJ_EVENT_GFX_TEALA",                        # Union Room / Wi-Fi Club attendant (binary NARC scripts, unreadable)
})

# Story/cutscene background characters — skip script=0 instances.
# Gate behind --include-background-npcs when that flag is wired up.
_PT_BACKGROUND_GFX: frozenset[str] = frozenset({
    "OBJ_EVENT_GFX_BARRY", "OBJ_EVENT_GFX_CYNTHIA", "OBJ_EVENT_GFX_CYRUS",
    "OBJ_EVENT_GFX_MARS", "OBJ_EVENT_GFX_JUPITER", "OBJ_EVENT_GFX_LOOKER",
    "OBJ_EVENT_GFX_PROF_ROWAN", "OBJ_EVENT_GFX_CHARON", "OBJ_EVENT_GFX_CRASHER_WAKE",
    "OBJ_EVENT_GFX_GRUNTS_GROUP_OF_3", "OBJ_EVENT_GFX_GRUNTS_GROUP_OF_4",
})

# GFX-based static encounter detection for objects whose scripts do not use
# StartWildBattle (Rotom form appliances use form-change scripts instead).
_PT_STATIC_ENC_GFX: dict[str, tuple[str, int]] = {
    "OBJ_EVENT_GFX_ROTOM_HEAT":  ("SPECIES_ROTOM", 20),
    "OBJ_EVENT_GFX_ROTOM_FAN":   ("SPECIES_ROTOM", 20),
    "OBJ_EVENT_GFX_ROTOM_FROST": ("SPECIES_ROTOM", 20),
    "OBJ_EVENT_GFX_ROTOM_WASH":  ("SPECIES_ROTOM", 20),
    "OBJ_EVENT_GFX_ROTOM_MOW":   ("SPECIES_ROTOM", 20),
}

# ---------------------------------------------------------------------------
# Honey tree prop extraction from map_data_NNN.bin binaries
# ---------------------------------------------------------------------------

_HONEY_TREE_MODEL_ID = 26      # index 26 in prop_models/meson.build
_TILE_SIZE_FX32      = 16 * 4096   # MAP_OBJECT_TILE_SIZE = 65536
_MAP_BLOCK_TILES     = 32          # MAP_TILES_COUNT_X / Z


def _parse_bin_props(raw: bytes) -> list[dict]:
    """Extract model_id + position from the props section of a map_data_NNN.bin."""
    if len(raw) < 16:
        return []
    # Header field order (from land_data.c LandDataHeader_Load):
    #   [0] terrainAttributesSize  [1] mapPropsSize  [2] mapModelSize  [3] bdhcSize
    terrain_size, props_size = struct.unpack_from("<ii", raw, 0)
    if terrain_size != 2048 or props_size <= 0:
        return []
    props_offset = 16 + terrain_size
    if props_offset + props_size > len(raw):
        return []
    entries = []
    # MapPropFile: int modelID, VecFx32 pos(x,y,z), VecFx32 rot, VecFx32 scale, int[2] dummy
    # sizeof(MapPropFile) = 4 + 12 + 12 + 12 + 8 = 48 bytes
    for i in range(props_size // 48):
        off = props_offset + i * 48
        model_id, px, py, pz = struct.unpack_from("<4i", raw, off)
        entries.append({"model_id": model_id, "px": px, "py": py, "pz": pz})
    return entries


def _get_honey_tree_positions(map_entry: dict) -> list[tuple[int, int]]:
    """
    Return list of (world_tile_x, world_tile_z) for honey trees on this map.
    Returns empty list if map is not in the honey tree list or has no tree prop.
    """
    constant = map_entry.get("constant", "")
    matrix_id_str = map_entry.get("mapMatrixID", "")
    if not constant or not matrix_id_str:
        return []
    matrix_path = MATRIX_DIR / f"{matrix_id_str}.json"
    if not matrix_path.exists():
        return []
    with open(matrix_path, encoding="utf-8") as f:
        matrix_json = json.load(f)
    headers  = matrix_json.get("headers", [])
    maps_ids = matrix_json.get("maps", [])

    positions: list[tuple[int, int]] = []
    for r, row in enumerate(headers):
        for c, cell in enumerate(row):
            if cell != constant:
                continue
            map_cell = maps_ids[r][c] if r < len(maps_ids) and c < len(maps_ids[r]) else None
            m = re.match(r"MAP_(\d+)$", map_cell or "")
            if not m:
                continue
            num = int(m.group(1))
            path = MAP_DATA_DIR / f"map_data_{num:03d}.bin"
            if not path.exists():
                continue
            props = _parse_bin_props(path.read_bytes())
            for p in props:
                if p["model_id"] != _HONEY_TREE_MODEL_ID:
                    continue
                rendering_x = (16 + c * _MAP_BLOCK_TILES) * _TILE_SIZE_FX32
                rendering_z = (16 + r * _MAP_BLOCK_TILES) * _TILE_SIZE_FX32
                wx = round((p["px"] + rendering_x) / _TILE_SIZE_FX32)
                wz = round((p["pz"] + rendering_z) / _TILE_SIZE_FX32)
                positions.append((wx, wz))
    return positions

# GFX substring → keyword that should appear in TRAINER_* constant for that NPC type.
# Ordered: check longest/most specific substrings first.
_EVENT_TRAINER_GFX = [
    ("COMMANDER_MARS",    "MARS"),
    ("COMMANDER_JUPITER", "JUPITER"),
    ("COMMANDER_SATURN",  "SATURN"),
    ("BOSS_CYRUS",        "CYRUS"),
    ("ADMIN_CHARON",      "CHARON"),
    ("BARRY",             "RIVAL"),
    ("GRUNT_M",           "GALACTIC_GRUNT"),
    ("GRUNT_F",           "GALACTIC_GRUNT"),
    ("MAID",              "MAID"),
]

_SCRIPT_TRAINER_CACHE: dict[str, list[str]] = {}


def _find_trainer_constants_in_script(map_name: str) -> list[str]:
    """Return all TRAINER_* constants referenced in StartTrainerBattle/StartTagBattle."""
    if map_name in _SCRIPT_TRAINER_CACHE:
        return _SCRIPT_TRAINER_CACHE[map_name]
    path = SCRIPTS_DIR / f"scripts_{map_name}.s"
    constants: list[str] = []
    if path.exists():
        battle_re = re.compile(r'\b(?:StartTrainerBattle|StartTagBattle)\b')
        token_re  = re.compile(r'\bTRAINER_\w+')
        for line in path.read_text(encoding="utf-8").splitlines():
            if battle_re.search(line):
                for m in token_re.finditer(line):
                    constants.append(m.group(0))
    _SCRIPT_TRAINER_CACHE[map_name] = constants
    return constants


def _gfx_trainer_keyword(gfx: str) -> str | None:
    for substr, keyword in _EVENT_TRAINER_GFX:
        if substr in gfx:
            return keyword
    return None


def _is_sign(gfx: str) -> bool:
    return any(k in gfx for k in _SIGN_GFX)


def _parse_party(td: dict) -> list[dict]:
    return [
        {
            "species":  _pretty_species(p.get("species", "?")),
            "lvl":      p.get("level", "?"),
            "moves":    [
                move_display(mv)
                for mv in (p.get("moves") or [])
                if mv and mv != "MOVE_NONE"
            ],
            "item":     (p.get("item") or "").replace("ITEM_", "").replace("_", " ").title() or None,
            "iv_scale": p.get("iv_scale", 0),
        }
        for p in td.get("party", [])
    ]


def _build_trainer_entry(x: int, z: int, td: dict, is_event: bool,
                         note: str = "", const: str = "") -> dict:
    messages: dict[str, str] = {}
    for msg in td.get("messages", []):
        mtype = msg.get("type", "")
        text  = msg.get("en_US", "")
        if isinstance(text, list):
            text = " ".join(t.strip() for t in text if t.strip())
        messages[mtype] = text
    party = _parse_party(td)
    trainer_class = td.get("class", "")

    # VS Seeker rematches
    seeker_map = _parse_vs_seeker_rematches()
    vs_rematches: list[dict | None] = []
    if const in seeker_map:
        unique_consts = seeker_map[const]
        if not unique_consts:
            vs_rematches = [None]  # NoUniqueRematches: same party
        else:
            for rc in unique_consts:
                rtd = _load_trainer(rc)
                if rtd:
                    vs_rematches.append({
                        "const": rc,
                        "party": _parse_party(rtd),
                        "prize": _calc_prize(rtd.get("class", trainer_class), _parse_party(rtd)),
                    })

    return {
        "x": x, "z": z,
        "trainer_const": const,
        "name":        td.get("name", "?"),
        "class":       trainer_class,
        "double":      td.get("double_battle", False),
        "ai_flags":    [f.replace("AI_FLAG_", "") for f in (td.get("ai_flags") or [])],
        "items":       [i for i in (td.get("items") or []) if i],
        "messages":    messages,
        "is_event":    is_event,
        "note":        note,
        "prize":       _calc_prize(trainer_class, party),
        "party":       party,
        "vs_rematches": vs_rematches,
    }


_MATRICES_DIR = BASE_DIR / "pokeplatinum/res/field/matrices"
_DIR_OFFSETS   = [("N", -1, 0), ("S", 1, 0), ("W", 0, -1), ("E", 0, 1)]


def _get_connections(map_entry: dict) -> list[dict]:
    """Return list of {direction, dest, label} for maps adjacent in the matrix."""
    matrix_id_str = map_entry.get("mapMatrixID", "")
    matrix_path   = _MATRICES_DIR / f"{matrix_id_str}.json"
    if not matrix_path.exists():
        return []
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    headers = matrix.get("headers", [])
    constant = map_entry.get("constant", "")

    # Find every cell occupied by this map
    own_cells: set[tuple[int, int]] = set()
    for r, row in enumerate(headers):
        for c, val in enumerate(row):
            if val == constant:
                own_cells.add((r, c))

    # Collect adjacent cells that belong to a different map
    seen_dest: set[str] = set()
    connections: list[dict] = []
    for (r, c) in own_cells:
        for direction, dr, dc in _DIR_OFFSETS:
            nr, nc = r + dr, c + dc
            if not (0 <= nr < len(headers) and 0 <= nc < len(headers[nr])):
                continue
            dest = headers[nr][nc]
            if dest == constant or dest == "MAP_HEADER_EVERYWHERE":
                continue
            key = f"{direction}:{dest}"
            if key in seen_dest:
                continue
            seen_dest.add(key)
            label = dest.replace("MAP_HEADER_", "").replace("_", " ").title()
            connections.append({"direction": direction, "dest": dest, "label": label})

    connections.sort(key=lambda c: c["direction"])
    return connections


def _item_name(constant: str) -> str:
    """ITEM_POKE_BALL → 'Poke Ball'"""
    return constant.removeprefix("ITEM_").replace("_", " ").title()


def _species_name(constant: str) -> str:
    """SPECIES_BULBASAUR → 'Bulbasaur'"""
    return constant.removeprefix("SPECIES_").replace("_", " ").title()


def _move_display(constant: str) -> str:
    return move_display(constant)


_PT_MART_TABLE_CACHE: dict | None = None


def _pt_mart_table() -> dict[str, list[str]]:
    """Parse include/data/mart_items.h → {array_name: [item_names]}"""
    global _PT_MART_TABLE_CACHE
    if _PT_MART_TABLE_CACHE is not None:
        return _PT_MART_TABLE_CACHE
    path = BASE_DIR / "pokeplatinum/include/data/mart_items.h"
    result: dict[str, list[str]] = {}
    if not path.exists():
        _PT_MART_TABLE_CACHE = result
        return result
    text = path.read_text(encoding="utf-8")
    # Match: const u16 NAME[] = { ITEM_X, ... SHOP_ITEM_END };
    for m in re.finditer(
        r"const u16 (\w+)\[\]\s*=\s*\{([^}]+)\}", text, re.S
    ):
        arr_name = m.group(1)
        items = [
            _item_name(tok.strip())
            for tok in m.group(2).split(",")
            if tok.strip().startswith("ITEM_")
        ]
        if items:
            result[arr_name] = items
    # Also build PokeMartSpecialties pointer table: {MART_SPECIALTIES_ID_X: array_name}
    result["_id_map"] = {}   # type: ignore[assignment]
    for m in re.finditer(
        r"\[(MART_SPECIALTIES_ID_\w+)\]\s*=\s*(\w+)", text
    ):
        result["_id_map"][m.group(1)] = m.group(2)   # type: ignore[index]
    _PT_MART_TABLE_CACHE = result
    return result


_PT_TUTOR_TABLE_CACHE: list | None = None


def _pt_tutor_table() -> list[dict]:
    """Parse res/pokemon/move_tutors.json → list of tutor records."""
    global _PT_TUTOR_TABLE_CACHE
    if _PT_TUTOR_TABLE_CACHE is not None:
        return _PT_TUTOR_TABLE_CACHE
    path = BASE_DIR / "pokeplatinum/res/pokemon/move_tutors.json"
    result: list[dict] = []
    if path.exists():
        with open(path, encoding="utf-8") as f:
            result = json.load(f)
    _PT_TUTOR_TABLE_CACHE = result
    return result


_PT_FOSSIL_PAIRS_CACHE: list | None = None


def _pt_fossil_pairs() -> list[tuple[str, str]]:
    """Parse src/scrcmd_fossil.c → [(fossil_item, pokemon_name)]"""
    global _PT_FOSSIL_PAIRS_CACHE
    if _PT_FOSSIL_PAIRS_CACHE is not None:
        return _PT_FOSSIL_PAIRS_CACHE
    path = BASE_DIR / "pokeplatinum/src/scrcmd_fossil.c"
    pairs: list[tuple[str, str]] = []
    if path.exists():
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(
            r"\{\s*\.item\s*=\s*(ITEM_\w+),\s*\.species\s*=\s*(SPECIES_\w+)", text
        ):
            pairs.append((_item_name(m.group(1)), _species_name(m.group(2))))
    _PT_FOSSIL_PAIRS_CACHE = pairs
    return pairs


_PT_TRADE_TABLE_CACHE: dict | None = None


def _pt_trade_table() -> dict[str, dict]:
    """Parse res/npc_trades/*.json → {NPC_TRADE_X: {gives, receives, nickname}}"""
    global _PT_TRADE_TABLE_CACHE
    if _PT_TRADE_TABLE_CACHE is not None:
        return _PT_TRADE_TABLE_CACHE
    trades_dir = BASE_DIR / "pokeplatinum/res/npc_trades"
    result: dict[str, dict] = {}
    if trades_dir.exists():
        for p in sorted(trades_dir.glob("*.json")):
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
                # File name: nick_species.json → key NPC_TRADE_NICK_SPECIES
                stem = p.stem.upper()
                key = f"NPC_TRADE_{stem}"
                result[key] = {
                    "gives":    _species_name(d.get("requestedSpecies", "")),
                    "receives": _species_name(d.get("species", "")),
                    "nickname": d.get("name", ""),
                }
            except Exception:
                pass
    _PT_TRADE_TABLE_CACHE = result
    return result


_PT_VILLA_CATALOG_CACHE: list | None = None


def _pt_villa_catalog() -> list[tuple[str, int]]:
    """Parse scripts_villa.s → [(furniture_name, price)] in menu order.

    Names from villa_furniture_names.json; prices from Villa_CheckMoney_* labels.
    The Table costs 0 (given free with the villa purchase).
    """
    global _PT_VILLA_CATALOG_CACHE
    if _PT_VILLA_CATALOG_CACHE is not None:
        return _PT_VILLA_CATALOG_CACHE
    names_path   = BASE_DIR / "pokeplatinum/res/text/villa_furniture_names.json"
    scripts_path = BASE_DIR / "pokeplatinum/res/field/scripts/scripts_villa.s"
    if not names_path.exists() or not scripts_path.exists():
        _PT_VILLA_CATALOG_CACHE = []
        return []
    with open(names_path, encoding="utf-8") as f:
        names = [m["en_US"] for m in json.load(f).get("messages", [])]
    text = scripts_path.read_text(encoding="utf-8")
    # Ordered furniture types from the BuyFurniture dispatch block
    call_entries = re.findall(
        r"CallIfEq\s+VAR_0x8002,\s+VILLA_FURNITURE_(\w+),\s+Villa_BuyFurniture_(\w+)",
        text,
    )
    # Prices from each BuyFurniture_NAME label's RemoveMoney line
    prices: dict[str, int] = {
        m.group(1): int(m.group(2))
        for m in re.finditer(
            r"Villa_BuyFurniture_(\w+):\s*\n(?:.*\n)*?\s*RemoveMoney\s+(\d+)", text
        )
    }
    result: list[tuple[str, int]] = []
    for i, (ftype, buy_label) in enumerate(call_entries):
        name = names[i] if i < len(names) else ftype.replace("_", " ").title()
        if "SILVER" in ftype:
            name += " (Silver)"
        result.append((name, prices.get(buy_label, 0)))
    _PT_VILLA_CATALOG_CACHE = result
    return result


_PT_FRONTIER_MART_CACHE: dict[str, list[str]] | None = None


def _pt_frontier_mart_items(mart_id_constant: str) -> list[str]:
    """Parse BattleFrontierRight/LeftExchangeServiceCorner[] from src/scrcmd.c."""
    global _PT_FRONTIER_MART_CACHE
    if _PT_FRONTIER_MART_CACHE is None:
        _PT_FRONTIER_MART_CACHE = {}
        scrcmd = BASE_DIR / "pokeplatinum/src/scrcmd.c"
        frontier_id_txt = BASE_DIR / "pokeplatinum/generated/mart_frontier_id.txt"
        if not scrcmd.exists():
            return []
        text = scrcmd.read_text(errors="replace")
        # Parse the two static arrays
        for arr_name in ("BattleFrontierRightExchangeServiceCorner",
                         "BattleFrontierLeftExchangeServiceCorner"):
            m = re.search(
                rf"static const u16 {arr_name}\[\]\s*=\s*\{{([^}}]+)}}", text, re.S
            )
            if m:
                items = re.findall(r"\b(ITEM_\w+)\b", m.group(1))
                _PT_FRONTIER_MART_CACHE[arr_name] = [_item_name(i) for i in items]
        # Map ID constants → array names using mart_frontier_id.txt order
        if frontier_id_txt.exists():
            id_names = [ln.strip() for ln in frontier_id_txt.read_text().splitlines() if ln.strip()]
            arrays = ["BattleFrontierRightExchangeServiceCorner",
                      "BattleFrontierLeftExchangeServiceCorner"]
            for i, id_name in enumerate(id_names):
                if i < len(arrays):
                    _PT_FRONTIER_MART_CACHE[id_name] = _PT_FRONTIER_MART_CACHE.get(arrays[i], [])
    return _PT_FRONTIER_MART_CACHE.get(mart_id_constant, [])


def _pt_exchange_pairs(map_name: str, script_idx: int, flags: set[str]) -> list[tuple[str, str]]:
    """Extract (given, obtained) exchange pairs for a Platinum NPC."""
    if not flags or not map_name:
        return []
    table = _load_script_table(map_name)
    idx = script_idx - 1
    if idx < 0 or idx >= len(table):
        return []
    label = table[idx]
    body_dict = _load_script_body(map_name)

    # BFS collecting all reachable lines via GoTo*/Call*
    seen_labels: set[str] = {label}
    queue_labels = [label]
    all_lines: list[str] = []
    while queue_labels:
        cur = queue_labels.pop(0)
        cur_lines = body_dict.get(cur, [])
        all_lines.extend(cur_lines)
        for line in cur_lines:
            m2 = re.match(r"\s*(?:GoTo\w*|Call\w*)\b.*?\b(\w+)\s*$", line)
            if m2:
                tgt = m2.group(1)
                if tgt not in seen_labels and tgt in body_dict:
                    seen_labels.add(tgt)
                    queue_labels.append(tgt)
    body = "\n".join(all_lines)

    if "move_reminder" in flags:
        return [("Heart Scale", "Any forgotten move")]

    pairs: list[tuple[str, str]] = []

    if "vendor" in flags:
        if re.search(r"GetFossilCount\b", body):
            return _pt_fossil_pairs()
        # Battle Frontier BP exchange
        frontier_m = re.search(r"PokeMartFrontier\s+(MART_FRONTIER_ID_\w+)", body)
        if frontier_m:
            for item in _pt_frontier_mart_items(frontier_m.group(1)):
                pairs.append(("BP", item))
            return pairs
        # Common mart
        if re.search(r"PokeMartCommon\b", body):
            mart = _pt_mart_table()
            for item in mart.get("PokeMartCommonItems", []):
                pairs.append(("Money", item))
        # Specialty mart
        spec_m = re.search(r"PokeMartSpecialties\w*\s+(MART_SPECIALTIES_ID_\w+)", body)
        if spec_m:
            mart_id = spec_m.group(1)
            mart = _pt_mart_table()
            arr_name = mart.get("_id_map", {}).get(mart_id)   # type: ignore[union-attr]
            if arr_name:
                for item in mart.get(arr_name, []):
                    pairs.append(("Money", item))

    if "move_tutor" in flags:
        loc_m = re.search(r"(TUTOR_LOCATION_\w+)", body)
        if loc_m:
            loc = loc_m.group(1)
            for rec in _pt_tutor_table():
                if rec.get("location") == loc:
                    move = _move_display(rec.get("move", ""))
                    costs = []
                    for color, shard in (
                        ("red", "Red Shard"),
                        ("blue", "Blue Shard"),
                        ("yellow", "Yellow Shard"),
                        ("green", "Green Shard"),
                    ):
                        n = rec.get(f"{color}Cost", 0)
                        if n:
                            costs.append(f"{n} {shard}")
                    cost_str = " + ".join(costs) if costs else "Shards"
                    pairs.append((cost_str, move))

    if "villa_catalog" in flags:
        for name, price in _pt_villa_catalog():
            price_str = "Free" if price == 0 else f"₱{price:,}"
            pairs.append((price_str, name))

    if "star_piece_exchange" in flags:
        # Find each RemoveItem STAR_PIECE,N + associated AddItem ITEM_*_SHARD lines
        remove_ms = list(re.finditer(r"\bRemoveItem\s+ITEM_STAR_PIECE,\s*(\d+)", body))
        shard_items = re.findall(r"\bAddItem\s+(ITEM_\w+_SHARD)\b", body)
        if remove_ms and shard_items:
            # Use the smallest quantity found; shards are always the full set
            qty = int(remove_ms[0].group(1))
            given = f"{qty}× Star Piece" if qty > 1 else "Star Piece"
            obtained = " + ".join(dict.fromkeys(_item_name(s) for s in shard_items))
            pairs.append((given, obtained))

    if "money_item_vendor" in flags:
        # Collect items from direct AddItem ITEM_X and SetVar VAR_0x8001/0x8004, ITEM_X
        raw_items: list[str] = []
        raw_items += re.findall(r"\bAddItem\s+(ITEM_\w+)", body)
        raw_items += re.findall(r"\bSetVar\s+VAR_0x8001,\s*(ITEM_\w+)", body)
        raw_items += re.findall(r"\bSetVar\s+VAR_0x8004,\s*(ITEM_\w+)", body)
        seen_items: dict[str, None] = dict.fromkeys(raw_items)
        # Collect all monetary amounts; use the minimum as the display price
        costs: list[int] = []
        costs += [int(m) for m in re.findall(r"\bCheckMoney\b\s+\w+,\s*(\d+)", body)]
        costs += [int(m) for m in re.findall(r"\bCheckMoney2\b\s+\w+,\s*(\d+)", body)]
        costs += [int(m) for m in re.findall(r"\bGoToIfNotEnoughMoney\s+(\d+)", body)]
        costs += [int(m) for m in re.findall(r"\bRemoveMoney\b\s+(\d+)", body)]
        price_str = f"₱{min(costs):,}" if costs else "Money"
        for raw in seen_items:
            pairs.append((price_str, _item_name(raw)))

    return pairs


def _pt_trade_info(map_name: str, script_idx: int) -> dict | None:
    """Find InitNPCTrade NPC_TRADE_X in script and return {gives, receives, nickname}."""
    if not map_name or not isinstance(script_idx, int):
        return None
    table = _load_script_table(map_name)
    idx = script_idx - 1
    if idx < 0 or idx >= len(table):
        return None
    label = table[idx]
    body_dict = _load_script_body(map_name)
    seen_labels: set[str] = {label}
    queue_labels = [label]
    all_lines: list[str] = []
    while queue_labels:
        cur = queue_labels.pop(0)
        cur_lines = body_dict.get(cur, [])
        all_lines.extend(cur_lines)
        for line in cur_lines:
            m2 = re.match(r"\s*(?:GoTo\w*|Call\w*)\b.*?\b(\w+)\s*$", line)
            if m2:
                tgt = m2.group(1)
                if tgt not in seen_labels and tgt in body_dict:
                    seen_labels.add(tgt)
                    queue_labels.append(tgt)
    body = "\n".join(all_lines)
    m = re.search(r"InitNPCTrade\s+(NPC_TRADE_\w+)", body)
    if m:
        return _pt_trade_table().get(m.group(1))
    return None


def _pt_detect_service_flags(map_name: str, script_idx: int) -> set[str]:
    """Scan a Platinum NPC script body (+ one goto level) for service-indicator commands."""
    flags: set[str] = set()
    if not map_name or not isinstance(script_idx, int):
        return flags
    table = _load_script_table(map_name)
    idx = script_idx - 1
    if idx < 0 or idx >= len(table):
        return flags
    label = table[idx]
    body_dict = _load_script_body(map_name)
    # BFS following GoTo* / Call variants (one level deep is enough for flag detection)
    seen_labels: set[str] = {label}
    queue_labels = [label]
    all_lines: list[str] = []
    while queue_labels:
        cur = queue_labels.pop(0)
        cur_lines = body_dict.get(cur, [])
        all_lines.extend(cur_lines)
        for line in cur_lines:
            # Extract last word-token on any GoTo*/Call* line as jump target
            m = re.match(r"\s*(?:GoTo\w*|Call\w*)\b.*?\b(\w+)\s*$", line)
            if m:
                tgt = m.group(1)
                if tgt not in seen_labels and tgt in body_dict:
                    seen_labels.add(tgt)
                    queue_labels.append(tgt)
    body = "\n".join(all_lines)
    if re.search(r"PokeMartCommon\w*|PokeMartSpecialties\w*|PokeMartFrontier\b", body):
        flags.add("vendor")
    if re.search(r"Common_CallPokecenterNurse\b", body):
        flags.add("healer")
    if re.search(r"InitNPCTrade\b", body):
        flags.add("trader")
    if re.search(r"OpenMoveReminderMenu\b", body):
        flags.add("move_reminder")
    if re.search(r"SelectPartyMonMove\b", body):
        flags.add("move_deleter")
    if re.search(r"CheckIsPartyMonOutsider\b", body):
        flags.add("name_rater")
    if re.search(r"PayShardCost\b|CheckHasLearnableTutorMoves\b", body):
        flags.add("move_tutor")
    if re.search(r"GetFossilCount\b", body):
        flags.add("vendor")
    if re.search(r"SetFlag\s+FLAG_VILLA_FURNITURE_", body):
        flags.add("villa_catalog")
    if re.search(r"\bCheckItem\s+ITEM_STAR_PIECE\b", body):
        flags.add("star_piece_exchange")
    if (re.search(r"\bCheckMoney\b|\bCheckMoney2\b|\bGoToIfNotEnoughMoney\b|\bRemoveMoney\b", body)
            and re.search(r"\bAddItem\s+ITEM_\w+|\bSetVar\s+VAR_0x800[14],\s*ITEM_\w+", body)
            and "villa_catalog" not in flags):
        flags.add("money_item_vendor")
    return flags


# ---------------------------------------------------------------------------
# Gym leader detection + dialogue extraction (Platinum)
# ---------------------------------------------------------------------------

_PT_GYM_BATTLE_RE  = re.compile(
    r"^StartTrainerBattle\s+(TRAINER_(?:LEADER|ELITE_FOUR|CHAMPION)_\w+)")
_PT_MSG_LINE_RE    = re.compile(r"^Message\s+(\w+)")
_PT_PLAIN_GOTO_RE  = re.compile(r"^GoTo\s+(\w+)")
_PT_TERMINAL_CMDS  = frozenset({"End", "ReleaseAll", "BlackOutFromBattle", "Return"})


def _pt_gym_leader_info(map_name: str, script_idx: int) -> dict | None:
    """
    Return gym leader data for a TRAINER_TYPE_NONE NPC whose script
    contains StartTrainerBattle TRAINER_LEADER_*.
    Returns None if the script does not match that pattern.
    """
    table = _load_script_table(map_name)
    idx = script_idx - 1
    if idx < 0 or idx >= len(table):
        return None

    script_body = _load_script_body(map_name)
    texts       = _load_text(map_name)

    # Find the label that contains StartTrainerBattle TRAINER_LEADER_*
    battle_label  = None
    trainer_const = None
    for lbl, body_lines in script_body.items():
        for line in body_lines:
            m = _PT_GYM_BATTLE_RE.match(line)
            if m:
                battle_label  = lbl
                trainer_const = m.group(1)
                break
        if battle_label:
            break

    if not trainer_const:
        return None

    # Walk the battle label (and its win-path successors) to collect dialogue.
    pre_battle:  list[str] = []
    post_battle: list[str] = []
    phase = "pre"          # pre → battle → post_pending → post
    visited: set[str] = set()
    gives_item: list = [None]    # [{"item_id": ..., "quantity": 1}] once found
    _pending_item: list = [None] # tracks SetVar VAR_0x8004 before Common_GiveItemQuantity
    gym_badge: list = [None]     # [badge_constant] once found

    def _collect(lbl: str) -> None:
        nonlocal phase
        if lbl in visited:
            return
        visited.add(lbl)
        for line in script_body.get(lbl, []):
            mm = _PT_MSG_LINE_RE.match(line)
            if mm:
                text = texts.get(mm.group(1), "")
                if text:
                    # Flatten newlines and extra whitespace
                    text = re.sub(r"\s+", " ", text.replace("\n", " ").replace("\r", "")).strip()
                    if phase == "pre":
                        pre_battle.append(text)
                    elif phase == "post":
                        post_battle.append(text)
                continue
            if _PT_GYM_BATTLE_RE.match(line):
                phase = "battle"
                continue
            if line.startswith("CheckWonBattle"):
                phase = "post_pending"
                continue
            # The lose-branch skip: GoToIfEq VAR_RESULT, FALSE, *LostBattle*
            if phase == "post_pending" and "FALSE" in line and "GoToIfEq" in line:
                phase = "post"
                continue
            # In the win path, follow unconditional GoTo only (avoids bag-full branches)
            if phase == "post":
                if gives_item[0] is None:
                    sv = re.match(r"SetVar\s+VAR_0x8004,\s+(ITEM_\w+)", line)
                    if sv:
                        _pending_item[0] = sv.group(1)
                    elif "Common_GiveItemQuantity" in line and _pending_item[0]:
                        gives_item[0] = {"item_id": _pending_item[0], "quantity": 1}
                        _pending_item[0] = None
                if gym_badge[0] is None:
                    gb_m = re.search(r"\bGiveBadge\s+(BADGE_ID_\w+)", line)
                    if gb_m:
                        gym_badge[0] = gb_m.group(1)
                gm = _PT_PLAIN_GOTO_RE.match(line)
                if gm:
                    _collect(gm.group(1))

    _collect(battle_label)

    # Trainer JSON
    td  = _load_trainer(trainer_const) or {}
    # Rematch JSON (Battleground fight)
    rematch_const = trainer_const + "_REMATCH"
    rtd = _load_trainer(rematch_const) or {}

    def _clean_pt_text(text: object) -> str:
        if isinstance(text, list):
            text = " ".join(t.strip() for t in text if t.strip())
        return re.sub(r"\s+", " ", str(text).replace("\n", " ").replace("\r", "")).strip()

    msgs: dict[str, str] = {}
    for msg in td.get("messages", []):
        mtype = msg.get("type", "")
        msgs[mtype] = _clean_pt_text(msg.get("en_US", ""))

    return {
        "trainer_const":  trainer_const,
        "name":           td.get("name", "?"),
        "class":          td.get("class", ""),
        "ai_flags":       [f.replace("AI_FLAG_", "") for f in (td.get("ai_flags") or [])],
        "items":          [_item_name(i) for i in (td.get("items") or []) if i],
        "pre_battle":     [_clean_pt_text(t) for t in pre_battle],
        "post_battle":    [_clean_pt_text(t) for t in post_battle],
        "messages":       msgs,
        "party":          _parse_party(td),
        "rematch_const":  rematch_const if rtd else None,
        "rematch_party":  _parse_party(rtd),
        "rematch_prize":  _calc_prize(rtd.get("class", td.get("class", "")), _parse_party(rtd)) if rtd else None,
        "gym_badge":      gym_badge[0],
        "gives_item":     gives_item[0],
    }


def load_events(map_entry: dict, include_background: bool = False) -> dict:
    """
    Extract events from a map entry as built by map_entry().
    Returns {trainers, event_trainers, permanent_npcs, event_npcs, signs,
             items, hidden_items, warps, connections}.
    Coordinates are world-grid tile positions.
    Subtract tile_entry['_tile_col_min'] / ['_tile_row_min'] for local grid coords.
    """
    result: dict = {
        "trainers":          [],
        "event_trainers":    [],
        "rivals":            [],
        "gym_leaders":       [],
        "permanent_npcs":    [],
        "event_npcs":        [],
        "signs":             [],
        "items":             [],
        "hidden_items":      [],
        "vending_machines":  [],
        "warps":             [],
        "connections":       _get_connections(map_entry),
        "field_objects":     [],
        "static_encounters": _pt_static_encounters(map_entry),
    }
    constant = map_entry.get("constant", "")
    map_name = _map_name_from_constant(constant)

    # Build a set of (x, z) coords claimed by static encounters so we can skip
    # those object_events from the NPC index.
    _static_enc_coords: set[tuple] = {
        (e["x"], e["z"])
        for e in result["static_encounters"]
        if e.get("x") is not None and e.get("z") is not None
    }

    # Pre-load all trainer constants referenced in this map's script (for event battles)
    script_trainer_constants = _find_trainer_constants_in_script(map_name)
    # Track which constants we've already emitted so double-battle partners aren't duplicated
    _used_trainer_constants: set[str] = set()
    # Track gym leaders already added (dedup by trainer const — same leader has two script slots)
    _gym_leader_consts_seen: set[str] = set()

    for obj in map_entry.get("object_events", []):
        x      = obj.get("x", 0)
        z      = obj.get("z", 0)
        tr     = obj.get("trainer_type", "TRAINER_TYPE_NONE")
        gfx    = obj.get("graphics_id", "")
        script = obj.get("script", "")
        flag   = obj.get("hidden_flag", 0)
        npc_id = obj.get("id", "")
        is_event = (flag != 0 and flag != "0")

        if gfx in _PT_SKIP_GFX:
            continue
        if gfx in _PT_BACKGROUND_GFX and script == 0 and not include_background:
            continue

        if tr != "TRAINER_TYPE_NONE" and isinstance(script, str):
            if script in _used_trainer_constants:
                continue  # double-battle partner shares the same script key
            _used_trainer_constants.add(script)
            td = _load_trainer(script) or {}
            entry = _build_trainer_entry(x, z, td, is_event, const=script)
            entry["category"] = "Standard"
            result["trainers"].append(entry)

        elif tr != "TRAINER_TYPE_NONE" and isinstance(script, int) and script > 0 and map_name:
            # Integer-script TRAINER_TYPE_NORMAL objects (Restaurant, etc.)
            # The actual StartTrainerBattle call may be in a sub-label branch, so scan
            # the whole script file for all StartTrainerBattle constants.
            # _find_trainer_constants_in_script already does this; _used_trainer_constants
            # ensures each constant is added once (subsequent objects are no-ops).
            for const in _find_trainer_constants_in_script(map_name):
                if const in _used_trainer_constants:
                    continue
                _used_trainer_constants.add(const)
                td = _load_trainer(const) or {}
                if td:
                    entry = _build_trainer_entry(x, z, td, is_event, const=const)
                    entry["category"] = "Standard"
                    result["trainers"].append(entry)

        elif "OBJ_EVENT_GFX_POKEBALL" in gfx:
            item_name = _resolve_item_ball(map_name, script) if isinstance(script, int) else str(script)
            result["items"].append({
                "x": x, "z": z, "script": script,
                "item_name": item_name, "is_event": is_event,
            })

        elif _is_sign(gfx):
            dialogue = None
            if isinstance(script, int) and map_name:
                dialogue = _resolve_npc_dialogue(map_name, script)
            label = npc_id.replace("LOCALID_", "").replace("_", " ").title()
            result["signs"].append({
                "x": x, "z": z,
                "graphics_id": gfx,
                "label":       label,
                "dialogue":    dialogue,
                "is_event":    is_event,
            })

        elif gfx in _FIELD_OBSTACLE_GFX:
            char, label = _FIELD_OBSTACLE_GFX[gfx]
            result["field_objects"].append({"x": x, "z": z, "char": char, "label": label, "gfx": gfx})

        elif gfx in _PT_STATIC_ENC_GFX:
            species, level = _PT_STATIC_ENC_GFX[gfx]
            form = gfx.removeprefix("OBJ_EVENT_GFX_ROTOM_").title()
            result["static_encounters"].append({
                "x": x, "z": z, "species": species, "level": level,
                "battleable": True, "tags": [f"Form: {form}"], "gfx": gfx,
            })

        elif tr == "TRAINER_TYPE_NONE" and gfx and not gfx.startswith("OBJ_EVENT_GFX_BERRY"):
            # Check if this NPC is actually an event battle trainer
            keyword = _gfx_trainer_keyword(gfx)
            if keyword and script_trainer_constants:
                # If any constant for this GFX type exists in the script (used or not),
                # this object is an event battle — never show it as a plain NPC.
                any_match = any(keyword in c for c in script_trainer_constants)
                if any_match:
                    new_constants = [c for c in script_trainer_constants
                                     if keyword in c and c not in _used_trainer_constants]
                    for const in new_constants:
                        _used_trainer_constants.add(const)
                        td = _load_trainer(const) or {}
                        if not td:
                            continue
                        # Variant label for multi-version battles (rival starter variants)
                        suffix = const.replace("TRAINER_", "").replace("_", " ").title()
                        entry = _build_trainer_entry(x, z, td, is_event=True, note=suffix, const=const)
                        entry["category"] = "Interaction"
                        bucket = "rivals" if keyword == "RIVAL" else "event_trainers"
                        result[bucket].append(entry)
                    continue  # skip NPC path regardless (already covered or duplicate)

            # Skip NPCs that are already represented as static encounters
            if (x, z) in _static_enc_coords:
                continue

            # Gym leader: TRAINER_TYPE_NONE with integer script whose body
            # contains StartTrainerBattle TRAINER_LEADER_*
            if isinstance(script, int) and map_name:
                gl = _pt_gym_leader_info(map_name, script)
                if gl:
                    tc = gl["trainer_const"]
                    if tc not in _gym_leader_consts_seen:
                        _gym_leader_consts_seen.add(tc)
                        result["gym_leaders"].append({**gl, "x": x, "z": z, "is_event": is_event, "category": "Interaction"})
                    continue

            script_data = None
            if isinstance(script, int) and map_name:
                script_data = _pt_collect_npc_script(map_name, script)
            service_flags = (
                _pt_detect_service_flags(map_name, script)
                if isinstance(script, int) and map_name else set()
            )
            if "healer" in service_flags and not any(
                s.get("pre_msgs") for s in (script_data or {}).get("states", [])
            ):
                script_data = {"states": [{
                    "flag": None, "polarity": "post",
                    "pre_msgs": [
                        "Hello, and welcome to the Pokémon Center. "
                        "We restore your tired Pokémon to full health. "
                        "Would you like to rest your Pokémon?"
                    ],
                    "gives": [], "yesno": True,
                    "declined_msg": "We hope to see you again!",
                }]}
            exchange_pairs = (
                _pt_exchange_pairs(map_name, script, service_flags)
                if isinstance(script, int) and map_name else []
            )
            trade_info = (
                _pt_trade_info(map_name, script)
                if "trader" in service_flags and isinstance(script, int) and map_name else None
            )
            entry = {
                "x": x, "z": z,
                "graphics_id": gfx,
                "id":          npc_id,
                "movement":    obj.get("movement_type", "MOVEMENT_TYPE_NONE"),
                "script_data": script_data,
                "is_event":    is_event,
                "service_flags": service_flags,
                "exchange_pairs": exchange_pairs,
                "trade_info":    trade_info,
            }
            if is_event:
                result["event_npcs"].append(entry)
            else:
                result["permanent_npcs"].append(entry)

    # StartTagBattle sweep: catches story-triggered double battles (Spear Pillar, Fight Area).
    # Scans the entire script file for StartTagBattle with TRAINER_* enemies plus any
    # SetVar VAR_0x8004, TRAINER_* ally variants (starter-dependent partner).
    _PT_TAG_BATTLE_RE = re.compile(r'StartTagBattle\s+\S+,\s*(TRAINER_\w+),\s*(TRAINER_\w+)')
    _PT_SET_ALLY_RE   = re.compile(r'SetVar\s+VAR_0x8004,\s*(TRAINER_\w+)')
    if map_name:
        _tag_script_path = SCRIPTS_DIR / f"scripts_{map_name.lower()}.s"
        if _tag_script_path.exists():
            _tag_text = _tag_script_path.read_text(encoding='utf-8')
            _ally_consts = [m.group(1) for m in _PT_SET_ALLY_RE.finditer(_tag_text)
                            if 'ITEM_' not in m.group(1)]
            for tbm in _PT_TAG_BATTLE_RE.finditer(_tag_text):
                for const in [tbm.group(1), tbm.group(2)]:
                    if const in _used_trainer_constants:
                        continue
                    _used_trainer_constants.add(const)
                    td = _load_trainer(const) or {}
                    if not td:
                        continue
                    entry = _build_trainer_entry(0, 0, td, is_event=True, const=const)
                    entry["no_coord"] = True
                    entry["category"] = "One-time"
                    entry["ally_variants"] = [
                        {**((_load_trainer(ac) or {})), "const": ac}
                        for ac in _ally_consts
                        if _load_trainer(ac)
                    ]
                    result["event_trainers"].append(entry)

    # bg_events: type 0 = interactive (vending machines, signs); type 2 = hidden items
    for bg in map_entry.get("bg_events", []):
        bg_type = bg.get("type")
        script_id = bg.get("script", 0)
        if bg_type == 0 and isinstance(script_id, int) and map_name:
            table = _load_script_table(map_name)
            idx = script_id - 1
            if 0 <= idx < len(table):
                label = table[idx]
                if re.search(r"VendingMachine", label, re.I):
                    result["vending_machines"].append({
                        "x": bg.get("x", 0), "z": bg.get("z", 0),
                        "label": label,
                        "exchange_pairs": [
                            ("Money", "Fresh Water"),
                            ("Money", "Soda Pop"),
                            ("Money", "Lemonade"),
                        ],
                    })
        elif bg_type == 2:
            hi = _get_hidden_item(script_id) if isinstance(script_id, int) else None
            if hi:
                item_name, qty = hi
            else:
                item_name, qty = f"Item#{script_id}", 1
            result["hidden_items"].append({
                "x": bg.get("x", 0), "z": bg.get("z", 0),
                "item_name": item_name, "qty": qty,
            })

    # Honey trees — 3D props from map_data_NNN.bin binaries.
    # The prop position is the bottom-right tile of a 2×2 footprint, so fill all 4.
    # Only the top-left tile is marked as the index entry.
    for wx, wz in _get_honey_tree_positions(map_entry):
        for i, (dc, dr) in enumerate(((-1, -1), (0, -1), (-1, 0), (0, 0))):
            result["field_objects"].append({
                "x": wx + dc, "z": wz + dr,
                "char": "Y", "label": "Honey Tree", "gfx": "honey_tree_nsbmd",
                "index_entry": i == 0,
            })

    # Warps — classify building type from dest_header_id
    for w in map_entry.get("warp_events", []):
        dest = w.get("dest_header_id", "")
        classified = _classify_warp(dest)
        result["warps"].append({
            "x": w.get("x", 0), "z": w.get("z", 0),
            "dest": dest,
            "char":  classified[0] if classified else "D",
            "label": classified[1] if classified else dest,
        })

    return result


# ---------------------------------------------------------------------------
# Event section renderers
# ---------------------------------------------------------------------------

def _local(world_val: int, offset: int) -> int:
    return world_val - offset


def render_sign_section(signs: list, col_off: int = 0, row_off: int = 0) -> list[str]:
    if not signs:
        return []
    SEP = "=" * 70
    lines = ["", SEP, "  Sign Index", SEP, ""]
    for s in signs:
        col = _local(s["x"], col_off)
        row = _local(s["z"], row_off)
        label = s.get("label", "")
        lines.append(f"  (col={col:>3}, row={row:>3})  {label}")
        if s.get("dialogue"):
            lines.append(f"          \"{s['dialogue']}\"")
        lines.append("")
    return lines


def render_npc_section(permanent: list, event: list,
                       col_off: int = 0, row_off: int = 0) -> list[str]:
    if not permanent and not event:
        return []
    SEP = "=" * 70
    lines = ["", SEP, "  NPC Index", SEP, ""]

    def _fmt_npc(n: dict) -> None:
        col = _local(n["x"], col_off)
        row = _local(n["z"], row_off)
        gfx = n["graphics_id"].replace("OBJ_EVENT_GFX_", "").replace("_", " ").title()
        npc_id = n.get("id", "").replace("LOCALID_", "").replace("_", " ").title()
        lines.append(f"  (col={col:>3}, row={row:>3})  {gfx}"
                     + (f"  [{npc_id}]" if npc_id else ""))
        sd = n.get("script_data")
        INDENT = "          "   # 10 spaces
        EXTRA  = "              "  # 14 spaces
        lines.append(f"{INDENT}Movement:  {_fmt_movement(n.get('movement', 'MOVEMENT_TYPE_NONE'))}")
        if sd:
            states = sd.get("states", [])
            single = len(states) == 1 and states[0].get("flag") is None
            for state in states:
                flag     = state.get("flag")
                polarity = state.get("polarity", "post")
                pre_msgs = state.get("pre_msgs", [])
                gives    = state.get("gives", [])
                yesno    = state.get("yesno", False)
                declined = state.get("declined_msg")
                if single:
                    content = INDENT
                else:
                    tag = ("[default]" if flag is None else
                           (f"[~{flag}]" if polarity == "pre" else f"[{flag}]"))
                    lines.append(f"{INDENT}{tag}")
                    content = EXTRA
                if yesno:
                    if pre_msgs:
                        lines.append(f"{content}Prompt (yes/no):    \"{pre_msgs[-1]}\"")
                    if gives:
                        g = gives[0]
                        qty_tag = f"  ×{g['qty']}" if g["qty"] > 1 else ""
                        lines.append(f"{content}Gives:  {_item_name(g['item'])}{qty_tag}")
                        for msg in g.get("post_msgs", []):
                            lines.append(f"{content}Dialogue (after):   \"{msg}\"")
                    if declined:
                        lines.append(f"{content}Declined:           \"{declined}\"")
                elif gives:
                    for msg in pre_msgs:
                        lines.append(f"{content}Dialogue (before):  \"{msg}\"")
                    for g in gives:
                        qty_tag = f"  ×{g['qty']}" if g["qty"] > 1 else ""
                        lines.append(f"{content}Gives:  {_item_name(g['item'])}{qty_tag}")
                        for msg in g.get("post_msgs", []):
                            lines.append(f"{content}Dialogue (after):   \"{msg}\"")
                else:
                    for msg in pre_msgs:
                        lines.append(f"{content}\"{msg}\"")
        for sf in sorted(n.get("service_flags", ())):
            lines.append(f"          [{sf}]")
        lines.append("")

    if permanent:
        lines.append("  -- Permanent --")
        lines.append("")
        for n in permanent:
            _fmt_npc(n)

    if event:
        lines.append("  -- Event / Conditional --")
        lines.append("")
        for n in event:
            _fmt_npc(n)

    return lines


def render_item_section(items: list, col_off: int = 0, row_off: int = 0) -> list[str]:
    if not items:
        return []
    SEP = "=" * 70
    lines = ["", SEP, "  Item Balls", SEP, ""]
    for it in items:
        col = _local(it["x"], col_off)
        row = _local(it["z"], row_off)
        lines.append(f"  (col={col:>3}, row={row:>3})  ⊙  {it.get('item_name', '?')}")
    lines.append("")
    return lines


def render_hidden_item_section(hidden: list, col_off: int = 0, row_off: int = 0) -> list[str]:
    if not hidden:
        return []
    SEP = "=" * 70
    lines = ["", SEP, "  Hidden Items", SEP, ""]
    for it in hidden:
        col = _local(it["x"], col_off)
        row = _local(it["z"], row_off)
        qty_str = f" ×{it['qty']}" if it.get("qty", 1) > 1 else ""
        lines.append(f"  (col={col:>3}, row={row:>3})  *  {it['item_name']}{qty_str}")
    lines.append("")
    return lines


def render_field_object_section(field_objects: list, col_off: int = 0, row_off: int = 0) -> list[str]:
    if not field_objects:
        return []
    SEP = "=" * 70
    lines = ["", SEP, "  Field Obstacles", SEP, ""]
    for fo in field_objects:
        # Multi-tile objects emit one entry per tile on the grid but only list once in the index
        if not fo.get("index_entry", True):
            continue
        col = _local(fo["x"], col_off)
        row = _local(fo["z"], row_off)
        lines.append(f"  [{fo['char']}]  (col={col:>3}, row={row:>3})  {fo['label']}")
    lines.append("")
    return lines


_PT_DEST_WARP_CACHE:  "dict[str, list]" = {}
_PT_REVERSE_WARP:     "dict[str, list[tuple[str, int, int]]] | None" = None


def pt_dest_warp_coord(dest_header_id: str, dest_warp_id: int) -> "tuple[int, int] | None":
    """Return (col, row) spawn coord in dest_header_id for dest_warp_id, or None."""
    if dest_header_id == "MAP_HEADER_DYNAMIC" or dest_warp_id == 256:
        return None
    reg = _map_registry()
    dest = reg.get(dest_header_id)
    if dest is None:
        return None
    events_id = dest.get("events_id", "")
    if not events_id or events_id == "events_empty":
        return None
    if events_id not in _PT_DEST_WARP_CACHE:
        p = EVENTS_DIR / f"{events_id}.json"
        try:
            _PT_DEST_WARP_CACHE[events_id] = json.loads(
                p.read_text(encoding="utf-8")
            ).get("warp_events") or []
        except (OSError, json.JSONDecodeError):
            _PT_DEST_WARP_CACHE[events_id] = []
    warps = _PT_DEST_WARP_CACHE[events_id]
    if 0 <= dest_warp_id < len(warps):
        w = warps[dest_warp_id]
        try:
            return int(w["x"]), int(w["z"])
        except (KeyError, TypeError, ValueError):
            return None
    return None


def pt_dynamic_warp_floors(elev_const: str) -> "list[tuple[str, int, int]]":
    """
    All maps that have a static warp pointing into elev_const, as
    (floor_const, local_col, local_row).  Used for elevator maps whose
    outbound warps target MAP_HEADER_DYNAMIC (dest_warp_id=256).
    """
    global _PT_REVERSE_WARP
    if _PT_REVERSE_WARP is None:
        _PT_REVERSE_WARP = {}
        reg = _map_registry()
        for const, entry in reg.items():
            events_id = entry.get("events_id", "")
            if not events_id or events_id == "events_empty":
                continue
            p = EVENTS_DIR / f"{events_id}.json"
            try:
                warps = json.loads(
                    p.read_text(encoding="utf-8")
                ).get("warp_events") or []
            except (OSError, json.JSONDecodeError):
                continue
            for w in warps:
                dest = str(w.get("dest_header_id", ""))
                try:
                    lx, lz = int(w["x"]), int(w["z"])
                except (KeyError, TypeError, ValueError):
                    continue
                _PT_REVERSE_WARP.setdefault(dest, []).append((const, lx, lz))
    return _PT_REVERSE_WARP.get(elev_const, [])


def render_warp_section(warps: list, connections: list = (),
                        col_off: int = 0, row_off: int = 0) -> list[str]:
    if not warps and not connections:
        return []
    SEP = "=" * 70
    lines = ["", SEP, "  Building / Warp Index  (Platinum)", SEP, ""]
    seen: set[str] = set()
    for w in warps:
        col   = _local(w["x"], col_off)
        row   = _local(w["z"], row_off)
        label = w.get("label", w.get("dest", "?"))
        char  = w.get("char", "D")
        line  = f"  [{char}]  (col={col:>3}, row={row:>3})  {label}"
        if line not in seen:
            seen.add(line)
            lines.append(line)
    if connections:
        if warps:
            lines.append("")
        lines.append("  Map Connections:")
        _DIR_LABEL = {"N": "North", "S": "South", "W": "West", "E": "East"}
        for conn in connections:
            dir_label = _DIR_LABEL.get(conn["direction"], conn["direction"])
            lines.append(f"    {dir_label:<6}  →  {conn['label']}")
    lines.append("")
    return lines


def render_trainer_section(trainers: list, event_trainers: list = (),
                           col_off: int = 0, row_off: int = 0) -> list[str]:
    if not trainers and not event_trainers:
        return []
    SEP = "=" * 70
    lines = ["", SEP, "  Trainer Index", SEP, ""]

    def _fmt_trainer(t: dict) -> None:
        col = _local(t["x"], col_off)
        row = _local(t["z"], row_off)
        cls_disp = (
            t["class"].replace("TRAINER_CLASS_", "").replace("_", " ").title()
            if t.get("class") else "?"
        )
        note = f"  ({t['note']})" if t.get("note") else ""
        cat_tag    = f"  [{t['category']}]" if t.get("category") else ""
        double_tag = "  [Double Battle]" if t.get("double") else ""
        prize_str  = f"  Prize: ¥{t['prize']}" if t.get("prize") else ""
        coord_str = "  (no fixed tile)  " if t.get("no_coord") else f"  (col={col:>3}, row={row:>3})  "
        lines.append(f"{coord_str}{cls_disp} {t['name']}{note}{cat_tag}{double_tag}{prize_str}")
        if t.get("items"):
            lines.append(f"          Items: {', '.join(i for i in t['items'] if i)}")
        msgs = t.get("messages", {})
        if t.get("double"):
            if msgs.get("TRMSG_PRE_DOUBLE_BATTLE_1"):
                lines.append(f"          Pre-battle 1:  \"{msgs['TRMSG_PRE_DOUBLE_BATTLE_1']}\"")
            if msgs.get("TRMSG_PRE_DOUBLE_BATTLE_2"):
                lines.append(f"          Pre-battle 2:  \"{msgs['TRMSG_PRE_DOUBLE_BATTLE_2']}\"")
            if msgs.get("TRMSG_DOUBLE_BATTLE_DEFEAT_1"):
                lines.append(f"          Defeat 1:      \"{msgs['TRMSG_DOUBLE_BATTLE_DEFEAT_1']}\"")
            if msgs.get("TRMSG_DOUBLE_BATTLE_DEFEAT_2"):
                lines.append(f"          Defeat 2:      \"{msgs['TRMSG_DOUBLE_BATTLE_DEFEAT_2']}\"")
            if msgs.get("TRMSG_POST_DOUBLE_BATTLE_1"):
                lines.append(f"          Post-battle 1: \"{msgs['TRMSG_POST_DOUBLE_BATTLE_1']}\"")
            if msgs.get("TRMSG_POST_DOUBLE_BATTLE_2"):
                lines.append(f"          Post-battle 2: \"{msgs['TRMSG_POST_DOUBLE_BATTLE_2']}\"")
        else:
            if msgs.get("TRMSG_PRE_BATTLE"):
                lines.append(f"          Pre-battle:  \"{msgs['TRMSG_PRE_BATTLE']}\"")
            if msgs.get("TRMSG_DEFEAT"):
                lines.append(f"          Defeat:      \"{msgs['TRMSG_DEFEAT']}\"")
            if msgs.get("TRMSG_POST_BATTLE"):
                lines.append(f"          Post-battle: \"{msgs['TRMSG_POST_BATTLE']}\"")
            if msgs.get("TRMSG_REMATCH"):
                lines.append(f"          Rematch:     \"{msgs['TRMSG_REMATCH']}\"")
        def _fmt_party(party: list, indent: str = "            ") -> None:
            for mon in party:
                lv_str = f"Lv {mon['lvl']}"
                mv_str = ", ".join(mon.get("moves") or ["(default)"])
                iv_raw = mon.get('iv_scale', 0)
                iv_str = f"IV={iv_raw} ({iv_raw * 31 // 255}/31)"
                lines.append(f"{indent}{mon['species'].title():<18} {lv_str:<6} {iv_str}")
                if mv_str != "(default)":
                    lines.append(f"{indent}{'':18} Moves: {mv_str}")
                if mon.get("item"):
                    lines.append(f"{indent}{'':18} Item:  {mon['item']}")

        lines.append("          Initial battle:")
        _fmt_party(t.get("party", []))

        vs = t.get("vs_rematches", [])
        if vs:
            for i, r in enumerate(vs, 1):
                if r is None:
                    lines.append(f"          VS Seeker rematch: same party")
                else:
                    prize_str = f"  Prize: ¥{r['prize']}" if r.get("prize") else ""
                    lines.append(f"          VS Seeker rematch {i}:{prize_str}")
                    _fmt_party(r.get("party", []))

        ally_list = t.get("ally_variants", [])
        if ally_list:
            acls_of = lambda a: (
                a.get("class", "").replace("TRAINER_CLASS_", "").replace("_", " ").title()
                or "?"
            )
            if len(ally_list) == 1:
                a = ally_list[0]
                lines.append(f"          Partner:  {acls_of(a)} {a.get('name','?')}  [{a.get('const','')}]")
                _fmt_party(_parse_party(a), indent="            ")
            else:
                lines.append("          Partner (starter-dependent):")
                for a in ally_list:
                    lines.append(f"            [{a.get('const','')}]  {acls_of(a)} {a.get('name','?')}")
                    _fmt_party(_parse_party(a), indent="              ")
        lines.append("")

    if trainers:
        lines.append("  -- Regular Trainers --")
        lines.append("")
        for t in trainers:
            _fmt_trainer(t)

    if event_trainers:
        lines.append("  -- Event Battles --")
        lines.append("")
        for t in event_trainers:
            _fmt_trainer(t)

    return lines


def render_rival_section(rivals: list, col_off: int = 0, row_off: int = 0) -> list[str]:
    if not rivals:
        return []
    SEP = "=" * 70
    lines = ["", SEP, "  Rival Encounter  (Platinum)", SEP, ""]
    for t in rivals:
        col = _local(t["x"], col_off)
        row = _local(t["z"], row_off)
        cls_disp = (
            t["class"].replace("TRAINER_CLASS_", "").replace("_", " ").title()
            if t.get("class") else "?"
        )
        note = f"  ({t['note']})" if t.get("note") else ""
        prize_str = f"  Prize: ¥{t['prize']}" if t.get("prize") else ""
        lines.append(f"  (col={col:>3}, row={row:>3})  {cls_disp} {t['name']}{note}{prize_str}")
        if t.get("items"):
            lines.append(f"          Items: {', '.join(i for i in t['items'] if i)}")
        msgs = t.get("messages", {})
        if msgs.get("TRMSG_PRE_BATTLE"):
            lines.append(f"          Pre-battle:  \"{msgs['TRMSG_PRE_BATTLE']}\"")
        if msgs.get("TRMSG_DEFEAT"):
            lines.append(f"          Defeat:      \"{msgs['TRMSG_DEFEAT']}\"")
        if msgs.get("TRMSG_POST_BATTLE"):
            lines.append(f"          Post-battle: \"{msgs['TRMSG_POST_BATTLE']}\"")
        party = t.get("party", [])
        if party:
            lines.append("")
            for mon in party:
                lv_str = f"Lv {mon['lvl']}"
                mv_str = ", ".join(mon.get("moves") or ["(default)"])
                lines.append(f"            {mon['species'].title():<18} {lv_str}")
                if mv_str != "(default)":
                    lines.append(f"            {'':18} Moves: {mv_str}")
                if mon.get("item"):
                    lines.append(f"            {'':18} Item:  {mon['item']}")
        lines.append("")
    return lines


def render_gym_leader_section(gym_leaders: list,
                              col_off: int = 0, row_off: int = 0) -> list[str]:
    if not gym_leaders:
        return []
    SEP = "=" * 70
    lines = ["", SEP, "  Gym Leader  (Platinum)", SEP, ""]

    _MSG_LABELS: list[tuple[str, str]] = [
        ("TRMSG_PRE_BATTLE",       "Pre-battle (trainer JSON)"),
        ("TRMSG_LAST_BATTLER",     "Last Pokémon"),
        ("TRMSG_LAST_BATTLER_HALF_HP", "Last mon (½ HP)"),
        ("TRMSG_DEFEAT",           "Defeat (trainer JSON)"),
        ("TRMSG_POST_BATTLE",      "Post-battle (trainer JSON)"),
    ]

    def _fmt_party(party: list, indent: str = "            ") -> None:
        for mon in party:
            lv_str = f"Lv {mon['lvl']}"
            mv_str = ", ".join(mon.get("moves") or ["(default)"])
            iv_raw = mon.get('iv_scale', 0)
            iv_str = f"IV={iv_raw} ({iv_raw * 31 // 255}/31)"
            lines.append(f"{indent}{mon['species'].title():<18} {lv_str:<6} {iv_str}")
            if mv_str != "(default)":
                lines.append(f"{indent}{'':18} Moves: {mv_str}")
            if mon.get("item"):
                lines.append(f"{indent}{'':18} Item:  {mon['item']}")

    for gl in gym_leaders:
        col = _local(gl["x"], col_off)
        row = _local(gl["z"], row_off)
        # Class is TRAINER_CLASS_LEADER_NAME — strip down to just "Leader"
        raw_cls = gl.get("class", "")
        if "LEADER" in raw_cls:
            cls_disp = "Leader"
        else:
            cls_disp = raw_cls.replace("TRAINER_CLASS_", "").replace("_", " ").title()
        lines.append(f"  (col={col:>3}, row={row:>3})  {cls_disp} {gl['name']}"
                     f"  [{gl['trainer_const']}]  [Interaction]")
        if gl.get("items"):
            lines.append(f"          Healing items: {', '.join(gl['items'])}")
        if gl.get("gym_badge"):
            lines.append(f"          Gym badge:     {gl['gym_badge']}")
        if gl.get("gives_item"):
            gi = gl["gives_item"]
            lines.append(f"          Gives item:    {gi['item_id']}  ×{gi['quantity']}")
        for pb in gl.get("pre_battle", []):
            lines.append(f"          Pre-battle:   \"{pb}\"")
        msgs = gl.get("messages", {})
        for key, label in _MSG_LABELS:
            text = msgs.get(key, "")
            if text:
                lines.append(f"          {label + ':':<28}\"{text}\"")
        post = gl.get("post_battle", [])
        for i, pb in enumerate(post):
            lbl = "Post-battle:" if i == 0 else " " * 12
            lines.append(f"          {lbl:<28}\"{pb}\"")
        lines.append("          Initial battle:")
        _fmt_party(gl.get("party", []))
        if gl.get("rematch_const"):
            lines.append(f"          Battleground rematch:  [{gl['rematch_const']}]")
            _fmt_party(gl.get("rematch_party", []))
        lines.append("")

    return lines


# ---------------------------------------------------------------------------
# Static Encounter Index (Platinum)
# ---------------------------------------------------------------------------

_PT_ENCOUNTER_RE = re.compile(
    r"^\s*(?:"
    r"StartLegendaryBattle\s+(SPECIES_\w+)\s*,\s*(\d+)"
    r"|StartWildBattle\s+(SPECIES_\w+)\s*,\s*(\d+)"
    r"|ActivateRoamingPokemon\s+(ROAMING_SLOT_\w+)"
    r")\s*$",
    re.M,
)

_PT_MESSAGE_RE = re.compile(r"^\s*Message\s+(\w+)\s*$", re.M)

_PT_ROAMING_SLOT_SPECIES_CACHE: dict | None = None
_PT_ROAMING_SLOTS_FILE = BASE_DIR / "pokeplatinum/generated/roaming_slots.txt"


def _load_pt_roaming_slot_species() -> dict[str, str]:
    global _PT_ROAMING_SLOT_SPECIES_CACHE
    if _PT_ROAMING_SLOT_SPECIES_CACHE is not None:
        return _PT_ROAMING_SLOT_SPECIES_CACHE
    result: dict[str, str] = {}
    if _PT_ROAMING_SLOTS_FILE.exists():
        for line in _PT_ROAMING_SLOTS_FILE.read_text(encoding="utf-8").splitlines():
            slot = line.strip()
            if not slot or slot == "ROAMING_SLOT_MAX":
                continue
            species = slot.removeprefix("ROAMING_SLOT_").replace("_", " ").title()
            result[slot] = species
    _PT_ROAMING_SLOT_SPECIES_CACHE = result
    return result


def _pt_bfs_body(start_label: str, body_dict: dict) -> list[str]:
    seen: set[str] = {start_label}
    queue = [start_label]
    all_lines: list[str] = []
    while queue:
        cur = queue.pop(0)
        cur_lines = body_dict.get(cur, [])
        all_lines.extend(cur_lines)
        for line in cur_lines:
            m = re.match(r"\s*(?:GoTo\w*|Call\w*)\b.*?\b(\w+)\s*$", line)
            if m:
                tgt = m.group(1)
                if tgt not in seen and tgt in body_dict:
                    seen.add(tgt)
                    queue.append(tgt)
    return all_lines


def _pt_encounter_from_body(
    lines: list[str], texts: dict
) -> list[dict]:
    """Parse encounter commands from a list of script lines.

    Returns a list of encounter dicts (may be multiple for multi-roamer labels
    like Eterna City South House).
    """
    body = "\n".join(lines)
    results: list[dict] = []
    roaming_species_accumulated: list[str] = []

    # Iterate lines in order so we can find the last Message before a battle
    last_msg_text: str | None = None
    for line in lines:
        line = line.strip()
        # Track most-recent message (pre-battle text candidate)
        mm = _PT_MESSAGE_RE.match(line)
        if mm:
            t = texts.get(mm.group(1), "")
            if t:
                last_msg_text = t

        m = re.match(
            r"^StartLegendaryBattle\s+(SPECIES_\w+)\s*,\s*(\d+)", line
        )
        if not m:
            m = re.match(
                r"^StartWildBattle\s+(SPECIES_\w+)\s*,\s*(\d+)", line
            )
        if m:
            species = m.group(1).replace("SPECIES_", "").replace("_", " ").title()
            level = int(m.group(2))
            results.append({
                "species": species,
                "level": level,
                "battleable": True,
                "tags": [],
                "pre_battle_text": last_msg_text,
                "x": None, "z": None,
            })
            last_msg_text = None
            continue

        mr = re.match(r"^ActivateRoamingPokemon\s+(ROAMING_SLOT_\w+)", line)
        if mr:
            slot = mr.group(1)
            sp = _load_pt_roaming_slot_species().get(slot,
                 slot.replace("ROAMING_SLOT_", "").replace("_", " ").title())
            roaming_species_accumulated.append(sp)
            # Look for message AFTER this line (trigger text) — captured in next iteration
            # We'll flush accumulated roamers when we find a Message command after them

    # After the loop: if we accumulated roaming slots, emit them as a group
    # (The trigger message is usually emitted AFTER the ActivateRoamingPokemon calls)
    if roaming_species_accumulated:
        # Re-scan for first Message after the last ActivateRoamingPokemon
        after_last_activate = False
        trigger_text: str | None = None
        for line in lines:
            line = line.strip()
            if re.match(r"^ActivateRoamingPokemon\s+ROAMING_SLOT_\w+", line):
                after_last_activate = True
            elif after_last_activate:
                mm = _PT_MESSAGE_RE.match(line)
                if mm:
                    trigger_text = texts.get(mm.group(1), "")
                    break
        species_str = " / ".join(roaming_species_accumulated)
        results.append({
            "species": species_str,
            "level": None,
            "battleable": False,
            "tags": ["roaming trigger → Sinnoh"],
            "pre_battle_text": trigger_text or None,
            "x": None, "z": None,
        })

    return results


def _pt_static_encounters(map_entry: dict) -> list[dict]:
    """
    Find all scripted static Pokémon encounters for a Platinum map.

    Scans object_events, coord_events, and bg_events for scripts that BFS-reach
    StartLegendaryBattle, StartWildBattle, or ActivateRoamingPokemon.  Returns
    a list of encounter dicts ready for render_static_encounter_section().
    """
    constant = map_entry.get("constant", "")
    map_name = _map_name_from_constant(constant)
    if not map_name:
        return []

    table = _load_script_table(map_name)
    body_dict = _load_script_body(map_name)
    texts = _load_text(map_name)

    if not body_dict:
        return []

    # Map label → encounter info, built from full script scan
    label_to_enc: dict[str, list[dict]] = {}
    for label, label_lines in body_dict.items():
        bfs_lines = _pt_bfs_body(label, body_dict)
        body_text = "\n".join(bfs_lines)
        if not _PT_ENCOUNTER_RE.search(body_text):
            continue
        encs = _pt_encounter_from_body(bfs_lines, texts)
        if encs:
            label_to_enc[label] = encs

    if not label_to_enc:
        return []

    # For each event source, BFS to see which encounter label it reaches
    encounter_labels_claimed: set[str] = set()
    result: list[dict] = []

    def _claim(script_idx: int, x: int, z: int) -> None:
        """Try to associate encounter(s) at script_idx with coords (x, z)."""
        idx = script_idx - 1
        if idx < 0 or idx >= len(table):
            return
        entry_label = table[idx]
        bfs_lines = _pt_bfs_body(entry_label, body_dict)
        body_text = "\n".join(bfs_lines)
        if not _PT_ENCOUNTER_RE.search(body_text):
            return
        # Find which encounter labels are reachable
        seen: set[str] = set()
        q = [entry_label]
        while q:
            lbl = q.pop(0)
            if lbl in seen:
                continue
            seen.add(lbl)
            if lbl in label_to_enc:
                if lbl not in encounter_labels_claimed:
                    # First event to reach this label — record its coords
                    for enc in label_to_enc[lbl]:
                        enc_copy = dict(enc)
                        enc_copy["x"] = x
                        enc_copy["z"] = z
                        result.append(enc_copy)
                encounter_labels_claimed.add(lbl)
            for ln in body_dict.get(lbl, []):
                m = re.match(r"\s*(?:GoTo\w*|Call\w*)\b.*?\b(\w+)\s*$", ln)
                if m and m.group(1) not in seen and m.group(1) in body_dict:
                    q.append(m.group(1))

    # Object events
    for obj in map_entry.get("object_events", []):
        script = obj.get("script", "")
        if isinstance(script, int):
            _claim(script, obj.get("x", 0), obj.get("z", 0))

    # Coord events (Platinum: direct integer index into script table)
    for ce in map_entry.get("coord_events", []):
        script = ce.get("script", 0)
        if isinstance(script, int) and script > 0:
            _claim(script, ce.get("x", 0), ce.get("z", 0))

    # bg_events type 0
    for bg in map_entry.get("bg_events", []):
        if bg.get("type") == 0:
            script = bg.get("script", 0)
            if isinstance(script, int) and script > 0:
                _claim(script, bg.get("x", 0), bg.get("z", 0))

    # Encounter labels still unclaimed (e.g. Dialga/Palkia triggered by cutscene)
    for label, encs in label_to_enc.items():
        if label not in encounter_labels_claimed:
            for enc in encs:
                if enc not in result:
                    result.append(dict(enc))

    # Deduplicate species lists (ActivateRoamingPokemon may call the same slot N times)
    for enc in result:
        sp = enc["species"]
        if " / " in sp:
            unique_sp = list(dict.fromkeys(sp.split(" / ")))
            enc["species"] = " / ".join(unique_sp)

    # For roaming triggers at the same (x, z), keep only the entry with the most species.
    # Scripts often have one combined activation block plus per-bird conditional branches —
    # the combined entry is always a superset of the individual ones.
    from itertools import groupby as _groupby

    roaming_by_coord: dict[tuple, list] = {}
    non_roaming: list[dict] = []
    for enc in result:
        tags = enc.get("tags", [])
        is_roaming = any("roaming" in t for t in tags)
        if is_roaming:
            key = (enc.get("x"), enc.get("z"))
            roaming_by_coord.setdefault(key, []).append(enc)
        else:
            non_roaming.append(enc)

    merged_roaming: list[dict] = []
    for (x, z), group in roaming_by_coord.items():
        best = max(group, key=lambda e: len(e["species"].split(" / ")))
        merged_roaming.append(best)

    # Deduplicate non-roaming by (species, x, z)
    seen_keys: set[tuple] = set()
    deduped_non_roaming: list[dict] = []
    for enc in non_roaming:
        key = (enc["species"], enc.get("x"), enc.get("z"))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped_non_roaming.append(enc)

    return deduped_non_roaming + merged_roaming


def render_static_encounter_section(encounters: list,
                                    col_off: int = 0,
                                    row_off: int = 0,
                                    overlapped: dict | None = None) -> list[str]:
    if not encounters:
        return []
    SEP = "=" * 70
    lines = ["", SEP, "  Static Encounter Index  (Platinum)", SEP, ""]
    overlapped = overlapped or {}
    for enc in encounters:
        species = enc["species"]
        level   = enc.get("level")
        tags    = enc.get("tags", [])
        pre_txt = enc.get("pre_battle_text") or ""

        lv_str  = f"  Lv {level}" if level is not None else ""
        tag_str = ("  " + "  ".join(f"[{t}]" for t in tags)) if tags else ""
        lines.append(f"  {species}{lv_str}{tag_str}")

        x = enc.get("x")
        z = enc.get("z")
        if x is not None and z is not None:
            ov = overlapped.get((x, z), " ")
            ov_note = f"  [@ overlaps {repr(ov)}]" if ov != " " else ""
            coord = f"  (col={x - col_off:>3}, row={z - row_off:>3}){ov_note}"
        else:
            coord = ""

        if pre_txt and coord:
            lines.append(f"{coord}  \"{pre_txt}\"")
        elif pre_txt:
            lines.append(f"  \"{pre_txt}\"")
        elif coord:
            lines.append(coord)

        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Coin Prize Exchange
# ---------------------------------------------------------------------------

_PT_PRIZE_C = BASE_DIR / "pokeplatinum/src/scrcmd_game_corner_prize.c"
_PT_ITEM_C       = BASE_DIR / "pokeplatinum/src/item.c"
_PT_ITEMS_DIR    = BASE_DIR / "pokeplatinum/res/items/data"
_PT_TM_MAP_H     = BASE_DIR / "pokeplatinum/res/items/item_tm_move_map.h"
_PT_TM_MOVE_CACHE: dict[str, str] = {}
_PT_TM_MOVE_LOADED = False

# Matches both:
#   [TMHM_ID(TM01)] = MOVE_FOCUS_PUNCH,   (inline-array decomp version)
#   [0] = MOVE_FOCUS_PUNCH,               (generated-header version)
_PT_TM_ENTRY_RE = re.compile(
    r"\[TMHM_ID\(TM(\d+)\)\]\s*=\s*(MOVE_\w+)"
)


def _pt_tm_move_name(item_const: str) -> str | None:
    """ITEM_TM90 → 'Substitute'.

    Route 1: parse sTMHMMoves[] inline in src/item.c via [TMHM_ID(TM01)]=MOVE_X
             entries — present in every source-only checkout of this decomp.
    Route 2: parse res/items/data/tm*.json teachesMove fields (some checkouts).
    Route 3: parse res/items/item_tm_move_map.h — the generated C header
             (built checkouts only).
    Hard-warns if all three yield zero entries (silent cache = invisible bug).
    """
    global _PT_TM_MOVE_LOADED
    if not _PT_TM_MOVE_LOADED:
        _PT_TM_MOVE_LOADED = True

        # Route 1 — inline sTMHMMoves[] in src/item.c (checkout-independent)
        if _PT_ITEM_C.exists():
            text = _PT_ITEM_C.read_text(encoding="utf-8")
            start = text.find("sTMHMMoves[]")
            if start != -1:
                body = text[start: text.find("};", start)]
                for m in _PT_TM_ENTRY_RE.finditer(body):
                    num  = int(m.group(1))
                    _PT_TM_MOVE_CACHE[f"ITEM_TM{num:02d}"] = move_display(m.group(2))

        # Route 2 — per-file JSON (pokeplatinum/res/items/data/tm*.json)
        if not _PT_TM_MOVE_CACHE and _PT_ITEMS_DIR.exists():
            for path in _PT_ITEMS_DIR.glob("tm*.json"):
                try:
                    with open(path, encoding="utf-8") as f:
                        d = json.load(f)
                    teaches = d.get("teachesMove", "")
                    if teaches and teaches.startswith("MOVE_"):
                        key = f"ITEM_{path.stem.upper()}"
                        _PT_TM_MOVE_CACHE[key] = move_display(teaches)
                except Exception:
                    pass

        # Route 3 — generated C header included by item.c (built checkouts)
        if not _PT_TM_MOVE_CACHE and _PT_TM_MAP_H.exists():
            text = _PT_TM_MAP_H.read_text(encoding="utf-8")
            start = text.find("sTMHMMoves[]")
            if start != -1:
                body = text[start: text.find("};", start)]
                for i, m in enumerate(re.finditer(r"(MOVE_\w+)", body), start=1):
                    _PT_TM_MOVE_CACHE[f"ITEM_TM{i:02d}"] = move_display(m.group(1))

        if not _PT_TM_MOVE_CACHE:
            import warnings
            warnings.warn(
                "platinum_data: TM move table is empty — "
                "src/item.c has no inline sTMHMMoves[], "
                "res/items/data/tm*.json not found, and "
                "res/items/item_tm_move_map.h not found. "
                "TM names will be missing from Platinum output.",
                stacklevel=2,
            )

    if not item_const.startswith("ITEM_TM"):
        return None
    return _PT_TM_MOVE_CACHE.get(item_const)


def pt_game_corner_prizes(map_constant: str) -> list[dict]:
    """Return coin prizes for MAP_HEADER_VEILSTONE_CITY_PRIZE_EXCHANGE, else []."""
    if map_constant != "MAP_HEADER_VEILSTONE_CITY_PRIZE_EXCHANGE":
        return []
    if not _PT_PRIZE_C.exists():
        return []
    text = _PT_PRIZE_C.read_text(encoding="utf-8")
    prizes: list[dict] = []
    for m in re.finditer(r"\{\s*(ITEM_\w+)\s*,\s*(\d+)\s*\}", text):
        item_const = m.group(1)
        cost = int(m.group(2))
        move = _pt_tm_move_name(item_const)
        if move:
            num = item_const.removeprefix("ITEM_TM")
            name = f"TM{num} ({move})"
        else:
            name = _item_name(item_const)
        prizes.append({"name": name, "coins": cost, "kind": "item"})
    return prizes


def render_coin_prize_section(prizes: list, col_off: int = 0, row_off: int = 0) -> list[str]:
    if not prizes:
        return []
    SEP = "=" * 70
    lines = ["", SEP, "  Coin Prize Exchange  (Platinum)", SEP, ""]
    for p in prizes:
        coins_str = f"{p['coins']:,}"
        lines.append(f"  {p['name']:<35}  {coins_str} coins")
    lines.append("")
    return lines
