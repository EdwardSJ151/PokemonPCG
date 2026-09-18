# Extraction Audit Sweeps — 2026-09-18 (read-only; no code changed)

Baseline re-verified before and during: `dex_dump.js` → 493 species / 954 moves / 320 abilities / 582 items;
`extract_sim_data.py` → 493 | 470 | 123 | 523 | exp:6 curves, HG diffs = 5 known rows, **0 warnings**.
Terrain layouts moved to repo root `terrain_maps_{emerald,heartgold,platinum}/` (content in `all/` per game;
platinum caves/routes/towns dirs are empty). Extractor reads `ROOT/terrain_maps_{game}/all` — green under this layout.

## CONFIRMED BUGS (decomp-provable)

### B1 — Platinum terrain: water/fishing encounter levels destroyed (min=max=1)
> **FIXED 2026-09-18** (terrain regen 20:10). Recheck: 154/154 maps exact across land + surf + all three rod buckets.
All `surf`, `old_rod`, `good_rod`, `super_rod` slots in `terrain_maps_platinum/all/*` report `min_level:1, max_level:1`.
Decomp truth (`pokeplatinum/res/field/encounters/*`): 216 broken bucket-rows across the files, e.g.
- route_224 surf: ROM Pelipper/Tentacruel/Gastrodon **L35–55** → terrain **L1–1**
- route_228 super_rod: ROM Gyarados **40–55** → terrain **1–1**; old_rod Magikarp **4–6** → 1–1
Species and slot rates are correct; **land tables are exact 154/154**. HG (14 854 slots) and emerald water levels are sane — PT-only defect.
Simulator impact: wrong wild water encounters if terrain is the loader. Fix belongs in the terrain extractor's water-table parse (it emits slot rates fine but reads levels as constant 1).

### B2 — Emerald terrain trainer `ivs` = floor(ROM_scale / 3) — wrong unit, inconsistent across blocks
> **FIXED 2026-09-18**: terrain `ivs` now stores final IVs `scale*31//255` in all three games. Recheck: 987 emerald trainers joined to `trainer_parties.h`, 1854/1854 rows exact. Note: `ivs` is IV-units now, NOT raw scale (audit §6.2(d)/L346/L455 text still stale); HG/PT nature seeds must still read raw (HG `difficulty`/PT `iv_scale` in raw trainer files).
ROM raw (`pokeemerald/src/data/trainer_parties.h`, `.iv` byte) is a **0–255 scale**; ROM computes
`fixedIV = iv * MAX_PER_STAT_IVS / 255` (`battle_main.c:2013`). Terrain:
- Victory Road B2F DODRIO: raw `.iv=100` (true IV 12) → terrain `ivs: 33`
- set correlation confirms: terrain values = raw/3 (10→3, 20→6, 50→16, 75→25, 100→33, 200→66, 255→85)
- `rivals[]` blocks keep **raw scale** (100/150/200) — two different units under one field name in one schema.
Simulator: ignore terrain `ivs` for emerald trainers; use raw scale ×31/255. (Terrain copies have no raw scale to recover — use `trainer_parties.h` or re-extract.)

### B3 — HeartGold terrain trainer copies drop IV scale entirely
> **FIXED 2026-09-18**: HG terrain now emits `floor(difficulty*31/255)`. Recheck: 806/806 joined rows exact; max IV 12 correct — difficulty>100 rows are E4/Red rematches, absent from HG terrain by coverage.
`terrain_maps_heartgold/all/*` party `ivs` is **0 for all 847 mons** while raw `trainers.json` carries `difficulty` 0–250
(30/80/150/250 common). Everything else (species, level, fixed moves, held item where present, ability/gender override) is carried.
HG load path must read raw `pokeheartgold/files/poketool/trainer/trainers.json` for ivScale; terrain serves placement + decoded names/AI only.

### B4 — `pokemon_data.json` evolvesInto: held-item level-evolutions mislabeled `condition:"item"`
> **FIXED 2026-09-18**: extractor now emits `condition:"levelWithHeldItem"` (item + timeOfDay, no `levelTriggered`); `condition:"item"` is exclusively use-item (stones). 3 rows changed (Sneasel, Gligar, Happiny); pipeline green; audit contract updated. Follow-up same day: Feebas→Milotic re-emitted as use-item `ITEM_PRISM_SCALE` (synthetic gen-5 item, +1 items.json entry; the 170 is beauty not level, provenance in `extra`), so the beauty-170-in-`level` overload is gone; remaining `special` rows carry GENUINE ROM level params (7/20) that MUST be read — v1 semantics (map gates kept, MAGNETIC→level-up, PID byte roll) normative in SIMULATOR_AUDIT.md §6.2.
Raw `EVO_LEVEL_WITH_HELD_ITEM_NIGHT` (Sneasel→Weavile, Gligar→Gliscor) emits
`{condition:"item", item:ITEM_RAZOR_CLAW/FANG, timeOfDay:"night", levelTriggered:true}` —
contract says "use item on it" but ROM requires **hold item + level up at night**. A simulator honoring `condition:"item"`
instant-evolves a level-45 Sneasel by feeding it a Razor Claw Claw. (Hints exist but the discriminator field lies; also
Feebas→Milotic puts beauty-threshold **170** into `level` — cosmetic field-name overload; raw kept in `extra`.)
Correct fix: new condition (e.g. `levelWithHeldItem`) for these two rows.

### B5 — Simulator IV rule must mask, not clamp (Volkner Electivire)
> **FIXED 2026-09-18**: `terrain_to_ascii.py:909` helper applies `raw*31//255 & 0x1F` (mask, not clamp) to every terrain IV — the Electivire semantics hold wherever the row appears.
`leader_volkner.json` Electivire `iv_scale: 2500` is **genuine ROM data**: PT party words are all `u16`
(`struct_defs/trainer_data.h`), `MAX_IV_SCALE 255` but `ivs = ivScale*31/255` is **unclamped** (`trainer_data.c:214`) and written
through 5-bit box fields → ROM IVs = `304 & 31` = **16**, and the PID/LCRNG seed uses **raw 2500** (`rnd = ivScale + level + species + trainerID`).
Load rule `min(31, scale*31/255)` would give 31. Correct rule: `iv = (scale*31/255) & 31` (no other row in 928 files exceeds 255).
Party itself verified correct: Platinum Volkner first battle legitimately 4 mons — Jolteon 46 / Raichu 46 / Luxray 48 / Electivire 50 (pokemondb); Candice legitimately 4.
Extraction field `ball_seal` is the ROM's **cbSeal** (combat/partner seal, cosmetic) — rename recommended, values fine (14 on that Electivire).

## STRUCTURAL / DATA-GAP FINDINGS

### S1 — Emerald terrain id-domain mismatch: `file.constant` = `LAYOUT_*` vs warps/connections dests = `MAP_*`
All 135 emerald connections and every warp dest use ROM *map-header* constants (`MAP_ROUTE121`), while layout files carry
`constant: LAYOUT_ROUTE121`. They join only via `map_graph.json` `nodes[].name` (verified: 0 unresolved warps/conns against graph node ids).
HG/PT consistent. Consumers keyed by file `constant` silently lose all emerald map links.

### S2 — Vending machines: positions carried, product lists empty
Only 3 populated vending groups exist game-wide (2 emerald Lilycove rooftop machines, 2 PT Veilstone Store 5F machines), all with `items: []`.
Coverage itself is faithful — HGSS decomp has **no** vending content at all (verified: zero hits in `pokeheartgold/files`). Cosmetic data gap unless K1 shops use them.

### S3 — HG National Park: 8 warps → `MAP_NATIONAL_PARK_UNUSED_GATEHOUSE` (map absent from repo)
Genuine HGSS leftover dead warp, not an extraction bug — but the simulator must treat unresolvable warp dests as blocked (it's the only such case game-wide).

### S4 — v1 scope blockers in faithful data (decision needed, not bugs)
- PT trainers with `form != 0` (10 mons): tuber_jared Shellos(form 1), beauty_devon Wormadam(1,2), jared rematch — base-forme-only scope cannot represent them.
- `pokeplatinum/res/trainers/data/none.json`: dummy (empty party, `TRAINER_CLASS_PLAYER_MALE`, name " -") — skip on load.
- HG `capsule` (ball-capsule deco): exactly 1 nonzero (Charlotte's Bellossom = 1) — cosmetic, ignore.
- PT terrain decodes 464 trainers; rematch/fight-area variants exist only in raw `res/trainers/data` (928 = matches `generated/trainers.txt` 928 exactly).
- HG raw names are `{TRNAME}`-templated (`{TRNAME}Silver`); pretty names exist only in terrain-decoded copies (89 files) — HG load must merge raw+terrain or template-decode.
- HG raw `ai_flags` are numeric masks (14 distinct, ≤519); decoded name lists only in terrain copies.

## VERIFIED CLEAN (numbers)
- **PT trainers** (928 files, = trainer-id table 928/928): every species/move/item constant resolves vs dex+alias+pool; party ≤6; levels ≤100; moves ≤4; `iv_scale ≤255` except B5 row. `ivScale` semantics confirmed `×31/255`, `MAX_IV_SCALE 255`.
- **HG trainers** (738): levels 2–88, party ≤6, `difficulty` 0–250, genderOverride {OFF×1774, FEMALE×2}, abilityOverride {OFF×1408, SECOND×368}, all names resolve.
- **Emerald terrain trainers**: 915 trainer + 72 rival blocks; all named `TRAINER_*` ids; every fixed/rematch move name is a valid **emerald** move constant (0/1365 violations).
- **Fixed-move learnability retcon**: vanilla fixed sets are hardcoded and *need not be learnable* — e.g. Cyrus's Honchkrow carries Drill Peck + Faint Attack, absent from every Honchkrow learn/tutor/TM table (donor AND PS). PS learnsets are additionally **clobbered** for gen≤4 (drillpeck removed from honchkrow entirely) → PS is invalid ground truth for gen-4 legality. Ground truth = per-game ROM tables only.
- **Emerald encounters vs ROM** (`wild_encounters.json`, graph-joined): **105/105 maps exact** (bucket-merged species+level multisets, slot counts, encounter rates).
- **PT encounters**: land tables exact **154/154**; water broken (B1). Extra terrain info (swarms, radar, gba_slots, honey_trees) matches src shape.
- **HG encounters**: 14 854 slots, all species resolve, sane level ranges, zero (1,1) rows.
- **Terrain structure**: warps 0 unresolved (emerald 428 / PT 536 nodes; HG 8 gatehouse = S3); connections HG 158/158 (S1 caveat); `gym_badge` ⊂ enum (8 emerald flags, 7+PT ids); vending/`gives_item`/map-item names resolve.
- **Donor (PT res)**: 6 600 by_level entries, max level 100, **no level-0**; 496 folders = 493 + none/egg/bad_egg; evo levels ≤100 except beauty-170 (B4 note).
- **Items**: 523 output items include every trainer-held/bag constant (extractor merges `trainer_ref_items`, 0 warnings = full coverage); stones' `evolvesSpecies` ↔ CSV evolve column consistent (10 stones); TM/HM tables asserted per run; balls' `catchBonus` from ROM switch.
- **Held-item chance semantics** (sweep 6): ROM uses global constants — emerald roll `rnd%100`: 45 no-item / ≤95 common / else rare (≈50%/5%; Compoundeyes 20/80→60/20); PT commented table 45/50/5, with Compoundeyes 20/60/20 (`pokemon.c:4688`). No per-species chances → `{id,rarity}` model is faithful; simulator roll rule is fully specified.
- **exp_thresholds**: anchors 1000000/800000/1250000/1059860/600000/1640000 ✓, all 6 curves used, L5/L16 spot values match canonical tables.

## RETRACTED probe artifacts (do not report as data bugs)
- "items.json missing 30 trainer items / sitrus" — my probe compared stripped-vs-unstripped key namespaces; items.json keys are ROM constants (`ITEM_SITRUS_BERRY` etc. present ✓).
- HG bag/held "bad items" and PT species/move "1878/2609 bad" — same probe namespace error / wrong ground-truth headers (PT has no SPECIES_/MOVE_ constant headers).
- Emerald encounter "26 mismatches" — `SPECIES_` prefix asymmetry in probe; data identical.
- PT "terrain files 0 with encounters" — platinum layouts live only in `all/`; earlier probes' `/all/` exclusion hid them.

## Action list (for the later fix pass — none applied now)
1. Terrain extractor: fix PT water/fishing level read (B1); carry HG `difficulty`→ivScale (B3); emerald trainer `ivs` → emit raw scale (B2); unify emerald constant domain or always key terrain files by `map_graph` node id (S1); optionally fill vending inventories (S2).
2. extract_sim_data.py: new evolve condition for held-item level evolutions (B4); document beauty-170 field.
3. Simulator contract: IV rule `iv=(scale*31/255)&31`, raw scale in PID seed (B5); HG raw+terrain merge for names/AI/iv; skip `none.json`; decide form≠0 handling (S4).


### B6 (NEW 2026-09-18, corrected same-day) — terrain misses ONLY the day/night encounter variants (+swarms)
Terrain encounter buckets are RICHER than first probed: `{grass,surf,fishing,_radar,gba_slots,honey_trees}` (PT) and `{grass,headbutt,hoenn_sound,sinnoh_sound,...}` (HG) — radar/gba-slots/honey/headbutt/sound-tables ALL extracted and ROM-exact (r229 `_radar` = venomoth×2+venonat×2 ✓). The real gap: PT `day`/`night` variant lists (183/185 maps carry them, **115 maps genuinely differ** — r229 Pidgey day → ARIADOS night; vb1f Graveler → GOLBAT; MC room3 → Golbat/Clefairy) and HG per-slot `{morn,day,nite}` triples (**401 night≠day rows, 67 maps**, R29 Hoothoot) collapse to the day slice; `grass` stores day species only, night-only species absent. `swarms` (139 maps) unextracted = weekly-feature gated. Fix needs variant-slot index semantics (list position → ROM slot). The 4 Turnback rooms (entrance/giratina/pillar_room/p3r6) have terrain maps+warps but no encounter tables despite ROM ones.
## Fix status (2026-09-18)
B1/B2/B3/B5 fixed in the terrain generator (`terrain_to_ascii.py` + game modules, regen 20:10), verified above with full-population joins.
B4 fixed in `qwenwork/extract_sim_data.py` (new `levelWithHeldItem` condition) + audit contract; regenerated `pokemon_data.json` — only the 3 affected species changed.
Remaining open: S1 (emerald domain join — document/consume via map_graph), S2 (vending inventories), S4 decisions (form≠0 mons, `none.json` skip, HG `{TRNAME}` decode, HG raw+terrain merge), audit IV-unit text drift (§6.2(d)).
