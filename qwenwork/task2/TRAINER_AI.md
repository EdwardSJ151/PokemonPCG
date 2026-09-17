# Gen 4 Trainer AI Reference

Complete reimplementation reference for the Pokémon Platinum (gen 4) trainer battle AI, with
HeartGold comparisons as exceptions. All file:line citations refer to this repository:
Platinum decomp under `pokeplatinum/`, HeartGold under `pokeheartgold/`.
Platinum is the authoritative spec; where HeartGold differs, the difference is called out.

**Provenance (the missing generated header).** The numeric `AI_FLAG_*`
constants exist nowhere in this checkout: `trainer_ai.h:4-7` includes four
build-time-generated headers (`generated/ai_flags.h`,
`generated/ai_action_choices.h`, `generated/ai_load_type_targets.h`,
`generated/ai_weather_types.h`) of which only the `.txt` sources ship.
`generated/meson.build:17` declares
`{'name': 'ai_flags', 'type': 'mask', 'tag': 'AIFlag', 'extra': ['--no-auto']}`;
the metang subproject that generates the headers is not in the checkout
(`subprojects/` contains only `rust/`), so the values are recovered from
data. Two independent data points fix the convention:
`pokeheartgold/include/constants/battle.h:560-561` defines `AI_DOUBLES
(1 << 7)` and `AI_29 (1 << 29)`, and
`pokeheartgold/src/battle/trainer_ai.c:23-27` uses exactly those in AI init
(roamer → `AI_29`; doubles → `|= AI_DOUBLES`). Those two names are lines
31 and 9 of `generated/ai_flags.txt`, which pins the rule: **line N (N ≥ 2)
of `ai_flags.txt` → `1 << (N-2)`; line 1 (`AI_FLAG_NONE`) → 0.** The bit
table is in §3.

---

## §1 Architecture

Paths below are relative to `pokeplatinum/`. `trainer_ai.c` (4199 lines) is
the whole engine: entry points, script interpreter, damage model, and the
switch/item decisions. `script.s` (8105 lines) is pure `.rodata` — one word
table containing every flag script.

### Supporting sources

|File|Role|
|---|---|
|`src/battle/trainer_ai/trainer_ai.c`|engine, interpreter, damage model, switch/item|
|`src/battle/trainer_ai/script.s`|all flag scripts as words (`gTrainerAITable`)|
|`include/battle/ai_context.h` (51 lines)|the `AIContext` struct|
|`include/constants/battle/trainer_ai.h` (55 lines)|AI constants (table near end of this section)|
|`include/data/scripts/aicmd.h` (120 lines)|the 109 opcode names (values 0-108 in declaration order)|
|`include/data/scripts/cmd_table.h` (27 lines)|the `ScriptCommand` dispatch macro|
|`asm/macros/aicmd.inc` (640 lines)|assembler macros + literal opcode values|
|`generated/ai_flags.txt` (34 lines)|the 32 flag names, one per line|
|`generated/ai_action_choices.txt` (7 lines)|`AI_ENEMY_ATTACK_1..4` = 0-3, `AI_ENEMY_ESCAPE` = 4, `AI_ENEMY_SAFARI` = 5, `AI_ENEMY_SWITCH` = 6|
|`generated/ai_weather_types.txt` (6 lines)|CLEAR, SUNNY, RAINING, SANDSTORM, HAILING, DEEP_FOG = 0-5|
|`generated/item_ai_categories.txt` (6 lines)|FULL_RESTORE, RECOVER_HP, RECOVER_STATUS, STAT_BOOSTER, GUARD_SPEC, MAX = 0-5|
|`generated/ai_load_type_targets.txt` (9 lines)|`LoadTypeFrom` targets: DEFENDER_TYPE_1=0, ATTACKER_TYPE_1=1, DEFENDER_TYPE_2=2, ATTACKER_TYPE_2=3, MOVE_TYPE=4, DEFENDER_PARTNER_TYPE_1=5, ATTACKER_PARTNER_TYPE_1=6, DEFENDER_PARTNER_TYPE_2=7, ATTACKER_PARTNER_TYPE_2=8|
|`tools/dataproc/src/trainerproc.c:157-163`|JSON `ai_flags` strings → numeric `header.aiMask`|

### The `AIContext` struct (include/battle/ai_context.h:9-50)

```c
typedef struct AIContext {
    u8 evalStep;                  // AI_EVAL_STEP_{INIT,EVAL,END} = 0,1,2
    u8 moveSlot;                  // 0-3: slot currently being scored
    u16 move;                     // move in `moveSlot` (MOVE_NONE if 0 PP)
    s8 moveScore[4];              // running score per slot
    int calcTemp;                 // scratch register for Load*/IfLoaded*
    u32 thinkingMask;             // flag mask, right-shifted as it runs
    u8 stateFlags;                // AI_STATUS_FLAG_* bits
    u8 thinkingBitShift;          // index (0-31) of the flag script running
    u8 padding0012;
    u8 padding0013;
    int padding0014;              // "this does not match with a u32, weird"
    u8 moveDamageRolls[4];        // per-slot damage variance, [85..100]
    u16 battlerMoves[4][4];       // last-observed moves, per battler (≤4)
    u8 battlerAbilities[4];
    u16 battlerHeldItems[4];
    u16 trainerItems[4][4];       // [opponent][item slot]
    u32 scriptStackPointer[8];
    u8 scriptStackSize;
    u8 trainerItemCounts[2];      // per side
    u8 attacker;
    u8 defender;
    u8 usedItemType[2];
    u8 usedItemCondition[2];
    u16 usedItem[2];
    u8 selectedTarget[4];
    MoveTable moveTable[MAX_MOVES];
    ItemData *itemTable;
    u16 padding1DD0[4];
    u16 padding1DD8[4];
} AIContext;
```

`AIContext` is a member of `BattleContext`; the script pointer and cursor
(`aiScriptTemp`, `aiScriptCursor`) live on `BattleContext`, not here.
`#define AI_CONTEXT (battleCtx->aiContext)` (trainer_ai.c:28).
(`MAX_OPPONENTS`=4, `MAX_TRAINER_ITEMS`=4, `MAX_BATTLERS`=4,
`MAX_BATTLERS_PER_SIDE`=2.)

The mirror caches are **populated by the battle system, not the AI**:
`battle_context_player.c:188-189` copies the whole `MoveTable` into
`aiContext.moveTable` and loads `itemTable` when the battle context is
created (freed on context free, :213-214); `:4808-4810` copies each
trainer's items into `trainerItems[battler >> 1]` at battle start
(adjacent to `:4816`, which sets `aiScriptTemp = gTrainerAITable`);
`battle_script.c:12108` records `battlerAbilities[battler]` on mon entry
(reset to `ABILITY_NONE` by `battle_lib.c:7726`). `TrainerAI_Init`
deliberately zeroes only the prefix of the struct up to `battlerMoves`
(trainer_ai.c:218-221), so `battlerMoves`, `trainerItems`, `usedItem*` and
the table pointers survive across turns.

### The word table (`gTrainerAITable`, script.s:16-55)

The table starts with a **FlagTable** of 32 `LabelDistance` words in
`ai_flags.txt` order, each pointing at that flag's `*_Main` label (all 18
`AI_FLAG_UNUSED_*` entries point at `Terminate`); all of `script.s`'s
script words follow, in file order. Encoding
(`asm/macros/aicmd.inc:634-640`):

- `LabelDistance(dst, src)` = `.long (dst - src)/4` — a word-sized offset.
- A taken conditional jump stores `.long (label - jumpword)/4 - 1`; the
  handler `AIScript_Iter`s by that value and lands exactly on the label. An
  untaken jump simply falls through to the next word.
- Table-carrying opcodes (`IfLoadedInTable`, …) carry one extra word
  `.long (table - .)/4 - 2`; a table is a run of `TableEntry` words
  (`.long \entry`) terminated by `TABLE_END` = `0xFFFFFFFF`.

### Phases and entry points

|Phase|Call site|Function|
|---|---|---|
|move select|`src/battle/battle_display.c:3580-3621` (`Task_TrainerShowMoveSelectMenu`)|`TrainerAI_Main`|
|command (switch / item / escape)|`src/battle/battle_display.c:3411`|`TrainerAI_PickCommand`|
|item use (folded into the command phase)|—|`TrainerAI_ShouldUseItem`|

The move-select task takes the AI path when `(battleType &
(BATTLE_TYPE_TRAINER | BATTLE_TYPE_ROAMER))` or
`BATTLE_STATUS_FIRST_BATTLE` is set, or the battler is on the player's own
side (an AI-controlled party member / partner)
(`battle_display.c:3587-3589`).

**Return-value / wire mapping.** `TrainerAI_Main` returns the **0-based**
move slot (0-3), or 4 = escape, 5 = safari. The task then runs
`switch (action) { case 0xFF: return; default: action++; }`
(`battle_display.c:3592-3599`) — the `0xFF` case is unreachable in this
decomp (Main never returns it) — and emits the incremented value
(`:3616`). So **on the wire: 1-4 = moves (1-based), 5 = escape, 6 =
safari**. `AI_ENEMY_SWITCH` (6 in the enum) is a command-phase value, not a
move-select value; the non-AI branch of the task picks a uniformly random
valid move (`:3601-3614`).

### `TrainerAI_Init` (trainer_ai.c:211-256)

Called once per acting battler by `TrainerAI_Main`, and again **per
target** by `MainDoubles`.

1. Byte-memset of the struct prefix up to `battlerMoves` (:218-221).
2. `moveScore[i] = (initScore & 1) ? 100 : 0; initScore >>= 1` (:223-231) —
   `initScore` is a per-slot bitmask (`AI_INIT_SCORE_ALL_MOVES` = 15 in
   normal play).
3. `BattleSystem_CheckInvalidMoves(battler, 0, CHECK_INVALID_ALL)`; every
   flagged slot is scored 0 (:234-238).
4. `moveDamageRolls[i] = 100 - (RandNext() % 16)` → variance factor
   [85..100] per slot (:240).
5. `scriptStackSize = 0` (:243).
6. `thinkingMask = (battleType & BATTLE_TYPE_ROAMER) ?
   AI_FLAG_ROAMING_POKEMON : battleSys->trainers[battler].header.aiMask`
   (:246-250).
7. `if (battleType & BATTLE_TYPE_DOUBLES) thinkingMask |=
   AI_FLAG_TAG_STRATEGY` (:253-255).

### `TrainerAI_Main` (258-277)

If `stateFlags` is not `CONTINUE`: `attacker = battler`, `defender =
BattleSystem_RandomOpponent(...)`, then `Init(attacker,
AI_INIT_SCORE_ALL_MOVES)`. Dispatch: `BATTLE_TYPE_DOUBLES` →
`TrainerAI_MainDoubles`, else `TrainerAI_MainSingles`.

### `TrainerAI_MainSingles` (286-343)

1. `TrainerAI_RecordLastMove` (2705-2717) — appends the defender's last
   used move (`battleCtx->movePrevByBattler[defender]`) to
   `battlerMoves[defender]` at the first empty slot, unless it already
   appears (dedup; max 4 entries). This is what `IfMove*Known` reads.
2. The flag loop (:297-309, verbatim):
   ```c
   while (AI_CONTEXT.thinkingMask) {
       if (AI_CONTEXT.thinkingMask & AI_FLAG_BASIC) {
           if ((AI_CONTEXT.stateFlags & AI_STATUS_FLAG_CONTINUE) == FALSE) {
               AI_CONTEXT.evalStep = AI_EVAL_STEP_INIT;
           }
           TrainerAI_EvalMoves(battleSys, battleCtx);
       }
       AI_CONTEXT.thinkingMask = AI_CONTEXT.thinkingMask >> 1;
       AI_CONTEXT.thinkingBitShift++;
       AI_CONTEXT.moveSlot = 0;
   }
   ```
   The test is always `& AI_FLAG_BASIC` (= 1) on the right-shifted mask:
   iteration k runs the script of flag k (selected as
   `aiScriptTemp[thinkingBitShift]` inside `EvalMoves`) iff original bit k
   was set — ascending bit order, 0 → 31.
3. Result (:311-341): `ESCAPE` → `AI_ENEMY_ESCAPE` (4); `SAFARI` → 5;
   otherwise max-score selection — slot 0 seeds the list **unconditionally**;
   slots 1-3 are only considered if `moves[i] != 0`; a `==` score appends to
   the tie list, a higher score resets it; the final pick is
   `maxScoreMoveSlots[RandNext() % numMaxScoreMoves]` (uniform random
   tie-break — the only selection randomness in a singles battle).
   `selectedTarget[attacker] = defender`.

### `TrainerAI_MainDoubles` (352-472)

For each of the 4 battlers: the attacker itself and fainted mons
(`curHP == 0`) are marked `-1` and skipped. Otherwise: `Init(attacker,
0xf)` (the per-battler local `maxScoreForBattler` is **not** reset by
`Init`), `defender = battler`, `RecordLastMove` only when the defender is on
the opposite side (`(battler & 1) != (attacker & 1)`), then the same shift
loop over a local copy of `thinkingMask`.

Per target: `ESCAPE`/`SAFARI` → `actionForBattler[target] = 4/5` (this path
leaves `maxScoreForBattler[target]` uninitialized — in practice only
reachable for roaming/Safari, which are singles battles); otherwise pick
uniformly among the tied max-scoring moves → `actionForBattler[target]`,
and `maxScoreForBattler[target] = max score`.

**Partner suppression** (:431-436): if the target is the attacker's partner
(`attacker ^ 2`) and its max score is `< 100`, `maxScoreForBattler[target]`
is set to `-1` — a penalized partner is never attacked; a partner boosted
above 100 can be.

Overall: take the max over all four targets (ties accumulate into
`battlerTemp[]`; a strictly higher score resets the list), then pick
uniformly among the tied targets → `selectedTarget[attacker]`;
`moveSlot = actionForBattler[selectedTarget]`. **Retargeting** (:462-469): a
move with `moveTable[move].range == RANGE_USER_OR_ALLY` whose chosen target
is on the attacker's own side (side 0) retargets to the attacker; and
`MOVE_CURSE` that is *not* a ghost-type Curse retargets to the attacker.
Returns `moveSlot` (0-3, or 4/5 if the chosen target's action was
escape/safari).

### `TrainerAI_EvalMoves` (483-526)

Per-(flag, move-slot) state machine, looping until `evalStep == END`
(values INIT=0, EVAL=1, END=2; the step counter is only ever incremented):

- **INIT**: `aiScriptCursor = aiScriptTemp[thinkingBitShift]` (the entry
  word of the current flag); `move = (ppCur[moveSlot] == 0) ? MOVE_NONE :
  moves[moveSlot]`; → EVAL.
- **EVAL**: if `move != MOVE_NONE` →
  `sAICommandTable[aiScriptTemp[aiScriptCursor]](sys, ctx)` — the
  dispatcher reads the opcode word **without consuming it**, so every
  handler's first act is `AIScript_Iter(ctx, 1)`. If `move == MOVE_NONE` →
  `moveScore[moveSlot] = 0` + `DONE`. After a command: if `DONE` →
  `moveSlot++`; if `moveSlot < 4` and `BREAK` not set → back to INIT (the
  same flag's script restarts from its entry word for the next move);
  otherwise → END. The `DONE` bit is cleared after processing;
  **`ESCAPE` / `SAFARI` / `BREAK` persist** across moves and across flags
  (until the next `Init`).

### Interpreter helpers (2554-2798)

- `AIScript_Read` (2726): `word = aiScriptTemp[aiScriptCursor++]`.
- `AIScript_ReadOffset(ctx, ofs)` (2741): peek at the word `+ofs` ahead
  without advancing.
- `AIScript_Iter(ctx, n)` (2752): `cursor += n`.
- `AIScript_Battler` (2765-2782): `AI_BATTLER_ATTACKER` → the attacker's
  slot; `AI_BATTLER_DEFENDER` (**and any unknown value — default**) →
  the defender's slot; the two `*_PARTNER` constants → `battler ^ 2`.
- `AICmd_PushAndGoTo` (2554): push the cursor on the stack (depth 8,
  `AI_MAX_STACK_SIZE`), then jump. `AICmd_GoTo` (2561): jump.
  `AICmd_PopOrEnd` (2568): pop → jump; empty stack → `DONE` (finish this
  move's evaluation).
- **Random distribution.** `BattleSystem_RandNext`
  (`battle_system.c:1308-1311`) is a 16-bit LCG
  (`seed = seed * LCRNG_MULTIPLIER + LCRNG_INCREMENT; return seed /
  LCRNG_DIVISOR`). `IfRandom*` compares `RandNext() % 256`, i.e. a uniform
  draw from [0..255] — a script threshold `N` therefore means probability
  `N/256`. (Other call sites use the raw 16-bit value with their own
  modulus, e.g. `RandNext() % numMax` for tie-breaks.)

### Constants (include/constants/battle/trainer_ai.h:9-53)

|Constant|Value|
|---|---|
|`AI_INIT_SCORE_MOVE_1..4`|`1<<0` .. `1<<3`|
|`AI_INIT_SCORE_ALL_MOVES`|15|
|`AI_STATUS_FLAG_DONE / ESCAPE / SAFARI / BREAK / CONTINUE`|1 / 2 / 4 / 8 / 0x10|
|`AI_STATUS_FLAG_*_OFF`|`flag ^ 0xFF` (clear-masks)|
|`AI_BATTLER_DEFENDER / AI_BATTLER_ATTACKER`|0 / 1|
|`AI_BATTLER_DEFENDER_PARTNER / AI_BATTLER_ATTACKER_PARTNER`|`0 ^ 2` / `1 ^ 2`|
|`AI_MAX_STACK_SIZE`|8|
|`AI_NO_COMPARISON_MADE / AI_NOT_HIGHEST_DAMAGE / AI_MOVE_IS_HIGHEST_DAMAGE`|0 / 1 / 2|
|`AI_NOT_HAVE / AI_HAVE / AI_UNKNOWN`|0 / 1 / 2|
|`USE_MAX_DAMAGE / ROLL_FOR_DAMAGE`|0 / 1|
|`CHECK_DISABLE / CHECK_ENCORE`|0 / 1|
|`CHECK_HIGHER_THAN_TARGET / CHECK_LOWER_THAN_TARGET / CHECK_EQUAL_TO_TARGET`|0 / 1 / 2|

### The damage model

`TrainerAI_CalcAllDamage` (2799-2849) computes damage for each of the 4
move slots. A move is **eligible** iff its effect is in
`sAltPowerMoveEffects`, or (`moves[i] != MOVE_NONE` and its effect is *not*
in `sNoDamageCalcMoveEffects` and `MOVE_DATA(moves[i]).power > 1`).
Eligible → `damageVals[i] = CalcDamage(…, roll)` with `roll =
moveDamageRolls[i]` when `varyDamage` is set (the `ROLL_FOR_DAMAGE` script
argument) else 100; ineligible → `damageVals[i] = 0`. Returns the max.

```c
// trainer_ai.c:31-42 — effects that never get a damage calc (score 0)
sNoDamageCalcMoveEffects = { HALVE_DEFENSE, RECOVER_DAMAGE_SLEEP,
    CHARGE_TURN_HIGH_CRIT, CHARGE_TURN_HIGH_CRIT_FLINCH, RECHARGE_AFTER,
    CHARGE_TURN_DEF_UP, SKIP_CHARGE_TURN_IN_SUN, SPIT_UP,
    HIT_LAST_WHIFF_IF_HIT, LOWER_OWN_ATK_AND_DEF,
    DECREASE_POWER_WITH_LESS_USER_HP, HIT_FIRST_IF_TARGET_ATTACKING,
    RECOIL_HALF, 0xFFFF };

// trainer_ai.c:44-56 — effects with special power formulas
sAltPowerMoveEffects = { RANDOM_POWER_BASED_ON_IVS, POWER_BASED_ON_LOW_SPEED,
    NATURAL_GIFT, JUDGEMENT, 40_DAMAGE_FLAT, LEVEL_DAMAGE_FLAT,
    RANDOM_DAMAGE_1_TO_150_LEVEL, POWER_BASED_ON_FRIENDSHIP,
    POWER_BASED_ON_LOW_FRIENDSHIP, 20_DAMAGE_FLAT,
    INCREASE_POWER_WITH_WEIGHT, 0xFFFF };
```

`TrainerAI_CalcDamage` (2868-3113) resolves power / type / flat damage per
move:

|Move|Resolution|
|---|---|
|Natural Gift|power + type from the held item's data; 0 if the ability is Klutz or the attacker is embargoed; type falls back to NORMAL when the item's power is 0|
|Judgment|power 0; type set by the held Arceus plate (16 cases), NORMAL otherwise; no effect under Klutz/Embargo|
|Hidden Power|`p`, `t` from bit 1 of each of the 6 IVs (`p` = HP, atk, def, spd, spA, spD bits assembled; `t` from bit 0); `power = p*40/63 + 30`; `type = t*15/63 + 1`, `+1` if `type >= TYPE_MYSTERY`|
|Gyro Ball|`1 + 25 * defenderSpeed / attackerSpeed` (integer division), capped at 150|
|Dragon Rage|flat damage 40|
|Seismic Toss / Night Shade|flat damage = attacker level|
|Psywave|`level * (RandNext() % 11 + 5) / 10`|
|Return|`friendship * 10 / 25`|
|Frustration|`(255 - friendship) * 10 / 25`|
|Magnitude|`RandNext() % 100` rolled into 10 (5%) / 30 (10%) / 50 (20%) / 70 (20%) / 90 (20%) / 110 (10%) / 150 (15%)|
|Sonic Boom|flat damage 20|
|Low Kick / Grass Knot|weight brackets from `sWeightToPower` (`#include "data/battle/weight_to_power.h"` at :2851); 120 above the last bracket|

Then: if no flat damage was preset (`damage == 0`) the normal path runs —
`BattleSystem_CalcMoveDamage(sys, ctx, move, sideConditions, fieldConditions,
power, type, attacker, defender, 1)` (the engine's own damage routine, with
the overridden power/type); otherwise (flat-damage moves) the code instead
sets `SYSCTL_IGNORE_TYPE_CHECKS` (type scaling skipped). Finally
`BattleSystem_ApplyTypeChart(…)` fills `effectivenessFlags` (the flag is
cleared afterwards); if `MOVE_STATUS_IMMUNE` → damage 0, else
`damage = (damage * variance) / 100` (integer division by the
`moveDamageRolls` factor).

### Type chart and effectiveness classes

`TrainerAI_CalcDamage` hands the resolved `type` to
`BattleSystem_ApplyTypeChart` (battle_lib.c:2560-2679), which:

- Returns Struggle's damage unchanged.
- `moveType = Normalize ? TYPE_NORMAL : (inType ? inType : the move's
  own type)` — the `inType` override is honored **only when
  non-zero**. The `type` the AI passes comes from
  `TrainerAI_MoveType` (trainer_ai.c:3127-3248): Natural Gift →
  `Battler_NaturalGiftType`; Judgment → the held Arceus-plate type
  (NORMAL if no plate); Hidden Power → the IV formula; Weather Ball →
  WATER / ROCK / FIRE / ICE per active weather, and only under
  `NO_CLOUD_NINE && (fieldConditionsMask & FIELD_CONDITION_WEATHER)` —
  **when no weather bit is set, `result` is left uninitialized**
  (decomp quirk). Every other move returns `TYPE_NORMAL`, which is `0`
  (line 1 of `generated/pokemon_types.txt`) — i.e. "use the move's
  own type".
- Applies STAB ×1.5 (×2 Adaptability) when the attacker's mon has
  `moveType` (`MON_HAS_TYPE`); skipped under
  `SYSCTL_IGNORE_TYPE_CHECKS` (which the AI sets for flat-damage
  moves, :3093).
- Ground-type immunities: Levitate (`MOVE_STATUS_LEVITATED`) or Magnet
  Rise (`MOVE_STATUS_MAGNET_RISE`), gated by
  `Battler_IgnorableAbility` (Mold Breaker et al.) and grounding
  items; these skip the table walk.
- Table walk over `sTypeMatchupMultipliers` (battle_lib.c:2399-2517):
  rows `{atkType, defType, mul}`, `mul` ∈ {0, 5, 20}, terminated by
  `0xFF`. Each of the defender's two types matches at most one row and
  both applications are sequential (`ApplyTypeMultiplier`,
  battle_lib.c:7542-7579: `damage = damage * mul / 10` — 0 = immune,
  5 = ×0.5, 20 = ×2; a dual-type defender thus ends at ×0.25 / ×1 /
  ×2 / ×4). Sets on the effectiveness word: `MOVE_STATUS_INEFFECTIVE`
  (0), `NOT_VERY_EFFECTIVE` (5), `SUPER_EFFECTIVE` (20). Row overrides
  (`BasicTypeMulApplies`, 2529-2558): grounding item / Ingrain /
  Roosting / Gravity cancel FLYING-immunity rows; Miracle Eye cancels
  DARK-immunity rows. A `0xFE` sentinel row (2509) separates the two
  rows making Normal and Fighting immune to Ghost (2513-2514): with
  Foresight (defender) or Scrappy (attacker) the walk `break`s at the
  sentinel and those immunities vanish; otherwise the two rows apply
  as normal.
- Post-table: Wonder Guard (not ignorable) on a move that is on its
  damaging turn (`MoveIsOnDamagingTurn`, 7588-7606: Bide, the
  charge-turn moves, Fly / Dive / Dig / Bounce, and Fire Fang count
  only on `SYSCTL_LAST_OF_MULTI_TURN`) and not 2×/4× →
  `MOVE_STATUS_WONDER_GUARD`; Filter / Solid Rock → ×3/4 on a
  super-effective hit; Wise Glass multiplies a super-effective hit;
  Tinted Lens doubles a non-super-effective hit.

**Immunity, as the AI sees it.** `MOVE_STATUS_IMMUNE` is a compound
mask (include/constants/battle/moves.h:86-87): `INEFFECTIVE |
WONDER_GUARD | LEVITATED | MAGNET_RISE`. In `TrainerAI_CalcDamage`
(:3106-3107) any of those zeroes the damage; otherwise
`damage = damage * variance / 100` (the per-slot `moveDamageRolls`).

**Effectiveness classes.** `CalcMaxEffectiveness` (42) and
`IfMoveEffectivenessEquals` (43) run a move through this path with
`TYPE_MULTI_BASE_DAMAGE` = 40, then map the scaled result
(trainer_ai.c:1290-1302 / 1329-1341): `120 (STAB×2) → 80`,
`240 (STAB×4) → 160`, `30 (STAB/2) → 20`, `15 (STAB/4) → 10`; if
`effectiveness & MOVE_STATUS_IMMUNE` → `0`; anything else passes
through (40, 60, 80, 160). The resulting class space:

|Class value|Meaning|
|---|---|
|0 (`TYPE_MULTI_IMMUNE`)|no hit (immunity / Wonder Guard / Levitate / Magnet Rise)|
|10 (`TYPE_MULTI_QUARTER_DAMAGE`)|×0.25|
|20 (`TYPE_MULTI_HALF_DAMAGE`)|×0.5|
|40 (`TYPE_MULTI_BASE_DAMAGE`)|×1|
|60 (`TYPE_MULTI_STAB_DAMAGE`)|×1, with STAB|
|80 (`TYPE_MULTI_DOUBLE_DAMAGE`)|×2, with or without STAB|
|160 (`TYPE_MULTI_QUADRUPLE_DAMAGE`)|×4, with or without STAB|

`CalcMaxEffectiveness` takes the max over the attacker's four actual
moves (seeded at 0); `IfMoveEffectivenessEquals` compares the current
move's class against the expected value. Name-collision caveat: the
chart-table constant `TYPE_MULTI_SUPER_EFF` carries the same value as
the class constant `TYPE_MULTI_HALF_DAMAGE` (20) — in the table, 20
means "×2", whereas class 20 means ×0.5; the class space above is
unambiguous either way. (`TYPE_MULTI_NOT_VERY_EFF` = 5 never appears
as a class: the 40-base math yields only {0, 10, 20, 40, 60, 80, 160}.)

## §2 Script command reference

The script is a stream of 32-bit words. The dispatcher
(`TrainerAI_EvalMoves`, §1) reads each opcode word **without consuming
it**, so every handler's first act is `AIScript_Iter(ctx, 1)`; operands
are then fetched with `AIScript_Read` (consume, :2726) or
`AIScript_ReadOffset(n)` (peek, :2741), and `AIScript_Iter(n)`
(:2752) advances the cursor by n words. A conditional's `jump` word —
encoded in the `.s` source by the assembler `LabelDistance`
(`aicmd.inc:638`) — is applied only when the condition holds, and is
measured in words from the cursor position **after** all operands have
been read; an untaken jump falls through to the next word.

**Conventions.**

- A `battler` operand is resolved by `AIScript_Battler`
  (trainer_ai.c:2765-2782): `AI_BATTLER_ATTACKER` → the attacker's
  slot; `AI_BATTLER_DEFENDER` (**and any unknown value — default**) →
  the defender's slot; `AI_BATTLER_ATTACKER_PARTNER` → `attacker ^ 2`;
  `AI_BATTLER_DEFENDER_PARTNER` → `defender ^ 2`.
- Every `IfRandom*` compares `BattleSystem_RandNext() % 256` — a
  uniform draw from [0..255] — so a script threshold N means
  probability N/256.
- `calcTemp` is the shared scratch register: written by `Load*`
  commands, compared by `IfLoaded*` / `IfTemp*`.

**0-4 Random and score.**

|Op|Handler (trainer_ai.c line)|Words read|Behavior|
|---|---|---|---|
|0|`IfRandomLessThan` (528)|threshold, jump|jump if `RandNext() % 256 < threshold`|
|1|`IfRandomGreaterThan` (540)|threshold, jump|`> threshold`|
|2|`IfRandomEqualTo` (552)|threshold, jump|`== threshold`|
|3|`IfRandomNotEqualTo` (564)|threshold, jump|`!= threshold`|
|4|`AddToMoveScore` (576)|delta|`moveScore[moveSlot] += delta`, clamped at 0|

**5-16 Battler and side state.**

|Op|Handler|Words read|Behavior|
|---|---|---|---|
|5|`IfHPPercentLessThan` (588)|battler, threshold, jump|jump if the resolved battler's `curHP * 100 / maxHP` (integer division) `< threshold`|
|6|`IfHPPercentGreaterThan` (603)|same|`> threshold`|
|7|`IfHPPercentEqualTo` (618)|same|`== threshold`|
|8|`IfHPPercentNotEqualTo` (633)|same|`!= threshold`|
|9|`IfStatus` (648)|battler, mask, jump|`battleMons[battler].status & mask` (sleep / poison / freeze / burn — the non-volatile status)|
|10|`IfNotStatus` (662)|same|`& mask == 0`|
|11|`IfVolatileStatus` (676)|same shape|`statusVolatile & mask` (poison, paralysis, confusion, flinch, …)|
|12|`IfNotVolatileStatus` (690)|same|`== 0`|
|13|`IfMoveEffect` (704)|same shape|`moveEffectsMask & mask` — the battler's *active move/ability effects* (Perish Song, Ability Suppress, …), not move data|
|14|`IfNotMoveEffect` (718)|same|`== 0`|
|15|`IfSideCondition` (732)|same shape|`sideConditionsMask[battler's side] & mask` (Spikes, Reflect, …)|
|16|`IfNotSideCondition` (747)|same|`== 0`|

**17-26 Loaded-value tests.**

|Op|Handler|Words read|Behavior|
|---|---|---|---|
|17|`IfLoadedLessThan` (762)|threshold, jump|`calcTemp < threshold`|
|18|`IfLoadedGreaterThan` (774)|same|`> threshold`|
|19|`IfLoadedEqualTo` (786)|same|`== threshold`|
|20|`IfLoadedNotEqualTo` (798)|same|`!= threshold`|
|21|`IfLoadedMask` (810)|mask, jump|`calcTemp & mask != 0`|
|22|`IfLoadedNotMask` (822)|same|`& mask == 0`|
|23|`IfMoveEqualTo` (834)|move, jump|jump if the move currently being scored (`AI_CONTEXT.move`) == move|
|24|`IfMoveNotEqualTo` (846)|same|`!= move`|
|25|`IfLoadedInTable` (858)|table offset, jump|walk the word stream from the offset forward, one word at a time, until `0xFFFFFFFF`; jump if `calcTemp` matches an entry|
|26|`IfLoadedNotInTable` (876)|same|jump only if `calcTemp` matches no entry|

**27-33 Attacker capability and first loaders.**

|Op|Handler|Words read|Behavior|
|---|---|---|---|
|27|`IfAttackerHasDamagingMoves` (895)|jump|jump if any of the attacker's four actual moves is `!= MOVE_NONE` with non-zero power|
|28|`IfAttackerHasNoDamagingMoves` (913)|jump|jump if none is|
|29|`LoadTurnCount` (931)|(none)|`calcTemp = battleCtx->totalTurns`|
|30|`LoadTypeFrom` (937)|target|one of the 9 `ai_load_type_targets.txt` values: loads the named mon's type 1 or 2 (`ATTACKER`/`DEFENDER`/partner variants, partners resolved via `BattleSystem_GetPartner`) or, for `MOVE_TYPE`, `MOVE_DATA(AI_CONTEXT.move).type`. **Quirk:** the `DEFENDER_PARTNER_TYPE_2` case loads `BATTLEMON_TYPE_1` (:982). Unknown target → `GF_ASSERT`|
|31|`LoadMovePower` (1006)|(none)|`calcTemp = MOVE_DATA(AI_CONTEXT.move).power`|
|32|`FlagMoveDamageScore` (1012)|varyDamage (0/1)|compares the current move's damage against the attacker's other moves (all `CalcAllDamage`, roll = per-slot roll if varyDamage else 100) → `calcTemp` = `AI_NO_COMPARISON_MADE` (0), `AI_NOT_HIGHEST_DAMAGE` (1), or `AI_MOVE_IS_HIGHEST_DAMAGE` (2)|
|33|`LoadBattlerPreviousMove` (1068)|battler|`calcTemp = battleCtx->movePrevByBattler[battler]` (0xFFFF if the battler has used no move)|

**38-43 Party count, current move, effectiveness.**

|Op|Handler|Words read|Behavior|
|---|---|---|---|
|38|`CountAlivePartyBattlers` (1126)|battler|count of the resolved battler's party members that are **not** the active slot and **not** the partner slot, alive (HP ≠ 0) and not `SPECIES_NONE`/`SPECIES_EGG` → `calcTemp`|
|39|`LoadCurrentMove` (1158)|(none)|`calcTemp = AI_CONTEXT.move`|
|40|`LoadCurrentMoveEffect` (1164)|(none)|`calcTemp = MOVE_DATA(AI_CONTEXT.move).effect`|
|41|`LoadBattlerAbility` (1170)|battler|see **Ability observation** below|
|42|`CalcMaxEffectiveness` (1268)|(none)|runs each of the attacker's four actual moves through the §1 type-chart path with base damage 40; `calcTemp` = the largest effectiveness class found (0 / 10 / 20 / 40 / 60 / 80 / 160)|
|43|`IfMoveEffectivenessEquals` (1311)|expected class, jump|same computation for the move currently being scored; jump if the class == expected|

**44-54 Party, weather, stats, kill checks.**

|Op|Handler|Words read|Behavior|
|---|---|---|---|
|44|`IfPartyMemberStatus` (1348)|battler, mask, jump|any living bench member (not active, not partner; HP ≠ 0, not NONE/EGG) has `status & mask`|
|45|`IfPartyMemberNotStatus` (1381)|same|none of them does|
|46|`LoadCurrentWeather` (1414)|(none)|`calcTemp` starts as `AI_WEATHER_CLEAR` (0); RAINING, SANDSTORM, SUNNY, HAILING, DEEP_FOG each overwrite it in that order if their bit is set — **the last matching weather wins** (e.g. deep fog beats sun when both bits are set)|
|47|`IfCurrentMoveEffectEqualTo` (1441)|effect, jump|`MOVE_DATA(AI_CONTEXT.move).effect == effect`|
|48|`IfCurrentMoveEffectNotEqualTo` (1453)|same|`!= effect`|
|49|`IfStatStageLessThan` (1465)|battler, stat, stage, jump|`battleMons[battler].statBoosts[stat] < stage`|
|50|`IfStatStageGreaterThan` (1480)|same|`> stage`|
|51|`IfStatStageEqualTo` (1495)|same|`== stage`|
|52|`IfStatStageNotEqualTo` (1510)|same|`!= stage`|
|53|`IfCurrentMoveKills` (1525)|useDamageRoll, jump|jump if the current move would reduce the defender's `curHP` to zero. The move must be eligible (an alt-power effect, or power > 1 and not in `sNoDamageCalcMoveEffects`); if eligible, damage = `TrainerAI_CalcDamage` with the attacker's held item, IVs, ability, embargo turns, and (roll flag ? per-slot roll : 100); if ineligible, never jumps. An immune move deals 0, so it never "kills"|
|54|`IfCurrentMoveDoesNotKill` (1576)|same|jump if the same computation leaves `curHP > 0`|

**55-63 Move knowledge and effect state.**

|Op|Handler|Words read|Behavior|
|---|---|---|---|
|55|`IfMoveKnown` (1627)|battler, move, jump|ATTACKER: scans the actual `battleMons.moves[4]`; ATTACKER_PARTNER: the same scan, but only if the partner is alive; DEFENDER: scans the observed history `AI_CONTEXT.battlerMoves[battler][]` (≤ 4 deduped entries); DEFENDER_PARTNER: default case — **never jumps**. Jumps on a match|
|56|`IfMoveNotKnown` (1683)|same|inverse — jumps when the move is not known|
|57|`IfMoveEffectKnown` (1739)|battler, effect, jump|only ATTACKER and DEFENDER (partner → default case): jump if any scanned move (skipping `MOVE_NONE`) has `MOVE_DATA.effect == effect`|
|58|`IfMoveEffectNotKnown` (1781)|same|inverse|
|59|`IfBattlerUnderEffect` (1823)|battler, check (0 = disable, 1 = encore), jump|`moveEffectsData.disabledTurns` / `encoredTurns` non-zero|
|60|`IfCurrentMoveMatchesEffect` (1850)|check|the disabled / encored move in `moveEffectsData` == the move currently being scored|
|61|`Escape` (1875)|(none)|sets `stateFlags |= DONE | ESCAPE | BREAK`|
|62|`Dummy3E` (1881)|table: opcode word + 1 operand (`aicmd.inc:383`, `i:req`)|handler is `return` — consumes nothing, not even the opcode word|
|63|`Dummy3F` (1886)|table: single word, literal `63` (`aicmd.inc:388`)|handler is `return` — consumes nothing|

**62/63 decomp inconsistency (Safari AI).** Both handlers contradict
the interpreter convention: the eval step *peeks* the opcode word
(`trainer_ai.c:501`) and every other handler consumes it first with
`AIScript_Iter(battleCtx, 1)`; these two return without touching the
cursor (so 62's operand word is never read either). Their only
occurrence is `Safari_Main` (`script.s:8088-8091`: `Dummy3E 1`,
`Dummy3F`, `Escape`). As decompiled, the first dispatch of
`Safari_Main` re-reads word 62 forever — `TrainerAI_EvalMoves`
spin-loops and the battle freezes — so this is a decomp artifact of
an original no-op that evidently consumed its own word; the intended
behaviour is plainly "do nothing, then `Escape`".

**64-74 Miscellaneous loads.**

|Op|Handler|Words read|Behavior|
|---|---|---|---|
|64|`LoadHeldItem` (1891)|battler|the actual `battleMons[battler].heldItem` (either side)|
|65|`LoadHeldItemEffect` (1901)|battler|opponent side: `ItemTable` hold effect of the mirrored `AI_CONTEXT.battlerHeldItems[battler]`; own side: the actual held item|
|66|`LoadGender` (2470)|battler|`battleMons[battler].gender`|
|67|`LoadIsFirstTurnInBattle` (2480)|battler|TRUE iff `moveEffectsData.fakeOutTurnNumber >= totalTurns` (battler just entered / battle start; Fake-Out-aware), else FALSE|
|68|`LoadStockpileCount` (2494)|battler|`moveEffectsData.stockpileCount`|
|69|`LoadBattleType` (2504)|(none)|`battleSys->battleType` (the full battle-type bitmask)|
|70|`LoadRecycleItem` (2510)|battler|`battleCtx->recycleItem[battler]`|
|71|`LoadTypeOfLoadedMove` (2520)|(none)|`calcTemp = MOVE_DATA(calcTemp).type` — chains off a previously loaded move id|
|72|`LoadPowerOfLoadedMove` (2526)|(none)|`... .power`|
|73|`LoadEffectOfLoadedMove` (2532)|(none)|`... .effect`|
|74|`LoadProtectChain` (2538)|battler|if `battleCtx->moveProtect[battler]` ∈ {Protect, Detect, Endure} → `moveEffectsData.protectSuccessTurns` (consecutive successful Protects), else 0|

**75-78 Flow and level.**

|Op|Handler|Words read|Behavior|
|---|---|---|---|
|75|`PushAndGoTo` (2554)|jump|push the cursor onto `scriptStackPointer` (max depth 8, `AIScript_PushCursor` :2671), then jump|
|76|`GoTo` (2561)|jump|unconditional jump|
|77|`PopOrEnd` (2568)|jump|`AIScript_PopCursor` (:2687): pop and jump; empty stack → set `DONE` (finish this move's evaluation)|
|78|`IfLevel` (2579)|op (0 = higher, 1 = lower, 2 = equal), jump|attacker level vs defender level per op|

**79-94 Battler comparisons, items, field, party probing.**

|Op|Handler|Words read|Behavior|
|---|---|---|---|
|79|`IfTargetIsTaunted` (2610)|jump|defender's `moveEffectsData.tauntedTurns != 0`|
|80|`IfTargetIsNotTaunted` (2620)|jump|`== 0`|
|81|`IfTargetIsPartner` (2630)|jump|attacker and defender on the same side (same parity)|
|82|`FlagBattlerIsType` (991)|battler, type|`calcTemp = MON_HAS_TYPE(battler, type) ? TRUE : FALSE` (a loader despite the name)|
|83|`CheckBattlerAbility` (1212)|battler, expected ability|see **Ability observation**; `calcTemp` = `AI_NOT_HAVE` (0) / `AI_HAVE` (1) / `AI_UNKNOWN` (2)|
|84|`IfActivatedFlashFire` (2641)|battler, jump|`moveEffectsData.flashFire` — the one-shot trigger flag set when Flash Fire absorbed a fire move|
|85|`IfHeldItemEqualTo` (1915)|battler, item, jump|own side (same parity as the attacker): the actual `heldItem`; opponent side: the mirrored `battlerHeldItems[battler]`; jump if equal to `item`|
|86|`IfFieldConditionsMask` (1936)|mask, jump|`battleCtx->fieldConditionsMask & mask`|
|87|`LoadSpikesLayers` (1948)|battler, side condition (SPIKES / TOXIC_SPIKES)|`calcTemp = sideConditions[battler's side].spikesLayers` / `.toxicSpikesLayers`|
|88|`IfAnyPartyMemberIsWounded` (1968)|battler, jump|any party member other than the active slot has `curHP < maxHP`|
|89|`IfAnyPartyMemberUsedPP` (1987)|battler, jump|any member other than the active has any move with `ppCur < max PP`|
|90|`LoadFlingPower` (2014)|battler|`Battler_ItemFlingPower(battleCtx, battler)` — power of the flingable held item|
|91|`LoadCurrentMovePP` (2024)|(none)|`battleMons[attacker].ppCur[moveSlot]`|
|92|`IfCanUseLastResort` (2030)|battler, jump|`moveEffectsData.lastResortCount >= Battler_CountMoves(...) - 1 && numKnownMoves > 1`|
|93|`LoadCurrentMoveClass` (2044)|(none)|`calcTemp = MOVE_DATA(AI_CONTEXT.move).class`|
|94|`LoadDefenderLastUsedMoveClass` (2050)|(none)|`calcTemp = MOVE_DATA(movePrevByBattler[defender]).class` — **no guard for 0xFFFF** (a defender that never moved would index out of range)|

**95-108 Speed, damage comparisons, party scouting, abilities.**

|Op|Handler|Words read|Behavior|
|---|---|---|---|
|95|`LoadBattlerSpeedRank` (2056)|battler|bubble-sorts all four battlers by effective speed (`BattleSystem_CompareBattlerSpeed(…, TRUE)`); `calcTemp` = the resolved battler's rank (0 = fastest)|
|96|`LoadBattlerTurnCount` (2096)|battler|`totalTurns - moveEffectsData.fakeOutTurnNumber` — turns this battler has been in the battle|
|97|`IfPartyMemberDealsMoreDamage` (2106)|varyDamage, jump|the active attacker's maximum move damage (its real in-battle moves / item / IVs / ability / embargo) vs each living bench member's maximum (that member's own moves / item / ability / IVs; embargo off); jump if any bench member exceeds|
|98|`IfHasSuperEffectiveMove` (2176)|jump|`AI_HasSuperEffectiveMove(attacker, TRUE)` — see below|
|99|`IfBattlerDealsMoreDamage` (2187)|battler, varyDamage, jump|the attacker's maximum move damage (all moves, `CalcAllDamage`) vs the resolved battler's **last-used move** (`CalcDamage` with that battler's held item / ability / IVs; roll = per-slot roll if `varyDamage`, else 100); jump if the battler's last move outruns the attacker's best|
|100|`SumPositiveStatStages` (2243)|battler|`Σ max(0, statBoosts[stat] − 6)` over the six stats (stage 7 → 1, stage 8 → 2 — the Punishment tiers)|
|101|`DiffStatStages` (2259)|battler, stat|`statBoosts[battler][stat] − statBoosts[attacker][stat]` (target − user)|
|102|`IfBattlerHasHigherStat` (2270)|battler, stat, jump|via `TrainerAI_GetStats` (2330-2367): HP → `curHP` (current, both sides); the other five → stage-adjusted effective values; jump if the *other* battler's stat is higher|
|103|`IfBattlerHasLowerStat` (2287)|same|lower|
|104|`IfBattlerHasEqualStat` (2304)|same|equal|
|105|`CheckIfHighestDamageWithPartner` (2369)|varyDamage|current move eligible (alt-power, or power > 1 and not a no-calc effect): the attacker's `CalcAllDamage` (baseline = its own move), plus the partner's in doubles; `calcTemp` = `AI_NOT_HIGHEST_DAMAGE` if any move of either battler of the pair outruns the baseline, else `AI_MOVE_IS_HIGHEST_DAMAGE`; ineligible → `AI_NO_COMPARISON_MADE`|
|106|`IfBattlerFainted` (2438)|battler, jump|asserts the operand is **neither** attacker nor defender; jump if the resolved battler's bit is set in `battleCtx->battlersSwitchingMask` (fainted or currently switching)|
|107|`IfBattlerNotFainted` (2454)|same|jump if the bit is not set|
|108|`LoadAbility` (2654)|battler|`Battler_Ability(battleCtx, battler)` — the raw ability, no suppression / guessing (contrast 41)|

**Ability observation (used by 41 and 83).** Own-side battlers use the real ability. Opponent battlers: the mirrored `battlerAbilities[battler]` if known (recorded when the mon entered, `battle_script.c:12108`; reset when it leaves, `battle_lib.c:7726`); else the mon's actual ability if it is Shadow Tag, Magnet Pull, or Arena Trap (self-announcing); else the species' ability if it has exactly one; if it has two, 41 coin-flips (`RandNext() & 1`) while 83 is asymmetric — if *neither* candidate is the expected ability it assumes ability 1, but if *either* is, it returns `ABILITY_NONE` (→ `AI_UNKNOWN`), i.e. it never confirms an expected ability from a two-ability guess. `MOVE_EFFECT_ABILITY_SUPPRESSED` in play makes both paths unknown. 83's mapping: unknown → `AI_UNKNOWN` (2), equals the expected ability → `AI_HAVE` (1), else → `AI_NOT_HAVE` (0).

**`AI_HasSuperEffectiveMove` (trainer_ai.c:3560-3624)** — used by 98 (with `TRUE`) and by the switching logic in §4. The target is the slot directly across (`GetBattlerType(battler) ^ 1` → `GetBattlerOfType`); if that defender's bit is set in `battlersSwitchingMask` the check is skipped. For each real move: type via `TrainerAI_MoveType`, effectiveness via `ApplyTypeChart(damage = 0)`; a `MOVE_STATUS_SUPER_EFFECTIVE` hit returns TRUE unconditionally if `flag` is set, else 90% (`RandNext() % 10 != 0`). In doubles the same check runs against the defender's partner (also switch-gated).

## §3 Per-flag breakdown

The per-trainer `aiMask` (§6) selects which flag scripts run: the
shift-loop in `TrainerAI_MainSingles`/`MainDoubles` (§1) runs one
script per set bit, in ascending bit order 0 → 31; for bit k the
entry word is `aiScriptTemp[k]` — the first 32 words of
`gTrainerAITable`, a `FlagTable` in `ai_flags.txt` order, each pointing
at that flag's `*_Main` label (all 18 `AI_FLAG_UNUSED_*` entries point
at `Terminate`). Every `*_Main` begins by checking
`IfTargetIsPartner` (a flag never scores the partner — the partner is
scored by its own loop; the exceptions, `TagStrategy` and `CheckHP`,
branch to a partner-specific sub-routine) and ends in `PopOrEnd`.

Flag values come from `generated/ai_flags.txt` (34 lines): line N
(N ≥ 2) defines value `1 << (N−2)`; line 1 is `AI_FLAG_NONE` (0).

|Flag|Value|Routine (script.s lines)|
|---|---|---|
|`AI_FLAG_NONE`|0|—|
|`AI_FLAG_BASIC`|1|`Basic_Main` (52-1622)|
|`AI_FLAG_EVAL_ATTACK`|2|`EvalAttack_Main` (6350-6413)|
|`AI_FLAG_EXPERT`|4|`Expert_Main` (1623-6349)|
|`AI_FLAG_SETUP_FIRST_TURN`|8|`SetupFirstTurn_Main` (6414-6495)|
|`AI_FLAG_RISKY`|16|`Risky_Main` (6518-6560)|
|`AI_FLAG_PRIORITIZE_EXTREMES`|32|`PrioritizeExtremes_Main` (6496-6517)|
|`AI_FLAG_BATON_PASS`|64|`BatonPass_Main` (6561-6633)|
|`AI_FLAG_TAG_STRATEGY`|128|`TagStrategy_Main` (6634-7692)|
|`AI_FLAG_CHECK_HP`|256|`CheckHP_Main` (7693-7975)|
|`AI_FLAG_WEATHER`|512|`Weather_Main` (7976-8017)|
|`AI_FLAG_HARRASSMENT`|1024|`Harrassment_Main` (8018-8069)|
|`AI_FLAG_UNUSED_11` … `AI_FLAG_UNUSED_28`|2048 … 268435456|all → `Terminate`|
|`AI_FLAG_ROAMING_POKEMON`|536870912|`RoamingPokemon_Main` (8070-8087)|
|`AI_FLAG_SAFARI`|1073741824|`Safari_Main` (8088-8092)|
|`AI_FLAG_CATCH_TUTORIAL`|2147483648|`CatchTutorial_Main` (8093-8101)|
|`AI_FLAG_ALL`|4294967296 = `1 << 32`|out of range of the mask — unused|
|—|—|`Terminate` (8102-8105): `PopOrEnd`|

### `AI_FLAG_BASIC` — `Basic_Main` (52-1622)

The basic scorer. Never targets the partner (54). Per-move flow:

1. **OHKO skip** (56-58): `FISSURE`/`HORN_DRILL` jump directly to
   `Basic_CheckForImmunity` — an OHKO's "damage" is excluded from the
   max-damage comparison, so only the immunity checks apply.
2. **Damage score** (60-63): `FlagMoveDamageScore USE_MAX_DAMAGE`; if no
   eligible comparison could be made (`AI_NO_COMPARISON_MADE`) →
   `Basic_CheckSoundproof`.
3. **`Basic_CheckForImmunity`** (65-113): effectiveness class
   `TYPE_MULTI_IMMUNE` → **-10** (68); attacker's Mold Breaker → skip
   the ability checks (70); defender abilities, each loading the
   move's type with -12 on a match: Volt Absorb / Motor Drive
   (electric, 81-84), Water Absorb (water, 86-89), Flash Fire (fire,
   91-94), Wonder Guard (96-99: no penalty at 2x/4x effectiveness,
   else -12), Levitate (ground, 101-104) **and** Levitate (water,
   106-109 — source note at 78: "BUG: This line should branch on Dry
   Skin rather than Levitate"; a Levitate target can therefore take
   both -12 penalties in one pass). Falls into
   `Basic_NoImmunityAbility` (111-113), which re-runs the damage flag
   and continues to the Soundproof check.
4. **`Basic_CheckSoundproof`** (115-131): skipped when the defender
   lacks Soundproof or the attacker has Mold Breaker; otherwise -10
   when the move is one of the 11 sound moves: Growl, Roar, Sing,
   Supersonic, Screech, Snore, Uproar, Metal Sound, Grass Whistle, Bug
   Buzz, Chatter.
5. **`Basic_ScoreMoveEffect`** (133-286): 152
   `IfCurrentMoveEffectEqualTo` entries (first match wins — a move has
   exactly one effect), then falls into `PopOrEnd` (286). Effect →
   check-routine mapping (in the stat-stage families each effect maps
   to the routine for the stat it affects):

|Check routine (line)|`BATTLE_EFFECT_*` (from `moves.h`)|
|---|---|
|`CheckCannotSleep` (288)|STATUS_SLEEP, STATUS_SLEEP_NEXT_TURN|
|`CheckCannotExplode` (297)|HALVE_DEFENSE (Self-Destruct family)|
|`CheckDreamEater` (329)|RECOVER_DAMAGE_SLEEP|
|`CheckHighStatStage_Attack` (343)|ATK_UP, ATK_UP_2|
|`CheckHighStatStage_Defense` (353)|DEF_UP, DEF_UP_2, DEF_UP_DOUBLE_ROLLOUT_POWER|
|`CheckHighStatStage_Speed` (363)|SPEED_UP, SPEED_UP_2|
|`CheckHighStatStage_SpAttack` (374)|SP_ATK_UP, SP_ATK_UP_2|
|`CheckHighStatStage_SpDefense` (384)|SP_DEF_UP, SP_DEF_UP_2|
|`CheckHighStatStage_Accuracy` (394)|ACC_UP, ACC_UP_2|
|`CheckHighStatStage_Evasion` (407)|EVA_UP, EVA_UP_2, EVA_UP_2_MINIMIZE|
|`CheckLowStatStage_Attack` (428)|ATK_DOWN, ATK_DOWN_2|
|`CheckLowStatStage_Defense` (434)|DEF_DOWN, DEF_DOWN_2|
|`CheckLowStatStage_Speed` (438)|SPEED_DOWN, SPEED_DOWN_2|
|`CheckLowStatStage_SpAttack` (445)|SP_ATK_DOWN, SP_ATK_DOWN_2|
|`CheckLowStatStage_SpDefense` (449)|SP_DEF_DOWN, SP_DEF_DOWN_2|
|`CheckLowStatStage_Accuracy` (453)|ACC_DOWN, ACC_DOWN_2, **EVA_DOWN_2** (176)|
|`CheckLowStatStage_Evasion` (462)|EVA_DOWN, EVA_DOWN_2, **ACC_DOWN_2** (177)|
|`CheckStatStageImbalance` (475)|RESET_STAT_CHANGES, COPY_STAT_CHANGES, SWAP_STAT_CHANGES|
|`CheckNonStandardDamageOrChargeTurn` (572)|BIDE, CHARGE_TURN_HIGH_CRIT, HALVE_HP, 40_DAMAGE_FLAT, RECHARGE_AFTER, LEVEL_DAMAGE_FLAT, RANDOM_DAMAGE_1_TO_150_LEVEL, COUNTER, INCREASE_POWER_WITH_LESS_HP, POWER_BASED_ON_FRIENDSHIP, RANDOM_POWER_MAYBE_HEAL, POWER_BASED_ON_LOW_FRIENDSHIP, 20_DAMAGE_FLAT, RANDOM_POWER_BASED_ON_IVS, MIRROR_COAT, CHARGE_TURN_DEF_UP, HIT_LAST_WHIFF_IF_HIT, LOWER_OWN_ATK_AND_DEF, SET_HP_EQUAL_TO_USER, INCREASE_POWER_WITH_WEIGHT, POWER_BASED_ON_LOW_SPEED, HIGHER_POWER_WHEN_LOW_PP, INCREASE_POWER_WITH_MORE_HP, INCREASE_POWER_WITH_MORE_STAT_UP|
|`CheckCanForceSwitch` (499)|FORCE_SWITCH|
|`CheckCanRecoverHP` (511)|RESTORE_HALF_HP, HEAL_HALF_MORE_IN_SUN, UNUSED_133, UNUSED_134, UNUSED_157, HEAL_HALF_REMOVE_FLYING_TYPE|
|`CheckCannotPoison` (519)|STATUS_BADLY_POISON, STATUS_POISON|
|`CheckAlreadyUnderLightScreen` (549)|SET_LIGHT_SCREEN|
|`CheckOHKOWouldFail` (554)|ONE_HIT_KO|
|`CheckAlreadyUnderMist` (587)|PREVENT_STAT_REDUCTION|
|`CheckAlreadyPumpedUp` (592)|CRIT_UP_2|
|`CheckCannotConfuse` (597)|STATUS_CONFUSE, ATK_UP_2_STATUS_CONFUSION, SP_ATK_UP_CAUSE_CONFUSION|
|`CheckAlreadyUnderReflect` (607)|SET_REFLECT|
|`CheckCannotParalyze` (612)|STATUS_PARALYZE|
|`CheckCannotSubstitute` (633)|SET_SUBSTITUTE|
|`CheckCannotLeechSeed` (639)|STATUS_LEECH_SEED|
|`CheckCannotDisable` (650)|DISABLE|
|`CheckCannotEncore` (655)|ENCORE|
|`CheckAttackerAsleep` (660)|DAMAGE_WHILE_ASLEEP, USE_RANDOM_LEARNED_MOVE_SLEEP|
|`CheckLockOn` (665)|NEXT_ATTACK_ALWAYS_HITS|
|`CheckMeanLook` (674)|PREVENT_ESCAPE|
|`CheckNightmare` (321)|STATUS_NIGHTMARE|
|`CheckCurse` (679)|CURSE|
|`CheckSpikes` (707)|SET_SPIKES|
|`CheckForesight` (715)|FORESIGHT|
|`CheckPerishSong` (720)|ALL_FAINT_3_TURNS|
|`CheckSandstorm` (725)|WEATHER_SANDSTORM|
|`CheckCannotAttract` (731)|INFATUATE|
|`CheckAlreadyUnderSafeguard` (754)|PREVENT_STATUS|
|`CheckMagnitude` (566)|PSYWAVE|
|`CheckBatonPass` (780)|PASS_STATS_AND_STATUS|
|`CheckRainDance` (786)|WEATHER_RAIN|
|`CheckSunnyDay` (803)|WEATHER_SUN|
|`CheckBellyDrum` (335)|MAX_ATK_LOSE_HALF_MAX_HP|
|`CheckFutureSight` (823)|HIT_IN_3_TURNS|
|`ScoreMinus10` (1591, direct)|FLEE_FROM_WILD_BATTLE|
|`CheckFirstTurnInBattle` (829)|ALWAYS_FLINCH_FIRST_TURN_ONLY|
|`CheckMaxStockpile` (835)|STOCKPILE|
|`CheckCanSpitUpOrSwallow` (841)|SPIT_UP, SWALLOW|
|`CheckHail` (853)|WEATHER_HAIL|
|`CheckTorment` (874)|TORMENT|
|`CheckCannotBurn` (879)|STATUS_BURN|
|`CheckMemento` (759)|FAINT_AND_ATK_SP_ATK_DOWN_2|
|`CheckHelpingHand` (892)|BOOST_ALLY_POWER_BY_50_PERCENT|
|`CheckCanRemoveItem` (898)|SWITCH_HELD_ITEMS, REMOVE_HELD_ITEM|
|`CheckAlreadyIngrained` (906)|GROUND_TRAP_USER_CONTINUOUS_HEAL|
|`CheckCanRecycle` (911)|RECYCLE|
|`CheckCanImprison` (917)|MAKE_SHARED_MOVES_UNUSEABLE|
|`CheckCanRefreshStatus` (923)|HEAL_STATUS|
|`CheckCanMudSport` (928)|HALVE_ELECTRIC_DAMAGE|
|`CheckTickle` (933)|ATK_DEF_DOWN|
|`CheckCosmicPower` (949)|DEF_SPD_UP|
|`CheckBulkUp` (965)|ATK_DEF_UP|
|`CheckWaterSport` (981)|HALVE_FIRE_DAMAGE|
|`CheckCalmMind` (986)|SP_ATK_SP_DEF_UP|
|`CheckDragonDance` (1002)|ATK_SPD_UP|
|`CheckCamouflage` (1021)|CAMOUFLAGE|
|`CheckGravityActive` (1026)|GRAVITY|
|`CheckMiracleEye` (1031)|IGNORE_EVATION_REMOVE_DARK_IMMUNE|
|`CheckHealingWish` (1036)|FAINT_AND_FULL_HEAL_NEXT_MON|
|`CheckNaturalGift` (1053)|NATURAL_GIFT|
|`CheckTailwind` (1128)|DOUBLE_SPEED_3_TURNS|
|`CheckAcupressure` (1135)|RANDOM_STAT_UP_2|
|`CheckMetalBurst` (1160)|METAL_BURST|
|`CheckEmbargo` (1184)|PREVENT_ITEM_USE|
|`CheckFling` (1199)|FLING|
|`CheckCanPsychoShift` (1301)|TRANSFER_STATUS|
|`CheckHealBlock` (1353)|PREVENT_HEALING|
|`CheckPowerTrick` (1358)|SWAP_ATK_DEF|
|`CheckGastroAcid` (1363)|SUPPRESS_ABILITY|
|`CheckLuckyChant` (1378)|PREVENT_CRITS|
|`CheckCopycat` (1383)|USE_LAST_USED_MOVE|
|`CheckPowerSwap` (1392)|SWAP_ATK_SP_ATK_STAT_CHANGES|
|`CheckGuardSwap` (1406)|SWAP_DEF_SP_DEF_STAT_CHANGES|
|`CheckLastResort` (1420)|FAIL_IF_NOT_USED_ALL_OTHER_MOVES|
|`CheckWorrySeed` (1428)|SET_ABILITY_TO_INSOMNIA|
|`CheckToxicSpikes` (1445)|TOXIC_SPIKES|
|`CheckAquaRing` (1456)|RESTORE_HP_EVERY_TURN|
|`CheckMagnetRise` (1461)|GIVE_GROUND_IMMUNITY|
|`CheckDefog` (1476)|REMOVE_HAZARDS_SCREENS_EVA_DOWN|
|`CheckTrickRoom` (1501)|TRICK_ROOM|
|`CheckCaptivate` (1508)|SP_ATK_DOWN_2_OPPOSITE_GENDER|
|`CheckStealthRock` (1540)|STEALTH_ROCK|
|`CheckLunarDance` (1549)|FAINT_FULL_RESTORE_NEXT_MON|

Note the 176-177 swap: `EVA_DOWN_2` routes to
`CheckLowStatStage_Accuracy` and `ACC_DOWN_2` to
`CheckLowStatStage_Evasion` — the two effect families are cross-wired
(the same swap exists in the Expert dispatch at 1675-76). The
`BATTLE_EFFECT_SUPRESS_ABILITY` spelling is as in `moves.h` (a typo in
the generated constant name).
**Check routines.** Internal stat stages are 0-12 with 6 neutral
(`battle_script.h:4-6`), so `12` = +6, `8` = +2.

- **`CheckCannotSleep`** (288-295): -10 if the defender is already
  statused, its side has Safeguard, or it has Insomnia / Vital Spirit.
- **`CheckCannotExplode`** (297-319): `TYPE_MULTI_IMMUNE` → -10 (299);
  defender has Damp and the attacker lacks Mold Breaker → -10 (305);
  last-mon logic (307-316) via `CountAlivePartyBattlers`, which
  actually counts *not-alive* members (0 = last mon): attacker not on
  its last mon → no penalty; attacker on last mon and defender has
  more → -10; both on their last mon → -1 (`ScoreMinus1`).
- **`CheckNightmare`** (321-327): -10 if already under Nightmare, -8
  if not asleep, -10 for Magic Guard.
- **`CheckDreamEater`** (329-333): -8 if the target is not asleep;
  -10 if `TYPE_MULTI_IMMUNE`.
- **`CheckBellyDrum`** (335-337): -10 when the attacker's HP% < 51.
- **`CheckHighStatStage_*`** (343-418, eight routines): -10 when the
  Simple attacker's stage for that stat is internal `> 8` (i.e. +3 or
  more); without Simple, -10 when the stage is `12` (+6). The source
  comment says "+2" for the Simple case but the test is strictly
  greater than 8. Speed first applies -10 if Trick Room is active
  (364); Accuracy and Evasion additionally apply -10 for No Guard on
  both the defender (395/408) and the attacker (398/411).
- **`CheckLowStatStage_*`** (428-473, eight routines): -10 when the
  target's stage for that stat is `0` (-6); then ability checks:
  Attack: Hyper Cutter -10 (431); Speed: Trick Room -10 (439), Speed
  Boost -10 (441-442, via `CheckBattlerAbility … AI_HAVE`); Accuracy:
  attacker No Guard -10 (456), Keen Eye -10 (458), defender No Guard
  -10 (459); Evasion: attacker No Guard -10 (465), defender No Guard
  -10 (467); all eight then share `Basic_CheckClearBodyEffect`
  (469-473): Clear Body -10, White Smoke -10.
- **`CheckStatStageImbalance`** (475-497): "imbalance" = the attacker
  has any stage below internal 6 or the target has any stage above
  6; if that holds → no penalty, otherwise -10.
- **`CheckCanForceSwitch`** (499-509): -10 if the defender is on its
  last mon (count 0); attacker Mold Breaker skips the next; defender
  Suction Cups → -10.
- **`CheckCanRecoverHP`** (511-517): -8 **only when the attacker is at
  full HP** (HP% ≠ 100 → no penalty).
- **`CheckCannotPoison`** (519-547): -10 for each of defender
  Steel/Poison type, Immunity / Magic Guard / Poison Heal, Leaf Guard
  under SUNNY, Hydration under RAINING, any status, and Safeguard.
- **`CheckAlreadyUnderLightScreen`** (549-552): attacker's side
  Light Screen → -8.
- **`CheckOHKOWouldFail`** (554-564): `TYPE_MULTI_IMMUNE` → -10 (556);
  defender Sturdy without attacker Mold Breaker → -10 (558-560);
  attacker level < defender level → -10 (563).
- **`CheckMagnitude`** (566-571) — **quirk**: line 568
  `IfLoadedEqualTo ABILITY_MOLD_BREAKER` tests the *stale*
  `calcTemp` left by the earlier `FlagMoveDamageScore` (0/1/2 — never
  the ability enum value), so the Mold Breaker exception in the
  comment (567) is dead code; the check then loads the defender's
  ability and -10 for Levitate. The documented intent (skip when the
  *attacker* has Mold Breaker) is not what executes.
- **`CheckNonStandardDamageOrChargeTurn`** (572-585): shared by the
  24 charge-turn / flat-damage / counter / variable-power effects:
  `TYPE_MULTI_IMMUNE` → -10 (575); defender Wonder Guard → -10 unless
  the attacker has Mold Breaker or the move is 2x/4x (577-582).
- **`CheckAlreadyUnderMist`** (587-590): attacker's side Mist → -8.
- **`CheckAlreadyPumpedUp`** (592-595): attacker already has the
  Focus Energy volatile (CRIT_UP_2) → -10.
- **`CheckCannotConfuse`** (597-605): -5 if already confused (note:
  -5, not -10); -10 for Own Tempo or Safeguard.
- **`CheckAlreadyUnderReflect`** (607-610): attacker's side Reflect
  → -8.
- **`CheckCannotParalyze`** (612-631): -10 for `TYPE_MULTI_IMMUNE`,
  Limber, Magic Guard; without attacker Mold Breaker, if the move is
  specifically THUNDER_WAVE: -10 for Motor Drive or Volt Absorb
  (620-626); -10 for any status or Safeguard.
- **`CheckCannotSubstitute`** (633-637): -8 if a Substitute is
  already up; -10 if the attacker's HP < 26%.
- **`CheckCannotLeechSeed`** (639-648): -10 if already seeded; -10
  for each defender Grass type slot; -10 for Magic Guard.
- **`CheckCannotDisable`** (650-653): -8 if the move is already
  disabled (volatile CHECK_DISABLE).
- **`CheckCannotEncore`** (655-658): -8 if already encoreed.
- **`CheckAttackerAsleep`** (660-663): -8 if the attacker is not
  asleep (Snore / Sleep Talk).
- **`CheckLockOn`** (665-672): -10 if the target is already
  lock-on'd, or either battler has No Guard.
- **`CheckMeanLook`** (674-677): -10 if the target is already under
  Mean Look.
- **`CheckCurse`** (679-705): ghost-type user →
  `Basic_CheckCurse_GhostType` (700-705): -10 if the target already
  has the CURSE volatile, -10 for Magic Guard; non-ghost: with
  Simple, -10 for each of ATK/DEF internal `> 8`; without Simple,
  -10 for ATK `== 12` and -8 for DEF `== 12` (696-697).
- **`CheckSpikes`** (707-713): -10 at 3 layers on the defender's
  side; -10 if the defender is on its last mon.
- **`CheckForesight`** (715-718): -10 if the target is already under
  the FORESIGHT volatile.
- **`CheckPerishSong`** (720-723): -10 if the target already has the
  perish effect.
- **`CheckSandstorm`** (725-729): -8 if the current weather is
  already SANDSTORM.
- **`CheckCannotAttract`** (731-752): -10 if already infatuated or
  the defender has Oblivious; gender: attacker male requires a female
  defender, attacker female requires a male (opposite sex → no
  penalty); a genderless attacker gets -10 outright (739).
- **`CheckAlreadyUnderSafeguard`** (754-757): attacker's side
  Safeguard → -8.
- **`CheckMemento`** (759-778): without attacker Mold Breaker, -10
  for defender Clear Body / White Smoke (764-766); -10 if the target's
  ATK stage is `0`, -8 if its SP_ATK stage is `0`; -10 if the
  attacker is on its last mon (the move kills the user).
- **`CheckBatonPass`** (780-784): -10 if the attacker is on its last
  mon.
- **`CheckRainDance`** (786-801): an attacker with Swift Swim or
  Hydration skips the defender check; defender with Hydration and any
  status → -8 (793-795); current weather already RAINING → -8.
- **`CheckSunnyDay`** (803-821): an attacker with Flower Gift / Leaf
  Guard / Solar Power skips the defender check; defender with
  Hydration and any status → **-10** (813-815; source note at 811-812:
  "Why does this consider Hydration? This is clearly a bug, but what
  was the intention?"); current weather already SUNNY → -8.
- **`CheckFutureSight`** (823-827): -12 for a FUTURE_SIGHT side
  condition on the defender's side **and** again on the attacker's
  side (up to -24 combined).
- **`CheckFirstTurnInBattle`** (829-833): -10 if it is not the
  attacker's first turn in the battle.
- **`CheckMaxStockpile`** (835-839): -10 when the stockpile count is
  already 3.
- **`CheckCanSpitUpOrSwallow`** (841-851): `TYPE_MULTI_IMMUNE` → -10
  — the source note (843-844) acknowledges this means Swallow is
  never chosen against a Ghost even though it would still work; -10
  when the stockpile count is 0; if the move is SWALLOW it also runs
  `CheckCanRecoverHP` (850).
- **`CheckHail`** (853-872): -8 if the current weather is HAILING
  (856); defender Ice Body → -8 (861); **attacker** Ice Body →
  **+8** (869), which cancels the defender's penalty — source note at
  863-866: "This feels like a bug of misintention; … such an
  attacker can only have a disincentive undone".
- **`CheckTorment`** (874-877): -10 if the target is already under
  Torment.
- **`CheckCannotBurn`** (879-890): -10 for Water Veil, Magic Guard,
  any status, each defender Fire type slot, and Safeguard.
- **`CheckHelpingHand`** (892-896): -10 when the battle is not
  doubles (the boost does nothing in singles).
- **`CheckCanRemoveItem`** (898-904): -10 if the defender has Sticky
  Hold; -10 if the defender holds no item.
- **`CheckAlreadyIngrained`** (906-909): -10 if the attacker already
  has the INGRAIN move effect.
- **`CheckCanRecycle`** (911-915): -10 when
  `LoadRecycleItem ATTACKER` is ITEM_NONE (no item to recycle).
- **`CheckCanImprison`** (917-921): -10 if the attacker is already
  under MOVE_EFFECT_IMPRISON; -10 if the defender is under
  MOVE_EFFECT_IMPRISONED (note the asymmetric effect names).
- **`CheckCanRefreshStatus`** (923-926): -10 unless the attacker is
  in a status that Refresh can cure
  (`IfNotStatus ATTACKER, MON_CONDITION_FACADE_BOOST`).
- **`CheckCanMudSport`** (928-931): -10 if already under MUD_SPORT.
- **`CheckTickle`** (933-947): without attacker Mold Breaker, -10 for
  defender Clear Body / White Smoke (938-940); -10 if the target's
  ATK stage is `0`; -8 if its DEF stage is `0`.
- **`CheckCosmicPower`** (949-963) / **`CheckBulkUp`** (965-979) /
  **`CheckCalmMind`** (986-1000) / **`CheckDragonDance`** (1002-1019):
  the two-stat boosters. With Simple: -10 for each of the two stats
  whose stage is internal `> 8`; without Simple: -10 for the first
  stat at `12` and **-8** for the second at `12` (961-62, 977-78,
  998-99, 1017-18). DragonDance additionally applies -10 for Trick
  Room first (1004).
- **`CheckWaterSport`** (981-984): -10 if already under WATER_SPORT.
- **`CheckCamouflage`** (1021-1024): -10 if the attacker already has
  the CAMOUFLAGE effect.
- **`CheckGravityActive`** (1026-1029): -10 if GRAVITY is on the
  field.
- **`CheckMiracleEye`** (1031-1034): -10 if the target is already
  under the MIRACLE_EYE effect.
- **`CheckHealingWish`** (1036-1051): base **-20** (1038); -10 more
  if the attacker is on its last mon (1041-42); -10 more if no party
  member is statused or wounded (`IfPartyMemberStatus ANY` /
  `IfAnyPartyMemberIsWounded` both failing, 1046-48) — otherwise no
  extra penalty.
- **`CheckNaturalGift`** (1053-1126): -10 unless the attacker holds a
  berry in the eligible table (1061-1126, 64 `TableEntry` rows: Cheri
  through Rowap, plus TABLE_END); `TYPE_MULTI_IMMUNE` → -10.
- **`CheckTailwind`** (1128-1133): -10 for Trick Room active; -10 if
  Tailwind is already on the attacker's side.
- **`CheckAcupressure`** (1135-1158): with Simple, -10 for each of
  the seven stages (ATK/DEF/SPD/SPATK/SPDEF/EVA/ACC) internal `> 8`
  (1151-57); without Simple, -10 for each at `12` (1140-46).
- **`CheckMetalBurst`** (1160-1182): `TYPE_MULTI_IMMUNE` → -10
  (1162); defender Stall → -10 (1168); defender holding Shiny Stone
  → -10 (1169) — source BUG notes (1164-66): "This should use the
  command LoadHeldItemEffect to check for the Lagging Tail effect";
  attacker with Stall or Shiny Stone → no penalty (1174-76, same BUG
  note at 1171-73); attacker faster than the target → -10 (1179).
- **`CheckEmbargo`** (1184-1197): -10 if the target is already under
  Embargo (1186); no recyclable item on the target's side → no
  penalty (1189-90); FRONTIER battle → -10 (1193-94).
- **`CheckFling`** (1199-1299): `TYPE_MULTI_IMMUNE` → -10 (1201);
  `LoadFlingPower ATTACKER` < 10 → -10 (1204-05 — no usable held
  item); attacker Multitype → -10 (1208-09); then branches on
  `LoadHeldItemEffect ATTACKER`:
  - poison items (`HOLD_EFFECT_PSN_USER` / `STRENGTHEN_POISON`,
    table 1288-91) → `Basic_FlingPoison` (1218-1251): if the defender
    is immune (Safeguard on its side, any status, Poison/Steel type,
    Immunity / Poison Heal / Magic Guard — 1219-1232) jump to
    `Basic_FlingPoison_AttackerChecks`: -5 each for attacker-side
    conditions that would make the item inert (attacker side
    Safeguard, any status, Poison/Steel type, Klutz / Immunity /
    Poison Heal / Magic Guard / Guts, 1236-1248), then a flat
    **+3** (1250). If the defender is not immune, each check fails
    and it exits with **no adjustment** (1233).
  - burn item (`HOLD_EFFECT_BRN_USER`, 1293-95) →
    `Basic_FlingBurn` (1253-1278): same shape — defender-side
    immunity set (Safeguard, any status, Fire type, Magic Guard,
    Water Veil — 1254-62); attacker-side set (Safeguard, any status,
    Fire, Klutz, Magic Guard, Water Veil, Guts — 1265-76); -5 each +
    flat +3 (1277); no adjustment when the defender is not immune.
  - paralyze item (`HOLD_EFFECT_PIKA_SPATK_UP`, 1297-99) →
    `Basic_FlingParalyze` (1280-86): defender-side only, -5 each for
    Safeguard on the defender's side, any status, Limber.
- **`CheckCanPsychoShift`** (1301-1351): -10 if the attacker is not
  statused (1304), if the target is statused (1305), or if the
  defender's side has Safeguard (1308); then per the attacker's
  condition: poison (ANY_POISON) → `Basic_PsychoShift_Poison`
  (1316-32: -10 for attacker Poison Heal, each defender
  Poison/Steel type, defender Immunity / Poison Heal / Magic Guard);
  burn → `Basic_PsychoShift_Burn` (1334-43: each defender Fire type,
  Magic Guard, Water Veil); paralysis →
  `Basic_PsychoShift_Paralysis` (1345-48: defender Limber).
- **`CheckHealBlock`** (1353-56): -10 if the target is already under
  Heal Block.
- **`CheckPowerTrick`** (1358-61): -10 if the attacker is already
  under Power Trick.
- **`CheckGastroAcid`** (1363-76): -10 if the target's ability is
  already suppressed (1365); -10 for each "worthless" defender
  ability in {Multitype, Truant, Slow Start, Stench, Run Away,
  Pickup, Honey Gather} (1368-75).
- **`CheckLuckyChant`** (1378-81): -10 if Lucky Chant is already on
  the attacker's side.
- **`CheckCopycat`** (1383-90): no penalty except on turn 0 (the
  opponent has not moved yet) when the attacker is faster — then -10
  (1385-87).
- **`CheckPowerSwap`** (1392-1404) / **`CheckGuardSwap`**
  (1406-1418): `DiffStatStages DEFENDER, ATK(or DEF)` = defender's
  stage minus attacker's; if `< 1` (the swap would not strictly
  improve that stat for the attacker) the routine jumps to the
  second stat's sub-check (`_SpAttack` at 1396 / `_SpDefense` at
  1410, shared labels reused across the two routines); if that is
  also `< 1` → **-10** (the swap is net-negative on both stats),
  otherwise no penalty.
- **`CheckLastResort`** (1420-26) — **code differs from comment**:
  the comment (1421) says "score -10 if the attacker has yet to use
  all of its other moves"; the code (1422-23) is the inverse —
  `IfCanUseLastResort` true (another move is still usable) → no
  penalty; false (LastResort is the only usable move) → -10.
- **`CheckWorrySeed`** (1428-43): -10 for each of defender Truant /
  Insomnia / Vital Spirit / Multitype (1431-34); if the defender is
  not asleep, or it knows SLEEP_TALK or SNORE → no penalty (1437-39);
  otherwise (asleep, no way to act) → -10 (1440).
- **`CheckToxicSpikes`** (1445-54): -10 at 2 layers on the
  defender's side; -10 if the defender is on its last mon; ends in
  a double `PopOrEnd` (1453-54, a no-op quirk).
- **`CheckAquaRing`** (1456-59): -10 if the attacker already has the
  AQUA_RING effect.
- **`CheckMagnetRise`** (1461-74): -10 if already risen; -10 if the
  attacker has Levitate; -10 for each attacker Flying type slot.
- **`CheckDefog`** (1476-99): no penalty when the target's evasion is
  not at internal `0`, the defender's side has Light Screen or
  Reflect, or the weather is DEEP_FOG (1479-85); -10 if the defender
  is on its last mon (1488-89); -10 when the defender's side has
  none of Spikes / Stealth Rock / Toxic Spikes (1491-96).
- **`CheckTrickRoom`** (1501-06): -10 if the attacker is faster than
  the target (1504); a **speed tie also scores -10** (1505 — comment
  1503-04: ties are treated as faster).
- **`CheckCaptivate`** (1508-38): without attacker Mold Breaker, -10
  for defender Oblivious / Clear Body / White Smoke (1513-16);
  gender must match the target's sex requirement — same sex or
  genderless attacker → -10 (1523-33); -10 if the target's SP_ATK
  stage is `< 1` (i.e. at or below +0, 1537).
- **`CheckStealthRock`** (1540-47): -10 if Stealth Rock is already on
  the defender's side; -10 if the defender is on its last mon.
- **`CheckLunarDance`** (1549-65): base **-20** (1551); -10 more if
  the attacker is on its last mon (1554-55); -10 more if no party
  member is wounded, statused, or fully PP-depleted
  (`IfAnyPartyMemberIsWounded` / `IfPartyMemberStatus ANY` /
  `IfAnyPartyMemberUsedPP`, 1559-62) — otherwise no extra penalty.

**Shared score labels** (1567-1621): `ScoreMinus1/2/3/5/6/8/10/12/30`
and `ScorePlus1/2/3/5/10` — each is `AddToMoveScore ±N` followed by
`PopOrEnd`. `ScoreMinus6` is marked `// unused` (1583) in the source.
### `AI_FLAG_EXPERT` — `Expert_Main` (1623-6349)

The "expert" scorer — a move-by-move jump table of special-case
logic. Never targets the partner (1624-25, comment: "This flag will
never target its partner."). 178 `IfCurrentMoveEffectEqualTo` entries
(1628-1805, first match wins) dispatch to per-move check routines;
anything unmatched falls to `PopOrEnd` (1808, "All other moves have
no additional logic"). Grouped by target routine:

|`BATTLE_EFFECT_*` (from `moves.h`)|Routine|
|---|---|
|STATUS_SLEEP|`Expert_StatusSleep`|
|RECOVER_HALF_DAMAGE_DEALT|`Expert_DrainMove`|
|HALVE_DEFENSE (1630), FAINT_AND_ATK_SP_ATK_DOWN_2 (1725)|`Expert_Explosion`|
|RECOVER_DAMAGE_SLEEP|`Expert_DreamEater`|
|COPY_MOVE|`Expert_MirrorMove`|
|ATK_UP, ATK_UP_2|`Expert_StatusAttackUp`|
|DEF_UP, DEF_UP_2, ATK_DEF_UP (1750)|`Expert_StatusDefenseUp`|
|SPEED_UP, SPEED_UP_2|`Expert_StatusSpeedUp`|
|SP_ATK_UP, SP_ATK_UP_2|`Expert_StatusSpAttackUp`|
|SP_DEF_UP, SP_DEF_UP_2, DEF_SPD_UP (1749), SP_ATK_SP_DEF_UP (1753)|`Expert_StatusSpDefenseUp`|
|ACC_UP, ACC_UP_2|`Expert_StatusAccuracyUp`|
|EVA_UP, EVA_UP_2, EVA_UP_2_MINIMIZE (1699)|`Expert_StatusEvasionUp`|
|BYPASS_ACCURACY|`Expert_BypassAccuracyMove`|
|ATK_DOWN, ATK_DOWN_2|`Expert_StatusAttackDown`|
|DEF_DOWN, DEF_DOWN_2, ATK_DEF_DOWN (1748)|`Expert_StatusDefenseDown`|
|SPEED_DOWN, SPEED_DOWN_2|`Expert_StatusSpeedDown`|
|SP_ATK_DOWN, SP_ATK_DOWN_2|`Expert_StatusSpAttackDown`|
|SP_DEF_DOWN, SP_DEF_DOWN_2|`Expert_StatusSpDefenseDown`|
|ACC_DOWN, **EVA_DOWN_2** (1675)|`Expert_StatusAccuracyDown`|
|EVA_DOWN, **ACC_DOWN_2** (1676)|`Expert_StatusEvasionDown`|
|RESET_STAT_CHANGES|`Expert_Haze`|
|BIDE|`Expert_Bide`|
|FORCE_SWITCH|`Expert_ForceSwitch`|
|CONVERSION|`Expert_Conversion`|
|RESTORE_HALF_HP (1652), UNUSED_157 (1719), SWALLOW (1722), HEAL_HALF_REMOVE_FLYING_TYPE (1755)|`Expert_Recovery`|
|STATUS_BADLY_POISON, STATUS_LEECH_SEED|`Expert_ToxicLeechSeed`|
|SET_LIGHT_SCREEN|`Expert_LightScreen`|
|REST|`Expert_Rest`|
|ONE_HIT_KO|`Expert_OHKOMove`|
|CHARGE_TURN_HIGH_CRIT, CHARGE_TURN_HIGH_CRIT_FLINCH (1682), CHARGE_TURN_DEF_UP (1715), SKIP_CHARGE_TURN_IN_SUN (1716)|`Expert_ChargeTurnNoInvuln`|
|HALVE_HP|`Expert_SuperFang`|
|BIND_HIT (1659), PREVENT_ESCAPE (1698)|`Expert_BindingMove`|
|HIGH_CRITICAL (1660), HIGH_CRITICAL_BURN_HIT (1745), HIGH_CRITICAL_POISON_HIT (1751)|`Expert_HighCritical`|
|RECOIL_QUARTER (1661), RECOIL_THIRD (1744), RECOIL_BURN_HIT (1793), RECOIL_PARALYZE_HIT (1799), RECOIL_HALF (1803)|`Expert_RecoilMove`|
|STATUS_CONFUSE|`Expert_StatusConfuse`|
|ATK_UP_2_STATUS_CONFUSION|`Expert_Swagger`|
|LOWER_SPEED_HIT|`Expert_SpeedDownOnHit`|
|PRIORITY_NEG_1_BYPASS_ACCURACY|`Expert_VitalThrow`|
|SET_SUBSTITUTE|`Expert_Substitute`|
|RECHARGE_AFTER|`Expert_RechargeTurn`|
|DISABLE|`Expert_Disable`|
|COUNTER|`Expert_Counter`|
|ENCORE|`Expert_Encore`|
|AVERAGE_HP|`Expert_PainSplit`|
|DAMAGE_WHILE_ASLEEP|`Expert_Nightmare`|
|NEXT_ATTACK_ALWAYS_HITS|`Expert_LockOn`|
|USE_RANDOM_LEARNED_MOVE_SLEEP|`Expert_SleepTalk`|
|KO_MON_THAT_DEFEATED_USER|`Expert_DestinyBond`|
|INCREASE_POWER_WITH_LESS_HP|`Expert_Reversal`|
|CURE_PARTY_STATUS|`Expert_HealBell`|
|STEAL_HELD_ITEM|`Expert_Thief`|
|CURSE|`Expert_Curse`|
|PROTECT|`Expert_Protect`|
|SET_SPIKES|`Expert_Spikes`|
|FORESIGHT|`Expert_Foresight`|
|SURVIVE_WITH_1_HP|`Expert_Endure`|
|PASS_STATS_AND_STATUS|`Expert_BatonPass`|
|HIT_BEFORE_SWITCH|`Expert_Pursuit`|
|HEAL_HALF_MORE_IN_SUN, UNUSED_133 (1708), UNUSED_134 (1709)|`Expert_Synthesis`|
|WEATHER_RAIN|`Expert_RainDance`|
|WEATHER_SUN|`Expert_SunnyDay`|
|MAX_ATK_LOSE_HALF_MAX_HP|`Expert_BellyDrum`|
|COPY_STAT_CHANGES|`Expert_PsychUp`|
|MIRROR_COAT|`Expert_MirrorCoat`|
|SKIP_CHARGE_TURN_IN_SUN (1717, **duplicate of 1716 — unreachable**)|`Expert_UnusedSolarbeam`|
|FLY (1718), DIVE (1794), DIG (1795), BOUNCE (1800)|`Expert_ChargeTurnWithInvuln`|
|ALWAYS_FLINCH_FIRST_TURN_ONLY|`Expert_FakeOut`|
|SPIT_UP|`Expert_SpitUp`|
|WEATHER_HAIL|`Expert_Hail`|
|SP_ATK_UP_CAUSE_CONFUSION|`Expert_Flatter`|
|DOUBLE_POWER_WHEN_STATUSED|`Expert_Facade`|
|HIT_LAST_WHIFF_IF_HIT|`Expert_FocusPunch`|
|DOUBLE_POWER_AND_CURE_PARALYSIS|`Expert_SmellingSalts`|
|SWITCH_HELD_ITEMS|`Expert_Trick`|
|COPY_ABILITY (1730), SWITCH_ABILITIES (1740)|`Expert_ChangeUserAbility`|
|GROUND_TRAP_USER_CONTINUOUS_HEAL|`Expert_Ingrain`|
|LOWER_OWN_ATK_AND_DEF|`Expert_Superpower`|
|APPLY_MAGIC_COAT|`Expert_MagicCoat`|
|RECYCLE|`Expert_Recycle`|
|DOUBLE_POWER_IF_HIT|`Expert_Revenge`|
|REMOVE_SCREENS|`Expert_BrickBreak`|
|REMOVE_HELD_ITEM|`Expert_KnockOff`|
|SET_HP_EQUAL_TO_USER|`Expert_Endeavor`|
|DECREASE_POWER_WITH_LESS_USER_HP|`Expert_WaterSpout`|
|MAKE_SHARED_MOVES_UNUSEABLE|`Expert_Imprison`|
|HEAL_STATUS|`Expert_Refresh`|
|STEAL_STATUS_MOVE|`Expert_Snatch`|
|USER_SP_ATK_DOWN_2|`Expert_Overheat`|
|HALVE_ELECTRIC_DAMAGE|`Expert_MudSport`|
|HALVE_FIRE_DAMAGE|`Expert_WaterSport`|
|ATK_SPD_UP|`Expert_DragonDance`|
|GRAVITY|`Expert_Gravity`|
|IGNORE_EVATION_REMOVE_DARK_IMMUNE|`Expert_MiracleEye`|
|DOUBLE_POWER_HEAL_SLEEP|`Expert_WakeUpSlap`|
|SPEED_DOWN_HIT|`Expert_HammerArm`|
|POWER_BASED_ON_LOW_SPEED|`Expert_GyroBall`|
|FAINT_AND_FULL_HEAL_NEXT_MON (1761), FAINT_FULL_RESTORE_NEXT_MON (1804)|`Expert_HealingWish`|
|DOUBLE_POWER_WHEN_BELOW_HALF|`Expert_Brine`|
|REMOVE_PROTECT|`Expert_Feint`|
|EAT_BERRY|`Expert_Pluck`|
|DOUBLE_SPEED_3_TURNS|`Expert_Tailwind`|
|RANDOM_STAT_UP_2|`Expert_Acupressure`|
|METAL_BURST|`Expert_MetalBurst`|
|SWITCH_HIT|`Expert_UTurn`|
|DEF_SPD_DOWN_HIT|`Expert_CloseCombat`|
|DOUBLE_POWER_IF_MOVING_SECOND|`Expert_Payback`|
|DOUBLE_POWER_IF_TARGET_HIT|`Expert_Assurance`|
|PREVENT_ITEM_USE|`Expert_Embargo`|
|FLING|`Expert_Fling`|
|TRANSFER_STATUS|`Expert_PsychoShift`|
|HIGHER_POWER_WHEN_LOW_PP|`Expert_TrumpCard`|
|PREVENT_HEALING|`Expert_HealBlock`|
|INCREASE_POWER_WITH_MORE_HP|`Expert_WringOut`|
|SWAP_ATK_DEF|`Expert_PowerTrick`|
|SUPRESS_ABILITY|`Expert_GastroAcid`|
|PREVENT_CRITS|`Expert_LuckyChant`|
|USE_MOVE_FIRST|`Expert_MeFirst`|
|USE_LAST_USED_MOVE|`Expert_Copycat`|
|SWAP_ATK_SP_ATK_STAT_CHANGES|`Expert_PowerSwap`|
|SWAP_DEF_SP_DEF_STAT_CHANGES|`Expert_GuardSwap`|
|INCREASE_POWER_WITH_MORE_STAT_UP|`Expert_Punishment`|
|FAIL_IF_NOT_USED_ALL_OTHER_MOVES|`Expert_LastResort`|
|SET_ABILITY_TO_INSOMNIA|`Expert_WorrySeed`|
|HIT_FIRST_IF_TARGET_ATTACKING|`Expert_SuckerPunch`|
|TOXIC_SPIKES|`Expert_ToxicSpikes`|
|SWAP_STAT_CHANGES|`Expert_HeartSwap`|
|RESTORE_HP_EVERY_TURN|`Expert_AquaRing`|
|GIVE_GROUND_IMMUNITY|`Expert_MagnetRise`|
|REMOVE_HAZARDS_SCREENS_EVA_DOWN|`Expert_Defog`|
|TRICK_ROOM|`Expert_TrickRoom`|
|BLIZZARD|`Expert_Blizzard`|
|SP_ATK_DOWN_2_OPPOSITE_GENDER|`Expert_Captivate`|
|STEALTH_ROCK|`Expert_StealthRock`|
|SHADOW_FORCE|`Expert_ShadowForce`|

Dispatch quirks (data, as in the table): 1675-76 cross-wires
`EVA_DOWN_2` → `Expert_StatusAccuracyDown` and `ACC_DOWN_2` →
`Expert_StatusEvasionDown` (the same swap Basic has at 176-77);
`SKIP_CHARGE_TURN_IN_SUN` appears twice — 1716 →
`Expert_ChargeTurnNoInvuln` (live) and 1717 →
`Expert_UnusedSolarbeam` (unreachable, the first match always wins).
**Probability convention.** `IfRandomLessThan N, L` jumps to `L` (the
"skip" path) with probability `N/256`; the delta after the jump
applies with probability `(256−N)/256`. The decomp comments state the
probability of the *delta*, e.g. `IfRandomLessThan 50` → "80.5%
chance of score -3" (206/256). Same for `IfRandomGreaterThan`
(probability `(255−N)/256`… see §2) and the `*Stage*` tests, which
compare internal stages (0-12, default 6; `battle_script.h:4-6`).

**`Expert_StatusSleep`** (1810-1823). Effects requiring an asleep
target (Dream Eater / Nightmare, comment 1813-14). If the attacker
knows a `RECOVER_DAMAGE_SLEEP` or `STATUS_NIGHTMARE` move, 50% (+1);
else end.

**`Expert_DrainMove`** (1824-1837). If the move is immune / resisted
/ quarter-damage against the target, 80.5% of −3 (1831-36:
`IfRandomLessThan 50` skip). Comment 1821.

**`Expert_Explosion`** (1838-1882). Self-KO moves. Spec (comment
1841-1857): defender Evasion ≥ +1 → −1; defender Evasion "+3 or
higher" → 50% additional −1; then by user HP%: ≥80 & faster →
80.5% of −3; ≥80 & slower → 80.5% of −1; >50 → 50% of −1; >30 →
50% of +1; ≤30 → 80.5% of +1. Code: 1859-62 eva
`IfStatStageLessThan DEFENDER, EVASION, 7` (=+1) skip, else −1;
1861-64 `... 10` (=+4, **comment says +3**) skip else
`IfRandomLessThan 128` skip else −1; 1865-68 HP<80 → medium, slower
→ medium, else 80.5% −3; 1869-72 HP>50 → 50% −1 else 50% +1 then
low-HP branch; 1873-76 HP>30 → end, else 80.5% +1.

**`Expert_DreamEater`** (1883-1903). Immune/quarter/half → −1
*instead of the damage score* (comment 1885); otherwise if target is
asleep, 80.1% of +3 (`IfRandomLessThan 51` skip).

**`Expert_MirrorMove`** (1904-1974). Spec (1906-1910): attacker
faster and target's last move is in the 46-move table (1925-1974:
Sleep Powder, Lovely Kiss, Spore, Hypnosis, Sing, Grass Whistle,
Shadow Punch, Sand Attack, Smoke Screen, Toxic, OHKO quartet —
Guillotine / Horn Drill / Fissure / Sheer Cold — Cross Chop,
Aeroblast, Confuse Ray, Sweet Kiss, Screech, Cotton Spore, Scary
Face, Fake Tears, Metal Sound, Thunder Wave, Glare, Poison Powder,
Shadow Ball, Dynamic Punch, Hyper Beam, Extreme Speed, Thief,
Covet, Attract, Swagger, Torment, Flatter, Trick, Superpower,
Skill Swap, Psycho Shift, Power Swap, Guard Swap, Sucker Punch,
Heart Swap, Switcheroo, Captivate, Dark Void) → 50% of +2;
else if the last move is *not* in the table, 68.75% of −1
(`IfRandomLessThan 80` skip).

**Stat-up family (attack / Sp.Atk / defense / Sp.Def / accuracy /
evasion).** Shared skeleton (1975-2203):

- `Expert_StatusAttackUp` (1975-2005) and `Expert_StatusSpAttackUp`
  (2078-2108): stage ≥ +3 (internal `9`) → 60.9% of −1
  (`IfRandomLessThan 100` skip); at 100% HP → 50% of +2; HP > 70 →
  end; HP < 40 → −2; else 84.4% of −2 (Attack: `N=40`;
  **Sp.Atk: `N=70` → 72.7%, comment copy-pasted from Attack says
  84.4%**).
- `Expert_StatusDefenseUp` (2006-2050) and
  `Expert_StatusSpDefenseUp` (2109-2153): stage ≥ +3 → 60.9% of −1;
  100% HP → 50% of +2; HP ≥ 70 → 78.1% suppresses all further
  modifiers (skip, `N=200`); HP < 40 → −2; 40-70: target's last
  move status (power 0) → 76.6% of −2; last move *special*
  (DefenseUp) / *physical* (SpDefenseUp) → **unconditional −2 in
  code** (comment claims 76.6% / 58.6% — drift); anything else
  76.6% of −2. Each has an unreferenced
  `*_PreSplitPhysicalTypes` 10-entry type table (2051-2062,
  2154-2165: Normal, Fighting, Poison, Ground, Flying, Rock, Bug,
  Ghost, Steel — pre-phys/special split leftovers).
- `Expert_StatusAccuracyUp` (2166-2180): stage ≥ +3 → 80.5% of −2
  (`N=50`); HP < 70 → −2.
- `Expert_StatusEvasionUp` (2181-2203): spec below (next chunk).
**`Expert_StatusEvasionUp`** (2181-2252). Spec
(2184-2196): HP ≥ 90 → 60.9% of +3; own evasion ≥ +3 → 50% of −1;
target Badly Poisoned → HP > 50 ? 80.5% of +3 : 72.7% of +3;
target Leech-Seed → 72.7% of +3; self Ingrain / Aqua Ring → 50% of
+2; target under Curse → 72.7% of +3; then HP ranges: > 70 → end;
own evasion exactly +0 (internal 6) → end; attacker HP < 40 → −2;
target HP < 40 → −2; else 72.7% of −2. Code matches the spec
(2206-2246: N=100/128/80/50/70/128/128/70 gates; HP-range block
2243-2249).

**`Expert_BypassAccuracyMove`** (2253-2273). Spec (2256-2261) says
extreme (target evasion ≥ +5 *or* attacker accuracy ≤ −5) → "60.9%
of +2, 39.1% of +1"; mild (evasion ≥ +3 or accuracy ≤ −3) → +1.
Code (2258-2262): extreme = target evasion > 10 (≥ +5) *or*
attacker accuracy < 2 (≤ −4) → **unconditional +1** (no random, no
+2); mild = evasion > 8 (≥ +3) or accuracy < 4 (≤ −3) → 60.9% of
+1 (N=100). The comment's +2 branch does not exist in code.

**Status-down family.** `Expert_StatusAttackDown` (2274-2305) and
`Expert_StatusSpAttackDown` (2365-2396): target's relevant stat not
at +0 (≠ 6) → −1; attacker HP ≤ 90 → −1; target at −3 or lower
(stage > 3) → 80.5% of −2; target HP ≤ 70 → −2; last-used move:
code jumps to end when the class is *not* Special (AttackDown) /
*not* Physical (SpAttackDown) and applies 50% of −2 when it *is* —
**the comments (2279, 2371) say the opposite** ("not a Special /
not a Physical move → 50% of −2"). Each has an unreferenced
`*_PreSplit*Types` table (2306-2314: Normal, Fighting, Ground,
Rock, Bug, Steel; 2397-2407: Fire, Water, Grass, Electric,
Psychic, Ice, Dragon, Dark).

`Expert_StatusDefenseDown` (2315-2334),
`Expert_StatusSpDefenseDown` (2408-2427),
`Expert_StatusEvasionDown` (2497-2516) — identical shape: attacker
HP < 70 → 80.5% of −2 (N=50); target at −3 or lower (stage > 3) →
80.5% of −2; target HP ≤ 70 → −2.

`Expert_StatusAccuracyDown` (2428-2496). Attacker HP < 70 *and*
target HP ≤ 70 → 60.9% of −1 (N=100); attacker accuracy at −3 or
lower (code: stage < 4… i.e. ≤ −3 — comment says "−2 or lower",
2430) → 68.75% of −2 (N=80); target Badly Poisoned → 72.7% of +2;
target seeded → 72.7% of +2; self Ingrain / Aqua Ring → 50% of +1;
target Cursed → 72.7% of +2; HP ranges: attacker > 70 → end;
target accuracy exactly +0 → end; either side < 40 → −2; else
72.7% of −2 (2487-2493).

**`Expert_SpeedDownOnHit`** (2335-2349). Immune / quarter / half →
no further modifiers; Icy Wind / Rock Tomb / Mud Shot → falls
through to `Expert_StatusSpeedDown`; any other move ends.
`Expert_StatusSpeedDown` (2350-2364): attacker slower → 72.7% of
+2 (N=70); attacker faster → −3.

**`Expert_Haze`** (2517-2562). Ten stage tests: any attacker stat
(atk/def/spatk/spd/eva) ≥ +3 (stage > 8), or any target stat
(atk/def/spatk/spd/**acc**) ≤ −3 (stage < 4) → 80.5% of −3
(comment says 80.4%); if none of those, the mirror ten tests
(any target ≥ +3 or any attacker ≤ −3) → 80.5% of +3; else 50% of
−1 (comment 2523 says unconditional −1). Note the asymmetry: the
minus-branch checks the target's *accuracy*, the plus-branch also
accuracy, but the minus list includes attacker evasion while the
plus list checks target evasion — Accuracy appears in both,
Evasion in both, while Sp.Def appears only via the same five-stat
sets (2526-2535, 2544-2553).

**`Expert_Bide`** (2563-2570). Attacker HP ≤ 90 → −2.
**`Expert_ForceSwitch`** (2571-2609). Spec (2574-2590):
target's turn count > 3 → 75% of +2 (N=64); Spikes / Stealth
Rock / Toxic Spikes on the *target's* side → 50% of +2 (N=128);
any target stat (atk/def/spatk/spd/eva) ≥ +3 (stage > 8) → 50% of
+2; if none of those matched, −3 (fall-through at 2587-88).

**`Expert_Conversion`** (2610-2624). Attacker HP ≤ 90 → −2;
not the battle's first turn (turn count ≠ 0) → code:
`IfRandomLessThan 200` skip → −2 with 21.9% (56/256) — **comment
2614 says 78.1%, i.e. the complement**.

**`Expert_Synthesis`** (2625-2636) → weather Hail / Rain /
Sandstorm → −2, then falls through to `Expert_Recovery`.

**`Expert_Recovery`** (2637-2676; shared by Synthesis, Recover
(UNUSED_157), Swallow, and Roost-family HEAL_HALF_REMOVE_FLYING
TYPE). Spec (2640-2649): full HP → −3 and terminate; faster than
target → −8 and terminate; HP ≥ 70 (slower branch) → 88.3% of −3
and terminate (N=30); otherwise target without Snatch → 92.2% of
+2 (N=20); target *with* Snatch → 56.2% of +2 (N=100).
An unreferenced `Expert_Recovery_Unused` (2651-2655) sits in the
middle: HP < 50 → snatch check, HP > 80 → −3 and end, else 72.7%
to the snatch check — dead label, no incoming jump.

**`Expert_ToxicLeechSeed`** (2677-2706; Toxic + Leech Seed).
Attacker with at least one damaging move: attacker HP ≤ 50 →
80.5% of −3; target HP ≤ 50 → 80.5% of −3 (2685-2698, both N=50).
Then if the attacker knows SP_DEF_UP or PROTECT → 76.6% of +2
(N=60; comment 2687-89 notes no vanilla move raises Sp.Def by
exactly 1, so only Protect matters).

**`Expert_LightScreen`** (2707-2730) / **`Expert_Reflect`**
(2890-2913) — mirror pair. Attacker HP < 50 → −2; HP ≥ 90 → 50%
of +1 (N=128); target's last-used move Special (LightScreen) /
Physical (Reflect) → 75% of +1 (N=64). Each has an unreferenced
pre-split type table (2731-2741: Fire, Water, Grass, Electric,
Psychic, Ice, Dragon, Dark; 2914-2925: Normal, Fighting, Flying,
Poison, Ground, Rock, Bug, Ghost, Steel).

**`Expert_Rest`** (2742-2788). Faster: HP = 100 → −8 and end;
HP < 40 → snatch check; HP > 50 → −3 and end; 40-50 → 72.7% of
−3 and end (N=70). Slower: HP < 60 → snatch check; HP > 70 → −3
and end; 60-70 → 80.5% of −3 and end (N=50). Snatch check
(2778-2785): target without Snatch → 96.1% of +3 (N=10); target
with Snatch → 77.3% of +3 (N=50).

**`Expert_OHKOMove`** (2789-2796). 25% of +1 (N=192).

**`Expert_SuperFang`** (2797-2804). Target HP ≤ 50 → −1.

**`Expert_BindingMove`** (2805-2823; Bind-family + Trap
Family). Target Toxic / Cursed / Perish Song / Attracted → 50% of
+1.

**`Expert_HighCritical`** (2824-2841). Immune / quarter / half →
no change; double / quadruple damage → 50% of +1; normal damage →
code 50% of +1 (N=128) — **comment 2827 says 25%**.

**`Expert_Swagger`** (2842-2889). Attacker knows Psych Up:
target attack ≥ −2 (code: stage > 3) → −5 and end — **comment
2845 says "−3 or higher"**; otherwise +3, and on the first battle
turn a further +2 (2879-2883: turn count 0 → +2). Attacker
without Psych Up falls through to `Expert_Flatter` (2845).

**`Expert_Flatter`** (2851-2857) → 50% of +1, else falls through
to `Expert_StatusConfuse`.

**`Expert_StatusConfuse`** (2858-2875). Target HP ≤ 70 → 50% of
−1; then ≤ 50 → −1; then ≤ 30 → −1 (cumulative: a target ≤ 30%
takes up to −3).

**`Expert_StatusPoison`** (2926-2936). Attacker HP < 50 → −1;
target HP ≤ 50 → −1.

**`Expert_StatusParalyze`** (2937-2952). Attacker slower → 92.2%
of +3 (N=20); attacker HP ≤ 70 → −1.

**`Expert_VitalThrow`** (2953-2972). Attacker slower → no
change; HP > 60 → no change; HP < 40 → 80.5% of −1 (N=50);
40-60 → code 29.7% of −1 (N=180) — **comment 2961 says 23.9%**.
**`Expert_Substitute`** (2973-3040). Attacker knows Focus
Punch → 62.5% of +1 (N=96). Then, HP > 90% → skip the penalty
rounds; HP ≤ 90% → roll −1 once (HP > 70), twice (HP > 50), or
thrice (HP ≤ 50), each roll 60.9% (N=100 chain at 3002-3009).
Faster than the target: load the target's previous move — status
inducing (sleep/poison/badly poison/paralyze/burn → check
target has *no* non-volatile condition; confuse → target
*not* Confused; leech seed → target *not* Seeded) → 60.9% of
+1. **The three checks are inverted relative to comment 2976-2982**
(which says +1 when the target *is* in the corresponding state;
code 3026-3035 gives +1 when it is *not*).

**`Expert_RechargeTurn`** (3041-3071; Fly/Dive/Bounce/Sky
Upper). Immune / quarter / half effectiveness → −1. Attacker
Truant → 68.75% of +1 and end (N=80). Slower: HP ≥ 60% → −1.
Faster: HP > 40% → −1.

**`Expert_Disable`** (3072-3091). Attacker slower → no change
and terminate. Target's previous move power 0 (Status) → 60.9%
of −1 (N=100); Damaging → unconditional +1.

**`Expert_Counter`** (3092-3173; Counter + Mirror Coat).
Target Asleep / Infatuated / Confused → −1 and terminate
(3104-3106, via the local `Expert_Counter_ScoreMinus1`
at 3156). HP ≤ 30% →
96.1% of −1 (N=10); HP ≤ 50% → 60.9% of −1, stacking.
Attacker knows Mirror Coat → 60.9% of +4 and terminate
(3121-3122). Target's previous move Status: target not
Taunted and neither type in the PhysicalTypes table
(3162-3173: Normal, Fighting, Flying, Poison, Ground, Rock, Bug,
Ghost, Steel — *this* table is referenced, unlike the pre-split
ones) → 77.3% of 60.9% ≈ 49% of +4 (3142-3152); target
Taunted → 60.9% of +1. Previous move Damaging: not Physical →
−1; Physical → 60.9% of +1; Taunted → 60.9% of +1.

**`Expert_Encore`** (3174-3283). Target under Disable → 88.3%
of +3 (N=30) and end. Attacker slower → −2. Target's previous
move effect not in the 83-entry encouraged table (3199-3283:
stat ups, Haze, ForceSwitch, Conversion/2, poison/burn,
Light Screen, Rest, Super Fang, Confuse, Protect, Trick,
Perish Song, all four weather setters, Endure, Swagger,
Infatuate, Prevent Status, Belly Drum, Stat Copy, Fly,
Fake Out, Stockpile, Spit Up, Swallow, Torment, Global
Target, Power Split, Switcheroo, Copy Ability, Recycle,
Knock Off, Imprison, Wish, Heal Pulse, Remove All PP,
Confuse All, Water Sport, Fire Sport, Dragon Dance,
Camouflage, Gravity, Heal Block, Power Trick, Natural Gift,
U-turn, Tailwind, Me First, Psycho Shift, Embargo, Fling,
Power Swap, Guard Swap, Gastro Acid, Lucky Chant, Aqua Ring,
Magnet Rise, Trick Room) → −2; otherwise 88.3% of +3. The
table lists `BATTLE_EFFECT_SWITCH_ABILITIES` twice (3225,
3242).

**`Expert_PainSplit`** (3284-3310). Target HP < 80% → −1.
Attacker slower: HP > 60% → −1, else +1. Attacker faster: HP >
40% → −1, else +1. (Comment 3299 says the faster/final branch
"Otherwise, score -1" — a typo for +1; code 3297.)

**`Expert_Nightmare`** (3311-3315). Unconditional +2.

**`Expert_LockOn`** (3316-3323). 50% of +2.

**`Expert_SleepTalk`** (3324-3331). Attacker Asleep → +10
(shared `ScorePlus10` label); otherwise −5.

**`Expert_DestinyBond`** (3332-3358). Start at −1. Attacker
slower → terminate. HP ≤ 70% → 50% of +1; HP ≤ 50% → 50% of
+1 (stacks); HP ≤ 30% → 60.9% of +2 (stacks).

**`Expert_Reversal`** (3359-3394). Slower: HP > 60% → −1; HP >
40% → no change; HP ≤ 40% → 60.9% of +1. Faster: HP > 33% →
−1; HP > 20% → no change; HP 8-20% → 60.9% of +1; HP < 8% →
+1 plus 60.9% of +1 (i.e. 39.1% of +1, 60.9% of +2).

**`Expert_HealBell`** (3395-3404). Attacker or any ally with
any non-volatile condition → no change; otherwise −5.

**`Expert_Thief`** (3405-3448). Target's held item effect not
in the 25-entry encouraged table (3421-3448: SLP_RESTORE,
STATUS_RESTORE, HP_RESTORE, ACC_REDUCE, HP_RESTORE_GRADUAL,
PIKA_SPATK_UP, CUBONE_ATK_UP, all 16 `WEAKEN_SE_*` type-weak
items, WEAKEN_NORMAL, HP_RESTORE_PSN_TYPE) → −2; otherwise
80.5% of +1 (N=50).
**`Expert_Curse`** (3449-3498). Ghost typing: HP > 80% → no
change and end; HP ≤ 80% → −1 and end. Non-Ghost: Def stage ≥
+4 (code: stage > 9) → no change and end — **comment 3471
says "at +3 or higher"**. Knows Gyro Ball or Trick Room →
87.5% of +1 (N=32) *plus* fall-through into the next roll;
otherwise 50% of +1 (N=128). Then: Def stage ≥ +2 (stage > 7)
→ terminate; else 50% of +1; then: Def stage ≥ +1 (stage > 6)
→ terminate; else 50% of +1 (both stack, per comment 3480).

**`Expert_Protect`** (3499-3587). Target knows Feint / Shadow
Force → 50% of −2. Protect chain > 1 (used twice already) →
−2 and terminate. Attacker under Toxic / Curse / Perish Song /
Attract / Seeded / Yawn, and *not* Locked On → −2 and
terminate. Target knows a half-HP recovery move or Defense
Curl, and attacker not Locked On → (skip) — these jump to
`CheckAttackerLockedOnto`, which terminates only if the
attacker *is* Locked On, else continues. Target under the same
six effects → +2 (and continues). Doubles battle → +2.
Attacker Locked On → +2. Else 33.2% of +2 (N=85). From
here-on: 50% of −1 (N=128). If the attacker used Protect last
turn (chain > 0): −1 plus 50% of a further −1.

**`Expert_Spikes`** (3588-3604). 50% of no change and
terminate; otherwise +1, and if the attacker knows Roar or
Whirlwind a further 75% of +1.

**`Expert_Foresight`** (3605-3629). Attacker Ghost-typed —
decomp comment 3616: "BUG: This should instead check the
opponent's typing" — → 47.3% of +2 (double N=80 roll:
31.25% terminate, then 68.75% of a second 68.75% roll).
Otherwise: target Evasion stage ≥ +3 (stage > 8) → 68.75% of
+2; else −2.

**`Expert_Endure`** (3630-3647). Attacker HP < 4% → −1 and
end. HP < 35% → 72.7% of +1 (N=70).

**`Expert_BatonPass`** (3648-3702). Any attacker stat stage ≥
+3 (stage > 8): slower + HP ≤ 70% → 68.75% of +2 (N=80);
faster + HP ≤ 60% → 68.75% of +2; else no change. Any stage
exactly +2 (stage > 7): **code: slower + HP ≥ 70% → −2;
faster + HP > 60% → −2** — **comment 3689-3696 has this
inverted** ("slower ≤ 70 / faster ≤ 60 → −2"). No boosted
stage at all → −2.

**`Expert_Pursuit`** (3703-3733). First turn in battle → 50%
of +1; target type 1 or 2 Ghost / Psychic → each 50% of +1
(code performs these rolls regardless of turn — comment
3714-3716 gates them on "NOT the first turn"); target knows
U-turn → 50% of +1. All independent, so up to six +1 rolls
stack.

**`Expert_RainDance`** (3734-3769). Attacker slower + Swift
Swim → +1 and terminate. HP < 40% → −1. Current weather Hail /
Sun / Sandstorm → +1. Rain Dish → +1. Hydration and attacker
statused → +1 (3755-3757).

**`Expert_SunnyDay`** (3770-3799). HP < 40% → −1. Current
weather Hail / Rain / Sandstorm → +1. Flower Gift → +1.
Leaf Guard and attacker statused → +1 — decomp comment
3781-3783: "BUG: This should check instead if the attacker is
NOT statused, as Leaf Guard has no effect on existing status
conditions".

**`Expert_BellyDrum`** (3800-3810). Attacker HP < 90% → −2.

**`Expert_PsychUp`** (3811-3848). Any target stat (atk/def/
spa/spd/eva) ≥ +3 (stage > 8): attacker eva ≤ +0 (stage < 7)
→ +2 (two +1s via the 3836-3842 fall-through chain); any
attacker of atk/def/spa/spd ≤ +0 → +1. Else: 80.5% of −2
(N=50) — comment 3834 says 80.4%. Target without any ≥ +3
stat → −2. (The `ScorePlus2` label at 3836 holds the first
`AddToMoveScore 1` of the eva +2 chain.)
**`Expert_MirrorCoat`** (3849-3929). Mirror image of
`Expert_Counter` (3092-3173): same structure, but the
referenced table is `SpecialTypes` (3919-3929: Fire, Water,
Grass, Electric, Psychic, Ice, Dragon, Dark) and the class
test is `CLASS_SPECIAL`; the +4 knowledge check is for Counter
instead of Mirror Coat. Target's previous move Physical →
−1; Special → 60.9% of +1.

**`Expert_ChargeTurnNoInvuln`** (3930-3971; Sky Attack,
Solar Beam, and other charge effects without an invulnerable
turn). Immune / quarter / half → −2 and terminate. Current
move has `SKIP_CHARGE_TURN_IN_SUN` and weather is actually
Sun → +2 and terminate (3945-3951). Holding Power Herb → +2
and terminate. Target knows Protect → −2 (stacks with the
next check); HP ≤ 38% → −1 (3960-3965).

**`Expert_UnusedSolarbeam`** (3972-3988) — dead label
(reached only through the shadowed duplicate dispatch entry
1717; the first entry 1716 routes Solar Beam to
`ChargeTurnNoInvuln`). Behavior if it ever ran: immune /
half / quarter → 77.3% of −3 (N=50); weather Sun → 77.3% of
−3; weather Rain → +1.

**`Expert_ChargeTurnWithInvuln`** (3989-4070; Fly, Dive,
Bounce, and other two-turn moves with an invulnerable first
turn). Holding Power Herb → +2 and terminate (shared
`ScorePlus2` at 3956). Target does *not* know Protect →
divert to the shared `Expert_ShadowForce` sub-routine
(4011-4019): immune / quarter / half → +1 and terminate
(decomp comment 3993-3995 flags this "(Bug?)"), Power Herb →
+1 and terminate, then continues into the common condition
checks below. Target *knows* Protect → −1 and terminate.
Common checks (4024-4053): target Toxic / Cursed / Seeded →
each 68.75% of +1 (N=80); weather Sandstorm and attacker
Ground / Rock / Steel (table 4065-4070) → 68.75% of +1;
weather Hail and attacker Ice → 68.75% of +1; attacker
faster and target's previous move is *not* an
always-hit effect → 68.75% of +1. All stack.

**`Expert_FakeOut`** (4071-4075). Unconditional +2.

**`Expert_SpitUp`** (4076-4085). Stockpile count ≥ 2 →
68.75% of +2 (N=80).

**`Expert_Hail`** (4086-4116). Attacker HP < 40% → −1 and
terminate. Weather Sun / Rain / Sand → +1, plus a further +2
if the attacker knows Blizzard (4103-4104); then Ice Body
→ +2 and terminate.

**`Expert_Facade`** (4117-4125). Target with a Facade-boosting
status (burn/poison/paralysis) → +1 — decomp comment
4117-4118: "BUG: This should instead check if the attacker
has such a status condition".

**`Expert_FocusPunch`** (4126-4161). Immune / quarter /
half → −1 and terminate. Attacker behind a Substitute → +5
(shared `ScorePlus5`). Target Asleep → +1 (and continues).
Target Infatuated / Confused → each 60.9% of +1 (N=100,
stacking). Not the attacker's first turn → 21.875% of +1
(N=200; 4143-4146).

**`Expert_SmellingSalts`** (4162-4172). Target Paralyzed →
+1.
**`Expert_Trick`** (4173-4411). Branch on the attacker's
held item effect:
- **Disruptive** (table 4340-4356: Choice ×3, Ball-85-style
  speed-down, Red-Card-style priority-down, contact-damage
  transfer, six level-up EV items — decomp comment 4346:
  "BUG: This list does not include Macho Brace"): target's
  item in `BadOpponentItems` (4393-4411: EV-up-speed-down,
  the same disruptive set, poison-user, burn-user,
  Black-Sludge) → −3; else +5.
- **Poisoning** (4357-4360): target's bad item → −3; target
  has any status / Safeguard / Steel or Poison typing /
  Immunity, Magic Guard, Poison Heal → check the attacker
  (any of those plus Klutz → −3, else +5); else +5.
- **Burning** (4361-4364): same shape with Water Veil / Magic
  Guard and Fire typing. The Klutz check at 4286-4299
  jumps to the global `ScoreMinus5` helper (1579-1581):
  −5 and terminate.
- **Black Sludge** (4365-4368): target's bad item → −3;
  target Poison typing → attacker check (poison typing /
  Magic Guard / Klutz → −3, else +5); target Magic Guard →
  **jumps to `CheckAttackerForPoison`** (4381), not the Sludge
  attacker check — so the attacker gets judged by the full
  poison criteria; else +5.
- **Flavor berry** (4332-4339: the five taste berries):
  target holds a bad item or a flavor berry (combined table
  4369-4392) → −3; else 80.5% of +2 (N=50).

**`Expert_ChangeUserAbility`** (4412-4459; the
ability-swap effect, e.g. Trick-Room-family `COPY_ABILITY`
and `SWITCH_ABILITIES` moves). Attacker has a desirable
ability (25-entry table 4432-4459: Speed Boost, Battle
Armor, Sand Veil, Static, Flash Fire, Wonder Guard, Effect
Spore, Swift Swim, Huge Power, Rain Dish, Cute Charm, Shed
Skin, Marvel Scale, Pure Power, Chlorophyll, Shield Dust,
Adaptability, Magic Guard, Mold Breaker, Super Luck,
Unaware, Tinted Lens, Filter, Solid Rock, Reckless) → −1
and terminate; else target has one → 80.5% of +2 (N=50).

**`Expert_Ingrain`** (4460-4463). No score change.

**`Expert_Superpower`** (4464-4488). Immune / quarter /
half → −1. Attacker Attack stage ≤ −1 (stage < 6) → −1.
Slower + HP ≥ 60% → −1. Faster + HP > 40% → −1.

**`Expert_MagicCoat`** (4489-4513). Target HP ≤ 30% → 60.9%
of −1 (N=100). Attacker's first turn in battle → 37.5% of
+1 (code: N=150) — **comment 4501 says 41.4%**; an
unreachable `IfRandomLessThan 50` sits after the `GoTo End`
at 4505. Not the first turn → 88.3% of −1 (N=30).

**`Expert_Recycle`** (4514-4538). Attacker's stored
(Recycle) item is not Chesto / Lum / Starf berry (table
4533-4538) → −2; else 80.5% of +1 (N=50).
**`Expert_Revenge`** (4539-4555). Target Asleep /
Infatuated / Confused → −2 and terminate. Else 70.3% of −2
(N=180), 29.7% of +2.

**`Expert_BrickBreak`** (4556-4567). Reflect or Light
Screen on the target's side → +1 (each, stacking).

**`Expert_KnockOff`** (4568-4579). Target HP ≥ 30% and
not the attacker's first turn → 29.7% of +1 (N=180).

**`Expert_Endeavor`** (4580-4606). Target HP < 70% → −1
and terminate. Attacker slower: HP > 50% → −1, else +1.
Attacker faster: HP > 40% → −1, else +1.

**`Expert_WaterSpout`** (4607-4630). Immune / quarter /
half → −1. Slower + target HP ≤ 70% → −1. Faster + target
HP ≤ 50% → −1. Decomp comment 4614: "BUG: This should
instead check for the user's HP".

**`Expert_Imprison`** (4631-4640). Not the attacker's
first turn → 60.9% of +2 (N=100).

**`Expert_Refresh`** (4641-4651). Target HP < 50% → −1.

**`Expert_Snatch`** (4652-4697). First turn → 37.5% of +2
(code: N=150) — **comment 4653 says 41.4%**. 11.7% of no
change and terminate (N=30). Attacker slower: target HP >
25% → 88.3% of −2 (N=30); target knows flat-Recovery or
Defense Curl → 37.5% of +2 (code: N=150) — **comment 4659
says 41.4%**; else 10.2% of +1 (N=230). Attacker faster:
attacker not full HP → 88.3% of −2; target HP < 70% →
88.3% of −2; else code 76.6% of −2 (N=60) — **comment
4665 says 67.6%**.

**`Expert_MudSport`** (4698-4715). Attacker HP < 50% → −1.
Target Electric-typed (either type) → +1 and terminate.
**Otherwise (fall-through at 4707) −1** — the comment
4699-4701 never mentions this default penalty, so a
non-Electric target at full HP is scored down.
**`Expert_Overheat`** (4719-4740). Immune / quarter /
half → −1. Slower + HP ≤ 80% → −1. Faster + HP ≤ 60% →
−1.

**`Expert_WaterSport`** (4741-4761). Attacker HP < 50% →
−1. Target Fire-typed → +1 and terminate. **Otherwise
(fall-through at 4726) −1** — unmentioned in the comment,
same shape as MudSport.

**`Expert_DragonDance`** (4762-4778). Slower → 50% of +1
(N=128). HP ≤ 50% → 72.7% of −1 (N=70).

**`Expert_Gravity`** (4779-4801). Target Levitate /
Magnet Rise / Flying-typed → 75% of +1 (N=64). Else HP
≥ 60% → 37.5% of +1 (N=128).

**`Expert_MiracleEye`** (4802-4825). Target Dark-typed →
47.3% of +2 (double N=80). Evasion stage > +2 (> 8) →
47.3% of +2 (single N=80). Else −2.

**`Expert_WakeUpSlap`** (4826-4845). Immune / half /
quarter → −1. Target Asleep → +1.

**`Expert_HammerArm`** (4846-4865). Immune / half /
quarter → −1. Attacker slower → +1.

**`Expert_GyroBall`** (4866-4869). No score change.

**`Expert_Brine`** (4870-4888). Immune / half / quarter →
−1. Target HP ≤ 50% → +1, then 50% of a further +1
(N=128).

**`Expert_Feint`** (4889-4953). Target does not know
Protect → 75% of flat 0 (N=64), 25% to the condition
checks; target knows it → straight to the checks. Attacker
Toxic / Cursed / Perish / Infatuated / Seeded / under
Yawn → 50% of +1 each (stacking). Else target not full
HP and holds Leftovers / Black Sludge → 50% of +1.
Protect chain: 0 → 50% of +1; 1 → 25% of +1 (N=192); ≥ 2
→ −2.

**`Expert_Pluck`** (4954-4978). Immune / half / quarter →
−1. First turn → 75% of +1 (N=64). Then 50% of a further
+1 (N=128).

**`Expert_Tailwind`** (4979-5004). 25% of flat 0 (N=64).
Attacker faster → −1. HP ≤ 30% → −1. HP > 75% → +1.
Else 75% of +1 (N=64).

**`Expert_Acupressure`** (5005-5025). HP ≤ 50% → −1.
HP > 90% → 75% of +1 (N=64). Else 37.5% of +1 (N=128).

**`Expert_MetalBurst`** (5026-5081). Target Asleep /
Infatuated / Confused or knows Avalanche-Revenge /
Focus Punch / Vital Throw → −1 and terminate. HP ≤ 30% →
96% of an added −1 (N=10). HP ≤ 50% → 60.9% of an added
−1 (N=100). HP > 50% → 25% of +1 (N=192). Target's
previous move non-damaging and target not Taunted →
60.9% of +1 (N=100). Target not Taunted → 60.9% of +1
(N=100).
**`Expert_UTurn`** (5082-5143). Immune / quarter /
half → −1 and terminate. Attacker is the last living
member (no other alive) → +2 and terminate. Attacker
holds a super-effective move → 75% of −2 (N=64). No
party member deals more damage (max) → 75% of −2
and terminate (N=64). Target HP > 70% → 75% of +1
(N=64); HP > 30% → 50% of +1 (N=128, cumulative); else
25% of +1 (N=128). Attacker faster → +1; else 50% of
+1 (N=128).

**`Expert_CloseCombat`** (5144-5165). Immune / quarter /
half → −1. Slower + HP ≤ 80% → −1. Faster + HP ≤ 60% →
−1.

**`Expert_Payback`** (5166-5185). Immune / half /
quarter → −1. Attacker faster → terminate (no bonus).
HP < 30% → terminate. Else 75% of +1 (N=64).

**`Expert_Assurance`** (5186-5219). Immune / half /
quarter → −1. Attacker faster → terminate. Rough Skin
or Jaboca / Rowap Berry → 50% of +1 (N=128); else 25%
of +1 (N=128 into a 50% roll).

**`Expert_Embargo`** (5220-5227). 50% of +1 (N=128).

**`Expert_Fling`** (5228-5292). Immune / half /
quarter: if the held item is one of the six Fling
boosters (King's Rock, Razor Fang, Poison Barb,
Toxic Orb, Flame Orb, Light Ball — table 5285-5291) no
penalty, else −1. Item's Fling power < 30 → −2. > 90 →
super-effective (×2/×4) → +4, else 50% of +1 (N=128)
and 75% of a further +1 (N=64). > 60 → 75% of +1
(N=64). Else (30-60) → 50% of −1 (N=128).

**`Expert_PsychoShift`** (5293-5304). Attacker has no
status → jump to the global `ScoreMinus10` helper
(1591-1593): −10 and terminate. Else 50% of no change
(N=128); target HP < 30% → no change; else +1.

**`Expert_TrumpCard`** (5305-5357). Immune / half /
quarter → −1. PP = 1 → +3. PP = 2 → **+1 unconditionally**
(code) — **comment 5329-5330 says 60.9% +2 / 39.1% +1**
(the `ScorePlus1Maybe2` label only ever adds 1). PP = 3
→ 60.9% of +1 (N=100). Target Pressure → 88.3% of +1
(N=30). Target Evasion ≥ +5 or attacker Accuracy ≤ −5
→ same unconditional +1 (comment claims the same
60.9/+39.1 split). Target Evasion ≥ +3 or Accuracy ≤
−3 → 60.9% of +1 (N=100).

**`Expert_HealBlock`** (5358-5401). Target knows any of
thirteen recovery effects (Dream Eater, half-restore,
Roost, UNUSED_157, sun-boosted restore, Rest, Swallow,
draining, Ingrain, per-turn restore, Leech Seed, Lunar
Dance, Healing Wish) → 90.2% of +1 (N=25). Attacker
Seeded / target under Aqua Ring or Ingrain → 90.2% of
+1. Else code 37.5% × 90.2% = 33.8% of +1 (N=96 into
N=25) — **comment 5390 says 56.4%**.

**`Expert_WringOut`** (5402-5438). Immune / half /
quarter → −1. Target HP < 50% → −1. Full HP → 90.2% of
+1 (N=25), **and both faster and slower give exactly
that +1** — **comment 5419-5421 says faster adds a
further +2 (total +3) and slower a further +1 (total
+2)**; the code has no such split. HP > 85% → 90.2% of
+1 (N=25).

**`Expert_PowerTrick`** (5439-5467). HP > 90% → 62.5%
of +1 (N=96). HP > 60% → 50% of +1 (N=128). HP > 30% →
35.9% of +1 (N=164). Else `GoTo ScoreMinus2` (5448) —
the global helper (1571-1573): −2 and terminate — the
comment never mentions this −2.

**`Expert_GastroAcid`** (5468-5492). 25% of flat 0
(N=64). Target HP > 70% → +1. Then 50% of no change
(N=128); else −1; HP > 50% → stop (−1); else −1 more
(−2); HP > 30% → stop (−2); else −1 more (−3).

**`Expert_LuckyChant`** (5493-5515). Attacker HP <
70% → −1. Target knows a high-critical move (three
effect families) → +1. Else 25% of +1 (N=64).
**`Expert_MeFirst`** (5516-5548). Slower → −2.
Attacker deals more (max) damage than target → 87.5%
of +1 (N=32). Target's last move was status → 75% of
+1 and terminate (N=64); damaging → 50% of +1
(N=128).

**`Expert_Copycat`** (5549-5628). 46-move "encouraged"
table (5579-5628: sleep moves, one-hit-KO moves,
stat-changing moves, Thief / Covet, Attract / Swagger /
Torment / Flatter / Trick / Superpower, the swap
families, Sucker Punch, Heart Swap, Switcheroo,
Captivate, Dark Void). Slower: deals more damage or
last move encouraged → no change; else 68.75% of −1
(N=80). Faster: deals more damage → 87.5% of +2
(N=32); last move encouraged → 50% of +2 (N=128).

**`Expert_PowerSwap`** (5629-5762) and
**`Expert_GuardSwap`** (5763-5896) share one shape
(PowerSwap: Attack/SpAttack, GuardSwap: Defense/
SpDefense). Let dA = attacker−defender stage diff of
the primary stat, dS of the secondary. Branch on dA
(> 3 / > 1 / > 0 / = 0), then dS (> 3 / > 1 / [> 0 or
= 0] / rest → no change). Each terminal `TryScorePlusN`
cascades: 50% of +N, else re-roll N−1, down to 50% of
+1 / 50% of +0. Peak: dA > 3 and dS > 3 → 50/25/12.5/
6.25/3.125/3.125% for +5/+4/+3/+2/+1/+0.

**`Expert_Punishment`** (5897-5949). Immune / half /
quarter → +0 and terminate. Sum of target's positive
stat stages: > 6 → 50/25/12.5/6.25/6.25% for
+4/+3/+2/+1/+0; = 6 → 50/25/12.5/12.5% for
+3/+2/+1/+0; = 5 → 50/25/25% for +2/+1/+0; = 3 or 4
→ 50/50% for +1/+0 (two separate `> 3` / `> 2` jumps
into the same roll); else +0.

**`Expert_LastResort`** (5950-5969). Immune / half /
quarter → −1. Attacker can actually use Last Resort
(no damaging move left) → +1, else +0.

**`Expert_WorrySeed`** (5970-5991). Target knows Rest
→ +1, then both branches continue to the HP check.
Attacker HP < 50% → 75% of +1 (N=64). HP ≥ 50% → code
87.5% of +1 (50% into a 75% roll, plus the other 50%
unconditional) — **comment 5976-5978 says "50% chance
of additional score +1"** and "75% chance of score +1"
(the unconditional half is unstated).
**`Expert_SuckerPunch`** (5992-6008). Immune / half /
quarter → −1. 75% of +1 (N=64).

**`Expert_ToxicSpikes`** (6009-6027) and
**`Expert_StealthRock`** (6265-6285) are identical in
shape. 50% of flat 0 (N=128). Else +1. Attacker knows
Roar or Whirlwind → 75% of a further +1 (N=64).

**`Expert_HeartSwap`** (6028-6077). Target has no stat
at ≥ +2 (stage > 7) and no Focus Energy → −2 and
terminate. Else: any attacker main stat ≤ +1 (stage <
7) → +1 and terminate; attacker Evasion ≤ +1 →
**+1** (label is `ScorePlus2` but 6062 adds 1) —
**comment 6028 says +2**; attacker without Focus
Energy → +1 and terminate. Else 80.5% of no change
(N=50), 19.5%… code: 80.5% of −2 (fall-through).

**`Expert_AquaRing`** (6078-6086). Attacker HP < 30% →
no change. Else 50% of +1 (N=128).

**`Expert_MagnetRise`** (6087-6121). Attacker HP <
50% → no change at all. Target knows Earthquake /
Earth Power / Fissure → +1. Target Ground-typed → +1;
else 50% of +1 (N=128). Decomp quirk: the shared
`Expert_MagnetRise_End` label contains **five
consecutive `PopOrEnd` lines** (6115-6121) — a single
pop is expected (same artifact, smaller: Blizzard_End
6229-6232 has two, StealthRock_End 6281-6285 three).
**`Expert_Defog`** (6122-6186). Code path: target
side under Light Screen / Reflect → attacker HP > 30%
or attacker has backup → +1; no backup (HP ≤ 30%) →
80.5% of −2 (N=50), then HP check. No backup and no
hazard → same −2 roll. Else (no screens): target side
hazard (Spikes / Stealth Rock / Toxic Spikes) → −2,
then continue. Continuation: +1; defender side has no
surviving member → stop; else target-side hazard →
50% of −1 (N=128). Then: attacker HP < 70% → 80.5% of
−2 (N=50); target Evasion stage ≤ −3 (stage ≤ 3) →
also 80.5% of −2 — **comment 6167-6173 says −2 "at -2
stage or greater" and unconditional**; target HP
> 70% → stop, else a further −2.

**`Expert_TrickRoom`** (6187-6212). Double Battle → no
change at all. Attacker HP ≤ 30%: no backup → no
change; else to the speed check. Speed check: faster →
−1; slower → 75% of +3 (N=64).

**`Expert_Blizzard`** (6213-6232). Immune / half /
quarter → 80.5% of −3 (N=50). Weather Hail → +1.

**`Expert_Captivate`** (6233-6264). Target SpAttack
stage = +0 → skip straight to the HP check; else −1,
and attacker HP ≤ 90% → a further −1. Target SpAttack
≤ −3 (stage ≤ 3) → 80.5% of a further −2 (N=50) —
**comment 6242 says "-3 or lower" matches the code**.
Target HP ≤ 70% → a further −2. Target's last move
was Physical → 75% of a further −1 (N=64).

**`Expert_RecoilMove`** (6286-6303; recoil-damage
effect family). Immune / half / quarter → no change at
all. Attacker Rock Head or Magic Guard → +1.

**`Expert_HealingWish`** (6304-6349). Attacker HP ≥
80% and faster: 25% of no change (N=192), else `GoTo
ScoreMinus5` (6316) — the global helper (1579-1581):
−5 and terminate (unconditional in code once the coin
flip passes; the comment's "25% of score -5" matches).
Otherwise (HP < 80% or slower) → the "happy path": HP
> 50% → 80.5% of −1 (N=50); 25% (N=192) jump to the
low-HP check; else +1, and no super-effective move in
the attacker's arsenal → 25% of a further +1 (N=192);
else a party member that deals more (max) damage →
50% of a further +1 (N=128). Low-HP check: HP ≤ 30% →
50% of +1 (N=128).

That covers every `Expert_Main` dispatch target
(133 unique routines; the only labels that do not
stand alone are the ShadowForce sub-label inside
`ChargeTurnWithInvuln`, the dead `Recovery_Unused`,
and the shadowed `UnusedSolarbeam`).
### `AI_FLAG_EVAL_ATTACK` — `EvalAttack_Main` (6350-6413)

"Reward the biggest hitter." Partner → terminate. If the
move's max non-critical damage KOs the target → kill
bonuses: Explosion / Self-Destruct (`HALVE_DEFENSE`) →
terminate with no bonus (kills by these are not
rewarded here); Focus Punch / Sucker Punch / Future
Sight → 33.6% of +4 (N=170) — decomp note: the AI never
treats Focus Punch / Sucker Punch as able to kill, so
only Future Sight can actually reach this; a
`BATTLE_EFFECT_PRIORITY_1` effect adds a further +2 on
top of the +4 (the code checks the effect, not the
move's priority value). Otherwise: the move must deal
the highest max damage in the pool (`FlagMoveDamageScore
USE_MAX_DAMAGE`); a non-top move jumps straight to the
global `ScoreMinus1` helper (−1 and terminate). The
top (or non-comparable) move then: Explosion / Focus
Punch / Sucker Punch effects → ~80% of −2 (N=51 →
19.9% skip, 80.1% −2); quad-effective → 68.75% of +2
(N=80), stacking on top; terminate.

### `AI_FLAG_SETUP_FIRST_TURN` — `SetupFirstTurn_Main` (6414-6495)

Partner → terminate. Not turn 0 → terminate. The move's
effect must be in the 61-entry `SetupFirstTurn_SetupEffects`
table (6432-6494): all single- and double-stage
stat-up/down effects, Conversion, Light Screen, Reflect,
Substitute, Leech Seed, Minimize, Curse, Swagger,
Camouflage (listed **twice** — 6474 and 6487), Yawn,
Defense Curl, Torment, Nasty Plot, Will-O-Wisp, Aqua
Ring, Disable, Spikes, Work Up, Rock Polish, Calm Mind,
Focus Energy, Magnet Rise, Defog, Whirlpool. Then
68.75% of +2 (N=80).

### `AI_FLAG_PRIORITIZE_EXTREMES` — `PrioritizeExtremes_Main` (6496-6517)

Partner → terminate. Applies only when
`FlagMoveDamageScore USE_MAX_DAMAGE` reports
`AI_NO_COMPARISON_MADE` — i.e. moves with no standard
damage calc: variable-power, flat-damage, or zero-power
effects (the comment points at `sNoDamageCalcMoveEffects`
and `sAltPowerMoveEffects` for the lists). Then 60.9% of
+2 (N=100; comment says "~61%").

### `AI_FLAG_RISKY` — `Risky_Main` (6518-6560)

Partner → terminate. The move's effect must be in the
25-entry `Risky_RiskyEffects` table (6533-6559):
Sleep, Explosion, Mirror Move, OHKO, high-critical,
Confuse, Sketch, Seismic Toss, Counter, Destiny Bond,
Swagger, Attract, Draco Meteor, Shell Smash, Belly
Drum, Mirror Coat, Focus Punch, Avalanche, Spikes,
Gyro Ball, Shell Smash-style random stat up, Metal
Burst, Assurance, Me First, Sucker Punch. Then 50% of
+2 (N=128).

### `AI_FLAG_BATON_PASS` — `BatonPass_Main` (6561-6633)

Partner → terminate. No other alive party member →
terminate. A damaging move → terminate (only
non-comparable moves continue). Attacker does **not**
know Baton Pass: 31.25% of no change (N=80 → jump to
`Risky_Terminate`, reusing Risky's shared terminate
label at 6530); 68.75% fall through to the move
evaluation. Then, in order:
- Swords Dance / Dragon Dance / Calm Mind / Nasty Plot
  → `SetupAtHighHP`: turn 0 → +5 and terminate
  (`ScorePlus5`); HP < 60% → −10 and terminate
  (`ScoreMinus10`); else +1 and terminate
  (`ScorePlus1`).
- Effect is Protect → last move was Protect / Detect →
  −2 and terminate (`ScoreMinus2`); else +2 and
  terminate.
- The move is Baton Pass → turn 0 → −2 and terminate;
  else the **first** matching stage check fires, adds
  once, and terminates: Attack ≥ +3 / ≥ +2 / ≥ +1 →
  +3 / +2 / +1, then the same three for SpAttack.
  **Comment 6622 says "+1 for each positive stat
  stage" of both stats — the code awards a single
  addition, Attack taking priority.**
- Any other non-damaging move: 7.8% of no change
  (N=20); 92.2% +3 — and the code then **falls through
  into the `SetupAtHighHP` block** (6589-6591; there is
  no `PopOrEnd` on this path): turn 0 → a further +5
  and terminate; HP < 60% → a further −10; else a
  further +1. The comment (6587) only mentions the +3.
### `AI_FLAG_TAG_STRATEGY` — `TagStrategy_Main` (6634-7692)

Doubles specialist: rewards coordination with the partner.
Target is the partner → the `TagStrategy_Partner` sub-routine
(below); `CheckHP` reuses that same sub-routine for its partner
branch.

**Damage-move path.** Non-damaging move → straight to
`CheckSpecialScoring`. Flat-damage effects (OHKO, 40- and
20-damage flat, level flat, random 1-150% level) → straight to
`ScoreMove` (skip the effectiveness penalty). Half-effective:
unless the max roll kills or the defender's partner is out,
25% of −1. Quarter-effective: same gates, 25% of −2.

**`ScoreMove`**: no bonus unless the move is the highest damage
including the partner (`CheckIfHighestDamageWithPartner`).
Highest: Explosion / Self-Destruct skip the bonus; a
priority +1 effect → 80.5% of +1 (N=50); else 50% of +1.

**`CheckBeforeScoring`**: the flat-damage family skips the
super-effective bonuses; 2× effective → 60.9% of +1 (N=100);
4× → 75% of +1 (N=64).

**`CheckSpecialScoring`** dispatches per move: Skill Swap,
Earthquake / Magnitude, Future Sight / Doom Desire, the four
weather moves, Gravity, Trick Room, Follow Me; then by move
type Electric / Fire / Water; and a partner-knows-Helping-Hand
check. Anything else → `PopOrEnd` (no change).

- **Rain Dance** (for attacker and partner): Hydration — but
  only while statused — or Dry Skin → +2 each.
- **Sunny Day** (for each): Leaf Guard (only while healthy and
  HP ≥ 30%) → +2; Flower Gift → +2; Dry Skin → −2; Solar Power:
  HP ≥ 50% → +1, HP < 50% → 50% of −2.
- **Hail** (for each): Ice Body / Snow Cloak / knows Blizzard →
  +2, independent checks that can stack.
- **Sandstorm** (for each): Sand Veil → +2; Rock typing (either
  type slot) → +2.
- **Gravity**: Gravity field currently active → global
  `ScoreMinus30` (−30, terminate). Each allied battler with
  Levitate / Flying typing / Magnet Rise → −5; each enemy
  battler with any of those → 75% of +3.
- **Trick Room**: any of attacker's partner / defender /
  defender's partner at 0 HP → global `ScoreMinus30`. By the
  attacker's speed rank: moves first with partner ranked 1st or
  2nd → −30 (global); moves 2nd with partner 1st → −30; moves
  3rd with partner not last, or moves last with partner not
  3rd → −5 and terminate; else 75% of +5, 25% of −5.
- **Follow Me**: a tree over the attacker's HP band (>90 /
  50-90 / 30-50 / <30) × partner's HP band (>90 / 50-90 /
  30-50 / <30); every leaf is a 75% coin (N=64) for the listed
  score. Attacker >90: partner >90 → −1, 50-90 → +1, 30-50 →
  +2, <30 → +3. Attacker 50-90: −2 / −1 / +1 / +2. Attacker
  30-50: −2 / −2 / +1 / +2. Attacker <30: 75% jumps to global
  `ScoreMinus5` (−5, terminate).
- **Earthquake / Magnitude** (partner-centric; no partner
  liveness check, so a solo battler takes the fallback): partner
  immune — Magnet Rise, Levitate, or Flying typing — → +2 and
  terminate; partner weak — Fire, Electric, Poison, or Rock — →
  −10 and terminate; else −3 and terminate. All three are
  global score labels.
- **Future Sight / Doom Desire**: no partner → no change.
  Partner knows the same move family → by the attacker's speed
  rank: last (3) → −3 and terminate; 3rd (2): partner 1st or
  2nd → −3, else 50% of −3; 2nd (1): partner 1st → −3, else
  50% of −3; first (0): 50% of −3.
- **Skill Swap**: the user has Truant / Slow Start / Stall /
  Klutz → +5 and terminate; the target has Shadow Tag / Pure
  Power / Huge Power / Mold Breaker / Solid Rock / Filter /
  Flower Gift → +2 and terminate; else no change.
- **Partner knows Helping Hand**: for damaging (non
  flat-damage) moves → +1 and terminate (global `ScorePlus1`).

`TagStrategy_Unused_1` / `_Unused_2` (7113-7122) are dead
labels: nothing in the file jumps to them.
- **Electric** (other moves): defender's partner has Lightning
  Rod → −1, plus a further −8 if that partner is also a Ground
  type; user's partner has Lightning Rod → −10 and terminate.
- **Discharge**: user's partner has Motor Drive / Volt Absorb →
  +3 and terminate; Water or Flying typing → −10 and
  terminate; Ground typing → +3 and terminate; else −3.
  Decomp-flagged BUG: the Ground check runs *after* the
  Water / Flying checks, so dual-typed Water/Ground or
  Flying/Ground partners (Swampert, Gliscor) take −10 instead
  of +3.
- **Water** (other moves): defender's partner has Storm Drain →
  −1; user's partner has Storm Drain → −10 and terminate. The
  Surf re-check near the end is unreachable (Surf was already
  dispatched at the top).
- **Surf**: user's partner has Dry Skin / Water Absorb → +3
  and terminate; Ground or Fire typing → −10 and terminate
  (decomp BUG note: no Rock typing check, though a Rock partner
  is immune); else −3.
- **Fire**: the user's Flash Fire already activated → +1 on top
  of everything else. Lava Plume: partner Dry Skin → −3 and
  terminate (the comment claims +3 — comment/code mismatch);
  partner Flash Fire → +3 and terminate; partner Grass / Steel
  / Ice / Bug typing → −10 and terminate; else −3.

**`TagStrategy_Partner` (7336-7692)** — scores a move aimed at
the partner (doubles only). Partner fainted → −30 and
terminate. Damaging move: Fire / Electric / Water types go to
the absorption routines below; Fling → no change. Status move →
`PartnerStatusMove`. Then, in each:
- **Fire at partner**: partner has Flash Fire not yet
  activated → +3 and terminate; else −30 and terminate.
- **Electric at partner**: partner Motor Drive → 62.5% no
  change (N=160), partner Speed at +6 (stage 12) → −30, else
  +3 (terminate). Partner Volt Absorb → HP 100% → −10; HP >90%
  → no change; HP >75% → 25% of +3 (N=64); HP >50% → 50% of +3
  (N=128); else 75% of +3 (N=192). Neither → −30 and terminate.
- **Water at partner**: partner Water Absorb or Dry Skin → the
  same HP ladder as Volt Absorb; else −30 and terminate.
- **`PartnerStatusMove`**: Skill Swap → partner (target) Truant
  / Slow Start → +10 and terminate; user has Levitate and
  partner Electric (per type slot: mono-Electric +2) → +1 each;
  user has Compound Eyes / No Guard and partner knows any of
  the 14 inaccurate moves (Fire Blast, Thunder, Cross Chop,
  Hydro Pump, Dynamic Punch, Blizzard, Zap Cannon, Megahorn,
  Focus Blast, Gunk Shot, Magma Storm, Power Whip, Seed Flare,
  Head Smash) → +3 and terminate; else −30. Will-O-Wisp →
  partner Flash Fire (not activated) → +3, else the Guts
  check: Guts, healthy, no Fire typing, no Flame/Toxic Orb,
  HP ≥ 81% → +5 and terminate; else −30. Thunder Wave →
  partner Ground typing → −30; partner Motor Drive / Volt
  Absorb → the Electric absorption routine; else −30. Poison /
  Toxics → partner Poison Heal, healthy, no Toxic Orb, and HP
  ≤ 91% (comment says 81% — mismatch) → +5 and terminate;
  else −30 (BUG note: no check that the partner is
  poison-immune). Helping Hand (used at the partner) → no
  partner → −30; partner HP > 50% or moves first → 75% of +2
  with 25% of −1; else no change. Swagger → partner holds
  Persim / Lum Berry and Attack stage ≤ +1 (stage ≤ 7) → +3;
  no berry → −30 (Own Tempo not considered — decomp note).
  Trick / Switcheroo → no change. Gastro Acid → partner
  ability already suppressed → −30; partner Truant / Slow
  Start → +5; else no change. Acupressure → partner Simple with
  any stat ≥ +3 → −10 and terminate; any stat at +6 (stage 12)
  → −30 and terminate; HP ≤ 50% → −1; HP > 90% → 68.75% of +2
  (N=80); else 31.25% of +2 (N=128). Any other status move →
  −30 and terminate.
### `AI_FLAG_CHECK_HP` — `CheckHP_Main` (7693-7975)

Discourages situational moves when the relevant battler's HP is
in the wrong band. Target is the partner → `TagStrategy_Partner`
(shared sub-routine). Two rounds:

1. **Attacker HP.** >70%: a 12-effect table (Explosion, Rest,
   Destiny Bond, Lunar Dance / Lovely Kiss-style 1-HP survivals,
   Solar Beam, Dragon Meteor, the two faint-and-heal-successor
   effects, and two more). 31-70%: 43 effects — all single and
   double stat up/down, Bide, Conversion, Light Screen,
   Safeguard, Crit Chance up, Work Up, Rock Polish, Calm Mind,
   Focus Energy, Skill Swap, the stat-swap effects, Swagger.
   1-30%: 52 effects — the medium list plus Lock On, Copycat,
   Mirror Coat, Venomous, Motor Drive, Flash Fire, Shell
   Smash-style random stat up, Metal Burst. A table match →
   `TryScoreMinus2`: 80.5% of −2 (N=50).
2. **Defender HP** (same shape, separate tables). >70%: the
   table is **empty** — nothing is discouraged at high target
   HP. 31-70%: 42 effects (stat up/down, Safeguard, poison,
   Wring Out-style, Disable, Swagger, Shell Smash, …). 1-30%:
   50 effects (everything in the medium table plus Sleep,
   Explosion, OHKO, both `HALVE_HP` entries — **listed twice**,
   7933-7934 — Confuse, Paralyze, Toxics, Disable, Swagger,
   Power Trip, Infatuation, Copycat, Mirror Coat, Burn,
   …). Match → 80.5% of −2 again. So one move can take up to
   −4 total from this flag.

### `AI_FLAG_WEATHER` — `Weather_Main` (7976-8017)

Partner → terminate. Battle turn count ≠ 0 → terminate. Sun /
Rain / Sandstorm / Hail setup moves: if that weather is already
active on the field → terminate; else, on the attacker's first
turn in battle only, +5.

### `AI_FLAG_HARRASSMENT` — `Harrassment_Main` (8018-8069)

Partner → terminate. The move effect must be in
`Harrassment_Effects` (35 entries: Sleep, single and double
Atk / Def / Acc / Eva / Speed / SpDef downs, Confuse, Poison,
Paralyze, Leech Seed, Encore, Disable, Spikes, Swagger,
Infatuation, Torment, Nasty Plot, Burn, Nature Power, Yawn,
Trick, Mean Look-style shared-move block, Secret Power,
Spikes-style confuse-all, AtkDef down, Camouflage, Embargo,
Psycho Shift, Toxic Spikes, Defog, Spite). Then 50% of +2
(N=128).

### `AI_FLAG_ROAMING_POKEMON` — `RoamingPokemon_Main` (8070-8087)

No partner check at all. Decides whether the roamer may flee:
attacker under Bind or Mean Look, or defender with Shadow Tag →
stay (`PopOrEnd`, no other move scoring happens after this
flag's `Escape`). Attacker has Levitate → `Escape`. Defender
with Arena Trap → stay. Otherwise → `Escape`. (Levitate beats
Arena Trap; Bind / Mean Look / Shadow Tag beat everything.)

### `AI_FLAG_SAFARI` — `Safari_Main` (8088-8091)

`Dummy3E 1`, `Dummy3F`, `Escape` — the decomp spin-loop
artifact described in §2 (the `Dummy*` handlers never advance
the cursor). As decompiled, a Safari battle freezes; in the
original, the intent is "always Escape."

### `AI_FLAG_CATCH_TUTORIAL` — `CatchTutorial_Main` (8093-8100)

Target HP ≤ 20% → `Escape` (the tutorial AI's mon retreats so
the player can throw a Poké Ball); otherwise `PopOrEnd`.

**`Terminate`** (8102-8105): `PopOrEnd` — the target of all
`AI_FLAG_UNUSED_*` flag entries.
## §4 Switching

`TrainerAI_PickCommand` (4000) runs for trainer battles or any
player-side battler and picks the command in this order:
switch, item, fight (move selection happens later, via the
AI script). `TrainerAI_ShouldSwitch` (3894-3987) decides the
switch and, when it fires, may leave
`aiSwitchedPartySlot[battler] = 6` — a sentinel meaning "use
the post-KO switch picker" (`BattleAI_PostKOSwitchIn`, falling
back to the first living, unselected bench mon in party
order, 4004-4028).

**Never switch** (3906-3913) when the battler is under the
TRAPPED volatile, under Ingrain, when any battler on the AI's
side has Shadow Tag or Arena Trap, or when an ally has Magnet
Pull and the battler is a Steel type. (Decomp note: this
check is naive — it does not credit a Shed Shell, or immunities
such as Flying vs Arena Trap.)

Then, if any eligible living bench mon exists, the checks run
in this order; the first that fires wins:
1. **Perish Song** (3263): effect active and on its final turn
   (`perishSongTurns == 0`) → switch, slot 6. Deterministic.
2. **Wonder Guard** (3286, solo battles only): the opponent
   has Wonder Guard and the active battler has no
   super-effective move → each bench mon with a
   super-effective move: 66% chance of switching to it
   (`RandNext % 3 < 2`).
3. **Only ineffective moves** (3361): the battler has ≥ 2
   damaging moves and every one is ineffective against both
   opponents (in solo, both checks are the same defender) →
   first bench pass: super-effective damaging move against
   either opponent → 66%; second pass: neutral
   (effectiveness 0) damaging move → 50%.
4. **Absorb ability on the bench** (3640): skipped when the
   active battler has a super-effective move — **67% of the
   time in code** (`RandNext % 3 != 0`; the doc comment says
   "roughly 33%"). Requires the last move to have hit the
   battler (`moveHit`) and be a damaging Fire / Water /
   Electric move, and the active battler itself to lack the
   matching ability (Flash Fire / Water Absorb / Volt Absorb)
   → bench mon with exactly that ability → 50% switch.
5. **Asleep with Natural Cure** (3817): asleep, Natural Cure,
   HP ≥ 50%. Cascade, each 50% (slot 6 = post-KO picker):
   no move has hit the battler yet; or the last move that hit
   was a status move; or a bench mon immune to the last move
   that hit has a super-effective move (rand 1 = always once
   found); or a resistant bench mon with a super-effective
   move; else an outright coin flip.
6. **Has a super-effective move** (3560, `flag = FALSE`):
   against the defender directly across (slot `^ 1`), and in
   doubles also that defender's partner; a defender currently
   switching is skipped. Any super-effective move → returns
   TRUE 90% per move (`RandNext % 10 != 0`). In
   `TrainerAI_ShouldSwitch` a TRUE here **stops switching**
   (return FALSE, 3964-3966).
7. **Heavily stat-boosted** (3872): sum of (stage − 6) over
   every stat with stage ≥ 7 is ≥ 4 → stop switching
   (3969-3971).
8. **Immunity + super-effective** (3727, mask 0x8 =
   INEFFECTIVE, rand 2): the last move that hit (damaging)
   would be ineffective against a bench mon that also has a
   super-effective move against the attacker who hit → 50%
   switch. **Comment says "33%" — code is 50%.**
9. **Resistance + super-effective** (same helper, mask 0x4 =
   NOT_VERY_EFFECTIVE, rand 3): same shape → 33% switch.
   **Comment says "25%" — code is 33%.**

**Post-KO sendouts (forced switch-ins).** When an AI's active
mon faints, the battle enters party selection for that battler
with `selectedPartySlot[battler] = 6` ("unselected"). The AI's
menu task, `Task_TrainerShowPartyMenu`
(`battle_display.c:4528-4566`), resolves it without a real box
menu:
1. `slot = BattleAI_SwitchedSlot(...)` — an already-committed
   AI switch slot wins.
2. Otherwise `slot = BattleAI_PostKOSwitchIn(...)`
   (`battle_lib.c:7923-8089`) — the post-KO picker (below).
3. If that also returns 6 (no eligible mon), the first living
   party slot not selected by either side (4548-4557).
The chosen slot is emitted as the party-menu result
(`1 + slot`, 1-indexed) along with the pending command
(4561-4562).

**`BattleAI_PostKOSwitchIn`** (`battle_lib.c:7923-8089`).
Defender = a random opponent
(`BattleSystem_RandomOpponent`). Eligible slots: non-egg /
non-empty, HP > 0, not selected, and not the AI's committed
switch slot (either side).
- **Stage 1 — type matchup + super-effective move.** Each
  eligible mon is scored as the sum of its two type-matchup
  multipliers against the defender's types (a mono-type counts
  twice); highest wins, ties broken by party order. The
  winner is only accepted if it has at least one
  super-effective move against that defender (full
  effectiveness calc including abilities and held items,
  7996-8016); otherwise that slot is marked "disregarded" and
  the scan repeats (up to all 6, `battlersDisregarded` mask
  0x3F, 7959-8029). The first mon that is both the top
  matchup and has a SE move is returned immediately (8023).
- **Stage 2 — damage, only if stage 1 fails.** Each eligible
  mon is scored by the **maximum non-critical damage** its
  moves would do to the defender (moves with power ≠ 1; type
  chart applied; immune → 0, 8038-8086); highest wins, ties
  broken by party order.
If neither stage finds a mon, 6 is returned → the
first-living fallback above. Decomp note: `score` /
`maxScore` are `u8` (7933, "Post-KO Switch-In AI Scoring
Overflow", see `docs/bugs_and_glitches.md`) — stage-2 damage
values over 255 wrap around.

**Shared with voluntary switches.** The same picker serves the
voluntary path: in `TrainerAI_PickCommand` (4004-4028), any
switch fired by a §4 check leaves
`aiSwitchedPartySlot = 6` — "use the post-KO picker"; if that
returns 6, the same first-living fallback runs (4013-4024).
Post-KO sendouts and voluntary switches therefore pick their
replacement by identical logic; only the trigger (KO vs. a §4
check) and the entry point (party menu vs. command phase)
differ.

## §5 Item use

`TrainerAI_ShouldUseItem` (4056-4199) is called from
`TrainerAI_PickCommand` (4034) immediately after the switch
check. State is kept per side: all buffers are indexed by
`battler >> 1` (battlers 0/1 share index 0, 2/3 share 1), so
the AI's two mons share one item pocket.

**Gates.** The AI's partner (side B, slot 2) never uses items
(4070). Embargo active on the battler → no item (4076).

**Pocket iteration.** The trainer data carries up to
`MAX_TRAINER_ITEMS = 4` items per trainer
(`TrainerHeader.items`, `include/struct_defs/trainer_data.h`).
Slots are walked in order 0-3; slot `i > 0` is only examined
when `aliveMons <= trainerItemCounts - i + 1` (4092) — i.e.,
bigger pockets are only consulted late in a battle, when few
of the trainer's mons remain. The **first item that matches a
use condition wins**; it is recorded in `AI_CONTEXT.usedItem`,
the item type/condition buffers are set, and the slot is
zeroed (4191-4194).

**Use conditions** (checked per item, in this order):
- **Full Restore**: battler HP > 0 and < 25% of max.
- **HP restorers** (Potion family; items with a known heal
  value): battler HP > 0 and (HP < 25% of max **or** the HP
  deficit exceeds the item's heal value — the item would be
  fully consumed).
- **Status cures**, matched to the condition: sleep,
  poison/toxic, burn, freeze, paralysis, confusion. On use,
  the matching `usedItemCondition` flag bit is set.
- **Stat boosters** (X Attack / Defense / Sp. Atk / Sp. Def /
  Speed / Accuracy): only when
  `moveEffectsData.fakeOutTurnNumber - totalTurns >= 0`
  (4156) — the decomp comments this as "not until after the
  first turn in play". The first boosting stat the item
  carries is used.
- **Guard Spec**: only when Mist is not already on the AI's
  side.
- Any other item type: marked `ITEM_AI_CATEGORY_MAX` (no use).

## §6 Trainer flag assignments

**Where the mask lives.** `TrainerHeader`
(`include/struct_defs/trainer_data.h:28-32`) =
`{ u8 partySize; u16 items[4]; u32 aiMask; u32 battleType; }` —
`aiMask` is byte 10 of each trainer record. The record is a binary
NARC member: `poketool/trainer/trdata.narc`, loaded whole by
`Trainer_Load` (`src/trainer_data.c:154-157`, index
`NARC_INDEX_POKETOOL__TRAINER__TRDATA`); only the `TRDATA_AI_MASK`
param (`trainer_data.c:86-88`) reads it. The per-trainer values live
in that NARC, which is **not in this decomp checkout**
(`generated/trainers.txt` is names only), so no per-trainer
assignment can be quoted from source here.

**How it is applied** (`TrainerAI_Init`, `trainer_ai.c:243-256`):
1. Roamer battle (`BATTLE_TYPE_ROAMER`) →
   `thinkingMask = AI_FLAG_ROAMING_POKEMON` (bit 29), overriding the
   trainer's own mask.
2. Otherwise → `thinkingMask = trainers[battler].header.aiMask`.
3. Doubles → `thinkingMask |= AI_FLAG_TAG_STRATEGY` (bit 7), forced
   on for *every* doubles battle.
4. Battle Frontier overwrites `header.aiMask` for all battlers
   before step 2 (table below), so the NARC values are ignored
   there.

**Bit numbering.** `pokeplatinum/generated/ai_flags.txt` (34 lines):
line 1 = `AI_FLAG_NONE` = 0; line N (N ≥ 2) = `1 << (N-2)`.
Active bits: 0 `BASIC`, 1 `EVAL_ATTACK`, 2 `EXPERT`,
3 `SETUP_FIRST_TURN`, 4 `RISKY`, 5 `PRIORITIZE_EXTREMES`,
6 `BATON_PASS`, 7 `TAG_STRATEGY`, 8 `CHECK_HP`, 9 `WEATHER`,
10 `HARRASSMENT`, 11-28 `UNUSED_11`-`UNUSED_28`, 29
`ROAMING_POKEMON`, 30 `SAFARI`, 31 `CATCH_TUTORIAL`. Line 34
`AI_FLAG_ALL` = `1 << 32`, out of range for the mask — unused (see
§3 flag table). HG uses the same numbering: its only named
constants are `AI_DOUBLES (1<<7)` and `AI_29 (1<<29)` — exactly the
`TAG_STRATEGY` and `ROAMING_POKEMON` bits
(`pokeheartgold/include/constants/battle.h:560-561`).

**Battle Frontier masks.** Every facility overwrites
`dto->trainer[i].header.aiMask` for all battlers at DTO construction
time. "Full" = `BASIC|EVAL_ATTACK|EXPERT` = 0x7; `BASIC` = 0x1.

|Facility (source)|Mask|
|---|---|
|Battle Tower, any mode (`src/overlay104/ov104_0223A0C4.c:926-928`)|always 0x7|
|Battle Castle, single vs head Darach (`overlay104/battle_castle_helpers.c:294-298`)|0x7|
|Battle Castle, all other battles (`:301-315`)|rounds 1-2 → 0; 3-4 → `BASIC`; 5+ → 0x7 (round = 0-based current round, `BattleCastle_GetCurrentRound`)|
|Battle Hall, multi challenge (`overlay104/battle_hall_helpers.c:1383-1385, 1578-1580`; rank forced to max)|0x7 for all|
|Battle Hall, single vs Matron Argenta (`:1569-1575`)|0x7|
|Battle Hall, single vs everyone else (`:1561-1567`)|rank+1 ≥ 8 → 0x7; rank+1 ≥ 4 → `BASIC`; else 0|
|Battle Factory, single vs head Thornton (`overlay104/ov104_0223A7F4.c:486-490`)|0x7|
|Battle Factory, all other battles (`:493-507`)|rounds 1-2 → 0; 3-4 → `BASIC`; 5+ → 0x7 (round getter `ov104_0223AF34`, 0-based)|
|Battle Arcade, single vs Star Dahlia (`overlay104/ov104_0223BCBC.c:347-351`)|0x7|
|Battle Arcade, all other battles (`:354-368`)|same round ladder (getter `ov104_0223C124`)|

The facility heads (Darach, Argenta, Thornton, Dahlia) appear as
`_SILVER`/`_GOLD` ID variants — the Silver/Gold version of the
facility's top trainer is always full-AI in a single challenge.

**Story-battle distribution.** The plan's example patterns ("gym
leaders use flags 0+1+2+3", "wild Pokémon use flag 29 only", "most
random trainers use flag 0 only") are targets to verify. This
checkout proves only the roamer override (bit 29 for every
`BATTLE_TYPE_ROAMER` battle — the same rule in both decomps, §7).
What a normal wild encounter's pseudo-trainer record carries is in
`trdata.narc` and is not quotable here.

## §7 HG vs Platinum

**Both games are script-driven** — the "HG is monolithic C"
framing is not supported by this checkout:

- **Platinum**: `trainer_ai.c` (4199 lines: interpreter +
  helpers) + `script.s` (8105 lines, annotated). The script is a
  *compiled word array* — `gTrainerAITable` is defined at
  `script.s:15-16`, aliased into the context by
  `battle_controller_player.c:4816`
  (`battleCtx->aiScriptTemp = gTrainerAITable`), and executed
  through the 109-entry `sAICommandTable` (§2). The ROM-side
  container `battle/tr_ai/tr_ai_seq.narc`
  (`src/narc.c:113`, `NARC_INDEX_BATTLE__TR_AI__TR_AI_SEQ`) exists
  at `res/prebuilt/battle/tr_ai/` but the decomp runs the linked
  table. DSL macros: `asm/macros/aicmd.inc`; opcode enum:
  `include/data/scripts/aicmd.h` (0-108).
- **HG**: `src/battle/trainer_ai.c` (67 lines: init + main
  dispatch, annotated) + `asm/overlay_10_trainer_ai.s` (12590
  lines of raw Thumb — 140 functions
  `ov10_0221BF44`…`ov10_0222B0B4`, **no semantic annotations**)
  + a **109-entry function-pointer dispatch table at
  `0222B0B4`** (dump lines 12482-12590) — the same table size as
  Platinum's opcode count, i.e. the same interpreter shape. The
  HG script *data* (`tr_ai_seq.narc`; the HG checkout has no
  `res/` tree and no `narc.c`) is absent, so the two scripts
  cannot be compared here.
- `src/battle/overlay_12_0224E4FC.c` (6719 lines, partially
  annotated) is **battle core, not AI**: damage
  (`CalcMoveDamage`, `ApplyDamageRange`, `TryCriticalHit`),
  legality (`StruggleCheck`, `Battler_CanSelectAction`,
  `CanSwitchMon`, `BattlerCanSwitch`), held items
  (`TryUseHeldItem`, `GetBattlerHeldItemEffect`). Its header
  declares the `ov10_0221*` AI functions with the note "the
  following functions haven't been decompiled as of now, and are
  in fact in different files" — the bridge to the raw asm overlay.

**Init parity** (the 67-line C file,
`pokeheartgold/src/battle/trainer_ai.c`):

|HG (`ov10_0221BE20`, lines 9-47)|Platinum (`TrainerAI_Init`)|
|---|---|
|zero the AI-struct prefix up to `moves`|same (`AI_CONTEXT`)|
|per-move score 100/0 from a 4-bit mask (callers pass 15 = all four)|same (`AI_INIT_SCORE_ALL_MOVES` = 15)|
|struggle-invalid moves → 0 (`StruggleCheck`)|same (`BattleSystem_CheckInvalidMoves`, `CHECK_INVALID_ALL`)|
|damage roll `100 - (BattleSystem_Random % 16)` → [85,100]|same: `moveDamageRolls[i] = 100 - (RandNext % 16)`|
|`unk98 = 0`|`scriptStackSize = 0`|
|roamer → `aiFlags = AI_29` (1<<29)|`thinkingMask = AI_FLAG_ROAMING_POKEMON` (bit 29 — same bit)|
|else `trainers[battler].data.aiFlags`|`trainers[battler].header.aiMask` (same NARC source)|
|doubles → `aiFlags \|= AI_DOUBLES` (1<<7)|`thinkingMask \|= AI_FLAG_TAG_STRATEGY` (bit 7 — same bit)|

`ov10_0221BEF4` (49-67) mirrors `TrainerAI_Main`
(`trainer_ai.c:258-277`): continue bit (`unk10 & 0x10` ↔
`AI_STATUS_FLAG_CONTINUE`), random opposing battler, then
singles/doubles split (`ov10_0221BF44` / `ov10_0221C038` ↔
`TrainerAI_MainSingles` / `TrainerAI_MainDoubles`).

**Field renames**: `trainerAIData`↔`AI_CONTEXT`,
`movePoints`↔`moveScore`, `unk18`↔`moveDamageRolls`,
`unk10`↔`stateFlags`, `aiFlags`↔`thinkingMask`,
`unk98`↔`scriptStackSize`.

**What this checkout can and cannot verify.** Verified identical:
the AI-data layout, the flag bit numbering (§6), the init
sequence, the main dispatch, the damage-roll distribution, and the
109-way command dispatch shape. Not verifiable: everything the
decision body does — per-move scoring, switching, item choice —
because the HG functions are unannotated Thumb and the HG script
NARC is absent. For a reimplementation: one engine serving both
games is well supported (same bits, same init, same dispatch
width); the per-game *script contents* may differ, but that
cannot be shown from this checkout, so Platinum's documented
behaviour (§1-§5) is the working baseline for HG, with any
HG-specific divergence unverified.

## §8 Cross-check vs AGENT_PLAN.md

Plan lines cited as `P<line>` refer to `AGENT_PLAN.md`.

**Verified as stated.**

- P183-186: file locations and sizes —
  `trainer_ai.c` = 4199 lines, `script.s` = 8105 lines ✓; the
  "11 named AI flags" at the top of the script — §3 documents 14
  named sections: the 11 trainer-data flags (bits 0-10) plus
  `ROAMING_POKEMON` (29), `SAFARI` (30), `CATCH_TUTORIAL` (31),
  the last three forced by battle type, not by trainer data (§6).
- P212: all four move slots start at 100
  (`TrainerAI_Init`, `trainer_ai.c:223-231`,
  `AI_INIT_SCORE_ALL_MOVES` = 15) ✓.
- P212-213: unusable moves set to 0 and skipped
  (`trainer_ai.c:234-238`) ✓.
- P214: `100 - rand(16)` ✓ (`trainer_ai.c:240`) — but the value
  is the per-move **damage-estimate baseline** stored in
  `moveDamageRolls` (range [85,100]), consumed by the damage
  comparison commands (§2); it is not an additive bonus added to
  the score.
- P217-218: highest final score wins, ties broken randomly —
  `trainer_ai.c:316-338` (singles) and `:405-428` (doubles): all
  max-tied moves collected, one picked with `RandNext % n` ✓.
- P283-284: the DSL macros live in `asm/macros/aicmd.inc` ✓
  (opcode enum in `include/data/scripts/aicmd.h`).
- P265 "wild Pokémon use flag 29": true for **roamer** battles —
  `AI_FLAG_ROAMING_POKEMON` (bit 29) is force-assigned in
  `TrainerAI_Init` (`trainer_ai.c:246-247`), and the identical
  `AI_29` in the HG init (§7). Whether *regular* wild encounters'
  pseudo-trainer records carry any mask is in `trdata.narc` and
  unquotable here.

**Discrepancies / corrections.**

- **P221-223 "~80 `AICmd_*` commands" → 109.**
  `include/data/scripts/aicmd.h` defines opcodes 0-108 (109
  total); the §2 table lists all 109, including the spin-loop
  `Dummy3E/3F` pair (§2).
- **P273 "HG's is monolithic C" → wrong.** HG is
  script-driven with a 109-entry function-pointer dispatch table
  (§7); what is *monolithic and unannotated* is the raw Thumb
  overlay. The plan's pointer to `overlay_12_0224E4FC.c` as the
  HG AI bulk (P270, P289-290) also fails: that 6719-line file is
  battle core (damage, legality, items); the AI decision
  functions are the `ov10_0221*` set in
  `asm/overlay_10_trainer_ai.s`, and the HG script NARC is absent
  from the checkout.
- **P248-249 "look for `ShouldSwitch` in
  `overlay_12_0224E4FC.c` / `trainer_ai.c`"** — for Platinum it
  is `TrainerAI_ShouldSwitch` in `trainer_ai.c:3906+` (§4); the
  HG-side search target does not exist in the checkout.
- **P257-258 item logic** — Platinum: `trainer_ai.c:4070-4194`
  (§5). HG: not present in `overlay_12_0224E4FC.c`.
- **P262-264 trainer-data locations** —
  `pokeplatinum/res/battle/trainer/` does not exist (the NARC is
  `poketool/trainer/trdata.narc`, `src/narc.c:67`; not in the
  checkout); `pokeheartgold/data/trainers/` does not exist (the
  HG checkout has no `res/` tree at all); `generated/` holds
  names only (`trainers.txt`) plus the flag *name* table
  (`ai_flags.txt` — P281 calls it `ai_flags.h`; it is a `.txt`).
  Consequence: the example distributions at P265-266 ("gym
  leaders use flags 0+1+2+3", "most random trainers use flag 0
  only") cannot be verified from this checkout; §6 documents
  everything the source does prove (storage, application,
  bit numbering, all five Frontier facilities' computed masks).

**Decomp anomalies recorded in this document** (details at the
cited §-locations; this is the inventory a reviewer checking
"does the doc match the decomp" should expect):

- *Comment/code mismatches (14)*: script.s:78 BUG note (Dry
  Skin branch) — doc 686; dead comment at 567 — doc 861; a
  usable move still never chosen against Ghost, −10 — doc 934;
  1164-66 and 1174-76 (Stall / Shiny Stone) — doc 993-995;
  3616 — doc 1568; 3781-83 — doc 1600; 4117-18 — doc 1664;
  "list does not include Macho Brace" — doc 1681; 4614 — doc
  1745; Discharge partner Ground-check order (7252-54) — doc
  2201; Surf partner missing Rock typing (7291) — doc 2211;
  Lava Plume Dry Skin comment +3 vs code −3 (7304-07) — doc
  2215; partner-poison threshold 91 vs comment "81%" (7575) —
  doc 2247; −30 branch without a partner-alive check — doc
  2248.
- *Dead / unreachable code (10)*: `0xFF` action case
  (`battle_display.c:3592-3599`) — doc 140-141; `Dummy3E/3F`
  operand never read — doc 555, 2320; `SKIP_CHARGE_TURN_IN_SUN`
  1717 duplicating 1716, making `Expert_UnusedSolarbeam`
  unreachable — doc 1168, 1244, 1629; dead label at the snatch
  check — doc 1400; `IfRandomLessThan 50` after `GoTo End` —
  doc 1723; `ChargeTurnWithInvuln` / `Recovery_Unused` — doc
  2032; `TagStrategy_Unused_1/2` (7113-7122) — doc 2193;
  unreachable Surf re-check (7277) — doc 2207; `ScoreMinus6`
  unused — doc 1092; `AI_FLAG_ALL` = 1<<32 out of range — doc
  666.
- *Missing guards / unrecorded values (7)*: no 0xFFFF guard in
  `LoadDefenderLastUsedMoveClass` (2050) — doc 607; default
  penalty absent from comment 4699-01 — doc 1766; a −2 penalty
  the comment never mentions — doc 1898; absorb-ability skip is
  67%, comment says 33% (`trainer_ai.c:3651`) — doc 2365;
  immune+SE switch is 50% (comment 33%) and resistant+SE is
  33% (comment 25%) (`trainer_ai.c:3975-3981`) — doc 2383; the
  AI's doubles partner (slot 2) never uses items (4070) — doc
  2462; a `CheckHP` target landing in the wrong band — doc
  2264.

**Reviewer checklist mapping** (P319-324): scoring model →
§1/§2/§3 (verified against `trainer_ai.c`); flag breakdown → §3
(14 sections) + §6 (bit table); AI flag assignments → §6
(mechanism, Frontier masks; per-trainer values in
`trdata.narc`, absent from this checkout — the one explicit gap).
