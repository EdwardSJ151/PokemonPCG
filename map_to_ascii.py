#!/usr/bin/env python3
"""
Render any map from pokeemerald, pokeheartgold, or pokeplatinum as an ASCII
text file.

Usage:
  python3 map_to_ascii.py <game> <map_name_or_constant> [output.txt]

  game:  emerald | heartgold | platinum

Examples:
  python3 map_to_ascii.py emerald LittlerootTown
  python3 map_to_ascii.py heartgold MAP_VIOLET
  python3 map_to_ascii.py platinum MAP_HEADER_JUBILIFE_CITY
  python3 map_to_ascii.py platinum jubilife_city

  # Render ALL maps for a game into a folder
  python3 map_to_ascii.py emerald --all
  python3 map_to_ascii.py heartgold --all
  python3 map_to_ascii.py platinum --all
"""

import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Symbol table
# ---------------------------------------------------------------------------
# Priority: higher number = drawn on top when two events share a cell.
# Symbol, priority, description

SYMBOLS = {
    # Warps / doors
    "warp":        ("W", 10, "Warp/door"),
    # NPCs by trainer type
    "trainer":     ("T", 20, "Trainer NPC"),
    "npc":         ("N", 15, "NPC"),
    # Items
    "item":        ("I", 25, "Item ball"),
    # Signs / boards
    "sign":        ("S",  8, "Sign/board"),
    # Coord triggers
    "trigger":     ("^",  5, "Coord trigger"),
    # BG events (hidden items, notices)
    "bg":          ("B",  6, "BG event"),
    # Fallback
    "unknown":     ("?",  1, "Unknown"),
}

# Patterns in graphics_id / spriteId that map to symbol keys
GFX_RULES = [
    (re.compile(r"ITEM|BALL|POKEBALL", re.I),          "item"),
    (re.compile(r"SIGN|BOARD|NOTICE|POSTER", re.I),    "sign"),
    (re.compile(r"TRAINER|ACE|SCHOOL|EXPERT|TWIN|"
                r"CLOWN|COLLECTOR|LOOKER|GRUNT|RIVAL|"
                r"LEADER|CHAMPION|RANGER|HIKER|LASS|"
                r"YOUNGSTER|BEAUTY|BIKER|SAILOR|BIRD|"
                r"FIREBREATHER|ROCKER|JUGGLER|TAMER|"
                r"GENTLEMAN|COOLTRAINER|SWIMMER|RUIN|"
                r"PSYCHIC|MEDIUM|KIMONO|SAGE|ELDER|"
                r"POKEMON_TRAINER|PROF", re.I),         "trainer"),
]


def classify_gfx(gfx_id: str) -> str:
    for pattern, key in GFX_RULES:
        if pattern.search(gfx_id):
            return key
    return "npc"


# ---------------------------------------------------------------------------
# Coordinate normalisation helpers
# ---------------------------------------------------------------------------

def events_emerald(map_data: dict):
    """
    Yield (col, row, symbol_key, label) for every event in a pokeemerald map.
    Axes: x = column, y = row (both 0-based tile coords).
    """
    for e in map_data.get("object_events", []):
        gfx = e.get("graphics_id", "")
        trainer_type = e.get("trainer_type", "TRAINER_TYPE_NONE")
        if trainer_type != "TRAINER_TYPE_NONE":
            key = "trainer"
        else:
            key = classify_gfx(gfx)
        label = gfx.replace("OBJ_EVENT_GFX_", "")
        yield e["x"], e["y"], key, label

    for e in map_data.get("warp_events", []):
        dest = e.get("dest_map", "?")
        yield e["x"], e["y"], "warp", dest

    for e in map_data.get("coord_events", []):
        yield e["x"], e["y"], "trigger", e.get("script", "")

    for e in map_data.get("bg_events", []):
        yield e["x"], e["y"], "bg", e.get("script", "")


def events_heartgold(map_data: dict):
    """
    Yield (col, row, symbol_key, label) for every event in a pokeheartgold map.
    Axes: x = column, z = row (world-space tiles; we normalise to 0-based).
    """
    for e in map_data.get("objects", []):
        gfx = e.get("spriteId", "")
        # type 1 = trainer
        if e.get("type") == 1:
            key = "trainer"
        else:
            key = classify_gfx(gfx)
        label = gfx.replace("SPRITE_", "")
        yield e["x"], e["z"], key, label

    for e in map_data.get("warps", []):
        dest = e.get("header", "?")
        yield e["x"], e["z"], "warp", dest

    for e in map_data.get("coords", []):
        yield e["x"], e["z"], "trigger", str(e.get("scriptId", ""))

    for e in map_data.get("bgs", []):
        yield e["x"], e["z"], "bg", str(e.get("scriptId", ""))


def events_platinum(map_data: dict):
    """
    Yield (col, row, symbol_key, label) for every event in a pokeplatinum map.
    Axes: x = column, z = row.
    """
    for e in map_data.get("object_events", []):
        gfx = e.get("graphics_id", "")
        trainer_type = e.get("trainer_type", "TRAINER_TYPE_NONE")
        if trainer_type != "TRAINER_TYPE_NONE":
            key = "trainer"
        else:
            key = classify_gfx(gfx)
        label = gfx.replace("OBJ_EVENT_GFX_", "")
        yield e["x"], e["z"], key, label

    for e in map_data.get("warp_events", []):
        dest = e.get("dest_header_id", "?")
        yield e["x"], e["z"], "warp", dest

    for e in map_data.get("coord_events", []):
        yield e["x"], e["z"], "trigger", str(e.get("script", ""))

    for e in map_data.get("bg_events", []):
        yield e["x"], e["z"], "bg", str(e.get("script", ""))


EVENT_EXTRACTORS = {
    "emerald":   events_emerald,
    "heartgold": events_heartgold,
    "platinum":  events_platinum,
}

# ---------------------------------------------------------------------------
# ASCII grid renderer
# ---------------------------------------------------------------------------

def render_map(map_data: dict, game: str, max_width: int = 120) -> str:
    """
    Render a single map as an ASCII string.
    Returns the full text block (header + grid + legend).
    """
    extractor = EVENT_EXTRACTORS[game]

    # Collect all events
    raw_events = list(extractor(map_data))
    if not raw_events:
        roster = build_trainer_roster(map_data, game)
        text = _header(map_data, game) + "\n  (no events)\n"
        if roster:
            text += render_trainer_roster(roster)
        return text

    cols = [e[0] for e in raw_events]
    rows = [e[1] for e in raw_events]
    min_col, max_col = min(cols), max(cols)
    min_row, max_row = min(rows), max(rows)

    width  = max_col - min_col + 1
    height = max_row - min_row + 1

    # Scale down if the map is very large (keep it readable in a terminal)
    scale = 1
    if width > max_width:
        scale = (width + max_width - 1) // max_width

    grid_w = (width  + scale - 1) // scale
    grid_h = (height + scale - 1) // scale

    # grid[row][col] = (priority, symbol_char, label)
    grid = [[(".", 0, "")] * grid_w for _ in range(grid_h)]

    legend_entries = {}  # symbol_key -> set of labels

    for (col, row, key, label) in raw_events:
        gc = (col - min_col) // scale
        gr = (row - min_row) // scale
        sym_char, priority, _ = SYMBOLS[key]
        cur_prio = grid[gr][gc][1]
        if priority > cur_prio:
            grid[gr][gc] = (sym_char, priority, label)
        legend_entries.setdefault(key, set()).add(label)

    # Build the text
    lines = []
    lines.append(_header(map_data, game))

    # Column ruler every 5 cells
    ruler_top = "    "
    for gc in range(grid_w):
        actual_col = min_col + gc * scale
        if actual_col % 5 == 0:
            ruler_top += str(actual_col % 100).ljust(5)[:1]
        else:
            ruler_top += " "
    lines.append(ruler_top)

    # Grid rows
    for gr in range(grid_h):
        actual_row = min_row + gr * scale
        row_label = f"{actual_row:4d} "
        row_str = "".join(cell[0] for cell in grid[gr])
        lines.append(row_label + row_str)

    lines.append("")

    # Legend
    lines.append("  Legend:")
    lines.append(f"  {'Symbol':<8} {'Type':<12} Examples")
    lines.append("  " + "-" * 60)
    for key, (sym_char, _, desc) in SYMBOLS.items():
        if key in legend_entries:
            examples = sorted(legend_entries[key])[:5]
            ex_str = ", ".join(examples)
            if len(legend_entries[key]) > 5:
                ex_str += f"  (+{len(legend_entries[key])-5} more)"
            lines.append(f"  {sym_char:<8} {desc:<12} {ex_str}")

    if scale > 1:
        lines.append(f"\n  [Scale: 1 cell = {scale} tiles]")

    lines.append("")

    # Trainer battle roster (emerald only)
    roster = build_trainer_roster(map_data, game)
    if roster:
        lines.append(render_trainer_roster(roster))

    return "\n".join(lines)


def _header(map_data: dict, game: str) -> str:
    name     = map_data.get("name") or map_data.get("map_name") or "?"
    constant = map_data.get("constant") or map_data.get("id") or ""
    music    = (map_data.get("dayMusicID") or map_data.get("music") or "?")
    weather  = map_data.get("weather", "?")
    map_type = map_data.get("mapType") or map_data.get("map_type") or "?"

    lines = [
        "=" * 70,
        f"  {name}",
        f"  Constant : {constant}",
        f"  Game     : {game}",
        f"  Music    : {music}",
        f"  Weather  : {weather}",
        f"  Map type : {map_type}",
        "=" * 70,
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Trainer battle info  (pokeemerald only for now)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_pokemon_data() -> dict:
    """
    Load pokemon_emerald.json if present.
    Returns {SPECIES_KEY: {learnset, abilities, types, ...}} or {}.
    Run extract_pokemon_data.py first to generate this file.
    """
    path = BASE_DIR / "pokemon_emerald.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _default_moves_at_level(species_key: str, level: int) -> list[str]:
    """
    Return the moves a Pokémon knows at the given level by level-up learnset.
    Pokémon can know at most 4 moves; later moves overwrite earlier ones.
    """
    pdata = _load_pokemon_data()
    entry = pdata.get(species_key)
    if not entry:
        return []
    learnset = entry.get("learnset", [])
    # Collect all moves learned at or below `level`, in order
    known: list[str] = []
    for lm in learnset:
        if lm["level"] <= level:
            move = lm["move"]
            if move in known:
                known.remove(move)
            known.append(move)
            if len(known) > 4:
                known.pop(0)
    return known


@lru_cache(maxsize=1)
def _load_trainer_class_names() -> dict:
    """
    Parse pokeemerald/src/data/text/trainer_class_names.h
    Returns {TRAINER_CLASS_FOO: "Foo Name"}.
    """
    path = BASE_DIR / "pokeemerald" / "src" / "data" / "text" / "trainer_class_names.h"
    result = {}
    if not path.exists():
        return result
    text = path.read_text(encoding="utf-8", errors="replace")
    # [TRAINER_CLASS_YOUNGSTER] = _("YOUNGSTER"),
    for m in re.finditer(r'\[(\w+)\]\s*=\s*_\("([^"]+)"\)', text):
        result[m.group(1)] = m.group(2)
    return result


@lru_cache(maxsize=1)
def _load_trainers() -> dict:
    """
    Parse pokeemerald/src/data/trainers.h
    Returns {TRAINER_CONST: {"name": str, "class": str, "party": str, "double": bool}}.
    """
    path = BASE_DIR / "pokeemerald" / "src" / "data" / "trainers.h"
    result = {}
    if not path.exists():
        return result
    text = path.read_text(encoding="utf-8", errors="replace")

    # Split on trainer blocks: [TRAINER_FOO] = { ... },
    # We scan for [TRAINER_XXX] = then collect lines until the matching closing },
    block_re = re.compile(r'\[(\w+)\]\s*=\s*\{')
    pos = 0
    while True:
        m = block_re.search(text, pos)
        if not m:
            break
        const = m.group(1)
        # Find the closing "}," for this block by counting braces
        start = m.end()
        depth = 1
        i = start
        while i < len(text) and depth > 0:
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
            i += 1
        block = text[start:i]
        pos = i

        # Extract fields
        name_m   = re.search(r'\.trainerName\s*=\s*_\("([^"]+)"\)', block)
        class_m  = re.search(r'\.trainerClass\s*=\s*(\w+)', block)
        double_m = re.search(r'\.doubleBattle\s*=\s*(TRUE|FALSE)', block)
        # Party macro: NO_ITEM_DEFAULT_MOVES(sParty_Foo)
        #              NO_ITEM_CUSTOM_MOVES(sParty_Foo)
        #              ITEM_DEFAULT_MOVES(sParty_Foo)
        #              ITEM_CUSTOM_MOVES(sParty_Foo)
        party_m  = re.search(
            r'\.(party)\s*=\s*(?:NO_ITEM_DEFAULT_MOVES|NO_ITEM_CUSTOM_MOVES'
            r'|ITEM_DEFAULT_MOVES|ITEM_CUSTOM_MOVES)\((\w+)\)',
            block,
        )
        # Also capture the macro name so we know if moves are custom
        party_macro_m = re.search(
            r'\.party\s*=\s*(NO_ITEM_DEFAULT_MOVES|NO_ITEM_CUSTOM_MOVES'
            r'|ITEM_DEFAULT_MOVES|ITEM_CUSTOM_MOVES)\((\w+)\)',
            block,
        )

        result[const] = {
            "name":        name_m.group(1)  if name_m  else "?",
            "class":       class_m.group(1) if class_m else "?",
            "party":       party_macro_m.group(2) if party_macro_m else None,
            "party_macro": party_macro_m.group(1) if party_macro_m else None,
            "double":      double_m and double_m.group(1) == "TRUE",
        }
    return result


@lru_cache(maxsize=1)
def _load_trainer_parties() -> dict:
    """
    Parse pokeemerald/src/data/trainer_parties.h
    Returns {sParty_Foo: [{"species": str, "lvl": int, "moves": list, "item": str}]}.
    """
    path = BASE_DIR / "pokeemerald" / "src" / "data" / "trainer_parties.h"
    result = {}
    if not path.exists():
        return result
    text = path.read_text(encoding="utf-8", errors="replace")

    # Match: static const struct TrainerMonXxx sParty_Foo[] = { ... };
    decl_re = re.compile(
        r'static\s+const\s+struct\s+(TrainerMon\w+)\s+(\w+)\[\]\s*=\s*\{'
    )
    pos = 0
    while True:
        m = decl_re.search(text, pos)
        if not m:
            break
        struct_type = m.group(1)
        party_name  = m.group(2)
        start = m.end()
        # Find the closing }; by counting braces
        depth = 1
        i = start
        while i < len(text) and depth > 0:
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
            i += 1
        block = text[start:i]
        pos = i

        # Parse individual mon entries (each is a { ... } sub-block).
        # Entries may contain nested braces for .moves = {MOVE_X, ...},
        # so we scan manually for top-level { } pairs.
        mons = []
        entry_starts = []
        depth2 = 0
        entry_start = -1
        for ci, ch in enumerate(block):
            if ch == '{':
                if depth2 == 0:
                    entry_start = ci + 1
                depth2 += 1
            elif ch == '}':
                depth2 -= 1
                if depth2 == 0 and entry_start >= 0:
                    entry_starts.append(block[entry_start:ci])
                    entry_start = -1

        for entry in entry_starts:
            species_m = re.search(r'\.species\s*=\s*(\w+)', entry)
            lvl_m     = re.search(r'\.lvl\s*=\s*(\d+)', entry)
            item_m    = re.search(r'\.heldItem\s*=\s*(\w+)', entry)
            moves_m   = re.search(
                r'\.moves\s*=\s*\{([^}]+)\}', entry
            )
            species = species_m.group(1) if species_m else "?"
            # Strip SPECIES_ prefix for readability
            species = re.sub(r'^SPECIES_', '', species)
            lvl     = int(lvl_m.group(1)) if lvl_m else 0
            item    = item_m.group(1) if item_m else None
            if item:
                item = re.sub(r'^ITEM_', '', item)
                if item == "NONE":
                    item = None
            moves = []
            if moves_m:
                raw_moves = [mv.strip() for mv in moves_m.group(1).split(',')]
                moves = [
                    re.sub(r'^MOVE_', '', mv)
                    for mv in raw_moves
                    if mv and mv.strip() != "MOVE_NONE"
                ]
            mons.append({
                "species": species,
                "lvl":     lvl,
                "moves":   moves,
                "item":    item,
            })
        result[party_name] = mons
    return result


def _get_trainer_const_from_script(scripts_inc_path: Path, script_label: str) -> str | None:
    """
    Given a scripts.inc path and a script label (e.g. 'Route102_EventScript_Calvin'),
    find the first trainerbattle_* line inside that label's block and return the
    TRAINER_XXX constant.
    """
    if not scripts_inc_path.exists():
        return None
    text = scripts_inc_path.read_text(encoding="utf-8", errors="replace")

    # Find the label
    label_re = re.compile(
        re.escape(script_label) + r'::\s*\n(.*?)(?=\n\w|\Z)',
        re.DOTALL,
    )
    m = label_re.search(text)
    if not m:
        return None
    block = m.group(1)

    # trainerbattle_single TRAINER_FOO, ...
    tb_m = re.search(
        r'trainerbattle(?:_\w+)?\s+(TRAINER_\w+)',
        block,
    )
    return tb_m.group(1) if tb_m else None


def build_trainer_roster(map_data: dict, game: str) -> list:
    """
    For a pokeemerald map, return a list of trainer dicts:
      {
        "name": str, "class_name": str, "trainer_const": str,
        "x": int, "y": int, "sight": int,
        "double": bool,
        "party": [{"species", "lvl", "moves", "item"}],
      }
    Returns [] for non-emerald games or maps with no trainers.
    """
    if game != "emerald":
        return []

    trainers_db    = _load_trainers()
    parties_db     = _load_trainer_parties()
    class_names_db = _load_trainer_class_names()

    # Locate the map's scripts.inc
    map_name = map_data.get("map_name") or map_data.get("name") or ""
    scripts_path = BASE_DIR / "pokeemerald" / "data" / "maps" / map_name / "scripts.inc"

    roster = []
    for obj in map_data.get("object_events", []):
        if obj.get("trainer_type", "TRAINER_TYPE_NONE") == "TRAINER_TYPE_NONE":
            continue

        script_label = obj.get("script", "")
        sight = int(obj.get("trainer_sight_or_berry_tree_id", 0) or 0)
        x, y = obj.get("x", 0), obj.get("y", 0)

        # Resolve TRAINER_CONST from the script label
        trainer_const = _get_trainer_const_from_script(scripts_path, script_label)
        if trainer_const is None:
            # Fallback: use script label as identifier
            roster.append({
                "name": script_label,
                "class_name": "?",
                "trainer_const": "?",
                "x": x, "y": y, "sight": sight,
                "double": False,
                "party": [],
            })
            continue

        tdata = trainers_db.get(trainer_const, {})
        party_name = tdata.get("party")
        party = parties_db.get(party_name, []) if party_name else []

        class_const = tdata.get("class", "?")
        class_name  = class_names_db.get(class_const, class_const.replace("TRAINER_CLASS_", ""))

        roster.append({
            "name":          tdata.get("name", "?"),
            "class_name":    class_name,
            "trainer_const": trainer_const,
            "x": x, "y": y, "sight": sight,
            "double":        tdata.get("double", False),
            "party":         party,
        })

    return roster


def render_trainer_roster(roster: list) -> str:
    """
    Render the trainer roster as a formatted text block.

    Ability:   Always slot 0 — trainer Pokémon in Emerald are created with a
               personality value whose low bit is always 0 (base 0x78/0x80/0x88,
               nameHash shifted into upper bytes only), so ability slot 1 is
               never selected.
    Held item: Only present when the trainer party uses ITEM_DEFAULT_MOVES or
               ITEM_CUSTOM_MOVES macros; NO_ITEM_* macros never assign one.
    Moves:     Custom moves shown as-is; default moves resolved from the
               level-up learnset in pokemon_emerald.json.
    """
    if not roster:
        return ""

    pdata = _load_pokemon_data()

    lines = [
        "",
        "=" * 70,
        "  Trainer Battle Roster",
        "=" * 70,
        "",
    ]

    for t in roster:
        battle_type = "Double" if t["double"] else "Single"
        lines.append(
            f"  [T] {t['name']}  ({t['class_name']})  "
            f"@ ({t['x']}, {t['y']})  "
            f"sight: {t['sight']}  [{battle_type}]"
        )
        lines.append(f"      Const: {t['trainer_const']}")

        if t["party"]:
            for i, mon in enumerate(t["party"], 1):
                species  = mon["species"]
                lvl      = mon["lvl"]

                sp_entry = pdata.get(species, {})
                types    = sp_entry.get("types", [])
                all_abs  = sp_entry.get("abilities", [])

                # Deduplicate mono-types (e.g. DARK/DARK → DARK)
                unique_types = list(dict.fromkeys(t2 for t2 in types if t2))
                type_str     = "/".join(unique_types) if unique_types else "?"

                # Ability: trainer Pokémon always use slot 0 (personality & 1 == 0
                # for every trainer in Emerald — the base PV is 0x78/0x80/0x88,
                # and nameHash is shifted into the upper bytes, never touching bit 0).
                ability = all_abs[0] if all_abs else "?"

                # Held item: only present when the party macro is ITEM_DEFAULT_MOVES
                # or ITEM_CUSTOM_MOVES (the game calls SetMonData MON_DATA_HELD_ITEM
                # only for those cases). NO_ITEM_* macros never give a held item.
                held_item = mon["item"]  # None when NO_ITEM_* macro was used

                # Species display name
                display_name = sp_entry.get("name", species) if sp_entry else species

                # Resolve moves
                if mon["moves"]:
                    moves_label = "[" + ", ".join(mon["moves"]) + "]  (custom)"
                else:
                    default_moves = _default_moves_at_level(species, lvl)
                    if default_moves:
                        moves_label = "[" + ", ".join(default_moves) + "]  (default)"
                    else:
                        moves_label = "[default moves]"

                # Header line: name / level / type / ability / held item
                item_part = f"  @ {held_item}" if held_item else ""
                lines.append(
                    f"        #{i}  {display_name:<16} Lv.{lvl:<3}"
                    f"  {type_str:<14}  Ability: {ability}{item_part}"
                )
                lines.append(f"             Moves: {moves_label}")
        else:
            lines.append("        (party data not found)")

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Map lookup helpers
# ---------------------------------------------------------------------------

def load_maps(game: str) -> list:
    path = BASE_DIR / f"maps_poke{game}.json"
    if not path.exists():
        sys.exit(f"ERROR: {path} not found. Run extract_maps.py first.")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_map(maps: list, query: str, game: str) -> dict | None:
    """
    Find a map by:
      - exact constant match  (MAP_HEADER_JUBILIFE_CITY)
      - exact map_name match  (LittlerootTown)
      - case-insensitive partial match on name/constant
      - slug match            (jubilife_city -> MAP_HEADER_JUBILIFE_CITY)
    """
    q = query.strip()
    q_upper = q.upper()

    # Determine the constant field name per game
    const_field = {
        "emerald":   "id",
        "heartgold": "constant",
        "platinum":  "constant",
    }[game]
    name_field = {
        "emerald":   "map_name",
        "heartgold": "name",
        "platinum":  "name",
    }[game]

    # 1. Exact constant
    for m in maps:
        if (m.get(const_field) or "").upper() == q_upper:
            return m

    # 2. Exact map_name / name
    for m in maps:
        if (m.get(name_field) or "").upper() == q_upper:
            return m

    # 3. Slug: "jubilife_city" -> "MAP_HEADER_JUBILIFE_CITY" or "MAP_JUBILIFE_CITY"
    slug = q_upper.replace("-", "_")
    for prefix in ("MAP_HEADER_", "MAP_"):
        candidate = prefix + slug
        for m in maps:
            if (m.get(const_field) or "").upper() == candidate:
                return m

    # 4. Partial case-insensitive match on constant or name
    q_lower = q.lower()
    for m in maps:
        if q_lower in (m.get(const_field) or "").lower():
            return m
        if q_lower in (m.get(name_field) or "").lower():
            return m

    return None


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

    maps = load_maps(game)

    # --all mode: render every map into a folder
    if args[1] == "--all":
        out_dir = BASE_DIR / f"ascii_maps_{game}"
        out_dir.mkdir(exist_ok=True)
        print(f"Rendering {len(maps)} maps to {out_dir}/...")
        for i, m in enumerate(maps):
            const_field = {"emerald": "id", "heartgold": "constant", "platinum": "constant"}[game]
            name_field  = {"emerald": "map_name", "heartgold": "name", "platinum": "name"}[game]
            fname = safe_filename(m.get(const_field) or m.get(name_field) or f"map_{i:04d}")
            out_path = out_dir / f"{fname}.txt"
            text = render_map(m, game)
            out_path.write_text(text, encoding="utf-8")
        print("Done.")
        return

    # Single map mode
    query = args[1]
    result = find_map(maps, query, game)
    if result is None:
        sys.exit(f"Map '{query}' not found in {game}.")

    text = render_map(result, game)

    if len(args) >= 3:
        out_path = Path(args[2])
        out_path.write_text(text, encoding="utf-8")
        print(f"Written to {out_path}")
    else:
        print(text)


if __name__ == "__main__":
    main()
