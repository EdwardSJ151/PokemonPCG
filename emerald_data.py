import json
import re
import struct
from pathlib import Path

from terrain_helpers import BASE_DIR, _fmt_movement

# ---------------------------------------------------------------------------
# Terrain and map data, read straight from the decomp
#
# These replace tiles_pokeemerald.json (92 MB) and maps_pokeemerald.json, which
# were pure functions of the files below — every field is recomputed here from
# the same bytes. Reading directly also means extraction bugs are fixable at
# render time; extract_terrain_data.py cannot be re-run on this machine.
# ---------------------------------------------------------------------------

_EM_ROOT          = BASE_DIR / "pokeemerald"
LAYOUTS_JSON      = _EM_ROOT / "data/layouts/layouts.json"
TILESETS_DIR      = _EM_ROOT / "data/tilesets"
EM_MAPS_DIR       = _EM_ROOT / "data/maps"
METATILE_LABELS_H = _EM_ROOT / "include/constants/metatile_labels.h"
BEHAVIORS_H       = _EM_ROOT / "include/constants/metatile_behaviors.h"

#: Metatile ids below this come from the layout's primary tileset; at or above
#: it, subtract this and look in the secondary. See [[metatile-encoding]].
PRIMARY_LIMIT = 0x200

_BEHAVIOR_NAMES: dict | None = None
_METATILE_LABELS: dict | None = None
_TILESET_CACHE: dict = {}
_LAYOUT_REGISTRY: dict | None = None
_LAYOUT_TO_MAPS: dict | None = None


def _behavior_names() -> dict:
    """{behavior value: MB_* name} from the implicit-valued enum."""
    global _BEHAVIOR_NAMES
    if _BEHAVIOR_NAMES is not None:
        return _BEHAVIOR_NAMES
    names: dict = {}
    if BEHAVIORS_H.exists():
        enum_re = re.compile(r"^\s*(MB_\w+)")
        idx, in_enum = 0, False
        for line in BEHAVIORS_H.read_text(encoding="utf-8").splitlines():
            if not in_enum:
                if "enum" in line and "{" in line:
                    in_enum, idx = True, 0
                continue
            if "}" in line:
                break
            m = enum_re.match(line)
            if m:
                names[idx] = m.group(1)
                idx += 1
    _BEHAVIOR_NAMES = names
    return names


#: Label prefixes that do not spell their tileset. `RS` marks metatiles carried
#: over from Ruby/Sapphire; the six SecretBase* tilesets share one label set.
_LABEL_PREFIX_ALIASES = {"MossdeepGym": "RSMossdeepGym"}


def _metatile_labels() -> tuple[dict, dict]:
    """
    (primary {id: name}, secondary {(tileset prefix, id): name}).

    Labels are `METATILE_<Tileset>_<Name>`, and ids >= PRIMARY_LIMIT are only
    meaningful *with* their secondary tileset — 0x206 is five different things.
    extract_terrain_data.py keyed on the id alone, so 310 of the 692 defines
    were overwritten and lost; scoping by tileset makes every one addressable
    (verified: zero collisions on either key).
    """
    global _METATILE_LABELS
    if _METATILE_LABELS is not None:
        return _METATILE_LABELS
    primary: dict = {}
    secondary: dict = {}
    if METATILE_LABELS_H.exists():
        define_re = re.compile(
            r"^#define\s+(METATILE_(\w+?)_\w+)\s+(0x[0-9A-Fa-f]+)")
        for line in METATILE_LABELS_H.read_text(encoding="utf-8").splitlines():
            m = define_re.match(line.strip())
            if not m:
                continue
            name, prefix, mid = m.group(1), m.group(2), int(m.group(3), 16)
            if mid < PRIMARY_LIMIT:
                primary[mid] = name
            else:
                secondary[(prefix, mid)] = name
    _METATILE_LABELS = (primary, secondary)
    return _METATILE_LABELS


def _label_lookup(secondary_tileset: str):
    """Return a f(metatile_id) -> label for one layout's tileset pairing."""
    primary, secondary = _metatile_labels()
    prefix = re.sub(r"^gTileset_", "", secondary_tileset)
    keys = [_LABEL_PREFIX_ALIASES.get(prefix, prefix)]
    if prefix.startswith("SecretBase"):
        keys.append("SecretBase")

    def lookup(metatile_id: int):
        if metatile_id < PRIMARY_LIMIT:
            return primary.get(metatile_id)
        for k in keys:
            name = secondary.get((k, metatile_id))
            if name is not None:
                return name
        return None

    return lookup


def _tileset_behaviors(name: str, is_secondary: bool) -> dict:
    """
    {metatile index: {behavior, layer_type}} from one tileset's
    metatile_attributes.bin. The u16 packs behavior in bits 7-0 and layer_type
    in bits 15-12.
    """
    if name in _TILESET_CACHE:
        return _TILESET_CACHE[name]
    # gTileset_BattleFrontier -> battle_frontier
    folder = re.sub(r"^gTileset_", "", name)
    folder = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", folder).lower()
    attr = (TILESETS_DIR / ("secondary" if is_secondary else "primary")
            / folder / "metatile_attributes.bin")
    result: dict = {}
    if attr.exists():
        raw = attr.read_bytes()
        for i in range(len(raw) // 2):
            word = struct.unpack_from("<H", raw, i * 2)[0]
            result[i] = {"behavior": word & 0x00FF,
                         "layer_type": (word & 0xF000) >> 12}
    _TILESET_CACHE[name] = result
    return result


def _layout_to_maps() -> dict:
    """{layout id: [map dir name, ...]} — 518 maps share 441 layouts."""
    global _LAYOUT_TO_MAPS
    if _LAYOUT_TO_MAPS is not None:
        return _LAYOUT_TO_MAPS
    out: dict = {}
    for map_dir in sorted(EM_MAPS_DIR.iterdir()):
        mj = map_dir / "map.json"
        if not map_dir.is_dir() or not mj.exists():
            continue
        lid = json.loads(mj.read_text(encoding="utf-8")).get("layout")
        if lid:
            out.setdefault(lid, []).append(map_dir.name)
    _LAYOUT_TO_MAPS = out
    return out


def layout_registry() -> dict:
    """
    {layout id: metadata} for all 441 layouts — dimensions, tilesets, member
    maps. Deliberately holds no grids, so lookups and `--all` stay cheap; call
    layout_tile_entry() for the one layout being rendered.
    """
    global _LAYOUT_REGISTRY
    if _LAYOUT_REGISTRY is not None:
        return _LAYOUT_REGISTRY
    to_maps = _layout_to_maps()
    reg: dict = {}
    data = json.loads(LAYOUTS_JSON.read_text(encoding="utf-8"))
    for lay in data.get("layouts", []):
        if not lay:
            continue
        lid = lay["id"]
        reg[lid] = {
            "layout_id":         lid,
            "layout_name":       lay["name"],
            "width":             lay["width"],
            "height":            lay["height"],
            "primary_tileset":   lay.get("primary_tileset", ""),
            "secondary_tileset": lay.get("secondary_tileset", ""),
            # blockdata_filepath is relative to the decomp root, not the repo.
            "blockdata":         _EM_ROOT / lay["blockdata_filepath"],
            "maps":              to_maps.get(lid, []),
        }
    _LAYOUT_REGISTRY = reg
    return reg


def layout_registry_entries() -> list:
    """Layout metadata in layouts.json order — the list for `--all`."""
    return list(layout_registry().values())


def layout_tile_entry(layout_id: str) -> dict | None:
    """
    One layout's full tile entry, the shape the renderer used to read out of
    tiles_pokeemerald.json. Each blockdata u16 is
    `metatile_id | collision << 10 | elevation << 12`.
    """
    meta = layout_registry().get(layout_id)
    if meta is None:
        return None
    width, height = meta["width"], meta["height"]
    path = meta["blockdata"]
    raw = path.read_bytes() if path.exists() else b""

    primary   = _tileset_behaviors(meta["primary_tileset"], is_secondary=False)
    secondary = _tileset_behaviors(meta["secondary_tileset"], is_secondary=True)
    label_of  = _label_lookup(meta["secondary_tileset"])
    beh_names = _behavior_names()

    grid = []
    for row in range(height):
        r = []
        for col in range(width):
            off = (row * width + col) * 2
            val = struct.unpack_from("<H", raw, off)[0] if off + 2 <= len(raw) else 0
            metatile_id = val & 0x3FF
            collision   = (val >> 10) & 0x3
            if metatile_id < PRIMARY_LIMIT:
                beh_info = primary.get(metatile_id, {})
            else:
                beh_info = secondary.get(metatile_id - PRIMARY_LIMIT, {})
            behavior = beh_info.get("behavior", 0)
            r.append({
                "metatile_id":    metatile_id,
                "metatile_label": label_of(metatile_id),
                "collision":      collision,
                "elevation":      (val >> 12) & 0xF,
                "passable":       collision == 0,
                "behavior":       behavior,
                "behavior_name":  beh_names.get(behavior, f"MB_{behavior}"),
                "layer_type":     beh_info.get("layer_type", 0),
            })
        grid.append(r)

    entry = {k: v for k, v in meta.items() if k != "blockdata"}
    entry["grid"] = grid
    return entry


def interior_dims() -> dict:
    """
    {map id: (width, height)} for every map, used to bound the building BFS by
    the real size of what a door leads into. Metadata only — no grids read.
    """
    result: dict = {}
    for meta in layout_registry().values():
        dims = (meta["width"], meta["height"])
        for name in meta["maps"]:
            mj = EM_MAPS_DIR / name / "map.json"
            if not mj.exists():
                continue
            mid = json.loads(mj.read_text(encoding="utf-8")).get("id")
            if mid:
                result[mid] = dims
    return result


def map_entries(layout_id: str) -> list:
    """
    Every map placed on this layout, as the renderer's map entries. Emerald is
    layout-driven and merges the events of all of them, so this is a list —
    unlike the Gen 4 games, where a map owns its terrain outright.
    """
    out = []
    for name in layout_registry().get(layout_id, {}).get("maps", []):
        mj = EM_MAPS_DIR / name / "map.json"
        if mj.exists():
            out.append({"map_name": name,
                        **json.loads(mj.read_text(encoding="utf-8"))})
    return out

# ---------------------------------------------------------------------------
# Warp destination resolution

_EM_CONST_TO_DIR: "dict[str, Path] | None" = None
_EM_DEST_WARP_CACHE: "dict[str, list]" = {}
_EM_DYNAMIC_IDS = frozenset({"WARP_ID_DYNAMIC", "WARP_ID_SECRET_BASE"})


def _em_const_to_dir_map() -> "dict[str, Path]":
    global _EM_CONST_TO_DIR
    if _EM_CONST_TO_DIR is not None:
        return _EM_CONST_TO_DIR
    result: dict = {}
    for d in EM_MAPS_DIR.iterdir():
        mj = d / "map.json"
        if not mj.exists():
            continue
        mid = json.loads(mj.read_text(encoding="utf-8")).get("id")
        if mid:
            result[mid] = d
    _EM_CONST_TO_DIR = result
    return result


def em_dest_warp_coord(dest_map: str, warp_id) -> "tuple[int, int] | None":
    """Return (col, row) spawn coord in dest_map for warp_id, or None if dynamic/missing."""
    raw = str(warp_id)
    if not dest_map or raw in _EM_DYNAMIC_IDS:
        return None
    try:
        wid = int(raw)
    except ValueError:
        return None
    if dest_map not in _EM_DEST_WARP_CACHE:
        d = _em_const_to_dir_map().get(dest_map)
        if d is None:
            _EM_DEST_WARP_CACHE[dest_map] = []
        else:
            _EM_DEST_WARP_CACHE[dest_map] = json.loads(
                (d / "map.json").read_text(encoding="utf-8")
            ).get("warp_events", [])
    warps = _EM_DEST_WARP_CACHE[dest_map]
    if 0 <= wid < len(warps):
        w = warps[wid]
        try:
            return int(w["x"]), int(w["y"])
        except (KeyError, TypeError, ValueError):
            return None
    return None


# ---------------------------------------------------------------------------
# String helpers
# ---------------------------------------------------------------------------

_STRING_ESCAPE = {
    "{PLAYER}": "<PLAYER>",
    "{KUN}": "",
    "{UP_ARROW}": "↑",
    "{DOWN_ARROW}": "↓",
    "{LEFT_ARROW}": "←",
    "{RIGHT_ARROW}": "→",
    "\\l": " ",
    "\\p": " / ",
    "\\n": " ",
}


def _clean_string(raw: str) -> str:
    s = raw.strip().strip('"')
    for k, v in _STRING_ESCAPE.items():
        s = s.replace(k, v)
    s = s.rstrip("$").strip()
    return s


# ---------------------------------------------------------------------------
# Script parsing
# ---------------------------------------------------------------------------

def _parse_scripts_file(path: Path) -> tuple[dict, dict]:
    """
    Core script parser. Returns (blocks, text_by_label):
      blocks:        {label: [body_lines]}
      text_by_label: {label: combined_string_text}
    """
    if not path.exists():
        return {}, {}

    raw = path.read_text(encoding="utf-8", errors="replace").splitlines()

    blocks: dict[str, list[str]] = {}
    cur = None
    for line in raw:
        stripped = line.strip()
        if not line.startswith("\t") and stripped.endswith(":"):
            cur = stripped.rstrip(":")
            blocks.setdefault(cur, [])
        elif cur is not None:
            blocks[cur].append(stripped)

    text_by_label: dict[str, str] = {}
    for label, body in blocks.items():
        parts = []
        for bl in body:
            m = re.match(r'\.string\s+"(.*)"', bl)
            if m:
                parts.append(m.group(1))
        if parts:
            combined = " ".join(_clean_string(p) for p in parts if p).strip()
            if combined:
                text_by_label[label] = combined

    return blocks, text_by_label


def _parse_scripts_inc(path: Path) -> dict[str, str]:
    """
    Parse a scripts.inc file and return {function_label: resolved_text}.
    Follows call_if_eq / goto chains up to 2 levels deep.
    """
    blocks, text_by_label = _parse_scripts_file(path)

    def _resolve(func_label: str, depth: int = 0) -> str:
        if depth > 2 or func_label not in blocks:
            return ""
        body = blocks[func_label]
        texts = []
        for bl in body:
            m = re.match(r"msgbox\s+(\w+)\s*,\s*MSGBOX_(SIGN|DEFAULT|NPC)", bl)
            if m:
                tl = m.group(1)
                if tl in text_by_label:
                    texts.append(text_by_label[tl])
            m2 = re.match(r"(?:call_if_eq|call_if_ne|call_if_set|call_if_unset|goto|call)\s+(.+)", bl)
            if m2:
                args = [a.strip() for a in m2.group(1).split(",")]
                sub = args[-1]
                if re.match(r"[A-Za-z_]\w+", sub):
                    sub_text = _resolve(sub, depth + 1)
                    if sub_text and sub_text not in texts:
                        texts.append(sub_text)
        return " / ".join(t for t in texts if t)

    script_to_text: dict[str, str] = {}
    for label in blocks:
        text = _resolve(label)
        if text:
            script_to_text[label] = text

    return script_to_text


_GLOBAL_BLOCKS_CACHE: dict[str, list[str]] | None = None
_GLOBAL_SCRIPT_INDEX_CACHE: dict | None = None


def _global_blocks() -> dict[str, list[str]]:
    """Merged script blocks from all global Emerald .inc files, cached."""
    global _GLOBAL_BLOCKS_CACHE
    if _GLOBAL_BLOCKS_CACHE is not None:
        return _GLOBAL_BLOCKS_CACHE
    merged: dict[str, list[str]] = {}
    sources = [
        _EM_ROOT / "data" / "event_scripts.s",
        *sorted((_EM_ROOT / "data" / "scripts").glob("*.inc")),
        *sorted((_EM_ROOT / "data" / "maps").glob("*/scripts.inc")),
    ]
    for path in sources:
        if not path.exists():
            continue
        blks, _ = _parse_scripts_file(path)
        merged.update(blks)
    _GLOBAL_BLOCKS_CACHE = merged
    return merged


def _global_script_index() -> dict[str, str | dict]:
    """
    Decomp-wide {script_label: text_or_yesno} for Emerald, built once.

    text_or_yesno is either a plain str or {"prompt": str, "yes": str, "no": str}
    for scripts that show a YESNO dialog.

    Covers event_scripts.s, data/scripts/*.inc, and all map scripts.inc so
    that Common_EventScript_* signs, Roulette table signs, and cross-map
    script references (Sealed Chamber braille, cave entrance signs on shared
    layouts) can all be resolved.
    """
    global _GLOBAL_SCRIPT_INDEX_CACHE
    if _GLOBAL_SCRIPT_INDEX_CACHE is not None:
        return _GLOBAL_SCRIPT_INDEX_CACHE

    merged_blocks: dict[str, list[str]] = {}
    merged_texts: dict[str, str] = {}

    sources = [
        _EM_ROOT / "data" / "event_scripts.s",
        *sorted((_EM_ROOT / "data" / "text").glob("*.inc")),
        *sorted((_EM_ROOT / "data" / "scripts").glob("*.inc")),
        *sorted((_EM_ROOT / "data" / "maps").glob("*/scripts.inc")),
    ]
    for path in sources:
        if not path.exists():
            continue
        blocks, texts = _parse_scripts_file(path)
        merged_blocks.update(blocks)
        merged_texts.update(texts)

    def _get_text(label: str) -> str:
        return merged_texts.get(label, "")

    def _first_text_from(func_label: str, _d: int = 0) -> str:
        """Return the first message text reachable from func_label (for YESNO branches).

        Uses its own depth counter (max 3) independent of the outer _resolve depth.
        """
        if _d > 3 or func_label not in merged_blocks:
            return ""
        body = merged_blocks[func_label]
        for bl in body:
            m = re.match(r"msgbox\s+(\w+)\s*,\s*MSGBOX_(?!YESNO)\w+", bl)
            if m:
                t = _get_text(m.group(1))
                if t:
                    return t
            m = re.match(r"message\s+(\w+)", bl)
            if m:
                t = _get_text(m.group(1))
                if t:
                    return t
            m2 = re.match(
                r"(?:call_if_eq|call_if_ne|call_if_set|call_if_unset|call|goto)\s+(.+)", bl)
            if m2:
                args = [a.strip() for a in m2.group(1).split(",")]
                sub = args[-1]
                if (re.match(r"[A-Za-z_]\w+", sub)
                        and not sub.startswith(("VAR_", "FLAG_", "YES", "NO"))):
                    t = _first_text_from(sub, _d + 1)
                    if t:
                        return t
        return ""

    def _resolve(func_label: str, depth: int = 0) -> str | dict:
        if depth > 2 or func_label not in merged_blocks:
            return ""
        body = merged_blocks[func_label]

        # First pass: detect MSGBOX_YESNO
        yesno_prompt: str | None = None
        yes_label: str | None = None
        no_label:  str | None = None
        for bl in body:
            m = re.match(r"msgbox\s+(\w+)\s*,\s*MSGBOX_YESNO", bl)
            if m and yesno_prompt is None:
                yesno_prompt = _get_text(m.group(1))
            m2 = re.match(r"goto_if_eq\s+VAR_RESULT\s*,\s*YES\s*,\s*(\w+)", bl)
            if m2 and yes_label is None:
                yes_label = m2.group(1)
            m3 = re.match(r"goto_if_eq\s+VAR_RESULT\s*,\s*NO\s*,\s*(\w+)", bl)
            if m3 and no_label is None:
                no_label = m3.group(1)

        if yesno_prompt is not None:
            yes_text = _first_text_from(yes_label) if yes_label else ""
            if no_label:
                no_text = _first_text_from(no_label)
            else:
                # NO falls through inline — first message after the YESNO block
                no_text = ""
                past_yesno = False
                for bl in body:
                    if re.match(r"msgbox\s+\w+\s*,\s*MSGBOX_YESNO", bl):
                        past_yesno = True
                        continue
                    if past_yesno:
                        m = re.match(r"message\s+(\w+)", bl)
                        if m:
                            no_text = _get_text(m.group(1))
                            break
                        m = re.match(r"msgbox\s+(\w+)\s*,\s*MSGBOX_(?!YESNO)\w+", bl)
                        if m:
                            no_text = _get_text(m.group(1))
                            break
            return {"prompt": yesno_prompt, "yes": yes_text, "no": no_text}

        # Second pass: regular text (msgbox, message, call/goto chains)
        texts: list[str] = []
        found_yesno_dict: dict | None = None
        for bl in body:
            m = re.match(r"msgbox\s+(\w+)\s*,\s*MSGBOX_(?!YESNO)\w+", bl)
            if m:
                t = _get_text(m.group(1))
                if t and t not in texts:
                    texts.append(t)
            m = re.match(r"message\s+(\w+)", bl)
            if m:
                t = _get_text(m.group(1))
                if t and t not in texts:
                    texts.append(t)
            m2 = re.match(
                r"(?:call_if_eq|call_if_ne|call_if_set|call_if_unset|goto|call)\s+(.+)", bl)
            if m2:
                args = [a.strip() for a in m2.group(1).split(",")]
                sub = args[-1]
                if (re.match(r"[A-Za-z_]\w+", sub)
                        and not sub.startswith(("VAR_", "FLAG_"))):
                    sub_result = _resolve(sub, depth + 1)
                    if isinstance(sub_result, dict) and found_yesno_dict is None:
                        found_yesno_dict = sub_result
                    elif isinstance(sub_result, str) and sub_result and sub_result not in texts:
                        texts.append(sub_result)
        # Propagate YESNO dict when no plain text was found (wrapper call pattern)
        if not texts and found_yesno_dict is not None:
            return found_yesno_dict
        return " / ".join(t for t in texts if t)

    result: dict[str, str | dict] = {}
    for label in merged_blocks:
        text = _resolve(label)
        if text:
            result[label] = text

    _GLOBAL_SCRIPT_INDEX_CACHE = result
    return result


def _parse_item_commands(path: Path) -> dict[str, str]:
    """Extract {script_label: item_name} from finditem/giveitem in a script file."""
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    current = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not line.startswith("\t") and s.endswith(":"):
            current = s.rstrip(":")
        elif current:
            m = re.match(r"(?:finditem|giveitem)\s+(ITEM_\w+)", s)
            if m:
                result[current] = _em_item_display(m.group(1))
    return result


# ---------------------------------------------------------------------------
# Trainer data loaders (cached)
# ---------------------------------------------------------------------------

_TRAINER_MONEY_TABLE_CACHE: dict | None = None
_TRAINERS_H_CACHE: dict | None = None
_TRAINER_PARTIES_CACHE: dict | None = None
_POKEMON_DATA_CACHE: dict | None = None
_GLOBAL_TEXT_CACHE: dict | None = None
_REMATCH_TABLE_CACHE: dict | None = None

_REMATCH_LINE_RE = re.compile(
    r"REMATCH\((\w+)\s*,\s*(\w+)\s*,\s*(\w+)\s*,\s*(\w+)\s*,\s*(\w+)\s*,\s*\w+\)"
)

_TRAINER_BLOCK_RE = re.compile(
    r"\[(\w+)\]\s*=\s*\n\s*\{((?:[^{}]|\{[^{}]*\})*)\},",
    re.MULTILINE,
)


def _load_global_text_labels() -> dict[str, str]:
    """Load all .string blocks from pokeemerald/data/text/*.inc into {label: text}."""
    global _GLOBAL_TEXT_CACHE
    if _GLOBAL_TEXT_CACHE is not None:
        return _GLOBAL_TEXT_CACHE
    text_dir = BASE_DIR / "pokeemerald" / "data" / "text"
    merged: dict[str, str] = {}
    if text_dir.exists():
        for inc_path in sorted(text_dir.glob("*.inc")):
            _, text_by_label = _parse_scripts_file(inc_path)
            merged.update(text_by_label)
    _GLOBAL_TEXT_CACHE = merged
    return merged


def _load_trainer_money_table() -> dict[str, int]:
    global _TRAINER_MONEY_TABLE_CACHE
    if _TRAINER_MONEY_TABLE_CACHE is not None:
        return _TRAINER_MONEY_TABLE_CACHE
    path = BASE_DIR / "pokeemerald/src/battle_main.c"
    table: dict[str, int] = {}
    if path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"gTrainerMoneyTable\[\]\s*=\s*\{(.+?)\};", text, re.DOTALL)
        if m:
            for em in re.finditer(r"\{(\w+),\s*(\d+)\}", m.group(1)):
                cls, val = em.group(1), int(em.group(2))
                table["DEFAULT" if cls == "0xFF" else cls] = val
    _TRAINER_MONEY_TABLE_CACHE = table
    return table


def _load_rematch_table() -> dict[str, list[str]]:
    """Parse gRematchTable from battle_setup.c → {base_const: [rematch_1..4]}."""
    global _REMATCH_TABLE_CACHE
    if _REMATCH_TABLE_CACHE is not None:
        return _REMATCH_TABLE_CACHE
    path = BASE_DIR / "pokeemerald/src/battle_setup.c"
    result: dict[str, list[str]] = {}
    if path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in _REMATCH_LINE_RE.finditer(text):
            base = m.group(1)
            rematches = [m.group(2), m.group(3), m.group(4), m.group(5)]
            result[base] = rematches
    _REMATCH_TABLE_CACHE = result
    return result


def _load_trainers_h() -> dict[str, dict]:
    global _TRAINERS_H_CACHE
    if _TRAINERS_H_CACHE is not None:
        return _TRAINERS_H_CACHE
    path = BASE_DIR / "pokeemerald/src/data/trainers.h"
    result: dict[str, dict] = {}
    if path.exists():
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in _TRAINER_BLOCK_RE.finditer(text):
            const = m.group(1)
            body  = m.group(2)
            entry: dict = {}
            mc = re.search(r"\.trainerClass\s*=\s*(\w+)", body)
            if mc:
                entry["class"] = mc.group(1)
            mn = re.search(r'\.trainerName\s*=\s*_\("([^"]+)"\)', body)
            if mn:
                entry["name"] = mn.group(1)
            md = re.search(r"\.doubleBattle\s*=\s*(\w+)", body)
            if md:
                entry["double"] = md.group(1).rstrip(",") == "TRUE"
            mp = re.search(
                r"\.party\s*=\s*(NO_ITEM_DEFAULT_MOVES|NO_ITEM_CUSTOM_MOVES"
                r"|ITEM_DEFAULT_MOVES|ITEM_CUSTOM_MOVES)\((\w+)\)",
                body,
            )
            if mp:
                entry["party_type"]  = mp.group(1)
                entry["party_array"] = mp.group(2)
            mi = re.search(r"\.items\s*=\s*\{([^}]*)\}", body)
            if mi:
                raw = mi.group(1).strip()
                entry["items"] = [
                    x.strip().replace("ITEM_", "").replace("_", " ").title()
                    for x in raw.split(",")
                    if x.strip() not in ("", "ITEM_NONE")
                ]
            mai = re.search(r"\.aiFlags\s*=\s*([^,\n]+)", body)
            if mai:
                _em_ai_drop = {"AI_SCRIPT_ROAMING", "AI_SCRIPT_SAFARI", "AI_SCRIPT_FIRST_BATTLE"}
                _em_ai_rename = {
                    "CHECK_BAD_MOVE":        "BASIC",
                    "TRY_TO_FAINT":          "EVAL_ATTACK",
                    "CHECK_VIABILITY":       "EXPERT",
                    "PREFER_POWER_EXTREMES": "PRIORITIZE_EXTREMES",
                    "PREFER_BATON_PASS":     "BATON_PASS",
                    "DOUBLE_BATTLE":         "TAG_STRATEGY",
                    "HP_AWARE":              "CHECK_HP",
                    "TRY_SUNNY_DAY_START":   "WEATHER",
                }
                flags = []
                for f in re.findall(r"AI_SCRIPT_\w+", mai.group(1)):
                    if f in _em_ai_drop:
                        continue
                    name = f.replace("AI_SCRIPT_", "")
                    flags.append(_em_ai_rename.get(name, name))
                entry["ai_flags"] = flags
            result[const] = entry
    _TRAINERS_H_CACHE = result
    return result


def _load_trainer_parties_h() -> dict[str, list]:
    global _TRAINER_PARTIES_CACHE
    if _TRAINER_PARTIES_CACHE is not None:
        return _TRAINER_PARTIES_CACHE
    path = BASE_DIR / "pokeemerald/src/data/trainer_parties.h"
    result: dict[str, list] = {}
    if not path.exists():
        _TRAINER_PARTIES_CACHE = result
        return result

    text = path.read_text(encoding="utf-8", errors="replace")

    for hdr_m in re.finditer(
        r"static const struct (TrainerMon\w+)\s+(\w+)\[\]\s*=\s*\{", text
    ):
        struct_type = hdr_m.group(1)
        array_name  = hdr_m.group(2)
        has_custom  = "CustomMoves" in struct_type
        has_item    = "Item" in struct_type and "NoItem" not in struct_type

        start = hdr_m.end()
        depth = 1
        pos   = start
        while pos < len(text) and depth > 0:
            if text[pos] == "{":
                depth += 1
            elif text[pos] == "}":
                depth -= 1
            pos += 1
        array_body = text[start : pos - 1]

        mons: list[dict] = []
        mon_pos = 0
        while True:
            bstart = array_body.find("{", mon_pos)
            if bstart == -1:
                break
            d = 1
            p = bstart + 1
            while p < len(array_body) and d > 0:
                if array_body[p] == "{":
                    d += 1
                elif array_body[p] == "}":
                    d -= 1
                p += 1
            mon_text = array_body[bstart + 1 : p - 1]
            mon_pos  = p

            mon: dict = {}
            iv_m = re.search(r"\.iv\s*=\s*(\d+)", mon_text)
            if iv_m:
                mon["iv"] = int(iv_m.group(1))
            lvl_m = re.search(r"\.lvl\s*=\s*(\d+)", mon_text)
            if lvl_m:
                mon["lvl"] = int(lvl_m.group(1))
            sp_m = re.search(r"\.species\s*=\s*SPECIES_(\w+)", mon_text)
            if sp_m:
                mon["species"] = sp_m.group(1)
            if has_item:
                item_m = re.search(r"\.heldItem\s*=\s*ITEM_(\w+)", mon_text)
                if item_m:
                    mon["item"] = item_m.group(1).replace("_", " ").title()
            if has_custom:
                moves_m = re.search(r"\.moves\s*=\s*\{([^}]+)\}", mon_text)
                if moves_m:
                    raw_moves = [x.strip() for x in moves_m.group(1).split(",")]
                    mon["moves"] = [
                        x.replace("MOVE_", "").replace("_", " ").title()
                        for x in raw_moves
                        if x and x != "MOVE_NONE"
                    ]
            if mon:
                mons.append(mon)

        result[array_name] = mons

    _TRAINER_PARTIES_CACHE = result
    return result


_ABILITY_NAMES_H  = _EM_ROOT / "src/data/text/abilities.h"
_SPECIES_INFO_H   = _EM_ROOT / "src/data/pokemon/species_info.h"
_LEARNSET_PTRS_H  = _EM_ROOT / "src/data/pokemon/level_up_learnset_pointers.h"
_LEARNSETS_H      = _EM_ROOT / "src/data/pokemon/level_up_learnsets.h"

_ABILITIES_RE   = re.compile(r"\.abilities\s*=\s*\{([^}]*)\}")
_ABILITY_NAME_RE = re.compile(r"\[(ABILITY_\w+)\]\s*=\s*_\(\"([^\"]*)\"\)")
_LEARN_PTR_RE   = re.compile(r"\[SPECIES_(\w+)\]\s*=\s*(\w+)\s*,")
_LEARNSET_RE    = re.compile(
    r"static\s+const\s+u16\s+(\w+)\[\]\s*=\s*\{(.*?)\};", re.DOTALL)
_LEVEL_UP_RE    = re.compile(r"LEVEL_UP_MOVE\(\s*(\d+)\s*,\s*MOVE_(\w+)\s*\)")


def _load_pokemon_data() -> dict:
    """
    {species name: {abilities, learnset}} — the two fields the trainer renderer
    actually consumes, parsed from the decomp instead of pokemon_emerald.json.

    Keys and value formats reproduce the extractor exactly: species and move
    names keep their underscores, and ABILITY_NONE is dropped rather than kept
    as an empty slot. Abilities use the in-game **display names** from
    abilities.h, not the constants — Gen 3 spells four of them without the
    space the constant has (ABILITY_COMPOUND_EYES is "COMPOUNDEYES").
    """
    global _POKEMON_DATA_CACHE
    if _POKEMON_DATA_CACHE is not None:
        return _POKEMON_DATA_CACHE

    # Learnset arrays, then the table naming one per species.
    arrays: dict = {}
    if _LEARNSETS_H.exists():
        text = _LEARNSETS_H.read_text(encoding="utf-8", errors="replace")
        for sym, body in _LEARNSET_RE.findall(text):
            arrays[sym] = [{"level": int(lv), "move": mv}
                           for lv, mv in _LEVEL_UP_RE.findall(body)]

    learnsets: dict = {}
    if _LEARNSET_PTRS_H.exists():
        text = _LEARNSET_PTRS_H.read_text(encoding="utf-8", errors="replace")
        for species, sym in _LEARN_PTR_RE.findall(text):
            learnsets[species] = arrays.get(sym, [])

    ability_names: dict = {}
    if _ABILITY_NAMES_H.exists():
        ability_names = dict(_ABILITY_NAME_RE.findall(
            _ABILITY_NAMES_H.read_text(encoding="utf-8", errors="replace")))

    # species_info.h nests braces, so split on the entry marker rather than
    # trying to match a balanced block.
    result: dict = {}
    if _SPECIES_INFO_H.exists():
        text = _SPECIES_INFO_H.read_text(encoding="utf-8", errors="replace")
        for chunk in text.split("[SPECIES_")[1:]:
            species = chunk.split("]", 1)[0].strip()
            if species == "NONE":
                continue
            m = _ABILITIES_RE.search(chunk)
            abilities = []
            if m:
                for tok in m.group(1).split(","):
                    tok = tok.strip()
                    if tok.startswith("ABILITY_") and tok != "ABILITY_NONE":
                        abilities.append(ability_names.get(
                            tok, tok[len("ABILITY_"):].replace("_", " ")))
            result[species] = {"abilities": abilities,
                               "learnset":  learnsets.get(species, [])}

    _POKEMON_DATA_CACHE = result
    return result


def _get_default_moves(species: str, level: int, pokemon_data: dict) -> list[str]:
    """4 most recent level-up moves for species at or below level."""
    entry = pokemon_data.get(species, {})
    learnable = [
        (e["level"], e["move"])
        for e in entry.get("learnset", [])
        if isinstance(e.get("level"), int) and e["level"] <= level
    ]
    learnable.sort(key=lambda x: x[0])
    return [mv for _, mv in learnable[-4:]]


# ---------------------------------------------------------------------------
# Script data extraction helpers
# ---------------------------------------------------------------------------

def _extract_berry_name(berry_id: str) -> str:
    """BERRY_TREE_ROUTE_119_POMEG_1  →  'Pomeg'"""
    parts = berry_id.split("_")
    if parts and parts[-1].isdigit():
        return parts[-2].title() if len(parts) >= 2 else berry_id
    return parts[-1].title() if parts else berry_id


# ---------------------------------------------------------------------------
# Gym leader extraction  (Emerald)
# ---------------------------------------------------------------------------

_EM_GYM_LEADER_GFX: dict[str, str] = {
    "OBJ_EVENT_GFX_ROXANNE":  "Roxanne",
    "OBJ_EVENT_GFX_BRAWLY":   "Brawly",
    "OBJ_EVENT_GFX_WATTSON":  "Wattson",
    "OBJ_EVENT_GFX_FLANNERY": "Flannery",
    "OBJ_EVENT_GFX_NORMAN":   "Norman",
    "OBJ_EVENT_GFX_WINONA":   "Winona",
    "OBJ_EVENT_GFX_LIZA":     "Tate & Liza",
    "OBJ_EVENT_GFX_TATE":     "Tate & Liza",
    "OBJ_EVENT_GFX_WALLACE":  "Wallace",
    "OBJ_EVENT_GFX_JUAN":     "Juan",
    # Elite Four + Champion
    "OBJ_EVENT_GFX_SIDNEY":   "Sidney",
    "OBJ_EVENT_GFX_PHOEBE":   "Phoebe",
    "OBJ_EVENT_GFX_GLACIA":   "Glacia",
    "OBJ_EVENT_GFX_DRAKE":    "Drake",
    "OBJ_EVENT_GFX_STEVEN":   "Steven",
}

_EM_TB_MAIN_RE   = re.compile(r"trainerbattle_(?:single|double|no_intro|single_no_music)\b", re.I)
_EM_TB_REMATCH_RE = re.compile(r"trainerbattle_rematch_double\b", re.I)
# 6-arg form: trainerbattle TRAINER_BATTLE_CONTINUE_SCRIPT/SET_TRAINER_A/B, <trainer_const>, ...
_EM_TB_6ARG_RE   = re.compile(r"trainerbattle\s+TRAINER_BATTLE_\w+,\s*(\w+)", re.I)


def _em_gym_leader_info(
    gfx: str,
    local_blocks: dict,
    text_by_label: dict,
) -> dict | None:
    """
    Scan all local blocks for trainerbattle* lines and return a gym leader dict.
    Handles Norman's switch-dispatch and Tate/Liza's double battle.
    """
    leader_name = _EM_GYM_LEADER_GFX.get(gfx, "")
    if not leader_name:
        return None

    trainer_const: str = ""
    intro_text:    str = ""
    defeat_text:   str = ""
    post_text:     str = ""
    badge_text:    str = ""
    gym_badge:     "str | None" = None
    gives_item:    "dict | None" = None
    is_double:     bool = False

    rematch_const:        str = ""
    pre_rematch_text:     str = ""
    rematch_defeat_text:  str = ""
    post_rematch_text:    str = ""

    def _t(label: str) -> str:
        return text_by_label.get(label, "")

    for block_lines in local_blocks.values():
        found_main   = False
        found_rematch = False
        for line in block_lines:
            if _EM_TB_MAIN_RE.search(line):
                found_main = True
                break
            if _EM_TB_REMATCH_RE.search(line):
                found_rematch = True
                break

        if found_main and not trainer_const:
            # Collect pre-battle context (msgbox before trainerbattle_no_intro)
            pre_tb_texts: list[str] = []
            seen_tb = False
            post_tb_lines: list[str] = []
            defeated_script_label = ""

            for line in block_lines:
                stripped = line.strip()
                if not seen_tb:
                    m_pre = re.match(r"msgbox\s+(\w+)\s*,\s*MSGBOX_DEFAULT", stripped, re.I)
                    if m_pre:
                        t = _t(m_pre.group(1))
                        if t:
                            pre_tb_texts.append(t)
                    if _EM_TB_MAIN_RE.search(stripped):
                        seen_tb = True
                        # Parse the trainerbattle line
                        tb_match = re.match(r"trainerbattle_\w+\s+(.+)", stripped, re.I)
                        if tb_match:
                            args = [a.strip() for a in tb_match.group(1).split(",")]
                            args = [a for a in args if a]
                            variant = stripped.split()[0].lower()
                            if "no_intro" in variant:
                                # trainerbattle_no_intro TRAINER_X, DefeatText
                                trainer_const = args[0] if args else ""
                                defeat_text   = _t(args[1]) if len(args) > 1 else ""
                                # pre_battle_text from preceding msgbox
                                intro_text = pre_tb_texts[-1] if pre_tb_texts else ""
                            elif "double" in variant and "rematch" not in variant:
                                # trainerbattle_double TRAINER_X, Intro, Defeat, NeedTwo[, Defeated[, NO_MUSIC]]
                                is_double     = True
                                trainer_const = args[0] if args else ""
                                intro_text    = _t(args[1]) if len(args) > 1 else ""
                                defeat_text   = _t(args[2]) if len(args) > 2 else ""
                                # defeated_script is last non-NO_MUSIC arg (skip NeedTwo text, ignore NO_MUSIC)
                                data_args = [a for a in args[3:] if a != "NO_MUSIC"]
                                if len(data_args) >= 2:
                                    defeated_script_label = data_args[-1]
                                elif len(data_args) == 1 and not _t(data_args[0]):
                                    defeated_script_label = data_args[0]
                            else:
                                # trainerbattle_single TRAINER_X, Intro, Defeat[, Defeated[, NO_MUSIC]]
                                trainer_const = args[0] if args else ""
                                intro_text    = _t(args[1]) if len(args) > 1 else ""
                                defeat_text   = _t(args[2]) if len(args) > 2 else ""
                                data_args = [a for a in args[3:] if a != "NO_MUSIC"]
                                if data_args:
                                    defeated_script_label = data_args[0]
                else:
                    post_tb_lines.append(stripped)

            # post_battle_text: first MSGBOX_DEFAULT after trainerbattle
            for line in post_tb_lines:
                m2 = re.match(r"msgbox\s+(\w+)\s*,\s*MSGBOX_DEFAULT", line, re.I)
                if m2:
                    t = _t(m2.group(1))
                    if t:
                        post_text = t
                        break
                # For no_intro: inline message after trainerbattle
                m3 = re.match(r"message\s+(\w+)", line, re.I)
                if m3:
                    t = _t(m3.group(1))
                    if t and not badge_text:
                        badge_text = t
                    break

            # Badge text from DefeatedScript block
            if defeated_script_label and defeated_script_label in local_blocks:
                block_lines_d = local_blocks[defeated_script_label]
                for dl in block_lines_d:
                    dm = re.match(r"message\s+(\w+)", dl.strip(), re.I)
                    if dm:
                        t = _t(dm.group(1))
                        if t:
                            badge_text = t
                            break
                    dm2 = re.match(r"msgbox\s+(\w+)\s*,\s*MSGBOX_DEFAULT", dl.strip(), re.I)
                    if dm2:
                        t = _t(dm2.group(1))
                        if t:
                            badge_text = t
                            break

            # Item give: search defeated-script block first, fall back to post_tb_lines
            def _scan_for_giveitem(lines_to_scan: list, depth: int = 0) -> "dict | None":
                for j, dl2 in enumerate(lines_to_scan):
                    s2 = dl2.strip()
                    gi = re.match(r"giveitem\s+(ITEM_\w+)", s2, re.I)
                    if gi:
                        item_id = gi.group(1)
                        flag_val = None
                        for fl in lines_to_scan[j + 1:]:
                            sf = re.match(r"setflag\s+(FLAG_\w+)", fl.strip(), re.I)
                            if sf:
                                flag_val = sf.group(1)
                                break
                        return {"item_id": item_id, "quantity": 1, "flag": flag_val}
                    if depth < 2:
                        g2 = re.match(r"(?:goto|call)\s+(\w+)", s2, re.I)
                        if g2 and g2.group(1) in local_blocks:
                            found = _scan_for_giveitem(local_blocks[g2.group(1)], depth + 1)
                            if found:
                                return found
                return None

            def _scan_for_badge(lines_to_scan: list, depth: int = 0) -> "str | None":
                for dl2 in lines_to_scan:
                    s2 = dl2.strip()
                    bf = re.match(r"setflag\s+(FLAG_BADGE\d+_GET)", s2, re.I)
                    if bf:
                        return bf.group(1)
                    if depth < 2:
                        g2 = re.match(r"(?:goto|call)\s+(\w+)", s2, re.I)
                        if g2 and g2.group(1) in local_blocks:
                            found = _scan_for_badge(local_blocks[g2.group(1)], depth + 1)
                            if found:
                                return found
                return None

            if defeated_script_label and defeated_script_label in local_blocks:
                gives_item = _scan_for_giveitem(local_blocks[defeated_script_label])
                gym_badge  = _scan_for_badge(local_blocks[defeated_script_label])
            if gives_item is None:
                gives_item = _scan_for_giveitem(post_tb_lines)
            if gym_badge is None:
                gym_badge = _scan_for_badge(post_tb_lines)

        elif found_rematch and not rematch_const:
            for line in block_lines:
                stripped = line.strip()
                m = re.match(r"trainerbattle_rematch_double\s+(.+)", stripped, re.I)
                if m:
                    args = [a.strip() for a in m.group(1).split(",")]
                    args = [a for a in args if a]
                    rematch_const       = args[0] if args else ""
                    pre_rematch_text    = _t(args[1]) if len(args) > 1 else ""
                    rematch_defeat_text = _t(args[2]) if len(args) > 2 else ""
                    break
                mr = re.match(r"msgbox\s+(\w+)\s*,\s*MSGBOX_AUTOCLOSE", stripped, re.I)
                if mr:
                    t = _t(mr.group(1))
                    if t:
                        post_rematch_text = t

    if not trainer_const:
        return None

    return {
        "leader_name":        leader_name,
        "trainer_const":      trainer_const,
        "is_double":          is_double,
        "intro_text":         intro_text,
        "defeat_text":        defeat_text,
        "post_text":          post_text,
        "badge_text":         badge_text,
        "gym_badge":          gym_badge,
        "gives_item":         gives_item,
        "rematch_const":      rematch_const,
        "pre_rematch_text":   pre_rematch_text,
        "rematch_defeat_text": rematch_defeat_text,
        "post_rematch_text":  post_rematch_text,
    }


def _extract_trainer_script_data(script_name: str, blocks: dict,
                                  text_by_label: dict) -> dict:
    """Extract trainer_const, intro_text, defeat_text, post_text from a trainer script."""
    if script_name not in blocks:
        return {}
    result: dict = {}
    for bl in blocks[script_name]:
        # 6-arg form: trainerbattle TRAINER_BATTLE_xxx, <const>, <localid>, intro, defeat[, cont]
        m6 = _EM_TB_6ARG_RE.match(bl)
        if m6 and "trainer_const" not in result:
            result["trainer_const"] = m6.group(1)
            # Extract intro/defeat from remaining comma-separated args
            parts = [p.strip() for p in bl.split(",")]
            # parts[0] = "trainerbattle TRAINER_BATTLE_xxx", [1]=const, [2]=localid, [3]=intro, [4]=defeat
            if len(parts) >= 5:
                result["intro_text"]  = text_by_label.get(parts[3], "")
                result["defeat_text"] = text_by_label.get(parts[4], "")
            continue
        m = re.match(
            r"trainerbattle(?:_single|_no_intro|_rematch|_double"
            r"|_single_no_music)?\s+(\w+)(?:,\s*(\w+))?(?:,\s*(\w+))?",
            bl,
        )
        if m and "trainer_const" not in result:
            result["trainer_const"] = m.group(1)
            labels = [l for l in (m.group(2), m.group(3)) if l]
            if len(labels) == 2:
                result["intro_text"]  = text_by_label.get(labels[0], "")
                result["defeat_text"] = text_by_label.get(labels[1], "")
            elif len(labels) == 1:
                result["defeat_text"] = text_by_label.get(labels[0], "")
        m2 = re.match(r"msgbox\s+(\w+),\s*MSGBOX_AUTOCLOSE", bl)
        if m2:
            result["post_text"] = text_by_label.get(m2.group(1), "")
    return result


# ---------------------------------------------------------------------------
# Trainer enrichment (resolves party, abilities, prize into trainer dicts)
# ---------------------------------------------------------------------------

def _enrich_trainers(trainers: list) -> None:
    """Mutate each trainer dict in-place, adding resolved party and display fields."""
    if not trainers:
        return

    trainers_h   = _load_trainers_h()
    parties_h    = _load_trainer_parties_h()
    money_table  = _load_trainer_money_table()
    pokemon_data = _load_pokemon_data()
    rematch_table = _load_rematch_table()

    for t in trainers:
        trainer_const = t.get("trainer_const", "")
        td = trainers_h.get(trainer_const, {}) if trainer_const else {}

        t["tr_name"]      = td.get("name") or trainer_const or t.get("graphics_id", "?")
        t["ai_flags"]     = td.get("ai_flags") or []
        cls               = td.get("class", "")
        t["tr_class"]     = cls
        t["tr_class_disp"] = (
            cls.replace("TRAINER_CLASS_", "").replace("_", " ").title() if cls else "?"
        )
        t["double"] = td.get("double", False)
        t["items"]  = td.get("items", [])

        party_array = td.get("party_array", "")
        raw_party   = parties_h.get(party_array, []) if party_array else []

        enriched_party = []
        for mon in raw_party:
            species = mon.get("species", "?")
            lvl     = mon.get("lvl", "?")

            if "moves" in mon:
                moves = mon["moves"] or ["(none)"]
            elif pokemon_data:
                moves = _get_default_moves(
                    species, int(lvl) if str(lvl).isdigit() else 0, pokemon_data
                )
                if not moves:
                    moves = ["(default — no learnset)"]
            else:
                moves = ["(default — learnset unavailable)"]

            pdata        = pokemon_data.get(species, {})
            abilities    = pdata.get("abilities", [])
            abilities_str = (
                " / ".join(a.replace("_", " ").title() for a in abilities if a) or "?"
            )

            enriched_mon: dict = {
                "species":      species,
                "lvl":          lvl,
                "iv":           mon.get("iv", 0),
                "moves":        moves,
                "abilities_str": abilities_str,
            }
            if mon.get("item"):
                enriched_mon["item"] = mon["item"]
            enriched_party.append(enriched_mon)

        t["party"] = enriched_party

        # VS Seeker rematches: look up base const in gRematchTable
        rematch_consts = rematch_table.get(trainer_const, [])
        vs_rematches: list[dict] = []
        for rc in rematch_consts:
            rtd = trainers_h.get(rc, {})
            r_party_array = rtd.get("party_array", "")
            r_raw = parties_h.get(r_party_array, []) if r_party_array else []
            r_cls = rtd.get("class", cls)
            r_enriched: list[dict] = []
            for mon in r_raw:
                species = mon.get("species", "?")
                lvl     = mon.get("lvl", "?")
                if "moves" in mon:
                    moves = mon["moves"] or ["(none)"]
                elif pokemon_data:
                    moves = _get_default_moves(
                        species, int(lvl) if str(lvl).isdigit() else 0, pokemon_data
                    )
                    if not moves:
                        moves = ["(default — no learnset)"]
                else:
                    moves = ["(default — learnset unavailable)"]
                pdata = pokemon_data.get(species, {})
                abilities = pdata.get("abilities", [])
                abilities_str = (
                    " / ".join(a.replace("_", " ").title() for a in abilities if a) or "?"
                )
                enriched_mon = {
                    "species": species, "lvl": lvl, "iv": mon.get("iv", 0),
                    "moves": moves, "abilities_str": abilities_str,
                }
                if mon.get("item"):
                    enriched_mon["item"] = mon["item"]
                r_enriched.append(enriched_mon)
            r_prize = None
            r_prize_detail = ""
            if r_enriched and r_cls:
                r_last_lvl = r_enriched[-1].get("lvl", 1)
                r_mult = money_table.get(r_cls, money_table.get("DEFAULT", 5))
                r_prize = 4 * int(r_last_lvl) * r_mult
                r_prize_detail = f"4 × {r_last_lvl} × {r_mult}"
            vs_rematches.append({"party": r_enriched, "prize": r_prize,
                                  "prize_detail": r_prize_detail})
        t["vs_rematches"] = vs_rematches

        if enriched_party and cls:
            last_lvl        = enriched_party[-1].get("lvl", 1)
            mult            = money_table.get(cls, money_table.get("DEFAULT", 5))
            prize           = 4 * int(last_lvl) * mult
            t["prize"]        = prize
            t["prize_detail"] = f"4 × {last_lvl} × {mult}"
        else:
            t["prize"]        = None
            t["prize_detail"] = ""


# ---------------------------------------------------------------------------
# Public loaders
# ---------------------------------------------------------------------------

def load_signs_from_decomp(tile_entry: dict, game: str) -> list[dict]:
    """
    Read bg_events[type=sign] from each map's map.json and resolve text.
    Emerald reads map.json; HeartGold delegates to its own event data.
    """
    if game == "heartgold":
        from heartgold_data import zone_signs, zone_sign_dialogue
        col_off = tile_entry.get("_tile_col_min", 0)
        row_off = tile_entry.get("_tile_row_min", 0)
        code    = tile_entry.get("internal_code", "")
        return [{"x": s["x"] - col_off, "y": s["z"] - row_off,
                 "text": zone_sign_dialogue(code, s) or ""}
                for s in zone_signs(code)]
    if game != "emerald":
        return []

    maps_dir = BASE_DIR / "pokeemerald" / "data" / "maps"
    if not maps_dir.exists():
        return []

    map_names = tile_entry.get("maps", [])
    results = []

    for map_name in map_names:
        map_dir       = maps_dir / map_name
        map_json_path = map_dir / "map.json"
        scripts_path  = map_dir / "scripts.inc"

        if not map_json_path.exists():
            continue

        try:
            with open(map_json_path, encoding="utf-8") as f:
                map_data = json.load(f)
        except Exception:
            continue

        script_texts = _parse_scripts_inc(scripts_path)

        for evt in map_data.get("bg_events", []):
            if evt.get("type") != "sign":
                continue
            script_name = evt.get("script", "")
            text = script_texts.get(script_name, "")
            if not text:
                for s in script_texts:
                    if s.endswith("_" + script_name.split("_")[-1]):
                        text = script_texts[s]
                        break
            if not text:
                global_text = _global_script_index().get(script_name, "")
                text = f"<{script_name}>" + (f" -> {global_text}" if global_text else "")
            is_vending = bool(re.search(r"VendingMachine", script_name, re.I))
            results.append({
                "x": evt.get("x", 0),
                "y": evt.get("y", 0),
                "map_name": map_name,
                "script": script_name,
                "text": text,
                "is_vending": is_vending,
                "exchange_pairs": (
                    [("Money", "Fresh Water"), ("Money", "Soda Pop"), ("Money", "Lemonade")]
                    if is_vending else []
                ),
            })

    return results


def _item_name(constant: str) -> str:
    """ITEM_POKE_BALL → 'Poke Ball'"""
    return constant.removeprefix("ITEM_").replace("_", " ").title()


_EM_TMS_HMS_H = _EM_ROOT / "include/constants/tms_hms.h"
_EM_TMHM_NUM_CACHE: dict[str, str] = {}   # "ITEM_TM_FOCUS_PUNCH" → "TM01", "ITEM_HM_CUT" → "HM01"
_EM_TMHM_NUM_LOADED = False


def _em_load_tmhm_numbers() -> None:
    global _EM_TMHM_NUM_LOADED
    if _EM_TMHM_NUM_LOADED:
        return
    _EM_TMHM_NUM_LOADED = True
    if not _EM_TMS_HMS_H.exists():
        return
    text = _EM_TMS_HMS_H.read_text(encoding="utf-8")
    tm_i = 1
    for m in re.finditer(r"FOREACH_TM\(F\)(.*?)(?:#define FOREACH_HM|\Z)", text, re.DOTALL):
        for name in re.findall(r"F\((\w+)\)", m.group(1)):
            _EM_TMHM_NUM_CACHE[f"ITEM_TM_{name}"] = f"TM{tm_i:02d}"
            tm_i += 1
    hm_i = 1
    for m in re.finditer(r"FOREACH_HM\(F\)(.*?)(?:#define FOREACH_TMHM|\Z)", text, re.DOTALL):
        for name in re.findall(r"F\((\w+)\)", m.group(1)):
            _EM_TMHM_NUM_CACHE[f"ITEM_HM_{name}"] = f"HM{hm_i:02d}"
            hm_i += 1


def _em_item_display(constant: str) -> str:
    """ITEM_TM_FOCUS_PUNCH → 'TM01 (Focus Punch)', ITEM_HM_CUT → 'HM01 (Cut)', else _item_name."""
    if constant.startswith("ITEM_TM_"):
        _em_load_tmhm_numbers()
        num  = _EM_TMHM_NUM_CACHE.get(constant, "TM")
        move = constant.removeprefix("ITEM_TM_").replace("_", " ").title()
        return f"{num} ({move})"
    if constant.startswith("ITEM_HM_"):
        _em_load_tmhm_numbers()
        num  = _EM_TMHM_NUM_CACHE.get(constant, "HM")
        move = constant.removeprefix("ITEM_HM_").replace("_", " ").title()
        return f"{num} ({move})"
    return _item_name(constant)


def _species_name(constant: str) -> str:
    """SPECIES_BULBASAUR → 'Bulbasaur'"""
    return constant.removeprefix("SPECIES_").replace("_", " ").title()


def _pretty_move(camel: str) -> str:
    """'SwordsDance' → 'Swords Dance'"""
    return re.sub(r"([a-z])([A-Z])", r"\1 \2", camel)


_EM_TRADE_TABLE_CACHE: dict | None = None


def _em_trade_table() -> dict[str, dict]:
    """Parse src/data/trade.h → {INGAME_TRADE_X: {gives, receives, nickname}}"""
    global _EM_TRADE_TABLE_CACHE
    if _EM_TRADE_TABLE_CACHE is not None:
        return _EM_TRADE_TABLE_CACHE
    path = _EM_ROOT / "src/data/trade.h"
    result: dict[str, dict] = {}
    if path.exists():
        text = path.read_text(encoding="utf-8")
        # Each entry: [INGAME_TRADE_X] = { ... }; fields may contain nested {}
        for block_m in re.finditer(r'\[(\w+)\]\s*=\n?\s*\{', text):
            key = block_m.group(1)
            start = block_m.end()
            depth, i = 1, start
            while i < len(text) and depth:
                if text[i] == '{':
                    depth += 1
                elif text[i] == '}':
                    depth -= 1
                i += 1
            block = text[start:i - 1]
            nick_m    = re.search(r'\.nickname\s*=\s*_\("([^"]+)"\)', block)
            sp_m      = re.search(r'\.species\s*=\s*(SPECIES_\w+)', block)
            req_m     = re.search(r'\.requestedSpecies\s*=\s*(SPECIES_\w+)', block)
            if nick_m and sp_m and req_m:
                result[key] = {
                    "gives":    _species_name(req_m.group(1)),
                    "receives": _species_name(sp_m.group(1)),
                    "nickname": nick_m.group(1),
                }
    _EM_TRADE_TABLE_CACHE = result
    return result


def _em_detect_service_flags(scr: str, blocks: dict) -> set[str]:
    """Scan an NPC script body (+ full goto chain) for service-indicator commands."""
    flags: set[str] = set()
    seen: set[str] = {scr}
    queue = [scr]
    all_body_parts: list[str] = []
    while queue:
        label = queue.pop(0)
        body = "\n".join(blocks.get(label, []))
        all_body_parts.append(body)
        if re.search(r"\bpokemart\b", body, re.I):
            flags.add("vendor")
        if re.search(r"\bpokemartdecoration\b", body, re.I):
            flags.add("decor_vendor")
        if re.search(r"special DisplayBerryPowderVendorMenu|specialvar\s+\w+,\s*HasEnoughBerryPowder", body):
            flags.add("berry_powder_vendor")
        if re.search(r"checkitem ITEM_(?:ROOT|CLAW)_FOSSIL", body, re.I):
            flags.add("vendor")
        if re.search(r"special DoInGameTradeScene", body):
            flags.add("trader")
        if re.search(r"special MoveDeleterChooseMoveToForget", body):
            flags.add("move_deleter")
        if re.search(r"checkitem ITEM_HEART_SCALE", body, re.I):
            flags.add("move_reminder")
        if re.search(r"special ChooseMonForMoveTutor", body):
            flags.add("move_tutor")
        if re.search(r"ScriptGetPartyMonSpecies", body):
            flags.add("name_rater")
        for m in re.finditer(
            r"^\s*(?:goto\w*|call\w*|case\s+\w+,)(?:\s+\w+,)*\s+(\w+)", body, re.I | re.M
        ):
            tgt = m.group(1)
            if tgt not in seen and tgt in blocks:
                seen.add(tgt)
                queue.append(tgt)
    # Post-BFS: checks that need the full accumulated body across all reachable blocks
    all_body = "\n".join(all_body_parts)
    if (re.search(r"checkitem ITEM_(?!(?:ROOT|CLAW)_FOSSIL|HEART_SCALE)\w+", all_body, re.I)
            and re.search(r"\bgiveitem\b", all_body, re.I)):
        flags.add("checkitem_exchange")
    if (re.search(r"\bcheckmoney\b|\bremovemoney\b", all_body, re.I)
            and re.search(r"\bgiveitem\b", all_body, re.I)
            and not re.search(r"\bpokemart\b", all_body, re.I)):
        flags.add("money_item_vendor")
    if re.search(
        r"specialvar\s+\w+,\s*GetFrontierBattlePoints|special\s+TakeFrontierBattlePoints",
        all_body,
    ):
        flags.add("bp_vendor")
    if re.search(r"\bVAR_ASH_GATHER_COUNT\b", all_body):
        flags.add("ash_vendor")
    return flags


def _em_exchange_name(c: str) -> str:
    """ITEM_X or DECOR_X → display name."""
    return re.sub(r"^(?:ITEM|DECOR)_", "", c).replace("_", " ").title()


def _em_exchange_pairs(
    scr: str,
    blocks: dict,
    flags: set[str],
    scripts_path: "Path | None" = None,
) -> list[tuple[str, str]]:
    """Extract (given, obtained) pairs from an Emerald NPC script."""
    if not flags:
        return []

    seen: set[str] = {scr}
    queue = [scr]
    visited_lines: list[str] = []

    while queue:
        label = queue.pop(0)
        body_lines = blocks.get(label, [])
        visited_lines.extend(body_lines)
        body = "\n".join(body_lines)
        for m in re.finditer(
            r"^\s*(?:goto\w*|call\w*|case\s+\w+,)(?:\s+\w+,)*\s+(\w+)", body, re.I | re.M
        ):
            tgt = m.group(1)
            if tgt not in seen and tgt in blocks:
                seen.add(tgt)
                queue.append(tgt)

    all_body = "\n".join(visited_lines)

    if "move_reminder" in flags:
        return [("Heart Scale", "Any forgotten move")]

    pairs: list[tuple[str, str]] = []

    if "vendor" in flags:
        if re.search(r"checkitem ITEM_(?:ROOT|CLAW)_FOSSIL", all_body, re.I):
            return [("Root Fossil", "Lileep"), ("Claw Fossil", "Anorith")]
        mart_m = re.search(r"\bpokemart\s+(\w+)", all_body, re.I)
        if mart_m:
            for line in blocks.get(mart_m.group(1), []):
                mi = re.search(r"\.2byte\s+(ITEM_\w+)", line)
                if mi:
                    pairs.append(("Money", _item_name(mi.group(1))))

    if "decor_vendor" in flags:
        decor_m = re.search(r"\bpokemartdecoration\s+(\w+)", all_body, re.I)
        if decor_m:
            for line in blocks.get(decor_m.group(1), []):
                di = re.search(r"\.2byte\s+(DECOR_\w+)", line)
                if di:
                    pairs.append(("Money", _em_exchange_name(di.group(1))))

    if "berry_powder_vendor" in flags:
        seen_bp: set[tuple] = set()
        for lbl in seen:
            bl_body = "\n".join(blocks.get(lbl, []))
            m8 = re.search(r"setvar\s+VAR_0x8008,\s+(ITEM_\w+)", bl_body, re.I)
            m9 = re.search(r"setvar\s+VAR_0x8009,\s+(\d+)", bl_body, re.I)
            if m8 and m9:
                item = _item_name(m8.group(1))
                cost_str = f"{m9.group(1)} Berry Powder"
                p = (cost_str, item)
                if p not in seen_bp:
                    seen_bp.add(p)
                    pairs.append(p)

    if "move_tutor" in flags:
        _SKIP_SUFFIXES = frozenset({
            "ExitTutorMoveSelect", "ExitTutorMove", "ConfirmMoveSelection",
            "ChooseNewLeftTutorMove", "ChooseNewRightTutorMove", "ChooseNewMove",
            "ChooseLeftTutorMove", "ChooseRightTutorMove",
            "AlreadyMetLeftTutor", "AlreadyMetRightTutor",
            "CancelChooseMon", "ChooseNewMove",
        })
        move_costs: dict[str, int] = {}
        case_moves: list[tuple[str, str]] = []
        for label in seen:
            body_lines = blocks.get(label, [])
            body = "\n".join(body_lines)
            cm = re.search(r"setvar VAR_0x8008,\s+(\d+)", body)
            if cm:
                move_costs[label] = int(cm.group(1))
            for m in re.finditer(r"case\s+\d+,\s+(\w+EventScript_([A-Za-z]+))", body):
                tgt_label, suffix = m.group(1), m.group(2)
                if suffix not in _SKIP_SUFFIXES and not suffix.startswith("Exit"):
                    case_moves.append((tgt_label, _pretty_move(suffix)))
        seen_moves: set[str] = set()
        for tgt_label, move_name in case_moves:
            cost = move_costs.get(tgt_label)
            if cost is not None and move_name not in seen_moves:
                pairs.append((f"{cost} BP", move_name))
                seen_moves.add(move_name)
        if not pairs:
            for label in seen:
                body = "\n".join(blocks.get(label, []))
                for m in re.finditer(r"setvar VAR_0x8005,\s+TUTOR_MOVE_(\w+)", body):
                    move_name = m.group(1).replace("_", " ").title()
                    if move_name not in seen_moves:
                        pairs.append(("Free (once)", move_name))
                        seen_moves.add(move_name)

    # Item-for-item exchanges (checkitem ITEM_X → removeitem + giveitem)
    if "checkitem_exchange" in flags:
        seen_pairs: set[tuple] = set()
        # Sub-pattern (a): per-block removeitem + giveitem pairing
        for lbl in seen:
            bl = blocks.get(lbl, [])
            removes = [
                (m.group(1), int(m.group(2) or 1))
                for line in bl
                if (m := re.match(r"removeitem\s+(ITEM_\w+)(?:,\s*(\d+))?", line.strip()))
            ]
            gives = [
                m.group(1)
                for line in bl
                if (m := re.match(r"giveitem\s+(ITEM_\w+)", line.strip()))
            ]
            if not removes or not gives:
                continue
            if len(removes) > 1 and len(gives) == 1:
                # Multiple items required for one give (e.g. Shoal Cave Shell Bell)
                given_str = " + ".join(
                    f"{q}× {_item_name(r)}" if q > 1 else _item_name(r)
                    for r, q in removes
                )
                p = (given_str, _item_name(gives[0]))
                if p not in seen_pairs:
                    seen_pairs.add(p)
                    pairs.append(p)
            else:
                for ritem, rqty in removes:
                    for gitem in gives:
                        if ritem != gitem:
                            given_str = (
                                f"{rqty}× {_item_name(ritem)}" if rqty > 1
                                else _item_name(ritem)
                            )
                            p = (given_str, _item_name(gitem))
                            if p not in seen_pairs:
                                seen_pairs.add(p)
                                pairs.append(p)
        # Sub-pattern (b): setvar VAR_0x8008, ITEM_X + setvar VAR_0x8009, ITEM_Y
        # (variable-swap style used by Route 124 shard→stone exchange)
        lines_list = all_body.splitlines()
        for i in range(len(lines_list) - 1):
            m8 = re.match(
                r"\s*setvar\s+VAR_0x8008,\s+(ITEM_\w+)\s*$", lines_list[i], re.I
            )
            m9 = re.match(
                r"\s*setvar\s+VAR_0x8009,\s+(ITEM_\w+)\s*$", lines_list[i + 1], re.I
            )
            if m8 and m9:
                p = (_item_name(m8.group(1)), _item_name(m9.group(1)))
                if p not in seen_pairs:
                    seen_pairs.add(p)
                    pairs.append(p)

    # Money-for-item exchanges (checkmoney/removemoney → giveitem)
    if "money_item_vendor" in flags:
        cost_m = re.search(r"\bcheckmoney\s+(\d+)", all_body, re.I)
        cost_str = f"₱{int(cost_m.group(1)):,}" if cost_m else "Money"
        for m in re.finditer(r"\bgiveitem\s+(ITEM_\w+)", all_body, re.I):
            pairs.append((cost_str, _item_name(m.group(1))))

    # Battle Frontier BP exchange (Exchange Service Corner)
    if "bp_vendor" in flags:
        seen_bp: set[tuple] = set()
        lines_list = all_body.splitlines()
        for i in range(len(lines_list) - 1):
            m8 = re.match(r"\s*setvar\s+VAR_0x8008,\s+(\d+)\s*$", lines_list[i], re.I)
            m9 = re.match(r"\s*setvar\s+VAR_0x8009,\s+(\w+)", lines_list[i + 1], re.I)
            if m8 and m9:
                cost = int(m8.group(1))
                item = _em_exchange_name(m9.group(1))
                p = (f"{cost} BP", item)
                if p not in seen_bp:
                    seen_bp.add(p)
                    pairs.append(p)

    # Ash/glass workshop exchange (VAR_ASH_GATHER_COUNT currency)
    if "ash_vendor" in flags:
        sym: dict[str, int] = {}
        if scripts_path is not None:
            try:
                raw_text = scripts_path.read_text(encoding="utf-8", errors="replace")
                for sm in re.finditer(r"\.set\s+(\w+),\s+(\w+)", raw_text):
                    name, val = sm.group(1), sm.group(2)
                    if val.isdigit():
                        sym[name] = int(val)
                    elif val in sym:
                        sym[name] = sym[val]
            except OSError:
                pass
        seen_ash: set[tuple] = set()
        for lbl in seen:
            bl_body = "\n".join(blocks.get(lbl, []))
            m8 = re.search(r"setvar\s+VAR_0x8008,\s+((?:ITEM|DECOR)_\w+)", bl_body, re.I)
            mA = re.search(r"setvar\s+VAR_0x800A,\s+(\w+)", bl_body, re.I)
            if m8 and mA:
                item = _em_exchange_name(m8.group(1))
                cost_name = mA.group(1)
                cost = sym.get(cost_name, 0)
                cost_str = f"{cost} Ash" if cost else f"{cost_name} Ash"
                p = (cost_str, item)
                if p not in seen_ash:
                    seen_ash.add(p)
                    pairs.append(p)

    return pairs


def _em_trade_info(scr: str, blocks: dict) -> dict | None:
    """Find INGAME_TRADE_X in NPC script and return {gives, receives, nickname}."""
    seen: set[str] = {scr}
    queue = [scr]
    while queue:
        label = queue.pop(0)
        body = "\n".join(blocks.get(label, []))
        m = re.search(r"setvar VAR_0x8008,\s+(INGAME_TRADE_\w+)", body)
        if m:
            return _em_trade_table().get(m.group(1))
        for mm in re.finditer(r"^\s*goto\w*\s+(\w+)", body, re.I | re.M):
            tgt = mm.group(1)
            if tgt not in seen and tgt in blocks:
                seen.add(tgt)
                queue.append(tgt)
    return None


def load_events_from_decomp(tile_entry: dict, game: str) -> dict:
    """
    Load NPC, trainer, item ball, hidden item, and berry events from map.json.
    Trainers are enriched with resolved party, abilities, and prize.
    Emerald only; other games return empty dicts.
    """
    empty: dict = {
        "npcs": [], "trainers": [], "items": [], "hidden_items": [], "berries": []
    }
    if game != "emerald":
        return empty

    maps_dir = BASE_DIR / "pokeemerald" / "data" / "maps"
    if not maps_dir.exists():
        return empty

    global_item_scripts = _parse_item_commands(
        BASE_DIR / "pokeemerald" / "data" / "scripts" / "item_ball_scripts.inc"
    )

    # Detect static encounters first so we can exclude those from NPC list
    _static_encs = _em_static_encounters(tile_entry, game)
    # Build set of (map_name, x, y) coords claimed by static encounters
    _static_enc_coords: set[tuple] = {
        (e["map_name"], e["x"], e["y"])
        for e in _static_encs
        if e.get("x") is not None and e.get("y") is not None
    }

    result: dict = {
        "npcs": [], "trainers": [], "gym_leaders": [], "rivals": [], "items": [], "hidden_items": [],
        "berries": [], "field_objects": [],
        "static_encounters": _static_encs,
        "coin_prizes": _em_game_corner_prizes(tile_entry),
        "secret_bases": [],
    }
    _gl_seen: set[str] = set()  # dedup gym leaders by trainer_const across shared maps

    _EMERALD_OBSTACLE_GFX: dict[str, tuple[str, str]] = {
        "OBJ_EVENT_GFX_CUTTABLE_TREE":  ("♣", "Cut tree"),
        "OBJ_EVENT_GFX_BREAKABLE_ROCK": ("⊗", "Rock Smash rock"),
        "OBJ_EVENT_GFX_PUSHABLE_BOULDER": ("▣", "Strength boulder"),
    }

    for map_name in tile_entry.get("maps", []):
        map_dir       = maps_dir / map_name
        map_json_path = map_dir / "map.json"
        if not map_json_path.exists():
            continue
        try:
            with open(map_json_path, encoding="utf-8") as f:
                map_data = json.load(f)
        except Exception:
            continue

        scripts_path        = map_dir / "scripts.inc"
        local_blocks, local_texts = _parse_scripts_file(scripts_path)
        blocks = {**_global_blocks(), **local_blocks}
        local_item_scripts  = _parse_item_commands(scripts_path)
        global_texts        = _load_global_text_labels()
        text_by_label       = {**global_texts, **local_texts}

        for evt in map_data.get("object_events", []):
            x        = evt.get("x", 0)
            y        = evt.get("y", 0)
            gfx      = evt.get("graphics_id", "")
            tt       = evt.get("trainer_type", "TRAINER_TYPE_NONE")
            scr      = evt.get("script", "")
            flag     = evt.get("flag", "0")
            berry_id = evt.get("trainer_sight_or_berry_tree_id", "")
            movement = evt.get("movement_type", "MOVEMENT_TYPE_NONE")

            if gfx in _EMERALD_OBSTACLE_GFX:
                char, label = _EMERALD_OBSTACLE_GFX[gfx]
                result["field_objects"].append({
                    "x": x, "y": y, "char": char, "label": label,
                    "gfx": gfx, "map_name": map_name,
                })

            elif gfx == "OBJ_EVENT_GFX_BERRY_TREE":
                result["berries"].append({
                    "x": x, "y": y,
                    "berry_id": berry_id,
                    "berry_name": _extract_berry_name(berry_id),
                    "map_name": map_name,
                })

            elif gfx == "OBJ_EVENT_GFX_ITEM_BALL":
                item_name = (
                    local_item_scripts.get(scr)
                    or global_item_scripts.get(scr)
                    or (scr.split("_Item")[-1].replace("_", " ").title()
                        if "_Item" in scr else scr)
                )
                result["items"].append({
                    "x": x, "y": y,
                    "item_name": item_name,
                    "flag": flag, "script": scr, "map_name": map_name,
                })

            elif gfx == "OBJ_EVENT_GFX_FOSSIL":
                # Fossil objects call giveitem in their script. The Mirage Tower
                # fossils have a direct giveitem in their label; the Desert
                # Underpass fossil branches to sub-labels, so we collect all
                # reachable giveitem calls from the script body.
                item_name = local_item_scripts.get(scr)
                if not item_name:
                    found: list[str] = []
                    seen_lbl: set[str] = set()
                    queue_lbl: list[str] = [scr]
                    while queue_lbl:
                        lbl = queue_lbl.pop()
                        if lbl in seen_lbl or lbl not in blocks:
                            continue
                        seen_lbl.add(lbl)
                        raw = blocks[lbl]
                        body = "\n".join(raw) if isinstance(raw, list) else raw
                        for m in re.finditer(r'\bgiveitem\s+(ITEM_\w+)', body, re.I):
                            name = _em_item_display(m.group(1))
                            if name not in found:
                                found.append(name)
                        for m in re.finditer(
                            r'^\s*(?:goto|call)\s+(\w+)'
                            r'|^\s*(?:goto\w+|call\w+)\s+\w+,\s*(\w+)',
                            body, re.I | re.M
                        ):
                            tgt = m.group(1) or m.group(2)
                            if tgt and tgt not in seen_lbl:
                                queue_lbl.append(tgt)
                    item_name = " / ".join(found) if found else scr
                result["items"].append({
                    "x": x, "y": y,
                    "item_name": item_name,
                    "flag": flag, "script": scr, "map_name": map_name,
                    "is_fossil": True,
                })

            elif tt == "TRAINER_TYPE_NONE" and gfx in _EM_GYM_LEADER_GFX:
                gl = _em_gym_leader_info(gfx, local_blocks, text_by_label)
                if gl and gl["trainer_const"] not in _gl_seen:
                    _gl_seen.add(gl["trainer_const"])
                    result["gym_leaders"].append({**gl, "x": x, "y": y, "map_name": map_name})

            elif tt != "TRAINER_TYPE_NONE":
                td = _extract_trainer_script_data(scr, blocks, text_by_label)
                result["trainers"].append({
                    "x": x, "y": y,
                    "graphics_id": gfx, "script": scr,
                    "flag": flag, "map_name": map_name,
                    "category": "Standard",
                    **td,
                })

            elif gfx == "OBJ_EVENT_GFX_VAR_0" and scr and scr not in ("0x0", "0"):
                # Rival NPCs: OBJ_EVENT_GFX_VAR_0 sprite with a script that leads
                # to trainerbattle_no_intro TRAINER_MAY_* or TRAINER_BRENDAN_*
                _EM_RIVAL_TB_RE_OBJ = re.compile(
                    r"trainerbattle_no_intro\s+(TRAINER_(?:MAY|BRENDAN)_\w+)")
                visited_obj: set[str] = set()
                queue_obj = [scr]
                combined_obj = ""
                while queue_obj:
                    lbl = queue_obj.pop(0)
                    if lbl in visited_obj or lbl not in blocks:
                        continue
                    visited_obj.add(lbl)
                    body_lines = blocks[lbl]
                    body = "\n".join(body_lines) if isinstance(body_lines, list) else body_lines
                    combined_obj += body + "\n"
                    for bm in re.finditer(
                            r"^\s*(?:goto\w*|call\w*|case\s+\w+,)(?:\s+\w+,)*\s+(\w+)",
                            body, re.I | re.M):
                        queue_obj.append(bm.group(1))
                found_rival = False
                for m in _EM_RIVAL_TB_RE_OBJ.finditer(combined_obj):
                    const = m.group(1)
                    if any(r["trainer_const"] == const for r in result["rivals"]):
                        continue
                    result["rivals"].append({
                        "trainer_const": const,
                        "x": x,
                        "y": y,
                        "map_name": map_name,
                    })
                    found_rival = True
                if found_rival:
                    continue
                # If no rival found, fall through to NPC
                if (map_name, x, y) in _static_enc_coords:
                    continue
                script_data = _em_collect_npc_script(scr, blocks, text_by_label)
                if script_data is None:
                    # Fallback: global script index or placeholder
                    global_result = _global_script_index().get(scr)
                    if isinstance(global_result, dict):
                        script_data = {"states": [{"flag": None, "polarity": "post",
                                                   "pre_msgs": [], "gives": [],
                                                   "yesno": True,
                                                   "prompt": global_result.get("prompt", ""),
                                                   "yes_text": global_result.get("yes", ""),
                                                   "no_text": global_result.get("no", "")}]}
                    else:
                        ref = f"<{scr}>" + (f" -> {global_result}" if global_result else "")
                        script_data = {"states": [{"flag": None, "polarity": "post",
                                                   "pre_msgs": [ref], "gives": []}]}
                _sflags = _em_detect_service_flags(scr, blocks)
                result["npcs"].append({
                    "x": x, "y": y,
                    "graphics_id": gfx, "script": scr,
                    "movement":    movement,
                    "script_data": script_data,
                    "flag": flag, "map_name": map_name,
                    "service_flags": _sflags,
                    "exchange_pairs": _em_exchange_pairs(scr, blocks, _sflags, scripts_path=scripts_path),
                    "trade_info": (_em_trade_info(scr, blocks) if "trader" in _sflags else None),
                })

            elif scr and scr not in ("0x0", "0"):
                # Check if this TRAINER_TYPE_NONE object has a trainerbattle in its script
                # (covers Maxie, Tabitha and similar story-scripted interaction battles)
                td_interaction = _extract_trainer_script_data(scr, blocks, text_by_label)
                if td_interaction.get("trainer_const"):
                    result["trainers"].append({
                        "x": x, "y": y,
                        "graphics_id": gfx, "script": scr,
                        "flag": flag, "map_name": map_name,
                        "category": "Interaction",
                        **td_interaction,
                    })
                    continue
                # Skip NPCs that are represented as static encounters
                if (map_name, x, y) in _static_enc_coords:
                    continue
                script_data = _em_collect_npc_script(scr, blocks, text_by_label)
                if script_data is None:
                    global_result = _global_script_index().get(scr)
                    if isinstance(global_result, dict):
                        script_data = {"states": [{"flag": None, "polarity": "post",
                                                   "pre_msgs": [], "gives": [],
                                                   "yesno": True,
                                                   "prompt": global_result.get("prompt", ""),
                                                   "yes_text": global_result.get("yes", ""),
                                                   "no_text": global_result.get("no", "")}]}
                    else:
                        ref = f"<{scr}>" + (f" -> {global_result}" if global_result else "")
                        script_data = {"states": [{"flag": None, "polarity": "post",
                                                   "pre_msgs": [ref], "gives": []}]}
                _sflags = _em_detect_service_flags(scr, blocks)
                result["npcs"].append({
                    "x": x, "y": y,
                    "graphics_id": gfx, "script": scr,
                    "movement":    movement,
                    "script_data": script_data,
                    "flag": flag, "map_name": map_name,
                    "service_flags": _sflags,
                    "exchange_pairs": _em_exchange_pairs(scr, blocks, _sflags, scripts_path=scripts_path),
                    "trade_info": (_em_trade_info(scr, blocks) if "trader" in _sflags else None),
                })

        # Scan all local blocks for tag-battle form (TRAINER_BATTLE_SET_TRAINER_A/B).
        # These are story-scripted one-time battles not tied to any object event's script chain.
        _em_tag_seen: set[str] = {
            t.get("trainer_const", "") for t in result["trainers"]
        }
        _EM_TAG_TRAINER_RE = re.compile(
            r"trainerbattle\s+TRAINER_BATTLE_SET_TRAINER_[AB],\s*(\w+)", re.I)
        _EM_TAG_TEXT_RE = re.compile(r"trainerbattle\s+TRAINER_BATTLE_SET_TRAINER_[AB]"
                                     r"(?:,\s*\w+){2},\s*(\w+),\s*(\w+)", re.I)
        for block_lines in local_blocks.values():
            for bl in block_lines:
                mt = _EM_TAG_TRAINER_RE.match(bl)
                if mt:
                    const = mt.group(1)
                    if const in _em_tag_seen:
                        continue
                    _em_tag_seen.add(const)
                    mt2 = _EM_TAG_TEXT_RE.match(bl)
                    intro = text_by_label.get(mt2.group(1), "") if mt2 else ""
                    defeat = text_by_label.get(mt2.group(2), "") if mt2 else ""
                    result["trainers"].append({
                        "x": 0, "y": 0,
                        "graphics_id": "", "script": "",
                        "flag": "", "map_name": map_name,
                        "category": "One-time",
                        "trainer_const": const,
                        "intro_text": intro,
                        "defeat_text": defeat,
                        "post_text": "",
                    })

        for evt in map_data.get("bg_events", []):
            if evt.get("type") == "hidden_item":
                item_raw  = evt.get("item", "")
                item_name = _em_item_display(item_raw)
                result["hidden_items"].append({
                    "x": evt.get("x", 0), "y": evt.get("y", 0),
                    "item_name": item_name,
                    "flag": evt.get("flag", ""), "map_name": map_name,
                })
            elif evt.get("type") == "secret_base":
                result["secret_bases"].append({
                    "x": evt.get("x", 0), "y": evt.get("y", 0),
                    "secret_base_id": evt.get("secret_base_id", ""),
                    "map_name": map_name,
                })

        # Rival battles (May / Brendan) are coord-triggered: invisible tiles fire
        # a script chain that ends in trainerbattle_no_intro TRAINER_MAY_* or
        # TRAINER_BRENDAN_*. Scan all blocks reachable from each coord_event script.
        _EM_RIVAL_TB_RE = re.compile(
            r"trainerbattle_no_intro\s+(TRAINER_(?:MAY|BRENDAN)_\w+)")
        for ce in map_data.get("coord_events", []):
            ce_script = ce.get("script", "")
            if not ce_script or ce_script in ("0x0", "0"):
                continue
            visited_ce: set[str] = set()
            queue_ce = [ce_script]
            combined = ""
            while queue_ce:
                lbl = queue_ce.pop(0)
                if lbl in visited_ce or lbl not in blocks:
                    continue
                visited_ce.add(lbl)
                body_lines = blocks[lbl]
                body = "\n".join(body_lines) if isinstance(body_lines, list) else body_lines
                combined += body + "\n"
                for bm in re.finditer(
                        r"^\s*(?:goto\w*|call\w*|case\s+\w+,)(?:\s+\w+,)*\s+(\w+)",
                        body, re.I | re.M):
                    queue_ce.append(bm.group(1))
            for m in _EM_RIVAL_TB_RE.finditer(combined):
                const = m.group(1)
                if any(r["trainer_const"] == const for r in result["rivals"]):
                    continue
                result["rivals"].append({
                    "trainer_const": const,
                    "x": ce.get("x", 0),
                    "y": ce.get("y", 0),
                    "map_name": map_name,
                })

    _enrich_trainers(result["trainers"])
    _enrich_trainers(result["gym_leaders"])
    _enrich_trainers(result["rivals"])
    return result


# ---------------------------------------------------------------------------
# Wild encounter data
# ---------------------------------------------------------------------------

def load_wild_encounters() -> dict:
    """Load pokeemerald wild_encounters.json → {map_id: (entry, fields)} lookup."""
    path = BASE_DIR / "pokeemerald" / "src" / "data" / "wild_encounters.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    result: dict = {}
    for group in data.get("wild_encounter_groups", []):
        fields = group.get("fields", [])
        for entry in group.get("encounters", []):
            mid = entry.get("map")
            if mid:
                result[mid] = (entry, fields)
    return result


def _pretty_species(species: str) -> str:
    name = species.removeprefix("SPECIES_").replace("_", " ").title()
    name = name.replace("Mr ", "Mr. ").replace("Mime Jr ", "Mime Jr. ").replace("Ho Oh", "Ho-Oh")
    return name


def _aggregate_mons(mons: list, rates: list) -> list[tuple[str, int, int, int]]:
    """Aggregate slots by species → [(species, min_lv, max_lv, total_rate)] sorted by rate desc."""
    agg: dict = {}
    for i, mon in enumerate(mons):
        sp   = mon["species"]
        rate = rates[i] if i < len(rates) else 0
        if sp not in agg:
            agg[sp] = [mon["min_level"], mon["max_level"], 0]
        agg[sp][0] = min(agg[sp][0], mon["min_level"])
        agg[sp][1] = max(agg[sp][1], mon["max_level"])
        agg[sp][2] += rate
    return sorted(
        [(sp, d[0], d[1], d[2]) for sp, d in agg.items()],
        key=lambda x: -x[3],
    )


def _render_encounter_table(label: str, mons: list, rates: list,
                             encounter_rate: int | None = None) -> list[str]:
    rows_out = []
    rate_note = f"  (rate: {encounter_rate})" if encounter_rate is not None else ""
    rows_out.append(f"  {label}{rate_note}")
    for sp, mn, mx, pct in _aggregate_mons(mons, rates):
        name   = _pretty_species(sp)
        lv_str = f"Lv {mn}" if mn == mx else f"Lv {mn}-{mx}"
        rows_out.append(f"    {name:<20} {lv_str:<12} {pct:>3}%")
    return rows_out


def _render_encounter_index(map_entries: list, wild_encounters: dict) -> list[str]:
    if not wild_encounters or not map_entries:
        return []

    seen: set = set()
    enc_list: list = []
    for m in map_entries:
        mid = m.get("id", "")
        if mid in wild_encounters and mid not in seen:
            seen.add(mid)
            enc_list.append((mid, wild_encounters[mid]))
    if not enc_list:
        return []

    SEP = "=" * 70
    lines: list = ["", SEP, "  Wild Encounters", SEP]

    for map_id, (entry, fields) in enc_list:
        if len(enc_list) > 1:
            lines.append(f"\n  [ {map_id} ]")

        rate_lookup: dict = {}
        fishing_groups: dict = {}
        for field in fields:
            ftype = field.get("type", "")
            rate_lookup[ftype] = field.get("encounter_rates", [])
            if field.get("groups"):
                fishing_groups = field["groups"]

        if "land_mons" in entry:
            lm = entry["land_mons"]
            lines += _render_encounter_table(
                "Tall Grass", lm["mons"],
                rate_lookup.get("land_mons", []),
                lm.get("encounter_rate"),
            )
            lines.append("")

        if "water_mons" in entry:
            wm = entry["water_mons"]
            lines += _render_encounter_table(
                "Surfing", wm["mons"],
                rate_lookup.get("water_mons", []),
                wm.get("encounter_rate"),
            )
            lines.append("")

        if "rock_smash_mons" in entry:
            rm = entry["rock_smash_mons"]
            lines += _render_encounter_table(
                "Rock Smash", rm["mons"],
                rate_lookup.get("rock_smash_mons", []),
                rm.get("encounter_rate"),
            )
            lines.append("")

        if "fishing_mons" in entry:
            fm       = entry["fishing_mons"]
            all_mons = fm["mons"]
            all_rates = rate_lookup.get("fishing_mons", [])
            rod_names = {"old_rod": "Old Rod", "good_rod": "Good Rod", "super_rod": "Super Rod"}
            if fishing_groups:
                for rod_key in ("old_rod", "good_rod", "super_rod"):
                    indices  = fishing_groups.get(rod_key, [])
                    if not indices:
                        continue
                    rod_mons  = [all_mons[i] for i in indices if i < len(all_mons)]
                    rod_rates = [all_rates[i] for i in indices if i < len(all_rates)]
                    if rod_mons:
                        lines += _render_encounter_table(
                            f"Fishing — {rod_names[rod_key]}", rod_mons, rod_rates,
                        )
                        lines.append("")
            else:
                lines += _render_encounter_table(
                    "Fishing", all_mons, all_rates, fm.get("encounter_rate"),
                )
                lines.append("")

    return lines


# ---------------------------------------------------------------------------
# Event section renderers  (parallel to heartgold_data / platinum_data)
# ---------------------------------------------------------------------------

_EM_SEP = "=" * 70


def _fmt_yesno(yesno: dict, indent: str = "          ") -> list[str]:
    """Render a YESNO dict as Prompt/Yes/No lines."""
    lines = []
    if yesno.get("prompt"):
        lines.append(f"{indent}Prompt: {yesno['prompt']}")
    if yesno.get("yes"):
        lines.append(f"{indent}  Yes:  {yesno['yes']}")
    if yesno.get("no"):
        lines.append(f"{indent}  No:   {yesno['no']}")
    return lines


def _em_fmt_party(party: list) -> list[str]:
    lines = []
    for mon in party:
        species = mon.get("species", "?").removeprefix("SPECIES_").replace("_", " ").title()
        lvl     = mon.get("lvl", "?")
        item    = mon.get("item", "")
        moves   = mon.get("moves", [])
        ab      = mon.get("abilities_str", "")
        iv      = mon.get("iv", 0)
        held    = (f"  @ {item.replace('ITEM_','').replace('_',' ').title()}"
                   if item and item.lower() not in ("none", "") else "")
        line    = f"    {species:<18} Lv {lvl:<3}{held}"
        if ab:
            line += f"  [{ab}]"
        lines.append(line)
        if moves:
            lines.append(f"      Moves: {', '.join(m.replace('MOVE_','').replace('_',' ').title() for m in moves)}")
        if iv:
            true_iv = iv * 31 // 255
            lines.append(f"      IVs:   {iv} ({true_iv}/31 each stat)")
    return lines


def render_gym_leader_section(gym_leaders: list) -> list[str]:
    if not gym_leaders:
        return []
    lines = ["", _EM_SEP, "  Gym Leader Index  (Emerald)", _EM_SEP, ""]
    for gl in gym_leaders:
        name   = gl.get("leader_name", "?")
        tc     = gl.get("trainer_const", "?")
        x, y   = gl.get("x", 0), gl.get("y", 0)
        prize  = gl.get("prize")
        double = gl.get("is_double", False)
        battle_kind = "Double" if double else "Single"
        prize_str = f"  Prize: ₽{prize}" if prize else ""
        lines.append(f"  (col={x:>3}, row={y:>3})  {name}  [{tc}]  [Interaction]  {battle_kind}{prize_str}")
        lines.append("")

        party = gl.get("party", [])
        if party:
            lines.append("  Party:")
            lines += _em_fmt_party(party)
            lines.append("")

        if gl.get("intro_text"):
            lines.append(f"  Battle intro:  \"{gl['intro_text']}\"")
        if gl.get("defeat_text"):
            lines.append(f"  Battle defeat: \"{gl['defeat_text']}\"")
        if gl.get("badge_text"):
            lines.append(f"  Badge text:    \"{gl['badge_text']}\"")
        if gl.get("post_text"):
            lines.append(f"  Post-battle:   \"{gl['post_text']}\"")
        if gl.get("gym_badge"):
            lines.append(f"  Gym badge:     {gl['gym_badge']}")
        if gl.get("gives_item"):
            gi = gl["gives_item"]
            lines.append(f"  Gives item:    {gi['item_id']}  ×{gi['quantity']}")

        rematches = gl.get("vs_rematches", [])
        if rematches:
            lines.append("")
            lines.append("  PokéNav Rematches (Match Call):")
            for i, rm in enumerate(rematches, 1):
                r_prize = rm.get("prize")
                r_party = rm.get("party", [])
                r_prize_str = f"  Prize: ₽{r_prize}" if r_prize else ""
                lines.append(f"  [{i}]{r_prize_str}")
                lines += _em_fmt_party(r_party)

        if gl.get("pre_rematch_text"):
            lines.append(f"  Pre-rematch:   \"{gl['pre_rematch_text']}\"")
        if gl.get("rematch_defeat_text"):
            lines.append(f"  Rematch defeat:\"{gl['rematch_defeat_text']}\"")
        if gl.get("post_rematch_text"):
            lines.append(f"  Post-rematch:  \"{gl['post_rematch_text']}\"")

        lines.append("")
    return lines


def render_rival_section(rivals: list) -> list[str]:
    if not rivals:
        return []
    lines = ["", _EM_SEP, "  Rival Encounters  (Emerald)", _EM_SEP, ""]
    seen_const: set[str] = set()
    for r in rivals:
        const = r.get("trainer_const", "")
        if const in seen_const:
            continue
        seen_const.add(const)
        x, y = r.get("x", 0), r.get("y", 0)
        name = r.get("tr_name") or const
        cls  = r.get("tr_class_disp", "")
        prize = r.get("prize")
        prize_str = f"  Prize: ₽{prize}" if prize else ""
        lines.append(f"  (col={x:>3}, row={y:>3})  {cls} {name}  [{const}]{prize_str}")
        lines.append("")
        party = r.get("party", [])
        if party:
            lines += _em_fmt_party(party)
            lines.append("")
    return lines


def render_sign_section(signs: list) -> list[str]:
    if not signs:
        return []
    lines = ["", _EM_SEP, "  Sign Index", _EM_SEP, ""]
    for s in signs:
        coord = f"(col={s['x']:>3}, row={s['y']:>3})"
        lines.append(f"  {coord}  {s['text']}")
    lines.append("")
    return lines


_EM_GIVEITEM_RE      = re.compile(r"^giveitem\s+(ITEM_\w+)", re.I)
_EM_FLAG_GATE_RE     = re.compile(r"^(goto_if_set|goto_if_unset)\s+(\w+)\s*,\s*(\w+)", re.I)
_EM_MSGBOX_RE        = re.compile(r"^msgbox\s+(\w+)\s*,\s*MSGBOX_(?!YESNO)\w+", re.I)
_EM_MSGBOX_YESNO_RE  = re.compile(r"^msgbox\s+(\w+)\s*,\s*MSGBOX_YESNO", re.I)
_EM_GOTO_IF_YES_RE   = re.compile(r"^goto_if_eq\s+VAR_RESULT\s*,\s*YES\s*,\s*(\w+)", re.I)
_EM_GOTO_IF_NO_RE    = re.compile(r"^goto_if_eq\s+VAR_RESULT\s*,\s*NO\s*,\s*(\w+)", re.I)
_EM_PLAIN_GOTO_RE    = re.compile(r"^goto\s+(\w+)$", re.I)


def _em_item_name(constant: str) -> str:
    return _em_item_display(constant)


def _em_msg_from_line(line: str, text_by_label: dict) -> str | None:
    """Return English text from an Emerald msgbox/message line, or None."""
    m = _EM_MSGBOX_RE.match(line)
    if m:
        return text_by_label.get(m.group(1)) or None
    m = re.match(r"^message\s+(\w+)", line, re.I)
    if m:
        return text_by_label.get(m.group(1)) or None
    return None


def _em_msgs_from_lines(lines: list[str], text_by_label: dict, blocks: dict,
                         follow_call: bool = True) -> list[str]:
    """Collect all English dialogue texts from Emerald script lines."""
    out = []
    for line in lines:
        t = _em_msg_from_line(line, text_by_label)
        if t:
            out.append(t)
        elif follow_call and re.match(r"^(?:call|goto)\s+(\w+)", line, re.I):
            m = re.match(r"^(?:call|goto)\s+(\w+)", line, re.I)
            sub = m.group(1)
            for sl in blocks.get(sub, []):
                t2 = _em_msg_from_line(sl, text_by_label)
                if t2:
                    out.append(t2)
    return out


def _em_collect_npc_script(scr: str, blocks: dict,
                            text_by_label: dict) -> dict | None:
    """
    Analyse an Emerald NPC's script block and return structured dialogue +
    item-give data in the same {"states": [...]} shape as the Gen 4 games.
    Returns None if the label is not in blocks (caller falls back to global index).
    """
    root = blocks.get(scr)
    if root is None:
        return None

    # Phase 1: flag gates before first content
    gates: list[tuple[str, str, str]] = []
    fallthrough_start = len(root)
    for i, line in enumerate(root):
        m = _EM_FLAG_GATE_RE.match(line)
        if m:
            opcode   = m.group(1).lower()
            polarity = "post" if opcode == "goto_if_set" else "pre"
            gates.append((m.group(2), m.group(3), polarity))
            continue
        if (_em_msg_from_line(line, text_by_label) is not None
                or _EM_MSGBOX_YESNO_RE.match(line)
                or _EM_GIVEITEM_RE.match(line)):
            fallthrough_start = i
            break

    fallthrough = root[fallthrough_start:]

    # Phase 2: locate give or yes/no in fallthrough
    item_const: str | None = None
    give_idx:   int | None = None
    yesno_idx:  int | None = None

    for i, line in enumerate(fallthrough):
        m = _EM_GIVEITEM_RE.match(line)
        if m:
            item_const, give_idx = m.group(1), i
            break
        if _EM_MSGBOX_YESNO_RE.match(line):
            yesno_idx = i
            break

    # Phase 3: collect messages and build default state
    declined_msg: str | None = None
    yesno_give   = False

    if give_idx is not None:
        pre_msgs  = _em_msgs_from_lines(fallthrough[:give_idx], text_by_label, blocks)
        post_msgs = _em_msgs_from_lines(fallthrough[give_idx + 1:], text_by_label, blocks)
        gives     = [{"item": item_const, "qty": 1, "post_msgs": post_msgs}]

    elif yesno_idx is not None:
        yesno_give = True
        pre_msgs   = _em_msgs_from_lines(fallthrough[:yesno_idx], text_by_label, blocks)
        yes_label = no_label = None
        for later in fallthrough[yesno_idx + 1:]:
            yes_m  = _EM_GOTO_IF_YES_RE.match(later)
            no_m   = _EM_GOTO_IF_NO_RE.match(later)
            goto_m = _EM_PLAIN_GOTO_RE.match(later)
            if yes_m and yes_label is None:
                yes_label = yes_m.group(1)
            if no_m and no_label is None:
                no_label = no_m.group(1)
            if goto_m and yes_label and not no_label:
                no_label = goto_m.group(1)
                break
        # Scan yes-branch for give
        yes_body = blocks.get(yes_label or "", [])
        gives = []
        for i, line in enumerate(yes_body):
            m = _EM_GIVEITEM_RE.match(line)
            if m:
                yes_post = _em_msgs_from_lines(yes_body[i + 1:], text_by_label, blocks,
                                               follow_call=False)
                gives = [{"item": m.group(1), "qty": 1, "post_msgs": yes_post}]
                break
        # Decline text
        no_body = blocks.get(no_label or "", [])
        no_texts = _em_msgs_from_lines(no_body, text_by_label, blocks, follow_call=False)
        declined_msg = no_texts[0] if no_texts else None

    else:
        pre_msgs = _em_msgs_from_lines(fallthrough, text_by_label, blocks)
        gives    = []

    default_state: dict = {
        "flag": None, "polarity": "post",
        "pre_msgs": pre_msgs, "gives": gives,
    }
    if yesno_give:
        default_state["yesno"]       = True
        default_state["declined_msg"] = declined_msg

    # Phase 4: gate states (earliest → latest)
    gate_states: list[dict] = []
    for flag_name, target_label, polarity in reversed(gates):
        target_msgs = _em_msgs_from_lines(
            blocks.get(target_label, []), text_by_label, blocks
        )[:1]
        gate_states.append({
            "flag": flag_name, "polarity": polarity,
            "pre_msgs": target_msgs, "gives": [],
        })

    # Only return structured data if something interesting was found
    if not gates and not gives and not pre_msgs:
        return None

    return {"states": [default_state] + gate_states}


def render_npc_section(npcs: list) -> list[str]:
    if not npcs:
        return []
    lines = ["", _EM_SEP, "  NPC Index", _EM_SEP, ""]
    # Group by map_name when multiple maps share the layout
    from itertools import groupby
    map_names = [n.get("map_name", "") for n in npcs]
    multi_map = len(set(m for m in map_names if m)) > 1
    for map_name, group in groupby(npcs, key=lambda n: n.get("map_name", "")):
        if multi_map and map_name:
            lines.append(f"  [ {map_name} ]")
            lines.append("")
        for n in group:
            coord = f"(col={n['x']:>3}, row={n['y']:>3})"
            gfx = n.get("graphics_id", "")
            lines.append(f"  {coord}  [{gfx}]")
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
                    prompt   = state.get("prompt")   # global script yes/no
                    yes_text = state.get("yes_text")
                    no_text  = state.get("no_text")
                    if single:
                        content = INDENT
                    else:
                        tag = ("[default]" if flag is None else
                               (f"[~{flag}]" if polarity == "pre" else f"[{flag}]"))
                        lines.append(f"{INDENT}{tag}")
                        content = EXTRA
                    if prompt:
                        lines.append(f"{content}Prompt: {prompt}")
                        if yes_text:
                            lines.append(f"{content}  Yes:  {yes_text}")
                        if no_text:
                            lines.append(f"{content}  No:   {no_text}")
                    elif yesno:
                        if pre_msgs:
                            lines.append(f"{content}Prompt (yes/no):    \"{pre_msgs[-1]}\"")
                        if gives:
                            g = gives[0]
                            qty_tag = f"  ×{g['qty']}" if g["qty"] > 1 else ""
                            lines.append(f"{content}Gives:  {_em_item_name(g['item'])}{qty_tag}")
                            for msg in g.get("post_msgs", []):
                                lines.append(f"{content}Dialogue (after):   \"{msg}\"")
                        if declined:
                            lines.append(f"{content}Declined:           \"{declined}\"")
                    elif gives:
                        for msg in pre_msgs:
                            lines.append(f"{content}Dialogue (before):  \"{msg}\"")
                        for g in gives:
                            qty_tag = f"  ×{g['qty']}" if g["qty"] > 1 else ""
                            lines.append(f"{content}Gives:  {_em_item_name(g['item'])}{qty_tag}")
                            for msg in g.get("post_msgs", []):
                                lines.append(f"{content}Dialogue (after):   \"{msg}\"")
                    else:
                        for msg in pre_msgs:
                            lines.append(f"{content}\"{msg}\"")
            if n.get("flag") and n["flag"] not in ("0", ""):
                lines.append(f"          Flag:     {n['flag']}")
            for sf in sorted(n.get("service_flags", ())):
                lines.append(f"          [{sf}]")
            lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Static Encounter Index (Emerald)
# ---------------------------------------------------------------------------

_EM_SETWILD_RE = re.compile(
    r"setwildbattle\s+(SPECIES_\w+)\s*,\s*(\d+)", re.I
)
_EM_SETEVENTMON_RE = re.compile(
    r"seteventmon\s+(SPECIES_\w+)\s*,\s*(\d+)", re.I
)
_EM_ROAMER_INIT_RE = re.compile(r"special\s+InitRoamer", re.I)
_EM_LATI_CHOICE_RE = re.compile(r"multichoice.*MULTI_TV_LATI", re.I)
_EM_MAP_SCRIPT2_RE = re.compile(r"map_script_2\s+\w+\s*,\s*\w+\s*,\s*(\w+)")


def _em_bfs_lines(start: str, blocks: dict) -> list[str]:
    """BFS all reachable lines from start label in Emerald script blocks."""
    seen: set[str] = {start}
    queue = [start]
    all_lines: list[str] = []
    while queue:
        label = queue.pop(0)
        body_lines = blocks.get(label, [])
        all_lines.extend(body_lines)
        body = "\n".join(body_lines)
        for m in re.finditer(
            r"^\s*(?:goto\w*|call\w*|case\s+\w+,)(?:\s+\w+,)*\s+(\w+)",
            body, re.I | re.M
        ):
            tgt = m.group(1)
            if tgt not in seen and tgt in blocks:
                seen.add(tgt)
                queue.append(tgt)
    return all_lines


def _em_static_encounters(tile_entry: dict, game: str) -> list[dict]:
    """
    Find all scripted static Pokémon encounters for an Emerald layout.

    Scans object_events in every map that shares the layout.  Returns a list
    of encounter dicts with keys:
      species, level, battleable, tags, pre_battle_text, x, y, map_name
    """
    if game != "emerald":
        return []

    maps_dir = _EM_ROOT / "data" / "maps"
    if not maps_dir.exists():
        return []

    results: list[dict] = []
    seen_species: set[str] = set()  # dedup by species within layout

    for map_name in tile_entry.get("maps", []):
        map_dir       = maps_dir / map_name
        map_json_path = map_dir / "map.json"
        scripts_path  = map_dir / "scripts.inc"
        if not map_json_path.exists():
            continue
        try:
            with open(map_json_path, encoding="utf-8") as f:
                map_data = json.load(f)
        except Exception:
            continue

        local_blocks, local_texts = (
            _parse_scripts_file(scripts_path) if scripts_path.exists() else ({}, {})
        )
        blocks = {**_global_blocks(), **local_blocks}
        text_by_label = {**_load_global_text_labels(), **local_texts}

        # Combined encounter detector: setwildbattle OR seteventmon
        _EM_ANY_ENCOUNTER_RE = re.compile(
            r"(?:setwildbattle|seteventmon)\s+(SPECIES_\w+)\s*,\s*(\d+)", re.I
        )

        def _try_add_encounter(bfs_lines: list[str], x, y) -> bool:
            """Return True if a static or roaming encounter was added."""
            body = "\n".join(bfs_lines)

            # Roaming trigger: TV broadcast that sets Latias/Latios loose
            if _EM_ROAMER_INIT_RE.search(body) and _EM_LATI_CHOICE_RE.search(body):
                key = "Latias/Latios"
                if key in seen_species:
                    return True
                seen_species.add(key)
                pre_text: str | None = None
                for ln in bfs_lines:
                    m2 = re.match(r"msgbox\s+(\w+)\s*,\s*MSGBOX_DEFAULT", ln.strip(), re.I)
                    if m2:
                        t = text_by_label.get(m2.group(1), "")
                        if t:
                            pre_text = t
                    if _EM_LATI_CHOICE_RE.search(ln):
                        break
                results.append({
                    "species": "Latias / Latios",
                    "level":   40,
                    "battleable": False,
                    "tags":    ["roaming trigger → Hoenn",
                                'choice: "Red" → Latias, "Blue" → Latios'],
                    "pre_battle_text": pre_text,
                    "x": x, "y": y,
                    "map_name": map_name,
                })
                return True

            m = _EM_ANY_ENCOUNTER_RE.search(body)
            if not m:
                return False
            species_const = m.group(1)
            level = int(m.group(2))
            species = species_const.removeprefix("SPECIES_").replace("_", " ").title()
            if species in seen_species:
                return True
            seen_species.add(species)
            pre_text = None
            for ln in bfs_lines:
                m2 = re.match(
                    r"msgbox\s+(\w+)\s*,\s*MSGBOX_(?!YESNO)\w+", ln.strip(), re.I
                )
                if m2:
                    t = text_by_label.get(m2.group(1), "")
                    if t:
                        pre_text = t
                if _EM_ANY_ENCOUNTER_RE.search(ln):
                    break
            results.append({
                "species": species,
                "level":   level,
                "battleable": True,
                "tags":    [],
                "pre_battle_text": pre_text,
                "x": x, "y": y,
                "map_name": map_name,
            })
            return True

        # object_events with non-zero scripts
        for evt in map_data.get("object_events", []):
            scr = evt.get("script", "")
            if not scr or scr in ("0x0", "0"):
                continue
            x = evt.get("x", 0)
            y = evt.get("y", 0)
            _try_add_encounter(_em_bfs_lines(scr, blocks), x, y)

        # bg_events with type "sign": Southern Island Latias/Latios catch spot
        for evt in map_data.get("bg_events", []):
            if evt.get("type") != "sign":
                continue
            scr = evt.get("script", "")
            if not scr or scr in ("0x0", "0"):
                continue
            x = evt.get("x", 0)
            y = evt.get("y", 0)
            _try_add_encounter(_em_bfs_lines(scr, blocks), x, y)

        # coord_events: Ho-Oh / other legendaries whose NPC has script "0x0"
        # but whose actual battle is triggered by stepping on a coord tile
        for evt in map_data.get("coord_events", []):
            scr = evt.get("script", "")
            if not scr or scr in ("0x0", "0"):
                continue
            x = evt.get("x", 0)
            y = evt.get("y", 0)
            _try_add_encounter(_em_bfs_lines(scr, blocks), x, y)

        # map_script_2 directives: player's house Latias/Latios roamer trigger.
        # These are auto-cutscenes with no tile position, so x=None, y=None so
        # that no @ is placed on the map grid.
        for blk_lines in local_blocks.values():
            body = "\n".join(blk_lines)
            for ms_m in _EM_MAP_SCRIPT2_RE.finditer(body):
                tgt = ms_m.group(1)
                if tgt in blocks:
                    _try_add_encounter(_em_bfs_lines(tgt, blocks), None, None)

    return results


def _em_game_corner_prizes(tile_entry: dict) -> list[dict]:
    """Parse MauvilleCity_GameCorner/scripts.inc → coin prize list.

    Returns dicts with keys: name (str), coins (int), kind ("item"|"decoration").
    """
    map_names = tile_entry.get("maps", [])
    if "MauvilleCity_GameCorner" not in map_names:
        return []

    scripts_path = (_EM_ROOT / "data" / "maps" / "MauvilleCity_GameCorner"
                    / "scripts.inc")
    if not scripts_path.exists():
        return []

    text = scripts_path.read_text(encoding="utf-8")

    prizes: list[dict] = []

    # TMs: .set TM_DOUBLE_TEAM_COINS, 1500 → name from constant, cost from value
    # Constants are TM_<MOVE_NAME>_COINS.  Order of .set lines matches menu order.
    for m in re.finditer(r"\.set\s+TM_(\w+?)_COINS\s*,\s*(\d+)", text):
        move_name = m.group(1).replace("_", " ").title()
        cost = int(m.group(2))
        prizes.append({"name": f"TM {move_name}", "coins": cost, "kind": "item"})

    # Decoration dolls: DOLL_COINS value + bufferdecorationname STR_VAR_1, DECOR_X
    doll_m = re.search(r"\.set\s+DOLL_COINS\s*,\s*(\d+)", text)
    doll_cost = int(doll_m.group(1)) if doll_m else 1000
    seen_decor: set[str] = set()
    for m in re.finditer(r"bufferdecorationname\s+STR_VAR_1\s*,\s*(DECOR_\w+)", text):
        decor_const = m.group(1)
        if decor_const in seen_decor:
            continue
        seen_decor.add(decor_const)
        name = decor_const.removeprefix("DECOR_").replace("_", " ").title()
        prizes.append({"name": name, "coins": doll_cost, "kind": "decoration"})

    return prizes


def render_coin_prize_section(prizes: list) -> list[str]:
    if not prizes:
        return []
    SEP = "=" * 70
    lines = ["", SEP, "  Coin Prize Exchange  (Emerald)", SEP, ""]
    for p in prizes:
        coins_str = f"{p['coins']:,}"
        note = "  [decoration]" if p["kind"] == "decoration" else ""
        lines.append(f"  {p['name']:<30}  {coins_str} coins{note}")
    lines.append("")
    return lines


def render_static_encounter_section(encounters: list,
                                    overlapped: dict | None = None) -> list[str]:
    if not encounters:
        return []
    SEP = "=" * 70
    lines = ["", SEP, "  Static Encounter Index  (Emerald)", SEP, ""]
    overlapped = overlapped or {}

    from itertools import groupby
    map_names = [e.get("map_name", "") for e in encounters]
    multi_map = len(set(m for m in map_names if m)) > 1

    for map_name, group in groupby(encounters, key=lambda e: e.get("map_name", "")):
        if multi_map and map_name:
            lines.append(f"  [ {map_name} ]")
            lines.append("")
        for enc in group:
            species = enc["species"]
            level   = enc.get("level")
            tags    = enc.get("tags", [])
            pre_txt = enc.get("pre_battle_text") or ""

            lv_str  = f"  Lv {level}" if level is not None else ""
            tag_str = ("  " + "  ".join(f"[{t}]" for t in tags)) if tags else ""
            lines.append(f"  {species}{lv_str}{tag_str}")

            x = enc.get("x")
            y = enc.get("y")
            if x is not None and y is not None:
                ov = overlapped.get((x, y), " ")
                ov_note = f"  [@ overlaps {repr(ov)}]" if ov != " " else ""
                coord = f"  (col={x:>3}, row={y:>3}){ov_note}"
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


def render_secret_base_section(secret_bases: list) -> list[str]:
    if not secret_bases:
        return []
    SEP = "=" * 70
    lines = ["", SEP, "  Secret Base Index  (Emerald)", SEP, ""]

    from itertools import groupby
    map_names = [e.get("map_name", "") for e in secret_bases]
    multi_map = len(set(m for m in map_names if m)) > 1

    for map_name, group in groupby(secret_bases, key=lambda e: e.get("map_name", "")):
        if multi_map and map_name:
            lines.append(f"  [ {map_name} ]")
            lines.append("")
        for sb in group:
            x   = sb.get("x", 0)
            y   = sb.get("y", 0)
            sid = sb.get("secret_base_id", "")
            lines.append(f"  (col={x:>3}, row={y:>3})  {sid}")
        if multi_map:
            lines.append("")

    return lines
