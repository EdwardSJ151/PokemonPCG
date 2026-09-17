"""
gen4_data.py — Game-agnostic Gen 4 (Nintendo DS) engine parsers shared by
platinum_data.py and heartgold_data.py.

CONTRACT (user-set, binding):
- Core logic here is agnostic: a change in this file affects BOTH games and
  must not introduce errors into either. Small `game == "platinum"` /
  `game == "heartgold"` conditional branches are allowed for simplicity, but
  the core algorithms stay shared.
- Platinum is the verified base; this module was extracted from
  platinum_data.py verbatim (byte-identical Platinum output is the
  regression test).

Contents:
- NSBMD/BMD0 parsing: NameLists, render-command bytecode, GPU display lists
  (verified 432/432 vs Route 213 searock ground truth)
- Land-data parsing: terrain permission grid, props section, BDHC height map
- Rasterization: bbox rule (chunky obstacles) and tile-center rule
  (rim-overhanging meshes; verified 0-extra vs Route 203 tree ground truth)
- Prop model footprints: vertex bbox × up_scale, 16 world units per tile
  (verified vs Jubilife painted collision)
"""

import math
import struct
from pathlib import Path

# ---------------------------------------------------------------------------
# NARC archive reader (HeartGold stores land data / building models in NARCs;
# Platinum ships them as loose files in the decomp)
# ---------------------------------------------------------------------------

class Narc:
    """
    Minimal Nintendo NARC archive reader.
    Layout: 16-byte header, then BTAF (file allocation table), BTNF (names),
    GMIF (file data). File i occupies data[start_i:end_i] relative to GMIF+8.
    """

    def __init__(self, path):
        raw = Path(path).read_bytes()
        magic, _bom, _ver, _fsize, hdr_size, _nblocks = struct.unpack_from("<4sHHIHH", raw, 0)
        if magic != b"NARC":
            raise ValueError(f"{path}: not a NARC archive")
        off = hdr_size
        btaf, btaf_size, count = struct.unpack_from("<4sIH", raw, off)
        if btaf != b"BTAF":
            raise ValueError(f"{path}: missing BTAF block")
        self._fat = off + 12
        off += btaf_size
        _btnf, btnf_size = struct.unpack_from("<4sI", raw, off)
        off += btnf_size
        gmif, _gmif_size = struct.unpack_from("<4sI", raw, off)
        if gmif != b"GMIF":
            raise ValueError(f"{path}: missing GMIF block")
        self._data = off + 8
        self._raw = raw
        self.count = count

    def file(self, index: int) -> bytes:
        """Return the bytes of archive member `index`."""
        if not (0 <= index < self.count):
            raise IndexError(f"NARC index {index} out of range (count={self.count})")
        start, end = struct.unpack_from("<2I", self._raw, self._fat + index * 8)
        return self._raw[self._data + start : self._data + end]


# ---------------------------------------------------------------------------
# Land-data section layout
#
# Both games store one block (32×32 tiles) per land-data file with the same
# four sections — terrain permissions, prop placements, BMD0 model, BDHC
# height map — but the headers differ:
#
#   Platinum: 16-byte header  [terrain_size, props_size, model_size, bdhc_size]
#             terrain starts at 0x10, sections follow back to back.
#
#   HeartGold: 20-byte header — same four sizes, then u16 magic 0x1234 and a
#             u16 "extra" size. The header ends at 0x14 (the HG decomp states
#             this outright: TERRAIN_ATTRIBUTES_OFFSET is 0x14 in HG/SS vs
#             0x10 in Platinum), then an extra region of `extra` bytes sits
#             BEFORE the terrain grid.
#             Verified on all 676 HG land files: magic always 0x1234, sizes
#             always sum to the file length, BMD0/BDHC always land where this
#             layout predicts.
#
#             The extra region's POSITION cost a real bug: total file size is
#             the same whether it precedes or follows the terrain, so the size
#             check passes either way. Putting it after the terrain reads the
#             grid `extra/2` tiles early. Route 30's blocks caught it — land 6
#             has extra=0 (rows 0-31 correct) while lands 7/8 have extra=16, so
#             the map visibly jumped 8 tiles at the block seam.
# ---------------------------------------------------------------------------

TERRAIN_BYTES = 0x800   # 32 × 32 × u16, both games

_LAND_HEADER_SIZE = {"platinum": 0x10, "heartgold": 0x14}


def land_sections(raw: bytes, game: str) -> dict | None:
    """
    Return {'terrain': (off, size), 'props': ..., 'model': ..., 'bdhc': ...}
    byte ranges for one land-data block, or None if the blob is unusable.
    """
    hdr = _LAND_HEADER_SIZE.get(game)
    if hdr is None:
        raise ValueError(f"unknown game {game!r}")
    if len(raw) < hdr:
        return None
    terrain_size, props_size, model_size, bdhc_size = struct.unpack_from("<4I", raw, 0)
    if terrain_size != TERRAIN_BYTES:
        return None
    # HG only: u16 magic 0x1234 at 16, u16 extra-region size at 18
    extra = struct.unpack_from("<H", raw, 18)[0] if game == "heartgold" else 0

    off = hdr + extra
    terrain = (off, terrain_size); off += terrain_size
    props   = (off, props_size);   off += props_size
    model   = (off, model_size);   off += model_size
    bdhc    = (off, bdhc_size)
    if bdhc[0] + bdhc_size > len(raw):
        return None
    return {"terrain": terrain, "props": props, "model": model, "bdhc": bdhc}

# ---------------------------------------------------------------------------
# NDS GPU / NSBMD constants
# ---------------------------------------------------------------------------

# NDS GPU command parameter counts (from desmume gfx3d_commandTypes)
_NDS_PARAM_COUNTS: dict[int, int] = {
    0x00: 0, 0x10: 1, 0x11: 0, 0x12: 1, 0x13: 1, 0x14: 1,
    0x15: 0, 0x16: 16, 0x17: 12, 0x18: 16, 0x19: 12, 0x1A: 9,
    0x1B: 3, 0x1C: 3, 0x20: 1, 0x21: 1, 0x22: 1, 0x23: 2,
    0x24: 1, 0x25: 1, 0x26: 1, 0x27: 1, 0x28: 1, 0x29: 1,
    0x2A: 1, 0x2B: 1, 0x30: 1, 0x31: 1, 0x32: 1, 0x33: 1,
    0x34: 32, 0x40: 1, 0x41: 0, 0x50: 1, 0x60: 1, 0x70: 3,
    0x71: 2, 0x72: 1,
}

# NSBMD render-command opcodes -> parameter byte counts (scurest nsbmd_docs / apicula)
_RENDER_CMD_PARAMS: dict[int, int] = {
    0x00: 0, 0x01: 0, 0x02: 2, 0x03: 1,
    0x04: 1, 0x24: 1, 0x44: 1,   # bind material (param = material index)
    0x05: 1,                      # draw mesh (param = mesh index)
    0x06: 3, 0x26: 4, 0x46: 4, 0x66: 5,
    0x07: 1, 0x47: 2, 0x08: 1, 0x09: 8, 0x0B: 0, 0x0C: 2, 0x0D: 2,
}

# Two mesh faces further apart than this in y are separate elevation levels
# (e.g. a bike bridge crossing above a foot bridge, both material 'bridge').
_LEVEL_GAP_FP = 1024


def _s16(v: int) -> int:
    return v if v < 32768 else v - 65536


def _s10(v: int) -> int:
    return v if v < 512 else v - 1024


# ---------------------------------------------------------------------------
# NSBMD parsing
# ---------------------------------------------------------------------------

def _parse_nds_faces(data: bytes) -> list[list[tuple[int, int, int]]]:
    """
    Parse an NDS GPU display list into faces: lists of (x, y, z) fixed-point
    vertices (1.3.12 format), split per primitive according to BEGIN_VTXS type
    (0=tris, 1=quads, 2=tri strip, 3=quad strip).
    """
    prims: list[tuple[int, list]] = []
    cur: list | None = None
    cx = cy = cz = 0
    pos = 0
    n = len(data)
    while pos + 4 <= n:
        pkt = struct.unpack_from("<I", data, pos)[0]
        ops = [(pkt >> (8 * i)) & 0xFF for i in range(4)]
        pos += 4
        total = sum(_NDS_PARAM_COUNTS.get(op, 0) for op in ops)
        if pos + total * 4 > n:
            break
        pp = pos
        for op in ops:
            np2 = _NDS_PARAM_COUNTS.get(op, 0)
            ps = [struct.unpack_from("<I", data, pp + i * 4)[0] for i in range(np2)]
            pp += np2 * 4
            if op == 0x40:   # BEGIN_VTXS
                cur = []
                prims.append((ps[0] & 3, cur))
            elif op == 0x23: # VTX_16: p0 = X | Y<<16, p1 = Z
                cx = _s16(ps[0] & 0xFFFF)
                cy = _s16((ps[0] >> 16) & 0xFFFF)
                cz = _s16(ps[1] & 0xFFFF)
                if cur is not None: cur.append((cx, cy, cz))
            elif op == 0x24: # VTX_10: bits 0-9=X, 10-19=Y, 20-29=Z (each s10 << 6)
                cx = _s10(ps[0] & 0x3FF) << 6
                cy = _s10((ps[0] >> 10) & 0x3FF) << 6
                cz = _s10((ps[0] >> 20) & 0x3FF) << 6
                if cur is not None: cur.append((cx, cy, cz))
            elif op == 0x25: # VTX_XY
                cx = _s16(ps[0] & 0xFFFF)
                cy = _s16((ps[0] >> 16) & 0xFFFF)
                if cur is not None: cur.append((cx, cy, cz))
            elif op == 0x26: # VTX_XZ
                cx = _s16(ps[0] & 0xFFFF)
                cz = _s16((ps[0] >> 16) & 0xFFFF)
                if cur is not None: cur.append((cx, cy, cz))
            elif op == 0x27: # VTX_YZ
                cy = _s16(ps[0] & 0xFFFF)
                cz = _s16((ps[0] >> 16) & 0xFFFF)
                if cur is not None: cur.append((cx, cy, cz))
            elif op == 0x28: # VTX_DIFF: 10-bit signed deltas per axis
                cx += _s10(ps[0] & 0x3FF)
                cy += _s10((ps[0] >> 10) & 0x3FF)
                cz += _s10((ps[0] >> 20) & 0x3FF)
                if cur is not None: cur.append((cx, cy, cz))
        pos += total * 4

    faces: list[list[tuple[int, int, int]]] = []
    for ptype, verts in prims:
        if ptype == 0:      # separate triangles
            faces.extend(verts[i:i+3] for i in range(0, len(verts) - 2, 3))
        elif ptype == 1:    # separate quads
            faces.extend(verts[i:i+4] for i in range(0, len(verts) - 3, 4))
        elif ptype == 2:    # triangle strip
            faces.extend(verts[i:i+3] for i in range(len(verts) - 2))
        else:               # quad strip
            faces.extend(verts[i:i+4] for i in range(0, len(verts) - 3, 2))
    return faces


def _parse_namelist(buf: bytes, base: int) -> tuple[int, list[int], list[bytes]]:
    """
    Parse a NameList at buf[base:].
    Returns (count, data_u32_list, name_list).
    Layout: dummy(1) count(1) size(2) UnknownHeader(subhdr_size bytes) unknown_u32[count]
            element_size(2) data_section_size(2) data[count*el_size bytes] names[count×16]
    names_start = data_start + count * el_size  (NOT data_sec_size, which includes padding)
    """
    count = buf[base + 1]
    subhdr_size = struct.unpack_from("<H", buf, base + 4)[0]
    el_off = base + 4 + subhdr_size + 4 * count          # position of element_size u16
    el_size = struct.unpack_from("<H", buf, el_off)[0]
    data_start = el_off + 4                               # skip element_size(2) + data_sec_size(2)
    data = [struct.unpack_from("<I", buf, data_start + i * el_size)[0] for i in range(count)]
    names_start = data_start + count * el_size            # names follow immediately after data
    names = [buf[names_start + i * 16 : names_start + i * 16 + 16].rstrip(b"\x00") for i in range(count)]
    return count, data, names


def _all_material_mesh_faces(bmd0: bytes) -> dict[str, list[list[tuple[int, int, int]]]]:
    """
    Walk the MDL0 block and return {material_name: faces} for EVERY mesh in
    the model. The render-command bytecode maps materials to meshes: opcode
    0x04/0x24/0x44 binds a material (param = material index), opcode 0x05
    draws a mesh (param = mesh index).
    """
    # MDL0 block at 0x14; Model NameList at MDL0+8; data[0] is the model offset
    # relative to the MDL0 block start.
    mdl0_start = 0x14
    try:
        _, model_offsets, _ = _parse_namelist(bmd0, mdl0_start + 8)
        if not model_offsets:
            return {}
        model_base = mdl0_start + model_offsets[0]

        # Model header: [filesize, render_cmds_off, materials_off, meshes_off]
        _, rc_off, mat_off, mesh_off = struct.unpack_from("<4I", bmd0, model_base)

        # Materials section header: u16 offset to the material NameList
        mat_sec = model_base + mat_off
        mat_list_rel = struct.unpack_from("<H", bmd0, mat_sec)[0]
        _, _, mat_names = _parse_namelist(bmd0, mat_sec + mat_list_rel)
    except Exception:
        return {}

    # Decode render-command bytecode: which material draws which mesh
    mesh_mat: dict[int, int] = {}
    cur_mat = None
    i = model_base + rc_off
    while i < len(bmd0):
        op = bmd0[i]; i += 1
        if op not in _RENDER_CMD_PARAMS:
            break
        n = _RENDER_CMD_PARAMS[op]
        params = bmd0[i : i + n]; i += n
        if op in (0x04, 0x24, 0x44):
            cur_mat = params[0]
        elif op == 0x05:
            if cur_mat is not None:
                mesh_mat[params[0]] = cur_mat
        elif op == 0x01:  # END
            break

    try:
        mesh_nl_base = model_base + mesh_off
        count, mesh_data, _ = _parse_namelist(bmd0, mesh_nl_base)
    except Exception:
        return {}

    result: dict[str, list[list[tuple[int, int, int]]]] = {}
    for mi, mat_idx in mesh_mat.items():
        if mi >= count or mat_idx >= len(mat_names):
            continue
        name = mat_names[mat_idx].decode("ascii", "replace")
        # Mesh struct: [dummy u16, size u16, unknown u32, cmds_off u32, cmds_len u32]
        # cmds_off is relative to the mesh struct itself.
        mesh_struct = mesh_nl_base + mesh_data[mi]
        _, _, cmds_off, cmds_len = struct.unpack_from("<4I", bmd0, mesh_struct)
        dl = bmd0[mesh_struct + cmds_off : mesh_struct + cmds_off + cmds_len]
        result.setdefault(name, []).extend(_parse_nds_faces(dl))
    return result


def _cluster_levels(ys: list[int]) -> tuple[int, ...]:
    """Collapse raw face heights into distinct descending elevation levels."""
    ys = sorted(ys, reverse=True)
    levels = [ys[0]]
    for y in ys[1:]:
        if levels[-1] - y > _LEVEL_GAP_FP:
            levels.append(y)
    return tuple(levels)


# ---------------------------------------------------------------------------
# Land-data parsing (per-block map binaries)
# ---------------------------------------------------------------------------

def land_data_bmd0(raw: bytes, game: str) -> bytes:
    """Slice the BMD0 model section out of a land-data block binary."""
    sec = land_sections(raw, game)
    if sec is None:
        return b""
    off, size = sec["model"]
    return raw[off : off + size]


def parse_terrain_grid(raw: bytes, game: str, bnames: dict) -> list[list[dict]] | None:
    """
    Parse a land-data block binary into a 32×32 grid of behavior dicts.
    bnames: {behavior_int: behavior_name} — per-game (each game has its own
    metatile_behavior.h constants).
    """
    sec = land_sections(raw, game)
    if sec is None:
        return None
    base = sec["terrain"][0]
    grid = []
    for row in range(32):
        r = []
        for col in range(32):
            idx = base + (row * 32 + col) * 2
            val = struct.unpack_from("<H", raw, idx)[0]
            behavior = val & 0xFF
            passable = (val & 0x8000) == 0
            bname = bnames.get(behavior, f"TILE_BEHAVIOR_{behavior}")
            r.append({"behavior": behavior, "behavior_name": bname,
                      "passable": passable, "raw": val})
        grid.append(r)
    return grid


def parse_prop_entries(raw: bytes, game: str) -> list[tuple[int, float, float]]:
    """
    Return (model_id, local_col, local_row) placements from the props section
    of a land-data block binary. Entry format (decomp MapPropFile, 48 bytes):
    int modelID; VecFx32 position, rotation, scale; int dummy[2].
    Position: 1 tile = 16 fx32 world units, block center at tile (16, 16).
    """
    result: list[tuple[int, float, float]] = []
    sec = land_sections(raw, game)
    if sec is not None:
        off, size = sec["props"]
        props = raw[off : off + size]
        for i in range(len(props) // 48):
            model_id, px, _py, pz = struct.unpack_from("<4i", props, i * 48)
            result.append((model_id,
                           px / 4096.0 / 16.0 + 16.0,
                           pz / 4096.0 / 16.0 + 16.0))
    return result


def land_material_tile_ys(raw: bytes, game: str) -> dict[str, dict[tuple[int, int], tuple[int, ...]]]:
    """
    Return {material_name: {(col, row): (y, ...)}} for EVERY material in this
    block's model. y values are distinct height levels (descending, clustered
    by _LEVEL_GAP_FP) so stacked meshes of the same material count as
    separate levels. Footprint per face is its bbox in tile units
    (1024 fixed-point units per tile, block center at tile 16,16).
    Coordinates may fall outside [0, 32): faces can overhang into
    neighboring blocks, and the caller must apply the spill globally.
    """
    result: dict[str, dict[tuple[int, int], tuple[int, ...]]] = {}
    bmd0 = land_data_bmd0(raw, game)
    if not bmd0:
        return result

    for material, faces in _all_material_mesh_faces(bmd0).items():
        raw_ys: dict[tuple[int, int], list[int]] = {}
        for face in faces:
            xs = [v[0] for v in face]
            ys = [v[1] for v in face]
            zs = [v[2] for v in face]
            y_max = max(ys)
            c0 = math.floor(min(xs) / 1024 + 16)
            c1 = math.ceil(max(xs) / 1024 + 16)
            r0 = math.floor(min(zs) / 1024 + 16)
            r1 = math.ceil(max(zs) / 1024 + 16)
            for c in range(c0, max(c1, c0 + 1)):
                for r in range(r0, max(r1, r0 + 1)):
                    raw_ys.setdefault((c, r), []).append(y_max)
        result[material] = {pos: _cluster_levels(ys) for pos, ys in raw_ys.items()}

    return result


def land_surface_stacks(raw: bytes, game: str,
                        skip_material=None) -> dict[tuple[int, int], dict[int, frozenset]]:
    """
    Return {(col, row): {y: frozenset(material, ...)}} — every HORIZONTAL mesh
    face the block draws over that tile, i.e. the stack of visible surfaces and
    what draws each one. Faces whose vertices are not all at one y are side
    walls, skirts and slopes; they are skipped because they are not surfaces
    anything stands on.

    Rasterization is ceil(v0) .. ceil(v1) - 1, which is what the block's own
    geometry asks for: a deck quad spanning z 19.0-21.5 covers tile rows 19-21
    while one spanning 0.5-5.0 covers rows 1-4. A half-covered edge tile counts
    on the far side of the quad and not on the near side, so neither the bbox
    rule (too generous by a row) nor the tile-center rule (too mean by one)
    reproduces it.

    `skip_material` is an optional predicate on the material name, for callers
    that need to drop a class of geometry. Material vocabularies are per-game,
    so the predicate belongs to the caller and never to this module.
    """
    result: dict[tuple[int, int], dict[int, set]] = {}
    bmd0 = land_data_bmd0(raw, game)
    if bmd0:
        for material, faces in _all_material_mesh_faces(bmd0).items():
            if skip_material is not None and skip_material(material):
                continue
            for face in faces:
                ys = [v[1] for v in face]
                if min(ys) != max(ys):
                    continue
                xs = [v[0] for v in face]
                zs = [v[2] for v in face]
                for c in range(math.ceil(min(xs) / 1024 + 16),
                               math.ceil(max(xs) / 1024 + 16)):
                    for r in range(math.ceil(min(zs) / 1024 + 16),
                                   math.ceil(max(zs) / 1024 + 16)):
                        result.setdefault((c, r), {}).setdefault(ys[0], set()).add(material)
    return {pos: {y: frozenset(mats) for y, mats in lv.items()}
            for pos, lv in result.items()}


def land_surface_levels(raw: bytes, game: str,
                        skip_material=None) -> dict[tuple[int, int], list[int]]:
    """{(col, row): [y, ...] ascending} — `land_surface_stacks` without the
    material names."""
    return {pos: sorted(lv) for pos, lv
            in land_surface_stacks(raw, game, skip_material).items()}


def land_center_rule_tiles(raw: bytes, game: str, materials: frozenset[str]) -> set[tuple[int, int]]:
    """
    Tiles covered by the given materials using the tile-center rule: a tile is
    claimed only if its CENTER lies inside a face bbox. The outward-rounding
    bbox rule (used for searock, verified exact there) overextends here:
    canopy rims / bridge skirts bleed sub-half-tile slivers onto neighboring
    tiles (center rule verified vs user's Route 203 tree ground truth — 0
    extra — and vs the Route 210 bridge span — exact, 0 conflicts).
    Coordinates may fall outside [0, 32): faces overhang blocks.
    """
    result: set[tuple[int, int]] = set()
    bmd0 = land_data_bmd0(raw, game)
    if bmd0:
        for material, faces in _all_material_mesh_faces(bmd0).items():
            if material not in materials:
                continue
            for face in faces:
                xs = [v[0] for v in face]
                zs = [v[2] for v in face]
                c0 = min(xs) / 1024 + 16
                c1 = max(xs) / 1024 + 16
                r0 = min(zs) / 1024 + 16
                r1 = max(zs) / 1024 + 16
                for c in range(math.floor(c0), math.ceil(c1) + 1):
                    for r in range(math.floor(r0), math.ceil(r1) + 1):
                        if c0 <= c + 0.5 < c1 and r0 <= r + 0.5 < r1:
                            result.add((c, r))
    return result


# ---------------------------------------------------------------------------
# BDHC height-map parsing
# ---------------------------------------------------------------------------

def _parse_bdhc_stair_tiles(raw: bytes, game: str) -> set[tuple[int, int]]:
    """
    Parse the BDHC section of a land-data block binary and return local
    (col, row) tile positions that are part of a tilted (stair) plate.

    BDHC binary layout (sequential after header):
      4B magic "BDHC", then 6×u16 counts, then:
      points (BDHCPoint: fx32 x, fx32 z = 8B each)
      normals (VecFx32: fx32 x,y,z = 12B each)
      constants (fx32 = 4B each)
      plates (BDHCPlate: u16×4 = 8B each)

    BDHC coordinates are in units of TILE_SIZE_FX32 (= 16 × FX32_ONE = 65536)
    relative to the block's rendering center (which is at tile +16 from the
    block's top-left corner). So block_local_col = round(px / TILE_FX32) + 16.

    For an E-W tilted plate (abs(nx) >= abs(nz)):
      stair cols = range(col_lo+1, col_hi+1)   — shift both bounds +1, excludes cliff edge
      stair rows = range(row_lo, row_hi)         — half-open on the non-slope axis
    For a N-S tilted plate:
      stair cols = range(col_lo, col_hi)
      stair rows = range(row_lo+1, row_hi+1)
    Confirmed against Route 203 stairs (user-verified positions).
    """
    sec = land_sections(raw, game)
    if sec is None:
        return set()
    bdhc_start, bdhc_size = sec["bdhc"]
    if bdhc_size < 16:
        return set()
    data = raw[bdhc_start : bdhc_start + bdhc_size]
    if data[:4] != b"BDHC":
        return set()

    off = 4
    n_pts, n_nrm, n_con, n_plt, _n_str, _n_acc = struct.unpack_from("<6H", data, off)
    off += 12

    TILE_FX32 = 16 * 4096

    points: list[tuple[int, int]] = []
    for _ in range(n_pts):
        x, z = struct.unpack_from("<2i", data, off); off += 8
        points.append((x, z))

    normals: list[tuple[int, int, int]] = []
    for _ in range(n_nrm):
        nx, ny, nz = struct.unpack_from("<3i", data, off); off += 12
        normals.append((nx, ny, nz))

    off += 4 * n_con  # skip constants

    stair_tiles: set[tuple[int, int]] = set()
    for _ in range(n_plt):
        p1_idx, p2_idx, ni, _ci = struct.unpack_from("<4H", data, off); off += 8
        nx, _ny, nz = normals[ni]
        if nx == 0 and nz == 0:
            continue  # flat horizontal plate — not a stair

        px1, pz1 = points[p1_idx]
        px2, pz2 = points[p2_idx]

        tc1 = round(px1 / TILE_FX32) + 16
        tr1 = round(pz1 / TILE_FX32) + 16
        tc2 = round(px2 / TILE_FX32) + 16
        tr2 = round(pz2 / TILE_FX32) + 16

        col_lo, col_hi = min(tc1, tc2), max(tc1, tc2)
        row_lo, row_hi = min(tr1, tr2), max(tr1, tr2)

        if abs(nx) >= abs(nz):  # primarily E-W slope
            c_lo, c_hi = col_lo, col_hi
            r_lo, r_hi = row_lo, row_hi
        else:                    # primarily N-S slope
            c_lo, c_hi = col_lo, col_hi
            r_lo, r_hi = row_lo, row_hi

        for col in range(c_lo, c_hi):
            for row in range(r_lo, r_hi):
                if 0 <= col < 32 and 0 <= row < 32:
                    stair_tiles.add((col, row))

    return stair_tiles


def _parse_bdhc_flat_plates(raw: bytes, game: str) -> list[tuple[float, float, float, float]]:
    """
    Return flat (horizontal) BDHC plates as (c0, c1, r0, r1) tile rects,
    block-local. Same binary layout as _parse_bdhc_stair_tiles; flat plates
    are the walking surfaces (ground, water, bridge decks at their height).
    Coordinates are floats: plate edges land mid-tile (test tile centers).
    """
    sec = land_sections(raw, game)
    if sec is None:
        return []
    bdhc_start, bdhc_size = sec["bdhc"]
    if bdhc_size < 16:
        return []
    data = raw[bdhc_start : bdhc_start + bdhc_size]
    if data[:4] != b"BDHC":
        return []

    off = 4
    n_pts, n_nrm, n_con, n_plt, _n_str, _n_acc = struct.unpack_from("<6H", data, off)
    off += 12
    TILE_FX32 = 16 * 4096

    points = [struct.unpack_from("<2i", data, off + i * 8) for i in range(n_pts)]
    off += n_pts * 8
    normals = [struct.unpack_from("<3i", data, off + i * 12) for i in range(n_nrm)]
    off += n_nrm * 12
    constants = [struct.unpack_from("<i", data, off + i * 4)[0] for i in range(n_con)]
    off += n_con * 4

    plates: list[tuple[float, float, float, float, float]] = []
    for _ in range(n_plt):
        p1_idx, p2_idx, ni, ci = struct.unpack_from("<4H", data, off); off += 8
        nx, ny, nz = normals[ni]
        if nx != 0 or nz != 0 or ny == 0:
            continue  # tilted plate (stair/slope) — not a walking deck rect
        (px1, pz1), (px2, pz2) = points[p1_idx], points[p2_idx]
        c1, c2 = px1 / TILE_FX32 + 16, px2 / TILE_FX32 + 16
        r1, r2 = pz1 / TILE_FX32 + 16, pz2 / TILE_FX32 + 16
        height = -constants[ci] / ny   # plane nx·x + ny·y + nz·z + d = 0
        plates.append((min(c1, c2), max(c1, c2), min(r1, r2), max(r1, r2), height))
    return plates


# ---------------------------------------------------------------------------
# Standalone prop model (BMD0 file) footprints
# ---------------------------------------------------------------------------

def prop_model_footprint_from_bytes(bmd0: bytes, polygon: int | list[int] | None = None) -> tuple[str, float, float, float, float, float, float] | None:
    """
    Return (internal_name, min_dc, max_dc, min_dr, max_dr, min_dy, max_dy)
    for a standalone prop model BMD0: the footprint extent in tiles relative
    to the placement position, plus the vertical (y) extent.
    world_units = vertex/4096 × up_scale (model header word 7), 16 units/tile.
    If polygon is an int, only that mesh index contributes to the bbox.
    If polygon is a list of ints, only those mesh indices contribute (union bbox).
    Returns None for unparseable models.
    """
    try:
        # Standalone BMD0: MDL0 block offset is a u32 at 0x10
        mdl0 = struct.unpack_from("<I", bmd0, 0x10)[0]
        _, model_offs, model_names = _parse_namelist(bmd0, mdl0 + 8)
        model = mdl0 + model_offs[0]
        words = struct.unpack_from("<12I", bmd0, model)
        mesh_off = words[3]
        up_scale = words[7] / 4096.0
        mesh_nl = model + mesh_off
        count, mesh_data, _ = _parse_namelist(bmd0, mesh_nl)
        xs: list[int] = []
        ys: list[int] = []
        zs: list[int] = []
        if polygon is None:
            indices = range(count)
        elif isinstance(polygon, list):
            indices = [p for p in polygon if 0 <= p < count]
        else:
            indices = [polygon] if 0 <= polygon < count else []
        for mi in indices:
            mesh_struct = mesh_nl + mesh_data[mi]
            _, _, cmds_off, cmds_len = struct.unpack_from("<4I", bmd0, mesh_struct)
            dl = bmd0[mesh_struct + cmds_off : mesh_struct + cmds_off + cmds_len]
            for face in _parse_nds_faces(dl):
                for x, y, z in face:
                    xs.append(x)
                    ys.append(y)
                    zs.append(z)
        if xs:
            scale = up_scale / 4096.0 / 16.0   # vertex fp -> tiles
            name = model_names[0].decode("ascii", "replace")
            return (name, min(xs) * scale, max(xs) * scale,
                    min(zs) * scale, max(zs) * scale,
                    min(ys) * scale, max(ys) * scale)
    except Exception:
        pass
    return None


def prop_model_ground_footprint_from_bytes(bmd0: bytes) -> tuple[float, float, float, float] | None:
    # Not hardcoded — uses actual vertex data filtered to player height (~1.5 tiles)
    # to fix buildings whose roof geometry inflates the bounding box beyond the walls.
    try:
        mdl0 = struct.unpack_from("<I", bmd0, 0x10)[0]
        _, model_offs, _ = _parse_namelist(bmd0, mdl0 + 8)
        model = mdl0 + model_offs[0]
        words = struct.unpack_from("<12I", bmd0, model)
        up_scale = words[7] / 4096.0
        mesh_nl = model + words[3]
        count, mesh_data, _ = _parse_namelist(bmd0, mesh_nl)
        xs: list[int] = []; ys: list[int] = []; zs: list[int] = []
        for mi in range(count):
            ms = mesh_nl + mesh_data[mi]
            _, _, co, cl = struct.unpack_from("<4I", bmd0, ms)
            for face in _parse_nds_faces(bmd0[ms + co: ms + co + cl]):
                for x, y, z in face:
                    xs.append(x); ys.append(y); zs.append(z)
        if not xs:
            return None
        scale = up_scale / 4096.0 / 16.0
        threshold = min(ys) + 1.5 / scale
        gxs = [x for x, y in zip(xs, ys) if y <= threshold]
        gzs = [z for z, y in zip(zs, ys) if y <= threshold]
        if not gxs:
            return None
        return min(gxs) * scale, max(gxs) * scale, min(gzs) * scale, max(gzs) * scale
    except Exception:
        pass
    return None

