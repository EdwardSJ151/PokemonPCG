# Execution Plan — `AGENT_PLAN.md` (Showdown Integration + Gen 4 Trainer AI Documentation)

Status: **PROPOSED — awaiting approval** (revised 2026-09-14: integration approach reworked per user feedback —
Showdown as imported library in a long-lived daemon, not per-battle subprocess, not JSON dump)
Author: omp agent

---

## 1. Goal, deliverables, constraints

Build the two artifacts specified in `AGENT_PLAN.md`. Nothing outside the output dirs except the
Showdown build artifacts the guidebook sanctions (the user has already run `node build`).

| # | Deliverable | Path |
|---|---|---|
| 1 | Integration layer — long-lived Node daemon importing Showdown as a library; JSON API for Java | `qwenwork/task1/showdown_daemon.js` |
| 2 | Proof-of-concept terminal battle (hardcoded teams, full Gen 4 battle) | `qwenwork/task1/battle_test.js` |
| 3 | Integration documentation | `qwenwork/task1/INTEGRATION.md` |
| 4 | Gen 4 Trainer AI reference (7 sections, Java-reimplementable) | `qwenwork/task2/TRAINER_AI.md` |

**Constraints**
- All battle mechanics come from the `pokemon-showdown/` clone. **Java never reimplements a
  mechanic** (damage, criticals, abilities, items, status, weather, PP, switches, faint/end rules).
  The only Java-side port in the whole project is the *trainer decision-making* (Task 2) — that is
  decomp logic, not Showdown's, and the guidebook explicitly requests it.
- Showdown is **imported as a library** by the Node side (`require` of the clone's built `dist/sim`) —
  never parsed, never copied. `Dex.forGen(4)` is the only data access path.
- Gen 4 ends at NatDex #493. Exp curves: use only if the Dex API exposes them; otherwise skip.
- Task 2: Platinum decomp primary; HG divergences as exceptions.
- Memory: one engine process for the whole game session; no per-battle process spawning.
- Write targets: `qwenwork/` (deliverables) and `omp/` (this plan) only.

**Success criteria** (the guidebook's reviewer checklist, verbatim intent)
- `battle_test.js` runs to completion with one command and prints readable turn-by-turn Gen 4 output.
- Spot-checkable mechanics in output: Earthquake super-effective vs Gyarados, Close Combat drops,
  Leftovers recovery, Dragon Dance boost (plus Intimidate and Sand Veil, §4.4).
- The daemon runs a real battle end-to-end from hand-fed JSON, and `INTEGRATION.md` lets a Java
  developer wire it up without reading Showdown source.
- `TRAINER_AI.md` scoring model, per-flag breakdown, and trainer flag assignments match the decomp.

**Non-goals**
- No Java code (deferred to a later phase).
- No custom battle logic anywhere; no JSON data dump for Java (the simulator's existing decomp-based
  `pokemon_data.json` pipeline, spec `simulator/07_battle_system.md`, is untouched and covers
  non-battle static data: catch rate, EVs, exp tables).
- No changes to the decomp checkouts or `simulator/` specs.
- No network services; IPC is local stdio only.

---

## 2. Research summary — verified facts & resolved open questions

| Question | Answer | Evidence |
|---|---|---|
| Is the Showdown clone built? | **Yes — user ran `node build`** (2026-09-14). Local node v24.19.0 (≥22.18 required). First use of `dist/` in execution doubles as the sanity check; re-run only if a require fails. | user confirmation; `package.json` |
| What headless-battle API exists? | Two, both documented in `sim/SIMULATOR.md`: (a) in-process `new Sim.BattleStream()` — write protocol strings (`>start`, `>player`, `>p1 move 1`), read `update`/`sideupdate`/`end` messages; (b) raw stdio via `./pokemon-showdown simulate-battle`. **The "separate subprocess per battle" line in SIMULATOR.md constrains only the raw-stdio transport, not the engine** — `BattleStream` is a plain object; Showdown's own mocha suite runs hundreds of battles in one process through this API. | `sim/SIMULATOR.md` (165 lines, read); `tools/simulate/runner.ts` creates streams per run in-process |
| Is there a published "library form" precedent? | Yes — `@pkmn/sim` (the published `sim/` dir) is used exactly as a library: `import { Dex, BattleStreams, ... }`. Our clone is the same codebase; the guidebook makes the local checkout the ground truth, so we `require` its `dist/` (its `package.json` `"main": "dist/sim/index.js"` — literally how a library consumer loads it) instead of installing the published package. | npm `@pkmn/sim` README; clone `package.json` |
| `Dex.simulateBattle` (named in the guidebook)? | Not present in this version (zero grep hits). The canonical current API is `BattleStream` — same intent ("direct dependency, import from it"). | grep `sim/` |
| Gen 4 mod patches present? | **Yes** — `data/mods/gen4/{pokedex,moves,abilities,items,scripts,conditions,rulesets,formats-data}.ts`. | glob |
| Which Gen 4 format id? | **Unresolved statically** — `gen4customgame`/`gen4randombattle`/`gen4ou` don't appear in `config/formats.ts`. Resolved at T1.2: grep `gen4` in `config/formats.ts` and `test/sim/` (tests must instantiate Gen 4 battles; mirror whatever they do); fallback = minimal inline ruleset, documented in INTEGRATION.md. | grep results |
| Are the guidebook's team sets Gen 4 legal? | 5 of 6 abilities fine. **Exception: Garchomp** — Gen 4 ability is **Sand Veil**; *Rough Skin* is Gen 5+, absent from the Gen 4 dex. Deviation: Sand Veil (documented; bonus — gives a visible accuracy-drop mechanic vs Gengar's Shadow Ball). | Gen 4 ability data; `Dex.forGen(4)` |
| Platinum AI file sizes? | **Exactly as guidebook**: `trainer_ai.c` 4199, `script.s` 8105, HG `overlay_12_0224E4FC.c` 6719, HG `trainer_ai.c` 67. | `wc -l` |
| How many `AICmd_*` commands? | **109 declared** (107 real + `Dummy3E`/`Dummy3F`) — guidebook's "~80" underestimates. Forward-declaration list `trainer_ai.c:71–179`; bodies 528–2500; opcodes in `include/data/scripts/aicmd.h`; DSL macros `asm/macros/aicmd.inc`. | grep of `trainer_ai.c` |
| AI flag constants (`generated/ai_flags.h`)? | **Missing from this checkout** (the headers `trainer_ai.h` includes don't exist anywhere under `pokeplatinum/`). Bit values recovered three ways and cross-checked: (a) flag table at top of `script.s`, (b) `aiFlags` field in `generated/trainers.txt`, (c) HG `include/constants/trainers.h` (`AI_29`, `AI_DOUBLES`, …). Gap footnoted in TRAINER_AI.md. | globs; read of `include/constants/battle/trainer_ai.h` |
| Core AI constants available? | **Yes** — `include/constants/battle/trainer_ai.h`: init-score bitmask bits, `AI_STATUS_FLAG_{DONE,ESCAPE,SAFARI,BREAK,CONTINUE}`, battler roles (`DEFENDER/ATTACKER/±PARTNER`), `AI_MAX_STACK_SIZE 8`, comparison modes, `USE_MAX_DAMAGE/ROLL_FOR_DAMAGE`, `CHECK_DISABLE/ENCORE`. | read in full |
| Context structs (Section 1)? | `pokeplatinum/include/battle/ai_context.h` (AI_CONTEXT) + `trainer_ai.h` (TrainerAIData). HG: `include/battle/trainer_ai.h` + `battle_context.h` (fields the 67-line wrapper touches: `movePoints`, `unk18`, `unk10`, `unk98`, `aiFlags`, battler ids). | glob |
| Trainer records with `aiFlags` (Section 6)? | Platinum: **text tables** `generated/trainers.txt`, `trainer_classes.txt` (+ `item_ai_categories.txt`, `item_battle_categories.txt`, `item_hold_effects.txt` for Section 5) — no binary parsing. HG: `include/constants/trainers.h` + `trainer_class.h`. | glob |
| HG AI entry behavior (wrapper, 67 lines, read in full)? | Zeroes `TrainerAIData`; `movePoints[i]=100` (0 if invalidated); Struggle zeroes; random `100-(rand()%16)` per move; `aiFlags = AI_29` for roamers else trainer's stored value; ORs `AI_DOUBLES` in doubles; dispatches singles vs doubles mains. | read in full |
| In-JVM embedding viable? | **Rejected.** Trireme (Apigee) and J2V8 are stale/unmaintained; GraalVM's Node runtime docs state Node *cannot* be embedded in-process (separate process only). True same-process calls would couple engine crashes to the whole game. The daemon boundary is the boring, correct line. | web research (2026-09-14) |

**Guidebook discrepancies (source of truth = decomp/deps; noted in the docs):**
1. "~80 commands" → actually 109 (107 functional + 2 dummies), all documented.
2. `generated/ai_flags.h` absent from the Platinum checkout → three-source recovery, footnoted.
3. Gen 4 format id unconfirmed in `config/formats.ts` → T1.2 resolves at runtime.
4. Garchomp "Rough Skin" not Gen 4 legal → Sand Veil.
5. The guidebook's two integration options (per-battle subprocess / JSON dump) are superseded by the
   daemon design (§3 D1) — both were rejected on the user's criteria (live-game fit, memory, no reimplementation).

---

## 3. Consequential decisions

**D1 — Integration: Showdown as an imported library inside a long-lived Node daemon.**
Neither guidebook option fits a live game:
- *Per-battle subprocess* — pays Node startup (hundreds of ms) + a full engine/data load on every
  battle, N processes over a game session. That "one subprocess per battle" is a raw-stdio-protocol
  property, not an engine property; in-process the engine happily hosts many battles in one process.
- *JSON dump* — duplicates the data store in Java and forces Java to reimplement every mechanic —
  violates the primary constraint outright.

Chosen: **one daemon process per game session** (`showdown_daemon.js`) that **imports the built clone
as a library** (`require` of `dist/sim/index.js` — the package's own `main`; import, not copy), hosts
battles as in-process `BattleStream` objects **addressed by id** (so any number can run concurrently),
and speaks **line-delimited JSON over stdin/stdout** — the language-server pattern: one long-lived
process, structured requests in, structured events out, zero per-use spawn cost.

The daemon is also a **protocol adapter**: it parses Showdown's raw wire protocol (`|request` choice
requests, `|update`/`|sideupdate` messages, `|end` + winner) into clean JSON events, so **Java never
sees the Showdown protocol and never implements a mechanic** — it submits teams/choices and consumes
results.

**Memory/speed:** one process baseline per session; the Dex data (pokedex/moves/mods — a few MB as
parsed JS objects) loads once, each active battle is just objects. Turn-based traffic is a handful of
lines per turn over a localhost pipe (sub-ms) — human decision time dominates by ~3 orders of
magnitude. Measured at execution if ever questioned; no per-battle cost by construction.

**Rejected alternatives (recorded):** in-JVM Node embedding (Trireme/J2V8/GraalVM — stale, version-
fragile, crash-coupling, and GraalVM says Node can't embed in-process anyway); raw `simulate-battle`
stdio one-shot (the guidebook's subprocess option, as documented — works, wrong cost profile);
published `@pkmn/sim` npm package (same code, but the guidebook designates the local clone as ground
truth, and it's already built).

**D2 — Files and roles.**
- `showdown_daemon.js` — the integration point Java consumes (persistent; the only thing Java spawns).
- `battle_test.js` — one-shot demo/harness, in-process `BattleStream`, hardcoded teams; validates the
  engine + teams before any daemon code is trusted. Not the integration; kept self-contained per the
  guidebook.
- `INTEGRATION.md` — the contract: commands, protocol schema, lifecycle, Java wiring, deviations.

**D3 — Determinism.** Fixed 4-number PRNG seed in `>start`; all six sets at level 50 with explicit
natures/IVs in team-builder strings (omitted fields would randomize). Move policy: each side cycles its
four moves in order; on own faint, switch to the next available slot. Same seed → same battle.

**D4 — Task 2 source hierarchy.** Platinum (script-driven, annotated) primary; HG (67-line wrapper +
6719-line monolithic overlay) the comparison target. Where they disagree, Platinum is the spec and the
HG difference is recorded as an exception.

**D5 — Parallelism.** Tasks 1 and 2 share no files (separate output dirs; decomp/clones read-only) →
two parallel subagents; Task 1's units strictly sequential, Task 2's ordered per §5.

---

## 4. Task 1 work units (sequential; all output in `qwenwork/task1/`)

### T1.1 — Build — **DONE** (user ran `node build` in `pokemon-showdown/`, 2026-09-14)
No action. The first `require` of `dist/sim` in T1.3/T1.5 doubles as the sanity check; if a require
fails, re-run `node build` and report.

### T1.2 — Pin the Gen 4 format id
- Grep `gen4` in `config/formats.ts` and `test/sim/`; pick the customgame-style id if registered
  (right vehicle for hardcoded teams). If none is registered, mirror however `test/sim/` Gen 4 tests
  construct their `Battle` (inline ruleset if that's what they do). Record the exact id in
  INTEGRATION.md.
- **Accept:** one verified `formatid` that accepts the 6-Pokémon team and runs the
  `data/mods/gen4/` script set — a real Gen 4 battle, not gen5+.

### T1.3 — `battle_test.js`
- In-process: `require('../../pokemon-showdown')` (→ `dist/sim/index.js`), `new BattleStream()`,
  exactly the `SIMULATOR.md` pattern; use `Teams`/team-builder strings for the six sets:
  - P1: Garchomp (Sand Veil, Salac Berry) — Dragon Claw/Earthquake/Swords Dance/Fire Fang;
    Gengar (Levitate, Choice Specs) — Shadow Ball/Thunderbolt/Focus Blast/Sludge Bomb;
    Blissey (Natural Cure, Leftovers) — Softboiled/Seismic Toss/Thunder Wave/Stealth Rock.
  - P2: Lucario (Steadfast, Life Orb) — Close Combat/Shadow Ball/Extreme Speed/Swords Dance;
    Gyarados (Intimidate, Lum Berry) — Waterfall/Ice Fang/Dragon Dance/Earthquake;
    Rotom (Levitate, Choice Scarf) — Shadow Ball/Thunderbolt/Will-O-Wisp/Trick.
- Start with `{formatid: <T1.2>, seed: [a,b,c,d]}`; choices per D3; print every protocol message
  received, lightly prettified (turn headers, move/damage/HP/status lines) to stdout.
- **Accept:** `node qwenwork/task1/battle_test.js` → full battle to `end` with a winner.

### T1.4 — Mechanics verification pass
Grep the captured output for: (1) Earthquake "It's super effective!" vs Gyarados; (2) Close Combat →
Defense fell; (3) Blissey Leftovers recovery across turns; (4) Dragon Dance → Gyarados Attack/Speed
raised; (5) bonus: Gengar's Shadow Ball at reduced accuracy vs Garchomp's Sand Veil.
**Accept:** all four mandatory checks present; if one never fires, adjust the move-cycle order only
(teams unchanged) until it does.

### T1.5 — `showdown_daemon.js` (the Java-facing integration point)
- Long-lived process; `require`s the clone's `dist/sim`; hosts battles as in-process `BattleStream`
  objects keyed by a client-chosen id. Line-delimited JSON, one object per line (no embedded
  newlines — the daemon guarantees framing).
- **In:**
  - `{"op":"start","id":"b1","format":"<T1.2 id>","seed":[…],"p1":{"name":…,"team":…},"p2":{…}}`
  - `{"op":"choice","id":"b1","player":"p1","kind":"move"|"switch"|"default","slot":n}`
  - `{"op":"cancel","id":"b1"}` — discard a battle's state
- **Out:**
  - `{"event":"log","id":"…","line":"…"}` — each raw protocol line (so replays/debug stay available)
  - `{"event":"choice","id":"…","player":"p1","moves":[{slot,name,pp}…],"switch":[…],"default":true}` —
    parsed from `|request`; this is what Java/the trainer AI consumes
  - `{"event":"end","id":"…","winner":"p1","turns":12}`
  - `{"event":"error","id":"…"?,"message":"…"}`; process exits non-zero on unrecoverable engine crash
- **Accept (smoke test):** drive it from a shell with hand-typed JSON — start a battle, send a few
  choices, receive choice events and battle end — including two concurrent battle ids, proving the
  multi-battle-in-one-process design.

### T1.6 — `INTEGRATION.md`
1. **Architecture & why** — Showdown imported as a library inside one long-lived daemon; Java =
   decision-maker (player input + ported trainer AI) and protocol client, never mechanic owner.
   One paragraph each rejecting per-battle subprocess (startup + data reload per battle, N processes)
   and JSON dump (data duplication + forced reimplementation) and in-JVM embedding (stale bindings,
   crash coupling, and Node can't embed in a JVM process per GraalVM).
2. **Setup** — `node build` (already run; re-run only if `dist/` disappears); node ≥ 22.18.
3. **Protocol** — full request/event schemas from T1.5 + the underlying Showdown protocol reference
   (`sim/SIMULATOR.md`, `SIM-PROTOCOL.md`, `TEAMS.md`) + team-builder string format.
4. **Java integration** — `ProcessBuilder` spawns the daemon **once at game start**, kills at game
   end, respawns on unexpected exit (a battle then restarts from the saved party state — document
   that recovery); reader/writer threads over stdin/stdout; timeout policy (choice events arrive
   after each submitted choice; N s of silence → treat as crash); optional
   `node --max-old-space-size=` for the memory ceiling; minimal `ShowdownClient.java` interface sketch
   (signatures only — Java implementation is out of scope).
5. **Deviations** — Garchomp Sand Veil; level/nature policy; seed format; format id resolved in T1.2;
   exp curves skipped if the Dex API doesn't expose them (verified at T1.2).
- **Accept:** a developer who has never seen this repo runs both scripts and wires the daemon from
  this document alone.

---

## 5. Task 2 work units (ordered; single output `qwenwork/task2/TRAINER_AI.md`)

One author, one file; research→write passes in dependency order. Every factual claim carries a
`file:line` citation for spot-checking.

### T2.1 — Section 1: Architecture
- `TrainerAIData` struct verbatim (`pokeplatinum/include/battle/trainer_ai.h`) with a field table,
  mapped to the HG wrapper's names (`movePoints`, `unk18` random array, `unk10`, `unk98`, `aiFlags`,
  battler ids) where the two align.
- AI_CONTEXT layout from `include/battle/ai_context.h` (`calcTemp`, `stateFlags`, 8-deep stack).
- **The scoring loop, exactly as `trainer_ai.c`:** (1) init — 100 per usable move, 0 for invalidated
  (no PP/Struggle/Disable/Encore); (2) randomize — per-move `100 - rand()%16`; (3) script pass — the
  flag table at the top of `script.s` maps each set bit to its labeled block, executed via the opcode
  dispatch table (function-pointer array in `trainer_ai.c`; opcodes from `aicmd.h`); (4) select — max
  final score, random tie-break.
- **Basic vs Expert** defined by flag sets, cross-referenced to Section 6's real assignments.

### T2.2 — Section 2: Script command reference
- Table of **all 109** `AICmd_*` (guidebook said ~80; the `trainer_ai.c:71–179` declaration block is
  authoritative). Per row: opcode (`aicmd.h`), name, one-line behavior (from the body), side
  (attacker/defender/partner/both — from which battler indices the body reads). Grouped by family:
  random, HP%, status, move-effect, side-condition, loaded comparisons, table lookups,
  type/effectiveness, party-member, doubles, control flow (`PushAndGoTo`/`GoTo`/`PopOrEnd`),
  escape, dummies.

### T2.3 — Section 3: Per-flag breakdown (the core of the doc)
- Each of the 11 labeled blocks in `script.s` read **in full**; per flag a condition→delta table:
  every `AddToMoveScore`/`SubFromMoveScore` with its **exact constant**, its gate (HP%, status, type,
  stat stage, item, weather, turn number), and which move classes/effects are favored/penalized;
  note `USE_MAX_DAMAGE` vs `ROLL_FOR_DAMAGE` where the block rolls damage.
- Must let someone rebuild the script engine's behavior in Java without seeing `script.s`.

### T2.4 — Section 4: Switching
- Platinum: the party-member commands (`IfPartyMemberStatus`, `IfAnyPartyMemberIsWounded`,
  `IfAnyPartyMemberUsedPP`, `IfPartyMemberDealsMoreDamage`) + the `AI_STATUS_FLAG_*` transitions into
  switch intent. HG: `ShouldSwitch` in `overlay_12_0224E4FC.c`.
- Document: when switching is considered, bench scoring, and the switch-vs-fight decision rule.

### T2.5 — Section 5: Item use
- Same decision blocks as T2.4 + `generated/item_ai_categories.txt`, `item_battle_categories.txt`,
  `item_hold_effects.txt`. Document: AI item categories, HP thresholds, priority between competing
  items. (Held-item *effects* on damage are Section 1–3 inputs; this section covers the use/switch
  decision only.)

### T2.6 — Section 6: Trainer flag assignments
- Parse `generated/trainers.txt` + `trainer_classes.txt` for `aiFlags`; bit values via the
  §2 three-source recovery (the missing `ai_flags.h` footnoted). Output: class→flags table with
  counts and named examples (gym leaders, E4, randoms, roamers `AI_29`) + the distribution pattern
  in prose. HG comparison column where data allows.

### T2.7 — Section 7: HG vs Platinum
- Structural: monolithic C (6719-line overlay behind the 67-line wrapper) vs script-driven
  (4199-line interpreter + 8105-line DSL). Functional: compare init (same shape, verified), flag
  space, doubles (`AI_DOUBLES` in both), item/switch logic; state equivalences and divergences
  plainly — no invented equivalence where Platinum's behavior can't be confirmed.

### T2.8 — Cross-check pass
- Reconcile against the guidebook's tables; record residual discrepancies (expected: 109 vs ~80,
  flag-bit provenance). Self-audit: each section answers its guidebook bullet; citations
  spot-checked; "reimplementable in Java without the decomp" cold-read test.

---

## 6. Verification (acceptance = the guidebook's reviewer checklist)

| Check | How |
|---|---|
| battle_test runs in one command | `node qwenwork/task1/battle_test.js`; exit 0; `end` with winner |
| Readable Gen 4 output | Visual read of printed battle |
| EQ super-effective / Close Combat drops / Leftovers / Dragon Dance | grep the log (T1.4 list) |
| No custom battle logic | `battle_test.js` + daemon contain only team strings, choices, and printing/forwarding |
| Daemon protocol works | T1.5 shell smoke test, incl. two concurrent battle ids |
| Java can wire it up | INTEGRATION.md standalone-wiring test (follow it blind) |
| TRAINER_AI.md matches decomp | Reviewer spot-checks §1 loop, §3 deltas, §6 assignments against `trainer_ai.c`/`script.s`/`generated/trainers.txt` |

---

## 7. Risks & mitigations

1. **Daemon crash mid-battle** → Java detects the dead pipe, respawns, and restarts the battle from
   the saved party state (documented in INTEGRATION.md §4; acceptable because battles start from
   save state, not from an unserializable mid-fight engine state).
2. **Framing bugs** (a JSON line containing a newline) → daemon is the single serializer; all
   payloads are escaped by construction; smoke test covers it.
3. **Node memory ceiling** → optional `--max-old-space-size`; one process regardless of battle count;
   measure during execution if the user wants a number.
4. **Clone is a vendored dependency** → the daemon `require`s it by relative path; INTEGRATION.md
   pins the expected layout (`dist/sim/index.js`) and the re-build command. No published-package
   drift, per the guidebook's ground-truth rule.
5. **No Gen 4 format id in `config/formats.ts`** → T1.2 resolves from `test/sim/` usage or an inline
   ruleset; either way documented.
6. **Garchomp/Rough Skin not Gen 4 legal** → Sand Veil deviation, documented (adds a visible mechanic).
7. **`generated/ai_flags.h` missing** → three-source recovery, footnoted in TRAINER_AI.md.
8. **Guidebook's "~80 commands" stale** → all 109 documented; delta noted.
9. **HG overlay is 6719 lines of dense C** → T2.7 scopes to AI-relevant symbols (`trainerAI`,
   `ShouldSwitch`, `ShouldUseItem`, `movePoints`); no full-file treatment.

---

## 8. Execution order & parallelism

```
T1.1 done (user) ─► T1.2 → T1.3 → T1.4 → T1.5 → T1.6          (Task 1: sequential, one agent)
(decomp, read-only) ─► T2.1 → T2.2 → T2.3 → T2.4 → T2.5 → T2.6 → T2.7 → T2.8   (Task 2: one agent)
```

- Two parallel subagents; zero file overlap (Task 1 → `qwenwork/task1/`, Task 2 → `qwenwork/task2/`;
  clones read-only).
- After both report: the main session runs §6 from scratch (run the battle script, drive the daemon
  smoke test, spot-check the doc) and reports the evidence.
