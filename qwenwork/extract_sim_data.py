#!/usr/bin/env python3
"""extract_sim_data.py — build the four canonical simulator JSONs (SIMULATOR_AUDIT.md §6.2):

  qwenwork/sim_data/pokemon_data.json   display/world data + typed evolutions
  qwenwork/sim_data/moves_ui.json       move display entries (referenced ∪ gen<=4)
  qwenwork/sim_data/abilities_ui.json   ability display entries (referenced slots)
  qwenwork/sim_data/items.json          item pool (gen3 ∪ gen4) + closed battleEffect

Sources (READ-ONLY):
  qwenwork/sim_data/dex_dump.json  <- node qwenwork/dex_dump.js (fork PS dex = battle authority)
  pokeplatinum/res/pokemon/<id>/data.json        donor: world fields, learnsets, evolutions
  pokeheartgold/files/poketool/personal/personal.json   cross-check only
  pokeplatinum/res/items/pl_item_data.csv        item pool + effect columns
  pokeemerald/include/constants/items.h          gen3-only item ids
  pokeplatinum/src/item.c sTMHMMoves / pokeheartgold/src/item.c sTMHMMoves / pokeemerald tms_hms.h
  pokeplatinum/src/battle/battle_script.c        ball catch modifiers
  terrain_maps_{emerald,heartgold,platinum}/all  rival fixed-move sets + map item refs
  pokeplatinum/res/trainers/data/*.json, pokeheartgold/files/poketool/trainer/trainers.json

Design rules (audit §6.2): invalid input => HARD FAIL, never silent null.
Battle semantics live in the daemon; these JSONs are display data plus a closed
battleEffect enum for the future daemon op:"item".

Run:  python3 qwenwork/extract_sim_data.py
"""
import csv
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SIMD = HERE / "sim_data"

WARNS = []
def warn(msg): WARNS.append(msg); return None

def die(msg):
    print(f"HARD FAIL: {msg}", file=sys.stderr)
    sys.exit(1)

def norm(s):
    """collapse ROM-constant / display-name / dex-id into one comparable key"""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())

DEX = json.loads((SIMD / "dex_dump.json").read_text())

spec_norm = {norm(k): k for k in DEX["species"]}
move_norm = {norm(k): k for k in DEX["moves"]}  # display names collapse onto ids too
abil_norm = {norm(k): k for k in DEX["abilities"]}
abil_norm.update({norm(v["name"]): k for k, v in DEX["abilities"].items()})
item_norm = {norm(k): k for k, v in DEX["items"].items()}
item_norm.update({norm(v["name"]): k for k, v in DEX["items"].items()})

# Moves PS re-spelled in later generations; ROM constants must resolve to the
# current dex ids (verified present in the dump below).
ROM_MOVE_RENAMES = {"faintattack": "feintattack", "hijumpkick": "highjumpkick",
                    "vicegrip": "visegrip", "smellingsalt": "smellingsalts"}
def m2id(const):
    m = re.fullmatch(r"MOVE_(.+)", const) or re.fullmatch(r"[A-Za-z][A-Za-z _0-9]*", const or "")
    if not m: die(f"bad move const {const!r}")
    k = norm(m.group(1) if m.groups() else m.group(0))
    if k not in move_norm:
        k = ROM_MOVE_RENAMES.get(k, k)
        if k not in move_norm: die(f"move {const} not in dex")
    return move_norm[k]

def sp2num(const):
    m = re.fullmatch(r"SPECIES_(.+)", const)
    if not m: die(f"bad species const {const!r}")
    k = norm(m.group(1))
    if k not in spec_norm: die(f"species {const} not in dex base list")
    return DEX["species"][spec_norm[k]]["num"]

def ab2id(const):
    m = re.fullmatch(r"(?:ABILITY_)?(.+)", const)
    if not m: die(f"bad ability const {const!r}")
    k = norm(m.group(1))
    if k not in abil_norm: die(f"ability {const} not in dex")
    return abil_norm[k]

# ---------------------------------------------------------------- ROM TM/HM
def parse_tmhm_tables():
    pt = {}
    src = (ROOT / "pokeplatinum/src/item.c").read_text()
    for m in re.finditer(r"\[TMHM_ID\((TM\d\d|HM\d\d)\)\]\s*=\s*(MOVE_[A-Z0-9_]+)", src):
        key = m.group(1)
        if key.startswith("HM"):
            key = "HM" + str(int(key[2:]))  # ROM pads HM01..HM08; normalize to HM1..HM8
        pt[key] = m.group(2)
    if len(pt) != 100: die(f"PT sTMHMMoves parsed {len(pt)} != 100")

    hg = {}
    body = re.search(r"sTMHMMoves\[\]\s*=\s*\{(.*?)\};",
                     (ROOT / "pokeheartgold/src/item.c").read_text(), re.S)
    if not body: die("HG sTMHMMoves not found")
    entries = re.findall(r"(MOVE_[A-Z0-9_]+)", body.group(1))
    if len(entries) != 100: die(f"HG sTMHMMoves {len(entries)} != 100")
    for i, mv in enumerate(entries):
        hg[f"TM{i+1:02d}" if i < 92 else f"HM{i-91}"] = mv

    em = {}
    hdr = (ROOT / "pokeemerald/include/constants/tms_hms.h").read_text()
    def foreach_block(name):
        i = hdr.index(f"FOREACH_{name}(F)")
        j = hdr.find("#define", i + 10)
        return hdr[i:j if j > 0 else len(hdr)]
    for name, count in [("TM", 50), ("HM", 8)]:
        names = re.findall(r"\bF\(([A-Z_0-9]+)\)", foreach_block(name))
        if len(names) != count: die(f"emerald {name} {len(names)} != {count}")
        for i, n in enumerate(names):
            em[f"{name}{i+1:02d}" if name == "TM" else f"{name}{i+1}"] = "MOVE_" + n
    return pt, hg, em

PT_TM, HG_TM, EM_TM = parse_tmhm_tables()

# ---------------------------------------------------------------- species donor
GENDER = {"GENDER_RATIO_FEMALE_12_5": 0.125, "GENDER_RATIO_FEMALE_25": 0.25,
          "GENDER_RATIO_FEMALE_50": 0.5, "GENDER_RATIO_FEMALE_75": 0.75,
          "GENDER_RATIO_FEMALE_87_5": 0.875, "GENDER_RATIO_MALE_ONLY": 0.0,
          "GENDER_RATIO_FEMALE_ONLY": 1.0, "GENDER_RATIO_NO_GENDER": -1.0}
STATK = {"hp": "hp", "attack": "atk", "defense": "def", "special_attack": "spa",
         "special_defense": "spd", "speed": "spe"}
EVO_SPECIAL = {"EVO_LEVEL_MAGNETIC_FIELD", "EVO_LEVEL_MOSS_ROCK",
               "EVO_LEVEL_ICE_ROCK", "EVO_LEVEL_PID_LOW", "EVO_LEVEL_PID_HIGH",
               "EVO_LEVEL_ATK_LT_DEF", "EVO_LEVEL_ATK_GT_DEF", "EVO_LEVEL_ATK_EQ_DEF",
               "EVO_LEVEL_NINJASK", "EVO_LEVEL_SHEDINJA", "EVO_LEVEL_SPECIES_IN_PARTY"}

def map_evo(e, evo_ref_moves):
    method = e[0]
    if method == "EVO_LEVEL":
        lvl, to = e[1], e[2]
        if not (1 <= lvl <= 100): die(f"evolution level {lvl} out of range: {e}")
        return {"to": sp2num(to), "condition": "level", "level": lvl}
    if method == "EVO_LEVEL_KNOW_MOVE":
        mid = m2id(e[1]); evo_ref_moves.append(mid)
        return {"to": sp2num(e[2]), "condition": "move", "move": mid}
    if method in ("EVO_LEVEL_HAPPINESS", "EVO_LEVEL_HAPPINESS_DAY", "EVO_LEVEL_HAPPINESS_NIGHT"):
        r = {"to": sp2num(e[1]), "condition": "friendship", "threshold": 220}
        if method.endswith("_DAY"): r["timeOfDay"] = "day"
        if method.endswith("_NIGHT"): r["timeOfDay"] = "night"
        return r
    if method in ("EVO_USE_ITEM", "EVO_USE_ITEM_MALE", "EVO_USE_ITEM_FEMALE"):
        r = {"to": sp2num(e[2]), "condition": "item", "item": e[1]}
        if method.endswith("_MALE"): r["gender"] = "M"
        if method.endswith("_FEMALE"): r["gender"] = "F"
        return r
    if method == "EVO_TRADE":
        return {"to": sp2num(e[1]), "condition": "trade"}
    if method == "EVO_TRADE_WITH_HELD_ITEM":
        return {"to": sp2num(e[2]), "condition": "trade", "item": e[1]}
    if method in ("EVO_LEVEL_WITH_HELD_ITEM_DAY", "EVO_LEVEL_WITH_HELD_ITEM_NIGHT"):
        # level-triggered by construction; ROM entry carries no numeric level.
        # Distinct condition (was mislabeled "item" + levelTriggered:true): the ROM
        # requires the mon to HOLD the item and level up in the day/night window
        # (pokemon.c:3655) — feeding the item does nothing and it is not consumed.
        return {"to": sp2num(e[2]), "condition": "levelWithHeldItem", "item": e[1],
                "timeOfDay": "day" if method.endswith("_DAY") else "night"}
    if method in ("EVO_LEVEL_MALE", "EVO_LEVEL_FEMALE"):
        return {"to": sp2num(e[2]), "condition": "level", "level": e[1],
                "gender": "M" if method.endswith("_MALE") else "F"}
    if method == "EVO_LEVEL_BEAUTY":
        # v1 decision (2026-09-18): beauty is unimplementable (contests cut) —
        # Feebas→Milotic becomes the gen-5 use-item evolution: feed ITEM_PRISM_SCALE
        # (synthetic pool entry; no gen-4 ROM contains it). The 170 beauty threshold
        # — NOT a level — is kept in `extra` for provenance.
        return {"to": sp2num(e[2]), "condition": "item", "item": "ITEM_PRISM_SCALE",
                "extra": [f"ROM gate EVO_LEVEL_BEAUTY beauty>={e[1]} dropped for v1"]}
    if method in EVO_SPECIAL:
        # v1 semantics are ROM-faithful per SIMULATOR_AUDIT.md §6.2 contract (map
        # gates kept; MAGNETIC_FIELD is the single level-up substitution; PID_* byte
        # roll; stat/party checks kept). `level` = genuine ROM param, MUST be read.
        # The old "never triggers" stance is superseded (2026-09-18).
        rest = [str(x) for x in e[1:-1]]
        out = {"to": sp2num(e[-1]), "condition": "special", "note": method}
        if len(e) >= 3 and isinstance(e[1], int): out["level"] = e[1]
        if rest: out["extra"] = rest
        return out
    die(f"unknown evolution method {method} in {e}")

def held_pair(v):
    """held_items common/rare: None | 'ITEM_X' | {'item':..,'chance':..}"""
    if isinstance(v, dict): v = v.get("item")
    if not v or v == "ITEM_NONE": return None
    return v

donor = {}
for f in sorted((ROOT / "pokeplatinum/res/pokemon").glob("*/data.json")):
    k = norm(f.parent.name)
    if k not in spec_norm:  # forme folder (rotom_fan, deoxys_attack, ...) — v1 base-only
        continue
    if k in donor: die(f"duplicate donor folder for {k}")
    donor[k] = json.loads(f.read_text())
missing = [k for k in spec_norm if k not in donor]
if len(donor) != 493 or missing: die(f"donor has {len(donor)} base species; missing {missing[:10]}")

# ---------------------------------------------------------------- items pool
csv_rows = list(csv.DictReader(open(ROOT / "pokeplatinum/res/items/pl_item_data.csv")))
by_item = {r["item"]: r for r in csv_rows}

emerald_names = [m.group(1) for m in re.finditer(
    r"#define\s+(ITEM_[A-Z0-9_]+)\s+\d+",
    (ROOT / "pokeemerald/include/constants/items.h").read_text())
    if m.group(1) != "ITEM_NONE"]

pool = {}
for r in csv_rows:
    if r["item"] != "ITEM_NONE":
        pool[r["item"]] = {"games": {"platinum", "heartgold"}}  # gen-4 universe is shared
for n in emerald_names:
    body = n[len("ITEM_"):]
    if re.fullmatch(r"[0-9A-F]{3,}", body):
        continue  # hex-index placeholder constants (ITEM_034...), not real items
    if n in pool: pool[n]["games"].add("emerald")
    else: pool[n] = {"games": {"emerald"}}

def humanize(const):
    m = re.fullmatch(r"ITEM_(TM|HM)(\d+)", const)
    if m:  # PS deleted TM/HM items from the modern dex; ROM display style is "TM01"
        return f"{m.group(1)}{int(m.group(2)):02d}"
    w = const[len("ITEM_"):].replace("_", " ").lower()
    return " ".join(p.capitalize() for p in w.split())

STATUS_COLS = {"healSleep": "sleep", "healPoison": "poison", "healBurn": "burn",
               "healFreeze": "freeze", "healParalysis": "paralysis",
               "healConfusion": "confusion", "healAttract": "attract"}

def derive_effect(name, r):
    """closed enum (audit §6.2): evolve | levelup | revive:<f> | heal:<n> | healfull |
    healfullstatus | cureall | status:<st> | boost:<stat>:<n> | cureblock | ppup |
    ppmax | pprestore:<n> | pprestore:all   (else null)"""
    if r["fieldUseFunc"] == "ITEM_USE_FUNC_EVO_STONE" or r["evolve"] == "true":
        return "evolve"
    if r["fieldPocket"] == "POCKET_BALLS" or name == "ITEM_MASTER_BALL":
        return None  # catch behaviour carried in catchBonus instead
    if r["levelUp"] == "true":
        return "levelup"
    if r["ppUp"] == "true": return "ppup"
    if r["ppMax"] == "true": return "ppmax"
    if r["ppRestoreAll"] == "true": return "pprestore:all"
    if r["ppRestore"] == "true":
        n = int(r["ppRestored"])
        return "pprestore:all" if n >= 127 else f"pprestore:{n}"
    heal_pocket = r["fieldPocket"] in ("POCKET_MEDICINE", "POCKET_BERRIES")
    heals = [st for c, st in STATUS_COLS.items() if r.get(c) == "true" and heal_pocket]
    if r["reviveAll"] == "true":
        return "revive:1"
    if r["revive"] == "true":
        hp = int(r["hpRestored"])
        if hp >= 255: return "revive:1"
        if hp >= 254: return "revive:0.5"
        return warn(f"{name}: revive with hpRestored {hp}") or f"revive:{hp}"
    if r["guardSpec"] == "true":
        return "cureblock"
    stage = None
    for col, stat in [("atkStages", "atk"), ("defStages", "def"), ("spatkStages", "spa"),
                      ("spdefStages", "spd"), ("speedStages", "spe"), ("accStages", "acc"),
                      ("critStages", "crit")]:
        v = int(r[col])
        if v:
            if stage is not None: warn(f"{name}: multiple stage columns set")
            stage = (stat, v)
    hp = int(r["hpRestored"]) if r["hpRestore"] == "true" else 0
    if hp:
        five = len(heals) >= 5
        if hp == 253:  # ROM sentinel: restore 1/8 of max HP (Sitrus-class berries)
            return "heal:eighth"
        if hp >= 255: return "healfullstatus" if five else "healfull"
        if five: warn(f"{name}: partial heal {hp} + full status cure -> heal only")
        return f"heal:{hp}"
    if len(heals) == 1:
        return f"status:{heals[0]}"
    if len(heals) == 5:
        return "cureall"
    if len(heals) not in (0, 6):
        warn(f"{name}: {len(heals)} status-cure flags -> null effect")
    if len(heals) == 6:  # + attract (healAttract): still 'cure all statuses'
        return "cureall"
    if stage:
        return f"boost:{stage[0]}:{stage[1]}"
    return None

# gen3-only battle-use constants absent from the platinum CSV (which uses the
# gen-4 spelling ITEM_X_DEFENSE). Effects verified in pokeemerald
# src/data/pokemon/item_effects.h: gItemEffect_XDefend (def +1),
# gItemEffect_ParalyzeHeal (paralysis only), gItemEffect_EnergyPowder (HP 50).
EMERALD_ONLY_EFFECTS = {"ITEM_X_DEFEND": "boost:def:1",
                        "ITEM_PARALYZE_HEAL": "status:paralysis",
                        "ITEM_ENERGY_POWDER": "heal:50"}

def item_fields(name, games):
    r = by_item.get(name)
    psid = item_norm.get(norm(name[len("ITEM_"):]))
    if r is None:
        fx = EMERALD_ONLY_EFFECTS.get(name)
        return {"name": DEX["items"][psid]["name"] if psid else humanize(name),
                "dexId": psid, "price": 0, "pocket": "battle-items" if fx else "items",
                "battle": fx is not None, "battleEffect": fx, "games": sorted(games)}
    pocket = r["fieldPocket"].replace("POCKET_", "").lower()
    battle = r["battlePocket"] not in ("BATTLE_POCKET_MASK_NONE", "")
    return {"name": DEX["items"][psid]["name"] if psid else humanize(name),
            "dexId": psid, "price": int(r["price"]), "pocket": pocket,
            "battle": battle, "battleEffect": derive_effect(name, r), "games": sorted(games)}

# ---------------------------------------------------------------- balls
ball_src = (ROOT / "pokeplatinum/src/battle/battle_script.c").read_text()
BALL_BASIC = {"ITEM_ULTRA_BALL": 20, "ITEM_GREAT_BALL": 15, "ITEM_POKE_BALL": 10, "ITEM_SAFARI_BALL": 15}
BALL_COND = {  # from BattleScript_CalcCatchShakes switch (mods are x/10)
    "ITEM_NET_BALL": (30, "if target is Water or Bug type"),
    "ITEM_DIVE_BALL": (35, "if battle is on water terrain"),
    "ITEM_NEST_BALL": (None, "40 - target level, min 10, only while target level < 40 (else 10)"),
    "ITEM_REPEAT_BALL": (30, "if species already registered (caught)"),
    "ITEM_TIMER_BALL": (None, "10 + battle turn count, max 40"),
    "ITEM_DUSK_BALL": (35, "dusk/night or inside a cave"),
    "ITEM_QUICK_BALL": (40, "if used on the first turn"),
}
def catch_bonus(name):
    if name in BALL_BASIC: return {"mult": BALL_BASIC[name] / 10.0}
    if name in BALL_COND:
        m, c = BALL_COND[name]
        return {"mult": (m / 10.0) if m else 1.0, "cond": c}
    return {"mult": 1.0}  # Heal/Luxury/Premier/Cherish/Master: default ballMod 10

# sanity: the switch classification must match the pocket list
for r in csv_rows:
    if r["fieldPocket"] == "POCKET_BALLS":
        n = r["item"]
        if f"case {n}:" in ball_src and n not in BALL_BASIC and n not in BALL_COND:
            warn(f"{n}: ROM switch has a special case my table missed")

# ---------------------------------------------------------------- pokemon_data.json
pokemon_out = {}
ref_moves = set()
ref_abilities = set()
evo_item_reverse = {}
evo_ref_moves = []

for k, d in donor.items():
    sid = spec_norm[k]
    dx = DEX["species"][sid]
    num = dx["num"]
    bs_dex = dx["baseStats"]
    bs_rom = {STATK[a]: d["base_stats"][a] for a in STATK}
    if bs_dex != bs_rom:
        warn(f"{sid}: baseStats dex {bs_dex} != ROM {bs_rom}")
    abilities = []
    for a in d["abilities"]:
        if a == "ABILITY_NONE":
            continue
        aid = ab2id(a)
        abilities.append(aid)
        ref_abilities.add(aid)
    if {norm(x) for x in dx["abilities"]} != {norm(a) for a in abilities}:
        warn(f"{sid}: ability slots differ from modern dex: ROM {abilities} vs dex {dx['abilities']}")
    lu = []
    for lvl, mv in d["learnset"]["by_level"]:
        mid = m2id(mv)
        ref_moves.add(mid)
        lu.append({"level": lvl, "move": mid})
    lu.sort(key=lambda x: x["level"])  # stable: keeps ROM order within one level
    by_tm = [("HM" + str(int(x[2:])) if x.startswith("HM") else x)
             for x in (d["learnset"]["by_tm"] or [])]  # donor pads HM01..HM08
    for x in by_tm:
        if x not in PT_TM: die(f"unknown TM/HM ref {x} in {sid}")
    tm = sorted({m2id(PT_TM[x]) for x in by_tm if x.startswith("TM")})
    hm = sorted({m2id(PT_TM[x]) for x in by_tm if x.startswith("HM")})
    ref_moves.update(tm); ref_moves.update(hm)
    evos = [map_evo(e, evo_ref_moves) for e in (d["evolutions"] or [])]
    for ev in evos:
        if "item" in ev:
            evo_item_reverse.setdefault(ev["item"], set()).add(num)
    held = d["held_items"]
    held_items = []
    for slot, rar in (("common", "common"), ("rare", "rare")):
        it = held_pair(held.get(slot))
        if it:
            if not it.startswith("ITEM_"): die(f"{sid}: held item {it!r} not a constant")
            held_items.append({"id": it, "rarity": rar})
    entry = {
        "num": num,
        "name": dx["name"],
        "types": dx["types"],
        "baseStats": bs_dex,
        "abilities": abilities,
        "hiddenAbility": None,  # generation 4 has no hidden abilities
        "genderRatio": GENDER[d["gender_ratio"]],  # fraction female, -1 = genderless
        "catchRate": d["catch_rate"],
        "expYield": d["base_exp_reward"],
        "growthRate": d["exp_rate"].replace("EXP_RATE_", "").replace("_", " ").title(),
        "baseFriendship": d["base_friendship"],
        "hatchCycles": d["hatch_cycles"],
        "evYield": {STATK[a]: d["ev_yields"][a] for a in STATK},
        "heldItems": held_items,
        "levelUpMoves": lu,
        "tmMoves": tm,
        "hmMoves": hm,
        "evolvesInto": evos,
    }
    if str(num) in pokemon_out: die(f"duplicate num {num}")
    pokemon_out[str(num)] = entry

# ---------------------------------------------------------------- TM/HM teaches
GAMES = ["emerald", "platinum", "heartgold"]
teaches = {}
for i in range(1, 93):
    t = {}
    if i <= 50: t["emerald"] = m2id(EM_TM[f"TM{i:02d}"])
    t["platinum"] = m2id(PT_TM[f"TM{i:02d}"])
    t["heartgold"] = m2id(HG_TM[f"TM{i:02d}"])
    teaches[f"ITEM_TM{i:02d}"] = t
for i in range(1, 9):
    t = {}
    t["emerald"] = m2id(EM_TM[f"HM{i}"])
    t["platinum"] = m2id(PT_TM[f"HM{i}"])
    t["heartgold"] = m2id(HG_TM[f"HM{i}"])
    teaches[f"ITEM_HM{i:02d}"] = t
for t in teaches.values():
    ref_moves.update(t.values())

# ---------------------------------------------------------------- trainer fixed moves + item refs
trainer_ref_moves = set()
trainer_ref_items = set()
for f in (ROOT / "pokeplatinum/res/trainers/data").glob("*.json"):
    d = json.loads(f.read_text())
    for p in d.get("party", []):
        for mv in p.get("moves") or []:
            trainer_ref_moves.add(m2id(mv))
        if p.get("item") and p["item"] != "ITEM_NONE":
            trainer_ref_items.add(p["item"])
    for it in d.get("items") or []:  # in-battle AI item usage list
        if it and it != "ITEM_NONE": trainer_ref_items.add(it)

hg_tr = json.loads((ROOT / "pokeheartgold/files/poketool/trainer/trainers.json").read_text())
def walk_hg(o):
    if isinstance(o, dict):
        for mv in o.get("moves") or []:
            if isinstance(mv, str) and mv.startswith("MOVE_"):
                trainer_ref_moves.add(m2id(mv))
        it = o.get("item")
        if isinstance(it, str) and it.startswith("ITEM_") and it != "ITEM_NONE":
            trainer_ref_items.add(it)
        for v in o.values(): walk_hg(v)
    elif isinstance(o, list):
        for v in o: walk_hg(v)
walk_hg(hg_tr)

# terrain rival fixed sets (display names) + map item refs (display names)
terrain_item_refs = set()
for game in GAMES:
    for f in (ROOT / f"terrain_maps_{game}/all").glob("*.json"):
        d = json.loads(f.read_text())
        stack = [d]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                stack.extend(o.values())
                mv = o.get("moves")
                if isinstance(mv, list):
                    for name in mv:
                        if isinstance(name, str):
                            # either pretty display ("Aerial Ace" — ROM-era spellings!)
                            # or bare ROM constant ("SMELLING_SALT"); both need the PS
                            # rename aliases (Faint Attack -> feintattack etc.)
                            if name.isupper() or "_" in name:
                                trainer_ref_moves.add(m2id(name))
                            else:
                                kk = norm(name)
                                kk = kk if kk in move_norm else ROM_MOVE_RENAMES.get(kk, kk)
                                if kk not in move_norm:
                                    die(f"terrain trainer move '{name}' ({game}/{f.name}) not in dex")
                                trainer_ref_moves.add(move_norm[kk])
                it = o.get("gives_item")
                if isinstance(it, str) and it:
                    terrain_item_refs.add((game, it))
                v = o.get("items")
                if isinstance(v, list):
                    for e in v:
                        if isinstance(e, dict) and isinstance(e.get("name"), str):
                            terrain_item_refs.add((game, e["name"]))
            elif isinstance(o, list):
                stack.extend(e for e in o if isinstance(e, (dict, list)))

for m in trainer_ref_moves:
    ref_moves.add(m)
for it in trainer_ref_items:
    if it in pool: pool[it]["games"] |= {"platinum", "heartgold"}
    else:
        pool[it] = {"games": {"platinum", "heartgold"}}
        warn(f"trainer item {it} absent from PT CSV ∪ emerald items.h")

# ---------------------------------------------------------------- moves_ui / abilities_ui
all_gen4 = {mid for mid, m in DEX["moves"].items() if m["gen"] <= 4
            and re.fullmatch(r"hiddenpower[a-z]+", mid) is None}  # HP variants are dex-only
moves_ui_ids = sorted(ref_moves | all_gen4 | {"struggle"})
moves_ui = {}
for mid in moves_ui_ids:
    m = DEX["moves"][mid]
    if not m["text"]: warn(f"move {mid}: empty description text")
    moves_ui[mid] = {kk: m[kk] for kk in ("name", "type", "category", "pp", "num")}
    moves_ui[mid]["text"] = m["text"]

for dx in DEX["species"].values():
    for a in dx["abilities"]:
        if norm(a) not in abil_norm: die(f"dex ability {a} unknown")
        ref_abilities.add(abil_norm[norm(a)])
abilities_ui = {}
for aid in sorted(ref_abilities):
    a = DEX["abilities"][aid]
    if not a["text"]: warn(f"ability {aid}: empty description text")
    abilities_ui[aid] = {"name": a["name"], "text": a["text"]}

# ---------------------------------------------------------------- items.json
FIELD_MOVES = {"strength", "flash", "rocksmash", "cut", "surf", "fly", "waterfall",
               "defog", "rockclimb", "teleport", "whirlpool", "dive"}
items_out = {}
for name, info in pool.items():
    f = item_fields(name, info["games"])
    if name in teaches:
        t = teaches[name]
        f["teaches"] = t
        f["available"] = sorted(t)
        fm = sorted(set(t.values()) & FIELD_MOVES)
        if fm: f["fieldMove"] = fm
        if t.get("emerald") == "dive":
            f["fieldOnly"] = {"emerald": "gen-3 HM08 Dive is field-only; the battle move Dive is gen-5"}
            f["fieldMove"] = sorted(set(f.get("fieldMove", [])) | {"dive"})
    elif by_item.get(name, {}).get("fieldPocket") == "POCKET_BALLS":
        f["catchBonus"] = catch_bonus(name)
    if name in evo_item_reverse:
        f["evolvesSpecies"] = sorted(evo_item_reverse[name])
    items_out[name] = f
# v1 synthetic item (2026-09-18 decision): Feebas→Milotic by use-item — gen-5
# mechanic; absent from all three ROM pools. Hand-authored entry, marked synthetic.
items_out["ITEM_PRISM_SCALE"] = {
    "name": "Prism Scale", "dexId": None, "price": 0, "pocket": "evolution",
    "battle": False, "battleEffect": None, "games": [],
    "synthetic": "gen-5 item; added for the v1 Feebas→Milotic use-item evolution"}
if "ITEM_PRISM_SCALE" in evo_item_reverse:
    items_out["ITEM_PRISM_SCALE"]["evolvesSpecies"] = sorted(evo_item_reverse["ITEM_PRISM_SCALE"])

# map item display names must resolve (CI for the client inventory UI)
rom_by_norm = {norm(n[len("ITEM_"):]): n for n in pool}
rom_by_norm.update({norm(n): n for n in pool})
def num_map(header, base):
    d = {}
    for m in re.finditer(r"#define\s+(ITEM_[A-Z0-9_]+)\s+(\d+)",
                         (ROOT / header).read_text()):
        d.setdefault(int(m.group(2)), m.group(1))  # first define wins for aliases
    return d
em_by_num = num_map("pokeemerald/include/constants/items.h", None)
hg_by_num = num_map("pokeheartgold/include/constants/items.h", None)
# platinum has no plain #defines; pl_item_data.csv row order IS the gen-4 id
# (spot-checked: rows 72/82 == heartgold items.h RED_SHARD/FIRE_STONE)
pt_by_num = {i: r["item"] for i, r in enumerate(csv_rows)}
NUM_BY_GAME = {"emerald": em_by_num, "platinum": pt_by_num, "heartgold": hg_by_num}
GAME_TBL = {"emerald": EM_TM, "platinum": PT_TM, "heartgold": HG_TM}
def tm_ref_key(kind, num):  # display number -> (pool key, ROM-table key)
    return (f"ITEM_{kind}{int(num):02d}",
            f"TM{int(num):02d}" if kind == "TM" else f"HM{int(num)}")
move_to_tm = {}
for g, tbl in GAME_TBL.items():
    mv2 = {}
    for tk, mv in tbl.items():
        key = ("ITEM_TM" if tk.startswith("TM") else "ITEM_HM") + \
              (tk[2:] if tk.startswith("TM") else f"{int(tk[2:]):02d}")
        mv2.setdefault(m2id(mv), key)
    move_to_tm[g] = mv2
TM_PAREN = re.compile(r"^(TM|HM)\d{1,2}\s*\((.+)\)$")
TM_BARE = re.compile(r"^(?:T\s?M|Tm|H\s?M|Hm)\s+([A-Za-z][A-Za-z ]*)$")
for game, disp in sorted(terrain_item_refs):
    if "EventScript" in disp or disp == "0x0":
        continue  # static-encounter labels / empty slot, not items
    m = TM_PAREN.match(disp)
    if m:  # "TM01 (Focus Punch)" — number AND taught move given: cross-check both
        kind, mvname = m.group(1), m.group(2)
        key, tabkey = tm_ref_key(kind, re.match(r"\d+", disp[len(kind):]).group(0))
        if key not in items_out:
            warn(f"terrain '{disp}' ({game}): {key} not in pool")
            continue
        if tabkey not in GAME_TBL[game]:
            warn(f"terrain '{disp}' ({game}): not taught in this game")
            continue
        rom = m2id(GAME_TBL[game][tabkey])
        kk = norm(mvname)
        kk = kk if kk in move_norm else ROM_MOVE_RENAMES.get(kk, kk)
        want = move_norm.get(kk)
        if want is None:
            warn(f"terrain '{disp}' ({game}): parenthetical move not in dex")
        elif want != rom:
            warn(f"terrain '{disp}' ({game}) DISAGREES with ROM teach ({rom})")
        continue
    m = TM_BARE.match(disp)
    if m:  # "TM Double Team" style: taught move determines the item, per game
        want = move_norm.get(norm(m.group(1)))
        if want and want in move_to_tm[game]:
            if move_to_tm[game][want] in items_out:
                continue
        else:
            warn(f"terrain '{disp}' ({game}): TM by move unresolved")
            continue
    parts = [x.strip() for x in disp.split("/")] if " / " in disp else [disp]
    unresolved = [p2 for p2 in parts
                  if norm(p2.removeprefix("Tm ")) not in rom_by_norm
                  and norm(p2) not in item_norm
                  and norm(p2.removeprefix("Tm ")) not in move_to_tm[game]]
    if not unresolved:
        continue  # resolves (item number directly, by taught move, or as a choice list)
    mnum = re.fullmatch(r"Item#(\d+)", disp)
    if mnum:
        n = int(mnum.group(1)) & ~0x2000  # items carry their pick-up flag in bit 13
        nm = NUM_BY_GAME[game].get(n)
        if nm in pool or norm((nm or "").removeprefix("ITEM_")) in item_norm:
            continue  # resolved through the game's own item numbering
        warn(f"terrain numeric item id {disp} ({game}) -> {nm} unresolved")
        continue
    kk = norm(disp)
    if kk not in rom_by_norm and kk not in item_norm:
        warn(f"terrain item '{disp}' ({game}) does not resolve to any pooled item")

# ---------------------------------------------------------------- exp_thresholds.json
# ROM ground truth: pokeemerald src/data/pokemon/experience_tables.h formulas
# (identical in all gen3/4 games; C integer floor division, operands positive).
# Table stores literals at levels 0/1; formulas apply from 2 up.
def _cube(n): return n * n * n
def _exp_slow(n): return 5 * _cube(n) // 4
def _exp_fast(n): return 4 * _cube(n) // 5
def _exp_mf(n): return _cube(n)
def _exp_ms(n): return 6 * _cube(n) // 5 - 15 * n * n + 100 * n - 140
def _exp_erratic(n):
    if n <= 50: return (100 - n) * _cube(n) // 50
    if n <= 68: return (150 - n) * _cube(n) // 100
    if n <= 98: return ((1911 - 10 * n) // 3) * _cube(n) // 500
    return (160 - n) * _cube(n) // 100
def _exp_fluctuating(n):
    if n <= 15: return ((n + 1) // 3 + 24) * _cube(n) // 50
    if n <= 36: return (n + 14) * _cube(n) // 50
    return (n // 2 + 32) * _cube(n) // 50
def _curve(fn):
    return [0, 1] + [max(0, fn(n)) for n in range(2, 101)]
EXP_CURVES = {"Medium Fast": _exp_mf, "Fast": _exp_fast, "Slow": _exp_slow,
              "Medium Slow": _exp_ms, "Erratic": _exp_erratic, "Fluctuating": _exp_fluctuating}
exp_thresholds = {name: _curve(fn) for name, fn in EXP_CURVES.items()}
ANCHORS = {"Medium Fast": 1000000, "Fast": 800000, "Slow": 1250000,
           "Medium Slow": 1059860, "Erratic": 600000, "Fluctuating": 1640000}
for name, anchor in ANCHORS.items():
    got = exp_thresholds[name][100]
    if got != anchor: die(f"exp curve {name}: level-100 {got} != {anchor}")
# every growthRate used by any species must be a known curve
used = {e["growthRate"] for e in pokemon_out.values()}
if not used <= set(EXP_CURVES): die(f"unknown growth rates: {used - set(EXP_CURVES)}")
(SIMD / "exp_thresholds.json").write_text(json.dumps({"_meta": {
    "source": "pokeemerald src/data/pokemon/experience_tables.h (ROM formulas; gen3=gen4)",
    "semantics": "cumulative total EXP to BE AT index-level; xp to next = t[lvl+1]-t[lvl]"},
    **exp_thresholds}))

# ---------------------------------------------------------------- validation
for num_s, e in pokemon_out.items():
    if len(e["levelUpMoves"]) == 0: die(f"{e['name']}: no level-up moves")
    for mv in e["levelUpMoves"]:
        if mv["move"] not in moves_ui: die(f"{e['name']}: learn move {mv['move']} missing from moves_ui")
    for mv in e["tmMoves"] + e["hmMoves"]:
        if mv not in moves_ui: die(f"{e['name']}: tm/hm move {mv} missing from moves_ui")
    if not 1 <= len(e["abilities"]) <= 2: die(f"{e['name']}: {len(e['abilities'])} abilities")
    for a in e["abilities"]:
        if a not in abilities_ui: die(f"ability {a} not in abilities_ui")
    for ev in e["evolvesInto"]:
        if str(ev["to"]) not in pokemon_out: die(f"evolution target {ev['to']} not in data")
        if ev.get("item") and ev["item"] not in items_out: die(f"evo item {ev['item']} not in items")
    for h in e["heldItems"]:
        if h["id"] not in items_out: die(f"{e['name']}: held item {h['id']} not in items")

# evolution graph acyclic
adj = {int(n): [ev["to"] for ev in e["evolvesInto"]] for n, e in pokemon_out.items()}
state = {}
def dfs(n):
    if state.get(n) == 1: die(f"evolution cycle involving {n}")
    if state.get(n) == 2: return
    state[n] = 1
    for m in adj.get(n, []): dfs(m)
    state[n] = 2
for n in adj: dfs(n)

# trainer item refs resolve
for it in trainer_ref_items:
    if it not in items_out: die(f"trainer item {it} missing from items.json")

# ---------------------------------------------------------------- HG cross-check
hg = json.loads((ROOT / "pokeheartgold/files/poketool/personal/personal.json").read_text())
recs = hg["baseStats"] if isinstance(hg, dict) and "baseStats" in hg else hg
hg_base = {norm(r["species"]): r for r in recs}
KNOWN_ITEM_DIFFS = {"electabuzz", "elekid", "magby", "magmar", "shuckle"}
hg_diffs = []
for k, d in donor.items():
    rec = hg_base.get(k)
    if rec is None: continue
    dx = DEX["species"][spec_norm[k]]
    for what, hv, pv in [
        ("hp", rec["hp"], dx["baseStats"]["hp"]), ("atk", rec["atk"], dx["baseStats"]["atk"]),
        ("def", rec["def"], dx["baseStats"]["def"]), ("spe", rec["speed"], dx["baseStats"]["spe"]),
        ("spa", rec["spatk"], dx["baseStats"]["spa"]), ("spd", rec["spdef"], dx["baseStats"]["spd"]),
        ("catch", rec["catchRate"], d["catch_rate"]), ("exp", rec["expYield"], d["base_exp_reward"]),
        ("friend", rec["friendship"], d["base_friendship"]), ("hatch", rec["eggCycles"], d["hatch_cycles"]),
    ]:
        if hv != pv: hg_diffs.append(f"{k}.{what}: HG {hv} vs PT {pv}")
    gr = -1.0 if float(rec["genderRatio"]) == 2.0 else float(rec["genderRatio"])
    if abs(gr - GENDER[d["gender_ratio"]]) > 1e-9:
        hg_diffs.append(f"{k}.gender: HG {rec['genderRatio']} vs PT {d['gender_ratio']}")
    hab = {ab2id(a) for a in rec["abilities"] if a and norm(a) not in ("none", "abilitynone")}
    pab = {ab2id(a) for a in d["abilities"] if a and a != "ABILITY_NONE"}
    if {norm(x) for x in hab} != {norm(x) for x in pab}:
        hg_diffs.append(f"{k}.abilities: HG {rec['abilities']} vs PT {d['abilities']}")
    hitems = [str(x) for x in rec.get("items", []) if x and str(x) not in ("0", "NONE", "ITEM_NONE")]
    hitems = [x if x.startswith("ITEM_") else "ITEM_" + x for x in hitems]
    pitems = [x for x in (held_pair(d["held_items"].get("common")),
                          held_pair(d["held_items"].get("rare"))) if x]
    if norm(",".join(hitems)) != norm(",".join(pitems)):
        tag = "known PT-vs-HG held-item difference" if k in KNOWN_ITEM_DIFFS else "UNEXPECTED"
        hg_diffs.append(f"{k}.items {tag}: HG {hitems} vs PT {pitems}")

# ---------------------------------------------------------------- write
def dump(fn, obj):
    (SIMD / fn).write_text(json.dumps(obj, indent=1, ensure_ascii=False))

dump("pokemon_data.json", {"_meta": {
    "source": "pokeplatinum donor + fork gen4 dex (dex_dump.json); audit §6.2",
    "species": len(pokemon_out),
    "hiddenAbility": "always null: gen 4 has no hidden abilities",
    "genderRatio": "fraction female; -1 = genderless",
    "evolvesInto": "list; condition=special entries are unreachable in v1 (see note)"},
    **{k: pokemon_out[k] for k in sorted(pokemon_out, key=int)}})
dump("moves_ui.json", {"_meta": {
    "note": "every gen<=4 dex move ∪ every move referenced by ROM learnsets, TM/HM teaches, "
            "trainer fixed sets, evolution-know-move conditions"},
    **moves_ui})
dump("abilities_ui.json", abilities_ui)
dump("items.json", {"_meta": {
    "pool": "platinum pl_item_data.csv ∪ pokeemerald items.h (gen3 ∪ gen4 universe)",
    "battleEffect": "closed enum for daemon op:item; null = no battle use",
    "games": "which games share this id; TM51-92: platinum+heartgold only"},
    **items_out})

print(f"pokemon_data: {len(pokemon_out)} species | moves_ui: {len(moves_ui)} | "
      f"abilities_ui: {len(abilities_ui)} | items: {len(items_out)} | "
      f"exp_thresholds: {len(EXP_CURVES)} curves ({len(used)} used)")
print(f"HG cross-check diffs: {len(hg_diffs)}")
for d in hg_diffs[:40]: print("  HG:", d)
print(f"warnings: {len(WARNS)}")
for w in WARNS[:60]: print("  W:", w)
if any("UNEXPECTED" in d for d in hg_diffs):
    die("unexpected HG-vs-Platinum differences — investigate before trusting donor")
