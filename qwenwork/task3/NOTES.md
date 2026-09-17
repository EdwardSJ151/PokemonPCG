# Task 3 working notes

Running log of pinned facts and port decisions. Source of truth over any earlier
handoff summary where they disagree. Updated as implementation proceeds.

## Re-verified this session (fresh reads)

- **Fork typechart convention** (`dist/data/typechart.js`): `TypeChart[defenderType].damageTaken[attackerType]` with
  **1 = super-effective, 2 = resisted, 3 = immune, 0/absent = neutral** (`dex.getEffectiveness`, sim/dex.ts:271-281).
  Gen4 mod does **not** override the typechart. Cross-check vs the decomp table:
  **287/289 cells identical**; only deviations: `ghost→steel` and `dark→steel` = 0.5 in decomp, neutral in fork.
  **Decision: port keeps the decomp table** (data_typechart.js as generated); the AI will estimate 0.5x for those
  two corner cells where the fork deals neutral damage. Documented deviation.
- **`BattleSystem_Divide`** (battle_lib.c:3599-3620): truncating integer division; nonzero dividend with
  truncated 0 rounds to ±1 (9/10 → 1, 19/10 → 1). All class-space divisions (40/60 × 5/20 ÷ 10) are exact, so plain
  `/` is fine there.
- **`BattleSystem_TypeMatchupMultiplier`** (battle_lib.c:3009-3033): starts `mul=40`, walks the **raw chart**
  (no sentinel skip, no overrides) matching atkType against the defender's 1 or 2 types; returns the 40-scaled value
  (0,10,20,40,80,160). PostKO stage 1 score = `mul(monType1) + mul(monType2)` (mono-type: type counted twice).
- **CalcEffectiveness (switch variant)** (battle_lib.c:2681-2757): pre-chart Levitate branch sets
  **INEFFECTIVE** (not LEVITATED); condition `attAbility != MB && defAbility == LEVITATE && moveType == GROUND &&
  !gravity && defItem != IRONBALL` — no magnet-rise branch, no ingrain check. 0xFE break **only for Scrappy**
  (raw attAbility arg). Overrides: NoImmunityOverrides (grounding item/Gravity vs FLYING-immune only).
  Post-chart: `attAbility != MB && defAbility == WONDER_GUARD && onDamagingTurn && !(mask & SE)` →
  **mask |= INEFFECTIVE** (no WONDER_GUARD bit, no movePower requirement, no Filter/TintedLens).
- **ApplyTypeChart (active variant) post-chart** (battle_lib.c:2652-2692): WG condition =
  `ignorable(WG) && onDamagingTurn && ((mask & SE)==0 || (mask & (SE|NVE))==(SE|NVE)) && movePower` → mask |= WONDER_GUARD
  (second disjunct is a decomp tautology artifact: SE+NVE never co-set; effectively "not super-effective").
  Then else-branch (no IGNORE flags): `mask & SE && movePower` → Filter/Solid Rock ×3/4 (ignorable-gated);
  `mask & NVE && movePower` → Tinted Lens ×2 (attacker raw ability). IGNORE branch: `mask &= ~(SE|NVE)`.
- **TrainerAI_CalcDamage** (trainer_ai.c:2868-3113): per-move special cases: Natural Gift (item NG power/type;
  Klutz or embargo → power 0, type Normal), Judgment (plate type, power 0), Hidden Power (IV-bit formula:
  power = bits6*40/63+30, type = bits6*15/63+1, ++ past ???), Gyro Ball (1+25*defSpeed/atkSpeed, cap 150),
  Dragon Rage 40, Seismic Toss/Night Shade = level, Psywave = level*(5..15)/10, Return = friendship*10/25,
  Frustration = (255-friendship)*10/25, Magnitude (5 tiers of %100), Sonic Boom 20, Low Kick/Grass Knot (weight table).
  Flat-damage moves set **SYSCTL_IGNORE_TYPE_CHECKS** around ApplyTypeChart (immunity still applies, SE/NVE cleared).
  **Port decision (handoff): all damage estimates use fork `dmg()`** — the fork natively implements all of the above
  and is the engine of record. The decomp lists survive only for *eligibility* (which slots get a damage score).
- **TrainerAI_MoveType** (trainer_ai.c:3127-3248): only Natural Gift / Judgment / Hidden Power / Weather Ball are
  variable; **everything else returns TYPE_NORMAL (= 0 = "use the move's own type")**. Weather Ball:
  `NO_CLOUD_NINE && fieldConditionsMask & FIELD_CONDITION_WEATHER` → four independent `if`s
  (RAIN→water, SAND→rock, SUN→fire, HAIL→ice; last match wins; no weather → **uninitialized** → port as Normal).
- **Battler_IsTrapped** (battle_lib.c:3512+): Shed Shell → FALSE; TRAPPED volatile || INGRAIN → TRUE;
  (own ability != Shadow Tag && other own-side alive with Shadow Tag) || (Steel type && other own-side alive with
  Magnet Pull) → TRUE; (grounded && other own-side alive with Arena Trap) → TRUE.
- **CompareBattlerSpeed** (battle_lib.c:1188+): dead-short-circuits; adjusted speed via `sStatStageBoosts`
  (stage after `CompareSpeed_ApplySimple` clamping), weather Swift Swim/Chlorophyll ×2 (Cloud Nine-gated),
  Quick Claw / Lagging Tail / move priority tiebreaks. **Port simplification: rank by fork effective speed
  (stage-adjusted, weather-ability ×2 when active) — priority/claw details irrelevant for AI ranking use.**
- **Held item accessors**: `Battler_HeldItem` = **observed** item for opponent (battlerHeldItems cache), actual for
  own side. `Battler_HeldItemPower(CHECK_EMBARGO)` → 0 when embargoed. Fling power/effect → 0/NONE when embargoed.
  **Port decision: use the actual `p.item` for both sides** (in-process AI knows it; the fork exposes it in the
  request JSON) — deviation from the decomp's observation model.

## Re-verified: opcode section (trainer_ai.c:528-2270, full read this session)

- Pattern: `AIScript_Iter(1); read params; [compute calcTemp / condition-jump]`. In the JS port each op becomes
  `AICmd_<name>(…params) → void|bool` on the engine using `this.ctx`; jump params disappear (control flow is the
  module code itself).
- **LoadBattlerAbility (1170-1211)**: suppressed → ABILITY_NONE. Opponent: known ability if cached, or if raw
  ability ∈ {Shadow Tag, Magnet Pull, Arena Trap}; else **coin-flip between the species' ability1/ability2**
  (`RandNext() & 1` → ability2, else ability1 — note the 1→ability1 mapping in the body).
  **Port decision (handoff): give the AI the real `p.ability` (suppression-aware), no coin-flip** — fork exposes
  the true ability; the coin-flip is a hardware-observation artifact.
- **IfBattlerUnderEffect (1823)**: DISABLE → any moveSlot disabled; ENCORE → encore volatile. Fork source:
  `p.moveSlots[].disabled` (taunt/disable/imprison set it) and `p.volatiles.encore`.
- **IfCurrentMoveMatchesEffect (1850)**: DISABLE → the *current move's* slot `.disabled`; ENCORE → `volatiles.encore?.move?.id` == current move.
- **LoadProtectChain (2538)**: decomp `moveProtect[battler]` last-protect move + `protectSuccessTurns` counter.
  **Port: 0 unless defender is under the Protect/Detect/Endure family this turn** (fork: `p.volatiles.protect` /
  `.detect` / `.endure` — set for the duration of the turn the move was used; protectSuccessTurns ≈ 1 while active).
- **LoadIsFirstTurnInBattle (2480)**: `fakeOutTurnNumber < totalTurns → FALSE else TRUE` → i.e. TRUE when the mon
  entered this turn (entryTurn >= currentTurn). Matches observer design.
- **LoadBattlerTurnCount (2096)**: `totalTurns − fakeOutTurnNumber`. Mon from battle start at turn T → T; mon that
  switched in during turn T (seen at T+1) → 1. Matches `battle.turn − entryTurn + 1` (observer entryTurn set at the
  turn the `|switch|` is first visible).
- **SumPositiveStatStages (2086)**: loops **all 8** stats (HP..Evasion), sums `max(0, stage−6)`. HP stage is always 0
  (no HP boost key) → contributes 0.
- **IfBattlerHasHigher/Lower/EqualStat (2098-2124)**: `TrainerAI_GetStats` (2330): HP → curHP; else the **raw
  (unboosted) stat value** (`battleMons[].attack` etc.). **Port: `p.calculateStat(key, 0, 1)` with boost 0 = unboosted
  fork stat** — matches the decomp's raw stat fields.
- **LoadBattlerSpeedRank (2056)**: bubble-sort all 4 slots by CompareBattlerSpeed (TRUE = battler1 faster;
  the comparator in the sort swaps cmp1/cmp2 accordingly); rank = final position (0 = fastest slot, descending).
  **Port: sort alive actives by effective speed desc (Tie: stable order = slot order); rank = index.**
- **IfPartyMemberDealsMoreDamage (2068)**: CalcAllDamage(self, self's moves) vs each eligible bench mon's
  CalcAllDamage (own held item, raw ability, no embargo); jump if any bench mon's max > self's max.
- **IfBattlerDealsMoreDamage (2080)**: self's CalcAllDamage vs `TrainerAI_CalcDamage(foe's last-used move, roll)`.
- **IfHasSuperEffectiveMove (2074)**: `AI_HasSuperEffectiveMove(attacker, TRUE)` — "any SE" (see ShouldSwitch §4).
- **CheckIfHighestDamageWithPartner (2104)**: doubles only; skip in singles.
- **IfBattlerFainted/NotFainted (2110/2116)**: `battlersSwitchingMask & FlagIndex(battler)` — "switching mask" is set
  when a battler just fainted / is being switched out. Fork: opponent's active `p.fainted` at decision time.
  **Port: `def.fainted` / `!def.fainted`** (partner asserts impossible in singles).
- **IfLevel (2579)**: attacker level vs defender level (higher/lower/equal). Fork: `p.level`.
- **IfTargetIsTaunted (2610)**: defender's taunt counter > 0 → fork `def.volatiles.taunt` (present while active).
- **IfTargetIsPartner (2630)**: `attacker & 1 == defender & 1` → FALSE in singles (opponents are on different sides).
- **IfActivatedFlashFire (2641)**: `moveEffectsData.flashFire` — set the turn a Fire move was blocked.
  **Port: observer: watch the log for `|msg|` lines like the fork's Flash Fire block (needs format check; likely
  "But it fails!" / ability line) — or approximate: defender has Flash Fire (effective) and last hit was a
  fire-type move that did 0 damage.** (Resolve when writing expert op 84; low impact.)
- **LoadAbility (2654)**: `Battler_Ability` (suppression-aware, gravity/ingrain-aware).
- **PushAndGoTo/GoTo/PopOrEnd (75/76/77)**: cursor stack = sub-routine calls. **Port: plain JS function calls/returns;
  PopOrEnd = "return from sub-routine, or set DONE if none."**
- **LoadTypeOfLoadedMove / Power / Effect (81-83)**: `MOVE_DATA(calcTemp).{type,power,effect}` — the currently
  loaded move id in calcTemp. **Port: lookup the fork move from `this.ctx.calcTemp` (a move id string).**
- **LoadRecycleItem (79)**: `recycleItem[battler]` — item recovered this turn (Recycle). **Port: 0 unless the mon
  used Recycle and the recovered item is known** — decomp fills this when the Recycle effect fires; the fork
  transcript has a `|enditemeffect|`/recovered-item line. Low impact; port as 0 until needed (no module in scope uses it?
  verify at expert port).
- **LoadStockpileCount (68)**: stockpile layers. **Fork storage: `volatiles.stockpile` state — layer storage
  unverified (probe pending).**

## Pinned constants

- `sStatStageBoosts` num/den (for raw-stat and speed calcs): decomp table [0]1/1 [1]1/16 [2]1/8 [3]1/4 [4]1/2
  [5]2/2 [6]2/1 [7]4/1 [8]8/1 [9]16/1. **Fork: `p.calculateStat` implements the same math natively — use it.**
- BATTLE_STAT order: HP=0 ATTACK=1 DEFENSE=2 SPEED=3 SP_ATK=4 SP_DEF=5 ACCURACY=6 EVASION=7 MAX=8.
  **Fork boost keys: atk, def, spe, spa, spd, accuracy, evasion** (no hp key).
- MOVE_STATUS bits: MISSED=1 SE=2 NVE=4 INEFF=8 CRIT=16 OHKO=32 FAILED=64 ENDURED=128 ENDURED_ITEM=256 NO_PP=512
  BYPASS_ACC=1024 LEVITATED=2048 OHKO_FAILED=4096 SPLASH=8192 MULTI_HIT_DISRUPTED=16384 PROTECTED=32768
  SEMI_INVULN=65536 LOST_FOCUS=131072 WONDER_GUARD=262144 STURDY=524288 MAGNET_RISE=1048576.
  IMMUNE = INEFF|WG|LEVITATED|MAGNET_RISE; BASIC_EFFECTIVENESS = SE|NVE (=6).
- Class space: 0 immune, 10 ×¼, 20 ×½, 40 ×1, 60 ×1+STAB, 80 ×2, 160 ×4. Mapping from 40-based scaled:
  120→80, 240→160, 30→20, 15→10, else passthrough; any IMMUNE flag → 0.
- AI flags (bits): 0 BASIC, 1 EVAL_ATTACK, 2 EXPERT, 3 SETUP_FIRST_TURN, 4 RISKY, 5 PRIORITIZE_EXTREMES,
  6 BATON_PASS, 7 TAG_STRATEGY, 8 CHECK_HP, 9 WEATHER, 10 HARRASSMENT, 11-26 UNUSED (no script — skip),
  29 ROAMING, 30 SAFARI, 31 CATCH_TUTORIAL (out of scope).
- **CONTINUE flag (0x10) is dead**: zero-init, never set anywhere in trainer_ai.c. Each set-flag module runs its
  script independently per move (cursor reloaded in INIT for every move of every flag). No chaining.
- EvalMoves per-move: INIT (cursor=entry; move = pp>0 ? moves[slot] : none) → if move none: score=0, DONE;
  else dispatch; DONE → next slot (until slot 4 or BREAK); BREAK persists across moves and flags (ends evaluation).
- MainSingles final pick: `maxScoreMoves[0] = score[0]` **unconditionally** (slot 0 seeds even with no move);
  slots 1-3 only if `moves[i] != 0`; equal appends, higher resets; pick `list[RandNext() % len]`.
- **Fork-safety deviation (document)**: slots with `pp == 0` or `disabled` are excluded from the final pick;
  if no candidate slot at all → return `'default'`. (Decomp may pick a locked-out move and waste the turn;
  the fork would reject/stall.)
- Item bag: decomp `trainerItems[side][0..3]` (max 4). `shouldUseItem` chain has **no break** — every matching
  slot is zeroed and `usedItem` overwritten → **last matching slot wins**. Slot examined iff
  `i == 0 || aliveMons <= initialItemCount - i + 1`.
- **Item execution (fork deviation)**: fork has no consumable items and no bag choice → AI executes the item
  directly on the live pokemon at decision time (`heal`/`cureStatus`/`boost` + one transcript line), then proceeds
  to its move. The decomp consumed the turn; the fork has no "use item" action.

## Open items (verify as reached)

- Fork `volatiles.stockpile` layer storage shape (op 68).
- Flash Fire block log-line format (op 84).
- Fork `dex.getHiddenPower` gen4 formula vs decomp IV-bit formula (should match; cross-check once).
- `p.boosts` key names (probe said atk/def/spa/spd/spe/accuracy/evasion — re-confirm at first use).

## Session 2026-09-16 — eff() core validated; fork protocol pinned

### eff() — all 24 smoke checks pass (eff_smoke.js)

- **Fork types are capitalized** (dex `groudon`.types = `['Ground']`); the engine normalizes at every
  boundary with `normType` (lowercase alnum). Fork gen4 dex species types: Groudon = **Ground**
  (fork quirk vs the real game — EQ is its STAB; the AI follows the fork), Gyarados Water/Flying,
  Spiritomb Ghost/Dark, Starly Normal/Flying, Rhyhorn Ground/Rock.
- **The fork's gen4 dex has exactly 3 items** (berry, cherritype, wacanberry) — no Iron Ball, Expert
  Belt, Shed Shell or plates. Item-driven `eff()` branches (ironball, embargo, shedshell) are only
  reachable in tests by mutating `p.item` directly; the `data_items.js` table (446) feeds the AI's
  item bag and the decomp item-effect logic.
- **`data_typechart.js` is the REAL gen4 chart** (110 rows; the 0xFE marker at array index 108, with
  the two batched ghost-immunity rows at 108-109). Gen4 deviations vs the modern table: fire→bug ×2,
  fighting→ghost immune, fighting→dark ×2, dark→ghost ×2, ghost→ghost ×2, ground→flying immune, no
  ground→ground row, no water→electric row. (Vs the FORK chart: ghost/dark→steel ×0.5 in decomp,
  neutral in fork — the documented 2-cell deviation.)
- **Sentinel walk semantics**: the marker is not a row in the generated array — index 108 holds the
  first batched row (normal→ghost). The walk breaks on the override (active: foresight || scrappy;
  switch: scrappy only — decomp quirk) and **otherwise walks the rows at/after index 108** (an
  unconditional `continue` at the sentinel silently dropped the normal→ghost immunity — fixed).
- `MoveIsOnDamagingTurn` (battle_lib.c:7588-7606) returns the `SYSCTL_LAST_OF_MULTI_TURN` bit (not
  bare FALSE) for its 10 charge effects.
- `Battler_IgnorableAbility` (3108-3123): non-MB → defender ability true; MB → FALSE + a per-turn
  flag that is never restored → **MB effectively always ignores in the decomp**; ported as "MB
  holders never ignore".
- `TrainerAI_MoveType` (3127-3248): Natural Gift = bare item lookup with **no Klutz check**; Judgment
  = 13 plates else Normal; Hidden Power = the decomp IV-bit formula `(bits*15/63+1)`, index ≥9
  (mystery) → +1 (differs from fork `getHiddenPower` at that edge — cross-check still open); Weather
  Ball = 4 sequential ifs, no weather → uninitialized → ported as Normal.
- `Battler_HeldItemPower` CHECK_EMBARGO has no break (falls into CHECK_NONE) ≡ `embargoed ? 0 : param`.

### Fork battle protocol (live sim, `dist/sim`)

- `new Sim.Battle({format})` **fails** in this fork — construction is `Sim.BattleStream` +
  `Sim.getPlayerStreams(stream)` only. Writes go to `st.omniscient` (the ObjectReadWriteStream whose
  `.write` routes into the base BattleStream, battle-stream.js:397→66→100) or the base stream
  directly; player substreams `st.p1/p2` are **read-only async iterables** (no `.on`) but DO have a
  `.write` used to send choice strings back.
- **The battle only advances while ALL streams are consumed** (for-await drains of omniscient + p1 +
  p2; attach `.catch(() => {})` to each IIFE — the streams close at battle end and an uncaught
  rejection would be unhandled). Team preview must be answered: `st.pN.write('team ' + indices)`
  (1-based; digits joined bare when ≤ 9, `', '` when > 9). Register `>player p1` before `>player p2`
  (engine crashes if a log line flushes while sides[0] is null).
- **`|request|` JSON (battle.js getRequests, 1174-1205)**:
  - forced switch: `{forceSwitch: bool[] per active, side}` — **no** active/wait fields;
  - team preview: `{teamPreview: true, maxChosenTeamSize?, side}`;
  - move: `{active: [getMoveRequestData()], side, ally?}`;
  - `{wait: true, side}` = "opponent still choosing" — answer nothing.
  - `side` is an **object** `{name, id: 'p1'|'p2', pokemon[]}` — never string-compare it.
  - each `side.pokemon[i]`: `{ident, details, condition, active, stats, moves (string ids),
    baseAbility, item, pokeball}` — **no explicit fainted field**: condition = `'0 fnt'` (fainted) /
    `'N/N'` (alive); the entry's `active` = `position < side.active.length` (a slot test, **not**
    "on the field").
- **`side.pokemon` reorders in place**: on switch-in the incoming mon takes position 0 and the
  outgoing one moves back (battle-actions.js:125-131); `pokemon.position` always equals its current
  array index (side.js:134, 789-793; battle.js:1323-1324). `switch N` / `team N` choices are 1-based
  into that **current** order — the request's pokemon[] is the same order. The **original team
  order** = the `>player` JSON order = the party array at battle start (an identity `team 1..N`
  reorders nothing); the engine snapshots it as `this.origParty` on the first request (the decomp's
  team-slot order, used for PostKOSwitchIn tie-breaks).
- **Live state can lag the request**: at a forced-switch request `pokemon.fainted` may not be set
  yet (the faint queue runs later in the fork's flow) while the request's `condition` already reads
  `'0 fnt'` — decide() skips a switch target if **either** source says fainted, and excludes the
  active slot by identity (`p === side.active[0]`). The fork has no per-pokemon boolean `.active`
  (it has `.isActive`, which is false even for a fainted active mon).
- `pokemon.moveSlots[i] = {move, id, pp, maxpp, target, disabled, disabledSource, ...}` — pp/disabled
  come from here (or the request's `active[].moves` — same data). `p.ability` is `''` unless the
  team entry explicitly sets one (the fork never auto-assigns species defaults in gen4customgame);
  `abilityOf` normalizes spelling (toID-style), `''` = no ability, Multitype is never suppressed,
  gravity/ingrain kill Levitate.
- `actions.getDamage` requires a **live Pokemon** (it dereferences `source.volatiles['dynamax']`,
  battle-actions.js:1406) — never a request-JSON object.
- Fork `getHealth().secret`: alive = `'N/N'`, fainted = `'0 fnt'`.

### State

eff() core validated (24/24, eff_smoke.js). PostKOSwitchIn ported and smoke-pinned (two
scenarios, deterministic switch order — see next section). Next: shouldSwitch →
shouldUseItem → PickCommand order → modules.

## Session 2026-09-16 (2) — PostKOSwitchIn ported and smoke-pinned

### PostKOSwitchIn (battle_lib.c:7923-8089) — full algorithm, ported into engine.js

`BattleAI_PostKOSwitchIn(battleSys, battler)` — `battler` = the side whose mon just fainted
(singles: the side index); returns original party slot 0-5 or 6 = none.

- Setup: `slot1 = battler`, `slot2` = the partner (singles: the just-fainted active by
  identity — the tag/2v2 partner exclusions are irrelevant); `defender` =
  `BattleSystem_RandomOpponent` (battle_lib.c:4177-4198) — **singles: `attacker ^ 1`,
  deterministic** (doubles only randomizes); `partySize = GetPartyCount`.
- **Stage 1** (`while (battlersDisregarded != 0x3F)`): per pass, `maxScore = 0, picked = 6`;
  for each i in **original party order**: eligible iff species valid && HP > 0 && not
  disregarded && not in the selected/aiSwitched slots of either battler — ineligible i sets
  its disregard bit (stays out forever). Eligible: `score =
  TypeMatchupMultiplier(monType1, d1, d2) + TypeMatchupMultiplier(monType2, d1, d2)` —
  **u8 (`&0xFF` wrap — the documented Post-KO Scoring Overflow)**; a mono-type mon stores
  type2 == type1 so its single type scores twice. `maxScore < score` (strict — **ties keep
  the earlier party slot**). If picked != 6: walk its 4 moves (**`if (move)` only — NO pp
  check**, a 0-pp move still counts) through the **switch-variant** CalcEffectiveness; if
  ANY has `mask & SE` → **return picked**. Else disregard it and rescan. No eligible at all
  → `disregarded = 0x3F` → exit to stage 2.
- **Stage 2**: `maxScore = 0, picked = 6`; for each i in party order, eligible iff valid &&
  HP > 0 (the disregard mask is **NOT** consulted — stage-1 failures get re-scored); inner j
  over 4 moves: `if (move && MOVE_DATA(move).power != 1)` (decomp power 1 = the status
  sentinel; variable-power moves have decomp power 0 and pass — fork equivalent: skip iff
  `dex.moves.get(id).category === 'Status'`) → `score = CalcMoveDamage(…, battler /* the
  fainted mon as attacker */, defender, 1)` → `ApplyTypeChart(battler, defender, move,
  moveType, score, &flags)` (it is **damage-in/damage-out** — battle_lib.c:2678 returns the
  scaled damage; its `totalMul` is computed and never read — dead) → `if (flags &
  IMMUNE) score = 0`. **`score` is a function-scope variable never reset per mon/move — a
  status or missing move leaves the previous value in the `maxScore < score` comparison
  (line 8080, outside the per-move if) — the stale-score carryover quirk, ported** (the
  uninitialized-C case is unreachable: any stage-2-eligible mon was scored in stage-1
  pass 1; the port initializes it to 0). Ties → earlier party slot. Returns picked (may
  still be 6).
- `MOVE_STATUS_IMMUNE` (include/constants/battle/moves.h:87) =
  `(INEFFECTIVE|WONDER_GUARD)|LEVITATED|MAGNET_RISE` = engine `IMMUNE` exactly.

### Port decisions (engine.js, implemented)

- Defender = the live foe active (`sides[1 - myIdx].active[0]`); mono defender: `d2 = d1`
  (decomp storage).
- Party = `this.origParty`; exclusions = just-fainted active **by identity** + fainted
  bench (`!p.fainted` — bench mons can't take damage in gen4, so the live flag is reliable
  for them; the lag only affects the active).
- Stage-2 damage = **fork ground truth**: `dmg(faintedActive, foe, moveId, {allowFainted:
  true})` — the decomp's fainted-attacker trick is faithful because the fork's
  `actions.getDamage` **accepts a fainted source** (probe: fainted gengar shadowball → 145
  vs 318 for the live one; no throw, not zeroed — `qwenwork/task3/_probe_fainted.js`).
  `dmg()`'s guard is now `!moveId || def.fainted || (src.fainted && !opts.allowFainted)`.
  The fork's damage **subsumes** the decomp's CalcMoveDamage+ApplyTypeChart (STAB, chart,
  items, immunities all native) — documented deviation; `IMMUNE → 0` is implicit (immune
  moves deal 0).
- decide(): forced branch → `postKOSwitchIn(myIdx)` → original slot →
  `side.pokemon.indexOf(origParty[side][slot])` → `switch ${idx+1}` (the request's array
  order at faint time is still the original order — the reordering happens when the switch
  choice is applied, after the request) else `'default'`.

### Smoke (rewritten qwenwork/task3/smoke.js) — two scenarios, PASSES

- TEAM_A (p1 'default'): snorlax (bold; body slam/selfdestruct/rest/encore), gengar,
  zapdos. TEAM_B scenario A (p2 AI): magnemite (modest lead), machoke, onix, gengar.
  Scenario B: magnemite, machoke, primeape.
- Stage-1 scores vs the Normal defender (snorlax): Fighting mono = 2x → **160**;
  onix (Rock/Ground) = 80+40 = **120**; gengar (Ghost/Poison) = 40+40 = **80**.
- **Verified** (fixed seed [7,3,11,5]): A forced switches = **Machoke → Onix → Gengar**
  (descending stage-1 order, each pick SE-confirmed); B = **Machoke** (160-160 tie with
  primeape → earlier party slot; strict `<` keeps it). Both battles end `|win|Team A`
  (p2 is deliberately the underdog), 0 p2 `|error|` lines, all non-wait requests answered.

### Fork facts learned this session

- Switch log line format: `|switch|<side>a: <Name>|<details>|<hp/hp>` — the active slot is
  spelled **`p2a`** (not `p2,`), fields are **pipe**-separated. The **lead-in at battle
  start is also a `|switch|` line** — smoke drops index 0. Win line = `|win|<side name>`.
- `Sim.Dex` is **not** exported as a constructor from `dist/sim`; the dex is only
  reachable via `battle.dex`. (The `eval` JS VM still can't require `dist/sim` —
  `ts-chacha20` — probes must be written to `.js` and run with `node`.)
- Fork gen4 dex types (verified via a live battle's dex): chinchou = **Water/Electric**
  (fork is right; an older note said Water mono); onix = **Rock/Ground**; rhyhorn =
  **Ground/Rock**; gengar = **Ghost/Poison**; **hariyarma is untyped (`["???"]`)** —
  unusable as a Fighting candidate. Verified Fighting mono: machop, machoke, primeape,
  hitmonlee, hitmonchan (scenario B uses machoke + primeape).
- **gen4customgame stat scale is ~half the standard gen4 formula** (snorlax L50 zero-IV
  bold: atk 103 vs 203 by the textbook formula; magnemite hp 85, machoke atk 105) —
  hand-computed 1HKOs are unreliable; the fork's numbers are ground truth.
- Request move objects: `move` = display name ("Karate Chop"), `id` = lowercased
  concatenated ("karatechop"); both resolve in `dex.moves`.

## Session 2026-09-16 (3) — ShouldSwitch ported and probe-pinned

- `TrainerAI_ShouldSwitch` (trainer_ai.c:3894-3987) ported into engine.js: the driver
  (`shouldSwitch`) plus all nine sub-checks (`aiPerishSongKO`, `aiCannotDamageWonderGuard`,
  `aiOnlyIneffectiveMoves`, `aiHasSuperEffectiveMove`, `aiHasAbsorbAbilityInParty`,
  `aiIsAsleepWithNaturalCure`, `aiIsHeavilyStatBoosted`, `aiHasPartyMemberWithSE` ×2) and
  the helpers `rawAbilityOf` (MON_DATA ability, no suppression) and `bench`. Singles
  reductions: CountAbility → foe-active ability only; all `selectedPartySlot`/
  `aiSwitchedPartySlot` exclusions collapse to the own active slot. Ported quirks: the
  comment/code mismatches (rand odds 3842/3848; the 3673 `return ABILITY_NONE`), the
  numMoves<2 bail, the no-`battlersSwitchingMask` at request boundary, the no-movePower
  gate on the switch-variant WG branch, the dead PerishSong check, the stale-score-free
  PostKO (unchanged from session 2).
- `decide()` now gates the voluntary path on `shouldSwitch`: slot → `switch N`; slot 6 →
  `postKOSwitchIn` → first alive non-active bench; FALSE → falls through to move pick
  (the item step lands here in the PickCommand ordering — next session).
- **Edit accident (the session's main scar):** a summary-read of engine.js showed
  elided/collapsed regions whose displayed line numbers diverged from the raw file;
  editing "285-287" on the summary's numbers clobbered the functional
  `const dTypes/defIdx1/defIdx2` definitions (they read like comments in the summary).
  Both `eff()` chart walks broke with `ReferenceError: defIdx1 is not defined`.
  Recovered by re-reading the raw file and re-inserting the three consts (mono def:
  `defIdx2 = defIdx1`). **qwenwork/task3 is git-untracked — no git recovery exists.**
  Rule: never edit from a summary view; re-read the exact raw region; trust the edit
  echo over the pre-edit read.
- `eff()` gained `opts.rawDef` (uses `rawAbilityOf(def)` instead of `abilityOf`) —
  used by the HasPartyMember (a)-call, which scores a bench mon's raw ability. An
  `opts.defItem` was added then REMOVED: the (b)-call's defender-item slot receives the
  attacker's OWN live item effect (decomp), which for our foe is just its own item —
  the default.
- **Fork fact — ability overrides stick:** the fork accepts arbitrary `ability` in team
  entries under gen4customgame (zapdos + `wonderguard` verified live). This is the
  state-construction primitive for ai_test.js.
- **Fork fact — `attackedBy` is an array:** `pokemon.attackedBy` is a push list of
  `{source, damage, move: <id>, damageValue, thisTurn}` (pushed by `gotAttacked`,
  battle-actions.js:860 → pokemon.js:582-587; reset to `[]` on switch-in,
  pokemon.js:1118). `getLastAttackedBy()` returns the last entry or `undefined`.
  Connected hits only (a miss sets nothing); status moves are included.
  `side.lastEnemyMove` is vestigial — never assigned; do not use it.
- **Fork fact — team-preview race:** the first p2 request (team preview) can arrive
  while `stream.battle` exists but `side.active` is still null; anything touching
  `side.active` must gate on request shape (`!teamPreview && !wait && !forceSwitch`)
  or null-guard.
- **Fork fact — the turn line is `|turn|N`** (pipe, gen4 style), not `|turn N`.
- **Fork fact — `st.omniscient` is write-only:** reading it yields an immediate EOF;
  battle observation (win/turn/switch lines) comes from the `st.p1`/`st.p2` streams,
  which carry the full transcript.
- **LCG seed bug (found and fixed):** `battle.prngSeed` is the joined string
  `"7,3,11,5"` (PRNG.startingSeed; the fork's default PRNG is Gen5RNG over those
  words — prng.js:43-65). The old constructor indexed it as an array and therefore
  read characters (`'7'`, `','` → NaN → 0) → the LCG seed was effectively constant
  across battles (two "different-seed" probe runs produced identical results).
  Fixed: split on `,`, build the 32-bit AI seed from words 1..0 per decomp
  battle_main.c:1034, single-word fallback to word 0. Verified: distinct `>start`
  seeds now change roll outcomes (asleep-NC: [31,17,5,42] → -1; [7,3,11,5] and
  [9,9,9,9] → 6).
- **Probe pins** (`qwenwork/task3/_probe_switch.js`, six real battles, default seed
  [7,3,11,5]; ALL PASS):
  1. `shouldSwitch` guard, foe Shadow Tag → -1 (deterministic guard).
  2. `aiCannotDamageWonderGuard`, foe zapdos/WG, own snorlax, bench pichu (thunder is
     SE) → -1 (the 66% roll failed for this seed).
  3. `aiHasAbsorbAbilityInParty`, last hit flamethrower, bench flareon Flash Fire
     (constructed via `attackedBy.push`) → -1 (the 50% roll failed).
  4. `aiIsAsleepWithNaturalCure`, injected `slp`, full HP, no hit yet → **6**
     (the `moveHit==NONE && (RandNext&1)` branch hit).
  5. `shouldSwitch` with +5 positive stat stages (no rolls precede this branch) → -1
     (deterministic).
  6. full `shouldSwitch` on the absorb state → -1 (absorb branch ran inside; roll
     failed).
  All six sub-checks executed live against battle state with zero errors. The
  roll-success paths (slots 1/6 on a passed roll) still need coverage — ai_test.js
  will advance the LCG before probing to hit them deterministically.
- Regression after the constructor change: `eff_smoke.js` ALL PASS (24/24);
  `smoke.js` PASS with outcomes unchanged (A: Machoke→Onix→Gengar; B: Machoke; both
  `|win|Team A`; 0 p2 errors).
- Next: `TrainerAI_ShouldUseItem` (trainer_ai.c:4056-4199) + item execution on the
  live Pokemon, then the full PickCommand ordering in `decide()`.

## Session 2026-09-16 (4) — ShouldUseItem ported and probe-pinned

- `TrainerAI_ShouldUseItem` (trainer_ai.c:4056-4199) ported into engine.js as
  `shouldUseItem(myIdx)` + `executeUsedItem(myIdx)`; wired into `decide()` between
  the shouldSwitch gate and the move pick (no return — the decomp's PickCommand
  continues to move choice after a used item).
- **Exact structure (quirks preserved):** `usedItemCondition = 0` per call (4066,
  before the guards — the decomp only resets this field; `usedItem`/`usedItemType`
  persist and are only read after a TRUE result). Embargo guard (4076) via
  `p.volatiles.embargo`; AI-partner guard (4070) omitted — we are the trainer.
  Slot i examined iff `i == 0 || aliveMons <= initialCount - i + 1` (initialCount =
  the INITIAL non-empty `initialItems` count = decomp trainerItemCounts); NONE slots
  `continue` BEFORE the chain and are never zeroed; **no break after a match** —
  every subsequently examined non-NONE slot is zeroed and `usedItem` repointed, and
  usedItemType can be clobbered by a non-matching slot's final `else` (writes MAX,
  result untouched). Chain per examined slot, decomp order: FULL_RESTORE (item-id
  match; fork: `rec.category === 'FULL_RESTORE'`) → RECOVER_HP (param-guarded inner)
  → cures SLP/PSN|TOX/BRN/FRZ/PAR/CONFUSION (condition |= CURE_BIT, category
  RECOVER_STATUS) → **fakeOut guard** → stat boosters (chain order ATK, DEF, SPATK,
  SPDEF, SPEED, ACC via `BOOST_STAT_IDX`; condition = BATTLE_STAT idx; no inner
  else) → GUARD_SPEC (opponent-side Mist check, see session 3) → final else (MAX).
  Category strings are the decomp names, so executeUsedItem dispatches on them
  without re-deriving the category from the record.
- **Execution (deviation — the fork has no consumable-bag action):**
  `executeUsedItem` logs `b.add('-item', p, name)` then applies the standard gen4
  effect per category: FULL_RESTORE = `p.heal(p.maxhp - p.hp)` + `p.cureStatus()`;
  RECOVER_HP = `p.heal(rec.hp)`; RECOVER_STATUS = `removeVolatile('confusion')` for
  the confusion bit, `p.cureStatus()` for status bits (psn bit covers psn and tox);
  STAT_BOOSTER = `b.boost({[key]: n}, p)` (key via STAT_KEYS, n from
  `rec.stages[STAGES_KEYS[key]]`); GUARD_SPEC = consume only. `battle.boost` works
  outside an action context (no error).
- **Fork fact — `p.heal(undefined)` is a NO-OP:** heal() starts with `trunc(d)`
  (pokemon.js:1197-1209) → NaN → false. FULL_RESTORE must pass an explicit delta.
- **Fork fact — `p.newlySwitched` is vestigial:** written only (ctor false
  pokemon.js:216, clearVolatile true 1120, endTurn clear battle.js:1385) and has
  ZERO readers in dist/sim. Rejected as the fakeOut proxy.
- **Fork fact — `activeTurns` lifecycle:** ctor 0 (pokemon.js:229), switchIn 0
  (battle-actions.js:137), incremented once per non-fainted active in endTurn
  (battle.js:1459). **But it is already 1 at the first move request** — a turn
  probe (1v1, mudkip/snorlax, both default) shows at req#1: `battle.turn=1`,
  `|turn|1` already in the log, `activeTurns=1`, `newlySwitched=false`,
  `previouslySwitchedIn=1`. The fork's team-preview phase runs an endTurn before
  the first move request, so `activeTurns === 0` never holds at a decision point.
- **FakeOut proxy chosen:** the engine tracks entry turns itself —
  `Engine.lastActive[side]` = the active mon seen at the last decision; the mon is
  on its entry turn iff `lastActive[side]` is unset or !== the current active
  (the decomp guard is a pure function of the turn: true exactly on the turn a mon
  entered play, lead = turn 1, mid-battle switch-in = that turn). Set on every
  `shouldUseItem` call; an embargo early-return leaves the last entry (harmless:
  embargo does not switch mons). Documented deviation in the method comment.
- **Probe pins** (`qwenwork/task3/_probe_items.js`, ten real battles, default seed
  [7,3,11,5]; ALL PASS; the probe calls shouldUseItem+executeUsedItem itself, then
  the harness's decide() re-calls shouldUseItem on the same request — with a
  consumed slot that second call is a no-op, with an entry item it sees
  not-entry-turn, so boosts apply exactly once):
  1. potion, HP < 1/4 → RECOVER_HP; slot zeroed; healed exactly +20 (record hp);
     `|-item|` line in battle.log.
  2. potion at full HP → false; slot intact; usedItemType MAX (ctor initial — the
     examined-but-no-match path writes nothing, the type just persists).
  3. Parlyz-Heal on `par` → RECOVER_STATUS, condition 2; status cured to ''.
  4. X Attack, entry turn (lead, turn 1) → STAT_BOOSTER, condition 1 (ATK);
     `boosts.atk === 1` after one apply.
  5. X Attack, mon pre-seeded as not-on-entry-turn → blocked; usedItemType
     clobbered to MAX; slot intact (the negative side of the fakeOut guard).
  6. two potions, low HP → **both slots zeroed** (no-break quirk: execution is
     after the loop, so the second potion also matches); usedItem = last examined.
  7. embargoed mon → false before the chain; slot intact.
  8. 3 alive mons, bag [fullrestore, xattack], full HP → slot 1 NOT examined
     (3 > 2 - 1 + 1); the xattack (which WOULD match on the entry turn) stays
     untouched; result false.
  9. Full Restore, HP < 1/4 + `par` → full hp (220/220) and status cured.
  10. Guard Spec, entry turn → GUARD_SPEC; slot zeroed; no state change (fork has
      no Mist).
- Regression: `eff_smoke.js` ALL PASS; `smoke.js` PASS (outcomes unchanged —
  smoke teams have no items, so shouldUseItem is a no-op there); `_probe_switch.js`
  ALL PASS with identical pins (the lastActive/usedItemCondition additions
  disturbed neither the LCG sequence nor the switch logic).
- Next: the PickCommand ordering in `decide()` (module scoring loop, BASIC then
  EXPERT, MainSingles final pick), then basic.js.
