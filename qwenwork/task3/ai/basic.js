'use strict';
// AI_FLAG_BASIC — Basic_Main (script.s:52-1622) port (TRAINER_AI.md §3,
// lines 669-1092; §1 damage model 293-347; §1 constants 276-292).
//
// Per-usable-slot flow (decomp order):
//   (1) OHKO jump (script.s:57-58): ONLY MOVE_FISSURE / MOVE_HORN_DRILL skip
//       the damage-score step; Guillotine/Sheer Cold take the normal path
//       (power 0 ⇒ NO_COMPARISON ⇒ immunity is SKIPPED — move-based jump,
//       not effect-based; the decomp comment even says "only Fissure and
//       Horn Drill").
//   (2) FlagMoveDamageScore USE_MAX_DAMAGE: the damageVals comparison
//       (trainer_ai.c:2799-2849). NO_COMPARISON (the current slot is not
//       eligible) skips CheckForImmunity and goes straight to Soundproof.
//   (3) Basic_CheckForImmunity (65-113).
//   (4) Basic_CheckSoundproof (115-131).
//   (5) Basic_ScoreMoveEffect (133-286): effect → check-routine dispatch,
//       then PopOrEnd. Every listed check routine below is ported verbatim,
//       quirks and comment-vs-code mismatches included (code wins).
//
// Dice-free: USE_MAX_DAMAGE is deterministic and ctx.dmg snapshots/ restores
// the battle PRNG, so ctx.aiRand is never called here (per the spec block).
// basic never sets ctx.cancel (no Break in Basic_Main).

// trainer_ai.c:31-42 — effects that never get a damage calc
const NODMG = new Set([
	'BATTLE_EFFECT_HALVE_DEFENSE',
	'BATTLE_EFFECT_RECOVER_DAMAGE_SLEEP',
	'BATTLE_EFFECT_CHARGE_TURN_HIGH_CRIT',
	'BATTLE_EFFECT_CHARGE_TURN_HIGH_CRIT_FLINCH',
	'BATTLE_EFFECT_RECHARGE_AFTER',
	'BATTLE_EFFECT_CHARGE_TURN_DEF_UP',
	'BATTLE_EFFECT_SKIP_CHARGE_TURN_IN_SUN',
	'BATTLE_EFFECT_SPIT_UP',
	'BATTLE_EFFECT_HIT_LAST_WHIFF_IF_HIT',
	'BATTLE_EFFECT_LOWER_OWN_ATK_AND_DEF',
	'BATTLE_EFFECT_DECREASE_POWER_WITH_LESS_USER_HP',
	'BATTLE_EFFECT_HIT_FIRST_IF_TARGET_ATTACKING',
	'BATTLE_EFFECT_RECOIL_HALF',
]);
// trainer_ai.c:44-56 — effects with special power formulas (always eligible).
// JUDGEMENT is the moves.h spelling used by data_moves.js (verified).
const ALT_POWER = new Set([
	'BATTLE_EFFECT_RANDOM_POWER_BASED_ON_IVS',
	'BATTLE_EFFECT_POWER_BASED_ON_LOW_SPEED',
	'BATTLE_EFFECT_NATURAL_GIFT',
	'BATTLE_EFFECT_JUDGEMENT',
	'BATTLE_EFFECT_40_DAMAGE_FLAT',
	'BATTLE_EFFECT_LEVEL_DAMAGE_FLAT',
	'BATTLE_EFFECT_RANDOM_DAMAGE_1_TO_150_LEVEL',
	'BATTLE_EFFECT_POWER_BASED_ON_FRIENDSHIP',
	'BATTLE_EFFECT_POWER_BASED_ON_LOW_FRIENDSHIP',
	'BATTLE_EFFECT_20_DAMAGE_FLAT',
	'BATTLE_EFFECT_INCREASE_POWER_WITH_WEIGHT',
]);
// Basic_CheckSoundproof's 11 moves (script.s:115-131)
const SOUND_MOVES = new Set(['growl', 'roar', 'sing', 'supersonic', 'screech',
	'snore', 'uproar', 'metalsound', 'grasswhistle', 'bugbuzz', 'chatter']);

// Natural Gift eligibility (CheckNaturalGift 1061-1126): the 64 Cheri..Rowap
// table rows. Derived from data_items.js: exactly the berry records carrying
// naturalGiftType (64 of them; the synthetic 'none' row is excluded — a
// battler with no item has p.item === '' and never resolves a record).

// decomp internal stages are 0-12 with 6 neutral (battle_script.h:4-6)
const KEYS7 = ['atk', 'def', 'spe', 'spa', 'spd', 'accuracy', 'evasion'];
const NO_GUARD = 'noguard';

// ---- small helpers (decomp primitives) ---------------------------------

// LoadBattlerHpPercentage — decomp BattleSystem_Divide semantics via ctx.divide
const hpPct = (ctx, p) => ctx.divide(p.hp * 100, p.maxhp);
// internal 0-12 stage of a boost key
const stage = (p, key) => p.boosts[key] + 6;
// CountAlivePartyBattlers (spec §3: the routine counts NOT-alive members;
// 0 = last mon). Ported as an alive count; the callers below only use the
// semantics the spec documents (last-mon and "defender has more").
const alive = (side) => side.pokemon.filter((m) => !m.fainted).length;
const isLastMon = (side) => alive(side) <= 1;
// decomp stores type2 == type1 for mono-type battlers (see engine postKO
// note), so "each type slot" double-counts a single type.
const typeSlots = (p) => { const t = p.getTypes().map((x) => String(x).toLowerCase()); return [t[0], t.length > 1 ? t[1] : t[0]]; };
const countType = (p, type) => typeSlots(p).reduce((n, t) => n + (t === type ? 1 : 0), 0);
const stockLayers = (p) => (p.volatiles.stockpile && p.volatiles.stockpile.layers) || 0;
// CompareBattlerSpeed ... CHECK_HIGHER_THAN_TARGET — the decomp treats a
// speed TIE as faster (script.s:1503-04 comment, CheckTrickRoom); the same
// primitive drives Copycat/Metal Burst, so ties count as faster there too.
// Fork stand-in: actions.getActionSpeed (engine.speedRank's basis).
const speedFasterOrTied = (a, b) => a.getActionSpeed() >= b.getActionSpeed();
// FUTURE_SIGHT "side condition": the fork's gen4 Future Sight / Doom Desire
// live in side.slotConditions[pos].futuremove (data/mods/gen4/moves.ts:336).
const sideHasFutureSight = (side) => {
	for (const sc of (side.slotConditions || [])) if (sc && sc.futuremove) return true;
	return false;
};

// ---- check routines (script.s:288-1621) --------------------------------

function checkCannotSleep(ctx) { // 288-295
	const foe = ctx.foeActive;
	if (foe.status) ctx.subScore(10);
	if (ctx.foe.sideConditions.safeguard) ctx.subScore(10);
	const ab = ctx.abilityOf(foe);
	if (ab === 'insomnia' || ab === 'vitalspirit') ctx.subScore(10);
}

function checkCannotExplode(ctx, r) { // 297-319
	if (r.class === 0) ctx.subScore(10);
	const attAb = ctx.abilityOf(ctx.active);
	if (ctx.abilityOf(ctx.foeActive) === 'damp' && attAb !== 'moldbreaker') ctx.subScore(10);
	// last-mon logic (307-316): attacker not on its last mon → nothing;
	// last mon + defender has more alive → -10; both on last mon → -1.
	if (!isLastMon(ctx.side)) return;
	if (alive(ctx.foe) > alive(ctx.side)) ctx.subScore(10);
	else ctx.subScore(1);
}

function checkNightmare(ctx) { // 321-327
	const foe = ctx.foeActive;
	if (foe.volatiles.nightmare) ctx.subScore(10);
	if (foe.status !== 'slp') ctx.subScore(8);
	if (ctx.abilityOf(foe) === 'magicguard') ctx.subScore(10);
}

function checkDreamEater(ctx, r) { // 329-333
	if (ctx.foeActive.status !== 'slp') ctx.subScore(8);
	if (r.class === 0) ctx.subScore(10);
}

function checkBellyDrum(ctx) { // 335-337
	if (hpPct(ctx, ctx.active) < 51) ctx.subScore(10);
}

// HighStatStage family (343-418). Simple attacker: internal > 8 (the comment
// says "+2" but the test is strictly greater than 8 — code wins); without
// Simple: internal === 12.
function checkHighStatStage(ctx, key, kind) {
	const att = ctx.active;
	if (kind === 'spe' && ctx.battle.field.pseudoWeather.trickroom) ctx.subScore(10);
	if (kind === 'acc' || kind === 'eva') {
		if (ctx.abilityOf(ctx.foeActive) === NO_GUARD) ctx.subScore(10); // 395/408
		if (ctx.abilityOf(att) === NO_GUARD) ctx.subScore(10); // 398/411
	}
	const ab = ctx.abilityOf(att);
	const s = stage(att, key);
	if (ab === 'simple' ? s > 8 : s === 12) ctx.subScore(10);
}

// LowStatStage family (428-473): target stage === internal 0, then per-stat
// extras, then the shared Basic_CheckClearBodyEffect (Clear Body -10, White
// Smoke -10 — no Mold Breaker guard here, decomp).
function checkLowStatStage(ctx, key, extra) {
	const foe = ctx.foeActive;
	if (stage(foe, key) === 0) ctx.subScore(10);
	if (extra) extra(ctx);
	const ab = ctx.abilityOf(foe);
	if (ab === 'clearbody') ctx.subScore(10);
	if (ab === 'whitesmoke') ctx.subScore(10);
}
const lowAtk = (ctx) => checkLowStatStage(ctx, 'atk',
	(c) => { if (c.abilityOf(c.foeActive) === 'hypercutter') c.subScore(10); }); // 431
const lowDef = (ctx) => checkLowStatStage(ctx, 'def');
const lowSpe = (ctx) => checkLowStatStage(ctx, 'spe', (c) => { // 439-442
	if (c.battle.field.pseudoWeather.trickroom) c.subScore(10);
	if (c.abilityOf(c.foeActive) === 'speedboost') c.subScore(10);
});
const lowSpA = (ctx) => checkLowStatStage(ctx, 'spa');
const lowSpD = (ctx) => checkLowStatStage(ctx, 'spd');
// Cross-wired dispatch (script.s:176-177, doc 719-720/804-807): EVA_DOWN_2
// routes here and ACC_DOWN_2 routes to the Evasion routine.
const lowAcc = (ctx) => checkLowStatStage(ctx, 'accuracy', (c) => { // 456-459
	if (c.abilityOf(c.active) === NO_GUARD) c.subScore(10);
	if (c.abilityOf(c.foeActive) === 'keeneye') c.subScore(10);
	if (c.abilityOf(c.foeActive) === NO_GUARD) c.subScore(10);
});
const lowEva = (ctx) => checkLowStatStage(ctx, 'evasion', (c) => { // 465-467
	if (c.abilityOf(c.active) === NO_GUARD) c.subScore(10);
	if (c.abilityOf(c.foeActive) === NO_GUARD) c.subScore(10);
});

function checkStatStageImbalance(ctx) { // 475-497
	const imb = KEYS7.some((k) => ctx.active.boosts[k] < 0) || KEYS7.some((k) => ctx.foeActive.boosts[k] > 0);
	if (!imb) ctx.subScore(10);
}

function checkCanForceSwitch(ctx) { // 499-509
	if (isLastMon(ctx.foe)) ctx.subScore(10);
	if (ctx.abilityOf(ctx.active) === 'moldbreaker') return;
	if (ctx.abilityOf(ctx.foeActive) === 'suctioncups') ctx.subScore(10);
}

function checkCanRecoverHP(ctx) { // 511-517: -8 ONLY at full HP
	if (hpPct(ctx, ctx.active) === 100) ctx.subScore(8);
}

function checkCannotPoison(ctx) { // 519-547
	const foe = ctx.foeActive;
	ctx.subScore(10 * countType(foe, 'steel'));
	ctx.subScore(10 * countType(foe, 'poison'));
	const ab = ctx.abilityOf(foe);
	if (ab === 'immunity' || ab === 'magicguard' || ab === 'poisonheal') ctx.subScore(10);
	if (ab === 'leafguard' && ctx.weather === 'sunnyday') ctx.subScore(10);
	if (ab === 'hydration' && ctx.weather === 'raindance') ctx.subScore(10);
	if (foe.status) ctx.subScore(10);
	if (ctx.foe.sideConditions.safeguard) ctx.subScore(10);
}

const checkAlreadyLightScreen = (ctx) => { if (ctx.side.sideConditions.lightscreen) ctx.subScore(8); }; // 549-552

function checkOHKOWouldFail(ctx, r) { // 554-564
	if (r.class === 0) ctx.subScore(10);
	if (ctx.abilityOf(ctx.foeActive) === 'sturdy' && ctx.abilityOf(ctx.active) !== 'moldbreaker') ctx.subScore(10);
	if (ctx.active.level < ctx.foeActive.level) ctx.subScore(10);
}

// CheckMagnitude (566-571) — QUIRK: line 568 tests the stale calcTemp left
// by FlagMoveDamageScore (0/1/2, never an ABILITY_* enum), so the Mold
// Breaker exception commented at 567 is dead code; the defender's Levitate
// -10 applies unconditionally. calcTemp is threaded in to replicate the
// (dead) guard verbatim — do not "fix".
function checkMagnitude(ctx, r, calcTemp) {
	if (calcTemp === 24 /* ABILITY_MOLD_BREAKER enum — never reachable */) return; // dead branch, ported
	if (ctx.abilityOf(ctx.foeActive) === 'levitate') ctx.subScore(10);
}

function checkNonStandard(ctx, r) { // 572-585
	if (r.class === 0) ctx.subScore(10);
	if (ctx.abilityOf(ctx.foeActive) === 'wonderguard' &&
		ctx.abilityOf(ctx.active) !== 'moldbreaker' &&
		r.class !== 80 && r.class !== 160) ctx.subScore(10); // 577-582
}

const checkAlreadyUnderMist = (ctx) => { if (ctx.side.sideConditions.mist) ctx.subScore(8); }; // 587-590
const checkAlreadyPumpedUp = (ctx) => { if (ctx.active.volatiles.focusenergy) ctx.subScore(10); }; // 592-595

function checkCannotConfuse(ctx) { // 597-605
	if (ctx.foeActive.volatiles.confusion) ctx.subScore(5); // -5, NOT -10 (601)
	const ab = ctx.abilityOf(ctx.foeActive);
	if (ab === 'owntempo') ctx.subScore(10);
	if (ctx.foe.sideConditions.safeguard) ctx.subScore(10);
}

const checkAlreadyUnderReflect = (ctx) => { if (ctx.side.sideConditions.reflect) ctx.subScore(8); }; // 607-610

function checkCannotParalyze(ctx, r, id) { // 612-631
	const foe = ctx.foeActive;
	if (r.class === 0) ctx.subScore(10);
	const ab = ctx.abilityOf(foe);
	if (ab === 'limber' || ab === 'magicguard') ctx.subScore(10);
	if (id === 'thunderwave' && ctx.abilityOf(ctx.active) !== 'moldbreaker' &&
		(ab === 'motordrive' || ab === 'voltabsorb')) ctx.subScore(10); // 620-626
	if (foe.status) ctx.subScore(10);
	if (ctx.foe.sideConditions.safeguard) ctx.subScore(10);
}

function checkCannotSubstitute(ctx) { // 633-637
	if (ctx.active.volatiles.substitute) ctx.subScore(8);
	if (hpPct(ctx, ctx.active) < 26) ctx.subScore(10);
}

function checkCannotLeechSeed(ctx) { // 639-648
	const foe = ctx.foeActive;
	if (foe.volatiles.leechseed) ctx.subScore(10);
	ctx.subScore(10 * countType(foe, 'grass'));
	if (ctx.abilityOf(foe) === 'magicguard') ctx.subScore(10);
}

const checkCannotDisable = (ctx) => { if (ctx.foeActive.volatiles.disable) ctx.subScore(8); }; // 650-653
const checkCannotEncore = (ctx) => { if (ctx.foeActive.volatiles.encore) ctx.subScore(8); }; // 655-658
const checkAttackerAsleep = (ctx) => { if (ctx.active.status !== 'slp') ctx.subScore(8); }; // 660-663

function checkLockOn(ctx) { // 665-672: "already lock-on'd" + either No Guard.
	// Fork stores the lock-on volatile on the USER (data/moves.ts addVolatile
	// on source); the decomp reads it on the defender — check both (noted).
	if (ctx.foeActive.volatiles.lockon || ctx.active.volatiles.lockon) ctx.subScore(10);
	if (ctx.abilityOf(ctx.active) === NO_GUARD) ctx.subScore(10);
	if (ctx.abilityOf(ctx.foeActive) === NO_GUARD) ctx.subScore(10);
}

// 674-677: fork Mean Look (and Bind-family) set the 'trapped' volatile
const checkMeanLook = (ctx) => { if (ctx.foeActive.volatiles.trapped || ctx.foeActive.volatiles.meanlook) ctx.subScore(10); };

function checkCurse(ctx) { // 679-705
	const att = ctx.active;
	if (typeSlots(att).includes('ghost')) { // Basic_CheckCurse_GhostType 700-705
		if (ctx.foeActive.volatiles.curse) ctx.subScore(10);
		if (ctx.abilityOf(ctx.foeActive) === 'magicguard') ctx.subScore(10);
		return;
	}
	const ab = ctx.abilityOf(att);
	if (ab === 'simple') {
		if (stage(att, 'atk') > 8) ctx.subScore(10);
		if (stage(att, 'def') > 8) ctx.subScore(10);
	} else {
		if (stage(att, 'atk') === 12) ctx.subScore(10); // 696-697
		if (stage(att, 'def') === 12) ctx.subScore(8);
	}
}

function checkSpikes(ctx) { // 707-713
	const sc = ctx.foe.sideConditions.spikes;
	if (sc && sc.layers >= 3) ctx.subScore(10);
	if (isLastMon(ctx.foe)) ctx.subScore(10);
}

const checkForesight = (ctx) => { if (ctx.foeActive.volatiles.foresight) ctx.subScore(10); }; // 715-718
const checkPerishSong = (ctx) => { if (ctx.foeActive.volatiles.perishsong) ctx.subScore(10); }; // 720-723
const checkSandstorm = (ctx) => { if (ctx.weather === 'sandstorm') ctx.subScore(8); }; // 725-729

function checkCannotAttract(ctx) { // 731-752
	const foe = ctx.foeActive;
	if (foe.volatiles.attract) ctx.subScore(10);
	if (ctx.abilityOf(foe) === 'oblivious') ctx.subScore(10);
	const g = ctx.active.gender;
	if (g === 'M') { if (foe.gender !== 'F') ctx.subScore(10); } // opposite sex required
	else if (g === 'F') { if (foe.gender !== 'M') ctx.subScore(10); }
	else ctx.subScore(10); // genderless attacker: -10 outright (739)
}

const checkAlreadyUnderSafeguard = (ctx) => { if (ctx.side.sideConditions.safeguard) ctx.subScore(8); }; // 754-757

function checkMemento(ctx) { // 759-778
	const mb = ctx.abilityOf(ctx.active) === 'moldbreaker';
	if (!mb) {
		const ab = ctx.abilityOf(ctx.foeActive);
		if (ab === 'clearbody') ctx.subScore(10);
		if (ab === 'whitesmoke') ctx.subScore(10);
	}
	if (stage(ctx.foeActive, 'atk') === 0) ctx.subScore(10);
	if (stage(ctx.foeActive, 'spa') === 0) ctx.subScore(8);
	if (isLastMon(ctx.side)) ctx.subScore(10); // the move kills the user
}

const checkBatonPass = (ctx) => { if (isLastMon(ctx.side)) ctx.subScore(10); }; // 780-784

function checkRainDance(ctx) { // 786-801
	const attAb = ctx.abilityOf(ctx.active);
	if (attAb !== 'swiftswim' && attAb !== 'hydration') {
		if (ctx.abilityOf(ctx.foeActive) === 'hydration' && ctx.foeActive.status) ctx.subScore(8); // 793-795
	}
	if (ctx.weather === 'raindance') ctx.subScore(8);
}

function checkSunnyDay(ctx) { // 803-821
	const attAb = ctx.abilityOf(ctx.active);
	if (attAb !== 'flowergift' && attAb !== 'leafguard' && attAb !== 'solarpower') {
		// 813-815 considers the DEFENDER's Hydration for a Sun move — source
		// note 811-812: "Why does this consider Hydration? This is clearly a
		// bug" — ported as is, at -10.
		if (ctx.abilityOf(ctx.foeActive) === 'hydration' && ctx.foeActive.status) ctx.subScore(10);
	}
	if (ctx.weather === 'sunnyday') ctx.subScore(8);
}

function checkFutureSight(ctx) { // 823-827: -12 per side, up to -24
	if (sideHasFutureSight(ctx.foe)) ctx.subScore(12);
	if (sideHasFutureSight(ctx.side)) ctx.subScore(12);
}

// 829-833: "not the attacker's first turn in the battle" — the observer's
// entryTurn is the fork stand-in for the decomp fakeOutTurnNumber.
const checkFirstTurnInBattle = (ctx) => { if (ctx.turn !== ctx.entryTurn(ctx.active)) ctx.subScore(10); };

const checkMaxStockpile = (ctx) => { if (stockLayers(ctx.active) === 3) ctx.subScore(10); }; // 835-839

function checkCanSpitUpOrSwallow(ctx, r, id) { // 841-851
	// TYPE_MULTI_IMMUNE → -10 even though Swallow would still work — the
	// source note 843-844 acknowledges this; ported.
	if (r.class === 0) ctx.subScore(10);
	if (stockLayers(ctx.active) === 0) ctx.subScore(10);
	if (id === 'swallow') checkCanRecoverHP(ctx); // 850
}

function checkHail(ctx) { // 853-872
	if (ctx.weather === 'hail') ctx.subScore(8); // 856
	if (ctx.abilityOf(ctx.foeActive) === 'icebody') ctx.subScore(8); // 861
	// 869: the ATTACKER's Ice Body ADDS 8, cancelling the defender penalty —
	// source note 863-866 calls it a bug of misintention; ported.
	if (ctx.abilityOf(ctx.active) === 'icebody') ctx.addScore(8);
}

const checkTorment = (ctx) => { if (ctx.foeActive.volatiles.torment) ctx.subScore(10); }; // 874-877

function checkCannotBurn(ctx) { // 879-890
	const foe = ctx.foeActive;
	const ab = ctx.abilityOf(foe);
	if (ab === 'waterveil' || ab === 'magicguard') ctx.subScore(10);
	if (foe.status) ctx.subScore(10);
	ctx.subScore(10 * countType(foe, 'fire'));
	if (ctx.foe.sideConditions.safeguard) ctx.subScore(10);
}

const checkHelpingHand = (ctx) => { if (ctx.side.active.length < 2) ctx.subScore(10); }; // 892-896 (singles)

// 898-904 (SWITCH_HELD_ITEMS / REMOVE_HELD_ITEM)
function checkCanRemoveItem(ctx) {
	if (ctx.abilityOf(ctx.foeActive) === 'stickyhold') ctx.subScore(10);
	if (!ctx.foeActive.item) ctx.subScore(10);
}

const checkAlreadyIngrained = (ctx) => { if (ctx.active.volatiles.ingrain) ctx.subScore(10); }; // 906-909

// 911-915: LoadRecycleItem ports as ITEM_NONE (NOTES "Open items" — the fork
// has no recycleItem[battler] state), so Recycle always takes the -10.
// ENGINE GAP reported to Main.
const checkCanRecycle = (ctx) => { ctx.subScore(10); };

function checkCanImprison(ctx) { // 917-921 (asymmetric effect names preserved)
	if (ctx.active.volatiles.imprison) ctx.subScore(10);
	// MOVE_EFFECT_IMPRISONED on the defender has no fork state equivalent
	// (the fork only models the user-side 'imprison' volatile) — never hits.
	if (ctx.foeActive.volatiles.imprisoned) ctx.subScore(10);
}

// 923-926: IfNotStatus ATTACKER, MON_CONDITION_FACADE_BOOST — the spec reads
// the condition as "a status Refresh can cure" (all except sleep).
const REFRESHABLE = ['psn', 'tox', 'brn', 'frz', 'par'];
const checkCanRefreshStatus = (ctx) => { if (!REFRESHABLE.includes(ctx.active.status)) ctx.subScore(10); };

// 928-931 / 981-984: the fork stores these as field pseudo-weather (deviation
// from the decomp's battler-side effect — closest live state).
const checkCanMudSport = (ctx) => { if (ctx.battle.field.pseudoWeather.mudsport) ctx.subScore(10); };
const checkWaterSport = (ctx) => { if (ctx.battle.field.pseudoWeather.watersport) ctx.subScore(10); };

function checkTickle(ctx) { // 933-947
	if (ctx.abilityOf(ctx.active) !== 'moldbreaker') {
		const ab = ctx.abilityOf(ctx.foeActive);
		if (ab === 'clearbody') ctx.subScore(10);
		if (ab === 'whitesmoke') ctx.subScore(10);
	}
	if (stage(ctx.foeActive, 'atk') === 0) ctx.subScore(10);
	if (stage(ctx.foeActive, 'def') === 0) ctx.subScore(8);
}

// two-stat boosters (949-1019): Simple → -10 per stat internal > 8;
// otherwise -10 for the FIRST stat at 12 and -8 for the SECOND at 12.
function twoStatBooster(ctx, k1, k2) {
	const att = ctx.active;
	if (ctx.abilityOf(att) === 'simple') {
		if (stage(att, k1) > 8) ctx.subScore(10);
		if (stage(att, k2) > 8) ctx.subScore(10);
	} else {
		if (stage(att, k1) === 12) ctx.subScore(10);
		if (stage(att, k2) === 12) ctx.subScore(8);
	}
}
const checkCosmicPower = (ctx) => twoStatBooster(ctx, 'def', 'spd');
const checkBulkUp = (ctx) => twoStatBooster(ctx, 'atk', 'def');
const checkCalmMind = (ctx) => twoStatBooster(ctx, 'spa', 'spd');
const checkDragonDance = (ctx) => { // 1002-1019
	if (ctx.battle.field.pseudoWeather.trickroom) ctx.subScore(10); // 1004
	twoStatBooster(ctx, 'atk', 'spe');
};

// 1021-1024: decomp "attacker already has the CAMOUFLAGE effect". The fork's
// gen4 Camouflage has no volatile — with no terrain in gen4customgame it
// always converts to Normal, so "already camouflaged" ≡ already Normal-type
// (documented deviation; closest live state).
const checkCamouflage = (ctx) => { if (typeSlots(ctx.active).includes('normal')) ctx.subScore(10); };

const checkGravityActive = (ctx) => { if (ctx.battle.field.pseudoWeather.gravity) ctx.subScore(10); }; // 1026-1029
const checkMiracleEye = (ctx) => { if (ctx.foeActive.volatiles.miracleeye) ctx.subScore(10); }; // 1031-1034

// 1036-1051: base -20; -10 attacker on last mon; -10 when NO party member is
// statused or wounded (the decomp's party scan includes the active mon).
const partyStatusedOrWounded = (side) =>
	side.pokemon.some((m) => !m.fainted && (m.status || m.hp < m.maxhp));
function checkHealingWish(ctx) {
	ctx.subScore(20);
	if (isLastMon(ctx.side)) ctx.subScore(10);
	if (!partyStatusedOrWounded(ctx.side)) ctx.subScore(10);
}

// CheckNaturalGift (1053-1126): -10 unless the attacker holds a berry in the
// 64-row Cheri..Rowap table (data_items.js naturalGiftType presence —
// verified: exactly 64 berry records carry it, plus the synthetic 'none' row
// which no battler can hold); TYPE_MULTI_IMMUNE → -10.
function checkNaturalGift(ctx, r, id) {
	const rec = ctx.itemRec(ctx.active.item);
	const eligible = !!(ctx.active.item && rec && rec.naturalGiftType);
	if (!eligible) ctx.subScore(10);
	if (r.class === 0) ctx.subScore(10);
}

function checkTailwind(ctx) { // 1128-1133
	if (ctx.battle.field.pseudoWeather.trickroom) ctx.subScore(10);
	if (ctx.side.sideConditions.tailwind) ctx.subScore(10);
}

// CheckAcupressure (1135-1158): the seven stages of the ATTACKER (Acupressure
// may target the user; the decomp pattern matches the HighStatStage family —
// attacker's Simple, attacker's stages; ambiguity noted at the source).
function checkAcupressure(ctx) {
	const att = ctx.active;
	const simple = ctx.abilityOf(att) === 'simple';
	for (const k of KEYS7) {
		const s = stage(att, k);
		if (simple ? s > 8 : s === 12) ctx.subScore(10);
	}
}

// CheckMetalBurst (1160-1182) — BUG notes 1164-66/1171-73: the code checks
// the Shiny Stone ITEM where the Lagging Tail hold effect belongs; ported.
function checkMetalBurst(ctx, r) {
	if (r.class === 0) ctx.subScore(10); // 1162
	const att = ctx.active; const foe = ctx.foeActive;
	if (ctx.abilityOf(foe) === 'stall') ctx.subScore(10); // 1168
	if (foe.item === 'shinystone') ctx.subScore(10); // 1169
	if (ctx.abilityOf(att) === 'stall' || att.item === 'shinystone') return; // 1174-76: no penalty
	if (speedFasterOrTied(att, foe)) ctx.subScore(10); // 1179
}

// CheckEmbargo (1184-1197): -10 if the target's side is already embargoed;
// the "recyclable item on the target's side" gate never passes (the fork has
// no item bag for the human opponent — engine gap reported) and the FRONTIER
// -10 is unreachable (this battle is not a Frontier battle).
const checkEmbargo = (ctx) => { if (ctx.foe.sideConditions.embargo) ctx.subScore(10); };

// CheckFling (1199-1299) — branches on the attacker's held-item EFFECT
// (data_items.js hold): psnuser/strengthenpoison (1288-91), brnuser (1293-95),
// pikaspatkup (1297-99).
function flingPoison(ctx) { // Basic_FlingPoison 1218-1251
	const foe = ctx.foeActive; const att = ctx.active;
	const foeImmune = ctx.foe.sideConditions.safeguard || !!foe.status ||
		countType(foe, 'poison') + countType(foe, 'steel') > 0 ||
		['immunity', 'poisonheal', 'magicguard'].includes(ctx.abilityOf(foe));
	if (!foeImmune) return; // exits with NO adjustment (1233)
	const ab = ctx.abilityOf(att);
	ctx.subScore(5 * (ctx.side.sideConditions.safeguard ? 1 : 0));
	ctx.subScore(5 * (att.status ? 1 : 0));
	ctx.subScore(5 * (countType(att, 'poison') + countType(att, 'steel')));
	if (['klutz', 'immunity', 'poisonheal', 'magicguard', 'guts'].includes(ab)) ctx.subScore(5);
	ctx.addScore(3); // 1250
}
function flingBurn(ctx) { // Basic_FlingBurn 1253-1278
	const foe = ctx.foeActive; const att = ctx.active;
	const foeImmune = ctx.foe.sideConditions.safeguard || !!foe.status ||
		countType(foe, 'fire') > 0 || ['magicguard', 'waterveil'].includes(ctx.abilityOf(foe));
	if (!foeImmune) return;
	const ab = ctx.abilityOf(att);
	ctx.subScore(5 * (ctx.side.sideConditions.safeguard ? 1 : 0));
	ctx.subScore(5 * (att.status ? 1 : 0));
	ctx.subScore(5 * countType(att, 'fire'));
	if (['klutz', 'magicguard', 'waterveil', 'guts'].includes(ab)) ctx.subScore(5);
	ctx.addScore(3); // 1277
}
function flingParalyze(ctx) { // Basic_FlingParalyze 1280-86: defender-side only, no +3
	const foe = ctx.foeActive;
	ctx.subScore(5 * (ctx.foe.sideConditions.safeguard ? 1 : 0));
	ctx.subScore(5 * (foe.status ? 1 : 0));
	if (ctx.abilityOf(foe) === 'limber') ctx.subScore(5);
}
function checkFling(ctx, r, id) {
	if (r.class === 0) ctx.subScore(10); // 1201
	const att = ctx.active;
	const rec = ctx.itemRec(att.item);
	const flingPower = (rec && rec.flingPower) || 0;
	if (flingPower < 10) ctx.subScore(10); // 1204-05: no usable held item
	if (ctx.abilityOf(att) === 'multitype') ctx.subScore(10); // 1208-09
	const hold = (rec && rec.hold) || '';
	if (hold === 'psnuser' || hold === 'strengthenpoison') flingPoison(ctx);
	else if (hold === 'brnuser') flingBurn(ctx);
	else if (hold === 'pikaspatkup') flingParalyze(ctx);
}

// CheckCanPsychoShift (1301-1351)
function checkCanPsychoShift(ctx) {
	const att = ctx.active; const foe = ctx.foeActive;
	if (!att.status) ctx.subScore(10); // 1304
	if (foe.status) ctx.subScore(10); // 1305
	if (ctx.foe.sideConditions.safeguard) ctx.subScore(10); // 1308
	const ab = ctx.abilityOf(foe);
	if (att.status === 'psn' || att.status === 'tox') { // 1316-32
		if (ctx.abilityOf(att) === 'poisonheal') ctx.subScore(10);
		ctx.subScore(10 * (countType(foe, 'poison') + countType(foe, 'steel')));
		if (ab === 'immunity' || ab === 'poisonheal' || ab === 'magicguard') ctx.subScore(10);
	} else if (att.status === 'brn') { // 1334-43
		ctx.subScore(10 * countType(foe, 'fire'));
		if (ab === 'magicguard' || ab === 'waterveil') ctx.subScore(10);
	} else if (att.status === 'par') { // 1345-48
		if (ab === 'limber') ctx.subScore(10);
	}
}

const checkHealBlock = (ctx) => { if (ctx.foeActive.volatiles.healblock) ctx.subScore(10); }; // 1353-56
const checkPowerTrick = (ctx) => { if (ctx.active.volatiles.powertrick) ctx.subScore(10); }; // 1358-61

// CheckGastroAcid (1363-76): ability already suppressed (the fork's
// suppression-aware view reads '' while the raw ability does not), then the
// "worthless" ability list.
const WORTHLESS_ABILITIES = ['multitype', 'truant', 'slowstart', 'stench', 'runaway', 'pickup', 'honeygather'];
function checkGastroAcid(ctx) {
	if (ctx.rawAbilityOf(ctx.foeActive) && !ctx.abilityOf(ctx.foeActive)) ctx.subScore(10); // 1365
	if (WORTHLESS_ABILITIES.includes(ctx.abilityOf(ctx.foeActive))) ctx.subScore(10); // 1368-75
}

const checkLuckyChant = (ctx) => { if (ctx.side.sideConditions.luckychant) ctx.subScore(10); }; // 1378-81

// CheckCopycat (1383-90): penalty only on the first action turn (the decomp's
// turn 0 — the opponent has not moved yet) when the attacker is faster (ties
// count as faster, same CompareBattlerSpeed primitive).
const checkCopycat = (ctx) => { if (ctx.turn === 1 && speedFasterOrTied(ctx.active, ctx.foeActive)) ctx.subScore(10); };

// CheckPowerSwap (1392-1404) / CheckGuardSwap (1406-1418): DiffStatStages =
// defender minus attacker; < 1 on BOTH stats → -10.
const swapDiff = (ctx, k1, k2) => {
	const d1 = ctx.foeActive.boosts[k1] - ctx.active.boosts[k1];
	const d2 = ctx.foeActive.boosts[k2] - ctx.active.boosts[k2];
	if (d1 < 1 && d2 < 1) ctx.subScore(10);
};
const checkPowerSwap = (ctx) => swapDiff(ctx, 'atk', 'spa');
const checkGuardSwap = (ctx) => swapDiff(ctx, 'def', 'spd');

// CheckLastResort (1420-26) — CODE DIFFERS FROM COMMENT (1421): another move
// still usable (pp > 0, not disabled) → no penalty; LastResort the only
// usable move → -10.
function checkLastResort(ctx) {
	const other = ctx.moves.some((ms, i) =>
		i !== ctx.slot && ms && ms.id && ms.pp > 0 && !ms.disabled);
	if (!other) ctx.subScore(10);
}

// CheckWorrySeed (1428-43)
const WORRY_BAD = ['truant', 'insomnia', 'vitalspirit', 'multitype'];
function checkWorrySeed(ctx) {
	const foe = ctx.foeActive;
	if (WORRY_BAD.includes(ctx.abilityOf(foe))) ctx.subScore(10); // 1431-34 (single ability)
	const canAct = foe.status !== 'slp' ||
		(foe.moveSlots || []).some((ms) => ms && (ms.id === 'sleeptalk' || ms.id === 'snore'));
	if (canAct) return; // 1437-39: no penalty
	ctx.subScore(10); // asleep with no way to act (1440)
}

function checkToxicSpikes(ctx) { // 1445-54 (double PopOrEnd at 1453-54 is a no-op)
	const sc = ctx.foe.sideConditions.toxicspikes;
	if (sc && sc.layers >= 2) ctx.subScore(10);
	if (isLastMon(ctx.foe)) ctx.subScore(10);
}

const checkAquaRing = (ctx) => { if (ctx.active.volatiles.aquaring) ctx.subScore(10); }; // 1456-59

function checkMagnetRise(ctx) { // 1461-74
	const att = ctx.active;
	if (att.volatiles.magnetrise) ctx.subScore(10);
	if (ctx.abilityOf(att) === 'levitate') ctx.subScore(10);
	ctx.subScore(10 * countType(att, 'flying'));
}

// CheckDefog (1476-99): NO penalty when the target's evasion is not at
// internal 0, OR the defender's side has Light Screen / Reflect, OR the
// weather is DEEP_FOG (1479-85) — faithful to the documented jump structure
// even though it makes the hazard checks rarely reachable. The fork has no
// Fog weather in gen4 (documented deviation: that disjunct never fires).
function checkDefog(ctx) {
	if (stage(ctx.foeActive, 'evasion') !== 0 ||
		ctx.foe.sideConditions.lightscreen || ctx.foe.sideConditions.reflect ||
		ctx.weather === 'fog') return;
	if (isLastMon(ctx.foe)) ctx.subScore(10); // 1488-89
	const sc = ctx.foe.sideConditions;
	if (!sc.spikes && !sc.stealthrock && !sc.toxicspikes) ctx.subScore(10); // 1491-96
}

// CheckTrickRoom (1501-06): attacker faster → -10; a tie ALSO scores -10
// (comment 1503-04: ties are treated as faster).
const checkTrickRoom = (ctx) => { if (speedFasterOrTied(ctx.active, ctx.foeActive)) ctx.subScore(10); };

// CheckCaptivate (1508-38): same-sex requirement — same sex or a genderless
// attacker → -10 (1523-33). SP_ATK gate 1537: "stage < 1, i.e. at or below
// +0" — the parenthetical fixes the compared quantity to the SIGNED stage
// (internal − 6): penalize unless the target's SpA is strictly positive
// (decomp-literal internal-<1 would mean only −6; the doc's gloss wins).
function checkCaptivate(ctx) {
	if (ctx.abilityOf(ctx.active) !== 'moldbreaker') {
		const ab = ctx.abilityOf(ctx.foeActive);
		if (ab === 'oblivious') ctx.subScore(10);
		if (ab === 'clearbody') ctx.subScore(10);
		if (ab === 'whitesmoke') ctx.subScore(10);
	}
	const g = ctx.active.gender;
	if (g !== 'M' && g !== 'F') ctx.subScore(10);
	else if (g !== ctx.foeActive.gender) ctx.subScore(10);
	if (ctx.foeActive.boosts.spa < 1) ctx.subScore(10); // 1537
}

function checkStealthRock(ctx) { // 1540-47
	if (ctx.foe.sideConditions.stealthrock) ctx.subScore(10);
	if (isLastMon(ctx.foe)) ctx.subScore(10);
}

// CheckLunarDance (1549-65): base -20; -10 attacker on last mon; -10 when no
// party member is wounded, statused, or PP-depleted (1559-62). "Used PP" is
// read as any consumed PP on any known move (pp < maxpp).
function checkLunarDance(ctx) {
	ctx.subScore(20);
	if (isLastMon(ctx.side)) ctx.subScore(10);
	const needed = ctx.side.pokemon.some((m) => !m.fainted && (m.status || m.hp < m.maxhp ||
		(m.moveSlots || []).some((ms) => ms && ms.id && ms.pp < ms.maxpp)));
	if (!needed) ctx.subScore(10);
}

// ---- effect → check-routine dispatch (the 152 IfCurrentMoveEffectEqualTo
// entries, 133-286; the routine names in comments are the doc's) ------------
// Cross-wired stat-down family per script.s:176-177 (see lowAcc/lowEva).
const DIS = {};
const map = (fn, ...effects) => effects.forEach((e) => { DIS['BATTLE_EFFECT_' + e] = fn; });

map((c, r, id, t) => checkCannotSleep(c), 'STATUS_SLEEP', 'STATUS_SLEEP_NEXT_TURN'); // CheckCannotSleep
map((c, r, id, t) => checkCannotExplode(c, r), 'HALVE_DEFENSE');
map((c, r, id, t) => checkDreamEater(c, r), 'RECOVER_DAMAGE_SLEEP');
map((c, r, id, t) => checkHighStatStage(c, 'atk'), 'ATK_UP', 'ATK_UP_2');
map((c, r, id, t) => checkHighStatStage(c, 'def'), 'DEF_UP', 'DEF_UP_2', 'DEF_UP_DOUBLE_ROLLOUT_POWER');
map((c, r, id, t) => checkHighStatStage(c, 'spe', 'spe'), 'SPEED_UP', 'SPEED_UP_2');
map((c, r, id, t) => checkHighStatStage(c, 'spa'), 'SP_ATK_UP', 'SP_ATK_UP_2');
map((c, r, id, t) => checkHighStatStage(c, 'spd'), 'SP_DEF_UP', 'SP_DEF_UP_2');
map((c, r, id, t) => checkHighStatStage(c, 'accuracy', 'acc'), 'ACC_UP', 'ACC_UP_2');
map((c, r, id, t) => checkHighStatStage(c, 'evasion', 'eva'), 'EVA_UP', 'EVA_UP_2', 'EVA_UP_2_MINIMIZE');
map(lowAtk, 'ATK_DOWN', 'ATK_DOWN_2');
map(lowDef, 'DEF_DOWN', 'DEF_DOWN_2');
map(lowSpe, 'SPEED_DOWN', 'SPEED_DOWN_2');
map(lowSpA, 'SP_ATK_DOWN', 'SP_ATK_DOWN_2');
map(lowSpD, 'SP_DEF_DOWN', 'SP_DEF_DOWN_2');
map(lowAcc, 'ACC_DOWN', 'EVA_DOWN_2'); // 176: EVA_DOWN_2 → Accuracy routine
map(lowEva, 'EVA_DOWN', 'ACC_DOWN_2'); // 177: ACC_DOWN_2 → Evasion routine
map((c, r, id, t) => checkStatStageImbalance(c), 'RESET_STAT_CHANGES', 'COPY_STAT_CHANGES', 'SWAP_STAT_CHANGES');
map((c, r, id, t) => checkNonStandard(c, r),
	'BIDE', 'CHARGE_TURN_HIGH_CRIT', 'HALVE_HP', '40_DAMAGE_FLAT', 'RECHARGE_AFTER',
	'LEVEL_DAMAGE_FLAT', 'RANDOM_DAMAGE_1_TO_150_LEVEL', 'COUNTER', 'INCREASE_POWER_WITH_LESS_HP',
	'POWER_BASED_ON_FRIENDSHIP', 'RANDOM_POWER_MAYBE_HEAL', 'POWER_BASED_ON_LOW_FRIENDSHIP',
	'20_DAMAGE_FLAT', 'RANDOM_POWER_BASED_ON_IVS', 'MIRROR_COAT', 'CHARGE_TURN_DEF_UP',
	'HIT_LAST_WHIFF_IF_HIT', 'LOWER_OWN_ATK_AND_DEF', 'SET_HP_EQUAL_TO_USER',
	'INCREASE_POWER_WITH_WEIGHT', 'POWER_BASED_ON_LOW_SPEED', 'HIGHER_POWER_WHEN_LOW_PP',
	'INCREASE_POWER_WITH_MORE_HP', 'INCREASE_POWER_WITH_MORE_STAT_UP');
map((c, r, id, t) => checkCanForceSwitch(c), 'FORCE_SWITCH');
map((c, r, id, t) => checkCanRecoverHP(c),
	'RESTORE_HALF_HP', 'HEAL_HALF_MORE_IN_SUN', 'UNUSED_133', 'UNUSED_134', 'UNUSED_157', 'HEAL_HALF_REMOVE_FLYING_TYPE');
map((c, r, id, t) => checkCannotPoison(c), 'STATUS_BADLY_POISON', 'STATUS_POISON');
map((c, r, id, t) => checkAlreadyLightScreen(c), 'SET_LIGHT_SCREEN');
map((c, r, id, t) => checkOHKOWouldFail(c, r), 'ONE_HIT_KO');
map((c, r, id, t) => checkAlreadyUnderMist(c), 'PREVENT_STAT_REDUCTION');
map((c, r, id, t) => checkAlreadyPumpedUp(c), 'CRIT_UP_2');
map((c, r, id, t) => checkCannotConfuse(c), 'STATUS_CONFUSE', 'ATK_UP_2_STATUS_CONFUSION', 'SP_ATK_UP_CAUSE_CONFUSION');
map((c, r, id, t) => checkAlreadyUnderReflect(c), 'SET_REFLECT');
map((c, r, id, t) => checkCannotParalyze(c, r, id), 'STATUS_PARALYZE');
map((c, r, id, t) => checkCannotSubstitute(c), 'SET_SUBSTITUTE');
map((c, r, id, t) => checkCannotLeechSeed(c), 'STATUS_LEECH_SEED');
map((c, r, id, t) => checkCannotDisable(c), 'DISABLE');
map((c, r, id, t) => checkCannotEncore(c), 'ENCORE');
map((c, r, id, t) => checkAttackerAsleep(c), 'DAMAGE_WHILE_ASLEEP', 'USE_RANDOM_LEARNED_MOVE_SLEEP');
map((c, r, id, t) => checkLockOn(c), 'NEXT_ATTACK_ALWAYS_HITS');
map((c, r, id, t) => checkMeanLook(c), 'PREVENT_ESCAPE');
map((c, r, id, t) => checkNightmare(c), 'STATUS_NIGHTMARE');
map((c, r, id, t) => checkCurse(c), 'CURSE');
map((c, r, id, t) => checkSpikes(c), 'SET_SPIKES');
map((c, r, id, t) => checkForesight(c), 'FORESIGHT');
map((c, r, id, t) => checkPerishSong(c), 'ALL_FAINT_3_TURNS');
map((c, r, id, t) => checkSandstorm(c), 'WEATHER_SANDSTORM');
map((c, r, id, t) => checkCannotAttract(c), 'INFATUATE');
map((c, r, id, t) => checkAlreadyUnderSafeguard(c), 'PREVENT_STATUS');
map((c, r, id, t) => checkMagnitude(c, r, t), 'PSYWAVE'); // Magnitude carries effect PSYWAVE
map((c, r, id, t) => checkBatonPass(c), 'PASS_STATS_AND_STATUS');
map((c, r, id, t) => checkRainDance(c), 'WEATHER_RAIN');
map((c, r, id, t) => checkSunnyDay(c), 'WEATHER_SUN');
map((c, r, id, t) => checkBellyDrum(c), 'MAX_ATK_LOSE_HALF_MAX_HP');
map((c, r, id, t) => checkFutureSight(c), 'HIT_IN_3_TURNS');
map((c, r, id, t) => c.subScore(10), 'FLEE_FROM_WILD_BATTLE'); // ScoreMinus10 direct (1591)
map((c, r, id, t) => checkFirstTurnInBattle(c), 'ALWAYS_FLINCH_FIRST_TURN_ONLY');
map((c, r, id, t) => checkMaxStockpile(c), 'STOCKPILE');
map((c, r, id, t) => checkCanSpitUpOrSwallow(c, r, id), 'SPIT_UP', 'SWALLOW');
map((c, r, id, t) => checkHail(c), 'WEATHER_HAIL');
map((c, r, id, t) => checkTorment(c), 'TORMENT');
map((c, r, id, t) => checkCannotBurn(c), 'STATUS_BURN');
map((c, r, id, t) => checkMemento(c), 'FAINT_AND_ATK_SP_ATK_DOWN_2');
map((c, r, id, t) => checkHelpingHand(c), 'BOOST_ALLY_POWER_BY_50_PERCENT');
map((c, r, id, t) => checkCanRemoveItem(c), 'SWITCH_HELD_ITEMS', 'REMOVE_HELD_ITEM');
map((c, r, id, t) => checkAlreadyIngrained(c), 'GROUND_TRAP_USER_CONTINUOUS_HEAL');
map((c, r, id, t) => checkCanRecycle(c), 'RECYCLE');
map((c, r, id, t) => checkCanImprison(c), 'MAKE_SHARED_MOVES_UNUSEABLE');
map((c, r, id, t) => checkCanRefreshStatus(c), 'HEAL_STATUS');
map((c, r, id, t) => checkCanMudSport(c), 'HALVE_ELECTRIC_DAMAGE');
map((c, r, id, t) => checkTickle(c), 'ATK_DEF_DOWN');
map((c, r, id, t) => checkCosmicPower(c), 'DEF_SPD_UP');
map((c, r, id, t) => checkBulkUp(c), 'ATK_DEF_UP');
map((c, r, id, t) => checkWaterSport(c), 'HALVE_FIRE_DAMAGE');
map((c, r, id, t) => checkCalmMind(c), 'SP_ATK_SP_DEF_UP');
map((c, r, id, t) => checkDragonDance(c), 'ATK_SPD_UP');
map((c, r, id, t) => checkCamouflage(c), 'CAMOUFLAGE');
map((c, r, id, t) => checkGravityActive(c), 'GRAVITY');
map((c, r, id, t) => checkMiracleEye(c), 'IGNORE_EVATION_REMOVE_DARK_IMMUNE');
map((c, r, id, t) => checkHealingWish(c), 'FAINT_AND_FULL_HEAL_NEXT_MON');
map((c, r, id, t) => checkNaturalGift(c, r, id), 'NATURAL_GIFT');
map((c, r, id, t) => checkTailwind(c), 'DOUBLE_SPEED_3_TURNS');
map((c, r, id, t) => checkAcupressure(c), 'RANDOM_STAT_UP_2');
map((c, r, id, t) => checkMetalBurst(c, r), 'METAL_BURST');
map((c, r, id, t) => checkEmbargo(c), 'PREVENT_ITEM_USE');
map((c, r, id, t) => checkFling(c, r, id), 'FLING');
map((c, r, id, t) => checkCanPsychoShift(c), 'TRANSFER_STATUS');
map((c, r, id, t) => checkHealBlock(c), 'PREVENT_HEALING');
map((c, r, id, t) => checkPowerTrick(c), 'SWAP_ATK_DEF');
map((c, r, id, t) => checkGastroAcid(c), 'SUPRESS_ABILITY'); // moves.h typo spelling (doc 808)
map((c, r, id, t) => checkLuckyChant(c), 'PREVENT_CRITS');
map((c, r, id, t) => checkCopycat(c), 'USE_LAST_USED_MOVE');
map((c, r, id, t) => checkPowerSwap(c), 'SWAP_ATK_SP_ATK_STAT_CHANGES');
map((c, r, id, t) => checkGuardSwap(c), 'SWAP_DEF_SP_DEF_STAT_CHANGES');
map((c, r, id, t) => checkLastResort(c), 'FAIL_IF_NOT_USED_ALL_OTHER_MOVES');
map((c, r, id, t) => checkWorrySeed(c), 'SET_ABILITY_TO_INSOMNIA');
map((c, r, id, t) => checkToxicSpikes(c), 'TOXIC_SPIKES');
map((c, r, id, t) => checkAquaRing(c), 'RESTORE_HP_EVERY_TURN');
map((c, r, id, t) => checkMagnetRise(c), 'GIVE_GROUND_IMMUNITY');
map((c, r, id, t) => checkDefog(c), 'REMOVE_HAZARDS_SCREENS_EVA_DOWN');
map((c, r, id, t) => checkTrickRoom(c), 'TRICK_ROOM');
map((c, r, id, t) => checkCaptivate(c), 'SP_ATK_DOWN_2_OPPOSITE_GENDER');
map((c, r, id, t) => checkStealthRock(c), 'STEALTH_ROCK');
map((c, r, id, t) => checkLunarDance(c), 'FAINT_FULL_RESTORE_NEXT_MON');

// ---- the flag main ------------------------------------------------------

module.exports = {
	id: 'basic',
	bit: 0,
	decide(ctx) {
		const OHKO = 'BATTLE_EFFECT_ONE_HIT_KO';
		// FlagMoveDamageScore's per-pass CalcAllDamage (trainer_ai.c:2799-2849),
		// USE_MAX_DAMAGE = roll 100 → plain ctx.dmg (fork ground truth, no dice).
		const effectOf = (i) => { const ms = ctx.moves[i]; return ms && ms.id ? ctx.effectOf(ms.id) : null; };
		const eligible = [false, false, false, false];
		const vals = [0, 0, 0, 0];
		for (let i = 0; i < 4; i++) {
			const ms = ctx.moves[i];
			if (!ms || !ms.id) continue; // moves[i] == MOVE_NONE
			const eff = ctx.effectOf(ms.id);
			let ok;
			if (ALT_POWER.has(eff)) ok = true;
			else if (NODMG.has(eff)) ok = false;
			else { const mv = ctx.move(ms.id); ok = !!(mv && mv.basePower > 1); }
			eligible[i] = ok;
			if (ok) vals[i] = ctx.dmg(ctx.active, ctx.foeActive, ms.id);
		}
		const maxV = Math.max(vals[0], vals[1], vals[2], vals[3]);

		ctx.eachSlot((i) => {
			const id = ctx.moves[i].id;
			const eff = ctx.effectOf(id);
			// (1) OHKO jump (56-58) — BY MOVE ID, fissure/horndrill only; calcTemp
			// keeps its previous value (decomp — never observed: the only
			// reader, CheckMagnitude's guard, is dead code).
			let calcTemp = 0;
			if (id !== 'fissure' && id !== 'horndrill') {
				// (2) FlagMoveDamageScore (60-63): NO_COMPARISON(0) /
				// NOT_HIGHEST(1) / HIGHEST(2).
				calcTemp = !eligible[i] ? 0 : (vals[i] >= maxV ? 2 : 1);
				// AI_NO_COMPARISON_MADE → Basic_CheckSoundproof (skip immunity).
				if (calcTemp !== 0) immunity(ctx, id);
			} else {
				immunity(ctx, id);
			}
			// (4) Basic_CheckSoundproof (115-131)
			if (SOUND_MOVES.has(id) &&
				ctx.abilityOf(ctx.foeActive) === 'soundproof' &&
				ctx.abilityOf(ctx.active) !== 'moldbreaker') ctx.subScore(10);
			// (5) Basic_ScoreMoveEffect (133-286) → PopOrEnd
			const fn = DIS[eff];
			if (fn) fn(ctx, ctx.eff(id, ctx.foeActive), id, calcTemp);
		});

		// Basic_CheckForImmunity (65-113) — runs ONLY on the compared/OHKO paths
		function immunity(c, id) {
			const r = c.eff(id, c.foeActive);
			if (r.mask & c.flags.IMMUNE) c.subScore(10); // 68
			if (c.abilityOf(c.active) === 'moldbreaker') return; // 70: skip ability checks
			const ab = c.abilityOf(c.foeActive);
			const type = r.type; // the loaded move type (79)
			if (type === 'electric' && (ab === 'voltabsorb' || ab === 'motordrive')) c.subScore(12); // 81-84
			if (type === 'water' && ab === 'waterabsorb') c.subScore(12); // 86-89
			if (type === 'fire' && ab === 'flashfire') c.subScore(12); // 91-94
			if (ab === 'wonderguard' && r.class !== 80 && r.class !== 160) c.subScore(12); // 96-99
			if (type === 'ground' && ab === 'levitate') c.subScore(12); // 101-104
			// 106-109 — PORTED BUG (source note at 78): "This line should
			// branch on Dry Skin rather than Levitate" — a Levitate target
			// takes this -12 for every WATER move.
			if (type === 'water' && ab === 'levitate') c.subScore(12);
		}
	},
};
