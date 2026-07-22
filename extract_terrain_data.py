#!/usr/bin/env python3
"""
Extract tile/terrain data (map shape, collision, tile behaviors) for every map
in pokeemerald, pokeheartgold, and pokeplatinum.

This is complementary to extract_maps.py which captures events/metadata.
Here we capture the actual tile grid: what behavior each cell has, whether it
is passable, and the raw metatile ID (pokeemerald only).

Output:
  tiles_pokeemerald.json
  tiles_pokeheartgold.json
  tiles_pokeplatinum.json

Data formats
------------
pokeemerald
  - Layout data: data/layouts/<Name>/map.bin  (uint16 LE per cell)
  - Dimensions:  data/layouts/layouts.json
  - Each uint16:
      bits  9-0  : metatile index (0-based, primary tileset if < 0x200, else secondary)
      bits 11-10 : collision  (0 = passable, non-zero = blocked)
      bits 15-12 : elevation
  - Behavior per metatile: data/tilesets/{primary,secondary}/<name>/metatile_attributes.bin
      4 bytes per metatile: uint16 behavior, uint8 layer_type, uint8 padding
  - Named behaviors: include/constants/metatile_behaviors.h  (enum, 0-indexed)

pokeheartgold / pokeplatinum (Gen 4 engine, same land_data format)
  - Each map is a 32×32 grid of uint16 terrain attributes
  - Each uint16:
      bit  15   : collision (1 = impassable / out-of-bounds, 0 = passable)
      bits  7-0 : tile behavior index
  - pokeheartgold land_data NARC: files/a/0/6/5  (676 files)
      The map matrix (files/fielddata/mapmatrix/map_matrix/) maps each zone to
      a land_data index via the landDataIDs field.
  - pokeplatinum land_data: res/field/maps/data/map_data_NNN.bin
      terrain attributes at offset 0x10, 32×32 uint16 array (2048 bytes)
      The map matrix JSONs (res/field/matrices/map_matrix_NNN.json) list
      landDataIDs per cell.
"""

import json
import os
import re
import struct
from pathlib import Path

BASE_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_narc(path: Path):
    """
    Parse a Nintendo DS NARC archive and return a list of raw bytes objects,
    one per contained file.
    """
    data = path.read_bytes()
    if data[:4] != b"NARC":
        raise ValueError(f"Not a NARC file: {path}")
    # NARC header: magic(4) + bom(2) + version(2) + filesize(4) + header_size(2) + num_sections(2)
    btaf_offset = 16  # NARC header is always 16 bytes
    btaf_magic = data[btaf_offset:btaf_offset + 4]
    if btaf_magic != b"BTAF":
        raise ValueError(f"Expected BTAF section in {path}")
    btaf_size = struct.unpack_from("<I", data, btaf_offset + 4)[0]
    num_files = struct.unpack_from("<I", data, btaf_offset + 8)[0]

    file_offsets = []
    for i in range(num_files):
        start = struct.unpack_from("<I", data, btaf_offset + 12 + i * 8)[0]
        end   = struct.unpack_from("<I", data, btaf_offset + 12 + i * 8 + 4)[0]
        file_offsets.append((start, end))

    # Skip BTNF section to reach GMIF
    btnf_offset = btaf_offset + btaf_size
    btnf_size   = struct.unpack_from("<I", data, btnf_offset + 4)[0]
    gmif_offset = btnf_offset + btnf_size
    if data[gmif_offset:gmif_offset + 4] != b"GMIF":
        raise ValueError(f"Expected GMIF section in {path}")
    data_start = gmif_offset + 8  # GMIF header is 8 bytes

    return [data[data_start + s : data_start + e] for s, e in file_offsets]


def parse_land_data(raw: bytes):
    """
    Parse a Gen 4 land_data entry.
    Returns a 32×32 list-of-lists of dicts:
      { "behavior": int, "passable": bool, "raw": int }
    """
    if len(raw) < 16:
        return None
    terrain_size = struct.unpack_from("<I", raw, 0)[0]
    if terrain_size != 2048:
        return None  # unexpected format

    grid = []
    for row in range(32):
        r = []
        for col in range(32):
            idx = 0x10 + (row * 32 + col) * 2
            val = struct.unpack_from("<H", raw, idx)[0]
            behavior = val & 0xFF
            passable  = (val & 0x8000) == 0
            r.append({"behavior": behavior, "passable": passable, "raw": val})
        grid.append(r)
    return grid


# ---------------------------------------------------------------------------
# pokeemerald
# ---------------------------------------------------------------------------

def load_emerald_behavior_names() -> dict:
    """
    Parse include/constants/metatile_behaviors.h to build
    behavior_value (int) -> name (str).
    """
    h_file = BASE_DIR / "pokeemerald" / "include" / "constants" / "metatile_behaviors.h"
    names = {}
    if not h_file.exists():
        return names
    # The file contains an enum; values are implicit (0, 1, 2, …)
    enum_re = re.compile(r"^\s*(MB_\w+)")
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
                names[idx] = m.group(1)
                idx += 1
    return names


def load_emerald_tileset_behaviors(tileset_path: Path) -> dict:
    """
    Load metatile_attributes.bin for one tileset.
    Returns metatile_index -> { "behavior": int, "layer_type": int }

    The uint16 attribute word is packed as:
      bits  7-0  : behavior (MB_* enum value)
      bits 11-8  : unused
      bits 15-12 : layer_type
    """
    attr_file = tileset_path / "metatile_attributes.bin"
    result = {}
    if not attr_file.exists():
        return result
    raw = attr_file.read_bytes()
    num = len(raw) // 2          # each entry is a u16 (2 bytes), declared as INCBIN_U16
    for i in range(num):
        attr_word  = struct.unpack_from("<H", raw, i * 2)[0]
        behavior   = attr_word & 0x00FF          # bits 7-0
        layer_type = (attr_word & 0xF000) >> 12  # bits 15-12
        result[i] = {"behavior": behavior, "layer_type": layer_type}
    return result


def load_emerald_metatile_labels() -> dict:
    """
    Parse include/constants/metatile_labels.h to build
    metatile_id (int) -> label_name (str).
    Format: #define METATILE_<Tileset>_<Name>  0x<HEX>
    """
    h_file = BASE_DIR / "pokeemerald" / "include" / "constants" / "metatile_labels.h"
    labels = {}
    if not h_file.exists():
        return labels
    define_re = re.compile(r"^#define\s+(METATILE_\w+)\s+(0x[0-9A-Fa-f]+)")
    for line in h_file.read_text(encoding="utf-8").splitlines():
        m = define_re.match(line.strip())
        if m:
            labels[int(m.group(2), 16)] = m.group(1)
    return labels


def extract_pokeemerald_tiles():
    root = BASE_DIR / "pokeemerald"
    layouts_json = root / "data" / "layouts" / "layouts.json"
    tilesets_dir = root / "data" / "tilesets"

    # Load behavior name lookup
    behavior_names = load_emerald_behavior_names()

    # Load metatile label lookup (sparse — only tiles referenced by game code)
    metatile_labels = load_emerald_metatile_labels()

    # Load all layouts metadata
    with open(layouts_json, "r", encoding="utf-8") as f:
        layouts_data = json.load(f)

    # Build layout_id -> layout_info lookup
    layout_lookup = {lay["id"]: lay for lay in layouts_data["layouts"]}

    # Cache tileset behaviors: tileset_name -> {metatile_idx: {...}}
    tileset_cache = {}

    def get_tileset(name: str, is_secondary: bool) -> dict:
        if name in tileset_cache:
            return tileset_cache[name]
        # Convert "gTileset_General" -> "general"
        folder = re.sub(r"^gTileset_", "", name)
        # CamelCase to snake_case
        folder = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", folder).lower()
        kind = "secondary" if is_secondary else "primary"
        path = tilesets_dir / kind / folder
        behaviors = load_emerald_tileset_behaviors(path)
        tileset_cache[name] = behaviors
        return behaviors

    # Map layout_id -> map names (from maps directory)
    maps_dir = root / "data" / "maps"
    layout_to_maps = {}
    for map_dir in maps_dir.iterdir():
        if not map_dir.is_dir():
            continue
        map_json_path = map_dir / "map.json"
        if not map_json_path.exists():
            continue
        with open(map_json_path, "r", encoding="utf-8") as f:
            mdata = json.load(f)
        lid = mdata.get("layout")
        if lid:
            layout_to_maps.setdefault(lid, []).append(map_dir.name)

    all_tiles = []
    layouts_dir = root / "data" / "layouts"

    for layout in layouts_data["layouts"]:
        lid        = layout["id"]
        name       = layout["name"]
        width      = layout["width"]
        height     = layout["height"]
        primary_ts = layout.get("primary_tileset", "")
        secondary_ts = layout.get("secondary_tileset", "")
        bin_path   = root / layout["blockdata_filepath"]

        if not bin_path.exists():
            continue

        raw = bin_path.read_bytes()
        expected = width * height * 2
        if len(raw) < expected:
            continue

        # Load tileset behaviors
        primary_behaviors   = get_tileset(primary_ts,   is_secondary=False)
        secondary_behaviors = get_tileset(secondary_ts, is_secondary=True)

        # Primary tileset covers metatile indices 0x000–0x1FF (0–511)
        # Secondary tileset covers 0x200–0x3FF (512–1023)
        PRIMARY_LIMIT = 0x200

        grid = []
        for row in range(height):
            r = []
            for col in range(width):
                offset = (row * width + col) * 2
                val = struct.unpack_from("<H", raw, offset)[0]

                metatile_id = val & 0x3FF
                collision   = (val >> 10) & 0x3
                elevation   = (val >> 12) & 0xF
                passable    = collision == 0

                # Look up behavior
                if metatile_id < PRIMARY_LIMIT:
                    beh_info = primary_behaviors.get(metatile_id, {})
                else:
                    sec_idx  = metatile_id - PRIMARY_LIMIT
                    beh_info = secondary_behaviors.get(sec_idx, {})

                behavior_val  = beh_info.get("behavior", 0)
                behavior_name = behavior_names.get(behavior_val, f"MB_{behavior_val}")
                layer_type    = beh_info.get("layer_type", 0)

                r.append({
                    "metatile_id":    metatile_id,
                    "metatile_label": metatile_labels.get(metatile_id),
                    "collision":      collision,
                    "elevation":      elevation,
                    "passable":       passable,
                    "behavior":       behavior_val,
                    "behavior_name":  behavior_name,
                    "layer_type":     layer_type,
                })
            grid.append(r)

        entry = {
            "layout_id":        lid,
            "layout_name":      name,
            "width":            width,
            "height":           height,
            "primary_tileset":  primary_ts,
            "secondary_tileset": secondary_ts,
            "maps":             layout_to_maps.get(lid, []),
            "grid":             grid,
        }
        all_tiles.append(entry)

    print(f"[pokeemerald] Extracted tile data for {len(all_tiles)} layouts")
    return all_tiles


# ---------------------------------------------------------------------------
# pokeheartgold
# ---------------------------------------------------------------------------

def load_hg_behavior_names() -> dict:
    """
    Parse include/constants/metatile_behavior.h to build
    behavior_value (int) -> name (str).
    """
    h_file = BASE_DIR / "pokeheartgold" / "include" / "constants" / "metatile_behavior.h"
    names = {}
    if not h_file.exists():
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
                names[idx] = m.group(1)
                idx += 1
    return names


def parse_hg_map_matrix(raw: bytes):
    """
    Parse a pokeheartgold map_matrix binary.
    Returns dict with height, width, land_data_ids (list of lists).
    """
    if len(raw) < 5:
        return None
    height       = raw[0]
    width        = raw[1]
    has_headers  = raw[2]
    has_alts     = raw[3]
    prefix_len   = raw[4]
    offset = 5 + prefix_len

    # mapHeaderIDs
    if has_headers:
        offset += 2 * height * width
    # altitudes
    if has_alts:
        offset += height * width
    # landDataIDs
    land_ids = []
    for row in range(height):
        r = []
        for col in range(width):
            lid = struct.unpack_from("<H", raw, offset + (row * width + col) * 2)[0]
            r.append(lid)
        land_ids.append(r)

    return {"height": height, "width": width, "land_data_ids": land_ids}


def parse_hg_maps_h(root: Path) -> dict:
    """
    Parse include/constants/maps.h to build:
      zone_id (int) -> { "constant": "MAP_...", "internal_code": "..." }
    """
    maps_h = root / "include" / "constants" / "maps.h"
    id_to_info = {}
    if not maps_h.exists():
        return id_to_info
    pattern = re.compile(r"#define\s+(MAP_\w+)\s+(\d+)\s*(?://\s*(.+))?")
    for line in maps_h.read_text(encoding="utf-8").splitlines():
        m = pattern.match(line.strip())
        if m:
            constant      = m.group(1)
            zone_id       = int(m.group(2))
            internal_code = (m.group(3) or "").strip()
            id_to_info[zone_id] = {
                "constant":      constant,
                "internal_code": internal_code,
            }
    return id_to_info


def extract_pokeheartgold_tiles():
    root = BASE_DIR / "pokeheartgold"
    behavior_names = load_hg_behavior_names()

    # Load land_data NARC (a/0/6/5)
    land_narc_path = root / "files" / "a" / "0" / "6" / "5"
    print(f"[pokeheartgold] Loading land_data NARC ({land_narc_path})...")
    land_files = parse_narc(land_narc_path)
    print(f"[pokeheartgold] {len(land_files)} land_data entries")

    # Load map matrix NARC
    matrix_narc_path = root / "files" / "fielddata" / "mapmatrix" / "map_matrix"
    # The map_matrix directory contains individual .bin files named map_matrix_NNNN_CODE.bin
    matrix_dir = root / "files" / "fielddata" / "mapmatrix" / "map_matrix"

    # Build matrix_id -> parsed matrix
    matrix_cache = {}
    for bin_file in sorted(matrix_dir.glob("map_matrix_*.bin")):
        m = re.match(r"map_matrix_(\d+)_(.+)\.bin$", bin_file.name)
        if m:
            matrix_id = int(m.group(1))
            raw = bin_file.read_bytes()
            parsed = parse_hg_map_matrix(raw)
            if parsed:
                matrix_cache[matrix_id] = parsed

    # Load zone info from maps.h
    id_to_info = parse_hg_maps_h(root)

    # Load zone_event JSONs to get zone_id -> matrix_id mapping
    # The zone_event files are named NNN_CODE.json
    zone_event_dir = root / "files" / "fielddata" / "eventdata" / "zone_event"

    # We need to know which matrix each zone uses.
    # This comes from the map header. Parse include/constants/maps.h for zone IDs,
    # then parse the map header to get matrix IDs.
    # The map header is in src/map_header.c or a data file.
    # In HG/SS the map header data is in files/fielddata/maptable/mapname.bin
    # and the zone->matrix mapping is in the zone table.
    # Let's parse the zone table from the C source.
    zone_to_matrix = parse_hg_zone_to_matrix(root)

    all_tiles = []
    for json_file in sorted(zone_event_dir.glob("*.json")):
        stem = json_file.stem
        parts = stem.split("_", 1)
        try:
            zone_id = int(parts[0])
        except ValueError:
            continue

        info = id_to_info.get(zone_id, {})
        matrix_id = zone_to_matrix.get(zone_id)
        matrix = matrix_cache.get(matrix_id) if matrix_id is not None else None

        if matrix is None:
            # No matrix data available for this zone
            all_tiles.append({
                "zone_id":       zone_id,
                "file_code":     parts[1] if len(parts) > 1 else stem,
                "constant":      info.get("constant"),
                "internal_code": info.get("internal_code"),
                "matrix_id":     matrix_id,
                "grid":          None,
                "note":          "no matrix data",
            })
            continue

        # Build combined grid from all land_data tiles in the matrix
        # Each matrix cell references one 32×32 land_data tile
        h = matrix["height"]
        w = matrix["width"]
        land_ids = matrix["land_data_ids"]

        # Build a combined grid: (h*32) rows × (w*32) cols
        combined = []
        for mr in range(h):
            # 32 rows per land tile
            for tr in range(32):
                row = []
                for mc in range(w):
                    lid = land_ids[mr][mc]
                    if lid >= len(land_files):
                        # Out-of-bounds land data ID
                        for tc in range(32):
                            row.append(None)
                        continue
                    raw = land_files[lid]
                    grid = parse_land_data(raw)
                    if grid is None:
                        for tc in range(32):
                            row.append(None)
                        continue
                    for tc in range(32):
                        cell = grid[tr][tc]
                        bname = behavior_names.get(cell["behavior"], f"TILE_BEHAVIOR_{cell['behavior']}")
                        row.append({
                            "behavior":      cell["behavior"],
                            "behavior_name": bname,
                            "passable":      cell["passable"],
                            "raw":           cell["raw"],
                        })
                combined.append(row)

        all_tiles.append({
            "zone_id":       zone_id,
            "file_code":     parts[1] if len(parts) > 1 else stem,
            "constant":      info.get("constant"),
            "internal_code": info.get("internal_code"),
            "matrix_id":     matrix_id,
            "grid_width":    w * 32,
            "grid_height":   h * 32,
            "grid":          combined,
        })

    print(f"[pokeheartgold] Extracted tile data for {len(all_tiles)} zones")
    return all_tiles


def parse_hg_zone_to_matrix(root: Path) -> dict:
    """
    Parse src/data/map_headers.h to get zone_id -> matrix_id.

    The matrixId field uses NARC constants of the form:
      NARC_map_matrix_map_matrix_NNNN_CODE_bin
    where NNNN is the 4-digit zero-padded matrix index.

    Returns zone_id (int) -> matrix_id (int).
    """
    result = {}

    # Build zone constant -> zone_id from maps.h
    maps_h = root / "include" / "constants" / "maps.h"
    const_to_id = {}
    if maps_h.exists():
        pat = re.compile(r"#define\s+(MAP_\w+)\s+(\d+)")
        for line in maps_h.read_text(encoding="utf-8").splitlines():
            m = pat.match(line.strip())
            if m:
                const_to_id[m.group(1)] = int(m.group(2))

    # Parse src/data/map_headers.h
    map_headers_file = root / "src" / "data" / "map_headers.h"
    if not map_headers_file.exists():
        return result

    content = map_headers_file.read_text(encoding="utf-8", errors="replace")

    # Match blocks like: [MAP_ROUTE_1] = { ... .matrixId = NARC_map_matrix_map_matrix_NNNN_..._bin, ... }
    # The blocks use nested braces for the struct literal, so we need a careful pattern.
    # Since the struct body doesn't contain nested braces, a simple [^}]+ works.
    block_pattern = re.compile(
        r"\[(?P<zone>MAP_\w+)\]\s*=\s*\{(?P<body>[^}]+)\}",
        re.DOTALL,
    )
    # matrixId can be a NARC constant or a plain integer
    matrix_narc = re.compile(r"\.matrixId\s*=\s*NARC_map_matrix_map_matrix_(\d+)")
    matrix_int  = re.compile(r"\.matrixId\s*=\s*(\d+)")

    for block in block_pattern.finditer(content):
        zone_const = block.group("zone")
        body = block.group("body")
        zone_id = const_to_id.get(zone_const)
        if zone_id is None:
            continue
        m = matrix_narc.search(body)
        if m:
            result[zone_id] = int(m.group(1))
            continue
        m = matrix_int.search(body)
        if m:
            result[zone_id] = int(m.group(1))

    return result


# ---------------------------------------------------------------------------
# pokeplatinum
# ---------------------------------------------------------------------------

def load_pt_behavior_names() -> dict:
    """
    Parse include/constants/field/map_tile_behaviors.h to build
    behavior_value (int) -> name (str).
    """
    h_file = BASE_DIR / "pokeplatinum" / "include" / "constants" / "field" / "map_tile_behaviors.h"
    names = {}
    if not h_file.exists():
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
                # Skip lines with explicit value assignments (e.g. = 255)
                if "=" in line:
                    # Parse explicit value
                    val_m = re.search(r"=\s*(\d+)", line)
                    if val_m:
                        idx = int(val_m.group(1))
                names[idx] = m.group(1)
                idx += 1
    return names


def parse_pt_map_matrix(json_path: Path) -> dict:
    """
    Parse a pokeplatinum map_matrix JSON.
    Returns { name, height, width, maps (list of lists of header IDs),
              land_data_ids (list of lists of ints) }
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    maps_grid = data.get("maps", [])
    # maps_grid is a list of rows, each row is a list of header ID strings
    # e.g. [["MAP_HEADER_JUBILIFE_CITY"], ["MAP_HEADER_ROUTE_202", ...], ...]
    height = len(maps_grid)
    width  = max((len(row) for row in maps_grid), default=0)

    return {
        "name":   data.get("name", ""),
        "height": height,
        "width":  width,
        "maps":   maps_grid,
    }


def extract_pokeplatinum_tiles():
    root = BASE_DIR / "pokeplatinum"
    behavior_names = load_pt_behavior_names()

    maps_data_dir = root / "res" / "field" / "maps" / "data"
    matrices_dir  = root / "res" / "field" / "matrices"
    events_dir    = root / "res" / "field" / "events"

    # Load all map_data_NNN.bin files indexed by number
    land_data_cache = {}
    for bin_file in maps_data_dir.glob("map_data_*.bin"):
        m = re.match(r"map_data_(\d+)\.bin$", bin_file.name)
        if m:
            idx = int(m.group(1))
            land_data_cache[idx] = bin_file.read_bytes()

    # Load all map matrix JSONs indexed by number
    matrix_cache = {}
    for json_file in matrices_dir.glob("map_matrix_*.json"):
        m = re.match(r"map_matrix_(\d+)\.json$", json_file.name)
        if m:
            idx = int(m.group(1))
            matrix_cache[idx] = parse_pt_map_matrix(json_file)

    # Parse map_headers.h to get constant -> { matrixID, ... }
    header_props = parse_pt_map_headers(root)

    # Build constant -> matrix_id lookup
    const_to_matrix = {}
    for const, props in header_props.items():
        mid = props.get("mapMatrixID")
        if mid is not None:
            const_to_matrix[const] = mid

    # Parse map_headers.txt for index -> constant
    headers_txt = root / "generated" / "map_headers.txt"
    index_to_const = {}
    if headers_txt.exists():
        for i, line in enumerate(headers_txt.read_text(encoding="utf-8").splitlines()):
            c = line.strip()
            if c:
                index_to_const[i] = c

    all_tiles = []
    for json_file in sorted(events_dir.glob("events_*.json")):
        stem = json_file.stem  # e.g. "events_jubilife_city"
        map_key  = stem.removeprefix("events_").upper()
        constant = f"MAP_HEADER_{map_key}"
        name     = map_key.replace("_", " ").title()

        matrix_id_raw = const_to_matrix.get(constant)
        # matrix_id_raw may be a string like "map_matrix_003" or an int
        matrix_id = None
        if matrix_id_raw is not None:
            if isinstance(matrix_id_raw, int):
                matrix_id = matrix_id_raw
            else:
                mm = re.search(r"(\d+)", str(matrix_id_raw))
                if mm:
                    matrix_id = int(mm.group(1))

        matrix = matrix_cache.get(matrix_id) if matrix_id is not None else None

        if matrix is None:
            all_tiles.append({
                "constant":  constant,
                "name":      name,
                "matrix_id": matrix_id,
                "grid":      None,
                "note":      "no matrix data",
            })
            continue

        h = matrix["height"]
        w = matrix["width"]
        maps_grid = matrix["maps"]

        # Build combined grid
        # Each cell in the matrix references a MAP_HEADER constant.
        # We need to find the land_data index for each cell.
        # The matrix JSON "maps" field gives header constants per cell.
        # We then look up the land_data index from the map_headers.h matrixID
        # ... but actually the land_data index IS the matrix cell index in the
        # map_matrix.narc. For Platinum, the matrix JSON doesn't directly list
        # land_data IDs — those come from the binary map_matrix format.
        # However, the decomp has already decoded the matrix to JSON with just
        # the header names. The land_data files are numbered sequentially and
        # correspond to the order they appear in the land_data.narc.
        #
        # The approach: use the map_matrix JSON "maps" grid to identify which
        # map header is at each cell, then use the header's matrixID to find
        # the correct land_data file. But that's circular.
        #
        # Better approach: the land_data files in res/field/maps/data/ are
        # numbered by their index in the original NARC. The matrix JSON
        # "maps" field lists header constants. We need the landDataIDs from
        # the binary matrix format — but those aren't in the JSON.
        #
        # Fallback: use the map_data files directly indexed by the matrix
        # cell position. For single-cell matrices (most indoor maps), there's
        # exactly one land_data file whose index matches the map_header index.
        #
        # For the overworld matrix (many cells), we use the matrix cell order
        # to index into the land_data files. The land_data NARC files are
        # ordered the same way as the matrix cells in the original game.
        # The decomp preserves this ordering in map_data_NNN.bin where NNN
        # is the land_data index.
        #
        # We reconstruct the land_data index by looking at the header's
        # eventsArchiveID (which matches the events file index) — but that's
        # also not directly available.
        #
        # Simplest reliable approach: for each cell in the matrix, find the
        # land_data file that corresponds to it. The platinum decomp stores
        # land_data files as map_data_NNN.bin where NNN is the sequential
        # index from the original NARC. We use the matrix cell's header
        # constant to find the correct file via the map_headers.h
        # eventsArchiveID field (which is the same index as the events file).
        # Since events files are named events_<map_name>.json and land_data
        # files are map_data_NNN.bin, we need a mapping.
        #
        # The cleanest solution: parse the binary map_matrix files to get
        # the actual landDataIDs. The binary files are NOT present in the
        # decomp (they're regenerated). Instead, we use the map_data files
        # directly: map_data_NNN.bin where NNN corresponds to the land_data
        # NARC index. The matrix JSON lists header constants per cell, and
        # the map_headers.h has a mapMatrixID per header. We can use the
        # eventsArchiveID to cross-reference.
        #
        # FINAL APPROACH: Use the map_headers.h eventsArchiveID field.
        # The events archive ID matches the index of the events_*.json file
        # (and also the land_data index for that specific map tile).
        # For multi-tile matrices, each cell has its own header and thus
        # its own land_data index.

        # Pre-compute parsed grids for each matrix cell (avoids re-parsing 32x per cell)
        cell_grids = []
        for mr in range(h):
            row_headers = maps_grid[mr] if mr < len(maps_grid) else []
            cell_row = []
            for mc in range(w):
                cell_const = row_headers[mc] if mc < len(row_headers) else None
                land_idx = get_pt_land_data_index(cell_const, header_props, index_to_const)
                raw = land_data_cache.get(land_idx) if land_idx is not None else None
                cell_row.append(parse_land_data(raw) if raw else None)
            cell_grids.append(cell_row)

        combined = []
        for mr in range(h):
            for tr in range(32):
                row = []
                for mc in range(w):
                    grid = cell_grids[mr][mc]
                    for tc in range(32):
                        if grid is None:
                            row.append(None)
                        else:
                            cell = grid[tr][tc]
                            bname = behavior_names.get(cell["behavior"],
                                                       f"TILE_BEHAVIOR_{cell['behavior']}")
                            row.append({
                                "behavior":      cell["behavior"],
                                "behavior_name": bname,
                                "passable":      cell["passable"],
                                "raw":           cell["raw"],
                            })
                combined.append(row)

        all_tiles.append({
            "constant":    constant,
            "name":        name,
            "matrix_id":   matrix_id,
            "grid_width":  w * 32,
            "grid_height": h * 32,
            "grid":        combined,
        })

    print(f"[pokeplatinum] Extracted tile data for {len(all_tiles)} maps")
    return all_tiles


def parse_pt_map_headers(root: Path) -> dict:
    """
    Parse include/data/map_headers.h to extract per-map properties.
    Returns constant -> { mapMatrixID (int), eventsArchiveID (int), ... }
    """
    header_file = root / "include" / "data" / "map_headers.h"
    result = {}
    if not header_file.exists():
        return result

    content = header_file.read_text(encoding="utf-8")
    block_pattern = re.compile(
        r"\[(?P<constant>MAP_HEADER_\w+)\]\s*=\s*\{(?P<body>[^}]+)\}",
        re.DOTALL,
    )
    field_pattern = re.compile(r"\.\s*(\w+)\s*=\s*([^,\n]+)")

    for block in block_pattern.finditer(content):
        constant = block.group("constant")
        body = block.group("body")
        props = {}
        for field_m in field_pattern.finditer(body):
            key = field_m.group(1)
            val = field_m.group(2).strip().rstrip(",").strip()
            props[key] = val

        # Resolve mapMatrixID
        matrix_raw = props.get("mapMatrixID", "")
        mm = re.search(r"map_matrix_(\d+)", matrix_raw)
        if mm:
            props["mapMatrixID"] = int(mm.group(1))

        # Resolve eventsArchiveID
        events_raw = props.get("eventsArchiveID", "")
        em = re.search(r"(events_\w+)", events_raw)
        if em:
            props["eventsArchiveID_name"] = em.group(1)

        result[constant] = props
    return result


def get_pt_land_data_index(cell_const: str, header_props: dict,
                           index_to_const: dict | None = None) -> int | None:
    """
    Given a MAP_HEADER_* or MAP_NNN constant, return the land_data index
    (map_data_NNN.bin).  We use the eventsArchiveID field from map_headers.h.
    The events archive index corresponds to the land_data index in the NARC.
    """
    if cell_const is None:
        return None

    # Resolve MAP_NNN -> MAP_HEADER_* using index_to_const
    resolved = cell_const
    map_num_m = re.fullmatch(r"MAP_(\d+)", cell_const)
    if map_num_m and index_to_const:
        resolved = index_to_const.get(int(map_num_m.group(1)), cell_const)

    props = header_props.get(resolved, {})
    events_name = props.get("eventsArchiveID_name")
    if events_name is None:
        return None

    # events_name is like "events_jubilife_city".
    # The land_data index matches the position of this entry in zone_event.order.
    events_order_file = BASE_DIR / "pokeplatinum" / "res" / "field" / "events" / "zone_event.order"
    if not hasattr(get_pt_land_data_index, "_order_cache"):
        get_pt_land_data_index._order_cache = {}
        if events_order_file.exists():
            for i, line in enumerate(events_order_file.read_text(encoding="utf-8").splitlines()):
                name = line.strip()
                if name:
                    get_pt_land_data_index._order_cache[name] = i

    idx = get_pt_land_data_index._order_cache.get(events_name)
    return idx


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    output_dir = BASE_DIR

    # pokeemerald
    print("Extracting pokeemerald tile data...")
    emerald_tiles = extract_pokeemerald_tiles()
    out_path = output_dir / "tiles_pokeemerald.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(emerald_tiles, f, indent=2, ensure_ascii=False)
    print(f"  -> Written to {out_path}")

    # pokeheartgold
    print("\nExtracting pokeheartgold tile data...")
    hg_tiles = extract_pokeheartgold_tiles()
    out_path = output_dir / "tiles_pokeheartgold.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(hg_tiles, f, indent=2, ensure_ascii=False)
    print(f"  -> Written to {out_path}")

    # pokeplatinum
    print("\nExtracting pokeplatinum tile data...")
    pt_tiles = extract_pokeplatinum_tiles()
    out_path = output_dir / "tiles_pokeplatinum.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(pt_tiles, f, indent=2, ensure_ascii=False)
    print(f"  -> Written to {out_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
