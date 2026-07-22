#!/usr/bin/env python3
"""
Extract per-species Pokémon data from the pokeemerald, pokeheartgold, and
pokeplatinum decompilations and write them to JSON files.

Output JSON structure (one entry per species key, e.g. "BULBASAUR"):
{
  "BULBASAUR": {
    "name":        "Bulbasaur",          // display name
    "types":       ["GRASS", "POISON"],
    "base_stats":  {"hp":45,"atk":49,"def":49,"spe":45,"spa":65,"spd":65},
    "abilities":   ["Overgrow"],         // human-readable names (slot 0 first)
    "ability_keys":["ABILITY_OVERGROW"], // raw constants
    "wild_items":  {"common": null, "rare": null},
    "learnset":    [
      {"level": 1, "move": "TACKLE"},
      {"level": 4, "move": "GROWL"},
      ...
    ]
  },
  ...
}

Usage:
  python3 extract_pokemon_data.py                      # all three games
  python3 extract_pokemon_data.py --emerald            # Emerald only
  python3 extract_pokemon_data.py --heartgold          # HeartGold only
  python3 extract_pokemon_data.py --platinum           # Platinum only
  python3 extract_pokemon_data.py --out my_output.json # custom output path (single game only)
"""

import json
import re
import struct
import sys
from pathlib import Path

BASE_DIR  = Path(__file__).parent
EMERALD   = BASE_DIR / "pokeemerald"
HEARTGOLD = BASE_DIR / "pokeheartgold"
PLATINUM  = BASE_DIR / "pokeplatinum"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _strip_prefix(s: str, prefix: str) -> str:
    return s[len(prefix):] if s.startswith(prefix) else s


def _parse_constants_h(path: Path, prefix: str) -> dict:
    """
    Parse a C header with  #define PREFIX_FOO  <int>  lines.
    Returns {PREFIX_FOO: int_value, ...}
    """
    result = {}
    if not path.exists():
        return result
    text = _read(path)
    for m in re.finditer(rf'#define\s+({re.escape(prefix)}\w+)\s+(\d+)', text):
        result[m.group(1)] = int(m.group(2))
    return result


def _parse_indexed_txt(path: Path) -> list:
    """
    Parse a generated .txt file where each line is a constant name.
    Line 0 → index 0, line 1 → index 1, etc.
    Returns [const_name, ...]
    """
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def _parse_gmm_indexed(path: Path) -> dict:
    """
    Parse a HeartGold .gmm XML file where rows are indexed sequentially.
    Returns {index: english_text}
    """
    result = {}
    if not path.exists():
        return result
    text = _read(path)
    for m in re.finditer(
        r'<row[^>]+index="(\d+)"[^>]*>.*?<language name="English">([^<]*)</language>',
        text, re.DOTALL
    ):
        idx = int(m.group(1))
        val = m.group(2).strip()
        if val:
            result[idx] = val
    return result


def _parse_json_messages(path: Path) -> list:
    """
    Parse a Platinum text JSON file (list of {id, en_US} messages).
    Returns [en_US_string, ...] in order (index 0 = first message).
    """
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [m.get("en_US", "") for m in data.get("messages", [])]


# ===========================================================================
# ─────────────────────────────  EMERALD  ───────────────────────────────────
# ===========================================================================

def _emerald_ability_names() -> dict:
    """Returns {ABILITY_OVERGROW: "Overgrow", ...}"""
    path = EMERALD / "src" / "data" / "text" / "abilities.h"
    result = {}
    if not path.exists():
        print(f"  WARN: {path} not found", file=sys.stderr)
        return result
    text = _read(path)
    for m in re.finditer(r'\[(\w+)\]\s*=\s*_\("([^"]+)"\)', text):
        result[m.group(1)] = m.group(2)
    return result


def _emerald_item_names() -> dict:
    """Returns {ITEM_SITRUS_BERRY: "Sitrus Berry", ...}"""
    path = EMERALD / "src" / "data" / "items.h"
    result = {}
    if not path.exists():
        print(f"  WARN: {path} not found", file=sys.stderr)
        return result
    text = _read(path)
    block_re = re.compile(r'\[(\w+)\]\s*=\s*\{')
    pos = 0
    while True:
        m = block_re.search(text, pos)
        if not m:
            break
        const = m.group(1)
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
        name_m = re.search(r'\.name\s*=\s*_\("([^"]+)"\)', block)
        if name_m:
            result[const] = name_m.group(1)
    return result


def _emerald_species_info(ability_names: dict, item_names: dict) -> dict:
    path = EMERALD / "src" / "data" / "pokemon" / "species_info.h"
    result = {}
    if not path.exists():
        print(f"  WARN: {path} not found", file=sys.stderr)
        return result
    text = _read(path)

    block_re = re.compile(r'\[(\w+)\]\s*=\s*\{')
    pos = 0
    while True:
        m = block_re.search(text, pos)
        if not m:
            break
        const = m.group(1)
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

        if not const.startswith("SPECIES_"):
            continue
        species_key = _strip_prefix(const, "SPECIES_")

        def _stat(field):
            sm = re.search(rf'\.{field}\s*=\s*(\d+)', block)
            return int(sm.group(1)) if sm else 0

        base_stats = {
            "hp":  _stat("baseHP"),
            "atk": _stat("baseAttack"),
            "def": _stat("baseDefense"),
            "spe": _stat("baseSpeed"),
            "spa": _stat("baseSpAttack"),
            "spd": _stat("baseSpDefense"),
        }

        types = []
        types_m = re.search(r'\.types\s*=\s*\{([^}]+)\}', block)
        if types_m:
            for t in types_m.group(1).split(','):
                t = t.strip()
                if t:
                    types.append(_strip_prefix(t, "TYPE_"))

        ability_keys = []
        ability_display = []
        ab_m = re.search(r'\.abilities\s*=\s*\{([^}]+)\}', block)
        if ab_m:
            for a in ab_m.group(1).split(','):
                a = a.strip()
                if a and a != "ABILITY_NONE":
                    ability_keys.append(a)
                    ability_display.append(
                        ability_names.get(a, _strip_prefix(a, "ABILITY_"))
                    )

        def _item(field):
            im = re.search(rf'\.{field}\s*=\s*(\w+)', block)
            if not im:
                return None
            v = im.group(1)
            if v == "ITEM_NONE":
                return None
            return item_names.get(v, _strip_prefix(v, "ITEM_"))

        wild_items = {
            "common": _item("itemCommon"),
            "rare":   _item("itemRare"),
        }

        result[species_key] = {
            "types":        types,
            "base_stats":   base_stats,
            "abilities":    ability_display,
            "ability_keys": ability_keys,
            "wild_items":   wild_items,
        }

    return result


def _emerald_learnsets() -> dict:
    """Returns {SPECIES_KEY: [{"level": int, "move": str}, ...]}"""
    learnsets_path = EMERALD / "src" / "data" / "pokemon" / "level_up_learnsets.h"
    pointers_path  = EMERALD / "src" / "data" / "pokemon" / "level_up_learnset_pointers.h"

    if not learnsets_path.exists() or not pointers_path.exists():
        print("  WARN: Emerald learnset files not found", file=sys.stderr)
        return {}

    learnsets_text = _read(learnsets_path)
    pointers_text  = _read(pointers_path)

    array_re = re.compile(
        r'static\s+const\s+u16\s+(\w+LevelUpLearnset)\[\]\s*=\s*\{([^}]+)\}',
        re.DOTALL,
    )
    raw_learnsets = {}
    for m in array_re.finditer(learnsets_text):
        arr_name = m.group(1)
        body     = m.group(2)
        moves = []
        for entry in re.finditer(
            r'LEVEL_UP_MOVE\s*\(\s*(\d+)\s*,\s*(MOVE_\w+)\s*\)', body
        ):
            lvl  = int(entry.group(1))
            move = _strip_prefix(entry.group(2), "MOVE_")
            moves.append({"level": lvl, "move": move})
        raw_learnsets[arr_name] = moves

    result = {}
    for m in re.finditer(
        r'\[(\w+)\]\s*=\s*(\w+LevelUpLearnset)\s*,', pointers_text
    ):
        const    = m.group(1)
        arr_name = m.group(2)
        if not const.startswith("SPECIES_"):
            continue
        species_key = _strip_prefix(const, "SPECIES_")
        result[species_key] = raw_learnsets.get(arr_name, [])

    return result


def _emerald_species_names() -> dict:
    """Returns {SPECIES_KEY: "Display Name"}"""
    path = EMERALD / "src" / "data" / "text" / "species_names.h"
    result = {}
    if not path.exists():
        return result
    text = _read(path)
    for m in re.finditer(r'\[(\w+)\]\s*=\s*_\("([^"]+)"\)', text):
        const = m.group(1)
        if const.startswith("SPECIES_"):
            result[_strip_prefix(const, "SPECIES_")] = m.group(2)
    return result


def extract_emerald() -> dict:
    """Extract all Pokémon data for Emerald. Returns output dict."""
    print("\n=== Pokémon Emerald ===")

    print("  Parsing ability names...")
    ability_names = _emerald_ability_names()
    print(f"    {len(ability_names)} abilities")

    print("  Parsing item names...")
    item_names = _emerald_item_names()
    print(f"    {len(item_names)} items")

    print("  Parsing species info...")
    species_info = _emerald_species_info(ability_names, item_names)
    print(f"    {len(species_info)} species")

    print("  Parsing level-up learnsets...")
    learnsets = _emerald_learnsets()
    print(f"    {len(learnsets)} learnsets")

    print("  Parsing species names...")
    species_names = _emerald_species_names()

    all_keys = sorted(set(species_info) | set(learnsets))
    output = {}
    for key in all_keys:
        if key == "NONE":
            continue
        info = species_info.get(key, {})
        output[key] = {
            "name":        species_names.get(key, key),
            "types":       info.get("types", []),
            "base_stats":  info.get("base_stats", {}),
            "abilities":   info.get("abilities", []),
            "ability_keys":info.get("ability_keys", []),
            "wild_items":  info.get("wild_items", {"common": None, "rare": None}),
            "learnset":    learnsets.get(key, []),
        }
    return output


# ===========================================================================
# ─────────────────────────────  HEARTGOLD  ─────────────────────────────────
# ===========================================================================
#
# Data sources:
#   Species info + abilities + wild items:
#       files/poketool/personal/personal.json
#       → baseStats[].{species, hp, atk, def, speed, spatk, spdef,
#                       types[], items[], abilities[]}
#
#   Level-up learnsets:
#       files/poketool/personal/wotbl.narc  (NARC binary)
#       Each file = one species (index matches SPECIES_ constant value).
#       Each entry = u32: bits[8:0]=move_id, bits[15:9]=level; 0xFFFFFFFF=end.
#       Move IDs map to MOVE_ constants via include/constants/moves.h.
#
#   Ability names:
#       files/msgdata/msg/msg_0720.gmm  (indexed XML; index = ABILITY_ value)
#
#   Item names:
#       files/msgdata/msg/msg_0222.gmm  (indexed XML; index = ITEM_ value)
#
#   Move names (for learnset display):
#       files/msgdata/msg/msg_0750.gmm  (indexed XML; index = MOVE_ value)
#
#   Species names:
#       files/msgdata/msg/msg_0237.gmm  (indexed XML; index = SPECIES_ value)
#
#   Species constants (name → int):
#       include/constants/species.h   (#define SPECIES_BULBASAUR 1)
#
#   Move constants (name → int):
#       include/constants/moves.h     (#define MOVE_TACKLE 33)
#
#   Ability constants (name → int):
#       include/constants/abilities.h (#define ABILITY_OVERGROW 65)
#
#   Item constants (name → int):
#       include/constants/items.h     (#define ITEM_NONE 0)
# ===========================================================================

def _hg_parse_narc_files(path: Path) -> list:
    """
    Parse a NARC archive and return a list of raw bytes objects, one per file.
    NARC layout: NARC header → BTAF (file table) → BTNF (name table) → GMIF (data).
    """
    if not path.exists():
        return []
    data = path.read_bytes()

    # BTAF section starts at offset 0x10
    btaf_offset = 0x10
    btaf_size   = struct.unpack_from('<I', data, btaf_offset + 4)[0]
    num_files   = struct.unpack_from('<I', data, btaf_offset + 8)[0]

    # File offsets: each entry is (start_u32, end_u32) at btaf_offset+12
    offsets = []
    for i in range(num_files):
        start = struct.unpack_from('<I', data, btaf_offset + 12 + i * 8)[0]
        end   = struct.unpack_from('<I', data, btaf_offset + 12 + i * 8 + 4)[0]
        offsets.append((start, end))

    # BTNF follows BTAF; GMIF follows BTNF
    btnf_offset = btaf_offset + btaf_size
    btnf_size   = struct.unpack_from('<I', data, btnf_offset + 4)[0]
    gmif_offset = btnf_offset + btnf_size
    data_start  = gmif_offset + 8  # skip GMIF header

    files = []
    for start, end in offsets:
        files.append(data[data_start + start : data_start + end])
    return files


def _hg_parse_wotbl(move_id_to_const: dict) -> dict:
    """
    Parse wotbl.narc (level-up learnsets).
    Returns {species_index: [{"level": int, "move": str}, ...]}
    Each entry in the NARC is one species (index 0 = NONE, 1 = BULBASAUR, ...).
    Entry format: sequence of u32 values.
        bits[8:0]  = move ID
        bits[15:9] = level
        0xFFFFFFFF = terminator
    """
    narc_path = HEARTGOLD / "files" / "poketool" / "personal" / "wotbl.narc"
    files = _hg_parse_narc_files(narc_path)
    result = {}
    for species_idx, raw in enumerate(files):
        moves = []
        for i in range(0, len(raw) - 3, 4):
            val = struct.unpack_from('<I', raw, i)[0]
            if val == 0xFFFFFFFF:
                break
            move_id = val & 0x1FF
            level   = (val >> 9) & 0x7F
            move_const = move_id_to_const.get(move_id, f"MOVE_{move_id}")
            move_name  = _strip_prefix(move_const, "MOVE_")
            moves.append({"level": level, "move": move_name})
        result[species_idx] = moves
    return result


def extract_heartgold() -> dict:
    """Extract all Pokémon data for HeartGold. Returns output dict."""
    print("\n=== Pokémon HeartGold ===")

    # --- Ability names: msg_0720.gmm (index = ABILITY_ value) ---
    print("  Parsing ability names...")
    ability_idx = _parse_gmm_indexed(
        HEARTGOLD / "files" / "msgdata" / "msg" / "msg_0720.gmm"
    )
    # Also build ABILITY_CONST → name via include/constants/abilities.h
    ability_consts = _parse_constants_h(
        HEARTGOLD / "include" / "constants" / "abilities.h", "ABILITY_"
    )
    # ability_consts: {ABILITY_OVERGROW: 65, ...}  → invert to {65: ABILITY_OVERGROW}
    ability_idx_to_const = {v: k for k, v in ability_consts.items()}
    # Build ABILITY_CONST → display name
    ability_names: dict = {}
    for idx, name in ability_idx.items():
        const = ability_idx_to_const.get(idx)
        if const:
            ability_names[const] = name
    print(f"    {len(ability_names)} abilities")

    # --- Item names: msg_0222.gmm (index = ITEM_ value) ---
    print("  Parsing item names...")
    item_idx = _parse_gmm_indexed(
        HEARTGOLD / "files" / "msgdata" / "msg" / "msg_0222.gmm"
    )
    item_consts = _parse_constants_h(
        HEARTGOLD / "include" / "constants" / "items.h", "ITEM_"
    )
    item_idx_to_const = {v: k for k, v in item_consts.items()}
    item_names: dict = {}
    for idx, name in item_idx.items():
        const = item_idx_to_const.get(idx)
        if const:
            item_names[const] = name
    print(f"    {len(item_names)} items")

    # --- Move constants (for learnset decoding) ---
    move_consts = _parse_constants_h(
        HEARTGOLD / "include" / "constants" / "moves.h", "MOVE_"
    )
    move_id_to_const = {v: k for k, v in move_consts.items()}

    # --- Species constants ---
    species_consts = _parse_constants_h(
        HEARTGOLD / "include" / "constants" / "species.h", "SPECIES_"
    )
    # species_consts: {SPECIES_BULBASAUR: 1, ...}
    species_idx_to_key = {v: _strip_prefix(k, "SPECIES_") for k, v in species_consts.items()}

    # --- Species names: msg_0237.gmm ---
    print("  Parsing species names...")
    species_name_idx = _parse_gmm_indexed(
        HEARTGOLD / "files" / "msgdata" / "msg" / "msg_0237.gmm"
    )
    # Map species_key → display name
    species_names: dict = {}
    for idx, name in species_name_idx.items():
        key = species_idx_to_key.get(idx)
        if key and key != "NONE":
            species_names[key] = name

    # --- Species info: personal.json ---
    print("  Parsing species info...")
    personal_path = HEARTGOLD / "files" / "poketool" / "personal" / "personal.json"
    species_info: dict = {}
    if personal_path.exists():
        personal = json.loads(personal_path.read_text(encoding="utf-8"))
        for entry in personal.get("baseStats", []):
            species_const = entry.get("species", "")
            if not species_const or species_const == "NONE":
                continue
            species_key = _strip_prefix(species_const, "SPECIES_")

            base_stats = {
                "hp":  entry.get("hp", 0),
                "atk": entry.get("atk", 0),
                "def": entry.get("def", 0),
                "spe": entry.get("speed", 0),
                "spa": entry.get("spatk", 0),
                "spd": entry.get("spdef", 0),
            }

            types = [_strip_prefix(t, "TYPE_") for t in entry.get("types", [])]

            ability_keys = []
            ability_display = []
            for a in entry.get("abilities", []):
                if a and a != "ABILITY_NONE":
                    ability_keys.append(a)
                    ability_display.append(ability_names.get(a, _strip_prefix(a, "ABILITY_")))

            raw_items = entry.get("items", ["ITEM_NONE", "ITEM_NONE"])
            def _item_name(v):
                if not v or v == "ITEM_NONE":
                    return None
                return item_names.get(v, _strip_prefix(v, "ITEM_"))

            wild_items = {
                "common": _item_name(raw_items[0] if len(raw_items) > 0 else "ITEM_NONE"),
                "rare":   _item_name(raw_items[1] if len(raw_items) > 1 else "ITEM_NONE"),
            }

            species_info[species_key] = {
                "types":        types,
                "base_stats":   base_stats,
                "abilities":    ability_display,
                "ability_keys": ability_keys,
                "wild_items":   wild_items,
            }
    print(f"    {len(species_info)} species")

    # --- Level-up learnsets: wotbl.narc ---
    print("  Parsing level-up learnsets (wotbl.narc)...")
    wotbl_by_idx = _hg_parse_wotbl(move_id_to_const)
    learnsets: dict = {}
    for idx, moves in wotbl_by_idx.items():
        key = species_idx_to_key.get(idx)
        if key and key != "NONE":
            learnsets[key] = moves
    print(f"    {len(learnsets)} learnsets")

    # --- Assemble output ---
    all_keys = sorted(set(species_info) | set(learnsets))
    output = {}
    for key in all_keys:
        if key == "NONE":
            continue
        info = species_info.get(key, {})
        output[key] = {
            "name":        species_names.get(key, key),
            "types":       info.get("types", []),
            "base_stats":  info.get("base_stats", {}),
            "abilities":   info.get("abilities", []),
            "ability_keys":info.get("ability_keys", []),
            "wild_items":  info.get("wild_items", {"common": None, "rare": None}),
            "learnset":    learnsets.get(key, []),
        }
    return output


# ===========================================================================
# ─────────────────────────────  PLATINUM  ──────────────────────────────────
# ===========================================================================
#
# Data sources:
#   Species info + abilities + wild items + learnsets:
#       res/pokemon/<species_dir>/data.json
#       → {base_stats, types, abilities, held_items, learnset.by_level}
#       Species directory names are lowercase species names (e.g. "bulbasaur").
#       The SPECIES_ constant order is in generated/species.txt (one per line,
#       index 0 = SPECIES_NONE, index 1 = SPECIES_BULBASAUR, ...).
#
#   Ability names:
#       res/text/ability_names.json  (messages[i].en_US, index = ABILITY_ value)
#       ABILITY_ constant order: generated/abilities.txt
#
#   Item names:
#       res/text/item_names.json     (messages[i].en_US, index = ITEM_ value)
#       ITEM_ constant order: generated/items.txt
#
#   Move names (for learnset display):
#       res/text/move_names.json     (messages[i].en_US, index = MOVE_ value)
#       MOVE_ constant order: generated/moves.txt
#
#   Species display names:
#       Embedded in each res/pokemon/<dir>/data.json under
#       pokedex_data.en.name  (English display name).
#
#   Trainer class names:
#       res/text/trainer_class_names.json  (messages[i].en_US)
#       TRAINER_CLASS_ constant order: generated/trainer_classes.txt
# ===========================================================================

def _pt_build_const_to_name(generated_txt: Path, names_json: Path) -> dict:
    """
    Build {CONST_NAME: display_name} by pairing generated/<file>.txt (index order)
    with res/text/<file>.json (messages in index order).
    Returns {const_name: display_name}
    """
    consts = _parse_indexed_txt(generated_txt)
    names  = _parse_json_messages(names_json)
    result = {}
    for i, const in enumerate(consts):
        if i < len(names) and names[i]:
            result[const] = names[i]
    return result


def _pt_dir_to_species_key(dirname: str) -> str:
    """
    Convert a Platinum pokemon directory name to a SPECIES_ key.
    e.g. "bulbasaur" → "BULBASAUR", "mr_mime" → "MR_MIME", "ho_oh" → "HO_OH"
    """
    return dirname.upper()


def extract_platinum() -> dict:
    """Extract all Pokémon data for Platinum. Returns output dict."""
    print("\n=== Pokémon Platinum ===")

    # --- Ability names ---
    print("  Parsing ability names...")
    ability_map = _pt_build_const_to_name(
        PLATINUM / "generated" / "abilities.txt",
        PLATINUM / "res" / "text" / "ability_names.json",
    )
    print(f"    {len(ability_map)} abilities")

    # --- Item names ---
    print("  Parsing item names...")
    item_map = _pt_build_const_to_name(
        PLATINUM / "generated" / "items.txt",
        PLATINUM / "res" / "text" / "item_names.json",
    )
    print(f"    {len(item_map)} items")

    # --- Species order (for consistent key lookup) ---
    species_list = _parse_indexed_txt(PLATINUM / "generated" / "species.txt")
    # species_list[i] = "SPECIES_BULBASAUR" etc.

    # --- Per-species data.json files ---
    print("  Parsing species data (per-pokemon data.json)...")
    pokemon_dir = PLATINUM / "res" / "pokemon"
    species_info: dict = {}
    learnsets:    dict = {}
    species_names: dict = {}

    for entry_dir in sorted(pokemon_dir.iterdir()):
        if not entry_dir.is_dir():
            continue
        data_file = entry_dir / "data.json"
        if not data_file.exists():
            continue

        species_key = _pt_dir_to_species_key(entry_dir.name)
        if species_key in ("NONE", "BAD_EGG"):
            continue

        try:
            pdata = json.loads(data_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        # Display name from pokedex_data.en.name
        display_name = pdata.get("pokedex_data", {}).get("en", {}).get("name", species_key)
        species_names[species_key] = display_name

        # Base stats
        bs = pdata.get("base_stats", {})
        base_stats = {
            "hp":  bs.get("hp", 0),
            "atk": bs.get("attack", 0),
            "def": bs.get("defense", 0),
            "spe": bs.get("speed", 0),
            "spa": bs.get("special_attack", 0),
            "spd": bs.get("special_defense", 0),
        }

        # Types
        types = [_strip_prefix(t, "TYPE_") for t in pdata.get("types", [])]

        # Abilities
        ability_keys = []
        ability_display = []
        for a in pdata.get("abilities", []):
            if a and a != "ABILITY_NONE":
                ability_keys.append(a)
                ability_display.append(ability_map.get(a, _strip_prefix(a, "ABILITY_")))

        # Wild held items
        held = pdata.get("held_items", {})
        def _pt_item(v):
            if not v or v == "ITEM_NONE":
                return None
            return item_map.get(v, _strip_prefix(v, "ITEM_"))

        wild_items = {
            "common": _pt_item(held.get("common")),
            "rare":   _pt_item(held.get("rare")),
        }

        species_info[species_key] = {
            "types":        types,
            "base_stats":   base_stats,
            "abilities":    ability_display,
            "ability_keys": ability_keys,
            "wild_items":   wild_items,
        }

        # Level-up learnset: [[level, "MOVE_XXX"], ...]
        by_level = pdata.get("learnset", {}).get("by_level", [])
        moves = []
        for entry in by_level:
            if len(entry) == 2:
                lvl, move_const = entry
                moves.append({"level": int(lvl), "move": _strip_prefix(move_const, "MOVE_")})
        learnsets[species_key] = moves

    print(f"    {len(species_info)} species")
    print(f"    {len(learnsets)} learnsets")

    # --- Assemble output ---
    all_keys = sorted(set(species_info) | set(learnsets))
    output = {}
    for key in all_keys:
        if key == "NONE":
            continue
        info = species_info.get(key, {})
        output[key] = {
            "name":        species_names.get(key, key),
            "types":       info.get("types", []),
            "base_stats":  info.get("base_stats", {}),
            "abilities":   info.get("abilities", []),
            "ability_keys":info.get("ability_keys", []),
            "wild_items":  info.get("wild_items", {"common": None, "rare": None}),
            "learnset":    learnsets.get(key, []),
        }
    return output


# ===========================================================================
# ─────────────────────────────  MAIN  ──────────────────────────────────────
# ===========================================================================

def main():
    args = sys.argv[1:]

    do_emerald   = "--emerald"   in args or not any(
        a in args for a in ("--emerald", "--heartgold", "--platinum")
    )
    do_heartgold = "--heartgold" in args or not any(
        a in args for a in ("--emerald", "--heartgold", "--platinum")
    )
    do_platinum  = "--platinum"  in args or not any(
        a in args for a in ("--emerald", "--heartgold", "--platinum")
    )

    # Custom output path only makes sense when a single game is selected
    custom_out = None
    for i, arg in enumerate(args):
        if arg.startswith("--out="):
            custom_out = Path(arg.split("=", 1)[1])
        elif arg == "--out" and i + 1 < len(args):
            custom_out = Path(args[i + 1])

    tasks = []
    if do_emerald:
        tasks.append(("emerald", extract_emerald, BASE_DIR / "pokemon_emerald.json"))
    if do_heartgold:
        tasks.append(("heartgold", extract_heartgold, BASE_DIR / "pokemon_heartgold.json"))
    if do_platinum:
        tasks.append(("platinum", extract_platinum, BASE_DIR / "pokemon_platinum.json"))

    if len(tasks) == 1 and custom_out:
        tasks[0] = (tasks[0][0], tasks[0][1], custom_out)

    for game, extractor, out_path in tasks:
        data = extractor()
        print(f"\nWriting {out_path} ({len(data)} entries)...")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Done → {out_path}")


if __name__ == "__main__":
    main()
