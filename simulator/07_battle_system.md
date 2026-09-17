# Battle System

---

## Authoritative sources

**Use these repos directly — do not reconstruct mechanics from memory or Bulbapedia.**

| Source | Path on disk | What to use it for |
|---|---|---|
| Pokémon Showdown | `pokemon-showdown/` | Ability implementations, move data (BP/type/category/accuracy/PP/effects), type chart, learnsets, nature modifiers |
| Showdown damage calculator | `damage-calc/` | Gen 4 damage formula (exact implementation: `calc/src/mechanics/gen4.ts`), stat calculation formulas (`calc/src/stats.ts`), type chart per gen (`calc/src/data/types.ts`) |
| HG decomp `personal.json` | `pokeheartgold/files/poketool/personal/personal.json` | Catch rate, exp yield, EV yields, growth rate, held items, abilities, TM/HM learnsets per species |
| HG decomp `growtbl.csv` | `pokeheartgold/files/poketool/personal/growtbl.csv` | Experience threshold for every level under every growth rate |
| Showdown `data/moves.ts` | `pokemon-showdown/data/moves.ts` | 21 k-line file; every move's complete data |
| Showdown `data/abilities.ts` | `pokemon-showdown/data/abilities.ts` | 5 k-line file; every ability's complete effect callbacks |
| Showdown `data/learnsets.ts` | `pokemon-showdown/data/learnsets.ts` | Per-species, per-gen learnsets |
| Showdown `data/pokedex.ts` | `pokemon-showdown/data/pokedex.ts` | Base stats, types, evolution chains, abilities per species |

The Showdown battle simulator (`pokemon-showdown/sim/`) is a complete, battle-tested
Gen 4 engine in TypeScript. Read it when there is any doubt about how a mechanic works.
The damage calculator's `gen4.ts` is the best reference for the exact damage formula and
modifier ordering.

---

## Static Pokémon data — `tools/export_pokemon_data.py`

After exporting the map `.txt`/`.json` pairs with the terrain renderer, run a one-time
extraction to produce static JSON that the Java simulator can load at runtime without
touching any decomp file.

### Output file: `game_folder/pokemon_data.json`

One JSON object keyed by National Dex number (string "1"–"493"):

```json
{
  "1": {
    "num": 1,
    "name": "Bulbasaur",
    "types": ["Grass", "Poison"],
    "baseStats": { "hp": 45, "atk": 49, "def": 49, "spa": 65, "spd": 65, "spe": 45 },
    "abilities": ["Overgrow"],
    "hiddenAbility": null,
    "catchRate": 45,
    "expYield": 64,
    "growthRate": "MEDIUM_SLOW",
    "evYield": { "hp": 0, "atk": 0, "def": 0, "spa": 1, "spd": 0, "spe": 0 },
    "heldItems": [],
    "levelUpMoves": [
      { "level": 1,  "move": "Tackle" },
      { "level": 1,  "move": "Growl" },
      { "level": 3,  "move": "Vine Whip" },
      ...
    ],
    "evolvesInto": { "species": 2, "condition": "level", "level": 16 }
  },
  ...
}
```

### Data sources per field

| Field | Source | Key / path |
|---|---|---|
| `num`, `name`, `types`, `baseStats`, `abilities`, `evolvesInto` | `pokemon-showdown/data/pokedex.ts` | `pokedex[id]` |
| `catchRate` | `personal.json` `baseStats[i].catchRate` | index = national dex number |
| `expYield` | `personal.json` `baseStats[i].expYield` | — |
| `growthRate` | `personal.json` `baseStats[i].growthRate` | strip `GROWTH_` prefix |
| `evYield.*` | `personal.json` `baseStats[i].*_yield` | hp_yield, atk_yield, def_yield, speed_yield, spatk_yield, spdef_yield |
| `heldItems` | `personal.json` `baseStats[i].items` | ITEM_NONE entries omitted |
| `levelUpMoves` | `pokemon-showdown/data/learnsets.ts` | `learnsets[id].learnset`, keep only `[num]L` entries for Gen 4 (ends with `4L` → `L` = level) |

### Output file: `game_folder/moves_data.json`

One JSON object keyed by move name (lowercase, no spaces):

```json
{
  "tackle": {
    "name": "Tackle",
    "type": "Normal",
    "category": "Physical",
    "bp": 40,
    "accuracy": 100,
    "pp": 35,
    "priority": 0,
    "multihit": null,
    "drain": null,
    "recoil": null,
    "flags": { "contact": true, "protect": true, "mirror": true }
  },
  ...
}
```

Source: `pokemon-showdown/data/moves.ts` for BP / type / category / flags;
`pokemon-showdown/sim/dex-moves.ts` for PP and accuracy.

### Output file: `game_folder/abilities_data.json`

For each ability, record which in-battle callbacks it has so the engine can register
the right hooks. Do not inline the effect logic — implement it in Java using the Showdown
`data/abilities.ts` implementation as the specification.

```json
{
  "intimidate": {
    "name": "Intimidate",
    "hooks": ["onSwitchIn"]
  },
  "levitate": {
    "name": "Levitate",
    "hooks": ["onImmunity"]
  },
  ...
}
```

### Experience thresholds: `game_folder/exp_thresholds.json`

Flatten `growtbl.csv` into a lookup keyed by growth rate → array of 101 integers
(index = level, value = total exp needed to reach that level):

```json
{
  "MEDIUM_FAST":   [0, 0, 8, 27, 64, 125, ...],
  "MEDIUM_SLOW":   [0, 0, 9, 57, 96, 135, ...],
  "FAST":          [0, 0, 6, 21, 51, 100, ...],
  "SLOW":          [0, 0, 10, 33, 80, 156, ...],
  "ERRATIC":       [0, 0, 15, 52, 122, 225, ...],
  "FLUCTUATING":   [0, 0, 4, 13, 32, 65, ...]
}
```

Source: `growtbl.csv` — read columns `lv000`–`lv100` for each growth rate row.

---

## Battle engine architecture

The engine is a self-contained subsystem. Nothing outside it reads or writes `BattleState`
directly — only `BattleEngine` does. The overworld calls `BattleEngine.start(...)` and
receives a `BattleResult` when it ends.

```
overworld
  └─ BattleEngine.start(BattleSpec) ──────────────────────────┐
       │                                                        │
       ├─ DataLayer (loads pokemon_data.json, moves_data.json) │
       ├─ BattleState (mutable snapshot of the current turn)   │
       ├─ TurnExecutor (runs one turn end-to-end)              │
       │    ├─ ActionQueue (priority + speed sort)             │
       │    ├─ MoveExecutor (damage, effects, secondary)       │
       │    │    ├─ DamageCalculator (formula, modifiers)      │
       │    │    └─ AbilityRegistry (all ability hooks)        │
       │    ├─ StatusManager (EOT damage, sleep ticks, etc.)   │
       │    └─ FaintHandler (exp, evolution triggers)          │
       ├─ AI (move selection for opponent)                      │
       └─ BattleRenderer (LibGDX, reads BattleState read-only) │
       │                                                        │
       └─ returns BattleResult ──────────────────────────────►overworld
```

### BattleState

```java
class BattleState {
    ActivePokemon attacker;        // player's current Pokémon on field
    ActivePokemon defender;        // opponent's current Pokémon on field
    Weather weather;               // NONE / SUN / RAIN / SAND / HAIL
    int weatherTurnsLeft;
    boolean trickRoom;
    int trickRoomTurnsLeft;
    BattleType type;               // WILD / TRAINER / STATIC
    TrainerData trainer;           // null for wild
    List<ActivePokemon> playerParty;
    List<ActivePokemon> opponentParty;
    List<BattleEvent> eventLog;    // for render and text generation
}

class ActivePokemon {
    SpeciesData species;
    int level;
    int currentHP, maxHP;
    StatsTable stats;              // computed at battle-start from base + IV + EV + nature + stage
    int[] statStages;              // [atk, def, spa, spd, spe, acc, eva] each -6 to +6
    Move[] moves;
    int[] currentPP;
    StatusCondition status;        // PAR SLP BRN FRZ PSN TOX NONE
    int sleepCounter;
    int toxCounter;
    boolean abilityOn;             // for Flash Fire, Speed Boost, etc.
    String ability;
    String item;
    // Volatile conditions (cleared on switch-out):
    boolean confused, flinched;
    boolean attract;
    boolean leechSeeded;
}
```

### Event-driven ability system

All 493 Pokémon have abilities and every ability that has an in-battle effect must be
implemented. **There is no stub list.** Use `pokemon-showdown/data/abilities.ts` as the
authoritative specification — every `onStart`, `onModifyAtk`, `onTryHit`,
`onDamagingHit`, `onResidual`, etc. callback maps directly to a hook in `AbilityRegistry`.

```java
interface AbilityHook {}

interface OnSwitchIn      extends AbilityHook { void onSwitchIn(BattleState s, ActivePokemon user); }
interface OnModifyAtk     extends AbilityHook { int  onModifyAtk(int atk, BattleState s, ActivePokemon user, Move m); }
interface OnModifyDef     extends AbilityHook { int  onModifyDef(int def, BattleState s, ActivePokemon defender, Move m); }
interface OnModifySpA     extends AbilityHook { int  onModifySpA(int spa, BattleState s, ActivePokemon user, Move m); }
interface OnModifySpD     extends AbilityHook { int  onModifySpD(int spd, BattleState s, ActivePokemon defender, Move m); }
interface OnModifySpe     extends AbilityHook { int  onModifySpe(int spe, BattleState s, ActivePokemon user); }
interface OnTryHit        extends AbilityHook { boolean onTryHit(BattleState s, ActivePokemon attacker, ActivePokemon defender, Move m); }
interface OnDamagingHit   extends AbilityHook { void onDamagingHit(BattleState s, ActivePokemon attacker, ActivePokemon defender, int damage, Move m); }
interface OnResidual      extends AbilityHook { void onResidual(BattleState s, ActivePokemon pokemon); }
interface OnWeatherChange  extends AbilityHook { void onWeatherChange(BattleState s, Weather w); }
// ... (add more as needed from Showdown's callback list)
```

`AbilityRegistry.register(String abilityName, AbilityHook... hooks)` at boot time.
Every ability in `abilities_data.json` that has hooks listed must be registered. Any
ability with no listed hooks (pure flavor — e.g. Honey Gather, Hustle's damage-split)
needs no registration; calling a hook on an unregistered ability is a no-op.

---

## Stat calculation

Source: `damage-calc/calc/src/stats.ts`, function `calcStatADV` (Gen 4 uses ADV formula):

**HP:**
```
hp = floor((2 * base + IV + floor(EV / 4)) * level / 100) + level + 10
```

**All other stats:**
```
stat = floor((floor((2 * base + IV + floor(EV / 4)) * level / 100) + 5) * N)
```
where `N` is the nature modifier: 1.1 if the stat is boosted by this nature, 0.9 if
lowered, 1.0 if neutral. Neutral natures (Bashful, Docile, Hardy, Quirky, Serious) apply
N = 1.0 to all stats.

Stat stage modifier (applied at use-time, not baked into stats):
- Attack / Defense / SpA / SpD / Speed: `[25,28,33,40,50,66,100,150,200,250,300,350,400]` / 100 for stages -6 to +6
- Accuracy / Evasion: `[33,36,43,50,60,75,100,133,166,200,250,266,300]` / 100

---

## Damage formula (Gen 4)

Source: `damage-calc/calc/src/mechanics/gen4.ts`, function `calculateDPP`.
Implement this exactly — do not simplify.

### Step 1 — base damage

```
baseDamage = floor(floor((floor(2*level/5 + 2) * basePower * Atk) / 50) / Def)
```
- `Atk` = attacker's effective Attack or SpA after stat-stage modifier  
  (`getModifiedStat` in `calc/src/mechanics/util.ts`)
- `Def` = defender's effective Defense or SpD after stat-stage modifier  
- For physical moves on a burned attacker without Guts:  
  `baseDamage = floor(baseDamage * 0.5)`

### Step 2 — pre-random modifiers (each applied with its own floor)

In order:
1. Reflect (physical) or Light Screen (special): `floor(baseDamage * 0.5)` in singles;  
   skipped on critical hits.
2. Double battle spread penalty (`allAdjacent`/`allAdjacentFoes`): `floor(baseDamage * 0.75)` — not needed in v1 (singles only)
3. Weather boost (Sun+Fire or Rain+Water): `floor(baseDamage * 1.5)`
4. Weather penalty (Sun+Water or Rain+Fire): `floor(baseDamage * 0.5)`
5. Flash Fire (attacker has Flash Fire boosted): `floor(baseDamage * 1.5)`
6. `baseDamage += 2`
7. Critical hit: `baseDamage *= 2` (or `*= 3` with Sniper)
8. Life Orb: `floor(baseDamage * 1.3)`

### Step 3 — random roll (16 samples)

```java
int[] damage = new int[16];
for (int i = 0; i < 16; i++) {
    damage[i] = (baseDamage * (85 + i)) / 100;   // integer division = floor
}
```

Pick one sample uniformly at random. **This is not a float [0.85, 1.0] — it is a discrete
16-point roll.** The damage calculator returns all 16 values; the simulator picks one.

### Step 4 — post-random modifiers (each with its own floor)

In order:
1. STAB: `floor(d * 1.5)`, or `floor(d * 2)` with Adaptability
2. Type effectiveness 1st type: `floor(d * eff1)` (0 / 0.5 / 1 / 2)
3. Type effectiveness 2nd type: `floor(d * eff2)` (1.0 if monotype)
4. Filter / Solid Rock (defender): `floor(d * 0.75)` if super effective
5. Expert Belt (attacker): `floor(d * 1.2)` if super effective
6. Tinted Lens (attacker): `floor(d * 2)` if not very effective
7. Berry resistance (defender): `floor(d * 0.5)` if berry resists the move's type
8. `damage[i] = max(1, damage[i])`

Type chart source: `damage-calc/calc/src/data/types.ts`, gen 4 section (`DPP`).
`getMoveEffectiveness` in `calc/src/mechanics/util.ts` handles the Scrappy /
Foresight / Wonder Guard / Levitate / type-immunity logic.

---

## Experience gain

Formula (from `pokeheartgold/src/battle/battle_command.c` line 1249):

```
expGained = floor(baseExpYield * opponentLevel / 7)
```

`baseExpYield` is `personal.json[i].expYield`. This is the wild-battle formula.
In Gen 4, trainer battles grant a 1.5× bonus (the games track whether the opponent is
a trainer). Apply that multiplier: `expGained = floor(expGained * 1.5)` for trainer
battles.

All Pokémon in the player's party that participated in the battle receive the full amount
independently. "Participated" = was sent out at any point during the battle.

### Leveling up

After adding exp, check `exp_thresholds.json[growthRate][level+1]`. If total exp ≥ that
threshold, the Pokémon levels up:
1. Increment `level`.
2. Recompute all stats using the stat formulas above.
3. Check level-up learnset: if `levelUpMoves` contains an entry for the new level, offer
   move learning.
4. Repeat from the threshold check (a Pokémon can gain multiple levels from one battle
   if there is a large exp gap).

---

## Critical hits

Source: Showdown `sim/battle.ts` / `data/abilities.ts`.

| Stage | Probability |
|---|---|
| 0 (base) | 1/16 |
| 1 (Focus Energy, Lansat Berry, Dire Hit) | 1/8 |
| 2 (high-crit move at stage 0; above moves at stage 1) | 1/4 |
| 3 | 1/3 |
| 4+ | 1/2 |

High-crit moves (stage +1 on their own): Aeroblast, Air Cutter, Blaze Kick, Crabhammer,
Cross Chop, Karate Chop, Night Slash, Poison Tail, Psycho Cut, Razor Leaf, Razor Wind,
Shadow Claw, Sky Attack, Slash, Stone Edge.

Critical hits:
- Ignore negative Attack/SpA stages on attacker.
- Ignore positive Defense/SpD stages on defender.
- Do NOT ignore abilities (Battle Armor, Shell Armor prevent crits).
- Do NOT ignore screens (Reflect / Light Screen still halve — this is the Gen 4 rule;
  crits do not break screens in Gen 4).

---

## Priority and turn order

Source: `pokemon-showdown/data/moves.ts` — each move has a `priority` field.

| Priority | Examples |
|---|---|
| +6 | (items and switch-in resolve here — not a move priority) |
| +5 | Helping Hand |
| +4 | Magic Coat, Snatch |
| +3 | Detect, Endure, Follow Me, Protect |
| +2 | ExtremeSpeed |
| +1 | Aqua Jet, Bullet Punch, Ice Shard, Mach Punch, Quick Attack, Shadow Sneak, Sucker Punch, Vacuum Wave |
| 0 | all other moves |
| -1 | Vital Throw |
| -3 | Focus Punch |
| -6 | Counter, Mirror Coat, Metal Burst |
| -7 | Trick Room (reverses speed order within bracket 0 for 5 turns) |

Within a bracket, faster Pokémon goes first. Equal speed → coin flip.
Items (+6) and forced switch-ins (faint replacement) always resolve before any move.

---

## Abilities

Every ability that has an in-battle effect must be fully implemented. There is no stub
list. All 493 Gen 4 Pokémon have abilities; they all work.

Use `pokemon-showdown/data/abilities.ts` as the specification. Every named callback in
that file is the authoritative definition of what the ability does. If an ability has no
callbacks at all in that file, it has no in-battle mechanical effect (e.g. Honey Gather,
Illuminate).

### Implementation priority

Start with the abilities that appear most frequently in wild and trainer encounters across
the three games. The most common ones in Gen 4:

Overgrow, Blaze, Torrent, Keen Eye, Run Away, Intimidate, Sand Veil, Levitate,
Synchronize, Natural Cure, Flash Fire, Volt Absorb, Water Absorb, Shed Skin, Static,
Flame Body, Hustle, Hyper Cutter, Pickup, Illuminate, Guts, Rock Head, Lightning Rod,
Serene Grace, Inner Focus, Thick Fat, Compound Eyes, Swift Swim, Chlorophyll, Pressure,
Wonder Guard, Speed Boost, Battle Armor, Sturdy, Damp, Limber, Cloud Nine, Color Change,
Rough Skin, Effect Spore, Shield Dust, Own Tempo, Suction Cups, Oblivious, Snow Cloak,
Sand Stream, Drizzle, Drought, Trace, Adaptability, Download, Anticipation, Forewarn,
Frisk, Filter, Solid Rock, Snow Warning, Motor Drive, Storm Drain.

Implement every ability in `abilities.ts` whose `onStart`, `onModifyAtk`,
`onModifyDef`, `onTryHit`, `onDamagingHit`, or `onResidual` callbacks are non-empty.

---

## Status conditions

One primary status per Pokémon at a time.

| Status | Effect | End-of-turn damage |
|---|---|---|
| PAR | Speed halved; 25% chance to be fully paralyzed (skip action) | — |
| SLP | Cannot act. Sleep counter set to rand(1–4) on infliction; decrements each turn; wakes when 0 | — |
| BRN | Physical damage halved (in damage formula, before +2); end of turn `floor(maxHP/8)` minimum 1 | `floor(maxHP/8)` |
| FRZ | Cannot act. 20% thaw chance each turn; thaws immediately if hit by any Fire move | — |
| PSN | `floor(maxHP/8)` at end of turn, minimum 1 | `floor(maxHP/8)` |
| TOX | `floor(maxHP/16 * n)` at end of turn (n starts at 1, increments each turn; resets to 1 on switch-out), minimum 1 | `floor(maxHP/16 * n)` |

Note: Burn halves physical damage BEFORE the +2 is added to baseDamage (step 1 in the
damage formula above). Guts ability overrides the damage reduction but still allows the
Burn to apply.

---

## Move hit check

1. If `move.accuracy` is null → always hits (e.g. Swift, Aerial Ace, Aura Sphere).
2. Effective accuracy = `floor(move.accuracy * attacker_acc_stage / defender_eva_stage)`
   using the accuracy/evasion stage table.
3. Roll `rand(1, 100)` — hits if roll ≤ effective accuracy.
4. Miss → no damage, no secondary effects.

---

## Type chart (Gen 4)

Source: `damage-calc/calc/src/data/types.ts` — use the `DPP` section directly.
17 types: Normal, Fire, Water, Electric, Grass, Ice, Fighting, Poison, Ground, Flying,
Psychic, Bug, Rock, Ghost, Dragon, Dark, Steel.

Notable Gen 4 values to verify against the source file:
- Ghost → Normal: 0 (immune); Ghost → Fighting: 0 (immune)  
- Normal → Ghost: 0 (immune); Fighting → Ghost: 0 (immune)  
- Poison → Steel: 0 (Steel immune to Poison)  
- Ground → Flying: 0 (Levitate + Iron Ball interaction handled in `getMoveEffectiveness`)

Do not hardcode the chart — load it from the exported `types_data.json` extracted from
the damage-calc source, so it can be updated without a code change.

---

## Entry points

Three events start a battle:

1. **Wild encounter**: encounter check fires on an eligible tile (see `06_encounters.md`).
   Species chosen from encounter table; level from table; IVs random 0–31; EVs all 0;
   nature random.
2. **Trainer battle**: trainer sight line triggers, or player presses A adjacent to a
   flagged NPC (see `08_npc_trainers.md`). Trainer party loaded from map JSON.
3. **Static encounter**: player steps onto `@` tile. `static_encounters` array in map
   JSON matched by col/row. Entry has species, level, IVs. Respects a flag set on catch
   to prevent re-triggering.

All battles are single (1v1). Doubles, Multi, Rotation: out of scope.

---

## Turn structure

1. **Input phase**: Player selects Fight / Item / Run. AI selects a move (see AI section).
2. **Priority sort**: build `ActionQueue` — items and forced switch-ins at +6, moves at
   their priority, then sort by Speed within bracket, then coin flip for ties.
3. **Execute actions in order**. For each action:
   - Faint check before acting: if the Pokémon is already fainted, skip.
   - Apply the action (damage, status, stat change, item use, etc.).
   - Fire any triggered ability hooks (OnDamagingHit, OnAfterMove, etc.).
   - Faint check: if HP ≤ 0, fire faint sequence (see Faint / EXP section).
4. **End-of-turn phase** (`OnResidual` hooks, in order):
   - Weather damage (Sandstorm / Hail — 1/16 max HP to non-immune types/abilities)
   - Burn, Poison, Toxic damage
   - Speed Boost (+1 spe stage)
   - Leech Seed drain
   - Leftovers recovery
   - Any other `onResidual` ability effects from `abilities.ts`
5. **Turn counter** increments; TOX counter increments for active Pokémon.

---

## AI

**Wild Pokémon**: pick uniformly at random from moves with PP > 0.

**Trainer AI**: for each move with PP > 0, compute expected damage against the player's
current Pokémon using the damage formula at the median random roll (index 7 out of 16).
Pick the highest. No type prediction, no switching, no lookahead.

**Gym leader AI** (`is_leader: true`): same greedy formula but also considers status moves
— a status move with a 100% secondary effect on a Pokémon that does not already have a
primary status is treated as having expected value = 50% of the target's current HP for
tie-breaking purposes.

---

## EXP, fainting, and evolution

On opponent faint:
1. Compute `expGained = floor(baseExpYield * opponentLevel / 7)`.
2. Multiply by 1.5 for trainer battles.
3. Distribute to all participating Pokémon.
4. For each: add exp, check level-up loop (see Leveling section above), check evolution.

Evolution: after all battles in a session (not during), for any Pokémon that hit or passed
its evolution level during level-ups. Conditions checked in v1: level-based only.
Sequence: "X is evolving!" → sprite crossfade (1s) → "X evolved into Y!" → stats update.
Player can press B during crossfade to cancel.

---

## Catching

Wild battles only. In trainer battles: "You can't catch another Trainer's Pokémon!"

```
a = floor((3*maxHP - 2*currentHP) * catchRate * ballBonus / (3*maxHP))
a = floor(a * statusBonus)
```

- `catchRate` from `pokemon_data.json`
- `ballBonus`: Poké Ball=1.0, Great Ball=1.5, Ultra Ball=2.0, Master Ball=skip checks
- `statusBonus`: SLP/FRZ=2.0, PAR/BRN/PSN/TOX=1.5, none=1.0

Three shake checks, each:
```
rand(0, 65535) < floor(65536 / (255 / max(1, a))^0.1875)
```
All three must pass.

---

## Battle conclusion

### Player wins
- EXP awarded, level-ups, evolution sequence.
- Trainer battles: prize money added, trainer flagged as defeated, defeat/post-battle
  dialogue displayed.
- Player returns to overworld at the tile where the battle started.

### Player loses
- All party Pokémon set to 1 HP (fainted Pokémon stay fainted).
- Player placed at `save.json → last_center_map` / `last_center_col` / `last_center_row`.
- No money lost in v1.

### Run (wild only)
- Succeeds automatically if player's fastest Pokémon Speed > 2× opponent's Speed.
- Otherwise 50% success. Failed run: opponent acts normally that turn.
- Trainer battles: "No running from a Trainer battle!" → return to action menu.

---

## Items in battle

Using an item is the player's action; opponent moves normally that turn.

| Item | In-battle effect |
|---|---|
| Potion | +20 HP (one party Pokémon) |
| Super Potion | +50 HP |
| Hyper Potion | +200 HP |
| Max Potion | full HP |
| Full Restore | full HP + cure status |
| Full Heal | cure primary status |
| Antidote / Parlyz Heal / Burn Heal / Ice Heal / Awakening | cure specific status |
| Poké Ball / Great Ball / Ultra Ball / Master Ball | catch attempt |
| X Attack / X Defense / X Speed / X Special / X Accuracy / Dire Hit | stat stage / crit boost (see `12_items.md`) |
| Revive / Max Revive | revive fainted Pokémon |

All items in `12_items.md` with `usable_in_battle: true` are available. Items not tagged
for battle use cannot be selected during battle even if in the bag.

---

## Pokémon Center

Map has a `P` warp entrance; interior has a nurse NPC with `"healer"` in its `service_flags`
array. Full mechanics are in `08_npc_trainers.md § Healer NPCs`.

On interaction: dialogue → all party Pokémon fully restored (HP max, PP max, all status
cleared) → update `last_center_map/col/row` in save.

---

## Between-battle persistence

HP, PP, and status conditions do NOT reset at battle end. They persist exactly as-is
into the overworld. Only Pokémon Centers and items restore them.

---

## Battle UI layout

| Region | Contents |
|---|---|
| Top-left | Opponent front sprite (Gen 4, 96×96 px) |
| Bottom-right | Player back sprite (Gen 4, 96×96 px) |
| Top-right | Opponent: name, level, gender, HP bar, status icon |
| Bottom-left | Player: name, level, gender, HP bar, HP numeric, EXP bar |
| Bottom strip | Action menu OR move menu OR text box |

HP bar: green > 50%, yellow 20–50%, red < 20% of max HP.

Move menu slots: move name, type icon, PP as `current/max`. 0 PP slots grayed out.
If all PP 0: Struggle used automatically (base power 50, Normal type, recoil 25% max HP).

Hit animation: defending sprite flashes white 3× at 10 fps per cycle (0.5s total).

Battle backgrounds: one PNG per biome. Flat color fallback if PNG missing.

### Music

| Context | Key |
|---|---|
| Wild battle | `battle_wild` |
| Trainer battle | `battle_trainer` |
| Gym leader battle | `battle_gym_leader` |
| Wild victory | `victory_wild` |
| Trainer victory | `victory_trainer` |

---

## Asset sources

| Asset | Source path |
|---|---|
| Pokémon front sprites | `pokeheartgold/graphics/pokemon/*/front.png` or `pokeplatinum/graphics/pokemon/*/front.png` (96×96, Gen 4, covers all 493) |
| Pokémon back sprites | same decomps, `back.png` |
| Battle backgrounds | custom / Gen 4 inspired, one per biome |
| Trainer intro sprite | `pokeemerald/graphics/trainers/<trainer_class>.png` (Gen 3 — only asset used inside battles) |
| Battle UI chrome | custom, Gen 4 inspired |

---

## Wild and trainer battle opening sequences

### Wild
1. Screen flash white (2 frames)
2. Wild front sprite slides in top-right, player back sprite slides in bottom-left (0.5s)
3. "A wild [Species] appeared!"
4. Turn input begins

### Trainer
1. Screen flash white
2. Trainer sprite (Gen 3, matched by `trainer_class`) slides in from right (0.5s)
3. "[Trainer class] [Name] wants to battle!"
4. Player presses A → trainer throw animation → first Pokémon slides in
5. Player back sprite slides in
6. "[Trainer] sent out [Species]!"
7. Turn input begins

On trainer's Pokémon fainting mid-battle: trainer sprite briefly reappears, next Pokémon
slides in.
