# Task 3 Plan — Trainer AI in the Showdown daemon

Status: **DONE** (2026-09-18). All 11 flag modules ported, solo- and combination-tested (`ai_test.js all`, all-11 mask), daemon AI mode wired + regression-green (`daemon_test.js`); machine-independent (relative requires; node ≥ 16, developed/tested on 24). Per-module audit status and protocol: `qwenwork/task3/README.md`; deviations, pinned constants, and session log: `qwenwork/task3/NOTES.md`.

## Goal

Make `qwenwork/task1/showdown_daemon.js` able to run battles where the p2 (opponent)
side is driven by a **reimplementation of the Gen 4 Platinum trainer AI**
(spec: `qwenwork/task2/TRAINER_AI.md`), in addition to the existing Java-driven
playerxplayer mode.

Deliverables:

1. **11 loadable AI module scripts** — one per active trainer-data AI flag
   (`BASIC`, `EVAL_ATTACK`, `EXPERT`, `SETUP_FIRST_TURN`, `RISKY`,
   `PRIORITIZE_EXTREMES`, `BATON_PASS`, `TAG_STRATEGY`, `CHECK_HP`, `WEATHER`,
   `HARRASSMENT`). The simulator "simply loads one of them" (or a set) at battle
   start, mirroring Platinum's `aiFlags` bitmask.
2. **A small shared engine** doing what `trainer_ai.c` does around the script
   table: per-turn init, damage-roll baseline, running the loaded modules in
   canonical flag order, move selection with the decomp's random tie-break,
   plus the `PickCommand` phases that live in C, not in the script table —
   switching (§4 of the spec) and item use (§5).
3. **Daemon extension** adding `ai` / `aiItems` to the `start` request; when
   present, the daemon auto-answers p2's `|request|` lines in-process.

## Non-goals

- **No subprocesses, pipes, IPC, or external services** anywhere in the AI path.
  Everything is a synchronous `require`d module running inside the daemon's
  existing Node process against the live `Battle` object.
- **No new information layer.** No JSON dumps, no REST, no parallel data
  structures for battle state. Mid-battle data comes from what the fork already
  exposes: the `Battle`/`Side`/`Pokemon`/`Field` objects, `battle.dex`, and the
  protocol lines the daemon already reads. (The small static item effect table
  in §B is item *metadata* from the decomp, not battle state.)
- **No changes inside `pokemon-showdown/`** (no fork, no data fixes).
- No Gen 4 **HeartGold** variant (spec §7 is documentation-only here; the
  reimplementation is Platinum's, per Task 2).
- No **escape/safari** actions (trainer battles have no escape option; the
  operator's `cancel` stays the only abort path).
- **p2-only.** The AI drives the opponent (p2). p1 stays Java-driven in all
  modes. (Making it side-agnostic is a trivial later extension, not now.)
- No change to `qwenwork/task2/TRAINER_AI.md`.

## Success criteria

- `node qwenwork/task1/battle_test.js` still passes (sim untouched).
- A daemon battle with **no** `ai` field behaves exactly as it does today
  (playerxplayer byte-compatible).
- A daemon battle with `ai: "…"` runs to completion with **zero** `|error|`
  lines from the AI side, for every one of the 11 modules individually and for
  the Frontier full profile `["basic","eval_attack","expert"]`.
- Same seed + same module set ⇒ **identical battle transcript** across two runs
  (determinism, including the AI's own dice and its damage estimates).
- Hand-checked spot-checks: at least one forced switch, one voluntary §4 switch,
  one item use, and per-module move choices match values computed by hand from
  the spec's formulas.

## Background (verified against this checkout)

### The daemon today (`qwenwork/task1/showdown_daemon.js`, 146 lines)

Line-delimited JSON over stdio. `start` builds `new Sim.BattleStream()`
(`Sim = require('<repo>/pokemon-showdown/dist/sim')`), splits it with
`Sim.getPlayerStreams(base)` into `{omniscient, p1, p2}` sub-streams, writes
`>start` / `>player p1` / `>player p2`, then runs three reader loops emitting
`{type:'line', id, side, line}` and detecting `|win|`/`|tie|`. Java sends
`{"op":"request","side":"p1|p2","choice":"move 1" | "switch 3" | "team 1 2 …" | "default"}`
and the daemon writes it verbatim to that side's sub-stream. `cancel` writes
`>forcetie` / `>forcewin`.

### The AI's view of the battle (all read-only, all already in memory)

|Need (Platinum `AIContext` / spec §)|Showdown source (fork)|
|---|---|
|Whole parties, actives, faints|`battle.sides[0..1]` → `side.pokemon[]` (`hp`, `baseMaxhp`, `fainted`, `isActive`), `side.active[]`|
|Status, volatiles, boosts, ability, held item|`pokemon.status` (`'slp' 'par' 'brn' 'tox' 'frz'`), `pokemon.volatiles`, `pokemon.boosts`, `pokemon.ability`, `pokemon.item`|
|Moves + PP per slot|`pokemon.moveSlots[]` = `{id, move, pp, maxPP}` (4 slots; PP-burned ⇒ slot absent/disabled)|
|Weather, turn|`battle.field.weather` / `field.effectiveWeather()`, `battle.turn`|
|Type chart, move/ability/item data, gen-4 categories|`battle.dex` (`dex.moves.get(id)`, `dex.species.get(id)`, `dex.getEffectiveness(move, type)`, `battle.getCategory(move)` = `dex.moves.get(move).category \|\| 'Physical'`)|
|Damage estimate|**`battle.getDamage(source, target, move, suppressMessages)`** — the engine's own damage pass (`sim/battle-actions.ts:1585`), so estimates can't drift from what the engine does. Returns a number (a single roll; `0` is a valid 0-damage hit), `false` (move fails), `null` (silent fail), `undefined` (no damage). `move.ohko ⇒ target.maxhp`. Crit: gen ≤ 5 uses `critMult [0,16,8,4,3,2]` rolled via the battle PRNG — a fresh `dex.getActiveMove(id)` with `willCrit = false` makes the estimate **non-crit and deterministic**.|
|Effectiveness of a move on a target|`target.runEffectiveness(move)` (`sim/pokemon.ts:2214`: per-type `dex.getEffectiveness` + `Effectiveness` events) — the same call the engine makes inside `getDamage`.|
|Legal actions this turn|the `|request|` JSON the daemon already receives for p2: `teamPreview`, `turn`, `wait`, `forceSwitch`, `active.moves[]` (`pp`, `disabled`), `side.pokemon[]` (`condition: "hp/max"`, `active`). The AI may only pick from what the request lists; `default` is the fallback (first legal action).|
|Last move that **hit** battler X|**not on the object** — `pokemon.attackedBy` is `{source, damage, thisTurn}` (no move field). Solved by a small in-process **observer** that scans the protocol lines the daemon already reads (`|damage` / `|-hit` with `[of] <attacker>` and `[from] move: <name>`) and keeps `lastHitMove[battlerId]`. Reading existing protocol output; no new data system. Fallback to `pokemon.lastMoveUsed` (`sim/pokemon.ts:172`) where a line lacks move attribution.|
|Last move **used** by battler X|`pokemon.lastMove` / `lastMoveUsed`|

### The battle is quiescent at request time

`|request|` for a side is emitted by the engine after turn state is settled,
before that side's choice is applied; the battle advances only once choices are
in via `battle.choose(sideid, input)` (`sim/battle.ts:2964`). So between a
request and the daemon's reply, reading `rec.base.battle` (the `BattleStream`
exposes `battle: Battle | null`) is safe and stable — no polling, no race.

### RNG discipline (why the AI gets its own dice)

Two separate random sources exist, and they must not mix:

- **Battle PRNG** — `battle.prng` (`sim/prng.ts`). With the daemon's numeric
  seed arrays (e.g. `[7,3,11,5]`) it wraps a `Gen5RNG` whose entire state is
  `rng.seed` — a plain 4×16-bit array that `next()` advances in place
  (`prng.ts:247`). Therefore snapshot = `const saved = [...battle.prng.rng.seed]`,
  restore = assign it back. This makes the AI's damage estimates (which call
  `getDamage`, and events inside it can consume rolls) **invisible to the
  battle's random stream**: a same-seed battle plays identically whether or not the
  AI estimated damage.
- **AI dice** — Platinum's AI has its own LCG (`trainer_ai.c` `Rand()`,
  constants recorded in spec §1), separate from the battle RNG, used for the
  damage-roll baseline (`100 - Rand() % 16` ∈ `[85,100]` per move — this is the
  *estimate baseline* stored in `AIContext.moveDamageRolls`, **not** an
  additive score bonus; spec §8 records this correction), the selection
  tie-break (`Rand() % n` among max-score moves, `trainer_ai.c:316-338`), and
  the per-check probability rolls. We implement it as a tiny in-process LCG
  seeded deterministically from the battle seed string, so AI behavior is
  reproducible per seed without touching `battle.prng`.

### Known fork gap (documented, not fixed here)

Real gen 4 re-splits the physical/special categories (e.g. Earthquake and Dig
are **Special** in gen 4; Headbutt and Body Slam are **Physical**). This fork's
`data/mods/gen4/moves.ts` carries only 5 `category:` overrides, so its gen 4
battles use the modern split for most moves. The AI **must** use the engine's
own `getDamage`/`getCategory` path — doing so keeps estimates consistent with
what the engine actually deals, which is what matters for a faithful *of-this-
simulator* AI. "Fixing" the split in AI-local code would make the AI score
moves against a different atk/spa than the engine uses. Correcting the fork's
gen 4 data is out of scope (fork is read-only for this task) and recorded as a
fork-level gap.

### Relationship to the decomp

- **Runtime: none.** The AI is pure JavaScript inside the daemon's process —
  no C, no NDS binaries, no `gTrainerAITable` word table, no decomp data files
  loaded at battle time. The only decomp-derived input is the `start` request
  itself (`ai`, `aiItems`) — the same values the game reads from trainer data.
- **Implementation: the Task 2 spec is the working source**
  (`qwenwork/task2/TRAINER_AI.md` — every routine's N values, jump targets, and
  code-vs-comment mismatches, already ported from
  `pokeplatinum/src/battle/trainer_ai/`). The decomp checkout is a tiebreaker
  for spec ambiguities, not a build input.
- **Why not move the decomp's own AI instead**: the per-flag logic is a
  word-encoded script table (`script.s`) run by a 109-opcode interpreter in
  `trainer_ai.c`, and the two are one interlocked unit — the opcodes come from
  a shared pool, so no per-flag subset exists (running even one flag needs the
  whole interpreter); the interpreter needs the decomp's own data tables (type
  chart, items, moves) and its own battle state, so driving it on our battles
  would require a decomp→Showdown state adapter, i.e. exactly the new
  information layer we are avoiding; and the decomp's `DAMAGE_ESTIMATE` would
  score moves against its own damage model instead of what the engine actually
  deals. Switching (§4) and items (§5) live in the C engine, not the script
  table, so "taking some modules" does not avoid the engine either. The 11 JS
  modules are the decomp's per-flag logic in the only form this simulator can
  load: one file per `aiFlags` bit, executed against the live `Battle` object,
  with the Java side still selecting profiles by the same `aiMask` the game
  uses.

## Design

### A. Architecture

```
Java (unchanged)                     daemon (extended)                     in-process
─────────────────    JSON/stdio    ───────────────────────    ─────────────────────────
start{team1,team2,  ─────────────▶  BattleStream               engine.js
  ai:"expert"|"full"|      base.battle ─────────────────────▶ │  decide(request)
  ["basic","expert"],          (live Battle)                  │   1. team preview → "team …"
  aiItems:[…], aiDir}   line events ◀───────────────          │   2. wait → nothing
request{side:"p1"}  ─────────────▶  streams.p1.write(...)     │   3. switch phase (spec §4)
request{side:"p2"}  ──▶ (rejected, error — AI owns p2)      │   4. item phase (spec §5,
                                                             │      if aiItems given)
                                                             │   5. move scoring:
                                                             │        init scores (s8) = 100,
                                                             │        rolls = 100 - aiRand()%16
                                                             │        run loaded modules in
                                                             │        canonical flag order
                                                             │        select max, tie → aiRand%n
                                                             │        → "move N" | "switch N" | "default"
```

- **playerxplayer** (no `ai` field): identical to today. Java writes both sides'
  choices; the daemon only relays.
- **playerxAI** (`ai` present): the p2 reader loop intercepts `|request|` lines,
  calls `engine.decide()`, and writes the resulting choice to `streams.p2`
  through the same write path Java uses. A `request` for `side:"p2"` arriving
  from Java is **rejected with an `error` event** in this mode (single source of
  truth for p2).

### B. `start` request additions

```json
{ "id": "b1", "op": "start",
  "formatid": "gen4customgame", "seed": [7, 3, 11, 5],
  "name1": "You", "team1": [ … ], "name2": "Trainer", "team2": [ … ],
  "ai": "expert",                      // or ["basic","eval_attack","expert"]
                                        // or preset "full" (= 0x7: basic+eval_attack+expert,
                                        // the Frontier mask, spec §6)
  "aiItems": ["potion","full_restore","full_heal","x_attack"],
  "aiDir": "/abs/path/to/ai" }         // optional; default <repo>/qwenwork/task3/ai
```

- `ai`: a module id (string) or array of module ids. Resolved to
  `require(aiDir/<id>.js)`. Unknown id ⇒ `error` event before the battle starts
  (fail loud, don't silently downgrade). Loaded modules run in **canonical flag
  order** (bit 0→10: basic, eval_attack, expert, setup_first_turn, risky,
  prioritize_extremes, baton_pass, tag_strategy, check_hp, weather, harassment)
  regardless of the order given.
- `aiItems`: the trainer's pocket (spec §5 `trainerItems`, up to 4 items).
  **Absent/empty ⇒ item use disabled** — the AI never uses items and the item
  phase is skipped (no invented defaults). **Effect data comes from the
  decomp**: this fork's item table carries no consumables (verified: no
  Potion-family entries under `pokemon-showdown/data/`), but the decomp
  checkout has the per-item effect data as JSON
  (`pokeplatinum/res/items/data/*.json` — Potion: `hpRestored: 20`,
  `battleUseFunc: 2`, `battlePocket: RECOVER_HP`), the same records
  `tools/dataproc/src/itemproc.c` packs into the `ItemPartyParam` /
  `ITEM_PARAM_*` fields (`include/item.h`) that the game loads. The executor
  carries a small generated table of the trainer-pocket items built from those
  files.
- `aiDir`: defaults to `<repo>/qwenwork/task3/ai`.
- The AI drives **p2 only**. `seed` stays required (it drives both the battle
  RNG and the AI dice seed, which is what makes transcripts reproducible).

### C. Module interface (the contract for the 11 scripts)

```js
// qwenwork/task3/ai/basic.js
module.exports = {
  id: 'basic',                    // canonical name = filename
  bit: 0,                         // Platinum ai_flags bit (spec §6) — docs only
  /**
   * One call per AI decision, in canonical flag order, after the engine
   * initialized scores/rolls. Mutates ctx in place; returns nothing.
   * Set ctx.cancel = true to abort later modules (decomp `Terminate`).
   */
  decide(ctx) { /* … */ }
};
```

`ctx` — the only view a module ever sees (engine builds it; no other
abstraction layers):

```js
{
  battle, side, foe,               // Battle / Side / Side (this AI = side)
  active, foeActive,               // this-side active / opponent active Pokemon
  turn, weather,                   // battle.turn, field.effectiveWeather()
  moves, foeMoves,                 // this / opponent active moveSlots[] {id, pp, maxPP}
  scores,                          // s8[4]: running moveScore (engine init: 100 per
                                   // legal slot; decomp s8 wrap/overflow semantics per spec §3)
  rolls,                           // u8[4]: damage-roll baseline [85..100], decomp formula
  lastHit,                         // { [battlerId]: { move, by } } — observer output
  lastUsed,                        // { [battlerId]: moveId|null } — Pokemon.lastMoveUsed
  abilities, heldItems,            // { [battlerId]: id } — mirror caches (direct reads)
  dmg(src, def, moveId, {crit}),   // estimate wrapper: fresh ActiveMove, willCrit=false
                                   // unless {crit}; PRNG snapshot/restore around the call;
                                   // maps false/null → 0, undefined → 0, number → number
  eff(moveId, def),                // 0 | 0.5 | 1 | 2 | 4 via def.runEffectiveness(move)
  aiRand(n),                       // the AI's own LCG: integer in [0, n)
}
```

Design notes:

- Modules never talk to Showdown directly — only through `ctx` and the objects
  it points at (which *are* the live `Battle`/`Pokemon` objects; `ctx` is a
  convenience view, not a copy of state).
- **s8 semantics**: the decomp's `moveScore` is signed 8-bit. Routines that
  overflow in the decomp wrap; those that clamp (several `AddN`-style
  saturating paths per spec §3) clamp. The engine provides `s8(x)`
  (`((x + 128) % 256) - 128`) and each routine follows its spec entry.
- **Doubles** (`battle.battleType === 'doubles'`): `foeActive` becomes an array
  (both opponent actives, `^1`/`^2` slot mapping as in the decomp);
  `tag_strategy` is the only module that needs it and is a no-op in singles.
  The daemon's current format is singles; doubles support is present in the
  engine but not exercised by the tests.

### D. Engine phases (per p2 `|request|`, spec anchors in TRAINER_AI.md)

1. **Team preview** (`req.teamPreview`): reply `team 1 2 3 4 5 6` (identity
   order — the decomp has no team-preview concept; lead order is fixed by
   trainer data in the game anyway).
2. **Wait** (`req.wait`): reply nothing.
3. **Switch phase** (spec §4, `TrainerAI_ShouldSwitch`): the 9 ordered checks
   against the opponent active (Perish-Song last turn → Wonder Guard
   66% → only-ineffective 66%/50% → Absorb-family 67%-skip/50% → asleep +
   Natural Cure cascade 50% → has-Super-Effective-move 90% → heavily boosted
   `sum(≥+7)−6 ≥ 4` → immune+SE 50% (decomp comment says 33 — code wins, per
   spec) → resistant+SE 33% (comment 25 — code wins)), plus the
   never-switch list and, for forced switches (active fainted / `forceSwitch`),
   the two-stage post-KO picker (type-matchup + SE-move, then max non-crit
   damage, incl. the documented `u8` overflow quirk, first-living fallback).
   Switch slot chosen ⇒ reply `switch N` and stop (a switch is the turn's
   action, decomp behavior).
4. **Item phase** (spec §5, only if `aiItems` non-empty): the 4-pocket,
   first-match-wins category walk (Full Restore → HP restorers → status cures →
   stat boosters gated by `fakeOutTurnNumber - totalTurns >= 0` → Guard Spec)
   with the slot-gating (`aliveMons ≤ trainerItemCounts − i + 1`), slot-2
   partner never uses items, and the **Embargo/HealBlock** guard (checked via
   `pokemon.volatiles`). **Execution**: Showdown has no trainer-item action in
   its protocol, so the engine applies the item's effect directly through
   existing engine primitives at request time (battle is quiescent; the effect
   lands during the same turn the in-game item phase would): `battle.heal(n,
   target)` for HP items, `target.cureStatus()` for status cures,
   `battle.boost(…, target)` for boosters — then logs one protocol line
   (`|msg|… used item …`-style via `battle.add`) so transcripts show it.
   This is the only part of the AI that *writes* battle state outside the
   engine's own turn flow; it is isolated in one engine function, gated on
   `aiItems`, and fully testable. Effect values come from the decomp's item
   data (§B) — Potion = 20 HP, Full Restore = full HP + status cure, X-items =
   +1 stage; an item with no mappable primitive is a no-op (counted, not
   applied).
5. **Move scoring** (spec §3): per legal slot, `scores[i] = 100`;
   `rolls[i] = 100 − aiRand(16)` (the decomp `moveDamageRolls` baseline).
   Run the loaded modules in canonical flag order; a module may set
   `ctx.cancel`. Then **selection**: max score among legal moves; collect all
   max-tied, pick `aiRand(n)` of them (decomp `trainer_ai.c:316-338`); reply
   `move N` using the `|request|` move indexing (1-based over the request's
   `active.moves`). If no legal move remains, reply `default` (the engine
   resolves it, e.g. to Struggle).
6. Every phase: all damage estimates go through `ctx.dmg` (PRNG
   snapshot/restore + `willCrit:false`) and the AI dice never touch
   `battle.prng`.

### E. The 11 modules (work breakdown)

All logic is a direct port of the per-flag sections of TRAINER_AI.md §3 (which
records every routine's N values, jump targets, and decomp-comment-vs-code
mismatches). Source column = the spec section each module ports.

|#|Module (file)|Spec §3 section|Roughly what it scores|Size|
|---|---|---|---|---|
|1|`basic.js`|Basic|Core damage model: `dmg()` × effectiveness × damage-roll baseline; PP/accuracy/crit/OHKO awareness; the routine most other modules build on|~150–250 ln|
|2|`eval_attack.js`|Eval Attack|Attacker-side evaluation: boosts, stat-stage interactions, move-vs-move comparisons on the user's side|~40–80 ln|
|3|`expert.js`|Expert|**The big one — 133 routines**: abilities (both sides), held items, weather interplay, hazards, status-specific counters, entry-hazard and trap handling, item/ability-aware damage corrections|~800–1300 ln — **the schedule bottleneck; implement as one pass over the doc's routine list**|
|4|`setup_first_turn.js`|Setup First Turn|Turn-1 dance/set-up preference (Swords Dance & co.) with the documented turn-0 condition|~30–60 ln|
|5|`risky.js`|Risky|Hazard/stall/aggressive-behavior corrections per its section's checks|~80–150 ln|
|6|`prioritize_extremes.js`|Prioritize Extremes|Boost extreme (maximal) moves per its section|~30–60 ln|
|7|`baton_pass.js`|Baton Pass|Score Baton Pass when boosts are bankable (per its section's boost-sum conditions)|~40–80 ln|
|8|`tag_strategy.js`|Tag Strategy|Doubles-only partner targeting/coordination; no-op in singles|~60–100 ln|
|9|`check_hp.js`|Check HP|HP-aware corrections (low-HP caution, KO-able vs not) per its section|~50–100 ln|
|10|`weather.js`|Weather|Weather-move bonuses (Rain Dance follow-ups, Hydro Pump under rain, etc.) per its section|~40–80 ln|
|11|`harassment.js`|Harassment|Disruption preferences (status/spikes/tailwind-style harassment per its section)|~40–80 ln|

Shared helpers live in `engine.js` (score math, `s8()`, `dmg()`, `eff()`,
selection) — modules stay free of infrastructure.

### F. File layout

```
qwenwork/task3/
  PLAN.md                 ← this document
  README.md               (post-implementation: protocol, how to load, per-module status)
  ai/
    engine.js             decide(), phases B1-B6, module loader, selection
    observer.js           lastHitMove bookkeeping over protocol lines
    rand.js               the AI's LCG (decomp constants from spec §1)
    basic.js  eval_attack.js  expert.js  setup_first_turn.js  risky.js
    prioritize_extremes.js  baton_pass.js  tag_strategy.js
    check_hp.js  weather.js  harassment.js
  ai_test.js              smoke + regression harness (below)
qwenwork/task1/
  showdown_daemon.js      extended in place (ai/aiItems/aiDir on start; p2
                          request interception; header doc updated)
```

### G. Daemon diff sketch (task 1 file, extended in place)

- `start` handling: parse `ai` (string|array|`"full"` preset), `aiItems`,
  `aiDir`; resolve+require the modules **before** battle start (loud error on
  unknown ids); store `{ engine, observer }` on the battle record
  (`rec.ai = { … }`) alongside the existing `rec.streams`.
- **p2 reader loop**: when `rec.ai` is set and a line starts with
  `|request|`, parse the JSON, feed the observer (also feeds the omni stream's
  `|damage`/`|-hit` lines — one observer instance for the whole battle, updated
  from the omni reader so it sees both sides' hits), run
  `engine.decide(rec, req)` synchronously, write the choice to
  `rec.streams.p2`. Team preview and wait handled per phase list.
- **`request` op**: with `rec.ai` set, a `side:"p2"` request ⇒ `error` event
  (`"p2 is AI-controlled in this battle"`). p1 unaffected.
- No other behavior changes; no `ai` ⇒ byte-identical to today.

## Validation plan

`qwenwork/task3/ai_test.js` (new; the daemon is driven in-process like
`task1/battle_test.js` does with the sim, or as a child… no — **in-process**:
it requires the daemon's record-management functions; simplest is to speak the
same stdio protocol to a `require`d daemon module — see below) will contain:

0. **Daemon regression (playerxplayer)**: one battle, no `ai` field, both
   sides driven by the test's round-robin policy over the stdio protocol →
   battle completes, transcript shape matches a control run. Proves the
   extended daemon is backward-compatible.
0b. Implementation note: to do this without subprocesses the daemon is
   structured as `module.exports` (the stdio bootstrap runs only under
   `require.main === module`), so the test can drive it in-process or spawn
   nothing. If in-process driving proves clumsy, the test spawns the node
   process it already talks to — that is the *existing* daemon boundary, not
   an AI subprocess; the AI itself never spawns anything.
1. **Completeness × 13 runs**: each of the 11 modules alone + `["basic",
   "eval_attack","expert"]` + `["basic","weather"]` — fixed seeds, scripted p1
   opponent, assert: battle reaches `|win|`/`|tie|`, **zero** `|error|` lines
   attributable to p2, every p2 choice matches `/^(move [1-4]|switch [1-6]|team .*|default)$/`.
2. **Determinism × 2**: rerun two of the above with the same seed → byte-identical
   transcript (covers AI dice, estimates, and the PRNG-snapshot invariant).
3. **PRNG neutrality (unit)**: mid-battle, call `ctx.dmg` once, assert
   `battle.prng.rng.seed` is unchanged.
4. **Spot-checks** (fixed seeds, assertions on protocol lines):
   - *Forced switch*: KO the AI's lead with a scripted p1 → expect `|switch|`
     with the post-KO picker's choice (hand-computed from the bench).
   - *Voluntary switch*: opponent active with an ability/move profile that
     triggers one of the 9 §4 checks (e.g. a Water-type facing only
     ineffective Electric moves at the 90% roll — seed chosen so the roll
     hits) → expect `|switch|`.
   - *Item use*: `aiItems: ["full_restore"]`, AI active burned → expect the
     item-use log line and the cure to follow.
   - *Move choice per module*: one scenario each for basic (max-damage move
     wins), weather (rain + water move), setup_first_turn (turn-1 dance),
     check_hp (low-HP caution), harassment (status preferred) — expected
     choices computed by hand from the spec's N values; because the seed
     fixes the AI dice, the expected choice is a deterministic assertion.

## Risks & mitigations

|Risk|Mitigation|
|---|---|
|`expert.js` scale (133 routines) — the main schedule risk|One implementation pass over the spec's routine list (it is the checklist); per-routine unit spot-checks from §validation.4; a routine that proves impossible against the fork's API is documented as a deviation, not silently dropped|
|`lastHit` observer regex vs the fork's exact line grammar|Parse both `|damage` and `|-hit`; require `[of]` + `[from] move:`; fallback to `lastMoveUsed`; a malformed line is ignored, never fatal. Verified against actual transcripts during implementation|
|Item execution mutates state outside the turn flow|Isolated in one function; only `battle.heal` / `cureStatus` / `battle.boost` (existing engine primitives); gated on `aiItems` (default off); determinism test covers it|
|Request-timing assumption (battle quiescent at `|request|`)|Verified above (`choose()` gates advancement); the `|request|` JSON is the same snapshot the engine used — the AI never sees fresher/staler state than the request itself|
|Gen-4 physical/special fork gap|AI uses the engine's own path ⇒ internally consistent; gap documented (Non-goals / Background). No fork edits|
|Doubles: `tag_strategy` untestable in the current singles-only daemon|Engine handles the `^1/^2` mapping; module is a no-op in singles; flagged for a later doubles-format test|

## Out of scope / future

- HeartGold AI variant (spec §7); doubles-format battles; p1-as-AI; escape;
  trainer data (NARC items/flags) — all future tasks. The `ai` field's value
  space and the module interface are designed to stay stable when they land.
