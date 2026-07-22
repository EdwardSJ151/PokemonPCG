#!/usr/bin/env python3
"""
Extract all map data from pokeemerald, pokeheartgold, and pokeplatinum decomps
into unified JSON files.

Output:
  maps_pokeemerald.json
  maps_pokeheartgold.json
  maps_pokeplatinum.json
"""

import json
import os
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# POKEEMERALD
# ---------------------------------------------------------------------------

def extract_pokeemerald():
    """
    pokeemerald stores each map as  data/maps/<MapName>/map.json
    The map_groups.json file lists every map name grouped by region group.
    We read every map.json and attach the group name.
    """
    root = BASE_DIR / "pokeemerald"
    maps_dir = root / "data" / "maps"
    groups_file = maps_dir / "map_groups.json"

    # Build a lookup: map_name -> group_name
    map_to_group = {}
    if groups_file.exists():
        with open(groups_file, "r", encoding="utf-8") as f:
            groups_data = json.load(f)
        group_order = groups_data.get("group_order", [])
        for group_name in group_order:
            for map_name in groups_data.get(group_name, []):
                map_to_group[map_name] = group_name

    all_maps = []
    for map_dir in sorted(maps_dir.iterdir()):
        if not map_dir.is_dir():
            continue
        map_json = map_dir / "map.json"
        if not map_json.exists():
            continue
        with open(map_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        map_name = map_dir.name
        entry = {
            "map_name": map_name,
            "group": map_to_group.get(map_name),
            **data,
        }
        all_maps.append(entry)

    print(f"[pokeemerald] Extracted {len(all_maps)} maps")
    return all_maps


# ---------------------------------------------------------------------------
# POKEHEARTGOLD
# ---------------------------------------------------------------------------

def parse_maps_h_heartgold(root: Path) -> dict:
    """
    Parse include/constants/maps.h to build:
      zone_id (int) -> { "constant": "MAP_...", "name": "...", "internal_code": "..." }
    """
    maps_h = root / "include" / "constants" / "maps.h"
    id_to_info = {}
    if not maps_h.exists():
        return id_to_info
    pattern = re.compile(
        r"#define\s+(MAP_\w+)\s+(\d+)\s*(?://\s*(.+))?"
    )
    with open(maps_h, "r", encoding="utf-8") as f:
        for line in f:
            m = pattern.match(line.strip())
            if m:
                constant = m.group(1)
                zone_id = int(m.group(2))
                internal_code = (m.group(3) or "").strip()
                # Convert constant to a human-readable name
                name = constant.removeprefix("MAP_").replace("_", " ").title()
                id_to_info[zone_id] = {
                    "constant": constant,
                    "name": name,
                    "internal_code": internal_code,
                }
    return id_to_info


def extract_pokeheartgold():
    """
    pokeheartgold stores event data per zone in:
      files/fielddata/eventdata/zone_event/<NNN>_<CODE>.json
    Zone IDs and names come from include/constants/maps.h.
    """
    root = BASE_DIR / "pokeheartgold"
    zone_event_dir = root / "files" / "fielddata" / "eventdata" / "zone_event"

    id_to_info = parse_maps_h_heartgold(root)

    all_maps = []
    for json_file in sorted(zone_event_dir.glob("*.json")):
        # Filename format: NNN_CODE.json  (e.g. 006_R01.json)
        stem = json_file.stem
        parts = stem.split("_", 1)
        try:
            zone_id = int(parts[0])
        except ValueError:
            zone_id = None

        with open(json_file, "r", encoding="utf-8") as f:
            event_data = json.load(f)

        info = id_to_info.get(zone_id, {})
        entry = {
            "zone_id": zone_id,
            "file_code": parts[1] if len(parts) > 1 else stem,
            "constant": info.get("constant"),
            "name": info.get("name"),
            "internal_code": info.get("internal_code"),
            **event_data,
        }
        all_maps.append(entry)

    print(f"[pokeheartgold] Extracted {len(all_maps)} maps")
    return all_maps


# ---------------------------------------------------------------------------
# POKEPLATINUM
# ---------------------------------------------------------------------------

def parse_map_headers_txt_platinum(root: Path) -> dict:
    """
    Parse generated/map_headers.txt (one MAP_HEADER_... per line, 0-indexed)
    to build:  index (int) -> constant_name (str)
    """
    txt_file = root / "generated" / "map_headers.txt"
    index_to_constant = {}
    if not txt_file.exists():
        return index_to_constant
    with open(txt_file, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            constant = line.strip()
            if constant:
                index_to_constant[i] = constant
    return index_to_constant


def extract_pokeplatinum():
    """
    pokeplatinum stores event data per map in:
      res/field/events/events_<map_name>.json
    Map header names come from generated/map_headers.txt.
    Area data (map properties) is in res/field/area_data/area_data_NNN.json.
    """
    root = BASE_DIR / "pokeplatinum"
    events_dir = root / "res" / "field" / "events"
    area_data_dir = root / "res" / "field" / "area_data"

    # Build constant -> index lookup from map_headers.txt
    index_to_constant = parse_map_headers_txt_platinum(root)
    constant_to_index = {v: k for k, v in index_to_constant.items()}

    # Load area_data files indexed by number
    area_data_cache = {}
    for ad_file in sorted(area_data_dir.glob("area_data_*.json")):
        m = re.search(r"area_data_(\d+)\.json$", ad_file.name)
        if m:
            idx = int(m.group(1))
            with open(ad_file, "r", encoding="utf-8") as f:
                area_data_cache[idx] = json.load(f)

    # Parse the map headers C file to extract per-map properties
    # (area_data index, matrix, weather, music, etc.)
    map_header_props = parse_map_header_c_platinum(root, constant_to_index)

    all_maps = []
    for json_file in sorted(events_dir.glob("events_*.json")):
        stem = json_file.stem  # e.g. "events_jubilife_city"
        # Derive the MAP_HEADER constant from the filename
        # events_jubilife_city -> MAP_HEADER_JUBILIFE_CITY
        map_key = stem.removeprefix("events_").upper()
        constant = f"MAP_HEADER_{map_key}"
        header_index = constant_to_index.get(constant)

        with open(json_file, "r", encoding="utf-8") as f:
            event_data = json.load(f)

        # Human-readable name from constant
        name = constant.removeprefix("MAP_HEADER_").replace("_", " ").title()

        props = map_header_props.get(constant, {})

        # Resolve area_data details
        area_data_idx = props.get("area_data_index")
        area_data = area_data_cache.get(area_data_idx) if area_data_idx is not None else None

        entry = {
            "map_header_index": header_index,
            "constant": constant,
            "name": name,
            "area_data": area_data,
            **props,
            **event_data,
        }
        all_maps.append(entry)

    print(f"[pokeplatinum] Extracted {len(all_maps)} maps")
    return all_maps


def parse_map_header_c_platinum(root: Path, constant_to_index: dict) -> dict:
    """
    Parse include/data/map_headers.h to extract per-map properties.
    Returns: constant -> { area_data_index, weather, map_type, ... }
    """
    header_file = root / "include" / "data" / "map_headers.h"
    result = {}
    if not header_file.exists():
        return result

    with open(header_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Find each [MAP_HEADER_...] = { ... } block
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

        # Resolve area_data_index from the archive ID token (e.g. "area_data_005")
        area_raw = props.get("areaDataArchiveID", "")
        area_m = re.search(r"area_data_(\d+)", area_raw)
        if area_m:
            props["area_data_index"] = int(area_m.group(1))

        result[constant] = props

    return result


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    output_dir = BASE_DIR

    # pokeemerald
    emerald_maps = extract_pokeemerald()
    out_path = output_dir / "maps_pokeemerald.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(emerald_maps, f, indent=2, ensure_ascii=False)
    print(f"  -> Written to {out_path}")

    # pokeheartgold
    hg_maps = extract_pokeheartgold()
    out_path = output_dir / "maps_pokeheartgold.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(hg_maps, f, indent=2, ensure_ascii=False)
    print(f"  -> Written to {out_path}")

    # pokeplatinum
    pt_maps = extract_pokeplatinum()
    out_path = output_dir / "maps_pokeplatinum.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(pt_maps, f, indent=2, ensure_ascii=False)
    print(f"  -> Written to {out_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
