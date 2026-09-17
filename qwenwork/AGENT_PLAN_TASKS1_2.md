# Agent Plan — Pokémon Data Integration & Trainer AI Documentation

## What we are building

A Pokémon simulator for a procedurally-generated game. The simulator reads static JSON
files produced by `terrain_to_ascii.py` — one file per map — that describe every map's
terrain, NPCs, trainers, encounters, warps, and items. The simulator plays the game
from those files: it runs battles, handles overworld movement, fires dialogues, tracks
save state, etc.

The mechanic specs for the simulator live in `simulator/` (12 numbered .md files). The
game data pipeline reads three retail decomps:

| Decomp | Path |
|---|---|
| Pokémon Emerald | `pokeemerald/` |
| Pokémon HeartGold | `pokeheartgold/` |
| Pokémon Platinum | `pokeplatinum/` |

There is a local clone of the pokemon-showdown battle simulator at:

```
pokemon-showdown/         ← full PS repo clone
  data/
    pokedex.ts            ← base stats, types, abilities (all gens, latest data)
    moves.ts              ← all moves, all gens
    abilities.ts          ← ability effects
    items.ts              ← item effects
    natures.ts            ← natures and stat multipliers
    learnsets.ts          ← per-species learnsets
    mods/gen4/            ← Gen 4 OVERRIDES (inherit: true patches on top of base data)
      moves.ts
      abilities.ts
      items.ts
      pokedex.ts
      scripts.ts          ← Gen 4 battle engine hooks (damage formula, mechanics)
  sim/
    dex.ts / dex-*.ts     ← Dex class that merges base + mod data
    battle.ts             ← core battle engine
    pokemon.ts            ← Pokémon object
    battle-actions.ts     ← action resolution
```

**All output from both tasks goes into the directory `qwenwork/` at the project root.**
Task 1 outputs go in `qwenwork/task1/`. Task 2 outputs go in `qwenwork/task2/`.
Do not write anything outside `qwenwork/`.

---

## Task 1 — Plug the simulator into pokemon-showdown

### Goal

The Java simulator needs Gen 4 battle mechanics (base stats, move data, damage formula,
ability effects, item effects, type chart, etc.). **Do not extract or rebuild any of this
from scratch.** Pokemon Showdown is the ground truth and must be used as a direct
dependency — import from it, don't copy its data.

The deliverable is two things:
1. A clear integration point: a script or module that the Java simulator calls into
   (or reads from) using Showdown as the backend for all Pokémon data and battle logic.
2. A working proof-of-concept terminal battle script that validates the integration.
   This is not the final Java simulator — it is a test harness to confirm everything
   is wired up correctly before any Java code is written.

### How showdown's data model works

Showdown's `data/` directory contains current-generation-canonical values. Older-gen
data lives in `data/mods/genN/` as incremental patches; each patch entry has
`inherit: true` plus only the fields that changed in that generation.

Showdown's `Dex` class applies the mod chain automatically:

```js
// Inside the pokemon-showdown/ directory, or with the path set correctly:
const { Dex } = require('./sim/dex');
const dex4 = Dex.forGen(4);   // Gen 4 Dex — applies all gen4 patches

const bulbasaur = dex4.species.get('bulbasaur');
// .baseStats, .types, .abilities, .learnset, etc. — all Gen 4 correct

const tackle = dex4.moves.get('tackle');
// .basePower, .accuracy, .pp, .type, .category (Physical/Special/Status), etc.

const overgrow = dex4.abilities.get('overgrow');
const leftovers = dex4.items.get('leftovers');
```

This is the ONLY way data should be accessed. Never parse the `.ts` files directly.

The build output (compiled JS) lives inside `pokemon-showdown/`. Check whether
`node build` has already been run (look for `.js` files in `sim/`). If not, run it.
`npx ts-node` is an alternative if the compiled output is missing.

Gen 4 ends at National Dex #493 (Arceus). Species #494+ did not exist in Gen 4.

### What showdown already has vs. what it might not

Showdown has everything needed for Gen 4 battle simulation:
- Species data (base stats, types, abilities per slot)
- Move data (power, accuracy, PP, type, category, priority, contact flag, etc.)
- Ability and item effects wired into the battle engine
- Type chart (accessible via `dex4.getEffectiveness(...)`)
- Natures
- The full Gen 4 battle engine in `sim/battle.ts` + `data/mods/gen4/scripts.ts`

Showdown may or may not expose exp curves through the Dex API. If `dex4` has no
exp-curve helper, skip exp curves entirely — do not build them from scratch.

### Deliverable 1 — Integration layer (`qwenwork/task1/`)

Produce a script (JavaScript or Python, whichever is cleaner) that:
- Starts a Showdown `Gen 4 Random Battle` or equivalent Gen 4 format battle
- Exposes the interface the Java simulator will call: given a battle state, return
  what move the AI/player should use; given a move and battle state, return the result

Document what the Java simulator needs to call and how. The integration approach
options are:
  - **Node subprocess**: Java spawns a Node process running a Showdown battle stream;
    JSON messages go back and forth over stdin/stdout (Showdown already has
    `battle-stream.ts` for this)
  - **Pre-serialized data**: use Showdown's Dex API to dump all Gen 4 data to JSON
    once at startup; Java reads the JSON and handles battle logic itself using
    Showdown as the data source only

Evaluate both and recommend one. The priority is correctness and simplicity.
If the subprocess approach works cleanly, that is strongly preferred because it means
the Java simulator never has to reimplement any battle mechanic — Showdown handles
all of it.

Produce a `qwenwork/task1/INTEGRATION.md` documenting:
- Which approach was chosen and why
- Exact commands to start the integration layer
- The message protocol (if subprocess) or the data schema (if JSON dump)
- How to add the integration to a Java project (dependency, classpath, etc.)

### Deliverable 2 — Terminal battle proof-of-concept (`qwenwork/task1/battle_test.*`)

Write a single self-contained script (`.js`, `.ts`, or `.py`) that:
- Imports / requires from `pokemon-showdown/` directly (no copy, no extraction)
- Hardcodes two teams, each with 3 Pokémon, items, abilities, and 4 moves
- Runs a full Gen 4 single battle in the terminal, printing each turn's actions and
  results to stdout
- Uses Showdown's own damage calculation and mechanics — not a custom implementation

The teams should be hardcoded to something concrete so the output is deterministic
enough to read, for example:

```
Team 1: Garchomp (Rough Skin, Salac Berry) — Dragon Claw / Earthquake / Swords Dance / Fire Fang
        Gengar (Levitate, Choice Specs)    — Shadow Ball / Thunderbolt / Focus Blast / Sludge Bomb
        Blissey (Natural Cure, Leftovers)  — Softboiled / Seismic Toss / Thunder Wave / Stealth Rock

Team 2: Lucario (Steadfast, Life Orb)     — Close Combat / Shadow Ball / Extreme Speed / Swords Dance
        Gyarados (Intimidate, Lum Berry)   — Waterfall / Ice Fang / Dragon Dance / Earthquake
        Rotom (Levitate, Choice Scarf)     — Shadow Ball / Thunderbolt / Will-O-Wisp / Trick
```

The script should run with a single command and print readable turn-by-turn output.
It does not need a UI. Terminal text is sufficient.

This script proves that the Showdown integration is working before any Java code
is written. If it runs and produces sensible battle output, Task 1 is complete.

---

## Task 2 — Document the Gen 4 Trainer AI

### Goal

Understand and document how trainers decide which move to use in battle in Gen 4.
We will port this logic directly to the Java simulator. Do not approximate or invent —
read the actual source, understand the mechanism, and write it down precisely enough
that a developer can reimplement it in Java without referring back to the decomp.

All output goes to `qwenwork/task2/TRAINER_AI.md`.

### Primary source: Platinum decomp

The most legible Gen 4 AI implementation is in Platinum:

```
pokeplatinum/src/battle/trainer_ai/
  trainer_ai.c      ← 4199 lines: the core AI engine (move scoring, script runner)
  script.s          ← 8105 lines: the AI decision scripts (11 named AI flags)
```

The AI flags table is at the top of `script.s`. The 11 active flags are:

| Bit | Flag name | Brief purpose |
|---|---|---|
| 0 | `AI_FLAG_BASIC` | Damage-based move scoring |
| 1 | `AI_FLAG_EVAL_ATTACK` | Evaluate type effectiveness and secondary effects |
| 2 | `AI_FLAG_EXPERT` | Advanced checks (abilities, held items, hazards) |
| 3 | `AI_FLAG_SETUP_FIRST_TURN` | Prioritize setup moves on the first turn |
| 4 | `AI_FLAG_RISKY` | Allow risky high-variance plays |
| 5 | `AI_FLAG_PRIORITIZE_EXTREMES` | Prefer moves with extreme scores (min/max) |
| 6 | `AI_FLAG_BATON_PASS` | Baton Pass strategy awareness |
| 7 | `AI_FLAG_TAG_STRATEGY` | Tag battle partner coordination |
| 8 | `AI_FLAG_CHECK_HP` | HP-dependent score modifiers |
| 9 | `AI_FLAG_WEATHER` | Weather-based score modifiers |
| 10 | `AI_FLAG_HARRASSMENT` | Harassment / annoying moves (disable, encore) |

The `aiFlags` bitmask on a trainer determines which flags are active. Each flag runs
its own section of `script.s` and modifies per-move scores. The final action is the
move with the highest score (with a small random tie-break).

### AI engine mechanics (from `trainer_ai.c` / `overlay_12_0224E4FC.c` in HG)

The scoring loop:

1. **Init**: all 4 move slots start at score 100. Moves the AI cannot use (out of PP,
   blocked by disable/encore/etc.) are set to 0 and skipped.
2. **Randomize**: each move gets a per-iteration random bonus in [-15, 0] (`100 - rand(16)`).
3. **Script pass**: for each active AI flag, the corresponding script section runs and
   modifies scores via `AddToMoveScore`, `SubFromMoveScore`, etc.
4. **Select**: move with the highest final score is chosen. If multiple moves tie, one
   is chosen randomly.

The AI script commands are defined in `trainer_ai.c` as `AICmd_*` functions (there are
~80 commands covering: random checks, HP percent comparisons, type checks, status checks,
stat stage checks, ability/item checks, move-effect checks, damage estimate comparisons,
partner coordination for doubles, etc.).

### What to document

Write all documentation to `qwenwork/task2/TRAINER_AI.md`. The document must be
complete enough to reimplement the AI in Java without referring back to the source.

**Section 1 — Architecture**
- The scoring loop in full (init, randomize, script pass, select)
- How `aiFlags` maps to script sections
- How the AI context struct (`AI_CONTEXT`, `trainerAIData`) is laid out
- The difference between Basic AI (most trainers) and Expert AI (gym leaders, E4)

**Section 2 — Script command reference**
- For every `AICmd_*` function in `trainer_ai.c` (or the equivalent in the HG overlay):
  a one-line description of what it checks and what side it acts on
  (attacker / defender / both)
- Include the opcode value where identifiable

**Section 3 — Per-flag breakdown**
- For each of the 11 active flags, describe exactly what it does: which moves it favors,
  which moves it penalizes, under what conditions, and by how much
- Read the relevant block in `script.s` in full. Each flag is a named labeled block.

**Section 4 — Switching logic**
- Trainer switching is handled separately from move selection (look for `ShouldSwitch`
  or similar in `overlay_12_0224E4FC.c` / `trainer_ai.c`)
- Document: when the AI considers switching, how it scores each benched Pokémon,
  and when it decides to switch vs. move

**Section 5 — Item use**
- Item use is handled in the switch/item decision block, not the move scoring block
- Document the full decision logic from source (HP thresholds, item categories,
  priority order)
- Key file for HG: inside `overlay_12_0224E4FC.c` (search `ShouldUseItem` or
  `trainerItems`). For Platinum: `trainer_ai.c` around the same keyword.

**Section 6 — Trainer AI flag assignments**
- Which real trainers use which combination of flags?
- Look at trainer data tables in the decomp. In Platinum: check
  `pokeplatinum/res/battle/trainer/` or `pokeplatinum/generated/` for trainer records
  with `aiFlags` fields. In HG: `pokeheartgold/data/trainers/` or similar JSON/binary.
- Document the pattern: "all gym leaders use flags 0+1+2+3", "wild Pokémon use flag 29
  only", "most random trainers use flag 0 only", etc.

**Section 7 — Differences between HG and Platinum AI**
- HG AI lives in `pokeheartgold/src/battle/trainer_ai.c` (67 lines, thin wrapper) and
  the bulk is in `pokeheartgold/src/battle/overlay_12_0224E4FC.c`
- Platinum AI lives in `pokeplatinum/src/battle/trainer_ai/trainer_ai.c` (4199 lines)
  and `pokeplatinum/src/battle/trainer_ai/script.s` (8105 lines — script-driven DSL)
- Platinum's AI is script-driven; HG's is monolithic C
- Document whether they are functionally equivalent or where they diverge

### How to read the decomp

**Platinum** — the most readable Gen 4 decomp:
- Headers: `pokeplatinum/include/`
- AI constants: `pokeplatinum/include/constants/battle/trainer_ai.h` and
  `pokeplatinum/generated/ai_flags.h`
- Source: `pokeplatinum/src/`
- The AI script DSL macros: search for `.macro` inside
  `pokeplatinum/src/battle/trainer_ai/` or check `pokeplatinum/asm/macros/aicmd.inc`

**HeartGold** — older decomp style, more C-heavy:
- Source: `pokeheartgold/src/`
- AI entry point: `pokeheartgold/src/battle/trainer_ai.c` (67 lines, calls overlay)
- AI bulk: `pokeheartgold/src/battle/overlay_12_0224E4FC.c` (search `trainerAI`,
  `ShouldSwitch`, `ShouldUseItem`, `movePoints`)
- Constants: `pokeheartgold/include/constants/`
- Trainer message types: `pokeheartgold/include/constants/trainers.h`

**Emerald** (Gen 3, for comparison only if needed):
- `pokeemerald/src/battle_ai_script_commands.c`
- `pokeemerald/src/battle_ai_switch_items.c`
- Gen 3 AI is simpler; reference only if something in Gen 4 is unclear

### Important note on HG vs Platinum authority

The simulator targets Platinum and HeartGold/SoulSilver. Platinum's decomp is better
annotated — use it as the primary source. Document HG divergences as exceptions.

---

## Output checklist

All output lives under `qwenwork/`. Nothing goes elsewhere.

**Task 1 — `qwenwork/task1/`**
- [ ] `INTEGRATION.md` — approach decision, protocol/schema, Java integration guide
- [ ] `battle_test.js` (or `.ts` or `.py`) — single script, hardcoded teams, runs a
  full Gen 4 battle in the terminal via Showdown, no custom battle logic
- [ ] Script runs to completion with a single command (document the command in `INTEGRATION.md`)

**Task 2 — `qwenwork/task2/`**
- [ ] `TRAINER_AI.md` — all 7 sections, complete enough to reimplement in Java

The agent reviewing this work will:
- Run the `battle_test` script and verify it produces readable Gen 4 battle output
- Spot-check mechanics (e.g. Earthquake super-effective vs. Gyarados, Close Combat
  stat drops, Leftovers recovery, Dragon Dance boost)
- Read `TRAINER_AI.md` and verify the scoring model, flag breakdown, and AI flag
  assignments match the decomp source
