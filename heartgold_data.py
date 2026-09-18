"""
HeartGold/SoulSilver terrain data (rebuilt from binaries, 2026-07-24).

Terrain comes straight from the land-data NARC — one 32×32 block per file —
assembled through the EVERYWHERE map matrix. The huge tiles_pokeheartgold.json
is NOT used: it is slow to load and its grids are shifted two tiles (the old
extractor read HG tiles at 0x10, but HG stores them at 0x14).

Shared NDS engine parsing lives in gen4_data.py; this module owns the HG
specifics: file locations, the matrix binary, behavior names, and the
encounter/headbutt sections.

Key data:
  land data     pokeheartgold/files/a/0/6/5                      (676 blocks)
  map matrix    files/fielddata/mapmatrix/map_matrix/map_matrix_0000_EVERYWHERE.bin
  behaviors     include/constants/metatile_behavior.h
  encounters    files/fielddata/encountdata/gs_enc_data.json
  headbutt      files/arc/headbutt.json
"""

import json
import re
import struct
from collections import deque
from pathlib import Path

from terrain_helpers import BASE_DIR, _fmt_movement, move_display
from hg_helpers import HG_POLYGON_OVERRIDES
from gen4_data import (
    Narc, parse_terrain_grid, parse_prop_entries, land_material_tile_ys,
    land_center_rule_tiles, land_surface_stacks,
    prop_model_footprint_from_bytes,

    land_data_bmd0, _all_material_mesh_faces,
    _parse_bdhc_stair_tiles, _parse_bdhc_flat_plates,
)

GAME        = "heartgold"
LAND_NARC   = BASE_DIR / "pokeheartgold/files/a/0/6/5"

# HG zone_event "movement" integers share the same engine as Platinum.
# Build from pokeplatinum/generated/movement_types.txt (0-indexed line = integer value).
def _load_hg_move_codes() -> dict[int, str]:
    p = BASE_DIR / "pokeplatinum/generated/movement_types.txt"
    try:
        lines = p.read_text().splitlines()
        return {i: ln for i, ln in enumerate(lines) if ln and ln != "MAX_MOVEMENT_TYPE"}
    except FileNotFoundError:
        return {}

_HG_MOVE_CODES: dict[int, str] = _load_hg_move_codes()
# Every 3D prop the field engine can place: 340 standalone BMD0 models,
# indexed directly by the props section's modelID (index 0 is `dmybox00`,
# a placeholder). Confirmed on Goldenrod: id 40 is `pc`, 80 `ko_gym`,
# 76 `ko_depart` — `ko` being Kogane, Goldenrod's Japanese name.
BM_FIELD_NARC = (BASE_DIR / "pokeheartgold/files/fielddata/build_model"
                 / "bm_field.narc")
MATRIX_DIR  = (BASE_DIR / "pokeheartgold/files/fielddata/mapmatrix"
               / "map_matrix")
MATRIX_FILE = MATRIX_DIR / "map_matrix_0000_EVERYWHERE.bin"
BEHAVIOR_H  = BASE_DIR / "pokeheartgold/include/constants/metatile_behavior.h"
#: The two tables that between them describe every map. `maps.h` gives the
#: numeric map id and internal code; `map_headers.h` names each map's matrix,
#: event and encounter NARC files symbolically. Together they replace
#: maps_pokeheartgold.json, whose per-map payload is shifted — its entry for
#: Union Cave B1F carries Violet City's Pokémon Center objects and warps.
MAPS_H        = BASE_DIR / "pokeheartgold/include/constants/maps.h"
MAP_HEADERS_H = BASE_DIR / "pokeheartgold/src/data/map_headers.h"
BLOCK_TILES = 32
_NO_BLOCK   = 0xFFFF     # landDataID meaning "no block here"
# TILE_BEHAVIOR_HEADBUTT. Despite the name this is HG's GENERIC tree wall —
# Route 30 has 1119 of these but only 75 headbutt-tree tiles in headbutt.json.
_TREE_BEHAVIOR = 6
# Elevation layers (tile u16 bits 8-14) that carry walk-over structures.
# See _block_bridge_tiles for why this is only one of four tests.
_BRIDGE_LAYERS = frozenset({6, 13})

# Maps where BDHC tilted plates bleed into raw=0 dead-zone tiles that lie
# beyond the actual terrain boundary. Strip stair only from cells where raw==0.
_STAIR_RAW0_FILTER: frozenset = frozenset({
    "MAP_AZALEA",
    "MAP_DARK_CAVE_ROUTE_31_SIDE",
    "MAP_DARK_CAVE_ROUTE_45_SIDE",
    "MAP_GOLDENROD_TUNNEL_B1F",
    "MAP_MOUNT_MORTAR_1F_ENTRANCE",
    "MAP_MOUNT_MORTAR_B1F",
})

# Maps where the staircase is a WARP_STAIRS_* warp, not a physical slope.
# The BDHC tilted plate covers the entire interior, producing spurious ¿ on
# every floor tile. Suppress all stair marks — the warp tile already renders D.
_STAIR_SUPPRESS_ALL: frozenset = frozenset({
    "MAP_NEW_BARK_PLAYER_HOUSE_1F",
    "MAP_NEW_BARK_RIVAL_HOUSE_1F",
})


# ---------------------------------------------------------------------------
# Map registry — the decomp's own map tables
# ---------------------------------------------------------------------------

_REGISTRY: dict | None = None
_REG_BY_ID: dict | None = None
_REG_BY_CODE: dict | None = None

#: `#define MAP_ROUTE_30  34  // MAP_R30` — id and internal code in one line.
_MAPS_H_RE = re.compile(
    r"^#define\s+(MAP_\w+)\s+(\d+)\s*//\s*MAP_(\w+)\s*$", re.M)
#: `[MAP_ROUTE_30] = { ... }` in sMapHeaders[].
_HEADER_ENTRY_RE = re.compile(r"\[(MAP_\w+)\] = \{(.*?)\n\s*\},", re.S)
_HEADER_FIELD_RE = re.compile(r"\.(\w+)\s*=\s*([^,\n]+)")


def _pretty_map_name(constant: str) -> str:
    """MAP_UNION_CAVE_B1F -> 'Union Cave B1F'. Floor tokens keep their case."""
    out = []
    for tok in constant[4:].split("_"):
        out.append(tok if re.fullmatch(r"B?\d+F", tok) or tok.isdigit()
                   else tok.capitalize())
    return " ".join(out)


def _map_registry() -> dict:
    """
    Every map, keyed by its MAP_* constant.

    Built from the decomp's own two tables, so nothing here depends on
    maps_pokeheartgold.json. `matrix_file` is what makes caves and interiors
    reachable: only 97 of 540 maps live on the shared EVERYWHERE matrix, and
    the other 443 each own one.
    """
    global _REGISTRY, _REG_BY_ID, _REG_BY_CODE
    if _REGISTRY is not None:
        return _REGISTRY

    codes: dict[str, tuple[int, str]] = {}
    for const, num, code in _MAPS_H_RE.findall(
            MAPS_H.read_text(encoding="utf-8", errors="replace")):
        codes[const] = (int(num), code)

    reg: dict = {}
    text = MAP_HEADERS_H.read_text(encoding="utf-8", errors="replace")
    for const, body in _HEADER_ENTRY_RE.findall(text):
        fields = dict(_HEADER_FIELD_RE.findall(body))
        map_id, code = codes.get(const, (None, ""))

        matrix = fields.get("matrixId", "").strip()
        matrix_name = re.sub(r"_bin$", ".bin",
                             re.sub(r"^NARC_map_matrix_", "", matrix))
        events = fields.get("eventsBank", "").strip()
        events_name = re.sub(r"_bin$", ".json",
                             re.sub(r"^NARC_zone_event_", "", events))
        enc = fields.get("wildEncounterBank", "").strip()

        reg[const] = {
            "constant":      const,
            "name":          _pretty_map_name(const),
            "code":          code,
            "internal_code": f"MAP_{code}" if code else "",
            "zone_id":       map_id,
            "map_type":      fields.get("mapType", "").strip(),
            "mapsec":        fields.get("mapsec", "").strip(),
            "matrix_file":   MATRIX_DIR / matrix_name,
            "own_matrix":    "EVERYWHERE" not in matrix,
            "events_file":   _ZONE_EVENT_DIR / events_name,
            # ENCDATA_R30 -> R30; ENCDATA_NA means the map has no wild table.
            "enc_code":      (re.sub(r"^ENCDATA_", "", enc)
                              if enc and enc != "ENCDATA_NA" else ""),
        }

    _REGISTRY = reg
    _REG_BY_ID = {e["zone_id"]: e for e in reg.values()
                  if e["zone_id"] is not None}
    # Two headers share one code (the duplicate is harmless — first wins).
    _REG_BY_CODE = {}
    for e in reg.values():
        if e["code"]:
            _REG_BY_CODE.setdefault(e["code"], e)
    return _REGISTRY


def map_registry_entries() -> list:
    """Registry rows in map-id order — the map list for `--all`."""
    return sorted(_map_registry().values(),
                  key=lambda e: (e["zone_id"] is None, e["zone_id"] or 0))


def map_entry(constant: str) -> dict | None:
    """
    The map entry shape the renderer expects, assembled from the decomp.

    Replaces the lookup into maps_pokeheartgold.json, which held only 491 of
    540 maps. HG's warps are read from the zone_event archive rather than from
    this entry (the merged JSON's `warps` were misaligned), so the payload is
    loaded mainly to keep the entry the same shape as Platinum's.
    """
    reg = _map_registry().get(constant)
    if reg is None:
        return None
    entry = {"constant":      constant,
             "name":          reg["name"],
             "internal_code": reg["internal_code"],
             "matrixId":      str(reg["matrix_file"].stem)}
    path = reg.get("events_file")
    if path is not None and path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            entry.update(payload)
    return entry


def _registry_by_code(code: str) -> dict | None:
    _map_registry()
    return (_REG_BY_CODE or {}).get(re.sub(r"^MAP_", "", code or ""))


# ---------------------------------------------------------------------------
# Behavior name lookup
# ---------------------------------------------------------------------------

_BEHAVIOR_NAMES: dict | None = None


def _get_behavior_names() -> dict:
    """Parse `enum TILE_BEHAVIOR` into {value: name}."""
    global _BEHAVIOR_NAMES
    if _BEHAVIOR_NAMES is not None:
        return _BEHAVIOR_NAMES
    names: dict = {}
    if BEHAVIOR_H.exists():
        entry_re = re.compile(r"^\s*(TILE_BEHAVIOR_\w+)\s*(?:=\s*(\d+))?\s*,?")
        idx, in_enum = 0, False
        for line in BEHAVIOR_H.read_text(encoding="utf-8").splitlines():
            if not in_enum:
                if "enum TILE_BEHAVIOR" in line:
                    in_enum = True
                continue
            if "}" in line:
                break
            m = entry_re.match(line)
            if m:
                if m.group(2) is not None:
                    idx = int(m.group(2))
                names[idx] = m.group(1)
                idx += 1
    _BEHAVIOR_NAMES = names
    return names


# ---------------------------------------------------------------------------
# Map matrix  (EVERYWHERE world grid)
#
# Binary layout: width(1) height(1) has_headers(1) has_altitudes(1)
# prefix_len(1) prefix(prefix_len) then, if present, the mapHeaderIDs plane
# (u16 per block), the altitudes plane (u8 per block), and always the
# landDataIDs plane (u16 per block).
#
# NOTE: byte 0 is the WIDTH (47) and byte 1 the HEIGHT (17) — reading them the
# other way round scatters every zone into disconnected fragments.
# ---------------------------------------------------------------------------

_MATRIX: dict | None = None


def _get_matrix(path: Path | None = None) -> dict:
    """
    Return {'width', 'height', 'headers', 'altitudes', 'land_ids'}.

    One format serves both kinds of matrix. The shared EVERYWHERE matrix
    carries all three planes; a per-map matrix sets has_headers/has_alts to 0
    and ships only landDataIDs, because the matrix *is* the map and there is
    nothing to filter by zone.
    """
    global _MATRIX
    path = path or MATRIX_FILE
    if _MATRIX is None:
        _MATRIX = {}
    cached = _MATRIX.get(path)
    if cached is not None:
        return cached
    raw = path.read_bytes()
    width, height, has_headers, has_alts, prefix_len = raw[0], raw[1], raw[2], raw[3], raw[4]
    n = width * height
    off = 5 + prefix_len
    headers: list[int] = []
    if has_headers:
        headers = list(struct.unpack_from(f"<{n}H", raw, off)); off += 2 * n
    altitudes: list[int] = []
    if has_alts:
        altitudes = list(raw[off:off + n]); off += n
    land_ids = list(struct.unpack_from(f"<{n}H", raw, off))
    _MATRIX[path] = {"width": width, "height": height, "headers": headers,
                     "altitudes": altitudes, "land_ids": land_ids}
    return _MATRIX[path]


# ---------------------------------------------------------------------------
# Land data blocks
# ---------------------------------------------------------------------------

_NARC: Narc | None = None
_BLOCK_CACHE: dict = {}
_MATERIAL_CACHE: dict = {}
_CENTER_CACHE: dict = {}
_STAIR_CACHE: dict = {}
_PROPS_CACHE: dict = {}
_MESH_COMP_CACHE: dict = {}
_SURFACE_CACHE: dict = {}


def _get_narc() -> Narc:
    global _NARC
    if _NARC is None:
        _NARC = Narc(LAND_NARC)
    return _NARC


def _land_bytes(land_id: int) -> bytes:
    """Raw land-data block for a landDataID, or b'' when absent."""
    if land_id == _NO_BLOCK:
        return b""
    narc = _get_narc()
    return narc.file(land_id) if 0 <= land_id < narc.count else b""


def _load_map_data_block(land_id: int) -> list[list[dict]] | None:
    """32×32 behavior grid for a block, cached."""
    if land_id in _BLOCK_CACHE:
        return _BLOCK_CACHE[land_id]
    raw = _land_bytes(land_id)
    result = parse_terrain_grid(raw, GAME, _get_behavior_names()) if raw else None
    _BLOCK_CACHE[land_id] = result
    return result


def _load_block_material_ys(land_id: int) -> dict:
    if land_id in _MATERIAL_CACHE:
        return _MATERIAL_CACHE[land_id]
    raw = _land_bytes(land_id)
    result = land_material_tile_ys(raw, GAME) if raw else {}
    _MATERIAL_CACHE[land_id] = result
    return result


def _load_center_rule_tiles(land_id: int, materials: frozenset) -> set:
    key = (land_id, materials)
    if key in _CENTER_CACHE:
        return _CENTER_CACHE[key]
    raw = _land_bytes(land_id)
    result = land_center_rule_tiles(raw, GAME, materials) if raw else set()
    _CENTER_CACHE[key] = result
    return result


def _load_stair_tiles(land_id: int) -> set:
    if land_id in _STAIR_CACHE:
        return _STAIR_CACHE[land_id]
    raw = _land_bytes(land_id)
    result = _parse_bdhc_stair_tiles(raw, GAME) if raw else set()
    _STAIR_CACHE[land_id] = result
    return result


_FLAT_PLATE_CACHE: dict = {}
_EXTRA_CACHE: dict = {}


def _load_flat_plates(land_id: int) -> list:
    if land_id in _FLAT_PLATE_CACHE:
        return _FLAT_PLATE_CACHE[land_id]
    raw = _land_bytes(land_id)
    result = _parse_bdhc_flat_plates(raw, GAME) if raw else []
    _FLAT_PLATE_CACHE[land_id] = result
    return result


def _land_extra_records(land_id: int) -> list:
    """
    Decode the land-data `extra` region: 8-byte records, two u32.

    This is the region that sits between the 20-byte HG header and the terrain
    grid (size at offset 18, non-zero in 230/676 blocks). word1 unpacks as four
    bytes forming a tile rectangle; word0 is a class/flags word whose value is
    shared by the records belonging to the same structure.

    Returns (cls, x0, x1, z0, z1) with the rect normalised, block-local tiles.

    The region sits between the header and the terrain grid: HG's decomp puts
    TERRAIN_ATTRIBUTES_OFFSET at 0x14 (0x10 in Platinum) and the records are
    plainly visible there, while 0x14 + extra onwards is repeated tile words.
    """
    if land_id in _EXTRA_CACHE:
        return _EXTRA_CACHE[land_id]
    raw = _land_bytes(land_id)
    out: list = []
    if len(raw) >= 20:
        extra = struct.unpack_from("<H", raw, 18)[0]
        data = raw[20:20 + extra]
        for i in range(extra // 8):
            w0, w1 = struct.unpack_from("<2I", data, i * 8)
            b = [(w1 >> (8 * k)) & 0xFF for k in range(4)]
            out.append((w0, min(b[0], b[2]), max(b[0], b[2]),
                            min(b[1], b[3]), max(b[1], b[3])))
    _EXTRA_CACHE[land_id] = out
    return out


def _load_surface_stacks(land_id: int) -> dict:
    """{(col, row): {y: frozenset(material)}} for one block."""
    if land_id in _SURFACE_CACHE:
        return _SURFACE_CACHE[land_id]
    raw = _land_bytes(land_id)
    result = land_surface_stacks(raw, GAME) if raw else {}
    _SURFACE_CACHE[land_id] = result
    return result


def _record_scope(land_id: int) -> set:
    """
    Block-local tiles the `extra` records claim for a raised structure.

    The records decompose each structure into rectangles, but not uniformly:
    Route 27 gets whole region rects, Celadon gets only the two end rows of its
    span. What is consistent is that all rects of one structure share a class
    word, so the structure's extent per column is the z range those rects span
    -- Celadon's two end rows then fill in to the deck between them, and a
    region rect stays itself.

    A class word's high half is itself a tile-attribute word, in the same
    format as the terrain grid's u16: bit 15 impassable, bits 8-14 elevation
    layer, low byte behaviour (see the notes on the `extra` region). It is
    tempting to read `0x8006` -- impassable with the tree-wall behaviour -- as
    scenery and drop those records, and doing so does fix Violet and Ecruteak.
    It is wrong: on Route 12 the `0x8006` records cover 71 tiles of real
    bridge, behaviour 0 and layer 6/13, indistinguishable in the grid from the
    462 tiles that map renders correctly. Every record is kept; what separates
    a deck from scenery is the geometry, in `_block_bridge_tiles`.
    """
    span: dict = {}
    for cls, x0, x1, z0, z1 in _land_extra_records(land_id):
        cols = span.setdefault(cls, {})
        for x in range(max(x0, 0), min(x1, BLOCK_TILES - 1) + 1):
            lo, hi = cols.get(x, (BLOCK_TILES, -1))
            cols[x] = (min(lo, z0), max(hi, z1))
    out: set = set()
    for cols in span.values():
        for x, (lo, hi) in cols.items():
            for z in range(max(lo, 0), min(hi, BLOCK_TILES - 1) + 1):
                out.add((x, z))
    return out


#: Terrain behaviours that describe a bridge outright.
#:   112 = approach ground -- NOT part of the bridge.
#:   113 = ramp apron at each end of an outdoor span.
#:   114 = cave/indoor bridge deck (wild encounters, no surf).
#:   115 = outdoor bridge deck (surfable; you surf under it).
_BRIDGE_DECK_BEHAVIORS = frozenset({114, 115})
_BRIDGE_RAMP_BEHAVIOR = 113


def _behavior_bridge_tiles(grid: list) -> set:
    """
    Decks that the terrain grid marks directly, independent of the `extra`
    records.

    HG builds bridges two different ways, and this is the second one. Route 26's
    two spans are described by no `extra` record at all -- that block has two
    records and neither touches either span -- so the structure rule cannot see
    them however it is tuned.

    Outdoor spans (Route 26, 44, 45 …): ground(112), ramp(113) x2, deck(115) x N,
    ramp(113) x2, ground(112). Deck is behaviour 115 (surfable) because you surf
    under them.

    Cave spans (Diglett, Seafoam B2F, Victory Road 1F …): ground(112), deck(114)
    x N, ground(112). Deck is behaviour 114 (encounter) — wild Pokémon appear;
    no ramp tiles.

    Across all 676 blocks behaviour 114 appears 261 times and behaviour 115
    appears 242 times; on every hand-made ground truth both are bridge deck and
    nothing else.
    """
    decks = {(tc, tr) for tr, row in enumerate(grid)
             for tc, cell in enumerate(row)
             if (cell["raw"] & 0xFF) in _BRIDGE_DECK_BEHAVIORS}
    # Tested against `decks`, never against the growing result: the aprons are
    # two tiles deep, so letting an accepted ramp recruit its neighbour would
    # walk the span out onto the bank one tile at a time.
    ramps = {(tc, tr) for tr, row in enumerate(grid)
             for tc, cell in enumerate(row)
             if (cell["raw"] & 0xFF) == _BRIDGE_RAMP_BEHAVIOR
             and any(nb in decks for nb in ((tc - 1, tr), (tc + 1, tr),
                                            (tc, tr - 1), (tc, tr + 1)))}
    return decks | ramps


#: One BDHC height unit in mesh y. Surveying all 676 blocks, 64 is by far the
#: commonest spacing between two surfaces on one tile (57,442 occurrences), so
#: it is the elevation quantum; anything closer is a decal drawn just clear of
#: the surface below it to avoid z-fighting, not a separate level.
_ELEVATION_UNIT = 64

#: How far above the BDHC collision plane a bridge deck is DRAWN.
#:
#: The BDHC flat plate is the plane the engine walks you along, at
#: `height * _ELEVATION_UNIT` in mesh y. For ordinary ground the drawn surface
#: sits exactly ON it -- across all 676 blocks 58% of tiles have a rise of 0.
#: A bridge deck is different: the collision plane stays at the elevation the
#: span belongs to and the deck model is drawn exactly 128 above it. That value
#: is a distinguished spike in the same survey (10,963 tiles, 4%), not a smear.
#:
#: This is what tells a deck from a road drawn over water. Vermilion City is
#: built on the sea: its streets are ordinary ground that happens to be at water
#: level, so they are drawn ON their collision plane, rise 0. Every one of the
#: 16 verified bridge components has rise exactly 128 -- across five maps and
#: seven different absolute heights, from 1152 to 7296.
_DECK_RISE = 128


def _bdhc_plane(land_id: int, tile: tuple) -> float | None:
    """Mesh y of the BDHC collision plane under a block-local tile."""
    x, z = tile
    for c0, c1, r0, r1, height in _load_flat_plates(land_id):
        if c0 <= x < c1 and r0 <= z < r1:
            return height * _ELEVATION_UNIT
    return None


def _drawn_above_collision(land_id: int, tops: list, top: int) -> bool:
    """
    Whether the candidate surface is a deck DRAWN OVER its collision plane
    rather than ground drawn ON it -- see `_DECK_RISE`.

    Tested on the MODAL rise, not on every tile. Where a span crosses a gorge
    the plate under a deck tile is the valley floor rather than the deck's own
    plane, so those tiles read a much larger rise: two Route 45 components have
    6 tiles of 2688 and 1664 against 12 of 128. The deck's own plane is what the
    majority of its tiles sit on, and 128 is the modal rise on all 16 verified
    bridge components.
    """
    rises = [top - plane for plane in (_bdhc_plane(land_id, t) for t in tops)
             if plane is not None]
    if not rises:
        return False
    return max(set(rises), key=rises.count) == _DECK_RISE


def _spans_something(stacks: dict, tops: list, top: int) -> bool:
    """
    Whether a candidate deck is drawn OVER something else -- which is what
    makes it a bridge rather than raised ground.

    Two ways to fail, and each is a real map:

      * nothing at all is drawn below it. Violet's island is a solid raised
        platform reached by stairs: below its road surface the block draws no
        face, because there is nothing to see under it.
      * the only thing below is the same material, i.e. one object drawn with
        a lip. Ecruteak's rock formation puts `rock01` at both 3072 and 3008,
        one elevation unit apart, and is a rock and not a deck.

    Verified on every hand-made ground truth: 16 of 16 bridge components pass,
    and no non-bridge component does. Deliberately NOT a height threshold --
    real deck-to-underside gaps run 256..3200 and the false ones 32..64, which
    looks separable but is not principled, since 64 is an ordinary elevation
    step (see `_ELEVATION_UNIT`).
    """
    above: set = set()
    below: set = set()
    for t in tops:
        for y, mats in stacks[t].items():
            if y == top:
                above |= mats
            elif y <= top - _ELEVATION_UNIT:
                below |= mats
    return bool(below - above)


def _climbed_onto(tops: list, stairs: set) -> bool:
    """
    Whether the candidate surface is reached by climbing rather than at grade.

    A bridge deck is level with the ground at both ends: you walk onto it. A
    raised platform is reached by going UP, which the block records as a tilted
    BDHC plate landing against it. So a candidate whose top surface a stair runs
    into is raised ground, not a span.

    This is Violet's island, which is a bridge/stair hybrid -- a Moon-Bridge
    that changes elevation -- and the game's own data calls it stairs.

    Across every hand-made ground truth, 16 of 16 real bridge components have no
    stair touching their deck, including Route 45's blocks that hold 15 and 21
    stair tiles of their own and Route 27's block that holds 6. Violet's island
    is the only component that touches one.
    """
    return any(nb in stairs for tc, tr in tops
               for nb in ((tc - 1, tr), (tc + 1, tr), (tc, tr - 1), (tc, tr + 1)))


def _block_bridge_tiles(land_id: int) -> tuple[set, set]:
    """
    Block-local (deck, under) tile sets for a block's bridges.

    Two independent sources, because HG builds bridges two ways:

      * `_behavior_bridge_tiles` -- the terrain grid names the deck outright
        (behaviour 115).  Used by Route 26, whose spans appear in no `extra`
        record.
      * the four structure tests below -- used by Route 27, Route 45, Celadon
        and Route 44, whose decks carry behaviour 0 and are described only by
        the `extra` records plus the mesh.

    A deck is a walking surface drawn above the thing it crosses, and for the
    structure kind the block says so four times over. All four are needed; each
    one alone is wrong on at least one of the hand-made ground truths:

      1. The tile u16's elevation-layer index (bits 8-14) is a walk-over layer.
         Necessary but nowhere near sufficient -- Celadon's raised street is 554
         layer-6 tiles against 16 tiles of bridge.
      2. The tile is inside a structure the `extra` records claim. This is what
         separates a deck from ordinary raised ground: Celadon records only its
         bridge, so the street drops out.
      3. Of the connected structure, the tile carries its TOPMOST drawn
         horizontal surface. This is the one that fixes the extra row: Route 27's
         decks have a row of layer-6 tiles along their north edge that is water
         in game, and it is invisible to both the tile grid (byte-identical to
         the deck rows) and the BDHC (same plate). The mesh is what knows -- the
         deck quads start half a tile south of it, so its top surface is the
         water below, not the deck.
      4. The structure stands above its surroundings: its top surface is higher
         than the lowest surface bordering it. Without this every flat city
         street qualifies -- Goldenrod painted 123 bridge tiles at one point,
         all of them sidewalk, because a flat block's top surface is trivially
         its own.

      5. Nothing climbs onto it -- see `_climbed_onto`. Violet's island is a
         raised platform reached by a stair, and passes the first four.
      6. It is DRAWN ABOVE its collision plane -- see `_drawn_above_collision`.
         Ordinary ground is drawn on the plane you walk along; a deck is drawn
         exactly `_DECK_RISE` above it. This is what tells a bridge from a road
         built over water, which is all of Vermilion City.

    Stairs are excluded: a ramp onto a deck passes all four, and the BDHC
    tilted-plate signal for stairs is the specific one.

    `under` is the rest of each bridge structure -- the tiles test 3 rejects,
    where what is drawn is the surface the deck passes over rather than the
    deck. The caller renders those as the terrain beneath.

    Exact on all three ground truths -- Route 27 153/153, Route 45 114/114,
    Celadon 16/16, no misses and no extras -- and yields nothing on Goldenrod,
    Cerulean, Route 22 or Route 30.
    """
    grid = _load_map_data_block(land_id)
    if grid is None:
        return set(), set()

    stairs = _load_stair_tiles(land_id)
    deck: set = set()
    under: set = set()
    deck |= _behavior_bridge_tiles(grid)

    scope = _record_scope(land_id)
    if not scope:
        return deck, under
    stacks = _load_surface_stacks(land_id)
    surf = {pos: sorted(lv) for pos, lv in stacks.items()}
    pending = {t for t in scope
               if ((grid[t[1]][t[0]]["raw"] >> 8) & 0x7F) in _BRIDGE_LAYERS
               and t in surf}
    if not pending:
        return deck, under

    while pending:
        seed = pending.pop()
        comp = [seed]
        queue = deque([seed])
        while queue:
            tc, tr = queue.popleft()
            for nb in ((tc - 1, tr), (tc + 1, tr), (tc, tr - 1), (tc, tr + 1)):
                if nb in pending:
                    pending.discard(nb)
                    comp.append(nb)
                    queue.append(nb)
        top = max(surf[t][-1] for t in comp)
        # Some blocks have deep underwater materials (e.g. sea_rock_m) in the
        # surface stacks of bridge deck tiles, inflating top far above the actual
        # deck surface. Use the MODAL peak Y across the component for the
        # structure tests — the bridge surface is the level that appears most
        # often as the highest Y in each tile, which matches _DECK_RISE for all
        # 16 verified bridge components (where all tiles share one peak level).
        _top_counts: dict = {}
        for _t in comp:
            _k = surf[_t][-1]
            _top_counts[_k] = _top_counts.get(_k, 0) + 1
        modal_top = max(_top_counts, key=_top_counts.__getitem__)
        border = {nb for tc, tr in comp
                  for nb in ((tc - 1, tr), (tc + 1, tr), (tc, tr - 1), (tc, tr + 1))}
        outside = [surf[t][-1] for t in border - set(comp) if t in surf]
        if not outside or modal_top <= min(outside):
            continue                       # flush with its surroundings: not a deck
        tops = [t for t in comp if surf[t][-1] == modal_top and t not in stairs]
        if not tops or not _spans_something(stacks, tops, modal_top):
            continue
        if _climbed_onto(tops, stairs):
            continue
        if not _drawn_above_collision(land_id, tops, modal_top):
            continue
        bridge_plane = modal_top - _DECK_RISE
        for t in comp:
            if t in stairs:
                continue
            # A tile is deck if its surface stack contains modal_top OR if it
            # sits on the same BDHC collision plate as the deck (plane ==
            # bridge_plane). Some approach/junction tiles lack the bridge mesh
            # entirely (their stack has only road or ground geometry) but share
            # the same plate — they are still deck tiles the player walks on.
            plane = _bdhc_plane(land_id, t)
            if modal_top in surf[t] or plane == bridge_plane:
                deck.add(t)
            else:
                under.add(t)
    return deck, under


def _load_block_props(land_id: int) -> list:
    if land_id in _PROPS_CACHE:
        return _PROPS_CACHE[land_id]
    raw = _land_bytes(land_id)
    result = parse_prop_entries(raw, GAME) if raw else []
    _PROPS_CACHE[land_id] = result
    return result


_BM_FIELD: Narc | None = None
_FOOTPRINT_CACHE: dict = {}

#: Smallest extent, in tiles, that counts as a structure rather than clutter.
#: The props section places everything the field engine draws in 3D, most of
#: which is furniture: Goldenrod alone puts down 21 street lights (1.41 × 1.22),
#: 7 flags, 8 signboards and 8 doors. Every real building clears 3 tiles on
#: both axes — the smallest in the city is `ko_h01` at 3.75 × 3.01 — so this
#: cut removes the clutter without touching a single structure. Same threshold
#: Platinum uses, for the same reason.
_MIN_STRUCTURE_TILES = 1.5


def _get_bm_field() -> Narc:
    global _BM_FIELD
    if _BM_FIELD is None:
        _BM_FIELD = Narc(BM_FIELD_NARC)
    return _BM_FIELD


def _prop_model_footprint(model_id: int):
    """
    (name, min_dc, max_dc, min_dr, max_dr, min_dy, max_dy) for a prop model,
    in tiles relative to its placement position, or None if unparseable.

    modelID indexes bm_field.narc directly. All 340 models parse.
    """
    if model_id in _FOOTPRINT_CACHE:
        return _FOOTPRINT_CACHE[model_id]
    narc = _get_bm_field()
    result = (prop_model_footprint_from_bytes(narc.file(model_id))
              if 0 <= model_id < narc.count else None)
    _FOOTPRINT_CACHE[model_id] = result
    return result



def _block_prop_structures(land_id: int, off_c: int, off_r: int) -> list:
    """
    Building-sized prop placements in a block, as float tile-rects in
    combined-grid coordinates. Which of these are actually buildings is not
    decided here — the overlay builder keeps only the ones a warp sits in.
    """
    out = []
    for model_id, lc, lr in _load_block_props(land_id):
        fp = _prop_model_footprint(model_id)
        if fp is None:
            continue
        name, min_dc, max_dc, min_dr, max_dr, _min_dy, _max_dy = fp
        overrides: list[dict] | None = None
        if name in HG_POLYGON_OVERRIDES:
            ov = HG_POLYGON_OVERRIDES[name]
            if isinstance(ov, list) and ov and isinstance(ov[0], dict):
                # List of partial-axis dicts: emit one footprint entry per dict,
                # each independently overriding the global footprint on only the
                # axes it lists. Lets a single model placement produce an
                # L-shaped (or otherwise non-rectangular) building body by
                # combining two or more rectangular footprints.
                overrides = ov
            elif isinstance(ov, dict):
                # Partial-axis override: keep the global footprint on every axis
                # not listed, replace only the axes that are. Needed when no
                # single polygon (or union) spans the right column/row range —
                # e.g. "fs" has its sign post in poly1 AND in poly2's max_dc,
                # so excluding poly1 alone still leaves the sign column via
                # poly2. Specifying {"max_dc": 2.125} trims the right edge to
                # poly0's body width while keeping the global bottom row.
                if "min_dc" in ov: min_dc = ov["min_dc"]
                if "max_dc" in ov: max_dc = ov["max_dc"]
                if "min_dr" in ov: min_dr = ov["min_dr"]
                if "max_dr" in ov: max_dr = ov["max_dr"]
            else:
                poly_fp = prop_model_footprint_from_bytes(
                    _get_bm_field().file(model_id),
                    polygon=ov,
                )
                if poly_fp is not None:
                    _, min_dc, max_dc, min_dr, max_dr, _min_dy, _max_dy = poly_fp
        if overrides is not None:
            for part in overrides:
                pdc0 = part.get("min_dc", min_dc)
                pdc1 = part.get("max_dc", max_dc)
                pdr0 = part.get("min_dr", min_dr)
                pdr1 = part.get("max_dr", max_dr)
                if (pdc1 - pdc0 < _MIN_STRUCTURE_TILES
                        or pdr1 - pdr0 < _MIN_STRUCTURE_TILES):
                    continue
                out.append({
                    "model_id": model_id,
                    "name":     name,
                    "file":     name,
                    "c0": off_c + lc + pdc0, "c1": off_c + lc + pdc1,
                    "r0": off_r + lr + pdr0, "r1": off_r + lr + pdr1,
                })
            continue
        if (max_dc - min_dc < _MIN_STRUCTURE_TILES
                or max_dr - min_dr < _MIN_STRUCTURE_TILES):
            continue
        entry = {
            "model_id": model_id,
            "name":     name,
            "file":     name,
            "c0": off_c + lc + min_dc, "c1": off_c + lc + max_dc,
            "r0": off_r + lr + min_dr, "r1": off_r + lr + max_dr,
        }
        if name == "en_sekia":
            gfp = prop_model_footprint_from_bytes(_get_bm_field().file(model_id), polygon=2)
            if gfp is not None:
                _, dc0, dc1, dr0, dr1, _, _ = gfp
                entry["c0g"] = off_c + lc + dc0
                entry["c1g"] = off_c + lc + dc1
                entry["r0g"] = off_r + lr + dr0
                entry["r1g"] = off_r + lr + dr1
        out.append(entry)
    return out


#: Buildings that are not props at all — they are modelled into the land
#: block's own terrain mesh, the way a cliff or a hillside is. `_load_block_props`
#: cannot see them (they have no placement entry), so they render as plain wall.
#:
#: Each row is one connected mesh component matched on geometry alone:
#: (face count, footprint width, footprint depth, y-min, y-max, label).
#: Material names are useless here — the two copies of Goldenrod's Underground
#: entrance carry completely different names (`ko_road_a`/`ko_road_r1` in one
#: block, `sea_on`/`sea_rock` in the other) despite being the same structure.
#:
#: This is a whitelist on purpose, and it should stay one. Treating every raised
#: warp-containing mesh component as a building sweeps in ~33 cave mouths (the
#: 28-face 3.50 × 1.99 model used on Routes 20/27/41/42/45, Blackthorn, Mount
#: Silver and Ruins of Alph), which are holes in a cliff and belong as `█`.
#: The known cost: Ruins of Alph's two Union Cave doorways read as structures on
#: screen but have no geometry of their own — only that same cave-mouth model —
#: so they stay `█`. Accepted gap, like Route 12's three bridge tiles.
_MESH_BUILDINGS: list[tuple[int, float, float, int, int, str]] = [
    # Goldenrod's north and south Underground entrances (land blocks 38 and 40).
    # Exactly two instances game-wide; both hold a MAP_GOLDENROD_TUNNEL_1F warp.
    (49, 3.75, 2.50, 1152, 3520, "underground_entrance"),
]

#: How far a component may drift from a table row and still match. Both copies
#: hit their signature exactly; this only guards against float round-tripping.
_MESH_SIG_TOL = 0.05


def _block_mesh_components(land_id: int) -> list:
    """
    Connected components of a block's terrain mesh, in block-local tiles.

    Faces are grouped by shared vertices across all materials at once, because
    one structure's faces can be split over several material names.
    """
    if land_id in _MESH_COMP_CACHE:
        return _MESH_COMP_CACHE[land_id]
    raw = _land_bytes(land_id)
    faces: list = []
    try:
        if raw:
            mats = _all_material_mesh_faces(land_data_bmd0(raw, GAME))
            faces = [f for flist in mats.values() for f in flist]
    except Exception:
        faces = []

    parent = list(range(len(faces)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    by_vertex: dict = {}
    for i, f in enumerate(faces):
        for v in f:
            by_vertex.setdefault(v, []).append(i)
    for shared in by_vertex.values():
        root = find(shared[0])
        for i in shared[1:]:
            r = find(i)
            if r != root:
                parent[r] = root

    groups: dict = {}
    for i in range(len(faces)):
        groups.setdefault(find(i), []).append(i)

    out = []
    for idxs in groups.values():
        vs = [v for i in idxs for v in faces[i]]
        xs = [v[0] for v in vs]
        ys = [v[1] for v in vs]
        zs = [v[2] for v in vs]
        out.append((len(idxs),
                    min(xs) / 1024 + 16, max(xs) / 1024 + 16,
                    min(zs) / 1024 + 16, max(zs) / 1024 + 16,
                    min(ys), max(ys)))
    _MESH_COMP_CACHE[land_id] = out
    return out


def _block_mesh_structures(land_id: int, off_c: int, off_r: int) -> list:
    """Whitelisted mesh-baked buildings in a block, as combined-grid rects."""
    out = []
    for nfaces, c0, c1, r0, r1, y0, y1 in _block_mesh_components(land_id):
        for sig_f, sig_w, sig_d, sig_y0, sig_y1, label in _MESH_BUILDINGS:
            if (nfaces == sig_f and y0 == sig_y0 and y1 == sig_y1
                    and abs((c1 - c0) - sig_w) <= _MESH_SIG_TOL
                    and abs((r1 - r0) - sig_d) <= _MESH_SIG_TOL):
                # Half-integer r1 (depth 2.50) lands exactly on a tile centre,
                # causing the strict `fr+0.5 < r1b` check in build_overlay to
                # exclude the last row.  Nudge r1 past the boundary.
                out.append({
                    "model_id": -1, "name": label, "file": label,
                    "c0": off_c + c0, "c1": off_c + c1,
                    "r0": off_r + r0, "r1": off_r + r1 + 0.001,
                })
                break
    return out


def _drop_scenery_backdrops(structures: list) -> list:
    """
    Remove the props that are landscape rather than buildings.

    A handful of zones place one enormous mesh that IS the setting — New Bark's
    windmill hillside (48 × 46 tiles, larger than the 32-tile block it lives
    in), Lake of Rage's wooded shore, Indigo Plateau's flower terrace. Each
    swallows the whole map, so every warp on it falls inside its rect and the
    enterable test would call it a building.

    What separates them from a building is that the game places the buildings
    ON them: all three enclose other prop placements, and no real structure
    encloses anything. Across the whole game exactly 3 of 192 warp-containing
    placements are enclosers, and they are exactly those three.
    """
    return [ps for ps in structures
            if not any(o is not ps
                       and ps["c0"] <= o["c0"] and o["c1"] <= ps["c1"]
                       and ps["r0"] <= o["r0"] and o["r1"] <= ps["r1"]
                       for o in structures)]


# ---------------------------------------------------------------------------
# Zone lookup
# ---------------------------------------------------------------------------

def _find_zone(maps_data: list | None, query: str) -> dict | None:
    """
    Match a user query against the map registry (name / constant / code).

    `maps_data` is accepted for signature compatibility and ignored — map
    identity now comes from the decomp's own tables, see _map_registry.
    """
    q_slug = re.sub(r"[^a-z0-9]", "", query.strip().lower())
    if not q_slug:
        return None

    def slug(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", (s or "").lower())

    reg = _map_registry()
    for key in ("name", "constant", "internal_code", "code"):
        for entry in reg.values():
            if slug(entry.get(key, "")) == q_slug:
                return entry
    for entry in reg.values():                    # partial, name only
        if q_slug in slug(entry.get("name", "")):
            return entry
    return None


def _blocks_for_zone(zone_id: int) -> list[tuple[int, int]]:
    m = _get_matrix()
    w = m["width"]
    return [(i // w, i % w) for i, v in enumerate(m["headers"]) if v == zone_id]


def _largest_cluster(blocks: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Largest 4-connected group — zones can appear in disjoint pieces."""
    remaining = set(blocks)
    best: list[tuple[int, int]] = []
    while remaining:
        seed = remaining.pop()
        group = [seed]
        queue = deque([seed])
        while queue:
            r, c = queue.popleft()
            for nb in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                if nb in remaining:
                    remaining.discard(nb)
                    group.append(nb)
                    queue.append(nb)
        if len(group) > len(best):
            best = group
    return best


def _warp_tiles_shifted(
    warps: list, combined: list,
    c_min: int, r_min: int, grid_cols: int, grid_rows: int,
) -> set:
    """
    Grid-local warp tile positions, with trigger tiles replaced by their
    adjacent WARP_ENTRANCE_* tile where one exists.

    Mirrors the logic in terrain_to_ascii._shift_gen4_warp_to_entrance so that
    build_overlay's enterable check uses the same tile position as the rendered
    entrance char.
    """
    raw: set[tuple[int, int]] = set()
    for wp in warps:
        x, z = wp.get("x"), wp.get("z")
        if isinstance(x, int) and isinstance(z, int):
            raw.add((x - c_min * BLOCK_TILES, z - r_min * BLOCK_TILES))

    shifted: set[tuple[int, int]] = set()
    for (wc, wr) in raw:
        entrance = None
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = wr + dr, wc + dc
            if not (0 <= nr < grid_rows and 0 <= nc < grid_cols):
                continue
            if (nc, nr) in raw:
                continue
            cell = combined[nr][nc]
            if cell is None:
                continue
            if "WARP_ENTRANCE" in (cell.get("behavior_name") or ""):
                entrance = (nc, nr)
                break
        shifted.add(entrance if entrance is not None else (wc, wr))
    return shifted


def get_zone_tile_entry(tiles, query: str, maps_data: list | None = None) -> dict | None:
    """
    Build a zone's tile entry by assembling land-data blocks from the matrix.
    `tiles` is accepted for signature compatibility and ignored — HG terrain
    no longer comes from tiles_pokeheartgold.json.
    """
    zone = _find_zone(maps_data, query)
    if zone is None:
        return None
    zone_id = zone.get("zone_id")

    if zone.get("own_matrix"):
        # The matrix holds this map and nothing else, so every block in it is
        # ours: no zone filtering, and the map's own origin is (0, 0).
        m = _get_matrix(zone["matrix_file"])
        r_min, c_min = 0, 0
        r_max, c_max = m["height"] - 1, m["width"] - 1
    else:
        if zone_id is None:
            return None
        blocks = _largest_cluster(_blocks_for_zone(zone_id))
        if not blocks:
            return None
        m = _get_matrix()
        r_min = min(r for r, _ in blocks); r_max = max(r for r, _ in blocks)
        c_min = min(c for _, c in blocks); c_max = max(c for _, c in blocks)

    w = m["width"]
    land_ids = m["land_ids"]
    n_rows, n_cols = r_max - r_min + 1, c_max - c_min + 1

    combined: list[list] = []
    prop_structures: list[dict] = []
    for br in range(r_min, r_max + 1):
        block_rows: list[list] = [[] for _ in range(BLOCK_TILES)]
        for bc in range(c_min, c_max + 1):
            land_id = land_ids[br * w + bc]
            grid = _load_map_data_block(land_id)
            off_c = (bc - c_min) * BLOCK_TILES
            off_r = (br - r_min) * BLOCK_TILES
            prop_structures += _block_prop_structures(land_id, off_c, off_r)
            # Buildings with no prop placement, modelled straight into the
            # block's terrain mesh. They join the same list so the warp gate
            # and the encloser drop treat them like any other structure.
            prop_structures += _block_mesh_structures(land_id, off_c, off_r)
            stair_set = _load_stair_tiles(land_id)
            deck_set, under_set = (_block_bridge_tiles(land_id) if grid
                                   else (set(), set()))
            for tr in range(BLOCK_TILES):
                if grid:
                    row = []
                    for tc in range(BLOCK_TILES):
                        cell = dict(grid[tr][tc])     # copy: never mutate cache
                        if (tc, tr) in stair_set:
                            cell["stair"] = True
                        if (tc, tr) in deck_set:
                            cell["bridge_mesh"] = True
                        elif (tc, tr) in under_set:
                            cell["bridge_under"] = True
                        row.append(cell)
                    block_rows[tr].extend(row)
                else:
                    block_rows[tr].extend([None] * BLOCK_TILES)
        combined.extend(block_rows)

    # Behaviour 6 paints every tree wall in Johto; headbutt.json is what says
    # which of them can actually be headbutted.  Its coords are world tiles.
    for wx, wz in _headbutt_tree_positions(zone.get("internal_code", "")):
        tc, tr = wx - c_min * BLOCK_TILES, wz - r_min * BLOCK_TILES
        if 0 <= tr < len(combined) and 0 <= tc < len(combined[tr]):
            cell = combined[tr][tc]
            # A tree group also lists the walkable tiles the player headbutts
            # FROM (~19% of coords); only the trunk tiles are behaviour 6, and
            # marking the rest would overwrite real grass/water/ledges.
            if cell is not None and cell.get("behavior") == _TREE_BEHAVIOR:
                cell["headbutt_tree"] = True

    # What shows on a bridge's `under` tiles.
    #
    # These carry no behavior of their own — the water they sit in is drawn by
    # the block model, not written into the tile grid — so the kind of water is
    # read off an orthogonal neighbour, which keeps a river span from coming out
    # looking like open sea. A tile with no water beside it is left alone rather
    # than guessed at: the shoulder tiles flanking Celadon's span are `under`
    # tiles over dry ground, and inventing sea for them was what put the two
    # stray tiles beside that bridge.
    # One span crosses one body of water, so the kind is resolved per connected
    # run of `under` tiles rather than per tile — two tiles in the middle of
    # Route 27's longest span have only rock above them and would otherwise be
    # left blank in the middle of a river.
    h_grid, w_grid = len(combined), n_cols * BLOCK_TILES
    remaining = {(gr, gc) for gr in range(h_grid) for gc in range(w_grid)
                 if combined[gr][gc] is not None
                 and combined[gr][gc].get("bridge_under")}
    while remaining:
        group = [remaining.pop()]
        queue = deque(group)
        water_name = None
        while queue:
            gr, gc = queue.popleft()
            for nr, nc in ((gr - 1, gc), (gr + 1, gc), (gr, gc - 1), (gr, gc + 1)):
                if not (0 <= nr < h_grid and 0 <= nc < w_grid):
                    continue
                if (nr, nc) in remaining:
                    remaining.discard((nr, nc))
                    group.append((nr, nc))
                    queue.append((nr, nc))
                    continue
                nb = combined[nr][nc]
                if nb is not None and "WATER" in (nb.get("behavior_name") or ""):
                    water_name = nb["behavior_name"]
        if water_name:
            for gr, gc in group:
                combined[gr][gc]["bridge_water"] = water_name

    # Abutments. Where a span meets its bank the deck's end sits on a tile the
    # grid marks impassable with no behaviour of its own, and the water it
    # crosses runs straight through: Route 45's river is continuous either side
    # of every bridge but goes blank on the one row the abutment occupies.
    # Requiring water DIRECTLY opposite the deck keeps this one tile deep and
    # keeps it from eating into the cliff walls that flank the same spans.
    for gr in range(h_grid):
        for gc in range(w_grid):
            cell = combined[gr][gc]
            if (cell is None or cell.get("behavior") != 0
                    or cell.get("passable", True)
                    or cell.get("bridge_mesh") or cell.get("stair")):
                continue
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nr, nc, fr, fc = gr - dr, gc - dc, gr + dr, gc + dc
                if not (0 <= nr < h_grid and 0 <= nc < w_grid
                        and 0 <= fr < h_grid and 0 <= fc < w_grid):
                    continue
                near, far = combined[nr][nc], combined[fr][fc]
                if near is None or far is None:
                    continue
                if near.get("bridge_mesh") and "WATER" in (far.get("behavior_name") or ""):
                    cell["bridge_water"] = far["behavior_name"]
                    break

    # Collect props from immediately adjacent blocks. Some gatehouses
    # (kn_gate_a, gate_a) are placed in the neighbouring zone's block but
    # extend into this zone's tile grid by up to BLOCK_TILES rows. The WARP
    # trigger tiles for these entrances sit a few tiles north of the building's
    # r0, outside the standard ±1 enterable slack. Mark them cross_block=True
    # so build_overlay can apply a wider slack (2.5 tiles) for just these props.
    # Only done for EVERYWHERE-matrix zones; own-matrix interiors have no
    # meaningful neighbours.
    if not zone.get("own_matrix"):
        mh, mw = m["height"], m["width"]
        adj_seen: set[tuple[int, int]] = set()
        for adj_br in (r_min - 1, r_max + 1):
            if 0 <= adj_br < mh:
                for bc in range(c_min, c_max + 1):
                    if (adj_br, bc) not in adj_seen:
                        adj_seen.add((adj_br, bc))
                        lid = land_ids[adj_br * w + bc]
                        oc = (bc - c_min) * BLOCK_TILES
                        or_ = (adj_br - r_min) * BLOCK_TILES
                        for ps in _block_prop_structures(lid, oc, or_):
                            prop_structures.append({**ps, "cross_block": True})
        for adj_bc in (c_min - 1, c_max + 1):
            if 0 <= adj_bc < mw:
                for br in range(r_min, r_max + 1):
                    if (br, adj_bc) not in adj_seen:
                        adj_seen.add((br, adj_bc))
                        lid = land_ids[br * w + adj_bc]
                        oc = (adj_bc - c_min) * BLOCK_TILES
                        or_ = (br - r_min) * BLOCK_TILES
                        for ps in _block_prop_structures(lid, oc, or_):
                            prop_structures.append({**ps, "cross_block": True})

    _const = zone.get("constant", "")
    if _const in _STAIR_RAW0_FILTER:
        for _row in combined:
            if not _row:
                continue
            for _cell in _row:
                if _cell and _cell.get("stair") and _cell.get("raw", -1) == 0:
                    del _cell["stair"]
    elif _const in _STAIR_SUPPRESS_ALL:
        for _row in combined:
            if not _row:
                continue
            for _cell in _row:
                if _cell and _cell.get("stair"):
                    del _cell["stair"]

    return {
        "constant":      zone.get("constant", ""),
        "name":          zone.get("name", ""),
        "internal_code": zone.get("internal_code", ""),
        # From the header table's wildEncounterBank, so it is the map's real
        # encounter table rather than a code guessed from a neighbouring field.
        "file_code":     zone.get("enc_code") or zone.get("code", ""),
        "zone_id":       zone_id,
        "map_type":      zone.get("map_type", ""),
        "matrix_id":     zone["matrix_file"].name,
        "grid":          combined,
        "grid_width":    n_cols * BLOCK_TILES,
        "grid_height":   n_rows * BLOCK_TILES,
        "_block_row_min": r_min,
        "_block_col_min": c_min,
        "_tile_col_min": c_min * BLOCK_TILES,
        "_tile_row_min": r_min * BLOCK_TILES,
        "prop_structures": _drop_scenery_backdrops(prop_structures),
        # Grid-local warp tiles, from the decomp's own event data rather than
        # maps_pokeheartgold.json (whose `warps` are misaligned — see
        # zone_warps). The overlay builder needs these to tell an enterable
        # building from a decorative one.
        #
        # Warp events sit on WARP_SOUTH / WARP_NORTH / WARP_ENTRANCE_SOUTH /…
        # trigger tiles, not always on the visible entrance tile. When a trigger
        # tile has an adjacent WARP_ENTRANCE_* tile, the renderer moves the
        # entrance char there (see _shift_gen4_warp_to_entrance). The overlay's
        # enterable check must use the same shifted position; otherwise a
        # decorative structure that brackets the ENTRANCE tile (not the trigger)
        # can be falsely gated. Example: MAP_INDIGO_PLATEAU's pke_gate sits
        # around the WARP_ENTRANCE_SOUTH at row 27, but the trigger is at row 26;
        # using row 26 puts the warp center just inside pke_gate's r1+1 and gates
        # it incorrectly.
        "warp_tiles": _warp_tiles_shifted(
            zone_warps(zone.get("internal_code", "")),
            combined, c_min, r_min,
            n_cols * BLOCK_TILES, n_rows * BLOCK_TILES,
        ),
    }


# ---------------------------------------------------------------------------
# Rock Smash item drops  (overlay_01_rock_smash_item.c / NARC a/2/5/3)
# ---------------------------------------------------------------------------

_RS_TABLES_CACHE: tuple | None = None  # (tables_dict, probs_list)
_RS_SMASH_C = BASE_DIR / "pokeheartgold/src/field/overlay_01_rock_smash_item.c"
_RS_NARC_CACHE: list | None = None
_RS_ARRAY_NAME_TO_TYPE = {
    "sRockSmashItems_Default":    0,
    "sRockSmashItems_RuinsOfAlph": 1,
    "sRockSmashItems_CliffCave":  2,
}


def _load_rs_tables() -> tuple[dict[int, list[str]], list[int]]:
    """Parse overlay_01_rock_smash_item.c for item tables and slot probabilities."""
    global _RS_TABLES_CACHE
    if _RS_TABLES_CACHE is not None:
        return _RS_TABLES_CACHE

    tables: dict[int, list[str]] = {}
    probs:  list[int] = [25, 20, 10, 10, 10, 10, 10, 5]  # fallback

    if _RS_SMASH_C.exists():
        text = _RS_SMASH_C.read_text(encoding="utf-8")

        for arr_name, type_idx in _RS_ARRAY_NAME_TO_TYPE.items():
            # Locate the array definition.  Default has no #ifdef; the other two
            # appear twice (HG and SS branches) — take the first occurrence.
            pat = rf"(?s)static const u16 {re.escape(arr_name)}\[\]\s*=\s*\{{([^}}]+)\}}"
            if type_idx > 0:
                # Extract only from the HEARTGOLD branch (before #else)
                hg_start = text.find("#ifdef HEARTGOLD")
                while hg_start != -1:
                    hg_end = text.find("#else", hg_start)
                    if hg_end == -1:
                        break
                    block = text[hg_start:hg_end]
                    m = re.search(pat, block)
                    if m:
                        items = re.findall(r"ITEM_\w+", m.group(1))
                        tables[type_idx] = [_item_name(i) for i in items]
                        break
                    hg_start = text.find("#ifdef HEARTGOLD", hg_start + 1)
            else:
                m = re.search(pat, text)
                if m:
                    items = re.findall(r"ITEM_\w+", m.group(1))
                    tables[type_idx] = [_item_name(i) for i in items]

        # Probabilities from DrawRockSmashIdx thresholds
        fn_start = text.find("DrawRockSmashIdx(")
        if fn_start != -1:
            thresholds = [int(x) for x in re.findall(r"if \(rand < (\d+)\)", text[fn_start:])]
            if thresholds:
                computed: list[int] = []
                prev = 0
                for t in thresholds:
                    computed.append(t - prev)
                    prev = t
                computed.append(100 - prev)
                if len(computed) == 8:
                    probs = computed

    _RS_TABLES_CACHE = (tables, probs)
    return _RS_TABLES_CACHE


def _load_rock_smash_narc() -> list[tuple[int, int]]:
    """Parse NARC a/2/5/3 → list[(odds, type)] indexed by zone_id."""
    global _RS_NARC_CACHE
    if _RS_NARC_CACHE is not None:
        return _RS_NARC_CACHE
    import struct as _struct
    path = BASE_DIR / "pokeheartgold" / "files" / "a" / "2" / "5" / "3"
    if not path.exists():
        _RS_NARC_CACHE = []
        return []
    from gen4_data import Narc
    narc = Narc(path)
    result = []
    for i in range(narc.count):
        data = narc.file(i)
        if len(data) >= 4:
            odds, typ = _struct.unpack_from("<HH", data, 0)
            result.append((odds, typ))
        else:
            result.append((0, 0))
    _RS_NARC_CACHE = result
    return result


def render_rock_smash_item_section(zone_id: int) -> list[str]:
    """Return Rock Smash item drop lines for the given zone_id, or []."""
    table = _load_rock_smash_narc()
    if not table or zone_id < 0 or zone_id >= len(table):
        return []
    odds, typ = table[zone_id]
    if odds == 0:
        return []
    rs_tables, rs_probs = _load_rs_tables()
    items = rs_tables.get(typ)
    if not items:
        return []
    SEP = "=" * 70
    lines = ["", SEP, "  Rock Smash Item Drops  (HeartGold)", SEP, ""]
    lines.append(f"  Base drop chance: {odds}%  (type {typ})")
    lines.append("")
    for item, prob in zip(items, rs_probs):
        lines.append(f"    {prob:>3}%  {item}")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Encounter data
# ---------------------------------------------------------------------------

_ENC_DATA_CACHE: dict | None = None


def _load_encounter_data() -> dict[str, dict]:
    """Return {map_code: entry} from gs_enc_data.json."""
    global _ENC_DATA_CACHE
    if _ENC_DATA_CACHE is not None:
        return _ENC_DATA_CACHE
    path = (
        BASE_DIR
        / "pokeheartgold/files/fielddata/encountdata/gs_enc_data.json"
    )
    if not path.exists():
        _ENC_DATA_CACHE = {}
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    _ENC_DATA_CACHE = {
        e["map"]: e
        for e in raw.get("encounters", [])
        if e.get("map")
    }
    return _ENC_DATA_CACHE


def _hg_species(sp: object) -> str:
    """Resolve species to HeartGold string."""
    if isinstance(sp, str):
        return sp.replace("SPECIES_", "").replace("_", " ").title()
    if isinstance(sp, dict):
        hg = sp.get("HEARTGOLD") or sp.get("gold")
        val = hg if hg else next(iter(sp.values()), "?")
        return _hg_species(val)
    return str(sp)


def _hg_level(lvl: object) -> str:
    """Resolve level to a display string."""
    if isinstance(lvl, int):
        return f"Lv {lvl}"
    if isinstance(lvl, dict):
        mn = lvl.get("min") or lvl.get("HEARTGOLD") or lvl.get("gold")
        mx = lvl.get("max") or lvl.get("SOULSILVER") or lvl.get("silver")
        hg_mn = mn.get("HEARTGOLD", mn) if isinstance(mn, dict) else mn
        hg_mx = mx.get("HEARTGOLD", mx) if isinstance(mx, dict) else mx
        if hg_mn == hg_mx:
            return f"Lv {hg_mn}"
        return f"Lv {hg_mn}-{hg_mx}"
    return str(lvl)


def _agg_encounter_mons(mons: list) -> list[tuple[str, str, int]]:
    """Aggregate slots by species → [(name, level_str, count)]."""
    agg: dict = {}
    for m in mons:
        sp  = _hg_species(m.get("species", "?"))
        lvl = _hg_level(m.get("level", 0))
        key = (sp, lvl)
        agg[key] = agg.get(key, 0) + 1
    return [(sp, lvl, cnt) for (sp, lvl), cnt in sorted(agg.items(), key=lambda x: -x[1])]


def _render_encounter_table_hg(label: str, mons: list, rate: int | None = None) -> list[str]:
    if not mons:
        return []
    rate_note = f"  (rate: {rate})" if rate else ""
    lines = [f"  {label}{rate_note}"]
    for sp, lvl, cnt in _agg_encounter_mons(mons):
        slot_pct = cnt * 100 // len(mons)
        lines.append(f"    {sp:<22} {lvl:<12} {slot_pct:>3}%")
    return lines


def render_encounter_section(file_code: str) -> list[str]:
    """Return formatted encounter lines for the given file_code, or empty list."""
    if not file_code:
        return []
    enc_data = _load_encounter_data()
    entry = enc_data.get(file_code)
    if not entry:
        return []

    SEP = "=" * 70
    lines = ["", SEP, "  Wild Encounters", SEP, ""]

    land = entry.get("land", {})
    if land.get("rate", 0) > 0 and land.get("mons"):
        lines += _render_encounter_table_hg("Tall Grass", land["mons"], land.get("rate"))
        lines.append("")

    # Time-of-day splits: show each time if any species differs
    if land.get("mons"):
        mons = land["mons"]
        morn_mons, day_mons, nite_mons = [], [], []
        for m in mons:
            sp = m.get("species", {})
            if isinstance(sp, dict) and ("morn" in sp or "day" in sp or "nite" in sp):
                lvl = m.get("level", 0)
                if sp.get("morn"):
                    morn_mons.append({"species": sp["morn"], "level": lvl})
                if sp.get("day"):
                    day_mons.append({"species": sp["day"], "level": lvl})
                if sp.get("nite"):
                    nite_mons.append({"species": sp["nite"], "level": lvl})
        if morn_mons or nite_mons:
            if morn_mons:
                lines += _render_encounter_table_hg("  Morning", morn_mons)
                lines.append("")
            if day_mons:
                lines += _render_encounter_table_hg("  Day", day_mons)
                lines.append("")
            if nite_mons:
                lines += _render_encounter_table_hg("  Night", nite_mons)
                lines.append("")

    # Swarm
    swarm = entry.get("landSwarm")
    if swarm:
        sp_str = _hg_species(swarm)
        lines.append(f"  Swarm: {sp_str}")
        lines.append("")

    surf = entry.get("surf", {})
    if surf.get("rate", 0) > 0 and surf.get("mons"):
        lines += _render_encounter_table_hg("Surfing", surf["mons"], surf.get("rate"))
        lines.append("")
    surf_swarm = entry.get("surfSwarm")
    if surf_swarm:
        lines.append(f"  Surf Swarm: {_hg_species(surf_swarm)}")
        lines.append("")

    fishing = entry.get("fishing", {})
    for rod in ("old_rod", "good_rod", "super_rod"):
        rod_data = fishing.get(rod, {})
        if rod_data.get("mons"):
            label = rod.replace("_", " ").title()
            lines += _render_encounter_table_hg(f"Fishing — {label}", rod_data["mons"],
                                                 rod_data.get("rate"))
            lines.append("")
    night_fish = entry.get("nightFish")
    if night_fish and isinstance(night_fish, str):
        lines.append(f"  Night fishing (replaces Good Rod slot 2): {_hg_species(night_fish)}")
        lines.append("")
    fish_swarm = entry.get("fishSwarm")
    if fish_swarm:
        lines.append(f"  Fish Swarm: {_hg_species(fish_swarm)}")
        lines.append("")

    rock_smash = entry.get("rock_smash", {})
    if rock_smash.get("rate", 0) > 0 and rock_smash.get("mons"):
        lines += _render_encounter_table_hg("Rock Smash", rock_smash["mons"],
                                             rock_smash.get("rate"))
        lines.append("")

    # Radio swarms (Hoenn / Sinnoh sound)
    hoenn = entry.get("hoenn")
    if hoenn:
        sp_str = _hg_species(hoenn)
        lines.append(f"  Hoenn Sound (radio): {sp_str}")
        lines.append("")
    sinnoh = entry.get("sinnoh")
    if sinnoh:
        sp_str = _hg_species(sinnoh)
        lines.append(f"  Sinnoh Sound (radio): {sp_str}")
        lines.append("")

    return lines


# ---------------------------------------------------------------------------
# Safari Zone encounters (area-based, dynamically assigned by Pokégear)
# ---------------------------------------------------------------------------

_HG_SAFARI_ENC_PATH = BASE_DIR / "pokeheartgold/files/arc/safari_enc.json"
_HG_SAFARI_ENC_CACHE: list | None = None


def _load_hg_safari_enc() -> list[dict]:
    global _HG_SAFARI_ENC_CACHE
    if _HG_SAFARI_ENC_CACHE is not None:
        return _HG_SAFARI_ENC_CACHE
    if not _HG_SAFARI_ENC_PATH.exists():
        _HG_SAFARI_ENC_CACHE = []
        return []
    with open(_HG_SAFARI_ENC_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    _HG_SAFARI_ENC_CACHE = raw.get("encounters", [])
    return _HG_SAFARI_ENC_CACHE


def _render_tod_tables(indent: str, section_label: str, mons_raw: object) -> list[str]:
    """Render encounter tables that may be split by time-of-day."""
    lines: list[str] = []
    if isinstance(mons_raw, dict):
        for time_key, tod_label in (("morn", "Morning"), ("day", "Day"), ("nite", "Night")):
            slot_list = mons_raw.get(time_key, [])
            if slot_list:
                lines += _render_encounter_table_hg(f"{indent}{section_label} — {tod_label}", slot_list)
    elif isinstance(mons_raw, list) and mons_raw:
        lines += _render_encounter_table_hg(f"{indent}{section_label}", mons_raw)
    return lines


def _render_safari_area(area: dict) -> list[str]:
    """Render one safari area's encounter tables."""
    area_name = area["area"].replace("SAFARI_ZONE_AREA_", "").replace("_", " ").title()
    lines = [f"  ── {area_name} ──"]
    land_mons = area.get("land", {}).get("mons", {})
    lines += _render_tod_tables("    ", "Tall Grass", land_mons)
    surf_mons = area.get("surf", {}).get("mons", {})
    lines += _render_tod_tables("    ", "Surfing", surf_mons)
    for rod_key, rod_label in (("oldrod", "Old Rod"), ("goodrod", "Good Rod"), ("superrod", "Super Rod")):
        rod_mons = area.get(rod_key, {}).get("mons", {})
        lines += _render_tod_tables("    ", f"Fishing — {rod_label}", rod_mons)
    return lines


def render_hg_safari_section(internal_code: str) -> list[str]:
    """Return Safari Zone encounter section.

    On the gate (MAP_D47) show all 12 area tables.
    On a zone block (MAP_SAF*) show a pointer note only.
    """
    is_gate = (internal_code == "MAP_D47")
    is_saf  = internal_code.startswith("MAP_SAF")
    if not is_gate and not is_saf:
        return []

    SEP = "=" * 70
    lines = ["", SEP, "  HeartGold Safari Zone  (area encounters)", SEP, ""]

    if is_saf:
        lines.append("  Area assignments are player-configured (6 of 12 areas active at once).")
        lines.append("  See the Safari Zone Gate (MAP_D47) render for all area encounter tables.")
        return lines

    areas = _load_hg_safari_enc()
    if not areas:
        lines.append("  [safari_enc.json not found]")
        return lines

    lines.append("  6 of these 12 areas are active at once; the player assigns them via Pokégear.")
    lines.append("")
    for area in areas:
        lines += _render_safari_area(area)
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Headbutt tree data
# ---------------------------------------------------------------------------

_HEADBUTT_CACHE: list | None = None


def _load_headbutt_tables() -> list[dict]:
    global _HEADBUTT_CACHE
    if _HEADBUTT_CACHE is not None:
        return _HEADBUTT_CACHE
    path = BASE_DIR / "pokeheartgold/files/arc/headbutt.json"
    if not path.exists():
        _HEADBUTT_CACHE = []
        return []
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    _HEADBUTT_CACHE = raw.get("tables", [])
    return _HEADBUTT_CACHE


def _headbutt_map_code(internal_code: str) -> str | None:
    """
    Infer headbutt table map code from the zone's internal_code field.
    internal_code format: "MAP_R27", "MAP_T25", "MAP_R47", etc.
    Headbutt table uses the suffix: "R27", "T25", "R47".
    """
    m = re.match(r"MAP_(.+)", internal_code or "")
    if not m:
        return None
    code = m.group(1)
    # Only route codes (R##) and city codes (T##) have headbutt tables
    if re.match(r"[RT]\d+$", code):
        return code
    return None


_ZONE_EVENT_DIR = BASE_DIR / "pokeheartgold/files/fielddata/eventdata/zone_event"
_ZONE_EVENT_INDEX: dict[str, Path] | None = None


def _zone_event_file(internal_code: str) -> Path | None:
    """
    Locate a map's event JSON.

    The header table's `eventsBank` names the file outright, which is what the
    game itself loads. Falling back to a CODE-suffix scan of the directory
    keeps the handful of codes with no header entry working; the numeric
    prefix is that file's own numbering and never matches the map id (Route 30
    is map 34 but event file 031), so it is not usable as a key.
    """
    entry = _registry_by_code(internal_code)
    if entry is not None and entry["events_file"].exists():
        return entry["events_file"]

    global _ZONE_EVENT_INDEX
    if _ZONE_EVENT_INDEX is None:
        _ZONE_EVENT_INDEX = {}
        for p in _ZONE_EVENT_DIR.glob("*_*.json"):
            _ZONE_EVENT_INDEX.setdefault(p.stem.split("_", 1)[1], p)
    code = re.sub(r"^MAP_", "", internal_code or "")
    return _ZONE_EVENT_INDEX.get(code)


def zone_warps(internal_code: str) -> list[dict]:
    """
    Warps for a zone, in world tile coords, straight from the decomp's own
    event data. maps_pokeheartgold.json's `warps` are misaligned (Route 30's
    point at Union Cave); these are authoritative.
    """
    path = _zone_event_file(internal_code)
    if path is None:
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("warps") or []


# Keywords in a warp's destination `header` → (map char, display label).
#
# Order matters: the first substring hit wins, so a longer name that contains a
# shorter one must come first — GATEHOUSE before GATE, DEPARTMENT_STORE before
# MART (Goldenrod's is MAP_GOLDENROD_DEPARTMENT_STORE_1F), POKEMON_SCHOOL
# before SCHOOL. Chars follow Platinum's (see platinum_data._BUILDING_WARP_TYPES)
# wherever the two games share a building type, so the maps read alike.
#
# 'H' is deliberately NOT used: in HG it is already the headbutt tree, which is
# the single most common letter in the outdoor renders. 'c' and 't' do collide
# with the cave-floor and trash-can behaviours, but those are interior-only and
# 'c' means "cave" in both readings anyway.
_BUILDING_WARP_TYPES: list[tuple[str, str, str]] = [
    ("GATEHOUSE",          "g", "Gatehouse"),
    ("GATE",               "g", "Gate"),
    ("POKECENTER",         "P", "Pokémon Center"),
    ("POKEMON_CENTER",     "P", "Pokémon Center"),
    ("DEPARTMENT_STORE",   "M", "Department Store"),
    ("POKEMART",           "$", "PokéMart"),
    ("MART",               "$", "PokéMart"),
    ("PHARMACY",           "M", "Pharmacy"),
    ("SHOP",               "$", "Shop"),
    ("RESTAURANT",         "M", "Restaurant"),
    ("CAFE",               "M", "Café"),
    ("GYM",                "G", "Gym"),
    ("DOJO",               "G", "Fighting Dojo"),
    ("POKEMON_SCHOOL",     "S", "Pokémon School"),
    ("SCHOOL",             "S", "School"),
    ("GLOBAL_TERMINAL",    "Q", "Global Terminal"),
    ("GAME_CORNER",        "X", "Game Corner"),
    ("PRIZE_CORNER",       "X", "Prize Corner"),
    ("THEATER",            "X", "Dance Theater"),
    ("MAGNET_TRAIN",       "T", "Magnet Train Station"),
    ("RADIO",              "⌁", "Radio Tower"),
    ("POKEMON_LEAGUE",     "F", "Pokémon League"),
    ("EMBEDDED_TOWER",     "V", "Embedded Tower"),
    ("BELL_TOWER",         "V", "Bell Tower"),
    ("BELLCHIME",          "V", "Bellchime Tower"),
    ("SPROUT_TOWER",       "V", "Sprout Tower"),
    ("BURNED_TOWER",       "V", "Burned Tower"),
    ("SHRINE",             "V", "Shrine"),
    ("BATTLE_",            "Z", "Battle facility"),
    ("FRONTIER",           "Z", "Battle Frontier"),
    ("LIGHTHOUSE",         "l", "Lighthouse"),
    ("RESEARCH",           "K", "Research Center"),
    ("SILPH_CO",           "K", "Silph Co."),
    ("ROCKET_HEADQUARTERS", "K", "Rocket HQ"),
    ("POWER_PLANT",        "K", "Power Plant"),
    ("MUSEUM",             "W", "Museum"),
    ("KILN",               "K", "Charcoal Kiln"),
    ("LAB",                "K", "Lab"),
    ("SAFARI",             "N", "Safari Zone"),
    ("PAL_PARK",           "N", "Pal Park"),
    ("DAYCARE",            "d", "Day-Care"),
    ("HOUSE",              "h", "House"),
    ("CONDOMINIUM",        "h", "Condominium"),
    ("COTTAGE",            "h", "Cottage"),
    ("STABLE",             "h", "Stable"),
    ("NAME_RATER",         "h", "Name Rater's house"),
    ("FAN_CLUB",           "h", "Pokémon Fan Club"),
    ("UNDERGROUND",        "t", "Underground path"),
    ("TUNNEL",             "t", "Tunnel"),
    ("CAVE",               "c", "Cave"),
    ("WELL",               "c", "Well"),
    ("ICE_PATH",           "c", "Ice Path"),
    ("MOUNT_",             "c", "Mountain"),
    ("VICTORY_ROAD",       "c", "Victory Road"),
    ("WHIRL_ISLANDS",      "c", "Whirl Islands"),
    ("SEAFOAM",            "c", "Seafoam Islands"),
    ("TOHJO",              "c", "Tohjo Falls"),
    ("DRAGONS_DEN",        "c", "Dragon's Den"),
    ("RUINS_OF_ALPH_",     "c", "Ruins of Alph"),
    ("FOREST",             "f", "Forest"),
    ("SS_AQUA",            "A", "S.S. Aqua"),
    ("PORT",               "A", "Port"),
]


#: Legend text per warp char. Several keywords share a char ($ covers marts,
#: pharmacies and the bike shop), so the legend needs one canonical wording
#: rather than whichever destination the map happened to list first.
WARP_CHAR_DESC: dict[str, str] = {
    "g": "Gate / gatehouse",
    "P": "Pokémon Center",
    "$": "Shop / PokéMart",
    "M": "Department Store / specialty shop",
    "G": "Gym",
    "S": "School",
    "Q": "Global Terminal",
    "T": "Magnet Train Station",
    "⌁": "Radio Tower",
    "F": "Pokémon League",
    "X": "Game Corner",
    "V": "Tower / shrine",
    "Z": "Battle facility",
    "l": "Lighthouse",
    "K": "Lab / research facility",
    "W": "Museum / exhibit",
    "N": "Safari Zone / Pal Park",
    "d": "Day-Care",
    "h": "House",
    "t": "Tunnel / underground path",
    "c": "Cave / dungeon",
    "f": "Forest",
    "A": "Ship / port",
    "D": "Door",
}


def classify_warp(header: str) -> tuple[str, str] | None:
    """(char, label) for a warp destination, or None for a generic door."""
    uhdr = (header or "").upper()
    for keyword, char, label in _BUILDING_WARP_TYPES:
        if keyword in uhdr:
            return char, label
    return None


def zone_warp_chars(internal_code: str, col_off: int, row_off: int) -> list[dict]:
    """
    Every warp in a zone as {c, r, header, char, label} in grid-local tiles.

    Unclassified destinations keep the plain 'D' the metatile behaviour would
    have drawn anyway, so nothing is lost by a header this table doesn't know.
    """
    out = []
    for w in zone_warps(internal_code):
        x, z = w.get("x"), w.get("z")
        if not (isinstance(x, int) and isinstance(z, int)):
            continue
        header = str(w.get("header", ""))
        hit = classify_warp(header)
        out.append({
            "c": x - col_off, "r": z - row_off, "header": header,
            "char":  hit[0] if hit else "D",
            "label": hit[1] if hit else "Door",
            "anchor": w.get("anchor"),
        })
    return out


# ---------------------------------------------------------------------------
# Warp destination resolution

_HG_DEST_WARP_CACHE:  "dict[str, list]" = {}
_HG_REVERSE_WARP:     "dict[str, list[tuple[str, int, int]]] | None" = None
_HG_SHARED_OFFSETS:   "dict[str, tuple[int, int]] | None" = None


def _hg_shared_offsets() -> "dict[str, tuple[int, int]]":
    """
    {map_constant → (tile_col_min, tile_row_min)} for every zone that sits on
    the shared world matrix.  Built once by scanning the matrix zone-ID headers.
    """
    global _HG_SHARED_OFFSETS
    if _HG_SHARED_OFFSETS is not None:
        return _HG_SHARED_OFFSETS
    m = _get_matrix()
    w = m["width"]
    zone_blocks: "dict[int, list[tuple[int,int]]]" = {}
    for i, zone_id in enumerate(m["headers"]):
        if zone_id is None or zone_id <= 0:
            continue
        zone_blocks.setdefault(zone_id, []).append((i // w, i % w))
    reg = _map_registry()
    zone_to_const: "dict[int, str]" = {
        e["zone_id"]: const
        for const, e in reg.items()
        if e.get("zone_id") is not None and not e.get("own_matrix")
    }
    result: "dict[str, tuple[int,int]]" = {}
    for zone_id, blocks in zone_blocks.items():
        cluster = _largest_cluster(blocks)
        if not cluster:
            continue
        r_min = min(r for r, _ in cluster)
        c_min = min(c for _, c in cluster)
        const = zone_to_const.get(zone_id)
        if const:
            result[const] = (c_min * BLOCK_TILES, r_min * BLOCK_TILES)
    _HG_SHARED_OFFSETS = result
    return result


def hg_dest_warp_coord(dest_header: str, anchor: int) -> "tuple[int, int] | None":
    """Return (col, row) spawn coord in dest_header zone for this anchor, or None."""
    if anchor == 256:
        return None
    reg = _map_registry()
    dest = reg.get(dest_header)
    if dest is None:
        return None
    ev_path = dest.get("events_file")
    if ev_path is None:
        return None
    key = str(ev_path)
    if key not in _HG_DEST_WARP_CACHE:
        try:
            _HG_DEST_WARP_CACHE[key] = json.loads(
                Path(ev_path).read_text(encoding="utf-8")
            ).get("warps") or []
        except (OSError, json.JSONDecodeError):
            _HG_DEST_WARP_CACHE[key] = []
    warps = _HG_DEST_WARP_CACHE[key]
    if not (0 <= anchor < len(warps)):
        return None
    w = warps[anchor]
    if dest.get("own_matrix"):
        col_off, row_off = 0, 0
    else:
        col_off, row_off = _hg_shared_offsets().get(dest_header, (0, 0))
    try:
        return int(w["x"]) - col_off, int(w["z"]) - row_off
    except (KeyError, TypeError, ValueError):
        return None


_HG_DIR_OFFSETS = [("N", -1, 0), ("S", 1, 0), ("W", 0, -1), ("E", 0, 1)]


def hg_get_connections(zone_id: int) -> "list[dict]":
    """
    Adjacent maps in the shared world matrix for this zone, with direction.
    Returns list of {direction, dest} sorted by direction.
    Maps that own their own matrix (caves, buildings) always return [].
    """
    from terrain_helpers import skip_map
    m = _get_matrix()
    width   = m["width"]
    headers = m["headers"]
    reg     = _map_registry()
    zone_to_const: "dict[int, str]" = {
        e["zone_id"]: const
        for const, e in reg.items()
        if e.get("zone_id") is not None and not e.get("own_matrix")
        and not skip_map("heartgold", const)
    }
    src_const = zone_to_const.get(zone_id)
    if src_const is None:
        return []
    seen: set[str] = set()
    connections: list[dict] = []
    for i, z in enumerate(headers):
        if z != zone_id:
            continue
        row, col = divmod(i, width)
        for direction, dr, dc in _HG_DIR_OFFSETS:
            nr, nc = row + dr, col + dc
            if not (0 <= nr and 0 <= nc < width):
                continue
            j = nr * width + nc
            if j >= len(headers):
                continue
            dst_const = zone_to_const.get(headers[j])
            if not dst_const or dst_const == src_const:
                continue
            key = f"{direction}:{dst_const}"
            if key in seen:
                continue
            seen.add(key)
            connections.append({"direction": direction, "dest": dst_const})
    connections.sort(key=lambda c: c["direction"])
    return connections


def hg_dynamic_warp_floors(elev_const: str) -> "list[tuple[str, int, int]]":
    """
    All maps that have a static warp pointing into elev_const, as
    (floor_const, local_col, local_row).  Used for elevator/multi-exit maps
    whose outbound warps are dynamic (anchor=256).
    """
    global _HG_REVERSE_WARP
    if _HG_REVERSE_WARP is None:
        _HG_REVERSE_WARP = {}
        reg = _map_registry()
        for const, entry in reg.items():
            ev_path = entry.get("events_file")
            if ev_path is None or str(ev_path).endswith("000_DUMMY.json"):
                continue
            try:
                warps = json.loads(
                    Path(ev_path).read_text(encoding="utf-8")
                ).get("warps") or []
            except (OSError, json.JSONDecodeError):
                continue
            col_off = entry.get("_tile_col_min", 0)
            row_off = entry.get("_tile_row_min", 0)
            for w in warps:
                dest = str(w.get("header", ""))
                lx = int(w.get("x", 0)) - col_off
                lz = int(w.get("z", 0)) - row_off
                _HG_REVERSE_WARP.setdefault(dest, []).append((const, lx, lz))
    return _HG_REVERSE_WARP.get(elev_const, [])


def zone_signs(internal_code: str) -> list[dict]:
    """
    Signposts for a zone, in world tile coords, from the decomp's event data.

    The `bgs` list mixes two things and the `type` field separates them:
    type 1 is a readable sign, type 2 a hidden item. Verified on Route 27 —
    both type-1 entries land exactly on the hand-marked § tiles, and none of
    the three type-2 entries do.
    """
    path = _zone_event_file(internal_code)
    if path is None:
        return []
    with open(path, encoding="utf-8") as f:
        bgs = json.load(f).get("bgs") or []
    return [b for b in bgs if b.get("type") == 1
            and isinstance(b.get("x"), int) and isinstance(b.get("z"), int)]


def _headbutt_tree_positions(internal_code: str) -> list[tuple[int, int]]:
    """World (x, z) tiles of every headbutt tree in this zone, normal + secret."""
    code = _headbutt_map_code(internal_code)
    if not code:
        return []
    entry = next((t for t in _load_headbutt_tables() if t.get("Map") == code), None)
    if entry is None:
        return []
    out: list[tuple[int, int]] = []
    for key in ("Trees", "SecretTrees"):
        for group in entry.get(key) or []:
            for p in group:
                out.append((p["x"], p["y"]))
    return out


def render_headbutt_section(zone_name: str, internal_code: str) -> list[str]:
    """Return formatted headbutt encounter lines, or empty list."""
    code = _headbutt_map_code(internal_code)
    if not code:
        return []
    tables = _load_headbutt_tables()
    table = next((t for t in tables if t.get("Map") == code), None)
    if not table:
        return []
    has_data = (table.get("CommonMons") or table.get("RareMons")
                or table.get("SecretMons"))
    if not has_data:
        return []

    SEP = "=" * 70
    lines = ["", SEP, "  Headbutt Trees", SEP, ""]

    def _render_group(label: str, mons: list) -> None:
        if not mons:
            return
        lines.append(f"  {label}:")
        for m in mons:
            sp  = _hg_species(m.get("species", "?"))
            mn  = m.get("minLevel", "?")
            mx  = m.get("maxLevel", "?")
            lvl = f"Lv {mn}" if mn == mx else f"Lv {mn}-{mx}"
            lines.append(f"    {sp:<22} {lvl}")
        lines.append("")

    _render_group("Common Trees", table.get("CommonMons", []))
    _render_group("Rare Trees", table.get("RareMons", []))
    _render_group("Secret Trees", table.get("SecretMons", []))

    n_trees = len(table.get("Trees", []))
    n_secret = len(table.get("SecretTrees", []))
    if n_trees or n_secret:
        lines.append(f"  Tree count: {n_trees} normal, {n_secret} secret")
        lines.append("")

    return lines


# ---------------------------------------------------------------------------
# Trainers, NPCs and field events
# ---------------------------------------------------------------------------
#
# HeartGold keeps all of this in far fewer places than Platinum:
#
#   trainers.json   one flat list, positionally indexed by trainer ID, with the
#                   party AND the battle dialogue inline — no message lookup
#   zone_event      each object names its trainer in `scriptId` as the literal
#                   text "std_trainer(TRAINER_X)", so no script scan is needed
#                   to find battles (Platinum needs one)
#   overlay_26      the rematch table; HG has no VS Seeker, rematches come from
#                   Pokégear phone calls
#   overlay_12 asm  the prize-money multiplier table, still un-decompiled
#
# Only ordinary NPC dialogue needs the script walk, because an object's script
# is a label, not a message.

TRAINERS_JSON = BASE_DIR / "pokeheartgold/files/poketool/trainer/trainers.json"
TRAINERS_H    = BASE_DIR / "pokeheartgold/include/constants/trainers.h"
#: sPrizeMoneyTbl lives here rather than in C — battle_command.c declares it
#: `extern` and the data was never decompiled.
PRIZE_ASM     = BASE_DIR / "pokeheartgold/asm/overlay_12_battle_command.s"
REMATCH_C     = BASE_DIR / "pokeheartgold/src/overlay_26_022598C0.c"
SCRIPT_DIR    = BASE_DIR / "pokeheartgold/files/fielddata/script/scr_seq"
MSG_DIR       = BASE_DIR / "pokeheartgold/files/msgdata/msg"


_TRAINERS: list | None = None
_TRAINER_IDS: dict | None = None
_PRIZE_MUL: dict | None = None
_REMATCH_SETS: list | None = None


def _load_trainers() -> list:
    """The trainers.json array. Index == trainer ID (0 is TRAINER_NONE)."""
    global _TRAINERS
    if _TRAINERS is None:
        try:
            with open(TRAINERS_JSON, encoding="utf-8") as f:
                _TRAINERS = json.load(f).get("trainers") or []
        except Exception:
            _TRAINERS = []
    return _TRAINERS


def _trainer_ids() -> dict:
    """{TRAINER_CONSTANT: id}. 738 entries, matching trainers.json exactly."""
    global _TRAINER_IDS
    if _TRAINER_IDS is None:
        try:
            text = TRAINERS_H.read_text(encoding="utf-8")
        except Exception:
            text = ""
        _TRAINER_IDS = {n: int(v) for n, v
                        in re.findall(r"#define\s+(TRAINER_\w+)\s+(\d+)\b", text)}
    return _TRAINER_IDS


def _trainer_data(const: str) -> dict | None:
    tid = _trainer_ids().get(const)
    tr  = _load_trainers()
    if tid is None or not (0 < tid < len(tr)):
        return None
    return tr[tid]


def _prize_multipliers() -> dict:
    """{TRAINERCLASS_X: multiplier} from the asm table (129 rows)."""
    global _PRIZE_MUL
    if _PRIZE_MUL is None:
        _PRIZE_MUL = {}
        try:
            text = PRIZE_ASM.read_text(encoding="utf-8")
        except Exception:
            text = ""
        m = re.search(r"sPrizeMoneyTbl:.*?\n"
                      r"((?:\s*\.short\s+TRAINERCLASS_\w+,\s*\d+\s*\n)+)", text)
        if m:
            for cls, mul in re.findall(r"\.short\s+(TRAINERCLASS_\w+),\s*(\d+)",
                                       m.group(1)):
                _PRIZE_MUL.setdefault(cls, int(mul))
    return _PRIZE_MUL


def _rematch_sets() -> list:
    """
    sTrainerRematchSets rows, as lists of trainer constants.

    Column 0 is the key the game looks up by; columns 1-5 are the progressive
    rematch parties, terminated by TRAINER_NONE. Note the key is not always the
    "first" battle — Bird Keeper Jose's row is keyed on his _2 entry.
    """
    global _REMATCH_SETS
    if _REMATCH_SETS is None:
        _REMATCH_SETS = []
        try:
            text = REMATCH_C.read_text(encoding="utf-8")
        except Exception:
            text = ""
        m = re.search(r"sTrainerRematchSets\[\]\[6\]\s*=\s*\{(.*?)\n\};", text, re.S)
        if m:
            for line in m.group(1).splitlines():
                row = re.findall(r"TRAINER_\w+", line)
                if len(row) == 6:
                    _REMATCH_SETS.append(row)
    return _REMATCH_SETS


def _rematch_chain(const: str) -> list[str]:
    """
    Phone-rematch trainer constants for a base trainer, in progression order.

    Dedupes and drops the base itself, so what comes back is only the battles
    that differ from the one standing on the map.
    """
    for row in _rematch_sets():
        if row[0] != const:
            continue
        out: list[str] = []
        for c in row[1:]:
            if c == "TRAINER_NONE":
                break
            if c != const and c not in out:
                out.append(c)
        return out
    return []


def _trainer_party(td: dict) -> list[dict]:
    result = []
    for p in (td.get("party") or []):
        mon: dict = {
            "species": _hg_species(p.get("species", "?")),
            "level":   p.get("level", 0),
            "moves":   [move_display(m) for m in (p.get("moves") or [])],
            "item":    (p.get("item") or "").replace("ITEM_", "").replace("_", " ").title(),
            # 0-255 "difficulty" is HG's IV field: the same byte DPPt calls ivs.
            "difficulty": p.get("difficulty", 0),
        }
        gender = p.get("genderOverride", "")
        if gender and gender != "TRPOKE_GENDER_OVERRIDE_OFF":
            mon["gender_override"] = gender.replace("TRPOKE_GENDER_OVERRIDE_", "").title()
        ability = p.get("abilityOverride", "")
        if ability and ability != "TRPOKE_ABILITY_OVERRIDE_OFF":
            mon["ability_override"] = ability.replace("TRPOKE_ABILITY_OVERRIDE_", "").title()
        result.append(mon)
    return result


def _trainer_prize(td: dict) -> int | None:
    """
    CalcPrizeMoney (battle_command.c): last party member's level × 4 × the
    class multiplier, doubled for a double battle. It is the LAST mon, not the
    highest-levelled one — HG parties are authored in ascending order so the two
    usually coincide, but not always.
    """
    party = td.get("party") or []
    mul   = _prize_multipliers().get(td.get("class", ""))
    if not party or mul is None:
        return None
    prize = party[-1].get("level", 0) * 4 * mul
    return prize * 2 if td.get("double") else prize


_HG_AI_FLAG_NAMES: list[str] = [
    "BASIC", "EVAL_ATTACK", "EXPERT", "SETUP_FIRST_TURN", "RISKY",
    "PRIORITIZE_EXTREMES", "BATON_PASS", "TAG_STRATEGY", "CHECK_HP",
    "WEATHER", "HARRASSMENT",
]


def _decode_hg_ai_flags(mask: int) -> list[str]:
    return [name for i, name in enumerate(_HG_AI_FLAG_NAMES) if mask & (1 << i)]


def _build_trainer(x: int, z: int, const: str, td: dict,
                   category: str = "Standard") -> dict:
    return {
        "x": x, "z": z,
        "const":    const,
        "category": category,
        # The name carries a "{TRNAME}" control code that switches the game's
        # font to the trainer-name typeface; it is not part of the name.
        "name":     (td.get("name") or "?").replace("{TRNAME}", "").strip(" -"),
        "class":    td.get("class", ""),
        "double":   bool(td.get("double")),
        "ai_flags": _decode_hg_ai_flags(td.get("ai_flags") or 0),
        "items":    [i.replace("ITEM_", "").replace("_", " ").title()
                     for i in (td.get("items") or []) if i],
        "messages": {m.get("type", ""): m.get("message", "")
                     for m in (td.get("messages") or [])},
        "party":    _trainer_party(td),
        "prize":    _trainer_prize(td),
        "rematches": [
            {"const": rc, "party": _trainer_party(rtd), "prize": _trainer_prize(rtd)}
            for rc in _rematch_chain(const)
            if (rtd := _trainer_data(rc))
        ],
    }


# ---------------------------------------------------------------------------
# Script / message resolution (ordinary NPC dialogue)
# ---------------------------------------------------------------------------

_SCRIPT_INDEX: dict | None = None
_SCRIPT_BLOCKS: dict = {}
_GMM_CACHE: dict = {}

#: Every script command that pushes a string on screen. Chosen by surveying all
#: 965 scr_seq sources; nothing else takes a msg_* symbol.
_MSG_CMD = re.compile(
    r"^\s*(?:NPCMsg|SimpleNPCMsg|NonNPCMsg|GenderMsgBox|DirectionSignpost"
    r"|TrainerTips|NPCMsgVar)\s+(msg_\w+)")
#: GoTo / Jump / Call and all their conditional variants (CallIfSet, JumpIfUnset,
#: …) plus `Case n, _label`. The target is always the last token on the line.
_JUMP_CMD = re.compile(r"^\s*(?:\w*(?:GoTo|Jump|Call)\w*|Case)\s+.*?"
                       r"(_[0-9A-F]{4}|scr_seq_\w+)\s*$")

# ---------------------------------------------------------------------------
# std_* script resolution (CallStd routes into scr_seq_0003.s)
# ---------------------------------------------------------------------------

# All std_XXX integer values from std_script.h — loaded once.
_HG_STD_CONSTS: dict[str, int] | None = None
_HG_STD_MISC_BLOCKS: dict[str, str] | None = None

_HG_STD_MISC_BASE = 2000   # _std_misc threshold; offsets from here index scr_seq_0003
_HG_STD_MISC_MSG  = "msg_0040"  # message file used by scr_seq_0003


def _hg_std_constants() -> dict[str, int]:
    global _HG_STD_CONSTS
    if _HG_STD_CONSTS is None:
        _HG_STD_CONSTS = {}
        p = BASE_DIR / "pokeheartgold/include/constants/std_script.h"
        if p.exists():
            for m in re.finditer(r"#define\s+(std_\w+)\s+(\d+)", p.read_text()):
                _HG_STD_CONSTS[m.group(1)] = int(m.group(2))
    return _HG_STD_CONSTS


def _hg_std_misc_blocks() -> dict[str, str]:
    global _HG_STD_MISC_BLOCKS
    if _HG_STD_MISC_BLOCKS is None:
        _HG_STD_MISC_BLOCKS = {}
        p = SCRIPT_DIR / "scr_seq_0003.s"
        if p.exists():
            text = p.read_text(encoding="utf-8")
            for m in re.finditer(r"^(\w+):\n((?:(?!^\w+:).*\n)*)", text, re.M):
                _HG_STD_MISC_BLOCKS.setdefault(m.group(1), m.group(2))
    return _HG_STD_MISC_BLOCKS


def _hg_resolve_std(std_id: int) -> str | None:
    """Return the opening NPC text for a std_* script in scr_seq_0003.s."""
    offset = std_id - _HG_STD_MISC_BASE
    if offset < 0:
        return None
    entry_label = f"scr_seq_0003_{offset:03d}"
    all_blocks = _hg_std_misc_blocks()
    # BFS across sub-labels (e.g. _0175) within scr_seq_0003.s.
    # Carry var_ints across the walk so SetVar before a GoTo is seen by the
    # NonNPCMsgVar in the target block.
    seen: set = set()
    queue = [entry_label]
    var_ints: dict[str, int] = {}
    while queue:
        label = queue.pop(0)
        if label in seen:
            continue
        seen.add(label)
        body = all_blocks.get(label, "")
        for line in body.splitlines():
            line = line.strip()
            # SetVar VAR, N (integer) — track for NonNPCMsgVar
            sv = re.match(r"SetVar\s+(\w+)\s*,\s*(\d+)$", line)
            if sv:
                var_ints[sv.group(1)] = int(sv.group(2))
                continue
            # NonNPCMsgVar / NPCMsgVar → integer index into msg_0040.gmm
            nv = re.match(r"(?:Non)?NPCMsgVar\s+(\w+)$", line)
            if nv:
                idx = var_ints.get(nv.group(1))
                if idx is not None:
                    sym = f"{_HG_STD_MISC_MSG}_{idx:05d}"
                    text = _clean_msg(_msg_text(sym))
                    if text:
                        return text
                continue
            # Direct NPCMsg / SimpleNPCMsg / etc.
            hit = _MSG_CMD.match(line)
            if hit:
                text = _clean_msg(_msg_text(hit.group(1)))
                if text:
                    return text
            # Follow sub-label jumps within scr_seq_0003 blocks
            jm = _JUMP_CMD.match(line)
            if jm:
                tgt = jm.group(1)
                if tgt not in seen and tgt in all_blocks:
                    queue.append(tgt)
    return None


def _script_file(code: str) -> Path | None:
    """The scr_seq source for a zone code, e.g. R30 → scr_seq_0227_R30.s."""
    global _SCRIPT_INDEX
    if _SCRIPT_INDEX is None:
        _SCRIPT_INDEX = {}
        for p in SCRIPT_DIR.glob("scr_seq_[0-9]*_*.s"):
            if p.stem.endswith("_hdr"):
                continue
            parts = p.stem.split("_", 3)
            if len(parts) == 4:
                _SCRIPT_INDEX.setdefault(parts[3], p)
    return _SCRIPT_INDEX.get(code)


def _load_hdr_script(code: str) -> str:
    """Contents of the _hdr.s companion file for a zone, or empty string."""
    for p in SCRIPT_DIR.glob(f"scr_seq_[0-9]*_{code}_hdr.s"):
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    return ""


def _script_blocks(code: str) -> dict:
    """{label: body} for every label in a zone's script, named or numeric."""
    if code in _SCRIPT_BLOCKS:
        return _SCRIPT_BLOCKS[code]
    path = _script_file(code)
    out: dict = {}
    if path is not None:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            text = ""
        for m in re.finditer(r"^(\w+):\n((?:(?!^\w+:).*\n)*)", text, re.M):
            out.setdefault(m.group(1), m.group(2))
    _SCRIPT_BLOCKS[code] = out
    return out


def _msg_text(symbol: str) -> str | None:
    """
    Resolve a msg_NNNN_CODE_NNNNN symbol to its English string.

    The .gmm files are plain XML and the row id IS the symbol, so the archive
    name falls out of the symbol itself — no map-header lookup needed.
    """
    fname = symbol.rsplit("_", 1)[0]
    table = _GMM_CACHE.get(fname)
    if table is None:
        table = {}
        path = MSG_DIR / f"{fname}.gmm"
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                text = ""
            for m in re.finditer(r'<row id="(\w+)".*?'
                                 r'<language name="English">(.*?)</language>',
                                 text, re.S):
                table[m.group(1)] = m.group(2)
        _GMM_CACHE[fname] = table
    return table.get(symbol)


def _clean_msg(text: str | None) -> str | None:
    """Flatten the game's line/page break codes into one readable line."""
    if not text:
        return None
    out = re.sub(r"\\[nfr]", " ", text)
    out = re.sub(r"\{[^}]*\}", "", out)
    return re.sub(r"\s+", " ", out).strip() or None



def _first_message(code: str, label: str, depth: int = 6) -> str | None:
    """
    The first line an NPC says, following control flow when the entry block
    only branches.

    Roughly a third of NPCs open with a flag check that jumps straight to a
    sub-label, so stopping at the entry block loses them; walking the jump
    targets breadth-first takes coverage from 78% to 83% of the 1228 NPCs that
    carry a per-map script label. The rest of those are menu-driven (the Union
    Room lobby dominates) and have no single opening line to quote.

    That 83% is NOT the overall rate: of all 1658 NPCs, 219 point at a shared
    `std_*` script whose body lives in a compiled NARC (not parseable from
    assembly) and 198 have scriptId 0 and no script at all. Overall = 61%.
    HG zone scripts never use the YesNo command — that mechanic only appears
    in scr_seq_0003.s (bike shop) and scr_seq_0166.s (wireless), both global.
    """
    blocks = _script_blocks(code)
    seen: set = set()
    queue: list = [(label, 0)]
    while queue:
        lb, d = queue.pop(0)
        if lb in seen or d > depth:
            continue
        seen.add(lb)
        body = blocks.get(lb)
        if body is None:
            continue
        lines = body.splitlines()
        targets = []
        for line in lines:
            hit = _MSG_CMD.match(line)
            if hit:
                text = _clean_msg(_msg_text(hit.group(1)))
                if text:
                    return text
            # CallStd std_XXX — routes into scr_seq_0003.s for std_misc range
            cs = re.match(r"\s*CallStd\s+(\w+)", line)
            if cs:
                std_id = _hg_std_constants().get(cs.group(1))
                if std_id is not None and std_id >= _HG_STD_MISC_BASE:
                    text = _hg_resolve_std(std_id)
                    if text:
                        return text
                continue
            jump = _JUMP_CMD.match(line)
            if jump:
                targets.append(jump.group(1))
        queue += [(t, d + 1) for t in targets]
    return None


def _item_name(constant: str) -> str:
    """ITEM_POKE_BALL → 'Poke Ball'"""
    return constant.removeprefix("ITEM_").replace("_", " ").title()


def _species_name(constant: str) -> str:
    """SPECIES_BULBASAUR → 'Bulbasaur'"""
    return constant.removeprefix("SPECIES_").replace("_", " ").title()


_HG_ITEM_ID_CACHE: dict[int, str] | None = None


def _hg_item_id_to_name(item_id: int) -> str:
    """Numeric item ID → display name, parsed from items.h."""
    global _HG_ITEM_ID_CACHE
    if _HG_ITEM_ID_CACHE is None:
        _HG_ITEM_ID_CACHE = {}
        items_h = BASE_DIR / "pokeheartgold/include/constants/items.h"
        if items_h.exists():
            for m in re.finditer(
                r"^#define\s+(ITEM_\w+)\s+(\d+)", items_h.read_text(errors="replace"), re.M
            ):
                _HG_ITEM_ID_CACHE[int(m.group(2))] = _item_name(m.group(1))
    return _HG_ITEM_ID_CACHE.get(item_id, f"Item#{item_id}")


_HG_COMMON_MART_CACHE: list | None = None
_HG_SCRCMD_MART_C = BASE_DIR / "pokeheartgold/src/scrcmd_mart.c"


def _load_hg_common_mart() -> list[str]:
    """Parse _020FBF22 badge-tiered mart array from scrcmd_mart.c."""
    global _HG_COMMON_MART_CACHE
    if _HG_COMMON_MART_CACHE is not None:
        return _HG_COMMON_MART_CACHE
    result: list[str] = []
    if _HG_SCRCMD_MART_C.exists():
        text = _HG_SCRCMD_MART_C.read_text(encoding="utf-8")
        start = text.find("_020FBF22[]")
        if start != -1:
            brace = text.find("{", start)
            # Walk to outer closing brace using brace depth
            depth, end = 0, brace
            for i in range(brace, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            block = text[brace:end + 1]
            for m in re.finditer(r"\{\s*(ITEM_\w+)\s*,\s*\d+\s*\}", block):
                result.append(_item_name(m.group(1)))
    _HG_COMMON_MART_CACHE = result
    return result

_HG_SPECIAL_MART_TABLE_CACHE: list | None = None


def _hg_special_mart_table() -> list[list[str]]:
    """Parse scrcmd_mart.c → ordered list of 30 item arrays for _0210FA3C."""
    global _HG_SPECIAL_MART_TABLE_CACHE
    if _HG_SPECIAL_MART_TABLE_CACHE is not None:
        return _HG_SPECIAL_MART_TABLE_CACHE
    path = BASE_DIR / "pokeheartgold/src/scrcmd_mart.c"
    if not path.exists():
        _HG_SPECIAL_MART_TABLE_CACHE = []
        return []
    text = path.read_text(encoding="utf-8")
    # Parse named arrays: const u16 NAME[] = { ITEM_X, ..., 0xFFFF };
    arrays: dict[str, list[str]] = {}
    for m in re.finditer(r"const u16 (_0[0-9A-Fa-f]+)\[\]\s*=\s*\{([^}]+)\}", text, re.S):
        items = [
            _item_name(tok.strip())
            for tok in m.group(2).split(",")
            if tok.strip().startswith("ITEM_")
        ]
        if items:
            arrays[m.group(1)] = items
    # Parse pointer table: const u16 *_0210FA3C[] = { name1, name2, ... };
    ptr_m = re.search(r"const u16 \*_0210FA3C\[\]\s*=\s*\{([^}]+)\}", text, re.S)
    result: list[list[str]] = []
    if ptr_m:
        for tok in ptr_m.group(1).split(","):
            tok = tok.strip().rstrip(";")
            result.append(arrays.get(tok, []))
    _HG_SPECIAL_MART_TABLE_CACHE = result
    return result


_HG_KURT_PAIRS_CACHE: list | None = None
_HG_KURT_ASM   = BASE_DIR / "pokeheartgold/asm/unk_02031B0C.s"
_HG_ITEMS_H    = BASE_DIR / "pokeheartgold/include/constants/items.h"


_HG_APRICORN_EXPAND: dict[str, str] = {
    "Ylw": "Yellow", "Blu": "Blue", "Grn": "Green",
    "Pnk": "Pink",  "Wht": "White", "Blk": "Black",
}


def _expand_apricorn_name(name: str) -> str:
    """'Ylw Apricorn' → 'Yellow Apricorn' etc."""
    parts = name.split()
    if parts:
        parts[0] = _HG_APRICORN_EXPAND.get(parts[0], parts[0])
    return " ".join(parts)


def _load_hg_kurt_pairs() -> list[tuple[str, str]]:
    """
    Parse the apricorn→ball mapping from the decomp.

    Ball order: 7 .short ITEM_* entries at the _020F68D0 rodata label in
    asm/unk_02031B0C.s.  Apricorn order: ITEM_*_APRICORN defines in
    include/constants/items.h, in definition order (RED/YLW/BLU/GRN/PNK/WHT/BLK).
    items.h uses abbreviated color names (YLW, BLU…); _expand_apricorn_name
    maps them to the player-facing full names.
    """
    global _HG_KURT_PAIRS_CACHE
    if _HG_KURT_PAIRS_CACHE is not None:
        return _HG_KURT_PAIRS_CACHE

    balls: list[str] = []
    if _HG_KURT_ASM.exists():
        lines = _HG_KURT_ASM.read_text(encoding="utf-8").splitlines()
        in_table = False
        for line in lines:
            if "_020F68D0:" in line:
                in_table = True
                continue
            if in_table:
                m = re.match(r"\s*\.short\s+(ITEM_\w+)", line)
                if m:
                    balls.append(_item_name(m.group(1)))
                else:
                    break  # end of table on first non-.short line

    apricorns: list[str] = []
    if _HG_ITEMS_H.exists():
        for m in re.finditer(r"^#define\s+(ITEM_\w+APRICORN)\b", _HG_ITEMS_H.read_text(encoding="utf-8"), re.M):
            name = _expand_apricorn_name(_item_name(m.group(1)))
            if name not in apricorns:
                apricorns.append(name)

    result = list(zip(apricorns, balls))
    if not result:
        result = [
            ("Red Apricorn",    "Level Ball"),
            ("Yellow Apricorn", "Moon Ball"),
            ("Blue Apricorn",   "Lure Ball"),
            ("Green Apricorn",  "Friend Ball"),
            ("Pink Apricorn",   "Love Ball"),
            ("White Apricorn",  "Fast Ball"),
            ("Black Apricorn",  "Heavy Ball"),
        ]
    _HG_KURT_PAIRS_CACHE = result
    return result

_HG_APRICORN_TREE_SYS = BASE_DIR / "pokeheartgold/src/apricorn_tree_sys.c"

_HG_APRICORN_INT_TO_COLOR: dict[int, str] = {
    1: "Red", 2: "Yellow", 3: "Blue", 4: "Green",
    5: "Pink", 6: "White", 7: "Black",
}

_HG_APRICORN_TABLE_CACHE: list[str] | None = None


def _load_apricorn_table() -> list[str]:
    """Return list indexed by tree index → color name, parsed from apricorn_tree_sys.c."""
    global _HG_APRICORN_TABLE_CACHE
    if _HG_APRICORN_TABLE_CACHE is not None:
        return _HG_APRICORN_TABLE_CACHE
    colors: list[str] = []
    if _HG_APRICORN_TREE_SYS.exists():
        src = _HG_APRICORN_TREE_SYS.read_text(encoding="utf-8")
        in_table = False
        for line in src.splitlines():
            if "sTreeApricorns" in line and "=" in line and "{" in line:
                in_table = True
                continue
            if in_table:
                if "}" in line:
                    break
                num_m = re.search(r"APRICORN_(\w+)", line)
                if num_m:
                    tag = num_m.group(1).title()
                    tag = _HG_APRICORN_EXPAND.get(tag, tag)
                    colors.append(tag)
    _HG_APRICORN_TABLE_CACHE = colors
    return colors


def _apricorn_color(tree_idx: int) -> str:
    table = _load_apricorn_table()
    if 0 <= tree_idx < len(table):
        return table[tree_idx]
    return "Unknown"


_HG_FOSSIL_PAIRS_CACHE: list | None = None


def _hg_fossil_pairs() -> list[tuple[str, str]]:
    """Parse scrcmd_fossils.c → [(fossil_item, pokemon_name)]"""
    global _HG_FOSSIL_PAIRS_CACHE
    if _HG_FOSSIL_PAIRS_CACHE is not None:
        return _HG_FOSSIL_PAIRS_CACHE
    path = BASE_DIR / "pokeheartgold/src/scrcmd_fossils.c"
    pairs: list[tuple[str, str]] = []
    if path.exists():
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(
            r"\{\s*(ITEM_\w+)\s*,\s*(SPECIES_\w+)\s*\}", text
        ):
            pairs.append((_item_name(m.group(1)), _species_name(m.group(2))))
    _HG_FOSSIL_PAIRS_CACHE = pairs
    return pairs


_HG_TRADE_TABLE_CACHE: dict | None = None
_HG_NPC_TRADE_H    = BASE_DIR / "pokeheartgold/include/constants/npc_trade.h"
_HG_TRADE_NARC_DIR = BASE_DIR / "pokeheartgold/files/a/1/1/2"
_HG_TRADE_RECORD_SIZE = 84
_HG_TRADE_GIVE_OFFSET = 0
_HG_TRADE_ASK_OFFSET  = 76


def _load_hg_trade_table() -> dict[int, dict]:
    """
    Parse the in-game trade table from NARC files/a/1/1/2 and npc_trade.h.

    Each NARC file is one 84-byte trade record.
    give_species: int32 at offset 0; ask_species: int32 at offset 76 (0x4C).
    Nicknames are derived from the enum constant names in npc_trade.h:
    NPC_TRADE_ROCKY_ONIX → split on last '_', prefix part title-cased = nickname.
    """
    global _HG_TRADE_TABLE_CACHE
    if _HG_TRADE_TABLE_CACHE is not None:
        return _HG_TRADE_TABLE_CACHE

    # Parse enum constants → ordered list of (index, SUFFIX) e.g. "ROCKY_ONIX"
    enum_names: list[str] = []
    if _HG_NPC_TRADE_H.exists():
        for m in re.finditer(r"\bNPC_TRADE_([A-Z][A-Z0-9_]+)", _HG_NPC_TRADE_H.read_text(encoding="utf-8")):
            name = m.group(1)
            if name in ("MAX", "OT_NUM"):
                continue
            enum_names.append(name)

    species_ids = _load_hg_species_ids()

    result: dict[int, dict] = {}
    if _HG_TRADE_NARC_DIR.exists():
        narc = Narc(_HG_TRADE_NARC_DIR)
        for i in range(narc.count):
            data = narc.file(i)
            if len(data) < _HG_TRADE_RECORD_SIZE:
                continue
            give_id = struct.unpack_from("<i", data, _HG_TRADE_GIVE_OFFSET)[0]
            ask_id  = struct.unpack_from("<i", data, _HG_TRADE_ASK_OFFSET)[0]
            # give_species = NPC gives to player = "receives" from player's perspective
            # ask_species  = NPC asks from player = "gives"  from player's perspective
            gives    = species_ids.get(str(ask_id),  f"Species#{ask_id}")
            receives = species_ids.get(str(give_id), f"Species#{give_id}")

            # Derive nickname from enum name suffix
            nickname = ""
            if i < len(enum_names):
                parts = enum_names[i].rsplit("_", 1)
                nickname = parts[0].replace("_", " ").title() if len(parts) == 2 else enum_names[i].title()

            result[i] = {"gives": gives, "receives": receives, "nickname": nickname}

    _HG_TRADE_TABLE_CACHE = result
    return result


def _hg_exchange_pairs(code: str, script_label: str, flags: set[str]) -> list[tuple[str, str]]:
    """Extract (given, obtained) exchange pairs for an HG NPC."""
    if not flags:
        return []
    all_blocks = _script_blocks(code)
    seen, body_all = _hg_bfs(code, script_label)

    if "move_reminder" in flags:
        return [("Heart Scale", "Any forgotten move")]

    pairs: list[tuple[str, str]] = []

    if "vendor" in flags:
        if re.search(r"CallStd std_special_mart", body_all):
            idx_m = re.search(r"SetVar VAR_SPECIAL_x8004,\s*(\d+)", body_all)
            if idx_m:
                idx = int(idx_m.group(1))
                mart = _hg_special_mart_table()
                if 0 <= idx < len(mart):
                    for item in mart[idx]:
                        pairs.append(("Money", item))
        elif re.search(r"CallStd std_pokemart", body_all):
            for item in _load_hg_common_mart():
                pairs.append(("Money", item))

    if "checkitem_exchange" in flags:
        if re.search(r"GetTotalApricornCount\b", body_all):
            pairs.extend(_load_hg_kurt_pairs())
        elif re.search(r"CountFossils\b|GetFossilPokemon\b", body_all):
            pairs.extend(_hg_fossil_pairs())

    # Shard-for-berry exchanges: scan all zone blocks — exchange blocks are reached
    # via Switch/Case fall-through from an NPCMsg block, not by explicit GoTo.
    if "shard_berry" in flags:
        seen_sb: set[tuple] = set()
        for blk in all_blocks.values():
            take_m = re.search(r"TakeItem\s+(ITEM_(?:RED|BLUE|YELLOW|GREEN)_SHARD)", blk)
            if not take_m:
                continue
            shard = _item_name(take_m.group(1))
            berries = re.findall(r"GiveItemNoCheck\s+(ITEM_\w+)", blk)
            if berries:
                obtained = " + ".join(_item_name(b) for b in berries)
                p = (shard, obtained)
                if p not in seen_sb:
                    seen_sb.add(p)
                    pairs.append(p)

    # Quest item exchange: per-block TakeItem + GiveItemNoCheck/GiveItem
    if "item_give_exchange" in flags:
        seen_iq: set[tuple] = set()
        for lbl in seen:
            blk = all_blocks.get(lbl, "")
            take_m = re.search(
                r"TakeItem\s+(ITEM_(?!(?:RED|BLUE|YELLOW|GREEN)_SHARD|HEART_SCALE)\w+)", blk
            )
            if not take_m:
                continue
            given = _item_name(take_m.group(1))
            gives = re.findall(r"GiveItemNoCheck\s+(ITEM_\w+)|(?<!\w)GiveItem\s+(ITEM_\w+)", blk)
            for g1, g2 in gives:
                obtained = _item_name(g1 or g2)
                p = (given, obtained)
                if p not in seen_iq:
                    seen_iq.add(p)
                    pairs.append(p)

    # Pokéathlon AP vendor: AP cost + item ID from VAR_SPECIAL_x8001
    if "ap_vendor" in flags:
        seen_ap: set[tuple] = set()
        ap_items = re.findall(r"SetVar\s+VAR_SPECIAL_x8001,\s*(\d+)", body_all)
        ap_costs = re.findall(r"TakeAthletePoints\s+(\d+)", body_all)
        for cost, item_id_str in zip(ap_costs, ap_items):
            item = _hg_item_id_to_name(int(item_id_str))
            p = (f"{cost} AP", item)
            if p not in seen_ap:
                seen_ap.add(p)
                pairs.append(p)

    # Battle Frontier BP exchange: items stored as numeric IDs in VAR_SPECIAL_x8004
    if "bp_vendor" in flags:
        seen_bp: set[str] = set()
        for item_id_str in re.findall(r"SetVar\s+VAR_SPECIAL_x8004,\s*(\d+)", body_all):
            item = _hg_item_id_to_name(int(item_id_str))
            if item not in seen_bp:
                seen_bp.add(item)
                pairs.append(("BP", item))

    return pairs


def _hg_trade_info(code: str, script_label: str) -> dict | None:
    """Find LoadNPCTrade N in NPC script and return {gives, receives, nickname}."""
    all_blocks = _script_blocks(code)
    seen: set[str] = set()
    queue = [script_label]
    body_all = ""
    while queue:
        lbl = queue.pop(0)
        if lbl in seen:
            continue
        seen.add(lbl)
        block = all_blocks.get(lbl, "")
        body_all += block
        for m in re.finditer(r"\bGoTo\w*\b.*?\b(\w+)\s*$", block, re.M):
            tgt = m.group(1)
            if tgt not in seen and tgt in all_blocks:
                queue.append(tgt)
    m = re.search(r"LoadNPCTrade\s+(\d+)", body_all)
    if m:
        return _load_hg_trade_table().get(int(m.group(1)))
    return None


_HG_TERMINAL_BLOCK_RE = re.compile(
    r"\b(?:End|Return|ReleaseAll|BlackOut|WhiteOut)\b"
    r"|^\s*GoTo\s+\w+\s*$",
    re.M,
)


def _hg_bfs(code: str, start_label: str) -> tuple[set[str], str]:
    """BFS over an HG zone script, following GoTo/Call/Case and fall-through."""
    all_blocks = _script_blocks(code)
    label_order = list(all_blocks.keys())
    label_pos = {lbl: i for i, lbl in enumerate(label_order)}
    seen: set[str] = set()
    body_all = ""
    queue = [start_label]
    while queue:
        lbl = queue.pop(0)
        if lbl in seen:
            continue
        seen.add(lbl)
        block = all_blocks.get(lbl, "")
        body_all += block
        for m in re.finditer(r"\b(?:GoTo\w*|Call\w*|Case)\b.*?\b(\w+)\s*$", block, re.M):
            tgt = m.group(1)
            if tgt not in seen and tgt in all_blocks:
                queue.append(tgt)
        # Fall-through: if block has no terminal instruction, execution continues
        # to the next label in source order
        if not _HG_TERMINAL_BLOCK_RE.search(block):
            pos = label_pos.get(lbl, -1)
            if 0 <= pos < len(label_order) - 1:
                nxt = label_order[pos + 1]
                if nxt not in seen:
                    queue.append(nxt)
    return seen, body_all


def _hg_detect_service_flags(code: str, script_label: str) -> set[str]:
    """Scan an HG NPC zone script (BFS over gotos/calls/cases) for service-indicator commands."""
    flags: set[str] = set()
    _, body_all = _hg_bfs(code, script_label)
    if re.search(r"CallStd std_pokemart|CallStd std_special_mart", body_all):
        flags.add("vendor")
    if re.search(r"LoadNPCTrade\b", body_all):
        flags.add("trader")
    if re.search(r"HasItem ITEM_HEART_SCALE", body_all):
        flags.add("move_reminder")
    if re.search(r"MonForgetMove\b", body_all) and "HEART_SCALE" not in body_all:
        flags.add("move_deleter")
    if re.search(r"GetTotalApricornCount\b", body_all):
        flags.add("checkitem_exchange")
    if re.search(r"CountFossils\b|GetFossilPokemon\b", body_all):
        flags.add("checkitem_exchange")
    if re.search(r"CallStd std_frontier_move_tutor", body_all):
        flags.add("move_tutor")
    if re.search(r"\bMoveTutorInit\b", body_all):
        flags.add("move_tutor")
    if re.search(r"\bNicknameInput\b", body_all):
        flags.add("name_rater")
    # Shard-for-berry exchanges (Fuchsia / Violet City traders)
    if re.search(r"HasItem ITEM_(?:RED|BLUE|YELLOW|GREEN)_SHARD", body_all):
        flags.add("shard_berry")
    # Item-for-item quest exchanges (HasItem + GiveItemNoCheck, not shard/heart-scale/vendor)
    if (re.search(
            r"HasItem ITEM_(?!(?:RED|BLUE|YELLOW|GREEN)_SHARD|HEART_SCALE)\w+", body_all
        )
            and re.search(r"GiveItemNoCheck\b|(?<!\w)GiveItem\b", body_all)
            and "vendor" not in flags
            and "shard_berry" not in flags):
        flags.add("item_give_exchange")
    # Pokéathlon AP exchange (Pokéathlon Dome prize shop)
    if re.search(r"\bCheckAthletePoints\b", body_all):
        flags.add("ap_vendor")
    # Battle Frontier BP exchange
    if re.search(r"\bCheckBattlePoints\b", body_all):
        flags.add("bp_vendor")
    if re.search(r"CallStd std_nurse_joy\b", body_all):
        flags.add("healer")
    return flags


# ---------------------------------------------------------------------------
# Zone events
# ---------------------------------------------------------------------------

#: Obstacle scripts → (grid char, label). Chars follow Platinum's
#: _FIELD_OBSTACLE_GFX so the two games' maps read the same; ♣ doubles as the
#: ordinary tree, which is correct — a cut tree is a tree until you cut it.
_FIELD_OBSTACLE_SCRIPTS: dict[str, tuple[str, str]] = {
    "std_field_cut":         ("♣", "Cut tree"),
    "std_field_rock_smash":  ("⊗", "Rock Smash rock"),
    "std_field_strength":    ("▣", "Strength boulder"),
}

_STD_TRAINER_RE = re.compile(r"^std_trainer(?:_2)?\((TRAINER_\w+)\)")
_SCRIPT_LABEL_RE = re.compile(r"^_EV_(scr_seq_\w+?)\s*\+\s*1$")


def _item_label(script: str, prefix: str) -> str:
    """
    Item name out of an itemball / hidden-item script constant.

    The constants embed the map they sit on: std_itemball_d24r0212_oran_berry,
    std_hiddenitem_r29_r30_t21_nugget. Strip leading map-code tokens (a letter
    or two followed by digits, possibly repeated as in d24r0212) but never the
    last token, so std_itemball_r32_tm09 keeps its TM.
    """
    tokens = script[len(prefix):].split("_")
    while len(tokens) > 1 and re.fullmatch(r"[a-z]{1,2}\d+(?:[a-z]\d+)*", tokens[0]):
        tokens.pop(0)
    # A bare trailing number only disambiguates two of the same item on one map
    # (..._pearl, ..._pearl_2); it is not part of the name.
    if len(tokens) > 1 and tokens[-1].isdigit():
        tokens.pop()
    name = "_".join(tokens)
    if re.fullmatch(r"(?:tm|hm)\d+", name):
        upper = name.upper()
        num   = int(re.search(r"\d+", name).group())
        const = f"ITEM_HM{num:02d}" if name.upper().startswith("HM") else f"ITEM_TM{num:02d}"
        move  = _hg_tm_move_name(const)
        return f"{upper} ({move})" if move else upper
    return re.sub(r"\bPp\b", "PP", name.replace("_", " ").title())


# ---------------------------------------------------------------------------
# Gym leader detection + dialogue extraction (HeartGold)
# ---------------------------------------------------------------------------

_HG_GYM_BATTLE_RE = re.compile(
    r"TrainerBattle\s+(TRAINER_(?:LEADER|ELITE_FOUR|CHAMPION)_\w+)")
_HG_NPCMsg_RE     = re.compile(r"(?:NPCMsg|GenderMsgBox)\s+(msg_\w+)")
_HG_GOTO_RE       = re.compile(r"^\s+GoTo\s+(\w+)")      # unconditional only
_HG_GOTO_ANY_RE   = re.compile(r"^\s+GoTo\w*\s+\S.*?(\w+)\s*$")  # any GoTo variant
_HG_TERMINAL_RE   = re.compile(r"^\s+(?:End|ReleaseAll|WhiteOut|BlackOut|Return)\b")
# Any TrainerBattle constant (used for broad coord/init sweeps)
_HG_ANY_TRAINER_BATTLE_RE = re.compile(r"TrainerBattle\s+(TRAINER_\w+)")
# MultiBattle: ally, enemy1, enemy2, flag
_HG_MULTI_BATTLE_RE = re.compile(
    r"MultiBattle\s+(TRAINER_\w+),\s*(TRAINER_\w+),\s*(TRAINER_\w+)")
# Interaction-category trainer constants (gym leaders, E4, champion, rivals, Red)
_HG_INTERACTION_TRAINER_RE = re.compile(
    r"TRAINER_(?:LEADER|ELITE_FOUR|CHAMPION|RIVAL_SILVER|PKMN_TRAINER)_")
# NPC item-gift patterns:
#   GiveItemNoCheck ITEM_X, N        — unconditional key-item give
#   GoToIfNoItemSpace ITEM_X, N, lbl — guarded give (followed by CallStd std_give_item_verbose)
_HG_GIVE_NOCHECK_RE = re.compile(r"GiveItemNoCheck\s+(ITEM_\w+),\s*(\d+)")
_HG_GIVE_SPACE_RE   = re.compile(r"GoToIfNoItemSpace\s+(ITEM_\w+),\s*(\d+)")
# Flag-gate opcodes that appear before the first NPCMsg in an NPC's root block
_HG_FLAG_GATE_RE    = re.compile(r"^\s+(GoToIfSet|GoToIfUnset)\s+(\w+),\s*(\w+)")


def _hg_collect_npc_script(code: str, script_label: str) -> dict:
    """
    Analyse an NPC's root script block and return structured dialogue + item-give data.

    Returns:
      {"states": [
          {"flag": None,      "polarity": "post", "pre_msgs": [...], "gives": [...]},
          {"flag": "FLAG_X",  "polarity": "post", "pre_msgs": [...], "gives": []},
          ...
      ]}

    States are ordered earliest → latest (default first, newest last).
    For GoToIfSet (polarity "post"): the state is active when the flag IS set.
    For GoToIfUnset (polarity "pre"): the state is active when the flag is NOT set.
    gives entries: {"item": "ITEM_X", "qty": N, "post_msgs": [...]}
    """
    blocks = _script_blocks(code)
    root_lines = blocks.get(script_label, "").splitlines()

    # --- Phase 1: collect flag-gate opcodes before first message/give ---
    gates: list[tuple[str, str, str]] = []  # (flag_name, target_label, polarity)
    fallthrough_start = len(root_lines)

    for i, line in enumerate(root_lines):
        m = _HG_FLAG_GATE_RE.match(line)
        if m:
            polarity = "post" if m.group(1) == "GoToIfSet" else "pre"
            gates.append((m.group(2), m.group(3), polarity))
            continue
        if _MSG_CMD.match(line):
            fallthrough_start = i
            break
        if _HG_GIVE_NOCHECK_RE.search(line) or _HG_GIVE_SPACE_RE.search(line):
            fallthrough_start = i
            break
        # LockAll / FacePlayer / PlaySE / other preamble — skip silently

    fallthrough = root_lines[fallthrough_start:]

    # --- Phase 2: find give opcode in fallthrough ---
    give_idx: int | None = None
    item_const: str | None = None
    item_qty = 1

    for i, line in enumerate(fallthrough):
        m = _HG_GIVE_NOCHECK_RE.search(line)
        if m and not m.group(1).startswith("VAR"):
            item_const, item_qty, give_idx = m.group(1), int(m.group(2)), i
            break
        m = _HG_GIVE_SPACE_RE.search(line)
        if m:
            item_const, item_qty, give_idx = m.group(1), int(m.group(2)), i
            break

    # --- Phase 3: collect messages from fallthrough ---
    def _msgs(lines: list[str]) -> list[str]:
        out = []
        for ln in lines:
            mm = _MSG_CMD.match(ln)
            if mm:
                txt = _clean_msg(_msg_text(mm.group(1)))
                if txt:
                    out.append(txt)
        return out

    pre_lines  = fallthrough[:give_idx] if give_idx is not None else fallthrough
    post_lines = fallthrough[give_idx + 1:] if give_idx is not None else []

    pre_msgs  = _msgs(pre_lines)
    post_msgs = _msgs(post_lines)

    gives = []
    if item_const:
        gives = [{"item": item_const, "qty": item_qty, "post_msgs": post_msgs}]

    default_state = {"flag": None, "polarity": "post", "pre_msgs": pre_msgs, "gives": gives}

    # --- Phase 4: collect one message from each gate-target block ---
    # Script checks most-recent flag first; reverse so output is earliest → latest.
    gate_states: list[dict] = []
    for flag_name, target_label, polarity in reversed(gates):
        target_msgs = _msgs(blocks.get(target_label, "").splitlines())[:1]
        gate_states.append({"flag": flag_name, "polarity": polarity,
                            "pre_msgs": target_msgs, "gives": []})

    return {"states": [default_state] + gate_states}


def _hg_gym_leader_info(code: str, label: str) -> dict | None:
    """
    Extract gym leader data from a HeartGold zone script.
    label: the entry script label (e.g. scr_seq_T23GYM0102_001).
    Returns None if no TrainerBattle TRAINER_LEADER_* is found.
    """
    blocks = _script_blocks(code)
    # Need ordered iteration for fall-through detection
    block_list = list(blocks.items())
    block_idx  = {lbl: i for i, (lbl, _) in enumerate(block_list)}

    # Find the block containing TrainerBattle TRAINER_LEADER_*
    # BFS from `label` so we only consider blocks actually reachable from it,
    # not every block that happens to appear later in the file.
    battle_label  = None
    trainer_const = None
    _bfs_seen: set[str] = set()
    _bfs_queue: list[str] = [label]
    while _bfs_queue and battle_label is None:
        blk_lbl = _bfs_queue.pop(0)
        if blk_lbl in _bfs_seen:
            continue
        _bfs_seen.add(blk_lbl)
        blk_body = blocks.get(blk_lbl, "")
        m = _HG_GYM_BATTLE_RE.search(blk_body)
        if m:
            battle_label  = blk_lbl
            trainer_const = m.group(1)
            break
        for gm in re.finditer(r"\bGoTo\w*\b.*?\b(\w+)\s*$", blk_body, re.M):
            tgt = gm.group(1)
            if tgt not in _bfs_seen and tgt in blocks:
                _bfs_queue.append(tgt)

    if not trainer_const:
        return None

    pre_battle:  list[str] = []
    post_battle: list[str] = []
    phase = "pre"      # pre → battle → post_pending → post
    visited: set[str] = set()
    gives_item: list = [None]   # [{"item_id": ..., "quantity": 1}] once found
    gym_badge:  list = [None]   # [badge_constant] once found

    def _collect(blk_lbl: str) -> None:
        nonlocal phase
        if blk_lbl in visited:
            return
        visited.add(blk_lbl)
        body = blocks.get(blk_lbl, "")
        lines = body.splitlines()
        last_terminal = False

        for line in lines:
            mm = _HG_NPCMsg_RE.search(line)
            if mm:
                text = _clean_msg(_msg_text(mm.group(1)))
                if text:
                    if phase == "pre":
                        pre_battle.append(text)
                    elif phase == "post":
                        post_battle.append(text)
                last_terminal = False
                continue

            if _HG_GYM_BATTLE_RE.search(line):
                phase = "battle"
                last_terminal = False
                continue

            if "CheckBattleWon" in line:
                phase = "post_pending"
                last_terminal = False
                continue

            # First conditional GoTo after CheckBattleWon = lose-branch skip
            if phase == "post_pending" and re.search(r"GoToIf", line):
                phase = "post"
                last_terminal = False
                continue

            if phase == "post":
                if gives_item[0] is None:
                    gi_m = re.search(r"GoToIfNoItemSpace\s+(ITEM_\w+)", line)
                    if gi_m:
                        gives_item[0] = {"item_id": gi_m.group(1), "quantity": 1}
                if gym_badge[0] is None:
                    gb_m = re.search(r"\bGiveBadge\s+(BADGE_\w+)", line)
                    if gb_m:
                        gym_badge[0] = gb_m.group(1)
                gm = _HG_GOTO_ANY_RE.match(line)
                if gm:
                    _collect(gm.group(1))
                last_terminal = bool(_HG_TERMINAL_RE.match(line))
                continue

            last_terminal = bool(_HG_TERMINAL_RE.match(line))

        # Label fall-through: if the block didn't terminate, continue to next block
        if not last_terminal and phase in ("post", "post_pending"):
            idx = block_idx.get(blk_lbl)
            if idx is not None and idx + 1 < len(block_list):
                next_lbl = block_list[idx + 1][0]
                # Don't fall through into a fresh named script entry
                if not next_lbl.startswith("scr_seq_"):
                    _collect(next_lbl)

    _collect(battle_label)

    td  = _trainer_data(trainer_const) or {}
    rematches = [
        {"const": rc, "party": _trainer_party(rtd), "prize": _trainer_prize(rtd)}
        for rc in _rematch_chain(trainer_const)
        if (rtd := _trainer_data(rc))
    ]

    msgs: dict[str, str] = {
        m.get("type", ""): m.get("message", "")
        for m in (td.get("messages") or [])
    }

    return {
        "trainer_const": trainer_const,
        "name":          (td.get("name") or "?").replace("{TRNAME}", "").strip(" -"),
        "class":         td.get("class", ""),
        "ai_flags":      _decode_hg_ai_flags(td.get("ai_flags") or 0),
        "items":         [i.replace("ITEM_", "").replace("_", " ").title()
                          for i in (td.get("items") or []) if i],
        "pre_battle":    pre_battle,
        "post_battle":   post_battle,
        "messages":      msgs,
        "party":         _trainer_party(td),
        "prize":         _trainer_prize(td),
        "rematches":     rematches,
        # Clair (BADGE_RISING) gives her badge in Dragon's Den after a post-battle
        # challenge, not in the gym script — hardcode it as her badge is unambiguous.
        "gym_badge":     gym_badge[0] or ("BADGE_RISING" if trainer_const == "TRAINER_LEADER_CLAIR_CLAIR" else None),
        "gives_item":    gives_item[0],
    }


_HG_RIVAL_BATTLE_RE = re.compile(r"TrainerBattle\s+(TRAINER_RIVAL_SILVER\w*)")


def _hg_rival_info(code: str, label: str) -> dict | None:
    """
    Extract Silver rival battle data from a HeartGold zone script.
    Returns None if no TrainerBattle TRAINER_RIVAL_SILVER* is found.
    """
    blocks = _script_blocks(code)
    trainer_const = None
    for _, blk_body in blocks.items():
        m = _HG_RIVAL_BATTLE_RE.search(blk_body)
        if m:
            trainer_const = m.group(1)
            break
    if not trainer_const:
        return None
    td = _trainer_data(trainer_const) or {}
    if not td:
        return None
    return {
        "trainer_const": trainer_const,
        "name":          "Silver",
        "class":         td.get("class", ""),
        "ai_flags":      _decode_hg_ai_flags(td.get("ai_flags") or 0),
        "items":         [i.replace("ITEM_", "").replace("_", " ").title()
                          for i in (td.get("items") or []) if i],
        "party":         _trainer_party(td),
        "prize":         _trainer_prize(td),
    }


def render_rival_section(rivals: list, col_off: int = 0, row_off: int = 0) -> list[str]:
    if not rivals:
        return []
    SEP = "=" * 70
    lines = ["", SEP, "  Rival Encounter  (HeartGold)", SEP, ""]
    for r in rivals:
        col = r["x"] - col_off
        row = r["z"] - row_off
        cls_disp = r["class"].replace("TRAINER_CLASS_", "").replace("_", " ").title() if r.get("class") else ""
        prize_str = f"  Prize: ¥{r['prize']}" if r.get("prize") else ""
        lines.append(f"  (col={col:>3}, row={row:>3})  {cls_disp} {r['name']}{prize_str}")
        if r.get("items"):
            lines.append(f"          Items: {', '.join(r['items'])}")
        for mon in r.get("party", []):
            lv_str = f"Lv {mon['level']}"
            lines.append(f"            {mon['species'].title():<20} {lv_str}")
            moves = mon.get("moves") or []
            if moves:
                lines.append(f"            {'':20} Moves: {', '.join(moves)}")
        lines.append("")
    return lines


def load_events(internal_code: str, include_background: bool = False) -> dict:
    """
    Everything interactive in a zone, in world tile coords.

    Returns {trainers, npcs, items, hidden_items, field_objects,
    static_encounters}. Signs stay in zone_signs() where the renderer reads them.
    """
    result: dict = {"trainers": [], "gym_leaders": [], "rivals": [], "npcs": [], "items": [],
                    "hidden_items": [], "field_objects": [], "apricorns": [],
                    "static_encounters": []}
    path = _zone_event_file(internal_code)
    if path is None:
        return result
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    code = path.stem.split("_", 1)[1]

    # Static encounters (runs once per zone, scans all objects + coords)
    result["static_encounters"] = _hg_static_encounters(code, data)
    # Build set of (x, z) coords claimed by static encounters
    _static_enc_coords: set[tuple] = {
        (e["x"], e["z"])
        for e in result["static_encounters"]
        if e.get("x") is not None and e.get("z") is not None
    }

    for obj in data.get("objects", []):
        x, z = obj.get("x"), obj.get("z")
        if not (isinstance(x, int) and isinstance(z, int)):
            continue
        script = str(obj.get("scriptId", ""))
        sprite = str(obj.get("spriteId", ""))

        # Trainers name themselves in the script id — match on that, NOT on the
        # object's `type` field. Game-wide, 35 std_trainer objects carry type 0
        # and 3 type-1 objects are not trainers at all.
        hit = _STD_TRAINER_RE.match(script)
        if hit:
            const = hit.group(1)
            td = _trainer_data(const)
            if td:
                entry = _build_trainer(x, z, const, td)
                entry["sprite"] = sprite
                # std_trainer_2 is the second script bank: the same trainer
                # approached from a different scene state.
                entry["variant"] = script.startswith("std_trainer_2")
                result["trainers"].append(entry)
                continue

        if script.startswith("std_itemball_"):
            result["items"].append({
                "x": x, "z": z, "script": script,
                "item": _item_label(script, "std_itemball_"),
            })
            continue

        if script == "std_apricorn_tree":
            tree_idx = obj.get("param0", -1)
            color    = _apricorn_color(tree_idx)
            result["apricorns"].append({
                "x": x, "z": z, "color": color,
                "apricorn_name": f"{color} Apricorn",
                "tree_index": tree_idx,
            })
            continue

        if script in _FIELD_OBSTACLE_SCRIPTS:
            char, label = _FIELD_OBSTACLE_SCRIPTS[script]
            result["field_objects"].append({
                "x": x, "z": z, "char": char, "label": label, "script": script,
            })
            continue

        # Pushable ice blocks (Seafoam Islands puzzle). These have scriptId 0
        # so they must be caught before the cutscene-actor skip below.
        if sprite == "SPRITE_ICE":
            result["field_objects"].append({
                "x": x, "z": z, "char": "◻", "label": "Ice block",
            })
            continue

        # Barrier props: SPRITE_STOP objects have scripts that only contain End;
        # they are removed by HidePerson after the relevant badge is earned.
        if sprite == "SPRITE_STOP":
            continue

        # Follower Pokémon and obstacle-Pokémon sprites are shown in the
        # static encounter index; skip them here.
        if sprite.startswith("SPRITE_FOLLOWER_MON") or sprite in _HG_OBSTACLE_SPRITES:
            continue
        # scriptId == 0 (integer) means no interaction script at all — these
        # are cutscene actors (Lance's overworld model, Oak/Kris walking in,
        # door props). They have no dialogue and cannot be interacted with.
        if obj.get("scriptId") == 0:
            continue
        label = _SCRIPT_LABEL_RE.match(script)
        script_label = label.group(1) if label else None

        # Photographer (Cameron): SPRITE_GSMIDDLEMAN1 uses GetStdMsgNaix which
        # is a runtime NARC call — dialogue is not statically extractable.
        # Include only when --include-background-npcs is active (never by default).
        if sprite == "SPRITE_GSMIDDLEMAN1" and not include_background:
            continue

        # Custom item pickups that don't use the std_itemball_ prefix (e.g. the
        # three Lake of Rage items which call std_hidden_item_fanfare directly).
        if script_label and _hg_is_custom_item(code, script_label):
            result["items"].append({
                "x": x, "z": z, "script": script_label,
                "item": _hg_custom_item_name(code, script_label),
            })
            continue

        # Rival (Silver): SPRITE_GSRIVEL with a battleable script
        if sprite == "SPRITE_GSRIVEL" and script_label:
            rv = _hg_rival_info(code, script_label)
            if rv:
                result["rivals"].append({**rv, "x": x, "z": z, "category": "Interaction"})
                continue

        # Gym leader / Elite Four / Champion: known sprite with a parseable script label
        _is_gym_or_e4 = (
            sprite.startswith("SPRITE_GSLEADER")
            or sprite.startswith("SPRITE_GSBIGFOUR")
            or sprite in ("SPRITE_CHAMPION", "SPRITE_WATARU")
        )
        if _is_gym_or_e4 and script_label:
            gl = _hg_gym_leader_info(code, script_label)
            if gl:
                result["gym_leaders"].append({**gl, "x": x, "z": z, "category": "Interaction"})
                continue

        # Interaction battles: TRAINER_TYPE_NONE NPC whose script leads to TrainerBattle
        # (Red, Eusine, Kimono Girls, etc.)
        if script_label:
            body = _hg_bfs_body(code, script_label)
            for tbm in _HG_ANY_TRAINER_BATTLE_RE.finditer(body):
                const = tbm.group(1)
                td = _trainer_data(const)
                if td:
                    entry = _build_trainer(x, z, const, td, category="Interaction")
                    entry["sprite"] = sprite
                    result["trainers"].append(entry)
                    # Fall through to NPC so dialogue is also captured
            # Only skip NPC if we found trainers AND no dialogue is expected
            # (interaction trainers like Red also have NPC dialogue — keep both)

        _sflags = (_hg_detect_service_flags(code, script_label) if script_label else set())
        _move_int = obj.get("movement", 0)
        _movement = _HG_MOVE_CODES.get(_move_int, f"MOVEMENT_TYPE_{_move_int}")
        _sd = _hg_collect_npc_script(code, script_label) if script_label else None
        if "healer" in _sflags and not any(
            s.get("pre_msgs") for s in (_sd or {}).get("states", [])
        ):
            _sd = {"states": [{
                "flag": None, "polarity": "post",
                "pre_msgs": [
                    "Hello, and welcome to the Pokémon Center. "
                    "We restore your tired Pokémon to full health. "
                    "Would you like to rest your Pokémon?"
                ],
                "gives": [], "yesno": True,
                "declined_msg": "We hope to see you again!",
            }]}
        result["npcs"].append({
            "x": x, "z": z,
            "id":          str(obj.get("id", "")),
            "sprite":      sprite,
            "flag":        str(obj.get("eventFlag", "")),
            "movement":    _movement,
            "script_data": _sd,
            "service_flags": _sflags,
            "exchange_pairs": (
                _hg_exchange_pairs(code, script_label, _sflags)
                if script_label else []
            ),
            "trade_info": (
                _hg_trade_info(code, script_label)
                if script_label and "trader" in _sflags else None
            ),
        })

    # bgs type 2 is a hidden item; type 1 is a readable sign (see zone_signs).
    for bg in data.get("bgs", []):
        if bg.get("type") != 2:
            continue
        x, z = bg.get("x"), bg.get("z")
        if not (isinstance(x, int) and isinstance(z, int)):
            continue
        script = str(bg.get("scriptId", ""))
        result["hidden_items"].append({
            "x": x, "z": z, "script": script,
            "item": (_item_label(script, "std_hiddenitem_")
                     if script.startswith("std_hiddenitem_") else script),
        })

    # Coord-event sweep: catches E4/Champion/Silver battle tiles and story trainer battles.
    _gl_seen_coords: set[str] = {gl["trainer_const"] for gl in result["gym_leaders"]}
    _rv_seen_coords: set[str] = {rv["trainer_const"] for rv in result["rivals"]}
    _tr_seen_coords: set[str] = {t["const"] for t in result["trainers"]}
    for ce in data.get("coords", []):
        sid = ce.get("scriptId", 0)
        if not sid or sid == 0:
            continue
        lm = _SCRIPT_LABEL_RE.match(str(sid))
        if not lm:
            continue
        ce_label = lm.group(1)
        ce_body = _hg_bfs_body(code, ce_label)
        cx, cz = ce.get("x", 0), ce.get("z", 0)

        # Fall hole: plays the RAKKA falling sound and warps the player down a floor.
        # Expand the event's w×h footprint into individual tile entries.
        if "RAKKA" in ce_body:
            ce_w = max(1, ce.get("w", 1))
            ce_h = max(1, ce.get("h", 1))
            for dz in range(ce_h):
                for dx in range(ce_w):
                    result["field_objects"].append({
                        "x": cx + dx, "z": cz + dz,
                        "char": "○", "label": "Fall hole",
                    })
            continue

        # E4/Champion
        m = _HG_GYM_BATTLE_RE.search(ce_body)
        if m:
            const = m.group(1)
            if const not in _gl_seen_coords:
                _gl_seen_coords.add(const)
                gl = _hg_gym_leader_info(code, ce_label)
                if gl:
                    result["gym_leaders"].append({**gl, "x": cx, "z": cz, "category": "Interaction"})
            continue

        # Silver rival
        m = _HG_RIVAL_BATTLE_RE.search(ce_body)
        if m:
            const = m.group(1)
            if const not in _rv_seen_coords:
                _rv_seen_coords.add(const)
                rv = _hg_rival_info(code, ce_label)
                if rv:
                    result["rivals"].append({**rv, "x": cx, "z": cz, "category": "Interaction"})
            continue

        # MultiBattle (Lance tag battle, Dragon's Den Silver variant)
        for mbm in _HG_MULTI_BATTLE_RE.finditer(ce_body):
            ally_const   = mbm.group(1)
            enemy1_const = mbm.group(2)
            enemy2_const = mbm.group(3)
            for ec in (enemy1_const, enemy2_const):
                if ec in _tr_seen_coords:
                    continue
                _tr_seen_coords.add(ec)
                etd = _trainer_data(ec)
                if etd:
                    entry = _build_trainer(cx, cz, ec, etd, category="One-time")
                    entry["sprite"] = ""
                    # Load ally party
                    ally_td = _trainer_data(ally_const)
                    if ally_td:
                        entry["ally"] = [_build_trainer(cx, cz, ally_const, ally_td, category="One-time")]
                    result["trainers"].append(entry)
            # Also check for variant ally constants from the whole body (Dragon's Den 3 variants)
            ally_variants = list(dict.fromkeys(
                m.group(1) for m in _HG_MULTI_BATTLE_RE.finditer(ce_body)
            ))
            if len(ally_variants) > 1:
                for ec in (enemy1_const, enemy2_const):
                    for t in result["trainers"]:
                        if t.get("const") == ec and "ally" in t:
                            t["ally"] = [
                                _build_trainer(cx, cz, ac, _trainer_data(ac), category="One-time")
                                for ac in ally_variants
                                if _trainer_data(ac)
                            ]
            if _HG_MULTI_BATTLE_RE.search(ce_body):
                continue

        # Any remaining TrainerBattle constants → story/one-time battles
        for tbm in _HG_ANY_TRAINER_BATTLE_RE.finditer(ce_body):
            const = tbm.group(1)
            if const in _gl_seen_coords or const in _rv_seen_coords or const in _tr_seen_coords:
                continue
            cat = "Interaction" if _HG_INTERACTION_TRAINER_RE.match(const) else "One-time"
            td = _trainer_data(const)
            if td:
                _tr_seen_coords.add(const)
                entry = _build_trainer(cx, cz, const, td, category=cat)
                entry["sprite"] = ""
                result["trainers"].append(entry)

    # Map-init script sweep: catches story battles triggered by OnFrameTable
    # (Giovanni in D45R0102, and similar flag-gated auto-start battles).
    _HG_INIT_GOTO_RE = re.compile(
        r"InitScriptGoToIfEqual\s+\w+,\s*\w+,\s*_EV_(scr_seq_\w+?)\s*\+\s*1")
    hdr_code = _load_hdr_script(code)
    if hdr_code:
        for hm in _HG_INIT_GOTO_RE.finditer(hdr_code):
            hdr_label = hm.group(1)
            hdr_body = _hg_bfs_body(code, hdr_label)
            for tbm in _HG_ANY_TRAINER_BATTLE_RE.finditer(hdr_body):
                const = tbm.group(1)
                if const in _tr_seen_coords:
                    continue
                _tr_seen_coords.add(const)
                td = _trainer_data(const)
                if td:
                    entry = _build_trainer(0, 0, const, td, category="One-time")
                    entry["sprite"] = ""
                    result["trainers"].append(entry)

    return result


def zone_sign_dialogue(internal_code: str, sign: dict) -> str | None:
    """The text on a signpost, resolved through its script."""
    path = _zone_event_file(internal_code)
    if path is None:
        return None
    label = _SCRIPT_LABEL_RE.match(str(sign.get("scriptId", "")))
    if not label:
        return None
    return _first_message(path.stem.split("_", 1)[1], label.group(1))


# ---------------------------------------------------------------------------
# Static Encounter Index (HeartGold)
# ---------------------------------------------------------------------------

_HG_WILD_BATTLE_RE = re.compile(
    r"WildBattle\s+(SPECIES_\w+|\d+|VAR_\w+)\s*,\s*(\d+|VAR_\w+)\s*,\s*(\d+)"
)

# Overworld-blocker Pokémon that use dedicated non-FOLLOWER sprites.
# These are missed by the FOLLOWER_MON_STATIC check and must be handled
# alongside it in _hg_static_encounters.
_HG_OBSTACLE_SPRITES: frozenset[str] = frozenset({
    "SPRITE_USOKKY",      # Sudowoodo (Route 36)
    "SPRITE_KABIGON",     # Snorlax (Route 11, Route 12)
    "SPRITE_YADON",       # Slowpoke (Slowpoke Well B1F, ×2)
    "SPRITE_RGYARADOSU",  # Red Gyarados (Lake of Rage)
    "SPRITE_RAPURASU",    # Lapras (Union Cave B2F — Fridays)
})
_HG_SETWILD_SPECIES_RE = re.compile(
    r"SetVar\s+VAR_TEMP_x400A\s*,\s*(\d+|SPECIES_\w+)"
)
_HG_SETLEVEL_RE = re.compile(
    r"SetVar\s+VAR_SPECIAL_x8004\s*,\s*(\d+)"
)
_HG_PLAYCRY_LEGEND_RE = re.compile(
    r"PlayCry\s+(SPECIES_(?:RAIKOU|ENTEI|SUICUNE|ARTICUNO|ZAPDOS|MOLTRES|"
    r"LATIAS|LATIOS|MEWTWO|LUGIA|HO_OH|CELEBI|GROUDON|KYOGRE|RAYQUAZA|DEOXYS))\s*,"
)

# Species numeric IDs used in SetVar for variable-species battles
_HG_SPECIES_IDS_CACHE: dict | None = None
_HG_SPECIES_H = BASE_DIR / "pokeheartgold/include/constants/species.h"
_HG_SKIP_SPECIES = frozenset({"NONE", "EGG", "OLD_UNOWN_B", "OLD_UNOWN_C",
                               "OLD_UNOWN_D", "OLD_UNOWN_E", "OLD_UNOWN_F",
                               "OLD_UNOWN_G", "OLD_UNOWN_H", "OLD_UNOWN_I",
                               "OLD_UNOWN_J", "INVALID_DEX_ENTRY", "COUNT"})


def _load_hg_species_ids() -> dict[str, str]:
    """Parse include/constants/species.h → {numeric_str: display_name}."""
    global _HG_SPECIES_IDS_CACHE
    if _HG_SPECIES_IDS_CACHE is not None:
        return _HG_SPECIES_IDS_CACHE
    result: dict[str, str] = {}
    if _HG_SPECIES_H.exists():
        for m in re.finditer(
            r"^#define\s+SPECIES_(\w+)\s+(\d+)", _HG_SPECIES_H.read_text(encoding="utf-8"), re.M
        ):
            key, num = m.group(1), m.group(2)
            if key in _HG_SKIP_SPECIES:
                continue
            result[num] = key.replace("_", " ").title()
    _HG_SPECIES_IDS_CACHE = result
    return result


def _HG_SPECIES_ID_lookup(raw: str) -> str:
    """Convenience: resolve a numeric species string to a display name."""
    return _load_hg_species_ids().get(raw, f"Species#{raw}")


def _hg_resolve_wild_battle(body: str, code: str) -> tuple[str, str | None, bool] | None:
    """
    From BFS-collected script body, return (species_display, level_display, shiny) or None.

    Handles:
      WildBattle SPECIES_X, LVL, 0      → direct
      WildBattle VAR_TEMP_x400A, LVL, 0 → look for prior SetVar VAR_TEMP_x400A
      WildBattle VAR_TEMP_x400A, VAR_SPECIAL_x8004 → look for both SetVars
    The third WildBattle argument is the shiny flag (1 = shiny, 0 = not).
    """
    m = _HG_WILD_BATTLE_RE.search(body)
    if not m:
        return None

    raw_species = m.group(1)
    raw_level   = m.group(2)
    shiny       = m.group(3) == "1"

    # Resolve species
    if raw_species.startswith("SPECIES_"):
        species = raw_species.removeprefix("SPECIES_").replace("_", " ").title()
    elif raw_species.startswith("VAR_"):
        # Look for SetVar VAR_TEMP_x400A, X / SPECIES_X in body
        ms = _HG_SETWILD_SPECIES_RE.findall(body)
        if ms:
            # May have multiple (conditional species), gather all unique
            species_list: list[str] = []
            seen_sp: set[str] = set()
            for raw in ms:
                if raw.startswith("SPECIES_"):
                    sp = raw.removeprefix("SPECIES_").replace("_", " ").title()
                elif raw in _load_hg_species_ids():
                    sp = _load_hg_species_ids()[raw]
                else:
                    sp = f"Species#{raw}"
                if sp not in seen_sp:
                    seen_sp.add(sp)
                    species_list.append(sp)
            species = " / ".join(species_list) if species_list else "Unknown"
        else:
            species = "Unknown"
    else:
        # Numeric species ID
        species = _load_hg_species_ids().get(raw_species, f"Species#{raw_species}")

    # Resolve level
    if raw_level.isdigit():
        level = raw_level
    elif raw_level.startswith("VAR_"):
        lvl_matches = _HG_SETLEVEL_RE.findall(body)
        if lvl_matches:
            unique_levels = list(dict.fromkeys(lvl_matches))
            level = "/".join(unique_levels)
        else:
            level = "?"
    else:
        level = raw_level

    return (species, level, shiny)


def _hg_bfs_body(code: str, label: str) -> str:
    """BFS from label in zone script, returns all collected lines joined."""
    blocks = _script_blocks(code)
    seen: set[str] = set()
    queue = [label]
    parts: list[str] = []
    while queue:
        lbl = queue.pop(0)
        if lbl in seen:
            continue
        seen.add(lbl)
        block = blocks.get(lbl, "")
        parts.append(block)
        for m in re.finditer(r"\bGoTo\w*\b.*?\b(\w+)\s*$", block, re.M):
            tgt = m.group(1)
            if tgt not in seen and tgt in blocks:
                queue.append(tgt)
    return "\n".join(parts)


_HG_CUSTOM_ITEM_VAR_RE = re.compile(r"\bSetVar\s+VAR_SPECIAL_x8008\s*,\s*(\d+)")


def _hg_is_custom_item(code: str, script_label: str) -> bool:
    """True when the BFS body of script_label calls std_hidden_item_fanfare."""
    return "std_hidden_item_fanfare" in _hg_bfs_body(code, script_label)


def _hg_custom_item_name(code: str, script_label: str) -> str:
    """Item name from SetVar VAR_SPECIAL_x8008, <id> in a custom item pickup script."""
    m = _HG_CUSTOM_ITEM_VAR_RE.search(_hg_bfs_body(code, script_label))
    return _hg_item_id_to_name(int(m.group(1))) if m else "Unknown Item"


def _hg_pre_battle_text(code: str, body: str) -> str | None:
    """Return the first dialogue message found in a script body."""
    msg_re = re.compile(r"^\s+msg_(\w+)\s*$", re.M)
    m = msg_re.search(body)
    if m:
        return _clean_msg(_msg_text(m.group(1)))
    return None


def _hg_static_encounters(internal_code: str, zone_data: dict) -> list[dict]:
    """
    Find all scripted static Pokémon encounters for an HG zone.

    Checks objects with SPRITE_FOLLOWER_MON_STATIC_* sprites (those with
    non-zero scriptId that BFS-reach WildBattle) and coord events that
    trigger roaming beast releases.

    Returns list of encounter dicts with keys:
      species, level, battleable, tags, pre_battle_text, x, z
    """
    code = internal_code
    result: list[dict] = []
    seen_labels: set[str] = set()

    # Static follower objects + dedicated obstacle-Pokémon sprites → battles
    blocks = _script_blocks(code)
    for obj in zone_data.get("objects", []):
        sp = str(obj.get("spriteId", ""))
        is_follower = "FOLLOWER_MON_STATIC" in sp
        is_obstacle = sp in _HG_OBSTACLE_SPRITES
        if not is_follower and not is_obstacle:
            continue
        sid = obj.get("scriptId", 0)
        if not sid or sid == 0:
            continue
        lm = _SCRIPT_LABEL_RE.match(str(sid))
        if not lm:
            continue
        label = lm.group(1)
        body = _hg_bfs_body(code, label)
        wb = _hg_resolve_wild_battle(body, code)
        if wb is None:
            continue
        species, level, shiny = wb

        tags: list[str] = []
        if shiny:
            tags.append("Shiny")

        # Pre-battle text: last msg_* line before WildBattle
        pre_text = None
        # Walk lines before WildBattle in the block
        block_lines = blocks.get(label, "").splitlines()
        last_msg: str | None = None
        for ln in block_lines:
            mg = re.match(r"^\s+msg_(\w+)\s*$", ln)
            if mg:
                last_msg = _clean_msg(_msg_text(mg.group(1)))
            if "WildBattle" in ln and last_msg:
                pre_text = last_msg
                break
        if not pre_text:
            pre_text = _first_message(code, label)

        x, z = obj.get("x", 0), obj.get("z", 0)
        result.append({
            "species": species,
            "level":   level,
            "battleable": True,
            "tags":    tags,
            "pre_battle_text": pre_text,
            "x": x, "z": z,
        })

    # Coord events (coords array) — check for roaming beast release
    for ce in zone_data.get("coords", []):
        sid = ce.get("scriptId", 0)
        if not sid or sid == 0:
            continue
        lm = _SCRIPT_LABEL_RE.match(str(sid))
        if not lm:
            continue
        label = lm.group(1)
        body = _hg_bfs_body(code, label)
        # Skip if it's a direct battle (those are in objects)
        if _HG_WILD_BATTLE_RE.search(body):
            continue
        # Check for beast release: PlayCry for legendary species
        cry_matches = _HG_PLAYCRY_LEGEND_RE.findall(body)
        if not cry_matches:
            continue
        # Determine where the Pokémon goes next by reading which HIDE flag is
        # cleared (ClearFlag FLAG_HIDE_<DEST>_SUICUNE / _RAIKOU / _ENTEI).
        # If no such flag exists this is just a battle-approach cutscene, not
        # a sighting event — skip it (the object event handles the battle).
        _NEXT_ZONE_RE = re.compile(
            r"ClearFlag\s+FLAG_HIDE_(\w+?)_(?:SUICUNE|RAIKOU|ENTEI)\b")
        next_zone_m = _NEXT_ZONE_RE.search(body)
        if not next_zone_m:
            continue
        next_dest = next_zone_m.group(1).replace("_", " ").title()
        species_list = list(dict.fromkeys(
            s.removeprefix("SPECIES_").replace("_", " ").title()
            for s in cry_matches
        ))
        species_str = " / ".join(species_list)
        # Trigger message: first msg_* after beast cry
        trigger_text: str | None = None
        msg_re = re.compile(r"^\s+msg_(\w+)\s*$", re.M)
        after_flag = False
        for ln in body.splitlines():
            if _HG_PLAYCRY_LEGEND_RE.search(ln):
                after_flag = True
            if after_flag:
                mm = msg_re.match(ln)
                if mm:
                    trigger_text = _clean_msg(_msg_text(mm.group(1)))
                    break
        result.append({
            "species": species_str,
            "level":   None,
            "battleable": False,
            "tags":    [f"departs → {next_dest}"],
            "pre_battle_text": trigger_text,
            "x": ce.get("x", 0), "z": ce.get("z", 0),
        })

    return result


def render_static_encounter_section(encounters: list,
                                    col_off: int = 0,
                                    row_off: int = 0,
                                    overlapped: dict | None = None) -> list[str]:
    if not encounters:
        return []
    SEP = "=" * 70
    lines = ["", SEP, "  Static Encounter Index  (HeartGold)", SEP, ""]
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
# Event section renderers
# ---------------------------------------------------------------------------

_SEP = "=" * 70


def _pretty(const: str, prefix: str) -> str:
    return const.replace(prefix, "").replace("_", " ").title()


def render_apricorn_section(apricorns: list,
                            col_off: int = 0, row_off: int = 0) -> list[str]:
    if not apricorns:
        return []
    lines = ["", _SEP, "  Apricorn Tree Index", _SEP, ""]
    for a in apricorns:
        col = a["x"] - col_off
        row = a["z"] - row_off
        lines.append(f"  (col={col:>3}, row={row:>3})  {a['apricorn_name']}  [tree {a['tree_index']}]")
    lines.append("")
    return lines


def render_field_object_section(field_objects: list,
                                col_off: int = 0, row_off: int = 0) -> list[str]:
    if not field_objects:
        return []
    lines = ["", _SEP, "  Field Obstacles", _SEP, ""]
    for fo in field_objects:
        lines.append(f"  [{fo['char']}]  (col={fo['x'] - col_off:>3}, "
                     f"row={fo['z'] - row_off:>3})  {fo['label']}")
    lines.append("")
    return lines


def render_item_section(items: list, col_off: int = 0, row_off: int = 0) -> list[str]:
    if not items:
        return []
    lines = ["", _SEP, "  Item Balls", _SEP, ""]
    for it in items:
        lines.append(f"  (col={it['x'] - col_off:>3}, row={it['z'] - row_off:>3})"
                     f"  ⊙  {it['item']}")
    lines.append("")
    return lines


def render_hidden_item_section(hidden: list, col_off: int = 0,
                               row_off: int = 0) -> list[str]:
    if not hidden:
        return []
    lines = ["", _SEP, "  Hidden Items", _SEP, ""]
    for it in hidden:
        lines.append(f"  (col={it['x'] - col_off:>3}, row={it['z'] - row_off:>3})"
                     f"  *  {it['item']}")
    lines.append("")
    return lines


def _render_npc_states(sd: dict) -> list[str]:
    """Render script_data states into display lines."""
    states = sd.get("states", [])
    lines: list[str] = []
    INDENT = "          "   # 10 spaces (standard NPC content indent)
    EXTRA  = "              "  # 14 spaces (content under a state tag)

    single = len(states) == 1 and states[0]["flag"] is None

    for state in states:
        flag     = state.get("flag")
        polarity = state.get("polarity", "post")
        pre_msgs = state.get("pre_msgs", [])
        gives    = state.get("gives", [])

        if single:
            content = INDENT
        else:
            tag = ("[default]" if flag is None
                   else (f"[~{flag}]" if polarity == "pre" else f"[{flag}]"))
            lines.append(f"{INDENT}{tag}")
            content = EXTRA

        yesno    = state.get("yesno", False)
        declined = state.get("declined_msg")
        if yesno:
            if pre_msgs:
                lines.append(f"{content}Prompt (yes/no):    \"{pre_msgs[-1]}\"")
            if declined:
                lines.append(f"{content}Declined:           \"{declined}\"")
        elif gives:
            for msg in pre_msgs:
                lines.append(f"{content}Dialogue (before):  \"{msg}\"")
            for g in gives:
                item_name = _item_name(g["item"])
                qty_tag = f"  ×{g['qty']}" if g["qty"] > 1 else ""
                lines.append(f"{content}Gives:  {item_name}{qty_tag}")
                for msg in g.get("post_msgs", []):
                    lines.append(f"{content}Dialogue (after):   \"{msg}\"")
        else:
            for msg in pre_msgs:
                lines.append(f"{content}\"{msg}\"")

    return lines


def render_npc_section(npcs: list, col_off: int = 0, row_off: int = 0) -> list[str]:
    if not npcs:
        return []
    lines = ["", _SEP, "  NPC Index", _SEP, ""]
    for n in npcs:
        sprite = _pretty(n["sprite"], "SPRITE_")
        lines.append(f"  (col={n['x'] - col_off:>3}, row={n['z'] - row_off:>3})"
                     f"  {sprite}")
        lines.append(f"          Movement:  {_fmt_movement(n.get('movement', 'MOVEMENT_TYPE_NONE'))}")
        sd = n.get("script_data")
        if sd:
            lines.extend(_render_npc_states(sd))
        for sf in sorted(n.get("service_flags", ())):
            lines.append(f"          [{sf}]")
        lines.append("")
    return lines


#: Message slots worth printing, in the order a battle plays them out.
_TRAINER_MSG_ORDER: list[tuple[str, str]] = [
    ("TRMSG_INTRO",                   "Pre-battle"),
    ("TRMSG_DBL_INTRO_1",             "Pre-battle 1"),
    ("TRMSG_DBL_INTRO_2",             "Pre-battle 2"),
    ("TRMSG_LAST_POKE",               "Last Pokémon"),
    ("TRMSG_LAST_POKE_HALF",          "Last Pokémon (half HP)"),
    ("TRMSG_DBL_1POKE_1",             "Last Pokémon 1"),
    ("TRMSG_DBL_1POKE_2",             "Last Pokémon 2"),
    ("TRMSG_LOSE",                    "Defeat"),
    ("TRMSG_DBL_LOSE_1",              "Defeat 1"),
    ("TRMSG_DBL_LOSE_2",              "Defeat 2"),
    ("TRMSG_WIN",                     "Victory"),
    ("TRMSG_AFTER",                   "Post-battle"),
    ("TRMSG_DBL_AFTER_1",             "Post-battle 1"),
    ("TRMSG_DBL_AFTER_2",             "Post-battle 2"),
    ("TRMSG_PHONE_REMATCH_INTRO",     "Phone rematch"),
    ("TRMSG_PHONE_REMATCH_DBL_INTRO_1", "Phone rematch 1"),
    ("TRMSG_PHONE_REMATCH_DBL_INTRO_2", "Phone rematch 2"),
]


def render_trainer_section(trainers: list, col_off: int = 0,
                           row_off: int = 0) -> list[str]:
    if not trainers:
        return []
    lines = ["", _SEP, "  Trainer Index", _SEP, ""]

    def _party(party: list, indent: str = "            ") -> None:
        for mon in party:
            iv_raw = mon['difficulty']
            lines.append(f"{indent}{mon['species']:<18} Lv {mon['level']:<4}"
                         f" IV={iv_raw} ({iv_raw * 31 // 255 & 0x1F}/31)")
            if mon["moves"]:
                lines.append(f"{indent}{'':18} Moves: {', '.join(mon['moves'])}")
            if mon["item"]:
                lines.append(f"{indent}{'':18} Item:  {mon['item']}")

    for t in trainers:
        cls    = _pretty(t["class"], "TRAINERCLASS_") if t["class"] else "?"
        double = "  [Double Battle]" if t["double"] else ""
        prize  = f"  Prize: ¥{t['prize']}" if t["prize"] else ""
        cat    = f"  [{t.get('category', 'Standard')}]"
        lines.append(f"  (col={t['x'] - col_off:>3}, row={t['z'] - row_off:>3})"
                     f"  {cls} {t['name']}{cat}{double}{prize}")
        lines.append(f"          [{t['const']}]")
        if t["items"]:
            lines.append(f"          Items: {', '.join(t['items'])}")
        msgs = t["messages"]
        for key, label in _TRAINER_MSG_ORDER:
            text = _clean_msg(msgs.get(key))
            if text:
                lines.append(f"          {label + ':':<24}\"{text}\"")
        lines.append("          Initial battle:")
        _party(t["party"])
        for i, r in enumerate(t["rematches"], 1):
            prize = f"  Prize: ¥{r['prize']}" if r["prize"] else ""
            lines.append(f"          Phone rematch {i}:{prize}")
            _party(r["party"])
        # Partner trainer (tag / multi battles)
        ally_list = t.get("ally", [])
        if ally_list:
            if len(ally_list) == 1:
                a = ally_list[0]
                acls = _pretty(a.get("class", ""), "TRAINERCLASS_") or "?"
                lines.append(f"          Partner:  {acls} {a.get('name','?')}  [{a.get('const','')}]")
                _party(a.get("party", []))
            else:
                lines.append("          Partner (starter-dependent):")
                for a in ally_list:
                    acls = _pretty(a.get("class", ""), "TRAINERCLASS_") or "?"
                    lines.append(f"            [{a.get('const','')}]  {acls} {a.get('name','?')}")
                    _party(a.get("party", []), indent="              ")
        lines.append("")
    return lines


def render_gym_leader_section(gym_leaders: list,
                              col_off: int = 0, row_off: int = 0) -> list[str]:
    if not gym_leaders:
        return []
    lines = ["", _SEP, "  Gym Leader", _SEP, ""]

    _GL_MSG_ORDER: list[tuple[str, str]] = [
        ("TRMSG_LAST_POKE",      "Last Pokémon"),
        ("TRMSG_LAST_POKE_HALF", "Last mon (½ HP)"),
        ("TRMSG_LOSE",           "Defeat (trainer JSON)"),
    ]

    def _party(party: list, indent: str = "            ") -> None:
        for mon in party:
            iv_raw = mon['difficulty']
            lines.append(f"{indent}{mon['species']:<18} Lv {mon['level']:<4}"
                         f" IV={iv_raw} ({iv_raw * 31 // 255 & 0x1F}/31)")
            if mon["moves"]:
                lines.append(f"{indent}{'':18} Moves: {', '.join(mon['moves'])}")
            if mon["item"]:
                lines.append(f"{indent}{'':18} Item:  {mon['item']}")

    for gl in gym_leaders:
        # Class is TRAINERCLASS_LEADER_NAME — just show "Leader"
        raw_cls = gl.get("class", "")
        cls = "Leader" if "LEADER" in raw_cls else (_pretty(raw_cls, "TRAINERCLASS_") if raw_cls else "?")
        prize = f"  Prize: ¥{gl['prize']}" if gl.get("prize") else ""
        lines.append(f"  (col={gl['x'] - col_off:>3}, row={gl['z'] - row_off:>3})"
                     f"  {cls} {gl['name']}  [{gl['trainer_const']}]  [Interaction]{prize}")
        if gl.get("items"):
            lines.append(f"          Healing items: {', '.join(gl['items'])}")
        if gl.get("gym_badge"):
            lines.append(f"          Gym badge:     {gl['gym_badge']}")
        if gl.get("gives_item"):
            gi = gl["gives_item"]
            lines.append(f"          Gives item:    {gi['item_id']}  ×{gi['quantity']}")
        for pb in gl.get("pre_battle", []):
            lines.append(f"          Pre-battle:   \"{_clean_msg(pb) or pb}\"")
        msgs = gl.get("messages", {})
        for key, label in _GL_MSG_ORDER:
            text = _clean_msg(msgs.get(key))
            if text:
                lines.append(f"          {label + ':':<28}\"{text}\"")
        post = gl.get("post_battle", [])
        for i, pb in enumerate(post):
            lbl = "Post-battle:" if i == 0 else " " * 12
            lines.append(f"          {lbl:<28}\"{_clean_msg(pb) or pb}\"")
        lines.append("          Initial battle:")
        _party(gl.get("party", []))
        for i, r in enumerate(gl.get("rematches", []), 1):
            prize = f"  Prize: ¥{r['prize']}" if r.get("prize") else ""
            lines.append(f"          Phone rematch {i}:{prize}  [{r['const']}]")
            _party(r["party"])
        lines.append("")

    return lines


# ---------------------------------------------------------------------------
# Coin Prize Exchange
# ---------------------------------------------------------------------------

_HG_ITEM_C = BASE_DIR / "pokeheartgold/src/item.c"
_HG_TM_MOVES_CACHE: dict[str, str] | None = None


def _hg_tm_move_name(item_const: str) -> str | None:
    """ITEM_TM09 → 'Bullet Seed', ITEM_HM04 → 'Strength' (from sTMHMMoves)."""
    global _HG_TM_MOVES_CACHE
    if _HG_TM_MOVES_CACHE is None:
        _HG_TM_MOVES_CACHE = {}
        if _HG_ITEM_C.exists():
            text = _HG_ITEM_C.read_text(encoding="utf-8")
            start = text.find("sTMHMMoves[]")
            if start != -1:
                body = text[start: text.find("};", start)]
                # HMs carry explicit comments: MOVE_CUT, // HM01
                for m in re.finditer(r"(MOVE_\w+)[^/\n]*//\s*HM(\d+)", body):
                    num = int(m.group(2))
                    _HG_TM_MOVES_CACHE[f"ITEM_HM{num:02d}"] = move_display(m.group(1))
                # TMs: positional (no HM comment on their lines)
                for i, m in enumerate(re.finditer(r"(MOVE_\w+)", body), start=1):
                    key = f"ITEM_TM{i:02d}"
                    if key not in _HG_TM_MOVES_CACHE:
                        _HG_TM_MOVES_CACHE[key] = move_display(m.group(1))
    if not item_const.startswith(("ITEM_TM", "ITEM_HM")):
        return None
    return _HG_TM_MOVES_CACHE.get(item_const)


def _hg_prize_script_prizes(script_path) -> list[dict]:
    """Parse a HeartGold prize exchange script → list of {name, coins, kind} dicts."""
    from pathlib import Path
    path = Path(script_path)
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")

    prizes: list[dict] = []
    seen: set[str] = set()

    # Items: GoToIfNoItemSpace ITEM_X → TakeCoins N  (in sequential blocks)
    item_re = re.compile(
        r"GoToIfNoItemSpace\s+(ITEM_\w+)\s*,\s*1\s*,\s*\S+"
        r"(?:.*?\n)+?.*?TakeCoins\s+(\d+)",
        re.M,
    )
    for m in item_re.finditer(text):
        item_const = m.group(1)
        cost = int(m.group(2))
        if item_const in seen:
            continue
        seen.add(item_const)
        move = _hg_tm_move_name(item_const)
        if move:
            num = item_const.removeprefix("ITEM_TM")
            name = f"TM{num} ({move})"
        else:
            name = _item_name(item_const)
        prizes.append({"name": name, "coins": cost, "kind": "item"})

    # Pokémon prizes: species are set by SetOrCopyVar VAR_TEMP_x4002, <id>.
    # After GiveMon, Compare VAR_TEMP_x4002, X → TakeCoins N handles each
    # explicitly-tested species; the last TakeCoins is the fallthrough.
    # Strategy:
    #   1. Find GiveMon; after it, read Compare-order species and TakeCoins in order.
    #   2. Pair them: compare_species[i] → takecoin_values[i]; last TakeCoins is
    #      the fallthrough cost for any species not matched by a Compare.
    #   3. Emit all SetOrCopyVar species with their resolved costs.

    givemon_pos = text.find("GiveMon VAR_TEMP_x4002")
    if givemon_pos != -1:
        after_give = text[givemon_pos:]
        compare_species = [
            m.group(1) for m in re.finditer(
                r"Compare\s+VAR_TEMP_x4002\s*,\s*(\d+)", after_give
            )
        ]
        takecoin_values = [
            int(m.group(1)) for m in re.finditer(r"TakeCoins\s+(\d+)", after_give)
        ]
        # compare_species[i] pairs with takecoin_values[i]; last value = fallthrough
        species_cost: dict[str, int] = dict(zip(compare_species, takecoin_values))
        fallthrough_cost = (
            takecoin_values[len(compare_species)]
            if len(takecoin_values) > len(compare_species)
            else None
        )

        # All offered species in SetOrCopyVar order
        all_sids: list[str] = []
        seen_sids: set[str] = set()
        for m in re.finditer(r"SetOrCopyVar\s+VAR_TEMP_x4002\s*,\s*(\d+)", text):
            sid = m.group(1)
            if sid not in seen_sids:
                seen_sids.add(sid)
                all_sids.append(sid)

        for sid in all_sids:
            if sid in seen:
                continue
            seen.add(sid)
            cost = species_cost.get(sid, fallthrough_cost)
            if cost is None:
                continue
            species_name = _HG_SPECIES_ID_lookup(sid)
            prizes.append({"name": species_name, "coins": cost, "kind": "pokemon"})

    return prizes


_HG_SCRIPT_DIR = BASE_DIR / "pokeheartgold/files/fielddata/script/scr_seq"

_HG_PRIZE_EXCHANGE_SCRIPTS: dict[str, str] = {
    "MAP_T07R0501": "scr_seq_0804_T07R0501.s",  # Celadon Prize Corner
    "MAP_T25R1101": "scr_seq_0906_T25R1101.s",  # Goldenrod Game Corner JP
}


def hg_game_corner_prizes(internal_code: str) -> list[dict]:
    """Return coin prizes for a HeartGold prize exchange map, or [] if not one."""
    script_name = _HG_PRIZE_EXCHANGE_SCRIPTS.get(internal_code)
    if not script_name:
        return []
    return _hg_prize_script_prizes(_HG_SCRIPT_DIR / script_name)


def render_coin_prize_section(prizes: list, col_off: int = 0, row_off: int = 0,
                               game_tag: str = "HeartGold") -> list[str]:
    if not prizes:
        return []
    SEP = "=" * 70
    lines = ["", SEP, f"  Coin Prize Exchange  ({game_tag})", SEP, ""]
    for p in prizes:
        coins_str = f"{p['coins']:,}"
        kind_note = "  [Pokémon]" if p["kind"] == "pokemon" else ""
        lines.append(f"  {p['name']:<35}  {coins_str} coins{kind_note}")
    lines.append("")
    return lines
