# simulator/ Design-Doc Audit — Battle-Logic Gap Report

Date: 2026-09-18 (rev 2). Method: full read of all 13 `simulator/*.md` docs, cross-checked
against the authoritative battle sources in this repo (`pokemon-showdown/sim` +
`data/mods/gen4`, `damage-calc`, the daemon `qwenwork/task1/showdown_daemon.js`, and the
ported AI `qwenwork/task3/ai/` — green 15/15), plus live battle logs from human play.

## Architecture premise (decided — reshapes the whole audit)

1. **Battles run through the daemon.** `showdown_daemon.js` (PS fork, `gen4customgame`) is
   the battle engine; the simulator is a *client*: build teams → `op:start` → read protocol
   lines → render + send choices. There is **no Java BattleEngine**.
2. **Trainer AI = the ported engine** (`task3/ai/`) running inside the daemon, driven by
   `ai: [flags]` + `aiItems: [...]` at `op:start`.
3. **Item pool is Gen 3 + Gen 4 only** (the extraction pipeline ships those maps). Nothing
   Gen 5+ is reachable; its rows are dead text.
4. Reference client already exists: `qwenwork/task3/play.js` (request→menu mapping, HUD
   transcript mirror, session recorder). Every gap below marked **[client]** is a job the
   simulator docs still own; **[daemon]** = owned by the fork/daemon, spec belongs in
   `task1/INTEGRATION.md`, not in the simulator docs.

Legend: **[R]** redundant → delete or replace with pointer · **[K]** still simulator's job,
currently missing/wrong · **[E]** factual error (in surviving scope) · **[X]** contradiction ·
**[D]** dangling reference.

---

## 1. REDUNDANT once the daemon owns the battle — delete, don't reimplement

The simulator docs currently spec a complete battle engine. That engine exists (the fork).
Duplicating it in Java would be re-implementing — and re-introducing — exactly the bugs the
fork already solved (see §3). These sections should shrink to one pointer sentence:
*"battle mechanics are computed by the daemon; see `task1/INTEGRATION.md` protocol and
`play.js` for the reference client."*

| Doc section | Why redundant |
|---|---|
| **[R]** 07 §Damage formula 257-313 (all 4 steps, all multipliers) | implemented in `sim/battle-damage` + `data/mods/gen4`; the doc's own E1 error proves the duplication risk (crit×screens is wrong *in the doc* and right *in the fork*) |
| **[R]** 07 §Critical hits 345-367, §Priority/turn order 370-390, §Move hit check 441-448, §Status conditions 422-438, §Turn structure 485-503 | fork-owned mechanics; the client only *mirrors* events (`|-crit|`, `|cant|`, `|-status|`…) |
| **[R]** 07 §Type chart 451-465 + the `types_data.json` requirement (also kills dangling ref D1: file nothing produces) | fork loads its own gen4 chart; the client never computes effectiveness — it renders `|-supereffective| / |-resisted| / |-immune|` |
| **[R]** 07 §Abilities 393-419 + §Event-driven ability system 204-231 + `abilities_data.json` export (hooks list) | the fork implements every ability; the client renders `|-ability|` / `|start|` lines. Export not needed for battle; name/description export may stay for UI/Pokédex |
| **[R]** 07 §Battle engine architecture 141-202 (`BattleEngine`/`TurnExecutor`/`ActionQueue`/`MoveExecutor`/`DamageCalculator`/`AbilityRegistry`/`StatusManager`) | replace with: `DaemonClient` (spawn/stdio), `BattleMirror` (protocol→UI state reducer), `ChoiceEncoder` (menu→`move N`/`switch N`/`team N`). play.js = existing prototype of all three |
| **[R]** 07 §AI 506-517 entirely (greedy median-roll + leader heuristic) | superseded by the ported decomp AI: `AI_FLAG_*` mask → modules (TRAINER_AI.md §3-6), `shouldSwitch`/`ShouldPostKOSwitch`/`shouldUseItem` (§4-5), private 16-bit LCG. Docs must *replace* this section, not extend it — see §2 A1 |
| **[R]** 07 §Stat calculation 234-254 → **re-scope, don't delete**: battle stats are the fork's; the simulator still needs the formulas for overworld party display, level-up growth, and box screens — but declare the *battle* source of truth the daemon (HP comes from the last request's `condition` field, play.js pattern) | |
| **[R]** 12 §11 held-item effect table | the fork implements hold effects (`sim` + `data_items.js`: Leftovers, berries incl. pinch/stat/cure, Choice lock, Life Orb, Light Clay, Iron Ball, Orbs *that exist in gen4*…). Simulator stores the item id and puts it in the team set; renders `|-item|`. Keep the table only as a UI/description catalog |
| **[R]** 12 §11 Gen-5+ rows — Flame Orb, Toxic Orb (E5 rev 1) | dead text under the gen3+4 pool rule — delete; and add a **pool rule sentence**: bag/held data is restricted to items defined in emerald ∪ HG/PT `include/constants/items.h` |
| **[R]** 07:81-95 + audit rev-1 items C1/C2 (moves_data `boosts`/`secondary`/dynamic-move representation; Natural Gift/Judgment/Weather Ball/Hidden Power data-model edges) | the fork's dex resolves all effect + dynamic-type data at battle time. The simulator's `moves_data.json` shrinks to **display/teaching metadata**: name, type (icon/color), category (UI color only), base PP, learnset/TM mapping. Effect semantics are NOT simulator data anymore. (The decomp MoveType edge cases stay documented in TRAINER_AI/NOTES as *AI-side* knowledge — the engine reads fork data, not these exports.) |
| **[R]** rev-1 §2 gaps G2-G7, G9-G24 except G1 (Sucker Punch, Protect cascade, multi-turn/trapping, OHKO, U-turn/Knock Off, Substitute, flinch, hazards, weather internals, Mold Breaker, grounded, screens…) | every one is implemented in the fork — the simulator never needs the rule, only the *event vocabulary* to render it. What remains as simulator work is §2 K3 (render map) + K4 (menu mapping) |
| **[R]** rev-1 G15 partial, G18-G21 | ditto — fork state is observed, not modeled |
| **[R]** faint-chain end trigger (rev-1 F1) | daemon emits `{"type":"win"}`; client renders defeat/victory |
| **[R]** determinism/RNG spec (rev-1 G-AI-6) | daemon-owned: `op:start` carries the seed (play.js uses `[7,3,11,5]`); simulator just stores/passes it. One sentence in the new protocol section |
| **[R]** 07:594 `usable_in_battle` gate on 07's items table + much of 07 §Items in battle 577-596 | splits: **player field items** stay simulator spec; **battle-time item use** moves to §2 K1 — it is not in the protocol today |

Net: roughly the damage/stat/AI/ability/turn half of `07_battle_system.md` collapses from
~450 lines of spec into a protocol-contract pointer. What survives is genuinely client work.

---

## 2. The real client-side job — what the docs must NEWLY specify

### K1. Out-of-protocol actions — DECIDED
- **Player bag items in battle → DAEMON ADDITION.** The protocol has no item command
  (`side.choose` covers move/switch/team/pass/default only). CPU-side items work because
  the engine applies effects internally ("The fork has no bag action — the effect is
  applied directly", engine.js:509-511, 1080-1085). Mirror that trust domain for p1:
  a daemon op (e.g. `{"op":"item","item":"potion"}`) that applies the gen4 effect to the
  live battle and consumes p1's turn. ⚠ asymmetry verified in the port: an **AI** item is
  folded into the decomp command phase and its mon STILL attacks (engine.js:511 →
  move eval follows) — that is cartridge-faithful for trainers; a **player** item consumes
  the mon's action. The docs must state both rules; they look contradictory otherwise.
- **Run → CLIENT-SIDE ONLY.** Roll the escape check in the simulator (07:571's formula is
  fine as the client rule), then destroy the battle (`op:cancel`). The daemon never learns;
  no daemon change wanted or needed. 07 §Conclusion survives as pure client spec.
- **Catching → CLIENT-SIDE, with turn coupling.** Not in the protocol at all
  (`pokeball` is a cosmetic team field). Flow, at the p1-request quiescence point:
  1. **Trainer battle** → message "You can't catch a Trainer's Pokémon!", no state change,
     turn not spent (07:538 already states this — pure client gate).
  2. **Success** (formula below) → battle ends immediately: `op:cancel`; capture the
     opponent's mon using its mirrored HP/status; party/box flow per C6. Opponent gets no
     last attack — matches the cartridge.
  3. **Failure** → the throw spent the turn: opponent attacks, our mon does not. This
     needs the SAME daemon turn-consume op as player items (there is no legal "do nothing"
     choice for a healthy active — `pass` is rejected, verified in `side.ts` choosePass).
  4. **Formula [E2]**: three incompatible versions exist (07:541 3-shakes/multiplicative;
     12:128 4-shakes/**additive**; real gen4 = `a` capped 255, `b = 1048560/(255/a)^¼`,
     4 shake rolls, status bonus **multiplicative** ×2/×1.5). Adopt the real gen4 one.
     Rolls should draw from the battle's seeded RNG stream (client-side extension of the
     op:start seed) so scripted smokes stay deterministic.

### K2. Team-set construction for `op:start` (the fidelity choke point)
- **[K] Sets must be explicit.** Verified live this session: the fork does **not** default
  missing fields — a set without `ability` fights with NO ability (abilityless Gengar ate
  Earthquake super-effectively). The docs' party schemas missing `ability`/`nature`/
  `gender`/explicit moves is therefore not cosmetic — it silently produces wrong battles.
- **[K] Trainer schemas carry `{species, level, ivs}` only** (08:287, 11:545). Must add:
  `moves` (fixed 4-set — 11's rival array already has the field; drop it nowhere),
  `held_item`, `ability`, `nature`, `gender` — all present in cartridge trainer data.
- **[K] A1 — trainer AI data.** Replace 07 §AI with: trainer entries gain
  `"ai_flags": ["basic","expert",…]` (mask names per TRAINER_AI.md §6) and
  `"items": [...]` → passed to `op:start.ai` / `op:start.aiItems` (≤4 slots, daemon
  enforces). Item-use policy is the engine's `shouldUseItem` — delete 08:339-347's ad-hoc
  25%-threshold table.
- **[K] `baseAbility:"" `detection:** validate teams at load (every species must resolve an
  ability from `Dex.mod('gen4')` unless intentionally none) — CI-style guard so the
  Gengar-class bug can't ship in generated content.
- **[K] gen4 has no team preview**; forced switches arrive as `{forceSwitch:[…]}` (slot
  array over *active* slots), faint ⇒ `condition:"0 fnt"`, wait requests are informational
  and must NOT be answered (today's double-advance bug). These protocol facts belong in a
  "Battle client contract" section — they will bite the Java client identically.

### K3. Transcript→UI render map **[client]**
The daemon's output is the authoritative "message pipeline" (rev-1 G25 dissolves into this).
Docs need the event→animation/text table — the vocabulary is already solved in
`play.js` `Hud.update` (move/damage/heal/status/curestatus `[msg]` = "thawed out!",
weather upkeep/end, sidestart `move:` prefix strip, `-immune` ⇒ "But it doesn't affect…",
boost grouping onto the causing line, drag ⇒ "was dragged out!"). Enumerate the fork's
emitted lines (grep `add(` over `sim` + `data/mods/gen4`) so the Java renderer covers all,
not just the ones play.js hit.

### K4. Menu←request mapping **[client]**
Legality is *given*, never computed: `{active:[{moves:[{move,id,pp,maxpp,disabled}]}]}` →
move menu (gray disabled/0-PP), `side.pokemon[]` → party menu (fainted/exhausted rules from
`condition`/`active`), `{forceSwitch}` → forced switch, `{wait}` → ignore. play.js's
`pkmnItems()`/`battleMenu*` is the reference. This replaces every "which move can be
selected" rule the old engine spec carried.

### K5. Battle-result → world handoff **[client]** (mostly already in docs, keep)
HP/PP/status persistence out of battle via final mirrored state (play.js keeps live HP from
`-damage/-heal` + last `condition`); faint flags; `win`→prize money; **XP/level-up loop
retained** ([K] C5 unguarded `exp_thresholds[rate][101]` at L100 — fix); evolution; **E4 = room
sequence (F2, DECIDED) → §6.6, NOT back-to-back battles**; **obedience (G24, DECIDED
IMPLEMENT) sim-side → full spec §6.5**.

---

## 3. Surviving errors, contradictions, dangling refs (client scope only)

| # | Item |
|---|---|
| E1 | 07:365 crit×screens — **stays as a warning exemplar**: it is dead spec text now, but proves hand-copied formulas rot (gen4.ts:683 proves it wrong in one grep). Delete the section; cite the fork instead. |
| E2 | Catch formula (now fully client-owned): unify 07:541 / 12:128 → real gen4 (see K1) |
| E3 | 12:28 "no healing items in trainer battles" — Gen-1 rule; gen4 allows. Wrong either way under daemon items-op design |
| E4 | X-items +1 → **+2** stages (applies wherever the items-op/daemon extension executes them) |
| X2 | `save.money` consumed (08 vendor, 07 prize, 12 Amulet Coin) — no field in 10 |
| X3 | EV cap 255 (12:427) vs 0–252 (10:125) — state 252 usable / 255 storage |
| X4 | static_encounters IVs: 07:477 vs 11 schema — reconcile (sets need explicit IVs, K2) |
| X5 | naming drift (`X Defense`/`X Defend`, `Parlyz`/`Paralyze`, 11 display strings vs `ITEM_*`) + no mapping table |
| X6 | IV scale drift: rivals 0–255 (11:422) vs trainers 0–31 (11:566) — one export scale, documented |
| D2 | `usable_in_battle: true` referenced (07:594), never defined in 12 — define or drop |
| D3 | 05 rock-smash 10% "encounter table" has no key in 11 |
| C5 | level-100 threshold OOB (07:334) |
| C6 | PC boxes: 05:279 deposit/withdraw; no save array — **DECIDED: caught-with-full-party auto-sends to a PC Box. Save needs `boxes` array (§10; suggested default 32×30 HG-style, design constant)** |
| C7 | shiny generation — **DECIDED: flat 1/8192 roll at generation (§6.4)** |
| C8 | EV-gain consumption path (participants, Macho Brace/Power items) — evYield exported, never spent |
| C9 | friendship/happiness stat absent ⇒ Happiny/Golbat/Gloom/Togepi-line evolutions + Return/Frustration unrepresentable — **RESOLVED: fully specified in §6.3** |
| C10 | Hidden Power: fork derives type from IVs at battle time; `extras.md` design says **re-roll at catch** ⇒ simulator re-rolls IVs on capture (storage rule) — write it into the docs, or the two systems disagree silently. |
| — | F3/F4 move policy — **DECIDED**: wild >4-move selection = last 4 learnable at/below level from learnset (cartridge-ish, deterministic); at 4/4 on level-up the player MAY CANCEL learning (not forced replace — deliberate deviation from cartridge) |

## 4. Keep as-is (verified correct AND still client-relevant)

Encounters (06) and encounter rates/repel · trainer sight/trigger/dialogue flow (08) ·
save/progression/badge gates (09/10) · map/graph/pipeline docs (00-05, 11) · field items &
bag storage (12 minus §11's engine semantics) · between-battle persistence · battle UI
layout/music/sprite pipeline (07 §UI — animations now trigger off protocol lines) ·
mid-battle trainer dialogue hooks (`last_pokemon`/`half_hp` — detectable from mirrored
state) · the docs' "no stub list / authoritative-source-first" posture.

## 5. Priority (rev 2)

1. **BEYOND-THE-SIMULATOR daemon extension** — player-item op + turn-consume op (for
   player items and failed catches). Blocks K1's item/catch flows; build first.
2. **K2 team-set contract** — explicit ability/nature/moves/held_item + AI flags on
   trainers + load-time validation (the Gengar lesson).
3. **§1 deletions** — collapse the engine-spec half of 07 (+ abilities export, 12 §11
   semantics, 07 §AI) into a "Battle client contract" section pointing at
   INTEGRATION.md + play.js; fold the protocol facts (wait-ack, forceSwitch shape,
   fainted-condition, no team preview, seed) into it.
4. **K3/K4 render + menu maps** — enumerate fork events; mirror play.js.
5. **§3 fix list** — catch formula (real gen4), save schema (money/nature/ability/box),
   the X-refs and D-refs, L100 guard, EV path, friendship stat, HP-at-catch rule.

## 6. BEYOND THE SIMULATOR — non-doc work the above decisions create

### 6.1 Daemon (`qwenwork/task1/showdown_daemon.js`)
- **`op:"item"` (player bag action, human side).** Applies a gen4 bag effect to the
  live battle object (precedent: `engine.executeUsedItem`, engine.js:1080-1085, which
  already mutates battle state for CPU items) + emits a protocol line so transcripts
  stay self-describing + **consumes the requesting side's turn** for this turn only.
- **`op:"turnpass"`** (or item-op with no effect) — the failed-catch case: the client
  ran the catch roll off-mirrored state; the sim must now run the opponent's action
  with no player action. Mechanism constraint, both verified: no legal do-nothing
  choice exists (`pass` is rejected for healthy actives, `side.ts choosePass`), and
  no run/item commands exist in `side.choose`. Candidate implementations, in order of
  preference: (a) daemon-side volatile trick (grant `commanding` to the active mon so
  `pass` is legal — same trust level the engine already has), (b) minimal fork mod
  adding `case 'pass':` legality flag for daemon-authenticated streams (fork edit =
  user decision). Pick during implementation; document in INTEGRATION.md either way.
- Player-item asymmetry to preserve (verified in the port): **AI** items are folded
  into the decomp command phase — the AI's mon still attacks (engine.js:511); **player**
  items consume the mon's action. Both are cartridge-faithful; the daemon docs must say so.
- `op:"state"` — dump each side's final `{ident, condition, stats, moves/pp}` at battle
  end so the client doesn't have to reconstruct persistence purely from event replay
  (today play.js mirrors `-damage/-heal` + last `condition`; a dump op kills drift risk).
- Seed echo: `op:start` already takes `seed`; emit the resolved seed back once per
  battle so the client can extend the same stream for catch rolls (K1.4) reproducibly.
- `op:"status"` and `op:"selfhit"` — obedience outcomes that mutate live daemon state
  (disobedient nap / self-inflicted Pound-40 hit; §6.5). Same trust domain and
  turn-consume semantics as `op:"item"`. turnpass thus has three consumers: player
  items, failed ball throws, and loafing/snoring disobedience.

### 6.2 Extraction pipeline — artifact schemas (exact)

All paths relative to `game_folder/`. Every field listed is REQUIRED in the output; the
simulator must never need to open a decomp/showdown file at runtime.

**`pokemon_data.json`** — keyed by dex # (string). DELIVERED (2026-09-18) by
`qwenwork/extract_sim_data.py`; sample of the actual output:
```jsonc
{ "152": {
  "num": 152, "name": "Chikorita",
  "types": ["Grass"],
  "baseStats": { "hp":45,"atk":49,"def":65,"spa":49,"spd":65,"spe":45 },   // fork gen-4 dex view
  "abilities": ["overgrow"],                  // slot ids [, slot2]; HA never inline
  "hiddenAbility": null,                      // ALWAYS null: gen 4 has no hidden abilities
  "genderRatio": 0.125,                       // FRACTION FEMALE (donor semantics); -1 = genderless
  "baseFriendship": 70,
  "catchRate": 45, "expYield": 64, "growthRate": "Medium Slow",             // title case
  "evYield": { "hp":0,"atk":0,"def":0,"spa":1,"spd":0,"spe":0 },
  "heldItems": [],                            // [{id:"ITEM_*", rarity:"common"|"rare"}]
  "levelUpMoves": [{ "level":1, "move":"tackle" }],                         // move ids; ROM order kept per level
  "tmMoves": ["bulletseed", ...], "hmMoves": ["cut", ...],                  // via PT sTMHMMoves
  "evolvesInto": [ { "to":153, "condition":"level", "level":16 } ]
  //  LIST (Eevee has 7 entries). condition ∈
  //   "level" [+level][+gender]        "friendship" {threshold:220}[+timeOfDay]
  //   "item"   {item:"ITEM_*"}[+gender]                        (true use-item evos: stones &
  //             gendered items — consumed on use, works on any hour. Includes the synthetic
  //             ITEM_PRISM_SCALE → Milotic: gen-5 item, injected in items.json `synthetic`)
  //   "levelWithHeldItem" {item:"ITEM_*", timeOfDay}             (must HOLD the item and level up
  //             in the day/night window: Sneasel→Weavile, Gligar→Gliscor (night); Happiny→Chansey
  //             (day). ROM EVO_LEVEL_WITH_HELD_ITEM_*, pokemon.c:3655; item NOT consumed, feeding
  //             it never evolves. Replaces the old mislabel "item"+levelTriggered:true. Earlier
  //             "Silk Scarf" sample was wrong — Happiny is ITEM_OVAL_STONE, day)
  //   "trade"  [+item]                  "move"   {move: id}  (know-a-move evos)
  //     ⇒ v1 SUBSTITUTION (DECIDED): v1 has no trading ⇒ every "trade" row fires on
  //       LEVEL-UP instead, same evaluation point as plain level evos. "trade"+item
  //       (Karrablast→Escavalier, Shelmet→Accelgor, Seadra→Kingdra-style holds) = item
  //       HELD at the level-up, NOT consumed — mechanics identical to levelWithHeldItem
  //       minus the time gate. (For the other v1 substitution see "special" below.)
  //   "special" {note:"EVO_LEVEL_*"} [+level = genuine ROM param][+extra = ROM provenance]
  //     ⇒ v1 semantics (DECIDED 2026-09-18) — ROM gates KEPT verbatim except ONE row:
  //       MAGNETIC_FIELD (Magneton→Magnezone, Nosepass→Probopass) = fires on level-up
  //         ANYWHERE; the single dropped ROM gate (ROM = 13 Mt-Coronet headers + Spear
  //         Pillar variants + Hall of Origin + UNKNOWN_511, map_header.c:255-292).
  //       MOSS_ROCK/ICE_ROCK (Eevee→Leafeon/Glaceon) = ROM check KEPT EXACTLY: level up
  //         with party standing on Eterna Forest / Route 217 (whole-map check,
  //         map_header.c:251-254 — both maps exist in the simulator world; no coin).
  //       PID_LOW/HIGH (Wurmple, level 7) = ROM check KEPT: pidUpper byte % 10 < 5 →
  //         PID_LOW target (Silcoon 130/256 ≈ 50.8%, pokemon.c:3612-3624). v1 has no PID
  //         ⇒ roll ONE byte 0..255 at GENERATION and STORE it on the mon (stable across
  //         reloads, same distribution).
  //       ATK_* (Tyrogue L20) = real Atk/Def compare (no RNG). SPECIES_IN_PARTY (Mantyke)
  //         = Remoraid in party (extra[0] names it). NINJASK (L20) = plain level-up.
  //         SHEDINJA (L20) = free bonus copy when a party slot is open (ROM ball
  //         requirement dropped — v1 has no ball items).
  //       BEAUTY is no longer a special row: emitted as condition "item"
  //         ITEM_PRISM_SCALE (gen-5 mechanic; ROM beauty 170 unreachable — contests cut,
  //         §6.4; provenance kept in that row's extra).
  //       Multi-row eligibility at one level-up ⇒ FIRST MATCH in donor row order wins
  //       (ROM evolution loop, pokemon.c:3555-3717).
}}
```
Sources (delivered): donor = Platinum `res/pokemon/<dexid>/data.json` — ALL world fields,
`learnset.by_level`/`by_tm` (→ PT `sTMHMMoves`), typed `evolutions` (25-method enum, unknown
method ⇒ hard fail). Display identity (num/name/types/baseStats/ability display names) comes
from `qwenwork/sim_data/dex_dump.json` = the fork's OWN gen-4 mod view (`Dex.forGen(4)` —
crucial: base dex carries post-XY stat buffs; the gen-4 mod view restores ROM-exact stats,
machine-verified against donor all 493). PS learnsets are NOT used (donor by_level/by_tm is
ROM truth); `by_tutor`/`egg_moves` exist in donor, ignored per v1 cuts. HG personal.json is
the CI cross-check (below). Eevee→Espeon/Umbreon and Happiny/Chansey/Togepi/Golbat come out
as `friendship`/`levelWithHeldItem` conditions from the donor's typed evolutions (§6.3).

**Delivered pipeline (2026-09-18).** `node qwenwork/dex_dump.js` then
`python3 qwenwork/extract_sim_data.py` ⇒ `qwenwork/sim_data/{dex_dump,pokemon_data,moves_ui,
abilities_ui,items}.json`. Built-in gates: exactly 493 base donor species (forme folders
rejected); every referenced move/ability/item resolves (learnsets, TM/HM teaches, PT
`res/trainers/data/*.json`, HG `trainers.json` incl. `genderOverride`/`abilityOverride`
camelCase, terrain rival fixed sets, terrain map item refs incl. emerald `Item#flag|id`
numeric ids); evolution graph acyclic; level conditions 1–100; HG cross-check must show ONLY
the 5 known held-item rows (machine-confirmed). 4 PS-renamed moves aliased from ROM
constants: Faint Attack→feintattack, Hi Jump Kick→highjumpkick, Vice Grip→visegrip,
Smelling Salt→smellingsalts (SV respellings; the daemon resolves current ids).

**HG vs Platinum donor comparison (machine-checked 495 shared species, 2026-09-18).**
Sources compared: HG `personal/personal.json` ∪ Platinum `res/pokemon/<dexid>/data.json`
(per-specie JSON — note: PT's data.json carries ALL donor fields **plus clean `learnset`
and `evolutions` per species** — likely the last source needed for `evolvesInto`).
Verdict: **baseStats, catchRate, expYield, baseFriendship, eggCycles, growthRate, all six
evYields, genderRatio: 0 true diffs** (genderless = HG sentinel `2.0` ↔
`GENDER_RATIO_NO_GENDER`; the 11 "ability diffs" are HG constant spellings,
`COMPOUNDEYES`↔`COMPOUND_EYES`). **Real sub-gen divergences: exactly 5 wild held-item
rows** — Electabuzz/Elekid/Magby/Magmar hold their evolution items in DPPt-Platinum but
NOTHING in HGSS; Shuckle holds Oran (Platinum) vs Berry Juice (HGSS). Donor choice
matters for those 5 rows only ⇒ **recommend Platinum `data.json` as the donor** (matches
the fork's DPPt-lineage dex flavor), HG personal.json kept as the CI cross-check with
the two normalization rules above. HG additionally stores 12 form entries as pseudo-
species (Rotom forms, Deoxys forms, Wormadan, Giratina Origin, Bad Egg) — ignore; forms
are out of v1 scope via base forms only.

**`moves_ui.json`** — keyed by move id (lowercase):
```jsonc
{ "return": { "name":"Return","type":"Normal","category":"Physical","pp":20,
              "text":"User retaliation depends on friendship." } }
```
Display-only: name/type/category/pp + one flavor string. NO BP/effects/secondary — the
fork's dex is the sole mechanics source (§1 R). PP = base PP for TM descriptions;
in-battle PP always comes from the daemon request payload. TM/HM→move resolution lives in
`items.json.teaches` (below), not here.
DELIVERED: 470 entries = every gen≤4 dex move ∪ every ROM-referenced move (learnsets,
teaches, trainer fixed sets, know-move evos) ∪ struggle; `hiddenpower<type>` variants
excluded (dex-only pseudo-entries; daemon uses base `hiddenpower`). text = fork
`MovesText.shortDesc|desc` cleaned of `[Gen X]` tags (from `dist/data/text/moves.js`).

**`abilities_ui.json`** — keyed by ability id: `{ "name":"Levitate","text":"..." }`.
DELIVERED: 123 entries = donor ability slots ∪ fork gen-4 dex slots of the 493 (client may
show either view); text = fork `AbilitiesText` stripped of `[Gen X]` tags. Hooks export deleted.

**Trainer entries (extends 08:270-293 / 11:521-566)** — the K2 carrier:
```jsonc
{ "trainer_id":"TRAINER_LEADER_ROARK", "ai_flags":["basic","eval_attack","risky"],
  "items":["potion","full_restore"],            // ≤4, decomp TrainerHeader.items
  "party":[{ "species":"GEODUDE","level":12,
    "ivs":[31,31,31,31,31,31], "nature":"hardy", "ability":"sturdy",
    "gender":"M", "moves":["rockthrow","tackle","bulldoze","defensecurl"],
    "held_item":null, "shiny":false }] }
```
Source: Platinum `include/struct_defs/trainer_data.h` layout (TRAINER_AI.md:2465-2502 —
party/personality-IV/nature/item/AI-mask words) + `generated/ai_flags.txt` for mask names
(already reconciled in task2); HG equivalent from `files/poketool/trainer`.
**VERIFIED against the struct itself** (`trainer_data.h:17-64`): each trainer header
carries a `monDataType` byte selecting the per-mon layout — BASE = {ivScale, level,
species} only; WITH_MOVES adds 4 fixed moves; WITH_ITEM adds one held item;
MOVES_AND_ITEM adds both. **Only key trainers (leaders, rivals, E4, story) use the
non-BASE layouts** — most NPC trainers store literally nothing else. Consequences:
(a)+(b) **CLOSED AT THE SOURCE (2026-09-18)** — the game-folder trainer JSON now ships
`ai_flags` and full party fields; see the delivered contract below.
(c) **nature and gender are stored by NO layout** — the ROM derives them from a PID
seeded deterministically by `ivScale + level + species + trainerID` (trainer_data.c:206,
231, 261); our analogue: derive nature/gender from a hash of exactly those four values —
reproducible per game, no ROM data needed, applies to ALL trainers;
(d) `ivs` in the JSON is the raw `ivScale` — actual IVs = `ivScale*31/255` flat across
all six stats (trainer_data.c:214, `MAX_IVS_SINGLE_STAT 31`), so Drew's `ivs:10` means
IV 1 everywhere; convert at load, do not treat as final IVs;
(e) **trainer mons are forced shiny-OFF** (`OTID_NOT_SHINY` passed to InitWith,
trainer_data.c:216) — extracted sets emit `shiny:false`; a shiny NPC mon exists only if
the PCG deliberately forces one. The §6.4 1/8192 roll therefore applies to
wild/gift/PCG-generated mons, never to trainer parties.
Everything else is already extracted per map: sight line/range, class, leader/badge,
prize money, trainer bag `items[]` (→ `aiItems`), dialogue + hooks, rematch.
**DELIVERED extraction contract — trainer fields in game folders (verified against the
fixed pipeline, 2026-09-18; supersedes the gap audit that preceded it):**
- `ai_flags` on EVERY trainer, all three games: list of flag-name strings (e.g.
  `["BASIC","EVAL_ATTACK","EXPERT"]`). Gen-4 names are canonical: HG decodes its numeric
  mask to them, Emerald's `AI_SCRIPT_*` bits (`battle_ai.h:38-51`, 0-8 semantically 1:1)
  are emitted under the same Gen-4 names, and the three emerald-only specials
  (ROAMING/SAFARI/FIRST_BATTLE) are dropped at extraction. ⇒ **direct pass-through to
  `op:start.ai` via the TRAINER_AI.md §6 aiFlags bitmask; no runtime gen-3↔gen-4 name
  mapping exists or is wanted.**
- `party[n].moves`: present iff the ROM stores a fixed moveset (leaders/rivals/E4/
  story); key ABSENT ⇒ simulator generates via F4 last-4-learnable. Emerald default
  movesets are populated from learnsets and formatted.
- `party[n].item`: present iff a held item is stored; absent ⇒ none (ROM-true, §6.2
  nullability row).
- `party[n].gender_override` / `party[n].ability_override`: HG only, present only when
  the source override is non-OFF (e.g. `"Second"`). `ability_override:"Second"` ⇒ set
  ability = slot 2 of `pokemon_data.json.abilities`; gender_override forces gender.
- Per-mon IV scalar (`ivs`/`iv_scale`/HG `difficulty`) — same ivScale semantics, ×31/255
  flat at load ((d) stands; HG `difficulty` is that same byte).
- **Nature: absent in ALL sources, all games** — (c)'s hash-derivation is the only rule,
  not a fallback. Shiny stays forced-off for trainers ((e)).
Remaining legitimate "absent" fields for the §6.2 nullability table: `moves` (⇒F4),
`item`, `gender_override`, `ability_override` (⇒generation defaults). Everything else
on a trainer or party mon is now always present.

**Gen-3→gen-4 fixed-moveset retcon check (empirical, 2026-09-18):** all 180 fixed
trainer movesets extracted from Emerald (688 species-move pairs) were probed against the
fork's own gen-4 learnsets (`Dex.forGen(4)` / `Dex.data.Learnsets`, codes prefixed 3 or 4
= learnable in gen 3 or 4). **Result: 688/688 ok, 0 moves removed from the species in
gen 4, 0 not-learnable pairs.** Emerald fixed sets are gen-4-safe; the F4 last-4 fallback
plus verbatim `moves` (when present) carries them unchanged. (Control: a post-gen-4 pair,
`flamecharge`/charmeleon, is correctly flagged, so the criterion discriminates.)
⚠ Covers MOVES only; the TM/HM ITEM axis is settled separately below (empirical three-way
ROM diff — the expected renumbering collisions turned out NOT to exist for TMs, but DO for HMs).

**`items.json`** (gen3∪gen4 pool — user rule): keyed by `ITEM_*`:
```jsonc
{ "ITEM_X_ATTACK": { "name":"X Attack","dexId":"xattack","price":500,
                     "pocket":"battle-items","battle":true,
                     "battleEffect":"boost:atk:1","games":["emerald","platinum","heartgold"] } }
```
`battleEffect` ∈ closed enum consumed ONLY by the daemon `op:"item"` implementation —
DELIVERED set (derived from PT CSV columns, not hand-typed):
`heal:<n>|heal:eighth|healfull|healfullstatus|status:<st>|cureall|revive:<frac>|
boost:<stat>:<n>|cureblock|ppup|ppmax|pprestore:<n>|pprestore:all|levelup|evolve` else null.
X-items are **+1 stage** (PT CSV stage columns all =1; earlier "+2 verified gen4" was wrong
— retracted; +2 is gen-1 lore). `hpRestored` sentinel `253` = 1/8-max-HP (Sitrus-class);
status-cure CSV flags honored only in medicine/berry pockets (mail items carry junk flags).
gen3-only battle constants absent from the PT CSV get a 3-row cited table from
`pokeemerald/src/data/pokemon/item_effects.h`: ITEM_X_DEFEND (gen-3 US spelling of gen-4
ITEM_X_DEFENSE)→boost:def:1, ITEM_PARALYZE_HEAL→status:paralysis,
ITEM_ENERGY_POWDER→heal:50 (with friendship −5/−5/−10 side effect, §6.3, gen-3 only).
Balls carry `catchBonus:{mult[,cond]}` parsed from `battle_script.c`
`BattleScript_CalcCatchShakes` (basic 10/15/20 scheme; Net/Dive/Nest/Repeat/Timer/Dusk/Quick
conditionals); Heal/Luxury/Premier/Cherish/Master default ×1. (balls are separate: they go
through the catch flow K1, not item-op). `evolvesSpecies:[dexnums]` reverse-map on stones.
Source: PT `pl_item_data.csv` + emerald `items.h` + the ROM tables above.
**TM/HM per-game resolution — empirical three-way ROM diff (2026-09-18).** Sources:
emerald `include/constants/tms_hms.h` (FOREACH_TM/HM), platinum `src/item.c`
`sTMHMMoves` designated array, heartgold `src/item.c` positional table (the extractor's
positional mapping is valid: 100 entries = 92 TM + 8 HM). Results:
**TM01–50: IDENTICAL in all three games** (0 diffs — e.g. TM47 = Steel Wing everywhere);
**TM51–92: identical platinum↔heartgold, absent from emerald**;
**HM01–04/06–07 identical; the collisions are HM05 = emerald Flash / platinum Defog /
heartgold Whirlpool (three-way!) and HM08 = emerald Dive vs gen-4 Rock Climb.**
(Retracted: earlier "TM47 = Low Kick / TM58 = Charge Beam" collision examples were
misremembered lore — the ROM tables disprove them; Low Kick is not a gen-4 move.)
Design per user rule: `items.json` TM/HM entries carry per-game payloads —
`"teaches": {"emerald":…, "platinum":…, "heartgold":…}` (TM entries: same value in all
columns; only HM05/HM08 actually vary) plus `"available": ["heartgold","platinum"]` for
the gen-4-only TM51–92 block (never placeable in emerald-flavored content). The
simulator resolves `teaches` by the game folder's `game` tag; field/traversal gating keys
on the FIELD MOVE identity (Dive, Rock Climb…), never the item number, since numbers
drift. Badge table 00:152 is gen-4-flavored and must be generated per game for emerald
maps (Dive instead of Rock Climb in the HM08 slot).

**Delivered items.json notes.** 523 entries. Keys are `ITEM_*` constants (stable across
games; terrain display names resolve through normalized match). `dexId` null for TM/HM
and other ids PS deleted from the modern dex (name falls back to ROM display style
`TM01`/`HM05`). Terrain display conventions (extractor 2.0, commit "better tm logging"),
all understood + machine-checked by the pipeline:
- ground TMs display as `"TM01 (Focus Punch)"` in ALL three games — the number resolves
  to `ITEM_TM01` and the parenthetical move is CROSS-CHECKED against that game's `teaches`
  entry (any drift = warning; currently zero drift). Client renders the same string from
  `name` + `teaches[game]` — no per-game name variants needed in items.json.
- bare `"TM Double Team"` reward tiles resolve via the per-game move→TM reverse map
  (emerald/platinum/heartgold tables differ correctly).
- `"Item#NNNN"` numeric tiles (emerald ids, and the platinum numbering the new extraction
  emits for Great Marsh hidden items) resolve through the game's own numbering: emerald/
  heartgold `items.h` defines, platinum `pl_item_data.csv` ROW ORDER (= gen-4 id; spot-
  checked rows 72/82 == heartgold `RED_SHARD`/`FIRE_STONE`). Earlier "ITEM_048/052
  unresolved" warnings were the emerald-only lookup misapplied — retracted, they are Red
  Shard-class items.
- trainer/rematch party `moves` arrive either as pretty display names (ROM-era spellings:
  "Faint Attack") or bare constants (`SMELLING_SALT`); both go through the PS-rename alias
  map (§6.2 delivered-pipeline note).`

**Event render map** `battle_events.md` (generated): one row per protocol line shape —
regex, captured fields, HUD slot (bars/messages/menu), animation trigger, fallback.
Generate: grep `add(`/`this.add(` over `pokemon-showdown/sim` + `data/mods/gen4`; seed
the manual half from play.js `Hud.update` (already battle-tested against real transcripts).

`exp_thresholds.json` — DELIVERED (emitted by the pipeline into `sim_data/`): six growth
curves as cumulative-total arrays indexed 0..100 (xp-to-next = t[lvl+1]−lvl formula),
formulas parsed from `pokeemerald src/data/pokemon/experience_tables.h` with C floor
division; levels 0/1 are ROM literals (formulas go negative at 1). Anchors asserted every
run (1000000/800000/1250000/1059860/600000/1640000); all six rates occur among the 493.
Semantics unchanged from 07:121-137 (client-side; XP is not in the fork).
Deleted artifacts: `abilities_data.json`, `types_data.json` (§1 R).

**Rerun + verification summary (2026-09-18).** Full green run: 493 species, 470 moves,
123 abilities, 523 items; HG cross-check diffs = exactly the 5 known held-item rows;
terrain + trainer coverage warnings = 0 (after TM-display + per-game numeric resolution;
the terrain extractor was meanwhile regenerated — pipeline re-verified against it).

**Field nullability & load-time defaults** (today scattered: 11:80 is the extraction
contract — every key present, empty data as `[]`/`null` per the 11 field tables;
08:28 all trainer-trigger fields required; 11:423 `party[].moves` omitted ⇒ level-up
defaults. The gap was what the SIMULATOR does with a null — closed here. Golden rule:
**null = legitimate absence ⇒ apply the default below; invalid = wrong type/id ⇒ HARD
FAIL at load, never silently patched** (CI gate, K2). **No null may cross the daemon
boundary** — the fork's own defaults are traps (omitted `ability` = abilityless,
omitted `happiness` = 255, pokemon.ts:342), so defaults resolve simulator-side, always.
  | field (per-mon) | may arrive null/absent? | resolution |
  |---|---|---|
  | species, level | no — null ⇒ folder invalid, hard fail | — |
  | ivs | yes (static/wild) | trainers: `ivScale*31/255` flat (§6.2(d)); rivals ÷8 (X6); null ⇒ roll 6× uniform 0–31 from the game-seeded stream, STORE on catch/generation (never re-roll silently — re-roll only at K1.2 capture) |
  | moves | yes | null ⇒ F4 last-4-learnable; explicit ⇒ respect verbatim |
  | ability | yes | HG `ability_override` wins ("Second" ⇒ slot 2); else dex slot 1 from `pokemon_data.json`; NEVER forward null to daemon |
  | nature, gender | yes | HG `gender_override` wins when present; else hash(`ivScale+level+species+trainerID`) for trainers (§6.2(c)); seeded roll for wild; stored explicitly in save. **Nature has no override source in any game — hash/seed only** |
  | held_item | yes — and null MEANS NONE (ROM-true) | forward as-is (daemon accepts no-item) |
  | shiny | yes | false for trainer sets (§6.2(e)); 1/8192 roll wild/gift/box (§6.4) |
  | happiness (daemon set field) | **never null, ever** | player mon: current friendship; NPC mon: species baseFriendship |
  | evs/exp/level-ups | yes (fresh mon) | zeros; growth handled by client-side XP loop |
  | trainer bag items | yes ⇒ `[]` | engine gets `aiItems:[]`, no item policy change |
Load validation is fail-fast and per-file: one bad id kills the game folder at load with
the offending path printed — never a battle that fights with wrong data.

### 6.3 Friendship — full implementation spec (simulator-side state)

Confirmed scope: Platinum computes friendship ONLY outside battle; the fork consumes a
static copy for Return/Frustration. All decomp facts below verified in
`pokeplatinum/src/pokemon.c` + `constants/pokemon.h:25-31` (+ call sites).
- **Storage**: `friendship: int 0..255` on every save party/box Pokémon (§10 schema add).
  Initial value = species `baseFriendship` DIRECTLY at creation/catch — NO halving
  (pokemon.c:426-427 sets base; the base/2 "rule" is fanon). Default base when missing: 70
  (`BASE_FRIENDSHIP_VALUE`).
- **Daemon mirror**: every team set passed to `op:start` MUST include `happiness:
  <current friendship>` — the fork defaults an omitted happiness to **255**
  (pokemon.ts:342), so omission = free max-power Return. Return/Frustration base power is
  computed BY THE FORK; the simulator never calculates it. (Cartridge parity: battle
  code only ever *reads* friendship — battle_system.c:720-733.)
- **Change table** (pokemon.c:2613-2624; tier = friendship <100 / 100-199 / ≥200):
  | event | <100 | 100-199 | ≥200 | trigger (call site verified) |
  |---|---|---|---|---|
  | level-up | +5 | +3 | +2 | every level gained (battle_script.c:10080) |
  | beat Leader/E4/Champion | +3 | +2 | +1 | battle_main.c:1364-1369 |
  | learn TM/HM | +1 | +1 | 0 | party menu (unk_02084B70.c:1129) |
  | walking cycle | +1 | +1 | +1 | every 128 player steps, whole party, each mon 50 % coin flip (field_control.c:750-878 — counter ≥128, then `LCRNG_Next()&1` skips) |
  | faint | −1 | −1 | −1 | battle_script.c:12312-12317 |
  | faint vs foe ≥30 levels higher | −5 | −5 | −10 | same site, level-diff branch |
  | survive poison/badly-poison at battle end | −5 | −5 | −10 | unk_02054884.c:199 |
  | contest win | +3/+2/+1 | — | — | no contests in v1: omit row |
- **Modifiers** (all apply only to positive gains, pokemon.c:2655-2671): held Soothe Bell
  (`HOLD_EFFECT_FRIENDSHIP_UP`) ×150/100 floor; held Luxury Ball +1; born-from-egg on the
  current map +1 (v1: skip — no daycare). Then clamp 0..255.
- **Does NOT touch friendship** (verified absent in Platinum): PC healing, box storage,
  vitamins (no call site exists; table entries UNK_1/UNK_2 are dead code). Do not
  implement Bulbapedia lore here.
- **Evolution**: condition `friendship ≥ 220` (`EVOLVE_FRIENDSHIP_THRESHOLD`,
  pokemon.h:25) evaluated in the post-battle evolution pass (same "after all battles"
  timing as 07 §Evolution), plus Espeon/Umbreon/Glaceon-style day/night gates from
  `pokemon_data.json.timeOfDay`.
- **UI bands** (optional flavor): 255/200/150/100/50/0 friendship-NPC reaction tiers.

### 6.4 Data-rule decisions (design, not extraction)
- **Shiny rule** (C7) — **DECIDED: flat 1/8192** roll at generation (wild/box/gift mons);
  trainer parties stay `shiny:false` to match the ROM's forced `OTID_NOT_SHINY`
  (§6.2(e)) unless the PCG deliberately forces one. No PID model — cartridge shiny-RNG
  parity is explicitly out of scope.
- **IV scale**: one export scale everywhere = **0–31** (fix X6; rival-array 0–255 values
  ÷8 at export time).
- **Gender**: roll at generation from `genderRatio` (independent random bit), STORE
  explicitly — no IV-derived gender (cartridge uses PID; we have no PID. Daemon-visible
  behavior is unaffected — gender only matters to Attract etc., which read the set field).
- **Hidden Power at catch** (extras.md): re-roll IVs on capture; HP type then follows the
  new IVs through the fork at battle time. One paragraph in 06/12, and the catch flow
  (K1.2) records "IVs re-rolled" for the summary screen.
- **Trade evolutions (v1) — DECIDED: level-up substitution.** All `condition:"trade"`
  rows (plain and +held-item) trigger at the post-level-up evolution pass; §6.2's
  contract block above is the normative spec.
- **Special evolutions (v1) — DECIDED: only Magnezone/Probopass change behavior**
  (magnetic-field map gate → level-up anywhere). Everything else keeps the ROM check:
  rock evos stay whole-map gates (Eterna Forest / Route 217), Wurmple keeps the
  ≈50/50 split via a byte stored at generation, Tyrogue compares real stats, Mantyke
  needs Remoraid in party, Shedinja is the slot-gated bonus copy. Feebas→Milotic is
  the gen-5 Prism Scale use-item (synthetic items.json entry — the 170-beauty gate is
  unreachable with contests cut). OPEN: where the sim world sources a Prism Scale.
- **Pause-menu time control (v1) — DECIDED.** Simulator clock = one integer
  `clockHour: 0..23` in the save. Pause menu entry "Time": set/step the hour by 1
  (wrap both ends). Everything derives from the hour, ROM-faithfully:
  5-phase lookup `TimeOfDayForHour` (pokeplatinum/src/rtc.c:155-180 — late-night 0-3,
  morning 4-9, day 10-16, twilight 17-19, night 20-23) for future encounter gating;
  evolution day/night via `IsNight` (rtc.c:134: night = 20:00-03:59 only; twilight is
  DAY). Natural advance (auto-tick) remains the open tuning knob; menu edits are instant
  and take effect at the next evolution check.
- **Trainer rematch triggers (v1) — DECIDED: no phone system.** Tiers come from the
  export (`_REMATCH_N` rows in `generated/trainers.txt`; HG same-name duplicate rows,
  ascending level tiers). Gate G1: after the 8th badge, the first entry into any
  Pokémon Center arms the rematch system; the next conversation with a rematchable
  NPC plays their tier-1 rematch. Gate G2: after the Elite Four are beaten, the next
  conversation plays tier 3 IF the trainer has one. All subsequent rematch fights —
  including a tier 2 where no 3rd exists — are availability-gated: the trainer must be
  left behind (player exits the current map) and re-entered to reset; then talk = next
  tier, ascending, one fight per area cycle, highest available row repeating once the
  trainer's list is exhausted.
- **Trainer sight (v1) — DECIDED: hardcoded cap, per-trainer ranges NOT extracted.**
  A straight cardinal ray in the trainer's facing (`sight_direction` in terrain; ALL
  for leader-type entries), range capped at **6 tiles flat** (emerald's per-object
  bytes run 0–7, `trainer_sight_or_berry_tree_id`; deliberately ignored — do not
  extract). The ray stops at the first impassable tile/object between trainer and
  player — any tile the player cannot walk through blocks sight; grass and other
  walkable features do not. Trigger: stepping into an unblocked ray starts the battle
  (ROM shape, `trainer_see.c:326-359` directional check); trainers never move on their
  own (no patrol extraction).
- **Scope cuts (DECIDED)** — **Day Care/breeding/eggs/egg-moves and Contests are CUT
  for v1**: facility legends 03:76-77 / 11:655 become decorative buildings, no egg
  inheritance rules, no appeal engine; the §6.3 contest-win row and the hatched-on-map
  +1 stay cut accordingly, and HP-at-catch needs no egg exception. **Badges are fully
  IN scope as already specced** (00 HM-unlock table + E4 gate + 09 BadgeManager; the only
  gen4 extras — obedience caps — are consumed by §6.5). Reversing breeding later = its
  three ripples above, nothing else.

### 6.5 Obedience — full spec (simulator-side; DECIDED)

Not in the fork (verified: no `obey` in `sim/` or `data/mods/gen4`). Cartridge
implementation = `pokeplatinum/src/battle/battle_controller_player.c:2111-2226`; the
simulator re-implements its logic at the p1-request point: after `request` arrives, roll
the check from the battle's echoed RNG stream (§6.1), THEN answer or route a daemon op.
- **Auto-obey gates** (any ⇒ normal command): mon's OT == player (⇒ save MUST store
  `ot`/`ot_id` per mon); badges ≥8; level ≤ cap; BIDE last-of-multi-turn; can't-pick-
  command; `BATTLE_TYPE_NO_OBEDIENCE_CHECK`.
- **Badge → level cap**: <2→10, ≥2→30, ≥4→50, ≥6→70, ≥8→∞.
- **Obey roll**: `rand8 = one 0..255 draw from the stream; obey iff
  (rand8 × (level + cap)) >> 8 < cap` (two successive rolls use the same formula —
  failure probability grows with level, exactly like the cartridge).
- **Fail outcomes in order**:
  1. sleeping + chose Snore/Sleep-Talk → do-nothing → `op:"turnpass"`.
  2. second obey roll succeeds → replace command: uniform random valid move (excluding
     Struggle) + fresh target; answer the request with that `move` (legal choice — the
     daemon can't tell); print "X won't obey! Used Y instead!". ⚠ RAGE edge: cartridge
     clears the rage lock here (c:2158), but the daemon owns the lock — if the active is
     rage-locked, degrade to turnpass (documented deviation).
  3. `gap = level − cap`; `r = rand8`:
     - `r < gap` ∧ mon healthy ∧ no Vital Spirit/Insomnia ∧ no UPROAR → falls asleep:
       `op:"status"` slp (persists like the cartridge) + message "fell asleep despite
       orders".
     - else `r−gap < gap` → hits itself: `op:"selfhit"` = POUND, bp 40, damage-variance
       roll from the same stream, no type chart (c:2213-2221: `CALC_SELF_HIT(MOVE_POUND,
       40)` ×variance) + "turned around and attacked!".
     - else → loaf around: `op:"turnpass"`.
- **v1 reality check**: nothing produces OT≠player mons yet (no trades) ⇒ the check is
  dead code until an event/NPC-trade mon ships — implement the plumbing + `ot` save
  fields NOW so a traded-mon source later needs zero rework.
- Text strings are cosmetic (match cartridge flavor loosely); the mechanics above are exact.

### 6.6 Elite Four — room-sequence design (DECIDED, replaces "sequential chain")

The current generator has no E4 structure; do NOT fake it with back-to-back daemon
battles. Design: **5 hardcoded rooms** (4 E4 members + Champion), chained by doors:
door N → forced entry to room N+1 → trainer trigger fires the daemon battle
(normal 08 trigger rules). After the win: post-battle dialogue on the NPC, player walks
freely, **heal station per room** (cartridge parity: heal *before* the next door, at
player's discretion), next door advances. Save field `e4_progress: 0..5` = rooms cleared;
blackout = standard blackout penalty, re-entry resumes at room `e4_progress`. Friendship
BEAT event fires per member win (same table row as gym leaders — battle_main.c:1364-1369
covers E4/champ). Ship as a static league template the generator injects into any game
(rooms/doors/NPCs) + the 5 trainer entries from extracted data.

### 6.7 Verification / test assets
- **Golden transcript tests**: keep `play_log_*.txt` sessions as fixtures — the Java
  BattleMirror must reduce each recorded transcript to the identical state sequence
  (HP/status/turn blocks) play.js produced. Free regression suite for K3/K4.
- **Play.js is the spec** — when doc and client behavior disagree, play.js+daemon are
  ground truth (they're the same engine the Java client will talk to).
- **CI team validation** (K2): every generated trainer/wild set must resolve a non-empty
  ability and carry ≤4 legal moves before a game folder ships.
