#!/usr/bin/env python3
"""
Render terrain tile data for any map as an ASCII text file.
Reads the decomps directly and uses no generated JSON.  Emerald assembles a
layout from data/layouts/*/map.bin plus its two tilesets' attributes; both Gen 4
games assemble a map from the land-data binaries named by their matrix.  Events,
warps and encounters come from each game's own archives.

Usage:
  python3 terrain_to_ascii.py <game> <map_name_or_constant> [output.txt]
  python3 terrain_to_ascii.py <game> --all [output_dir]
                              [--keep-duplicates] [--include-unused]

  game: emerald | heartgold | platinum

  --all writes one file per distinct terrain grid.  Maps whose terrain repeats
  one already written (24 HeartGold Pokecenter 1Fs are one grid) are listed in
  duplicate_terrain.txt instead.  --keep-duplicates renders every map, which is
  what you want if you need their events — those are per-map, not per-grid.

  --all also skips maps the decomp marks as leftovers (Emerald's `Unused*`
  layouts, the Gen 4 games' `*_UNUSED` constants, Platinum's `UNKNOWN_<id>`
  headers).  --include-unused renders them.  Naming a map explicitly always
  renders it, whether or not it is unused.

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
from collections import Counter
from pathlib import Path

from terrain_helpers import (
    BASE_DIR,
    map_category,
    _char_description,
    build_overlay,
    find_map_entries,
    find_tile_entry,
    terrain_signature,
    skip_map,
    tile_to_char,
    resolve_cell,
)
from emerald_data import (
    _render_encounter_index,
    interior_dims as em_interior_dims,
    layout_registry_entries as em_layout_registry_entries,
    layout_tile_entry as em_layout_tile_entry,
    map_entries as em_map_entries,
    load_events_from_decomp,
    load_signs_from_decomp,
    load_wild_encounters,
    render_gym_leader_section as em_render_gym_leader_section,
    render_rival_section as em_render_rival_section,
    render_npc_section as em_render_npc_section,
    render_sign_section as em_render_sign_section,
    render_static_encounter_section as em_render_static_encounter_section,
    render_secret_base_section as em_render_secret_base_section,
    render_coin_prize_section as em_render_coin_prize_section,
)
from heartgold_data import (
    get_zone_tile_entry as hg_get_zone_tile_entry,
    map_registry_entries as hg_map_registry_entries,
    zone_warp_chars as hg_zone_warp_chars,
    WARP_CHAR_DESC as hg_warp_char_desc,
    _load_encounter_data as _hg_load_enc_data,
    _load_hg_safari_enc as _hg_load_safari_enc,
    _load_headbutt_tables as _hg_load_headbutt_tables,
    _headbutt_map_code as _hg_headbutt_map_code,
    _load_rock_smash_narc as _hg_load_rock_smash_narc,
    _load_rs_tables as _hg_load_rs_tables,
    render_encounter_section as hg_render_encounter_section,
    render_hg_safari_section,
    render_headbutt_section as hg_render_headbutt_section,
    load_events as hg_load_events,
    render_field_object_section as hg_render_field_object_section,
    render_hidden_item_section as hg_render_hidden_item_section,
    render_item_section as hg_render_item_section,
    render_npc_section as hg_render_npc_section,
    render_trainer_section as hg_render_trainer_section,
    render_gym_leader_section as hg_render_gym_leader_section,
    render_rival_section as hg_render_rival_section,
    render_static_encounter_section as hg_render_static_encounter_section,
    render_rock_smash_item_section as hg_render_rock_smash_item_section,
    render_apricorn_section as hg_render_apricorn_section,
    hg_game_corner_prizes,
    render_coin_prize_section as hg_render_coin_prize_section,
)
from platinum_data import (
    get_zone_tile_entry as pt_get_zone_tile_entry,
    map_registry_entries as pt_map_registry_entries,
    load_events as pt_load_events,
    ENC_DIR as _pt_enc_dir,
    HONEY_TREE_FILE as _pt_honey_tree_file,
    _get_honey_tree_positions as _pt_get_honey_tree_positions,
    render_encounter_section as pt_render_encounter_section,
    render_field_object_section as pt_render_field_object_section,
    render_hidden_item_section as pt_render_hidden_item_section,
    render_honey_tree_section as pt_render_honey_tree_section,
    render_item_section as pt_render_item_section,
    render_npc_section as pt_render_npc_section,
    render_sign_section as pt_render_sign_section,
    render_trainer_section as pt_render_trainer_section,
    render_rival_section as pt_render_rival_section,
    render_gym_leader_section as pt_render_gym_leader_section,
    render_warp_section as pt_render_warp_section,
    render_static_encounter_section as pt_render_static_encounter_section,
    _pt_detect_service_flags,
    _map_name_from_constant as pt_map_name_from_constant,
    pt_game_corner_prizes,
    render_coin_prize_section as pt_render_coin_prize_section,
)


# ---------------------------------------------------------------------------
# Shared Exchange / Trade section renderers
# ---------------------------------------------------------------------------

_EX_SEP = "=" * 70


def _render_exchange_section(exchanges: list,
                              col_off: int = 0, row_off: int = 0) -> list[str]:
    """Exchange Index — one block per exchange source (vendor, tutor, etc.)."""
    if not exchanges:
        return []
    from itertools import groupby
    multi_map = len({e.get("map_name", "") for e in exchanges if e.get("map_name")}) > 1
    lines = ["", _EX_SEP, "  Exchange Index", _EX_SEP, ""]
    for map_name, group in groupby(exchanges, key=lambda e: e.get("map_name", "")):
        if multi_map and map_name:
            lines.append(f"  [ {map_name} ]")
            lines.append("")
        for ex in group:
            col = ex["x"] - col_off
            row = ex["z"] - row_off
            lines.append(f"  (col={col:>3}, row={row:>3})  {ex['label']}")
            for given, obtained in ex["pairs"]:
                lines.append(f"      {given}  →  {obtained}")
            lines.append("")
    return lines


def _render_trade_section(trades: list,
                           col_off: int = 0, row_off: int = 0) -> list[str]:
    """NPC Trade Index — in-game species swaps."""
    if not trades:
        return []
    from itertools import groupby
    multi_map = len({t.get("map_name", "") for t in trades if t.get("map_name")}) > 1
    lines = ["", _EX_SEP, "  NPC Trade Index", _EX_SEP, ""]
    for map_name, group in groupby(trades, key=lambda t: t.get("map_name", "")):
        if multi_map and map_name:
            lines.append(f"  [ {map_name} ]")
            lines.append("")
        for t in group:
            col = t["x"] - col_off
            row = t["z"] - row_off
            nick = f' "{t["nickname"]}"' if t.get("nickname") else ""
            lines.append(f"  (col={col:>3}, row={row:>3})  {t['label']}")
            lines.append(f"      {t['gives']}  →  {t['receives']}{nick}")
            lines.append("")
    return lines


def _collect_exchange(npcs: list, z_key: str = "z",
                       vending: list | None = None) -> list[dict]:
    """Build exchange Index records from NPC and vending machine lists."""
    entries: list[dict] = []
    seen: set[tuple] = set()
    for n in npcs:
        pairs = n.get("exchange_pairs", [])
        if not pairs:
            continue
        x = n.get("x", 0)
        z = n.get(z_key, 0)
        map_name = n.get("map_name", "")
        key = (x, z, map_name)
        if key in seen:
            continue
        seen.add(key)
        raw_id = n.get("id") or ""
        clean_id = raw_id.replace("LOCALID_", "").replace("_", " ").title()
        label = n.get("script") or clean_id or "NPC"
        entries.append({"x": x, "z": z, "label": label, "pairs": pairs,
                        "map_name": map_name})
    for v in (vending or []):
        pairs = v.get("exchange_pairs", [])
        if not pairs:
            continue
        x = v.get("x", 0)
        z = v.get("z", v.get("y", 0))
        label = v.get("label") or v.get("script") or "Vending Machine"
        entries.append({"x": x, "z": z, "label": label, "pairs": pairs})
    return entries


def _collect_trades(npcs: list, z_key: str = "z") -> list[dict]:
    """Build NPC Trade Index records from NPC list."""
    entries: list[dict] = []
    seen: set[tuple] = set()
    for n in npcs:
        ti = n.get("trade_info")
        if not ti:
            continue
        x = n.get("x", 0)
        z = n.get(z_key, 0)
        map_name = n.get("map_name", "")
        key = (x, z, map_name)
        if key in seen:
            continue
        seen.add(key)
        label = n.get("script") or n.get("id") or "NPC Trade"
        entries.append({
            "x": x, "z": z, "label": label, "map_name": map_name,
            "gives": ti.get("gives", "?"),
            "receives": ti.get("receives", "?"),
            "nickname": ti.get("nickname", ""),
        })
    return entries


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

# 0 = original (event tile gets classified char, entrance tile keeps 'D')
# 1 = shift to entrance (classified char moves to WARP_ENTRANCE_* tile)  [default]
# 2 = both tiles get the classified char
WARP_SHIFT_MODE = 1


def _find_warp_entrance(
    tile_r: int, tile_c: int,
    grid: list, rows: int, cols: int,
    warp_tile_positions: set, game: str,
) -> "tuple[int, int] | None":
    """Return (entrance_r, entrance_c) grid-local if a WARP_ENTRANCE_* neighbour
    exists that naturally renders as 'D', otherwise None."""
    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        nr, nc = tile_r + dr, tile_c + dc
        if not (0 <= nr < rows and 0 <= nc < cols):
            continue
        if (nr, nc) in warp_tile_positions:
            continue
        cell = grid[nr][nc]
        if cell is None:
            continue
        if "WARP_ENTRANCE" not in (cell.get("behavior_name") or ""):
            continue
        if tile_to_char(cell, game) == "D":
            return (nr, nc)
    return None


def _shift_gen4_warp_to_entrance(
    warps, game: str, grid: list, rows: int, cols: int,
    warp_tile_positions: set, col_off: int, row_off: int, mode: int,
) -> list:
    """Return an adjusted warp list for Gen 4 maps.

    In mode 1 the classified char is placed on the WARP_ENTRANCE_* tile instead
    of the WARP_* event tile, so the visible cave/gate mouth shows the correct
    symbol.  Event tiles that lose their char revert to terrain 'D'.
    This includes plain-D warps: a D-char warp beside a WARP_ENTRANCE_* tile
    was previously skipped, leaving two adjacent D cells in the output.
    """
    if mode == 0:
        return list(warps)

    adjusted = []
    for w in warps:
        if game == "heartgold":
            tile_r, tile_c = w["r"], w["c"]
        else:
            tile_r = w["z"] - row_off
            tile_c = w["x"] - col_off

        entrance = _find_warp_entrance(
            tile_r, tile_c, grid, rows, cols, warp_tile_positions, game,
        )

        if entrance is None:
            adjusted.append(w)
            continue

        enr, enc = entrance
        if mode == 2:
            adjusted.append(w)
        else:
            # Mode 1: erase the terrain 'D' that resolve_cell left on the event
            # tile — write a space so the trigger tile disappears visually.
            if game == "heartgold":
                adjusted.append({"_erase": True, "r": tile_r, "c": tile_c})
            else:
                adjusted.append({"_erase": True,
                                  "x": tile_c + col_off, "z": tile_r + row_off})

        new_w = dict(w)
        if game == "heartgold":
            new_w["r"] = enr
            new_w["c"] = enc
        else:
            new_w["x"] = enc + col_off
            new_w["z"] = enr + row_off
        adjusted.append(new_w)

    return adjusted


def _render_event_sections(
    entry: dict,
    game: str,
    maps_data,
    wild_encounters,
    events: dict,
    signs_all: list,
    signs: list,
    _hg_events,
    _pt_events,
    _em_static_overlapped: dict,
    _hg_static_overlapped: dict,
    _pt_static_overlapped: dict,
    map_entries: list,
) -> list[str]:
    """Render all event/NPC/encounter sections. Called by both render_terrain
    (which builds a full file) and render_events_only (which appends to one).

    Canonical section order (all games):
      Warp Index → Wild Encounters (+ game extras) → Field Obstacles → Signs
      → Item Balls → Hidden Items → Berry Trees (EM) → NPCs → Gym Leaders
      → Rivals → Trainers → Static Encounters → Secret Bases (EM)
      → Exchange / Trade → Coin Prizes
    """
    lines: list[str] = []
    lines += _render_warp_index(map_entries, game, maps_data, tile_entry=entry)

    if game == "emerald":
        if wild_encounters:
            lines += _render_encounter_index(map_entries, wild_encounters)
        lines += _render_field_object_index(events.get("field_objects", []))
        lines += em_render_sign_section(signs)
        lines += _render_item_index(events["items"])
        lines += _render_hidden_item_index(events["hidden_items"])
        lines += _render_berry_index(events["berries"])
        lines += em_render_npc_section(events["npcs"])
        lines += em_render_gym_leader_section(events.get("gym_leaders", []))
        lines += em_render_rival_section(events.get("rivals", []))
        lines += _render_trainer_index(events["trainers"])
        lines += em_render_static_encounter_section(
            events.get("static_encounters", []), _em_static_overlapped)
        lines += em_render_secret_base_section(events.get("secret_bases", []))
        _em_ex = _collect_exchange(events["npcs"], z_key="y",
                                   vending=[s for s in signs_all if s.get("is_vending")])
        _em_tr = _collect_trades(events["npcs"], z_key="y")
        lines += _render_exchange_section(_em_ex)
        lines += _render_trade_section(_em_tr)
        lines += em_render_coin_prize_section(events.get("coin_prizes", []))

    if game == "heartgold":
        file_code     = entry.get("file_code", "")
        internal_code = entry.get("internal_code", "")
        zone_name     = entry.get("name") or ""
        lines += hg_render_encounter_section(file_code)
        lines += render_hg_safari_section(internal_code)
        lines += hg_render_rock_smash_item_section(entry.get("zone_id", -1))
        lines += hg_render_headbutt_section(zone_name, internal_code)
        if _hg_events is not None:
            col_off = entry.get("_tile_col_min", 0)
            row_off = entry.get("_tile_row_min", 0)
            lines += hg_render_field_object_section(
                _hg_events["field_objects"], col_off, row_off)
            lines += hg_render_apricorn_section(
                _hg_events.get("apricorns", []), col_off, row_off)
            lines += em_render_sign_section(signs)
            lines += hg_render_item_section(_hg_events["items"], col_off, row_off)
            lines += hg_render_hidden_item_section(
                _hg_events["hidden_items"], col_off, row_off)
            lines += hg_render_npc_section(_hg_events["npcs"], col_off, row_off)
            lines += hg_render_gym_leader_section(
                _hg_events.get("gym_leaders", []), col_off, row_off)
            lines += hg_render_rival_section(
                _hg_events.get("rivals", []), col_off, row_off)
            lines += hg_render_trainer_section(
                _hg_events["trainers"], col_off, row_off)
            lines += hg_render_static_encounter_section(
                _hg_events.get("static_encounters", []), col_off, row_off,
                _hg_static_overlapped)
            _hg_ex = _collect_exchange(_hg_events["npcs"])
            _hg_tr = _collect_trades(_hg_events["npcs"])
            lines += _render_exchange_section(_hg_ex, col_off, row_off)
            lines += _render_trade_section(_hg_tr, col_off, row_off)
            lines += hg_render_coin_prize_section(
                hg_game_corner_prizes(internal_code), col_off, row_off)

    if game == "platinum":
        wild_id   = (map_entries[0].get("wildEncountersArchiveID", "")
                     if map_entries else "")
        zone_name = entry.get("name") or ""
        lines += pt_render_encounter_section(wild_id, zone_name)
        if wild_id and wild_id != "ENCOUNTERS_NONE":
            lines += pt_render_honey_tree_section()
        if _pt_events is not None:
            col_off = entry.get("_tile_col_min", 0)
            row_off = entry.get("_tile_row_min", 0)
            lines += pt_render_field_object_section(_pt_events["field_objects"], col_off, row_off)
            lines += pt_render_sign_section(_pt_events["signs"], col_off, row_off)
            lines += pt_render_item_section(_pt_events["items"], col_off, row_off)
            lines += pt_render_hidden_item_section(_pt_events["hidden_items"], col_off, row_off)
            lines += pt_render_npc_section(
                _pt_events["permanent_npcs"], _pt_events["event_npcs"], col_off, row_off
            )
            lines += pt_render_gym_leader_section(
                _pt_events.get("gym_leaders", []), col_off, row_off
            )
            lines += pt_render_trainer_section(
                _pt_events["trainers"], _pt_events["event_trainers"], col_off, row_off
            )
            lines += pt_render_rival_section(
                _pt_events.get("rivals", []), col_off, row_off
            )
            lines += pt_render_static_encounter_section(
                _pt_events.get("static_encounters", []), col_off, row_off,
                _pt_static_overlapped)
            _pt_all_npcs = (
                _pt_events["permanent_npcs"] + _pt_events["event_npcs"]
            )
            _pt_ex = _collect_exchange(
                _pt_all_npcs,
                vending=_pt_events.get("vending_machines", []),
            )
            _pt_tr = _collect_trades(_pt_all_npcs)
            lines += _render_exchange_section(_pt_ex, col_off, row_off)
            lines += _render_trade_section(_pt_tr, col_off, row_off)
            _pt_map_const = map_entries[0].get("constant", "") if map_entries else ""
            lines += pt_render_coin_prize_section(
                pt_game_corner_prizes(_pt_map_const), col_off, row_off)

    return lines


def render_events_only(entry: dict, game: str, maps_data,
                       wild_encounters=None, interior_dims=None,
                       include_background: bool = False) -> str:
    """Return only the event/NPC/encounter sections for entry, under a
    [ Map Name ] header.  Used by --all to append events from terrain-identical
    maps into the canonical file without re-writing the grid."""
    events    = load_events_from_decomp(entry, game)
    signs_all = load_signs_from_decomp(entry, game)
    signs     = [s for s in signs_all if not s.get("is_vending")]

    _hg_events = None
    _pt_events = None
    if game == "heartgold":
        _hg_events = hg_load_events(entry.get("internal_code", ""), include_background=include_background)
    if game == "platinum":
        _pt_map_entries = find_map_entries(maps_data, entry, game)
        if _pt_map_entries:
            _pt_events = pt_load_events(_pt_map_entries[0], include_background=include_background)

    map_entries = find_map_entries(maps_data, entry, game)

    pretty_name = (entry.get("name") or entry.get("layout_name")
                   or entry.get("constant") or "?")
    header = [
        "",
        "=" * 70,
        f"  [ {pretty_name} ]",
        "=" * 70,
        "",
    ]
    # Overlapped dicts map grid coords → underlying char.  There is no char_grid
    # here, so pass empty dicts — static encounter positions show @ only.
    event_lines = _render_event_sections(
        entry, game, maps_data, wild_encounters,
        events, signs_all, signs,
        _hg_events, _pt_events,
        {}, {}, {},
        map_entries,
    )
    return "\n".join(header + event_lines)


def _strip_mv(raw: str) -> str:
    """'MOVEMENT_TYPE_LOOK_SOUTH' → 'LOOK_SOUTH'; digit/empty → 'NONE'."""
    s = str(raw).removeprefix("MOVEMENT_TYPE_")
    return s if s and not s.isdigit() else "NONE"


def _facing_from_mv(mv: str) -> str:
    if mv.startswith(("LOOK_SOUTH", "FACE_DOWN")):
        return "SOUTH"
    if mv.startswith(("LOOK_NORTH", "FACE_UP")):
        return "NORTH"
    if mv.startswith(("LOOK_EAST", "FACE_RIGHT")):
        return "EAST"
    if mv.startswith(("LOOK_WEST", "FACE_LEFT")):
        return "WEST"
    return "SOUTH"


def _npc_dialogue(script_data) -> list:
    if not script_data:
        return [{"flag": None, "text": ""}]
    out = []
    for s in (script_data.get("states") or []):
        msgs = s.get("pre_msgs") or []
        text = " / ".join(str(m).strip() for m in msgs if m)
        out.append({"flag": s.get("flag"), "text": text})
    return out or [{"flag": None, "text": ""}]


def _npc_gives_item(script_data) -> "dict | None":
    """Extract the first gives entry from NPC script_data states, if any."""
    if not script_data:
        return None
    for s in (script_data.get("states") or []):
        for g in (s.get("gives") or []):
            if g.get("item"):
                return {"item_id": g["item"], "quantity": g.get("qty", 1), "flag": s.get("flag")}
    return None


_LAND_SLOT_RATES = [20, 20, 10, 10, 10, 10, 5, 5, 4, 4, 1, 1]
_SURF_SLOT_RATES = [60, 30, 5, 4, 1]
_FISH_SLOT_RATES = [60, 30, 5, 4, 1]


def _enc_sp(sp) -> str:
    """Resolve any species value to a clean string (SPECIES_ prefix stripped)."""
    if isinstance(sp, dict):
        sp = (sp.get("morn") or sp.get("day") or sp.get("HEARTGOLD") or
              sp.get("gold") or next(iter(sp.values()), ""))
    return str(sp).removeprefix("SPECIES_")


def _enc_lvl(lvl) -> tuple[int, int]:
    if isinstance(lvl, dict):
        mn = lvl.get("min") or lvl.get("HEARTGOLD") or lvl.get("gold") or 1
        mx = lvl.get("max") or lvl.get("SOULSILVER") or lvl.get("silver") or mn
        if isinstance(mn, dict): mn = next(iter(mn.values()), 1)
        if isinstance(mx, dict): mx = next(iter(mx.values()), mn)
        return int(mn), int(mx)
    v = int(lvl)
    return v, v


def _enc_slots(mons: list, rates: list, use_day: bool = False) -> list[dict]:
    out = []
    for i, m in enumerate(mons):
        sp_raw = m.get("species", "")
        if isinstance(sp_raw, dict) and use_day:
            sp_raw = sp_raw.get("day") or sp_raw.get("morn") or next(iter(sp_raw.values()), "")
        sp = _enc_sp(sp_raw)
        lvl = m.get("level", None)
        if lvl is None:
            mn, mx = int(m.get("min_level", 1)), int(m.get("max_level", 1))
        else:
            mn, mx = _enc_lvl(lvl)
        r = rates[i] if i < len(rates) else 0
        out.append({"species": sp, "min_level": mn, "max_level": mx, "rate": r})
    return out


def _hg_time_overrides(mons: list, rates: list) -> "dict | None":
    """Return time_overrides if any slot has per-time species; else None."""
    if not any(isinstance(m.get("species"), dict) and
               any(k in m.get("species", {}) for k in ("morn", "nite"))
               for m in mons):
        return None
    result = {}
    for tod_key, json_key in (("morn", "morning"), ("day", "day"), ("nite", "night")):
        slots = []
        for i, m in enumerate(mons):
            sp_raw = m.get("species", "")
            if isinstance(sp_raw, dict):
                sp_raw = sp_raw.get(tod_key) or sp_raw.get("day") or next(iter(sp_raw.values()), "")
            sp = _enc_sp(sp_raw)
            mn, mx = _enc_lvl(m.get("level", 1))
            r = rates[i] if i < len(rates) else 0
            slots.append({"species": sp, "min_level": mn, "max_level": mx, "rate": r})
        result[json_key] = slots
    return result


def _build_encounters_json(game: str, entry: dict, map_entries: list,
                            wild_encounters: "dict | None") -> "dict | None":
    """Build the encounters dict for render_json. Returns None if no encounters."""
    enc: dict = {}

    if game == "emerald":
        if not wild_encounters or not map_entries:
            return None
        seen: set = set()
        for me in map_entries:
            mid = me.get("id", "")
            if mid in wild_encounters and mid not in seen:
                seen.add(mid)
                wdata, fields = wild_encounters[mid]
                rate_lookup = {f.get("type", ""): f.get("encounter_rates", []) for f in fields}
                fish_groups = next((f["groups"] for f in fields if f.get("groups")), {})

                if "land_mons" in wdata:
                    lm = wdata["land_mons"]
                    rates = rate_lookup.get("land_mons", _LAND_SLOT_RATES)
                    enc["grass"] = {
                        "rate": lm.get("encounter_rate", 0),
                        "slots": _enc_slots(lm["mons"], rates),
                        "swarm": None,
                    }
                if "water_mons" in wdata:
                    wm = wdata["water_mons"]
                    rates = rate_lookup.get("water_mons", _SURF_SLOT_RATES)
                    enc["surf"] = {"rate": wm.get("encounter_rate", 0),
                                   "slots": _enc_slots(wm["mons"], rates)}
                if "rock_smash_mons" in wdata:
                    rm = wdata["rock_smash_mons"]
                    rates = rate_lookup.get("rock_smash_mons", _SURF_SLOT_RATES)
                    enc["rock_smash"] = {"rate": rm.get("encounter_rate", 0),
                                         "slots": _enc_slots(rm["mons"], rates)}
                if "fishing_mons" in wdata:
                    fm = wdata["fishing_mons"]
                    all_mons  = fm["mons"]
                    all_rates = rate_lookup.get("fishing_mons", _FISH_SLOT_RATES)
                    fishing: dict = {}
                    if fish_groups:
                        for rod in ("old_rod", "good_rod", "super_rod"):
                            idxs = fish_groups.get(rod, [])
                            if idxs:
                                rod_mons  = [all_mons[i] for i in idxs if i < len(all_mons)]
                                rod_rates = [all_rates[i] for i in idxs if i < len(all_rates)]
                                fishing[rod] = {"rate": fm.get("encounter_rate", 0),
                                                "slots": _enc_slots(rod_mons, rod_rates)}
                    else:
                        fishing["fishing"] = {"rate": fm.get("encounter_rate", 0),
                                              "slots": _enc_slots(all_mons, all_rates)}
                    if fishing:
                        enc["fishing"] = fishing
                break  # only first map's encounters for dedupe canonical

    elif game == "heartgold":
        file_code = entry.get("file_code") or entry.get("internal_code", "").removeprefix("MAP_")
        hg_enc = _hg_load_enc_data().get(file_code) if file_code else None

        if hg_enc:
            land = hg_enc.get("land", {})
            if land.get("rate", 0) > 0 and land.get("mons"):
                mons = land["mons"]
                rates = _LAND_SLOT_RATES
                slots = _enc_slots(mons, rates, use_day=True)
                tod = _hg_time_overrides(mons, rates)
                grass: dict = {"rate": land["rate"], "slots": slots}
                if tod:
                    grass["time_overrides"] = tod
                swarm = hg_enc.get("landSwarm")
                grass["swarm"] = _enc_sp(swarm) if swarm else None
                enc["grass"] = grass

            surf = hg_enc.get("surf", {})
            if surf.get("rate", 0) > 0 and surf.get("mons"):
                enc["surf"] = {"rate": surf["rate"],
                               "slots": _enc_slots(surf["mons"], _SURF_SLOT_RATES, use_day=True)}

            fishing = hg_enc.get("fishing", {})
            fish_json: dict = {}
            for rod in ("old_rod", "good_rod", "super_rod"):
                rd = fishing.get(rod, {})
                if rd.get("mons"):
                    fish_json[rod] = {"rate": rd.get("rate", 0),
                                      "slots": _enc_slots(rd["mons"], _FISH_SLOT_RATES, use_day=True)}
            if fish_json:
                enc["fishing"] = fish_json

            rock_smash = hg_enc.get("rock_smash", {})
            if rock_smash.get("rate", 0) > 0 and rock_smash.get("mons"):
                enc["rock_smash"] = {"rate": rock_smash["rate"],
                                     "slots": _enc_slots(rock_smash["mons"], _SURF_SLOT_RATES)}

            swarm_sp = {k: _enc_sp(hg_enc[k]) for k in ("surfSwarm", "fishSwarm") if hg_enc.get(k)}
            if swarm_sp:
                enc["_swarms"] = swarm_sp

            night_fish = hg_enc.get("nightFish")
            if night_fish:
                enc["night_fishing"] = _enc_sp(night_fish)

            hoenn = hg_enc.get("hoenn")
            if hoenn:
                enc["hoenn_sound"] = [_enc_sp(s) for s in (hoenn if isinstance(hoenn, list) else [hoenn])]
            sinnoh = hg_enc.get("sinnoh")
            if sinnoh:
                enc["sinnoh_sound"] = [_enc_sp(s) for s in (sinnoh if isinstance(sinnoh, list) else [sinnoh])]

    # HG Safari Zone (gate MAP_D47 shows all areas; SAF* blocks reference the gate)
    if game == "heartgold":
        internal_code = entry.get("internal_code", "")
        is_gate = (internal_code == "MAP_D47")
        is_saf  = internal_code.startswith("MAP_SAF")
        if is_gate or is_saf:
            if is_saf:
                enc["safari"] = {"ref": "MAP_D47"}
            else:
                def _safari_slot(m: dict) -> dict:
                    lvl = m.get("level", 1)
                    return {"species": _enc_sp(m.get("species", "")),
                            "min_level": int(lvl), "max_level": int(lvl)}
                def _safari_mons(raw) -> "dict | list | None":
                    if isinstance(raw, dict):
                        tod = {}
                        for tk, jk in (("morn", "morning"), ("day", "day"), ("nite", "night")):
                            sl = raw.get(tk, [])
                            if sl:
                                tod[jk] = [_safari_slot(m) for m in sl]
                        return tod if tod else None
                    if isinstance(raw, list) and raw:
                        return [_safari_slot(m) for m in raw]
                    return None
                areas_out = []
                for area in _hg_load_safari_enc():
                    name = area.get("area", "").replace("SAFARI_ZONE_AREA_", "").replace("_", " ").title()
                    ao: dict = {"area": name}
                    land_mons = _safari_mons(area.get("land", {}).get("mons", {}))
                    if land_mons:
                        ao["grass"] = land_mons
                    surf_mons = _safari_mons(area.get("surf", {}).get("mons", {}))
                    if surf_mons:
                        ao["surf"] = surf_mons
                    fishing: dict = {}
                    for rk, rj in (("oldrod", "old_rod"), ("goodrod", "good_rod"), ("superrod", "super_rod")):
                        fm = _safari_mons(area.get(rk, {}).get("mons", {}))
                        if fm:
                            fishing[rj] = fm
                    if fishing:
                        ao["fishing"] = fishing
                    areas_out.append(ao)
                if areas_out:
                    enc["safari"] = areas_out

        # Headbutt tree encounters
        hb_code = _hg_headbutt_map_code(entry.get("internal_code", ""))
        if hb_code:
            hb_tables = _hg_load_headbutt_tables()
            hb_table = next((t for t in hb_tables if t.get("Map") == hb_code), None)
            if hb_table:
                def _hb_slots(mons: list) -> list[dict]:
                    return [
                        {"species": _enc_sp(m.get("species", "")),
                         "min_level": m.get("minLevel", 1),
                         "max_level": m.get("maxLevel", 1)}
                        for m in mons
                    ]
                headbutt: dict = {}
                if hb_table.get("CommonMons"):
                    headbutt["common"] = _hb_slots(hb_table["CommonMons"])
                if hb_table.get("RareMons"):
                    headbutt["rare"] = _hb_slots(hb_table["RareMons"])
                if hb_table.get("SecretMons"):
                    headbutt["secret"] = _hb_slots(hb_table["SecretMons"])
                n_normal = len(hb_table.get("Trees") or [])
                n_secret = len(hb_table.get("SecretTrees") or [])
                if n_normal:
                    headbutt["tree_count"] = n_normal
                if n_secret:
                    headbutt["secret_tree_count"] = n_secret
                if headbutt:
                    enc["headbutt"] = headbutt

    elif game == "platinum":
        if not map_entries:
            return None
        enc_id = map_entries[0].get("enc_id") or map_entries[0].get("wildEncountersArchiveID", "")
        if not enc_id or enc_id == "ENCOUNTERS_NONE":
            return None
        enc_path = _pt_enc_dir / f"{enc_id}.json"
        if not enc_path.exists():
            return None
        with open(enc_path, encoding="utf-8") as f:
            pe = json.load(f)

        land_rate = pe.get("land_rate", 0)
        land_mons = pe.get("land_encounters", [])
        if land_rate > 0 and land_mons:
            slots = _enc_slots(land_mons, _LAND_SLOT_RATES)
            grass: dict = {"rate": land_rate, "slots": slots}
            # Time overrides
            day_sp   = pe.get("day", [])
            night_sp = pe.get("night", [])
            if day_sp or night_sp:
                # PT day/night replace slots 2-3; build slot objects using base slot levels
                def _pt_tod_slots(sp_list, base, idxs=(2, 3)):
                    out = []
                    for i, sp in zip(idxs, sp_list if isinstance(sp_list, list) else [sp_list]):
                        if i < len(base):
                            mn, mx = _enc_lvl(base[i].get("level", 1))
                            r = _LAND_SLOT_RATES[i] if i < len(_LAND_SLOT_RATES) else 0
                            out.append({"species": _enc_sp(sp), "min_level": mn,
                                        "max_level": mx, "rate": r})
                    return out or None
                tod = {
                    "day":   _pt_tod_slots(day_sp, land_mons) if day_sp else None,
                    "night": _pt_tod_slots(night_sp, land_mons) if night_sp else None,
                }
                grass["time_overrides"] = tod
            swarms = pe.get("swarms", [])
            grass["swarm"] = _enc_sp(swarms[0]) if swarms else None
            enc["grass"] = grass

        surf_rate = pe.get("surf_rate", 0)
        surf_mons = pe.get("surf_encounters", [])
        if surf_rate > 0 and surf_mons:
            enc["surf"] = {"rate": surf_rate,
                           "slots": _enc_slots(surf_mons, _SURF_SLOT_RATES)}

        fish_json: dict = {}
        for rod in ("old_rod", "good_rod", "super_rod"):
            r = pe.get(f"{rod}_rate", 0)
            m = pe.get(f"{rod}_encounters", [])
            if r > 0 and m:
                fish_json[rod] = {"rate": r, "slots": _enc_slots(m, _FISH_SLOT_RATES)}
        if fish_json:
            enc["fishing"] = fish_json

        radar = pe.get("radar")
        if radar:
            radar_list = radar if isinstance(radar, list) else [radar]
            enc["_radar"] = [_enc_sp(s) for s in radar_list if s and s != "SPECIES_NONE"]

        # GBA cartridge slots (replace land slots 8-9 for ruby/sapphire/emerald,
        # 10-11 for firered/leafgreen when a GBA game is inserted into the DS slot)
        gba: dict = {}
        for cart in ("ruby", "sapphire", "emerald", "firered", "leafgreen"):
            sp_list = pe.get(cart, [])
            valid = [_enc_sp(s) for s in sp_list if s and s != "SPECIES_NONE"]
            if valid:
                gba[cart] = valid
        if gba:
            enc["gba_slots"] = gba

        # Trophy Garden daily pool (slots 6-7 replaced each day by Mr. Backlot)
        if "daily_encounters" in pe:
            daily = pe["daily_encounters"]
            land_mons_raw = pe.get("land_encounters", [])
            slots_67 = land_mons_raw[6:8] if len(land_mons_raw) >= 8 else []
            pool = [_enc_sp(s) for s in daily if s and s != "SPECIES_NONE"]
            if pool:
                lvls = [int(m.get("level", 1)) for m in slots_67]
                enc["daily_pool"] = {
                    "species": pool,
                    "min_level": min(lvls) if lvls else 1,
                    "max_level": max(lvls) if lvls else 1,
                }

        # Great Marsh daily pool (binoculars slots, before/after National Dex)
        if enc_id.startswith("encounters_great_marsh_") and not enc_id.endswith("lookout"):
            lookout_path = _pt_enc_dir / "encounters_great_marsh_lookout.json"
            if lookout_path.exists():
                with open(lookout_path, encoding="utf-8") as f:
                    lk = json.load(f)
                land_mons_raw = pe.get("land_encounters", [])
                slots_67 = land_mons_raw[6:8] if len(land_mons_raw) >= 8 else []
                lvls = [int(m.get("level", 1)) for m in slots_67]
                gm: dict = {
                    "min_level": min(lvls) if lvls else 1,
                    "max_level": max(lvls) if lvls else 1,
                }
                before = list(dict.fromkeys(_enc_sp(s) for s in lk.get("before_national_dex", []) if s and s != "SPECIES_NONE"))
                after  = list(dict.fromkeys(_enc_sp(s) for s in lk.get("after_national_dex", []) if s and s != "SPECIES_NONE"))
                if before:
                    gm["before_national_dex"] = before
                if after:
                    gm["after_national_dex"] = after
                if before or after:
                    enc["great_marsh_daily"] = gm

        # Honey tree encounters (same table for every tree in Sinnoh)
        if _pt_honey_tree_file.exists():
            with open(_pt_honey_tree_file, encoding="utf-8") as f:
                ht = json.load(f)
            honey: dict = {}
            for tier in ("common", "uncommon", "rare"):
                sp_list = ht.get(tier, [])
                unique = list(dict.fromkeys(_enc_sp(s) for s in sp_list if s))
                if unique:
                    honey[tier] = unique
            if honey:
                enc["honey_trees"] = honey

    return enc if enc else None


def _normalize_rematches(rematches, game: str) -> list:
    """Convert per-game rematch lists to a uniform [{trainer_id, party, prize}] format."""
    out = []
    for r in (rematches or []):
        if r is None:
            continue
        party = [
            {
                "species": p.get("species", ""),
                "level":   p.get("lvl", p.get("level", 0)),
                "ivs":     p.get("iv_scale", p.get("iv", 0) // 3 if game == "emerald" else 0),
                **( {"moves": p["moves"]} if p.get("moves") else {} ),
                **( {"item":  p["item"]}  if p.get("item")  else {} ),
            }
            for p in (r.get("party") or [])
        ]
        out.append({
            "trainer_id": r.get("const") or r.get("trainer_id"),
            "party":      party,
            "prize":      r.get("prize"),
        })
    return out


def render_json(entry: dict, game: str, maps_data: list | None,
                wild_encounters: dict | None = None,
                include_background: bool = False) -> dict:
    """Build the per-map JSON dict matching simulator/11_game_folder_format.md."""
    grid = entry.get("grid") or []
    rows = len(grid)
    cols = len(grid[0]) if rows else 0

    events      = load_events_from_decomp(entry, game)
    signs_all   = load_signs_from_decomp(entry, game)
    signs       = [s for s in signs_all if not s.get("is_vending")]
    map_entries = find_map_entries(maps_data, entry, game)

    _hg_ev = _pt_ev = None
    col_off = row_off = 0
    if game == "heartgold":
        col_off = entry.get("_tile_col_min", 0)
        row_off = entry.get("_tile_row_min", 0)
        _hg_ev  = hg_load_events(entry.get("internal_code", ""), include_background=include_background)
    elif game == "platinum":
        col_off = entry.get("_tile_col_min", 0)
        row_off = entry.get("_tile_row_min", 0)
        if map_entries:
            _entry_const = entry.get("constant", "")
            _pt_me = next(
                (me for me in map_entries if me.get("constant") == _entry_const),
                map_entries[0],
            )
            _pt_ev = pt_load_events(_pt_me, include_background=include_background)

    constant = entry.get("constant") or entry.get("layout_id") or ""
    name     = entry.get("name") or entry.get("layout_name") or constant
    if game == "platinum" and map_entries:
        _entry_const = entry.get("constant", "")
        _music_me = next(
            (me for me in map_entries if me.get("constant") == _entry_const),
            map_entries[0],
        )
        music = _music_me.get("music") or ""
    else:
        music = (map_entries[0].get("music") or "") if map_entries else ""

    # ── warps ──────────────────────────────────────────────────────────────────
    warps: list[dict] = []
    if game == "emerald":
        from emerald_data import em_dest_warp_coord
        for me in map_entries:
            for w in me.get("warp_events", []):
                coord = em_dest_warp_coord(w.get("dest_map", ""), w.get("dest_warp_id", "0"))
                wd = {"col": w.get("x", 0), "row": w.get("y", 0), "dest": w.get("dest_map", "")}
                if coord:
                    wd["spawn_col"], wd["spawn_row"] = coord[0], coord[1]
                warps.append(wd)
    elif game == "heartgold":
        from heartgold_data import zone_warp_chars, hg_dest_warp_coord
        for w in zone_warp_chars(entry.get("internal_code", ""), col_off, row_off):
            wd = {"col": w.get("c", 0), "row": w.get("r", 0), "dest": w.get("header") or ""}
            anchor = w.get("anchor")
            if anchor is not None and anchor != 256:
                coord = hg_dest_warp_coord(w.get("header", ""), anchor)
                if coord:
                    wd["spawn_col"], wd["spawn_row"] = coord[0], coord[1]
            warps.append(wd)
    elif game == "platinum" and map_entries:
        from platinum_data import pt_dest_warp_coord
        _warp_me = next(
            (me for me in map_entries if me.get("constant") == constant),
            map_entries[0],
        )
        for w in _warp_me.get("warp_events", []):
            lx, lz = w.get("x", 0) - col_off, w.get("z", 0) - row_off
            dest = w.get("dest_header_id", "")
            wd = {"col": lx, "row": lz, "dest": dest}
            coord = pt_dest_warp_coord(dest, w.get("dest_warp_id", 0))
            if coord:
                wd["spawn_col"], wd["spawn_row"] = coord[0], coord[1]
            warps.append(wd)

    # ── connections ────────────────────────────────────────────────────────────
    connections: list[dict] = []
    if game == "emerald":
        _DIR = {"up": "NORTH", "down": "SOUTH", "left": "WEST", "right": "EAST"}
        for me in map_entries:
            for c in (me.get("connections") or []):
                connections.append({
                    "direction": _DIR.get(c.get("direction", ""), c.get("direction", "").upper()),
                    "dest":      c.get("map", ""),
                    "offset":    c.get("offset", 0),
                })
    elif game == "heartgold":
        _DIR4 = {"N": "NORTH", "S": "SOUTH", "E": "EAST", "W": "WEST"}
        _zone_id = entry.get("zone_id")
        if _zone_id is not None:
            from heartgold_data import hg_get_connections
            for c in hg_get_connections(_zone_id):
                connections.append({
                    "direction": _DIR4.get(c["direction"], c["direction"]),
                    "dest":      c["dest"],
                    "offset":    0,
                })
    elif game == "platinum" and map_entries:
        _DIR4 = {"N": "NORTH", "S": "SOUTH", "E": "EAST", "W": "WEST"}
        from platinum_data import _get_connections as _pt_get_connections
        _entry_const = entry.get("constant", "")
        _conn_me = next(
            (me for me in map_entries if me.get("constant") == _entry_const),
            map_entries[0],
        )
        for c in _pt_get_connections(_conn_me):
            connections.append({
                "direction": _DIR4.get(c["direction"], c["direction"]),
                "dest":      c["dest"],
                "offset":    0,
            })

    # ── NPCs ───────────────────────────────────────────────────────────────────
    # Gym leaders are trainer-type dicts — they go in trainers[], not npcs[].
    npc_list: list[dict] = []
    if game == "emerald":
        npc_list = events.get("npcs", [])
    elif game == "heartgold" and _hg_ev:
        npc_list = _hg_ev.get("npcs", [])
    elif game == "platinum" and _pt_ev:
        npc_list = _pt_ev.get("permanent_npcs", []) + _pt_ev.get("event_npcs", [])

    npcs: list[dict] = []
    for i, n in enumerate(npc_list):
        mv = _strip_mv(n.get("movement", "MOVEMENT_TYPE_NONE"))
        if game == "emerald":
            col_n, row_n = n.get("x", 0), n.get("y", 0)
            sprite = n.get("graphics_id", "")
        else:
            col_n, row_n = n.get("x", 0) - col_off, n.get("z", 0) - row_off
            sprite = n.get("sprite") or n.get("graphics_id", "")
        npc_id = str(n.get("id") or n.get("flag") or f"{constant}_{i}")
        if not npc_id.upper().startswith("NPC_"):
            npc_id = f"NPC_{npc_id}"
        _sflags = sorted(n.get("service_flags") or [])
        _pairs  = n.get("exchange_pairs") or []
        _shop_items = [
            {"item_id": p[1]}
            for p in _pairs
            if p[1] and (p[0] == "Money" or str(p[0]).startswith("₱"))
        ] if _pairs else None
        _exchanges = [
            {"gives": p[0], "receives": p[1]}
            for p in _pairs
            if p[0] and p[1] and p[0] != "Money" and not str(p[0]).startswith("₱")
        ] if _pairs else None
        npc_entry: dict = {
            "npc_id":          npc_id,
            "col":             col_n,
            "row":             row_n,
            "name":            sprite.replace("_", " ").title(),
            "sprite":          sprite,
            "facing":          _facing_from_mv(mv),
            "movement":        mv,
            "is_trainer":      False,
            "gives_item":      _npc_gives_item(n.get("script_data")),
            "dialogue_states": _npc_dialogue(n.get("script_data")),
        }
        if _sflags:
            npc_entry["service_flags"] = _sflags
        if _shop_items:
            npc_entry["shop_items"] = _shop_items
        if _exchanges:
            npc_entry["exchanges"] = _exchanges
        _trade = n.get("trade_info")
        if _trade:
            npc_entry["trade"] = {
                "gives":    _trade.get("gives", ""),
                "receives": _trade.get("receives", ""),
                "nickname": _trade.get("nickname") or None,
            }
        npcs.append(npc_entry)

    # ── trainers ───────────────────────────────────────────────────────────────
    # Build trainer list with is_leader flag from the source list.
    tr_list: list[tuple[dict, bool]] = []
    if game == "emerald":
        tr_list  = [(t, False) for t in events.get("trainers", [])]
        tr_list += [(t, True)  for t in events.get("gym_leaders", [])]
    elif game == "heartgold" and _hg_ev:
        tr_list  = [(t, False) for t in _hg_ev.get("trainers", [])]
        tr_list += [(t, True)  for t in _hg_ev.get("gym_leaders", [])]
    elif game == "platinum" and _pt_ev:
        tr_list  = [(t, False) for t in _pt_ev.get("trainers", [])]
        tr_list += [(t, False) for t in _pt_ev.get("event_trainers", [])]
        tr_list += [(t, True)  for t in _pt_ev.get("gym_leaders", [])]

    trainers: list[dict] = []
    for t, is_leader_from_list in tr_list:
        is_leader = is_leader_from_list or bool(
            t.get("is_leader") or t.get("category") in ("Gym", "Elite", "Champion")
        )
        badge = t.get("gym_badge")
        if game == "emerald":
            col_t, row_t = t.get("x", 0), t.get("y", 0)
            tid    = t.get("trainer_const", "") or f"TRAINER_{constant}_{col_t}_{row_t}"
            tname  = t.get("tr_name", "?") or "?"
            tclass = t.get("tr_class", "")
            sprite = t.get("graphics_id", "")
            pre    = t.get("intro_text") or ""
            defeat = t.get("defeat_text") or ""
            post   = t.get("post_text") or ""
            prize  = t.get("prize", 0)
            double = t.get("double", False)
            items_t = t.get("items") or []
            last_pokemon = ""
            half_hp = ""
            vs_rem = _normalize_rematches(t.get("vs_rematches", []), "emerald")
            party  = [
                {
                    "species": p.get("species", ""),
                    "level":   p.get("lvl", 0),
                    "ivs":     p.get("iv", 0) // 3,
                    **( {"moves": [m.replace("_", " ").title() for m in p["moves"] if m and not m.startswith("(")]} if p.get("moves") else {} ),
                    **( {"item":  p["item"]} if p.get("item") else {} ),
                }
                for p in (t.get("party") or [])
            ]
        elif game == "heartgold":
            col_t, row_t = t.get("x", 0) - col_off, t.get("z", 0) - row_off
            tid    = (t.get("const") or t.get("trainer_const")
                      or f"TRAINER_{constant}_{col_t}_{row_t}")
            tname  = t.get("name", "?")
            tclass = t.get("class", "")
            sprite = t.get("sprite", "")
            msgs   = t.get("messages") or {}
            _pb    = t.get("pre_battle", "") or msgs.get("TRMSG_INTRO", "")
            pre    = " ".join(_pb) if isinstance(_pb, list) else (_pb or "")
            defeat = msgs.get("TRMSG_LOSE", "")
            post   = (t.get("post_battle", "") or msgs.get("TRMSG_AFTER", ""))
            prize  = t.get("prize", 0)
            double = t.get("double", False)
            items_t = t.get("items") or []
            last_pokemon = msgs.get("TRMSG_LAST_POKE", "")
            half_hp = msgs.get("TRMSG_LAST_POKE_HALF", "")
            vs_rem = _normalize_rematches(t.get("rematches") or t.get("vs_rematches", []), "heartgold")
            party  = [
                {
                    "species": p.get("species", ""),
                    "level":   p.get("level", 0),
                    "ivs":     0,
                    **( {"moves": p["moves"]} if p.get("moves") else {} ),
                    **( {"item":  p["item"]}  if p.get("item")  else {} ),
                    **( {"gender_override":  p["gender_override"]}  if p.get("gender_override")  else {} ),
                    **( {"ability_override": p["ability_override"]} if p.get("ability_override") else {} ),
                }
                for p in (t.get("party") or [])
            ]
        else:  # platinum
            col_t, row_t = t.get("x", 0) - col_off, t.get("z", 0) - row_off
            tid    = (t.get("trainer_const") or t.get("const")
                      or f"TRAINER_{constant}_{col_t}_{row_t}")
            tname  = t.get("name", "?")
            tclass = t.get("class", "")
            sprite = ""
            msgs   = t.get("messages") or {}
            _pb    = t.get("pre_battle") or next((v for k, v in msgs.items() if "PRE_BATTLE" in k.upper() or "INTRO" in k.upper()), "")
            pre    = " ".join(_pb) if isinstance(_pb, list) else (_pb or "")
            defeat = next((v for k, v in msgs.items() if "DEFEAT" in k.upper() or "LOSE" in k.upper()), "")
            _po    = t.get("post_battle") or next((v for k, v in msgs.items() if "POST_BATTLE" in k.upper() or "AFTER" in k.upper()), "")
            post   = " ".join(_po) if isinstance(_po, list) else (_po or "")
            prize  = t.get("prize", 0)
            double = t.get("double", False)
            items_t = t.get("items") or []
            last_pokemon = next((v for k, v in msgs.items() if "LAST_BATTLER" in k.upper() and "HALF" not in k.upper()), "")
            half_hp = next((v for k, v in msgs.items() if "HALF_HP" in k.upper() or ("LAST_BATTLER" in k.upper() and "HALF" in k.upper())), "")
            vs_rem = _normalize_rematches(t.get("vs_rematches", []), "platinum")
            party  = [
                {
                    "species": p.get("species", ""),
                    "level":   p.get("lvl", p.get("level", 0)),
                    "ivs":     p.get("iv_scale", 0),
                    **( {"moves": p["moves"]} if p.get("moves") else {} ),
                    **( {"item":  p["item"]}  if p.get("item")  else {} ),
                }
                for p in (t.get("party") or [])
            ]
        trainers.append({
            "trainer_id":      tid,
            "col":             col_t,
            "row":             row_t,
            "name":            tname,
            "sprite":          sprite,
            "trainer_class":   tclass,
            "is_leader":       is_leader,
            "gym_badge":       badge,
            "sight_direction": "ALL" if is_leader else "SOUTH",
            "sight_range":     0 if is_leader else 4,
            "prize_money":     prize,
            "double":          double,
            "ai_flags":        t.get("ai_flags") or [],
            "items":           items_t,
            "gives_item":      t.get("gives_item"),
            "pre_battle":      pre,
            "defeat_text":     defeat,
            "post_battle":     post,
            "last_pokemon":    last_pokemon,
            "half_hp":         half_hp,
            "vs_rematches":    vs_rem,
            "party":           party,
        })

    # ── items ──────────────────────────────────────────────────────────────────
    items: list[dict] = []

    def _add_items(src: list, hidden: bool, xk: str, zk: str,
                   xoff: int, zoff: int, nk: str) -> None:
        for it in src:
            items.append({
                "col":    it.get(xk, 0) - xoff,
                "row":    it.get(zk, 0) - zoff,
                "name":   it.get(nk, ""),
                "hidden": hidden,
            })

    if game == "emerald":
        _add_items(events.get("items", []),        False, "x", "y", 0, 0, "item_name")
        _add_items(events.get("hidden_items", []), True,  "x", "y", 0, 0, "item_name")
    elif game == "heartgold" and _hg_ev:
        _add_items(_hg_ev.get("items", []),        False, "x", "z", col_off, row_off, "item")
        _add_items(_hg_ev.get("hidden_items", []), True,  "x", "z", col_off, row_off, "item")
    elif game == "platinum" and _pt_ev:
        _add_items(_pt_ev.get("items", []),        False, "x", "z", col_off, row_off, "item_name")
        _add_items(_pt_ev.get("hidden_items", []), True,  "x", "z", col_off, row_off, "item_name")

    # ── signs ──────────────────────────────────────────────────────────────────
    json_signs: list[dict] = []
    if game in ("emerald", "heartgold"):
        for s in signs:
            json_signs.append({"col": s.get("x", 0), "row": s.get("y", 0), "text": s.get("text", "")})
    elif game == "platinum" and _pt_ev:
        for s in _pt_ev.get("signs", []):
            json_signs.append({
                "col":  s.get("x", 0) - col_off,
                "row":  s.get("z", 0) - row_off,
                "text": s.get("dialogue") or s.get("label", ""),
            })

    # ── static encounters ──────────────────────────────────────────────────────
    raw_se: list[dict] = events.get("static_encounters") or []
    if game == "heartgold" and _hg_ev:
        raw_se = _hg_ev.get("static_encounters") or []
    elif game == "platinum" and _pt_ev:
        raw_se = _pt_ev.get("static_encounters") or []

    static_encs: list[dict] = []
    for se in raw_se:
        sx = se.get("x", 0)
        sz = se.get("z", se.get("y", 0))
        if game in ("heartgold", "platinum"):
            sx -= col_off
            sz -= row_off
        se_dict: dict = {
            "col":      sx,
            "row":      sz,
            "species":  se.get("species", ""),
            "level":    se.get("level", 0),
            "is_shiny": se.get("is_shiny", False),
        }
        if se.get("gender"):
            se_dict["gender"] = se["gender"]
        if se.get("ability"):
            se_dict["ability"] = se["ability"]
        if se.get("held_item"):
            se_dict["held_item"] = se["held_item"]
        static_encs.append(se_dict)

    # ── field objects ──────────────────────────────────────────────────────────
    field_objs: list[dict] = []
    def _add_field_objects(src: list, xk: str, zk: str, xoff: int, zoff: int) -> None:
        for fo in src:
            field_objs.append({
                "col":   fo.get(xk, 0) - xoff,
                "row":   fo.get(zk, 0) - zoff,
                "char":  fo.get("char", ""),
                "label": fo.get("label", ""),
            })

    if game == "emerald":
        _add_field_objects(events.get("field_objects", []), "x", "y", 0, 0)
    elif game == "heartgold" and _hg_ev:
        _add_field_objects(_hg_ev.get("field_objects", []), "x", "z", col_off, row_off)
    elif game == "platinum" and _pt_ev:
        _add_field_objects(_pt_ev.get("field_objects", []), "x", "z", col_off, row_off)

    # ── apricorn trees (HG only) ───────────────────────────────────────────────
    apricorns: list[dict] = []
    if game == "heartgold" and _hg_ev:
        for a in _hg_ev.get("apricorns", []):
            apricorns.append({
                "col":          a["x"] - col_off,
                "row":          a["z"] - row_off,
                "apricorn":     a["apricorn_name"],
                "tree_index":   a["tree_index"],
            })

    # ── berry trees (Emerald only) ────────────────────────────────────────────
    berries: list[dict] = []
    if game == "emerald":
        for b in events.get("berries", []):
            berries.append({
                "col":        b.get("x", 0),
                "row":        b.get("y", 0),
                "berry_id":   b.get("berry_id", ""),
                "berry_name": b.get("berry_name", ""),
            })

    # ── vending machines ───────────────────────────────────────────────────────
    vending: list[dict] = []
    if game == "emerald":
        for s in (signs_all or []):
            if s.get("is_vending"):
                vending.append({"col": s.get("x", 0), "row": s.get("y", 0),
                                 "items": s.get("items", [])})
    elif game == "platinum" and _pt_ev:
        for v in _pt_ev.get("vending_machines", []):
            vending.append({"col": v.get("x", 0) - col_off, "row": v.get("z", 0) - row_off,
                             "items": v.get("items", [])})

    # ── rivals ─────────────────────────────────────────────────────────────────
    def _rival_party_json(party: list, game_: str) -> list[dict]:
        out_p = []
        for p in party:
            sp = p.get("species", "")
            lv = p.get("lvl") or p.get("level", 0)
            iv = p.get("iv", 0) if game_ == "emerald" else p.get("iv_scale", 0)
            moves = [m.replace("MOVE_", "").replace("_", " ").title()
                     for m in (p.get("moves") or []) if m and m not in ("", "MOVE_NONE")]
            entry_p: dict = {"species": sp, "level": int(lv), "ivs": iv}
            if moves:
                entry_p["moves"] = moves
            out_p.append(entry_p)
        return out_p

    rivals: list[dict] = []
    _raw_rivals: list[dict] = []
    if game == "emerald":
        _raw_rivals = events.get("rivals", [])
    elif game == "heartgold" and _hg_ev:
        _raw_rivals = _hg_ev.get("rivals", [])
    elif game == "platinum" and _pt_ev:
        _raw_rivals = _pt_ev.get("rivals", [])

    for rv in _raw_rivals:
        if game == "emerald":
            col_r, row_r = rv.get("x", 0), rv.get("y", 0)
            tid   = rv.get("trainer_const", "")
            rname = rv.get("tr_name", "")
            rcls  = rv.get("tr_class_disp", rv.get("tr_class", ""))
            prize = rv.get("prize", 0)
            note  = None
        elif game == "heartgold":
            col_r, row_r = rv.get("x", 0) - col_off, rv.get("z", 0) - row_off
            tid   = rv.get("trainer_const", "")
            rname = rv.get("name", "")
            rcls  = rv.get("class", "")
            prize = rv.get("prize", 0)
            note  = None
        else:  # platinum
            col_r, row_r = rv.get("x", 0) - col_off, rv.get("z", 0) - row_off
            tid   = rv.get("trainer_const", "")
            rname = rv.get("name", "")
            rcls  = rv.get("class", "")
            prize = rv.get("prize", 0)
            note  = rv.get("note") or None
        rivals.append({
            "trainer_id":    tid,
            "col":           col_r,
            "row":           row_r,
            "name":          rname,
            "trainer_class": rcls,
            "prize_money":   prize,
            "note":          note,
            "party":         _rival_party_json(rv.get("party", []), game),
        })

    # ── coin prizes ────────────────────────────────────────────────────────────
    coin_prizes: list[dict] = []
    if game == "emerald":
        coin_prizes = [{"name": p["name"], "coins": p["coins"], "kind": p.get("kind", "item")}
                       for p in events.get("coin_prizes", [])]
    elif game == "heartgold":
        _hg_ic = entry.get("internal_code", "")
        coin_prizes = [{"name": p["name"], "coins": p["coins"], "kind": p.get("kind", "item")}
                       for p in hg_game_corner_prizes(_hg_ic)]
    elif game == "platinum":
        _pt_const = entry.get("constant", "")
        coin_prizes = [{"name": p["name"], "coins": p["coins"], "kind": p.get("kind", "item")}
                       for p in pt_game_corner_prizes(_pt_const)]

    # ── rock smash drops (HG only) ─────────────────────────────────────────────
    rock_smash_drops = None
    if game == "heartgold":
        _zone_id = entry.get("zone_id", -1)
        _rs_narc = _hg_load_rock_smash_narc()
        if _rs_narc and 0 <= _zone_id < len(_rs_narc):
            _rs_odds, _rs_type = _rs_narc[_zone_id]
            if _rs_odds > 0:
                _rs_tables, _rs_probs = _hg_load_rs_tables()
                _rs_items = _rs_tables.get(_rs_type)
                if _rs_items:
                    rock_smash_drops = {
                        "drop_chance": _rs_odds,
                        "slots": [
                            {"item": item, "probability": prob}
                            for item, prob in zip(_rs_items, _rs_probs)
                        ],
                    }

    # ── wild encounters ────────────────────────────────────────────────────────
    encounters = _build_encounters_json(game, entry, map_entries, wild_encounters)

    out: dict = {
        "constant":           constant,
        "name":               name,
        "game":               game,
        "cols":               cols,
        "rows":               rows,
        "music":              music,
        "warps":              warps,
        "connections":        connections,
        "elevation_addendum": _build_elevation_addendum(
            grid, rows, cols, game,
            entry.get("secondary_tileset") or "",
        ),
        "items":              items,
        "signs":              json_signs,
        "field_objects":      field_objs,
        "apricorns":          apricorns,
        "berries":            berries,
        "vending_machines":   vending,
        "static_encounters":  static_encs,
        "rivals":             rivals,
        "coin_prizes":        coin_prizes,
        "rock_smash_drops":   rock_smash_drops,
        "npcs":               npcs,
        "trainers":           trainers,
        "encounters":         encounters,
    }
    return out


def render_terrain(entry: dict, game: str, maps_data: list | None,
                   max_width: int | None = None,
                   interior_dims: dict | None = None,
                   wild_encounters: dict | None = None,
                   include_background: bool = False) -> str:
    grid = entry.get("grid")
    if not grid:
        note = entry.get("note", "no grid data")
        return _header(entry, game) + f"\n  ({note})\n"

    rows = len(grid)
    cols = len(grid[0]) if rows else 0

    # Downsampling is OFF by default. It used to kick in at 160 columns, which
    # silently decimated *both* axes: each scale×scale block collapsed to the
    # first non-space char found, so one wall tile could swallow the walkable
    # tile beside it and close off a corridor. Only Route 27 (192 wide) ever
    # tripped it, and it rendered as a half-size map with paths sealed shut.
    # Pass max_width explicitly if a caller really wants a shrunken overview.
    scale = 1
    if max_width and cols > max_width:
        scale = (cols + max_width - 1) // max_width

    grid_h = (rows + scale - 1) // scale
    grid_w = (cols + scale - 1) // scale

    secondary_tileset = entry.get("secondary_tileset") or ""

    overlay = build_overlay(entry, maps_data, game, interior_dims=interior_dims)

    char_grid  = []
    seen_chars = {}
    cell_descs: dict = {}

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
                    ch, desc = resolve_cell(cell, game, secondary_tileset)
                    if best == " " and ch != " ":
                        best = ch
                    # Key the legend on the ELEMENT. Two elements may share a
                    # char (a tree and a crown are both ♣) and each still needs
                    # its own correct wording, which the char alone cannot give.
                    cell_descs.setdefault(ch, {})[desc] = True
            row_chars.append(best)
        char_grid.append(row_chars)

    for (or_, oc), och in overlay.items():
        gr = or_ // scale
        gc = oc // scale
        if 0 <= gr < grid_h and 0 <= gc < grid_w:
            char_grid[gr][gc] = och

    events = load_events_from_decomp(entry, game)

    _BUILDING_CHARS = {"C", "M", "G", "K", "F", "S", "H", "▓"}
    for it in events["items"]:
        gr = it["y"] // scale
        gc = it["x"] // scale
        if 0 <= gr < grid_h and 0 <= gc < grid_w:
            if char_grid[gr][gc] not in _BUILDING_CHARS:
                char_grid[gr][gc] = "I"

    for fo in events.get("field_objects", []):
        gr = fo["y"] // scale
        gc = fo["x"] // scale
        if 0 <= gr < grid_h and 0 <= gc < grid_w:
            char_grid[gr][gc] = fo["char"]

    signs_all = load_signs_from_decomp(entry, game)
    signs = [s for s in signs_all if not s.get("is_vending")]
    vending_em = [s for s in signs_all if s.get("is_vending")]
    for s in signs:
        gr = s["y"] // scale
        gc = s["x"] // scale
        if 0 <= gr < grid_h and 0 <= gc < grid_w:
            existing = char_grid[gr][gc]
            if existing not in ("C", "M", "G", "K", "F", "S", "H", "D"):
                char_grid[gr][gc] = "§"
    for s in vending_em:
        gr = s["y"] // scale
        gc = s["x"] // scale
        if 0 <= gr < grid_h and 0 <= gc < grid_w:
            existing = char_grid[gr][gc]
            if existing not in ("C", "M", "G", "K", "F", "S", "H"):
                char_grid[gr][gc] = "V"

    _em_static_overlapped: dict[tuple, str] = {}
    for se in events.get("static_encounters", []):
        x_se = se.get("x")
        y_se = se.get("y")
        if x_se is None or y_se is None:
            continue
        gr = y_se // scale
        gc = x_se // scale
        if 0 <= gr < grid_h and 0 <= gc < grid_w:
            _em_static_overlapped[(se.get("x"), se.get("y"))] = char_grid[gr][gc]
            char_grid[gr][gc] = "@"

    # Preload Platinum events: place signs, items, buildings on char_grid before rendering
    _pt_events = None
    _pt_static_overlapped: dict[tuple, str] = {}
    if game == "platinum":
        _pt_map_entries = find_map_entries(maps_data, entry, game)
        if _pt_map_entries:
            _pt_events = pt_load_events(_pt_map_entries[0], include_background=include_background)
            col_off = entry.get("_tile_col_min", 0)
            row_off = entry.get("_tile_row_min", 0)

            # Signs
            for s in _pt_events["signs"]:
                gc = (s["x"] - col_off) // scale
                gr = (s["z"] - row_off) // scale
                if 0 <= gr < grid_h and 0 <= gc < grid_w:
                    char_grid[gr][gc] = "§"

            # Vending machines
            for s in _pt_events["vending_machines"]:
                gc = (s["x"] - col_off) // scale
                gr = (s["z"] - row_off) // scale
                if 0 <= gr < grid_h and 0 <= gc < grid_w:
                    char_grid[gr][gc] = "V"

            # Visible items (⊙)
            for it in _pt_events["items"]:
                gc = (it["x"] - col_off) // scale
                gr = (it["z"] - row_off) // scale
                if 0 <= gr < grid_h and 0 <= gc < grid_w:
                    char_grid[gr][gc] = "⊙"

            # Field obstacles (cut trees, rock smash, strength boulders, special rocks)
            for fo in _pt_events["field_objects"]:
                gc = (fo["x"] - col_off) // scale
                gr = (fo["z"] - row_off) // scale
                if 0 <= gr < grid_h and 0 <= gc < grid_w:
                    char_grid[gr][gc] = fo["char"]

            # Warp entrance chars. Building bodies (▓) come from 3D prop
            # placements in build_overlay — no geometry heuristics here.
            # Every warp gets its char (incl. plain D) so multi-door
            # buildings show all their entrances.
            _pt_warp_tile_pos = {
                (w["z"] - row_off, w["x"] - col_off)
                for w in _pt_events["warps"]
                if isinstance(w.get("x"), int) and isinstance(w.get("z"), int)
            }
            _pt_warps_shifted = _shift_gen4_warp_to_entrance(
                _pt_events["warps"], "platinum", grid, rows, cols,
                _pt_warp_tile_pos, col_off, row_off, WARP_SHIFT_MODE,
            )
            for w in _pt_warps_shifted:
                wgc = (w["x"] - col_off) // scale
                wgr = (w["z"] - row_off) // scale
                if not (0 <= wgr < grid_h and 0 <= wgc < grid_w):
                    continue
                if w.get("_erase"):
                    char_grid[wgr][wgc] = " "
                else:
                    char_grid[wgr][wgc] = w.get("char", "D")

            _pt_static_overlapped: dict[tuple, str] = {}
            for se in _pt_events.get("static_encounters", []):
                sgc = (se["x"] - col_off) // scale
                sgr = (se["z"] - row_off) // scale
                if 0 <= sgr < grid_h and 0 <= sgc < grid_w:
                    _pt_static_overlapped[(se["x"], se["z"])] = char_grid[sgr][sgc]
                    char_grid[sgr][sgc] = "@"

    # Warp entrance chars, same idea as Platinum's above: every warp gets the
    # char for what it leads into, so multi-door buildings show every entrance.
    # Seeding seen_chars here is deliberate — several of these letters are also
    # HG behaviour symbols ('P' is a PC, 'T' a TV), and _char_description would
    # resolve them to the behaviour meaning.
    _hg_events = None
    if game == "heartgold":
        col_off = entry.get("_tile_col_min", 0)
        row_off = entry.get("_tile_row_min", 0)

        # Items and obstacles first, warps second — a doorway always outranks
        # whatever happens to be standing on it.
        _hg_events = hg_load_events(entry.get("internal_code", ""), include_background=include_background)
        for it in _hg_events["items"]:
            gc = (it["x"] - col_off) // scale
            gr = (it["z"] - row_off) // scale
            if 0 <= gr < grid_h and 0 <= gc < grid_w:
                char_grid[gr][gc] = "⊙"
        for fo in _hg_events["field_objects"]:
            gc = (fo["x"] - col_off) // scale
            gr = (fo["z"] - row_off) // scale
            if 0 <= gr < grid_h and 0 <= gc < grid_w:
                char_grid[gr][gc] = fo["char"]

        _hg_warp_list = list(hg_zone_warp_chars(
            entry.get("internal_code", ""), col_off, row_off))
        _hg_warp_tile_pos = {(w["r"], w["c"]) for w in _hg_warp_list}
        _hg_warps_shifted = _shift_gen4_warp_to_entrance(
            _hg_warp_list, "heartgold", grid, rows, cols,
            _hg_warp_tile_pos, col_off, row_off, WARP_SHIFT_MODE,
        )
        for w in _hg_warps_shifted:
            wgr, wgc = w["r"] // scale, w["c"] // scale
            if not (0 <= wgr < grid_h and 0 <= wgc < grid_w):
                continue
            if w.get("_erase"):
                char_grid[wgr][wgc] = " "
            else:
                tr, tc = w["r"], w["c"]
                cell_at = (grid[tr][tc] if 0 <= tr < rows and 0 <= tc < cols else None)
                # Inter-floor ladders within a cave sit on LADDER_* tiles and are
                # classified 'c' (cave destination). Keep 'L' — it is more precise
                # than the destination's cave classification.
                if ("LADDER" in (cell_at.get("behavior_name") or "") if cell_at else False):
                    wch = "L"
                else:
                    wch = w["char"]
                char_grid[wgr][wgc] = wch
                seen_chars.setdefault(
                    wch,
                    hg_warp_char_desc.get(wch, w["label"]) + " (entrance)")

        _hg_static_overlapped: dict[tuple, str] = {}
        for se in _hg_events.get("static_encounters", []):
            sgc = (se["x"] - col_off) // scale
            sgr = (se["z"] - row_off) // scale
            if 0 <= sgr < grid_h and 0 <= sgc < grid_w:
                _hg_static_overlapped[(se["x"], se["z"])] = char_grid[sgr][sgc]
                char_grid[sgr][sgc] = "@"

    for gr in range(grid_h):
        for gc in range(grid_w):
            ch = char_grid[gr][gc]
            if ch not in seen_chars:
                seen_chars[ch] = _char_description(ch, game)

    # Expand any char whose cells resolved to more than one element. The terrain
    # resolver knows which element it matched, so prefer that over
    # _char_description(), which can only guess from the char and therefore
    # cannot tell a tree from a crown or a hot spring from a house door.
    # Overlay-only chars (building bodies, entrances, signs) have no terrain
    # resolution and keep the char-level description.
    legend: list = []
    for ch, desc in seen_chars.items():
        observed = list(cell_descs.get(ch, {}))
        legend.extend((ch, d) for d in (observed or [desc]))

    # Per-map column cutoffs that remove geographic overlap with adjacent maps.
    # National Park's own 3×3 matrix physically includes the Route 36 gateway
    # strip (same geographic area rendered by Route 36's EVERYWHERE blocks).
    # Route 36's left edge is mirrored back by 22 cols for the same reason.
    _COL_CLIP = {
        "MAP_NATIONAL_PARK": (0,  82),   # drop the rightmost ~14 cols (R36 overlap)
        "MAP_ROUTE_36":      (22, None),  # drop the leftmost 22 cols (NP overlap)
    }
    _const = entry.get("constant", "")
    if _const in _COL_CLIP:
        _cc_start, _cc_end = _COL_CLIP[_const]
        _cc_start = _cc_start or 0
        _cc_end   = _cc_end   if _cc_end is not None else grid_w
        char_grid = [row[_cc_start:_cc_end] for row in char_grid]
        grid_w    = _cc_end - _cc_start

    _clip_col_start = _COL_CLIP[_const][0] if _const in _COL_CLIP else 0

    header_text = _header(entry, game)
    if _const in _COL_CLIP:
        header_text = re.sub(
            r'(Size\s*:\s*)(\d+)( × \d+ tiles)',
            lambda m: m.group(1) + str(grid_w) + m.group(3),
            header_text,
        )
    lines = [header_text]

    ruler = "     "
    for gc in range(grid_w):
        actual_col = _clip_col_start * scale + gc * scale
        ruler += str(actual_col)[0] if actual_col % 10 == 0 else " "
    lines.append(ruler)

    for gr in range(grid_h):
        actual_row = gr * scale
        lines.append(f"{actual_row:5d} " + "".join(char_grid[gr]))

    lines.append("")

    lines.append("  Legend:")
    lines.append(f"  {'Char':<6} Description")
    lines.append("  " + "-" * 40)
    for ch, desc in sorted(dict.fromkeys(legend),
                            key=lambda x: ("\xff" if x[0] == " " else x[0], x[1])):
        display = repr(ch) if ch == " " else ch
        lines.append(f"  {display:<6} {desc}")

    if scale > 1:
        lines.append(f"\n  [Scale: 1 char = {scale}×{scale} tiles]")

    lines.append("")

    lines += _render_elevation_addendum(grid, rows, cols, scale, game, secondary_tileset)

    map_entries = find_map_entries(maps_data, entry, game)
    lines += _render_event_sections(
        entry, game, maps_data, wild_encounters,
        events, signs_all, signs,
        _hg_events, _pt_events,
        _em_static_overlapped,
        _hg_static_overlapped if game == "heartgold" else {},
        _pt_static_overlapped if game == "platinum" else {},
        map_entries,
    )
    return "\n".join(lines)


def _infer_under_bridge(grid: list, rows: int, r: int, c: int, game: str,
                        secondary_tileset: str = "") -> str:
    """
    For a bridge tile at (r, c), scan the same column outward to find what
    terrain passes below.  Passable candidates preferred over impassable ones.
    Returns the inferred char, or '?' if indeterminate.
    """
    passable_candidate: str | None = None
    impassable_candidate: str | None = None

    for delta in range(1, rows):
        for nr in (r - delta, r + delta):
            if not (0 <= nr < rows):
                continue
            cell = grid[nr][c] if grid[nr] else None
            if not cell:
                continue
            ch = tile_to_char(cell, game, secondary_tileset)
            if ch == "=":
                continue
            if ch not in ("█", " ") or ch == " ":
                if passable_candidate is None:
                    passable_candidate = ch
            elif impassable_candidate is None:
                impassable_candidate = ch
            if passable_candidate is not None:
                return passable_candidate
        if impassable_candidate is not None and delta >= 3:
            break

    return passable_candidate or impassable_candidate or "?"


# Material name → the char the renderer draws it as. Only materials the
# renderer knows about exist as elevation levels — everything else (raw
# tileset ground/cliff/decal meshes) is not part of our representation.
_PLATINUM_MATERIAL_CHARS: dict[str, str] = {
    "bridge": "=",
    "searock": "∘",
    "sea": "≈", "lake": "≈", "asasea": "≈", "puddle_b": "≈",
    "tree01": "♣", "tree2_01": "♣", "tree3_02": "♣",
    "conttree_b": "♣", "conttree_t": "♣",
    "conttree2_b": "♣", "conttree2_t": "♣",
}


def _render_platinum_layers(grid: list, rows: int, cols: int, game: str) -> list[str]:
    """
    Elevation stacks for Platinum crossings, restricted to elements the
    renderer knows (bike bridge, bridge, sea rocks, water, trees). Ordered
    by mesh height; same-material meshes at distinct heights (bike bridge
    deck above foot bridge deck) count as separate levels.
    """
    def cell_sig(r: int, c: int):
        cell = grid[r][c] if grid[r] else None
        if not cell:
            return None
        mats = cell.get("layer_ys") or {}
        top = tile_to_char(cell, game)
        if top not in ("=", "≡") or not mats:
            return None
        entries = [
            (y, _PLATINUM_MATERIAL_CHARS[mat])
            for mat, levels in mats.items() if mat in _PLATINUM_MATERIAL_CHARS
            for y in levels
        ]
        entries.sort(key=lambda e: (-e[0], e[1]))
        # The rendered char already shows the top deck: drop its own mesh
        # (the bike bridge's deck is the highest 'bridge'-material level).
        for i, (_y, ch) in enumerate(entries):
            if ch == "=":
                del entries[i]
                break
        # Collapse adjacent duplicates (e.g. two water meshes = one level)
        stack: list[str] = []
        for _y, ch in entries:
            if not stack or stack[-1] != ch:
                stack.append(ch)
        if not stack:
            return None
        return (top, tuple(stack))

    # Row runs of identical stack signatures
    runs: list[tuple[int, int, int, tuple]] = []   # (row, c0, c1, sig)
    for r in range(rows):
        c = 0
        while c < cols:
            sig = cell_sig(r, c)
            if sig is None:
                c += 1
                continue
            c0 = c
            while c + 1 < cols and cell_sig(r, c + 1) == sig:
                c += 1
            runs.append((r, c0, c, sig))
            c += 1
    if not runs:
        return []

    # Merge identical col-spans across consecutive rows
    merged: list[list] = []   # [r0, r1, c0, c1, sig]
    for r, c0, c1, sig in runs:
        for m in merged:
            if m[2] == c0 and m[3] == c1 and m[4] == sig and m[1] == r - 1:
                m[1] = r
                break
        else:
            merged.append([r, r, c0, c1, sig])

    lines = [
        "=" * 70,
        "  Elevation Addendum  (stacked layers, top first)",
        "=" * 70,
        "",
        "  Crossing tiles whose rendered top element covers lower rendered",
        "  elements. Stack order comes from the 3D model's mesh heights.",
        "",
    ]
    for r0, r1, c0, c1, (top, hidden) in merged:
        row_s = f"row {r0}" if r0 == r1 else f"rows {r0}-{r1}"
        col_s = f"col {c0}" if c0 == c1 else f"cols {c0}-{c1}"
        labels = [f"{top} {_char_description(top, game).lower()}"]
        labels += [f"{ch} {_char_description(ch, game).lower()}" for ch in hidden]
        stack = " over ".join(labels)
        lines.append(f"  {row_s:<12} {col_s:<14} {len(labels)} levels:  {stack}")
    lines.append("")
    return lines


def _build_elevation_addendum(grid: list, rows: int, cols: int,
                               game: str,
                               secondary_tileset: str = "") -> list[dict]:
    """Build JSON elevation_addendum: one entry per bridge tile with non-trivial under-char.
    Tiles with passable floor underneath are omitted — absence means passable floor."""
    if game == "platinum":
        return []
    result: list[dict] = []
    for r in range(rows):
        for c in range(cols):
            cell = grid[r][c] if grid[r] else None
            if not cell:
                continue
            if tile_to_char(cell, game, secondary_tileset) != "=":
                continue
            under_ch = _infer_under_bridge(grid, rows, r, c, game, secondary_tileset)
            if under_ch != " ":
                result.append({"col": c, "row": r, "under_char": under_ch})
    return result


def _render_elevation_addendum(grid: list, rows: int, cols: int,
                                scale: int, game: str,
                                secondary_tileset: str = "") -> list[str]:
    """Report bridge tile spans with under-bridge content."""
    if game == "platinum":
        return _render_platinum_layers(grid, rows, cols, game)

    by_row: dict[int, list[tuple[int, str]]] = {}
    for r in range(rows):
        for c in range(cols):
            cell = grid[r][c] if grid[r] else None
            if not cell:
                continue
            if tile_to_char(cell, game, secondary_tileset) != "=":
                continue
            under_ch = _infer_under_bridge(grid, rows, r, c, game, secondary_tileset)
            by_row.setdefault(r, []).append((c, under_ch))

    if not by_row:
        return []

    lines = [
        "=" * 70,
        "  Elevation Addendum  (bridge deck tiles)",
        "=" * 70,
        "",
        "  All tiles that render as '=' (bridge deck). Under-bridge terrain is",
        "  inferred from the nearest non-bridge tile in the same column.",
        "  Entries showing only passable ground (  ) are omitted — only",
        "  non-trivial under-bridge content is listed.",
        "",
        f"  {'Row':<6} {'Col':<6} Under",
        "  " + "-" * 30,
    ]
    for row in sorted(by_row):
        for c, under_ch in sorted(by_row[row]):
            if under_ch == " ":
                continue
            from_desc = _char_description(under_ch, game) if under_ch != "?" else "unknown"
            lines.append(f"  {row:<6} {c:<6} {under_ch}  ({from_desc})")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Index formatters
# ---------------------------------------------------------------------------

def _render_warp_index(map_entries: list, game: str,
                        maps_data: list | None,
                        tile_entry: dict | None = None) -> list[str]:
    if not map_entries:
        return []

    lines = ["=" * 70, "  Warp Index", "=" * 70, ""]

    for map_entry in map_entries:
        map_label = (map_entry.get("map_name")
                     or map_entry.get("constant")
                     or map_entry.get("name", "?"))
        if len(map_entries) > 1:
            lines.append(f"  [ {map_label} ]")
            lines.append("")

        if game == "emerald":
            from emerald_data import em_dest_warp_coord
            warps = map_entry.get("warp_events", [])
            warp_lines = []
            for w in warps:
                dest_map = w.get("dest_map", "?")
                dest_id  = w.get("dest_warp_id", "0")
                coord    = em_dest_warp_coord(dest_map, dest_id)
                spawn    = (f"  →  spawn (col={coord[0]:>3}, row={coord[1]:>3})"
                            if coord else "")
                warp_lines.append(
                    f"    (col={w.get('x','?'):>3}, row={w.get('y','?'):>3})"
                    f"  →  {dest_map}  [warp #{dest_id}]{spawn}"
                )
        elif game == "heartgold":
            # From zone_event/*.json, not map_entry["warps"] — the latter is
            # misaligned in maps_pokeheartgold.json.
            from heartgold_data import (zone_warp_chars,
                                        hg_dest_warp_coord,
                                        hg_dynamic_warp_floors)
            col_off      = (tile_entry or {}).get("_tile_col_min", 0)
            row_off      = (tile_entry or {}).get("_tile_row_min", 0)
            internal     = (tile_entry or {}).get("internal_code", "")
            src_const    = (tile_entry or {}).get("constant", internal)
            _hg_wi_grid  = (tile_entry or {}).get("grid") or []
            _hg_wi_rows  = len(_hg_wi_grid)
            _hg_wi_cols  = len(_hg_wi_grid[0]) if _hg_wi_rows else 0
            _hg_wi_warps = list(zone_warp_chars(internal, col_off, row_off))
            _hg_wi_pos   = {(w2["r"], w2["c"]) for w2 in _hg_wi_warps}
            warp_lines = []
            for w in _hg_wi_warps:
                c, r      = w["c"], w["r"]
                header    = w["header"] or "?"
                char      = w["char"]
                anchor    = w.get("anchor")
                # Mirror the render-loop override: LADDER tiles are inter-floor
                # connections, not cave entrances.
                if _hg_wi_grid and 0 <= r < _hg_wi_rows and 0 <= c < _hg_wi_cols:
                    _wi_cell = _hg_wi_grid[r][c]
                    if _wi_cell and "LADDER" in (_wi_cell.get("behavior_name") or ""):
                        char = "L"
                if char == "D" and _hg_wi_grid:
                    ent = _find_warp_entrance(
                        r, c, _hg_wi_grid, _hg_wi_rows, _hg_wi_cols,
                        _hg_wi_pos, "heartgold",
                    )
                    if ent is not None:
                        r, c = ent
                if anchor == 256:
                    floors = hg_dynamic_warp_floors(src_const)
                    line   = (f"    [{char}]  (col={c:>3}, row={r:>3})"
                              f"  →  {header}  [warp #256]  (dynamic)")
                    for fc, fx, fz in floors:
                        line += f"\n               {fc}  →  spawn (col={fx:>3}, row={fz:>3})"
                    warp_lines.append(line)
                else:
                    coord = (hg_dest_warp_coord(header, anchor)
                             if anchor is not None else None)
                    spawn = (f"  →  spawn (col={coord[0]:>3}, row={coord[1]:>3})"
                             if coord else "")
                    warp_lines.append(
                        f"    [{char}]  (col={c:>3}, row={r:>3})"
                        f"  →  {header}{spawn}"
                    )
        else:
            from platinum_data import (pt_dest_warp_coord, pt_dynamic_warp_floors,
                                       _classify_warp)
            warps     = map_entry.get("warp_events", [])
            col_off   = (tile_entry or {}).get("_tile_col_min", 0)
            row_off   = (tile_entry or {}).get("_tile_row_min", 0)
            src_const = map_entry.get("constant", "")
            _pt_wi_grid = (tile_entry or {}).get("grid") or []
            _pt_wi_rows = len(_pt_wi_grid)
            _pt_wi_cols = len(_pt_wi_grid[0]) if _pt_wi_rows else 0
            _pt_wi_pos  = {
                (w2["z"] - row_off, w2["x"] - col_off)
                for w2 in warps
                if isinstance(w2.get("x"), int) and isinstance(w2.get("z"), int)
            }
            warp_lines = []
            for w in warps:
                if not (isinstance(w.get("x"), int) and isinstance(w.get("z"), int)):
                    continue
                lx       = w["x"] - col_off
                lz       = w["z"] - row_off
                dest     = w.get("dest_header_id", "?")
                dest_id  = w.get("dest_warp_id", 0)
                classified = _classify_warp(dest)
                char       = classified[0] if classified else "D"
                if char == "D" and _pt_wi_grid:
                    ent = _find_warp_entrance(
                        lz, lx, _pt_wi_grid, _pt_wi_rows, _pt_wi_cols,
                        _pt_wi_pos, "platinum",
                    )
                    if ent is not None:
                        lz, lx = ent
                if dest == "MAP_HEADER_DYNAMIC" or dest_id == 256:
                    floors = pt_dynamic_warp_floors(src_const)
                    line   = (f"    (col={lx:>3}, row={lz:>3})"
                              f"  →  {dest}  [warp #{dest_id}]  (dynamic)")
                    for fc, fx, fz in floors:
                        line += f"\n               {fc}  →  spawn (col={fx:>3}, row={fz:>3})"
                    warp_lines.append(line)
                else:
                    coord = pt_dest_warp_coord(dest, dest_id)
                    spawn = (f"  →  spawn (col={coord[0]:>3}, row={coord[1]:>3})"
                             if coord else "")
                    warp_lines.append(
                        f"    (col={lx:>3}, row={lz:>3})"
                        f"  →  {dest}  [warp #{dest_id}]{spawn}"
                    )

        if warp_lines:
            lines.append("  Warps (doors / building entrances):")
            lines.extend(warp_lines)
            lines.append("")

        conn_lines = []
        if game == "emerald":
            connections = map_entry.get("connections") or []
            for conn in connections:
                conn_lines.append(
                    f"    {conn.get('direction','?'):<8}  →  {conn.get('map','?')}"
                    f"  (offset: {conn.get('offset',0)})"
                )
        elif game == "heartgold":
            from heartgold_data import hg_get_connections
            zone_id = (tile_entry or {}).get("zone_id")
            if zone_id is not None:
                for conn in hg_get_connections(zone_id):
                    conn_lines.append(
                        f"    {conn['direction']:<8}  →  {conn['dest']}"
                    )
        else:
            from platinum_data import _get_connections
            for conn in _get_connections(map_entry):
                conn_lines.append(
                    f"    {conn.get('direction','?'):<8}  →  {conn.get('dest','?')}"
                )

        if conn_lines:
            lines.append("  Connections (adjacent areas):")
            lines.extend(conn_lines)
            lines.append("")

        if not warp_lines and not conn_lines:
            lines.append("  (no warps or connections on this map)")
            lines.append("")

    return lines



def _render_trainer_index(trainers: list) -> list[str]:
    if not trainers:
        return []

    lines = ["=" * 70, "  Trainer Index", "=" * 70, ""]

    for t in trainers:
        coord         = f"(col={t['x']:>3}, row={t['y']:>3})"
        trainer_const = t.get("trainer_const", "")
        cat_tag       = f"  [{t.get('category', 'Standard')}]"
        lines.append(
            f"  {coord}  {t.get('tr_class_disp', '?')} {t.get('tr_name', '?')}"
            f"  [{trainer_const}]{cat_tag}"
        )
        if t.get("intro_text"):
            lines.append(f"          Intro:      {t['intro_text']}")
        if t.get("defeat_text"):
            lines.append(f"          Defeat:     {t['defeat_text']}")
        if t.get("post_text"):
            lines.append(f"          Post-fight: {t['post_text']}")
        if t.get("items"):
            lines.append(f"          Items:      {', '.join(t['items'])}")
        lines.append(f"          Double:     {'Yes' if t.get('double') else 'No'}")
        lines.append("          Party:")

        for mon in t.get("party", []):
            species   = mon.get("species", "?")
            lvl       = mon.get("lvl", "?")
            iv_val    = mon.get("iv", 0)
            moves     = mon.get("moves", ["(unavailable)"])
            moves_str = ", ".join(
                mv.replace("_", " ").title() if mv.isupper() else mv
                for mv in moves
            )
            ab_str = mon.get("abilities_str", "?")
            held   = mon.get("item")
            true_iv = iv_val * 31 // 255
            lines.append(
                f"            {species.title():<16} Lv{lvl:<3}  IV={iv_val} ({true_iv}/31)"
                f"  Moves: {moves_str}"
                + (f"  Item: {held}" if held else "")
            )
            lines.append(f"            {'':16}        Ability: {ab_str}")

        if t.get("prize"):
            lines.append(f"          Prize: ₽{t['prize']}  ({t.get('prize_detail', '')})")

        for i, r in enumerate(t.get("vs_rematches", []), 1):
            r_party = r.get("party", [])
            r_prize = r.get("prize")
            prize_str = f"  Prize: ₽{r_prize}  ({r.get('prize_detail', '')})" if r_prize else ""
            lines.append(f"          VS Seeker rematch {i}:{prize_str}")
            for mon in r_party:
                species   = mon.get("species", "?")
                lvl       = mon.get("lvl", "?")
                iv_val    = mon.get("iv", 0)
                moves     = mon.get("moves", ["(unavailable)"])
                moves_str = ", ".join(
                    mv.replace("_", " ").title() if mv.isupper() else mv
                    for mv in moves
                )
                ab_str = mon.get("abilities_str", "?")
                held   = mon.get("item")
                true_iv = iv_val * 31 // 255
                lines.append(
                    f"            {species.title():<16} Lv{lvl:<3}  IV={iv_val} ({true_iv}/31)"
                    f"  Moves: {moves_str}"
                    + (f"  Item: {held}" if held else "")
                )
                lines.append(f"            {'':16}        Ability: {ab_str}")

        lines.append("")

    return lines


def _render_item_index(items: list) -> list[str]:
    if not items:
        return []
    lines = ["=" * 70, "  Item Balls", "=" * 70, ""]
    for it in items:
        coord = f"(col={it['x']:>3}, row={it['y']:>3})"
        tag = "  [Fossil]" if it.get("is_fossil") else ""
        lines.append(f"  {coord}  {it['item_name']}{tag}")
    lines.append("")
    return lines


def _render_hidden_item_index(hidden: list) -> list[str]:
    if not hidden:
        return []
    lines = ["=" * 70, "  Hidden Items", "=" * 70, ""]
    for it in hidden:
        coord = f"(col={it['x']:>3}, row={it['y']:>3})"
        lines.append(f"  {coord}  {it['item_name']}")
    lines.append("")
    return lines


def _render_field_object_index(field_objects: list) -> list[str]:
    if not field_objects:
        return []
    lines = ["=" * 70, "  Field Obstacles", "=" * 70, ""]
    for fo in field_objects:
        coord = f"(col={fo['x']:>3}, row={fo['y']:>3})"
        lines.append(f"  [{fo['char']}]  {coord}  {fo['label']}")
    lines.append("")
    return lines


def _render_berry_index(berries: list) -> list[str]:
    if not berries:
        return []
    lines = ["=" * 70, "  Berry Tree Index", "=" * 70, ""]
    for b in berries:
        coord = f"(col={b['x']:>3}, row={b['y']:>3})"
        lines.append(f"  {coord}  {b['berry_name']} Berry  [{b['berry_id']}]")
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
# Entry point
# ---------------------------------------------------------------------------

#: `--all` writes one file per *distinct terrain*, not one per map: 24 HeartGold
#: Pokecenter 1Fs are one grid stamped 24 times.  Maps sharing an already-written
#: grid are listed in duplicate_terrain.txt instead of getting a file of their own.
#: They are NOT identical files — a Pokecenter's NPCs and dialogue are per-map, so
#: skipping one does drop its event sections.  --keep-duplicates renders every map.
SKIP_DUPLICATE_TERRAIN = True

#: `--all` also skips maps the decomp marks as leftovers — Emerald's `Unused*`
#: layouts, both Gen 4 games' `*_UNUSED` constants, and Platinum's unidentified
#: `UNKNOWN_<id>` headers.  Keeping them was actively harmful: `UNKNOWN_255` sorts
#: one slot before Floaroma Meadow and holds the same grid, so it claimed the grid
#: and Floaroma Meadow got no file at all.  --include-unused restores them.
SKIP_UNUSED_MAPS = True


def main():
    args = sys.argv[1:]
    flags = {a for a in args if a.startswith("--") and a != "--all"}
    args  = [a for a in args if a not in flags]
    skip_duplicates = SKIP_DUPLICATE_TERRAIN and "--keep-duplicates" not in flags
    skip_unused     = SKIP_UNUSED_MAPS and "--include-unused" not in flags
    # --include-background-npcs: emit story/cutscene background NPCs (Cameron,
    # Galactic Grunts group sprites, etc.).  Gating logic lives in the per-game
    # data modules; this flag is parsed for CLI completeness.
    _include_bg = "--include-background-npcs" in flags
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)

    game = args[0].lower()
    if game not in ("emerald", "heartgold", "platinum"):
        sys.exit(f"Unknown game '{game}'. Choose: emerald | heartgold | platinum")

    # All three games now render from the decomp and read no generated JSON.
    # Terrain is loaded one map at a time, so nothing holds every grid at once.
    tiles         = []
    maps_data     = None
    interior_dims = em_interior_dims() if game == "emerald" else {}
    wild_enc      = load_wild_encounters() if game == "emerald" else {}

    const_field = "layout_id" if game == "emerald" else "constant"
    name_field  = "layout_name" if game == "emerald" else "name"

    if args[1] == "--all":
        out_dir = Path(args[2]) if len(args) >= 3 else BASE_DIR / f"terrain_maps_{game}"
        out_dir.mkdir(parents=True, exist_ok=True)
        # Every game iterates its own registry; grids are loaded per map below.
        source = (hg_map_registry_entries() if game == "heartgold" else
                  pt_map_registry_entries() if game == "platinum" else
                  em_layout_registry_entries())
        if skip_unused:
            kept = [e for e in source
                    if not skip_map(game, e.get(const_field), e.get(name_field))]
            if len(kept) != len(source):
                print(f"Excluding {len(source) - len(kept)} unused/unidentified maps.")
            source = kept
        print(f"Rendering {len(source)} maps to {out_dir}/...")
        # Fixed set, so an empty category is an empty directory rather than a
        # missing one — "no caves" and "caves not classified" look different.
        for sub in ("all", "routes", "towns", "caves"):
            (out_dir / sub).mkdir(parents=True, exist_ok=True)
        written: Counter = Counter()
        # sig -> (fname, label, category)  — tracks canonical per terrain group
        seen_terrain: dict[str, tuple[str, str, str | None]] = {}
        duplicates:   list[tuple[str, str]] = []
        for i, entry in enumerate(source):
            # Read the category off the registry entry BEFORE slicing: the tile
            # entry that replaces it below carries terrain, not identity.
            # Emerald is layout-driven, so one layout can serve maps of several
            # types (a route's cave shares nothing, but a town's houses do) —
            # the category of the layout is whichever type most of its maps
            # agree on.
            if game == "emerald":
                types = [m.get("map_type") for m
                         in em_map_entries(entry.get(const_field, ""))]
                mtype = Counter(t for t in types if t).most_common(1)
                mtype = mtype[0][0] if mtype else None
            else:
                mtype = entry.get("map_type")
            category = map_category(game, mtype)
            if game == "heartgold":
                sliced = hg_get_zone_tile_entry(tiles, entry.get(const_field, ""), maps_data)
                if sliced is None:
                    continue
                entry = sliced
            elif game == "platinum":
                sliced = pt_get_zone_tile_entry(tiles, entry.get(const_field, ""), maps_data)
                if sliced is None:
                    continue
                entry = sliced
            else:
                sliced = em_layout_tile_entry(entry.get(const_field, ""))
                if sliced is None:
                    continue
                entry = sliced
            label = entry.get(name_field) or entry.get(const_field) or f"map_{i:04d}"
            if skip_duplicates:
                sig = terrain_signature(entry)
                canon = seen_terrain.get(sig)
                if canon is not None:
                    canon_fname, canon_label, canon_cat = canon
                    # Append this map's events to the canonical .txt (terrain view).
                    events_text = render_events_only(
                        entry, game, maps_data, wild_encounters=wild_enc,
                        include_background=_include_bg)
                    for sub in (["all"] + ([canon_cat] if canon_cat else [])):
                        target = out_dir / sub / f"{canon_fname}.txt"
                        if target.exists():
                            with open(target, "a", encoding="utf-8") as f:
                                f.write(events_text)
                    # Each map gets its own JSON with its own NPCs/events.
                    dup_fname = safe_filename(
                        entry.get(const_field) or entry.get(name_field) or f"map_{i:04d}"
                    )
                    dup_jdata = render_json(entry, game, maps_data, wild_encounters=wild_enc,
                                            include_background=_include_bg)
                    dup_jtxt  = json.dumps(dup_jdata, ensure_ascii=False, indent=2)
                    (out_dir / "all" / f"{dup_fname}.json").write_text(dup_jtxt, encoding="utf-8")
                    if canon_cat:
                        (out_dir / canon_cat / f"{dup_fname}.json").write_text(dup_jtxt, encoding="utf-8")
                    duplicates.append((label, canon_label))
                    continue
                seen_terrain[sig] = (
                    safe_filename(entry.get(const_field) or entry.get(name_field) or f"map_{i:04d}"),
                    label,
                    category,
                )

            fname = safe_filename(
                entry.get(const_field) or entry.get(name_field) or f"map_{i:04d}"
            )
            text = render_terrain(entry, game, maps_data, interior_dims=interior_dims,
                                  wild_encounters=wild_enc, include_background=_include_bg)
            jdata = render_json(entry, game, maps_data, wild_encounters=wild_enc,
                                include_background=_include_bg)
            jtxt  = json.dumps(jdata, ensure_ascii=False, indent=2)
            # `all/` holds everything; the category dirs are views onto it, so a
            # route is written twice on purpose rather than moved.
            (out_dir / "all" / f"{fname}.txt").write_text(text, encoding="utf-8")
            (out_dir / "all" / f"{fname}.json").write_text(jtxt, encoding="utf-8")
            written["all"] += 1
            if category:
                (out_dir / category / f"{fname}.txt").write_text(text, encoding="utf-8")
                (out_dir / category / f"{fname}.json").write_text(jtxt, encoding="utf-8")
                written[category] += 1

        if duplicates:
            # Grouped under the map that claimed the grid.  Each indented entry
            # was terrain-identical and had its events merged into that file.
            by_first: dict[str, list[str]] = {}
            for dup, first in duplicates:
                by_first.setdefault(first, []).append(dup)
            lines = [f"{len(duplicates)} maps share terrain with another map — their "
                     f"events are merged into that file under a [ Map Name ] section.",
                     ""]
            for first in sorted(by_first):
                lines.append(f"{first}")
                lines += [f"    {d}" for d in sorted(by_first[first])]
            (out_dir / "all" / "duplicate_terrain.txt").write_text(
                "\n".join(lines) + "\n", encoding="utf-8")
            print(f"Merged {len(duplicates)} terrain-identical maps into their "
                  f"canonical files (see {out_dir}/all/duplicate_terrain.txt).")
        print("  " + "  ".join(f"{k}: {written[k]}"
                               for k in ("all", "routes", "towns", "caves")))
        # Write map_graph.json at the output root.
        from map_graph import build_graph
        graph_data = build_graph(game, include_unused=not skip_unused)
        (out_dir / "map_graph.json").write_text(
            json.dumps(graph_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Written map_graph.json  ({len(graph_data['nodes'])} nodes, "
              f"{len(graph_data['edges'])} edges)")
        print("Run tools/export_pokemon_data.py to generate pokemon_data.json, "
              "moves_data.json, abilities_data.json, exp_thresholds.json.")
        print("Done.")
        return

    query = args[1]
    if game == "heartgold":
        entry = hg_get_zone_tile_entry(tiles, query, maps_data)
    elif game == "platinum":
        entry = pt_get_zone_tile_entry(tiles, query, maps_data)
    else:
        # Resolve against layout metadata (no grids), then load just that grid.
        meta  = find_tile_entry(em_layout_registry_entries(), query, game)
        entry = em_layout_tile_entry(meta["layout_id"]) if meta else None
    if entry is None:
        sys.exit(f"Map '{query}' not found in {game} tile data.")

    text = render_terrain(entry, game, maps_data, interior_dims=interior_dims,
                          wild_encounters=wild_enc, include_background=_include_bg)

    if len(args) >= 3:
        out_path = Path(args[2])
        out_path.write_text(text, encoding="utf-8")
        jdata    = render_json(entry, game, maps_data, wild_encounters=wild_enc,
                               include_background=_include_bg)
        json_path = out_path.with_suffix(".json")
        json_path.write_text(json.dumps(jdata, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Written to {out_path} + {json_path.name}")
    else:
        print(text)


if __name__ == "__main__":
    main()
