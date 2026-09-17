"""
Cross-check every .txt/.json pair in a terrain_maps_{game}_pairs/all/ directory.
Checks every encounter sub-type, trainer data, NPC data, gym leaders, warps, items.
Reports any discrepancy between TXT and JSON.
"""
import json
import re
import sys
from pathlib import Path


# ── TXT field extractors ─────────────────────────────────────────────────────

def _canonical(txt: str) -> str:
    """Strip merged-map sections — only check the canonical map's content."""
    return txt.split("\n  [ ")[0]


def txt_has(txt: str, label: str) -> bool:
    return label in _canonical(txt)


def txt_section_nonempty(txt: str, header: str) -> bool:
    return txt_count_col_entries(txt, header) > 0


def txt_count_col_entries(txt: str, header: str) -> int:
    """Count '  (col=' entries in a section. Skips the first === underline."""
    canon = _canonical(txt)
    in_sec = False
    sep_count = 0
    count = 0
    for line in canon.splitlines():
        if header in line and not in_sec:
            in_sec = True
            sep_count = 0
            continue
        if in_sec:
            if re.match(r'\s*={3,}', line):
                sep_count += 1
                if sep_count >= 2:  # second === ends the section
                    break
                continue
            if re.match(r'  \(col=', line):
                count += 1
    return count


def txt_trainer_ids(txt: str) -> set:
    return set(re.findall(r'\[(TRAINER_\w+)\]', txt))


def _count_npc_section(txt: str) -> int:
    """Count all NPC (col=) entries across the full NPC Index section including sub-sections."""
    in_sec = False
    sep_count = 0
    count = 0
    for line in txt.splitlines():
        if "NPC Index" in line and not in_sec:
            in_sec = True
            sep_count = 0
            continue
        if in_sec:
            if re.match(r'\s*={3,}', line):
                sep_count += 1
                if sep_count >= 2:
                    break
                continue
            if re.match(r'  \(col=', line):
                count += 1
    return count


def _count_item_section(txt: str) -> int:
    """Count all item (col=) entries across Item Balls and Hidden Items sections
    including sub-sections (for EM layouts with merged maps)."""
    count = 0
    for header in ("Item Balls", "Hidden Items"):
        in_sec = False
        sep_count = 0
        for line in txt.splitlines():
            if header in line and not in_sec:
                in_sec = True
                sep_count = 0
                continue
            if in_sec:
                if re.match(r'\s*={3,}', line):
                    sep_count += 1
                    if sep_count >= 2:
                        break
                    continue
                if re.match(r'  \(col=', line):
                    count += 1
    return count


def txt_warp_count(txt: str) -> int:
    """Count warp entries. HG uses '[x] (col=' format; PT/EM use plain '(col=' under 'Warps (doors'."""
    canon = _canonical(txt)
    # HG style: [single-char] (col=
    hg_warps = len(re.findall(r'^\s+\[[a-z]\]\s+\(col=', canon, re.MULTILINE))
    if hg_warps > 0:
        return hg_warps
    # PT/EM style: count (col= entries within the "Warps (doors" subsection
    in_warps = False
    count = 0
    for line in canon.splitlines():
        if "Warps (doors" in line:
            in_warps = True
            continue
        if in_warps:
            if line.strip().startswith("Connections") or re.match(r'\s*={3,}', line):
                break
            if re.match(r'\s+\(col=', line):
                count += 1
    return count


# ── Per-game encounter checks ─────────────────────────────────────────────────

def check_encounters(json_path: Path, jd: dict, txt: str, game: str) -> list[str]:
    issues = []
    stem = json_path.stem
    canon = _canonical(txt)
    enc = jd.get("encounters", {})

    # Is Wild Encounters section present at all?
    # Safari Zone maps use "HeartGold Safari Zone" instead of "Wild Encounters"
    txt_has_enc = "Wild Encounters" in canon or "HeartGold Safari Zone" in canon
    json_has_enc = bool(enc)
    if txt_has_enc and not json_has_enc:
        issues.append(f"{stem}: TXT has Wild Encounters section but JSON missing 'encounters'")
        return issues  # rest of checks meaningless
    if json_has_enc and not txt_has_enc:
        issues.append(f"{stem}: JSON has 'encounters' but TXT has no Wild Encounters section")
        return issues

    if not txt_has_enc:
        return issues  # no encounters expected on either side

    # Safari Zone maps: standard grass/surf/fishing appear inside area tables, not as top-level keys
    if enc.get("safari") is not None:
        return issues

    # Grass / Tall Grass
    txt_grass = "Tall Grass" in canon
    json_grass = "grass" in enc
    if txt_grass and not json_grass:
        issues.append(f"{stem}: TXT has Tall Grass but JSON missing 'encounters.grass'")
    if json_grass and not txt_grass:
        issues.append(f"{stem}: JSON has 'encounters.grass' but TXT has no Tall Grass section")

    if json_grass:
        slots = enc["grass"].get("slots", [])
        if not slots:
            issues.append(f"{stem}: encounters.grass has no slots")

    # Time-of-day (HG morning/night, PT day/night)
    if game == "heartgold":
        txt_morning = "Morning" in canon
        txt_night   = "Night" in canon and "Wild Enc" in canon
        json_tod    = enc.get("grass", {}).get("time_overrides")
        if txt_morning and not json_tod:
            issues.append(f"{stem}: TXT has morning/night splits but JSON missing grass.time_overrides")

    if game == "platinum":
        txt_day   = "Day-only slots" in canon
        txt_night = "Night-only slots" in canon
        json_tod  = enc.get("grass", {}).get("time_overrides")
        if (txt_day or txt_night) and not json_tod:
            issues.append(f"{stem}: TXT has day/night slots but JSON missing grass.time_overrides")

    # Land Swarm (must be "  Swarm:" not "Surf Swarm:" or "Fish Swarm:")
    txt_swarm = bool(re.search(r'^\s+Swarm: ', canon, re.MULTILINE))
    json_swarm = enc.get("grass", {}).get("swarm") is not None
    if txt_swarm and not json_swarm:
        issues.append(f"{stem}: TXT has land Swarm but JSON grass.swarm is null/missing")

    # Surfing
    txt_surf = "Surfing" in canon
    json_surf = "surf" in enc
    if txt_surf and not json_surf:
        issues.append(f"{stem}: TXT has Surfing but JSON missing 'encounters.surf'")
    if json_surf and not txt_surf:
        issues.append(f"{stem}: JSON has 'encounters.surf' but TXT has no Surfing section")

    # Fishing
    txt_fishing = "Fishing" in canon
    json_fishing = "fishing" in enc
    if txt_fishing and not json_fishing:
        issues.append(f"{stem}: TXT has Fishing but JSON missing 'encounters.fishing'")
    if json_fishing and not txt_fishing:
        issues.append(f"{stem}: JSON has 'encounters.fishing' but TXT has no Fishing section")

    # Rock Smash (only the encounter table, not the legend or warp entries)
    txt_rock = bool(re.search(r'Rock Smash\s+\(rate:', canon))
    json_rock = "rock_smash" in enc
    if txt_rock and not json_rock:
        issues.append(f"{stem}: TXT has Rock Smash encounters but JSON missing 'encounters.rock_smash'")

    # HG-specific
    if game == "heartgold":
        if "Hoenn Sound" in canon and "hoenn_sound" not in enc:
            issues.append(f"{stem}: TXT has Hoenn Sound but JSON missing 'encounters.hoenn_sound'")
        if "Sinnoh Sound" in canon and "sinnoh_sound" not in enc:
            issues.append(f"{stem}: TXT has Sinnoh Sound but JSON missing 'encounters.sinnoh_sound'")
        if "Night fishing" in canon and "night_fishing" not in enc:
            issues.append(f"{stem}: TXT has Night fishing but JSON missing 'encounters.night_fishing'")
        if "Surf Swarm" in canon and not enc.get("_swarms", {}).get("surfSwarm"):
            issues.append(f"{stem}: TXT has Surf Swarm but JSON missing '_swarms.surfSwarm'")
        if "Fish Swarm" in canon and not enc.get("_swarms", {}).get("fishSwarm"):
            issues.append(f"{stem}: TXT has Fish Swarm but JSON missing '_swarms.fishSwarm'")

    # PT-specific
    if game == "platinum":
        if "PokéRadar" in canon and "_radar" not in enc:
            issues.append(f"{stem}: TXT has PokéRadar but JSON missing 'encounters._radar'")
        for cart in ("Ruby", "Sapphire", "Emerald", "FireRed", "LeafGreen"):
            if f"{cart} inserted" in canon:
                key = cart.lower().replace(" ", "_")
                if not enc.get("gba_slots", {}).get(key):
                    issues.append(f"{stem}: TXT has {cart} inserted but JSON missing 'encounters.gba_slots.{key}'")
        if re.search(r'Daily \(Mr\. Backlot', canon) and "daily_pool" not in enc:
            issues.append(f"{stem}: TXT has Trophy Garden daily pool but JSON missing 'encounters.daily_pool'")
        if re.search(r'Daily \(binoculars', canon) and "great_marsh_daily" not in enc:
            issues.append(f"{stem}: TXT has Great Marsh daily pool but JSON missing 'encounters.great_marsh_daily'")

    # PT honey trees (map-level flag, not per-map encounters section)
    if game == "platinum":
        if "Honey Tree" in canon and "honey_trees" not in enc:
            issues.append(f"{stem}: TXT has Honey Tree section but JSON missing 'encounters.honey_trees'")

    # HG Safari Zone
    if game == "heartgold":
        if "HeartGold Safari Zone" in canon and "safari" not in enc:
            issues.append(f"{stem}: TXT has Safari Zone encounters but JSON missing 'encounters.safari'")

    # Slot count sanity
    grass = enc.get("grass", {})
    if grass.get("slots") and len(grass["slots"]) not in (10, 12):
        issues.append(f"{stem}: grass slots = {len(grass['slots'])} (expected 10 or 12)")

    return issues


# ── Main audit ────────────────────────────────────────────────────────────────

def audit_dir(pairs_dir: Path, game: str) -> dict:
    issues = []
    ok = 0
    gym_leaders_found = []

    for json_path in sorted(pairs_dir.glob("*.json")):
        if json_path.name in ("duplicate_terrain.txt",):
            continue
        txt_path = json_path.with_suffix(".txt")
        if not txt_path.exists():
            issues.append(f"MISSING TXT for {json_path.name}")
            continue

        try:
            jd = json.load(json_path.open())
        except Exception as e:
            issues.append(f"JSON PARSE ERROR {json_path.name}: {e}")
            continue

        txt  = txt_path.read_text(encoding="utf-8")
        stem = json_path.stem

        # ── NPC count — scan full NPC Index section including sub-sections ──
        txt_npcs  = _count_npc_section(txt)
        json_npcs = len(jd.get("npcs", []))
        if json_npcs > txt_npcs:
            issues.append(f"{stem}: NPC count json={json_npcs} > txt={txt_npcs}")

        # ── Trainer IDs ──
        txt_tr_ids  = txt_trainer_ids(txt)
        json_tr_ids = {t["trainer_id"] for t in jd.get("trainers", []) if not t.get("is_leader")}
        missing_in_txt = json_tr_ids - txt_tr_ids
        # PT trainers use coord-based IDs that don't appear bracketed in TXT — skip check for PT
        if game != "platinum" and missing_in_txt:
            issues.append(f"{stem}: trainers in JSON not in TXT: {missing_in_txt}")

        # ── Gym leaders ──
        leaders = [t for t in jd.get("trainers", []) if t.get("is_leader")]
        txt_gl_ids = set(re.findall(r'\[(TRAINER_\w+)\]', txt))
        for gl in leaders:
            gym_leaders_found.append((stem, gl["trainer_id"]))
            if gl["sight_range"] != 0:
                issues.append(f"{stem}: gym leader {gl['trainer_id']} sight_range={gl['sight_range']} (expected 0)")
            if gl["sight_direction"] != "ALL":
                issues.append(f"{stem}: gym leader {gl['trainer_id']} sight_direction={gl['sight_direction']} (expected ALL)")
            if gl["trainer_id"] not in txt_gl_ids and gl["trainer_id"] not in txt_trainer_ids(txt):
                issues.append(f"{stem}: gym leader {gl['trainer_id']} not found in TXT")

        # ── Trainer party completeness ──
        for t in jd.get("trainers", []):
            if not t.get("party") and t.get("trainer_id", "").startswith("TRAINER_"):
                issues.append(f"{stem}: trainer {t['trainer_id']} has empty party")

        # ── NPC movement sanity ──
        for n in jd.get("npcs", []):
            mv = n.get("movement", "")
            if not mv or mv.isdigit():
                issues.append(f"{stem}: NPC {n.get('npc_id')} has bad movement='{mv}'")

        # ── Warp count sanity — only flag when TXT has more warps than JSON ──
        json_wc = len(jd.get("warps", []))
        txt_wc  = txt_warp_count(txt)
        if txt_wc > json_wc + 2:
            issues.append(f"{stem}: warp count json={json_wc} < txt={txt_wc} (missing from JSON)")

        # ── Encounter checks (all sub-types) ──
        issues += check_encounters(json_path, jd, txt, game)

        # ── Items ──
        txt_items  = _count_item_section(txt)
        json_items = len(jd.get("items", []))
        if json_items > txt_items and txt_items > 0:
            issues.append(f"{stem}: item count json={json_items} > txt={txt_items}")

        ok += 1

    return {"ok": ok, "issues": issues, "gym_leaders": gym_leaders_found}


if __name__ == "__main__":
    for game in ("emerald", "heartgold", "platinum"):
        d = Path(f"terrain_maps_{game}_pairs/all")
        if not d.exists():
            print(f"SKIP {game}: {d} not found")
            continue
        print(f"\n{'='*60}")
        print(f"  {game.upper()}")
        print(f"{'='*60}")
        result = audit_dir(d, game)
        print(f"  Maps checked:    {result['ok']}")
        print(f"  Gym leaders:     {len(result['gym_leaders'])}")
        for stem, tid in result['gym_leaders']:
            print(f"    {stem}: {tid}")
        if result["issues"]:
            print(f"\n  ISSUES ({len(result['issues'])}):")
            for iss in result["issues"][:50]:
                print(f"    {iss}")
            if len(result["issues"]) > 50:
                print(f"    ... and {len(result['issues'])-50} more")
        else:
            print("  No issues found.")
