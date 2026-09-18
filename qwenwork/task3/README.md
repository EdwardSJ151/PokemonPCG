# Task 3 — Gen 4 Platinum trainer AI for the Showdown daemon

Reimplements Platinum's trainer AI (`qwenwork/task2/TRAINER_AI.md` is the spec;
`pokeplatinum/` is the read-only decomp checkout the ports were verified
against) as **11 loadable flag modules** plus a shared engine, wired into
`qwenwork/task1/showdown_daemon.js` as p2 "AI mode". Runs on any checkout of
this repo — no hardcoded paths, no subprocesses, no build steps.

## Quick start

```sh
node qwenwork/task3/ai_test.js all          # every module + combos + determinism
node qwenwork/task3/daemon_test.js          # daemon AI mode + playerxplayer regression
node qwenwork/task1/showdown_daemon.js      # the daemon itself (stdio JSON protocol)
```

Daemon start request with AI (full protocol in `task1/INTEGRATION.md`):

```json
{"id":"b1","op":"start","team1":[…],"name1":"You","team2":[…],
 "ai":["basic","expert"], "aiItems":["full_restore","persimberry"], "aiDir":"optional/path"}
```

* `ai`: module id(s), `"full"` (= basic + eval_attack + expert, mirroring the
  Platinum trainer default), or `[]` (engine-only AI: switch/item logic, random
  legal moves). Unknown ids ⇒ `error` event, battle never starts.
* `aiItems`: the trainer's 4-slot bag, keyed by **decomp item ids** (the keys
  of `ai/data_items.js` — e.g. `persimberry`, not modern `persim`). Absent ⇒
  items never used.
* The daemon answers every p2 `|request|` itself and emits
  `{"type":"ai","id":…,"choice":…}` per decision; a `request` op for p2 is
  rejected. Without `ai`, the daemon is byte-identical to playerxplayer mode.

## Layout

| file | role |
|---|---|
| `ai/engine.js` | the `trainer_ai.c` counterpart: EvalMoves scoring, `MainSingles` pick, §4 switching, §5 items, module loader, `ctx` contract (documented in `buildCtx`) |
| `ai/observer.js` | decomp-side memory: entry turns, observed moves (≤4), last-hit tracking |
| `ai/rand.js` | the AI's private Gen 3 LCG (`AICpuLcg`), seeded once per battle, independent of the battle PRNG |
| `ai/data_moves.js`, `ai/data_items.js`, `ai/data_typechart.js` | static tables generated from the decomp (`gen_data.js`, dev-time only) |
| `ai/<flag>.js` | the 11 flag modules: `{ id, bit, decide(ctx) }` |
| `ai_test.js` | in-process harness: completeness, determinism, PRNG-neutrality; exports `runAI` for scenarios |
| `daemon_test.js` | daemon regression incl. AI mode, item use, fail-loud |
| `*_smoke.js`, `_probe_*.js` | scenario suites with hand-computed pins (see Testing) |

## Canonical flag order (engine runs modules in this order)

`basic(0) eval_attack(1) expert(2) setup_first_turn(3) risky(4)
prioritize_extremes(5) baton_pass(6) tag_strategy(7) check_hp(8) weather(9)
harassment(10)` — the decomp `EvalMoves` bit order; `ctx.cancel` (set only by
the op-61 `Escape` equivalent) stops later modules, mirroring `BREAK`.
"Terminate" in a flag script merely ends that move's script — later slots and
later flags still run; no module sets `ctx.cancel` today.

## Module status

| module | port | solo (`ai_test.js <id>`) | audited vs script.s/C |
|---|---|---|---|
| basic | ✅ 879 ln | ✅ | main path + immunity full (script.s:52-131); OHKO-scope fix applied (move-id jump); dispatch routines covered by 10-scenario smoke |
| eval_attack | ✅ | ✅ | ✅ full |
| expert | ✅ 1912 ln | ✅ | ✅ dispatch = script.s:1628-1805 order-identical (178 entries, first-match-wins, dead dup kept); 31 hand-computed pins |
| setup_first_turn | ✅ | ✅ | ✅ full |
| risky | ✅ | ✅ | ✅ full |
| prioritize_extremes | ✅ | ✅ | ✅ full (spec's "variable-power → NO_COMPARISON" prose contradicts code; code wins) |
| baton_pass | ✅ | ✅ | ✅ full |
| tag_strategy | ✅ | ✅ | ✅ decomp-literal — **not** a singles no-op: the zeroed partner slot makes EQ/Magnitude/Discharge/Surf −3, non-Surf Water −1, Trick Room −30 fire exactly as on-cartridge in singles (independently probed: EQ scores 97) |
| check_hp | ✅ | ✅ | ✅ tables/gates full |
| weather | ✅ | ✅ | ✅ full (fall-through quirk confirmed in script.s; gate aligned with the fixed observer) |
| harassment | ✅ | ✅ | ✅ full |

## Testing

```
node qwenwork/task1/battle_test.js        # fork mechanics sanity (6)
node qwenwork/task3/eff_smoke.js          # engine eff()/helpers (24)
node qwenwork/task3/smoke.js              # engine switch/item path pins
node qwenwork/task3/_probe_switch.js      # §4 switch sub-checks (6)
node qwenwork/task3/_probe_items.js       # §5 item chain (10)
node qwenwork/task3/switch_smoke.js       # voluntary-switch ROLL-SUCCESS (dice-queue injection)
node qwenwork/task3/basic_smoke.js        # basic scoring scenarios (exact per-slot scores)
node qwenwork/task3/small_b_smoke.js      # baton/tag/check_hp/weather/harassment scenarios (26 pins)
node qwenwork/task3/expert_smoke.js       # expert scoring scenarios (31 pins, dice pinned)
node qwenwork/task3/small_a_smoke.js      # eval_attack/setup/prioritize/risky hand-scored scenarios
node qwenwork/task3/ai_test.js <ids>      # completeness ×N + determinism ×2 + PRNG neutrality
node qwenwork/task3/daemon_test.js        # daemon: byte-compat, fail-loud, AI mode, item use
node qwenwork/task3/play.js [ids]         # interactive duel: cartridge-style HUD, arrow-key move grid (piped input falls back to numbered menus)
```

Notes: `|t:|` transcript lines carry real wall-clock seconds — every
byte-comparison strips them. `aiItems` and the engine bag use decomp ids
(`persimberry` cures **confusion** in gen 4 — decomp data beats modern memory).
The battle object for a running daemon battle is `rec.base.battle`
(`daemon.battles.get(id)`), useful for in-process scenario seeding.

## Deviations from the cartridge (deliberate, documented)

* The fork has no bag action: an item decision applies its effect directly
  (`|-item|` + heal/cure/boost) and the turn continues into move choice.
* Gen 4 physical/special split: the fork is authoritative for damage categories
  (e.g. it lacks fork-side quirks the decomp lists and vice versa).
* Fork lacks: fog weather (weather module keeps the arm, unreachable),
  Mist side condition (GUARD_SPEC consumes only), Shell Smash / Work Up move
  carriers (their effect-table rows are unobservable → omitted).
* Real abilities/held items are visible to the AI on both sides (the decomp
  hides them); damage estimates use the fork's `getDamage` (expected damage,
  PRNG restored — the AI never advances the battle seed; `ai_test.js` enforces
  this unit-style).
