'use strict';
// AI_FLAG_CHECK_HP — CheckHP_Main (pokeplatinum script.s:7683-7965, spec §3
// "CheckHP_Main"). Ported from the script source; code beats spec comments
// (effect-table entry counts in the spec are approximate — the tables below
// are transcribed verbatim from script.s).
//
// Dice: IfRandomLessThan N == (RandNext() % 256) < N (trainer_ai.c:635).
// AddToMoveScore: s8 add, clamp <0 → 0 (trainer_ai.c:683-692).
// Two rounds — attacker-HP band then defender-HP band; each table match
// rolls 80.5% (rand ≥ 50) of −2, so a move can lose up to 4.
//
// The partner branch (IfTargetIsPartner) jumps into TagStrategy_Partner —
// a TagStrategy label shared across flags in the decomp. Modules are
// self-contained per the task contract, so this file carries its own
// faithful copy. It is only reachable in doubles AND when the AI's move
// targets its own partner, which the engine's buildCtx never produces
// (defender is always the opposing active) — a structural doubles branch.
// Deviation there: LoadBattlerAbility/CheckBattlerAbility read the TRUE
// abilities instead of the decomp's coin-flip guessing (no dice consumed;
// same convention as the engine's abilityOf elsewhere).

// ── CheckHP effect tables (script.s:7722-7965, verbatim) ────────────────
const ATTACKER_HIGH = new Set([ // >70% HP (12 entries)
	'BATTLE_EFFECT_HALVE_DEFENSE',
	'BATTLE_EFFECT_RESTORE_HALF_HP',
	'BATTLE_EFFECT_REST',
	'BATTLE_EFFECT_KO_MON_THAT_DEFEATED_USER',
	'BATTLE_EFFECT_INCREASE_POWER_WITH_LESS_HP',
	'BATTLE_EFFECT_SURVIVE_WITH_1_HP',
	'BATTLE_EFFECT_HEAL_HALF_MORE_IN_SUN',
	'BATTLE_EFFECT_FAINT_AND_ATK_SP_ATK_DOWN_2',
	'BATTLE_EFFECT_REMOVE_ALL_PP_ON_DEFEAT',
	'BATTLE_EFFECT_HEAL_HALF_REMOVE_FLYING_TYPE',
	'BATTLE_EFFECT_FAINT_AND_FULL_HEAL_NEXT_MON',
	'BATTLE_EFFECT_FAINT_FULL_RESTORE_NEXT_MON',
]);
const ATTACKER_MED = new Set([ // 31-70% HP (46 entries)
	'BATTLE_EFFECT_HALVE_DEFENSE',
	'BATTLE_EFFECT_ATK_UP', 'BATTLE_EFFECT_DEF_UP', 'BATTLE_EFFECT_SPEED_UP',
	'BATTLE_EFFECT_SP_ATK_UP', 'BATTLE_EFFECT_SP_DEF_UP', 'BATTLE_EFFECT_ACC_UP',
	'BATTLE_EFFECT_EVA_UP',
	'BATTLE_EFFECT_ATK_DOWN', 'BATTLE_EFFECT_DEF_DOWN', 'BATTLE_EFFECT_SPEED_DOWN',
	'BATTLE_EFFECT_SP_ATK_DOWN', 'BATTLE_EFFECT_SP_DEF_DOWN', 'BATTLE_EFFECT_ACC_DOWN',
	'BATTLE_EFFECT_EVA_DOWN',
	'BATTLE_EFFECT_BIDE', 'BATTLE_EFFECT_CONVERSION', 'BATTLE_EFFECT_SET_LIGHT_SCREEN',
	'BATTLE_EFFECT_PREVENT_STAT_REDUCTION', 'BATTLE_EFFECT_CRIT_UP_2',
	'BATTLE_EFFECT_ATK_UP_2', 'BATTLE_EFFECT_DEF_UP_2', 'BATTLE_EFFECT_SPEED_UP_2',
	'BATTLE_EFFECT_SP_ATK_UP_2', 'BATTLE_EFFECT_SP_DEF_UP_2', 'BATTLE_EFFECT_ACC_UP_2',
	'BATTLE_EFFECT_EVA_UP_2',
	'BATTLE_EFFECT_ATK_DOWN_2', 'BATTLE_EFFECT_DEF_DOWN_2', 'BATTLE_EFFECT_SPEED_DOWN_2',
	'BATTLE_EFFECT_SP_ATK_DOWN_2', 'BATTLE_EFFECT_SP_DEF_DOWN_2', 'BATTLE_EFFECT_EVA_DOWN_2',
	'BATTLE_EFFECT_ACC_DOWN_2',
	'BATTLE_EFFECT_CONVERSION2', 'BATTLE_EFFECT_PREVENT_STATUS',
	'BATTLE_EFFECT_MAX_ATK_LOSE_HALF_MAX_HP', 'BATTLE_EFFECT_ATK_DEF_DOWN',
	'BATTLE_EFFECT_DEF_SPD_UP', 'BATTLE_EFFECT_ATK_DEF_UP', 'BATTLE_EFFECT_SP_ATK_SP_DEF_UP',
	'BATTLE_EFFECT_ATK_SPD_UP', 'BATTLE_EFFECT_PREVENT_CRITS',
	'BATTLE_EFFECT_SWAP_ATK_SP_ATK_STAT_CHANGES', 'BATTLE_EFFECT_SWAP_DEF_SP_DEF_STAT_CHANGES',
	'BATTLE_EFFECT_SP_ATK_DOWN_2_OPPOSITE_GENDER',
]);
const ATTACKER_LOW = new Set([ // ≤30% HP (51 entries — note: NO Halve Defense;
	// the medium list is NOT a strict subset, decomp quirk preserved)
	'BATTLE_EFFECT_ATK_UP', 'BATTLE_EFFECT_DEF_UP', 'BATTLE_EFFECT_SPEED_UP',
	'BATTLE_EFFECT_SP_ATK_UP', 'BATTLE_EFFECT_SP_DEF_UP', 'BATTLE_EFFECT_ACC_UP',
	'BATTLE_EFFECT_EVA_UP',
	'BATTLE_EFFECT_ATK_DOWN', 'BATTLE_EFFECT_DEF_DOWN', 'BATTLE_EFFECT_SPEED_DOWN',
	'BATTLE_EFFECT_SP_ATK_DOWN', 'BATTLE_EFFECT_SP_DEF_DOWN', 'BATTLE_EFFECT_ACC_DOWN',
	'BATTLE_EFFECT_EVA_DOWN',
	'BATTLE_EFFECT_BIDE', 'BATTLE_EFFECT_CONVERSION', 'BATTLE_EFFECT_SET_LIGHT_SCREEN',
	'BATTLE_EFFECT_PREVENT_STAT_REDUCTION', 'BATTLE_EFFECT_CRIT_UP_2',
	'BATTLE_EFFECT_ATK_UP_2', 'BATTLE_EFFECT_DEF_UP_2', 'BATTLE_EFFECT_SPEED_UP_2',
	'BATTLE_EFFECT_SP_ATK_UP_2', 'BATTLE_EFFECT_SP_DEF_UP_2', 'BATTLE_EFFECT_ACC_UP_2',
	'BATTLE_EFFECT_EVA_UP_2',
	'BATTLE_EFFECT_ATK_DOWN_2', 'BATTLE_EFFECT_DEF_DOWN_2', 'BATTLE_EFFECT_SPEED_DOWN_2',
	'BATTLE_EFFECT_SP_ATK_DOWN_2', 'BATTLE_EFFECT_SP_DEF_DOWN_2', 'BATTLE_EFFECT_EVA_DOWN_2',
	'BATTLE_EFFECT_ACC_DOWN_2',
	'BATTLE_EFFECT_RAISE_ATK_WHEN_HIT', 'BATTLE_EFFECT_CONVERSION2',
	'BATTLE_EFFECT_NEXT_ATTACK_ALWAYS_HITS', 'BATTLE_EFFECT_PREVENT_STATUS',
	'BATTLE_EFFECT_MAX_ATK_LOSE_HALF_MAX_HP', 'BATTLE_EFFECT_COPY_STAT_CHANGES',
	'BATTLE_EFFECT_MIRROR_COAT', 'BATTLE_EFFECT_DECREASE_POWER_WITH_LESS_USER_HP',
	'BATTLE_EFFECT_ATK_DEF_DOWN', 'BATTLE_EFFECT_DEF_SPD_UP', 'BATTLE_EFFECT_ATK_DEF_UP',
	'BATTLE_EFFECT_SP_ATK_SP_DEF_UP', 'BATTLE_EFFECT_ATK_SPD_UP',
	'BATTLE_EFFECT_HALVE_ELECTRIC_DAMAGE', 'BATTLE_EFFECT_HALVE_FIRE_DAMAGE',
	'BATTLE_EFFECT_RANDOM_STAT_UP_2', 'BATTLE_EFFECT_METAL_BURST',
	'BATTLE_EFFECT_SP_ATK_DOWN_2_OPPOSITE_GENDER',
]);
const TARGET_HIGH = new Set(); // ≤ empty table (script.s:7789-7790): nothing
// is discouraged at high target HP.
const TARGET_MED = new Set([ // target 31-70% (42 entries)
	'BATTLE_EFFECT_ATK_UP', 'BATTLE_EFFECT_DEF_UP', 'BATTLE_EFFECT_SPEED_UP',
	'BATTLE_EFFECT_SP_ATK_UP', 'BATTLE_EFFECT_SP_DEF_UP', 'BATTLE_EFFECT_ACC_UP',
	'BATTLE_EFFECT_EVA_UP',
	'BATTLE_EFFECT_ATK_DOWN', 'BATTLE_EFFECT_DEF_DOWN', 'BATTLE_EFFECT_SPEED_DOWN',
	'BATTLE_EFFECT_SP_ATK_DOWN', 'BATTLE_EFFECT_SP_DEF_DOWN', 'BATTLE_EFFECT_ACC_DOWN',
	'BATTLE_EFFECT_EVA_DOWN',
	'BATTLE_EFFECT_PREVENT_STAT_REDUCTION', 'BATTLE_EFFECT_CRIT_UP_2',
	'BATTLE_EFFECT_ATK_UP_2', 'BATTLE_EFFECT_DEF_UP_2', 'BATTLE_EFFECT_SPEED_UP_2',
	'BATTLE_EFFECT_SP_ATK_UP_2', 'BATTLE_EFFECT_SP_DEF_UP_2', 'BATTLE_EFFECT_ACC_UP_2',
	'BATTLE_EFFECT_EVA_UP_2',
	'BATTLE_EFFECT_ATK_DOWN_2', 'BATTLE_EFFECT_DEF_DOWN_2', 'BATTLE_EFFECT_SPEED_DOWN_2',
	'BATTLE_EFFECT_SP_ATK_DOWN_2', 'BATTLE_EFFECT_SP_DEF_DOWN_2', 'BATTLE_EFFECT_EVA_DOWN_2',
	'BATTLE_EFFECT_ACC_DOWN_2',
	'BATTLE_EFFECT_STATUS_POISON', 'BATTLE_EFFECT_AVERAGE_HP', 'BATTLE_EFFECT_ALL_FAINT_3_TURNS',
	'BATTLE_EFFECT_PREVENT_STATUS', 'BATTLE_EFFECT_ATK_DEF_DOWN', 'BATTLE_EFFECT_DEF_SPD_UP',
	'BATTLE_EFFECT_ATK_DEF_UP', 'BATTLE_EFFECT_SP_ATK_SP_DEF_UP', 'BATTLE_EFFECT_ATK_SPD_UP',
	'BATTLE_EFFECT_RANDOM_STAT_UP_2', 'BATTLE_EFFECT_INCREASE_POWER_WITH_MORE_HP',
	'BATTLE_EFFECT_SP_ATK_DOWN_2_OPPOSITE_GENDER',
]);
const TARGET_LOW = new Set([ // target ≤30% (BATTLE_EFFECT_HALVE_HP is listed
	// TWICE at script.s:7933-7934 — a Set dedupes; membership semantics equal)
	'BATTLE_EFFECT_STATUS_SLEEP', 'BATTLE_EFFECT_HALVE_DEFENSE',
	'BATTLE_EFFECT_ATK_UP', 'BATTLE_EFFECT_DEF_UP', 'BATTLE_EFFECT_SPEED_UP',
	'BATTLE_EFFECT_SP_ATK_UP', 'BATTLE_EFFECT_SP_DEF_UP', 'BATTLE_EFFECT_ACC_UP',
	'BATTLE_EFFECT_EVA_UP',
	'BATTLE_EFFECT_ATK_DOWN', 'BATTLE_EFFECT_DEF_DOWN', 'BATTLE_EFFECT_SPEED_DOWN',
	'BATTLE_EFFECT_SP_ATK_DOWN', 'BATTLE_EFFECT_SP_DEF_DOWN', 'BATTLE_EFFECT_ACC_DOWN',
	'BATTLE_EFFECT_EVA_DOWN',
	'BATTLE_EFFECT_BIDE', 'BATTLE_EFFECT_CONVERSION', 'BATTLE_EFFECT_STATUS_BADLY_POISON',
	'BATTLE_EFFECT_SET_LIGHT_SCREEN', 'BATTLE_EFFECT_ONE_HIT_KO',
	'BATTLE_EFFECT_HALVE_HP', 'BATTLE_EFFECT_HALVE_HP',
	'BATTLE_EFFECT_PREVENT_STAT_REDUCTION', 'BATTLE_EFFECT_CRIT_UP_2',
	'BATTLE_EFFECT_STATUS_CONFUSE',
	'BATTLE_EFFECT_ATK_UP_2', 'BATTLE_EFFECT_DEF_UP_2', 'BATTLE_EFFECT_SPEED_UP_2',
	'BATTLE_EFFECT_SP_ATK_UP_2', 'BATTLE_EFFECT_SP_DEF_UP_2', 'BATTLE_EFFECT_ACC_UP_2',
	'BATTLE_EFFECT_EVA_UP_2',
	'BATTLE_EFFECT_ATK_DOWN_2', 'BATTLE_EFFECT_DEF_DOWN_2', 'BATTLE_EFFECT_SPEED_DOWN_2',
	'BATTLE_EFFECT_SP_ATK_DOWN_2', 'BATTLE_EFFECT_SP_DEF_DOWN_2', 'BATTLE_EFFECT_EVA_DOWN_2',
	'BATTLE_EFFECT_ACC_DOWN_2',
	'BATTLE_EFFECT_STATUS_POISON', 'BATTLE_EFFECT_STATUS_PARALYZE', 'BATTLE_EFFECT_AVERAGE_HP',
	'BATTLE_EFFECT_CONVERSION2', 'BATTLE_EFFECT_NEXT_ATTACK_ALWAYS_HITS',
	'BATTLE_EFFECT_DECREASE_LAST_MOVE_PP', 'BATTLE_EFFECT_ALL_FAINT_3_TURNS',
	'BATTLE_EFFECT_ATK_UP_2_STATUS_CONFUSION', 'BATTLE_EFFECT_DOUBLE_POWER_EACH_TURN',
	'BATTLE_EFFECT_INFATUATE', 'BATTLE_EFFECT_PREVENT_STATUS', 'BATTLE_EFFECT_COPY_STAT_CHANGES',
	'BATTLE_EFFECT_MIRROR_COAT', 'BATTLE_EFFECT_STATUS_BURN',
	'BATTLE_EFFECT_ATK_DEF_DOWN', 'BATTLE_EFFECT_DEF_SPD_UP', 'BATTLE_EFFECT_ATK_DEF_UP',
	'BATTLE_EFFECT_SP_ATK_SP_DEF_UP', 'BATTLE_EFFECT_ATK_SPD_UP',
	'BATTLE_EFFECT_RANDOM_STAT_UP_2', 'BATTLE_EFFECT_INCREASE_POWER_WITH_MORE_HP',
	'BATTLE_EFFECT_SP_ATK_DOWN_2_OPPOSITE_GENDER',
]);

// ── local helper copies (see weather.js / baton_pass.js headers) ────────
const NO_DAMAGE_CALC = new Set([
	'BATTLE_EFFECT_HALVE_DEFENSE', 'BATTLE_EFFECT_RECOVER_DAMAGE_SLEEP',
	'BATTLE_EFFECT_CHARGE_TURN_HIGH_CRIT', 'BATTLE_EFFECT_CHARGE_TURN_HIGH_CRIT_FLINCH',
	'BATTLE_EFFECT_RECHARGE_AFTER', 'BATTLE_EFFECT_CHARGE_TURN_DEF_UP',
	'BATTLE_EFFECT_SKIP_CHARGE_TURN_IN_SUN', 'BATTLE_EFFECT_SPIT_UP',
	'BATTLE_EFFECT_HIT_LAST_WHIFF_IF_HIT', 'BATTLE_EFFECT_LOWER_OWN_ATK_AND_DEF',
	'BATTLE_EFFECT_DECREASE_POWER_WITH_LESS_USER_HP',
	'BATTLE_EFFECT_HIT_FIRST_IF_TARGET_ATTACKING', 'BATTLE_EFFECT_RECOIL_HALF',
]);
const ALT_POWER_CALC = new Set([
	'BATTLE_EFFECT_RANDOM_POWER_BASED_ON_IVS', 'BATTLE_EFFECT_POWER_BASED_ON_LOW_SPEED',
	'BATTLE_EFFECT_NATURAL_GIFT', 'BATTLE_EFFECT_JUDGEMENT', 'BATTLE_EFFECT_40_DAMAGE_FLAT',
	'BATTLE_EFFECT_LEVEL_DAMAGE_FLAT', 'BATTLE_EFFECT_RANDOM_DAMAGE_1_TO_150_LEVEL',
	'BATTLE_EFFECT_POWER_BASED_ON_FRIENDSHIP', 'BATTLE_EFFECT_POWER_BASED_ON_LOW_FRIENDSHIP',
	'BATTLE_EFFECT_20_DAMAGE_FLAT', 'BATTLE_EFFECT_INCREASE_POWER_WITH_WEIGHT',
]);
function comparisonMade(ctx, id) {
	const eff = ctx.effectOf(id);
	const m = ctx.move(id);
	return ALT_POWER_CALC.has(eff) || (!!m && m.basePower > 1 && !NO_DAMAGE_CALC.has(eff));
}
function add(ctx, v) {
	ctx.addScore(v);
	if (ctx.scores[ctx.slot] < 0) ctx.scores[ctx.slot] = 0;
}
// curHP*100/maxHP, C division (ARMv5 SWIDiv returns 0 on ÷0 → the decomp's
// zeroed partner slot in SINGLES reads hpPct 0 — reproduced by null → 0).
const hpPct = (p) => (p && p.maxhp ? Math.floor(p.hp * 100 / p.maxhp) : 0);
const norm = (s) => (s ? String(s).toLowerCase().replace(/[^a-z0-9]/g, '') : '');
// LoadBattlerAbility / CheckBattlerAbility read battleMons[].ability with
// ONLY the MOVE_EFFECT_ABILITY_SUPPRESSED flag applied (trainer_ai.c:1277,
// 1319) — NOT Battler_Ability, so Gravity/Ingrain do not null Levitate here.
const scrAb = (ctx, p) => (!p || ctx.engine.battle.suppressingAbility(p) ? '' : ctx.rawAbilityOf(p));
// CheckBattlerAbility result space: have / not-have / UNKNOWN (ability none
// → AI_UNKNOWN — decisive for the CheckWaterMove gate in tag_strategy and
// its copies).
const abChk = (ctx, p, ab) => {
	const a = scrAb(ctx, p);
	return !a ? 'unknown' : a === ab ? 'have' : 'no';
};
const typesOf = (p) => (p ? p.getTypes().map(norm) : ['normal', 'normal']); // zeroed mon = TYPE_NORMAL 0
const hasType = (p, t) => typesOf(p).includes(t);
// IfMoveKnown partner arm: `curHP == 0` breaks BEFORE the scan (trainer_ai.c:1752).
const knowsMove = (p, id) => !!p && p.hp > 0 && (p.moveSlots || []).some((ms) => ms && ms.id === id);
const knowsEffect = (ctx, p, eff) => !!p && (p.moveSlots || []).some((ms) => ms && ms.id && ctx.effectOf(ms.id) === eff);
const statStage = (p, key) => (p ? p.boosts[key] + 6 : 6); // internal stage; zeroed mon = 6

const PARTNER_ACCURACY_MOVES = ['fireblast', 'thunder', 'crosschop', 'hydropump', 'dynamicpunch',
	'blizzard', 'zapcannon', 'megahorn', 'focusblast', 'gunkshot', 'magmastorm', 'powerwhip',
	'seedflare', 'headsmash'];

// ── TagStrategy_Partner (script.s:7326-7681), doubles-only branch ───────
// `partner` is simultaneously AI_BATTLER_ATTACKER_PARTNER and the defender
// (the move targets the partner).
function partnerSubroutine(ctx, partner) {
	const end = {}; // PopOrEnd sentinel
	const partnerItem = norm(partner && partner.item);
	// HP-ladder used by Volt Absorb / Water Absorb / Dry Skin absorption.
	const absorbLadder = () => {
		if (hpPct(partner) === 100) return add(ctx, -10); // ScoreMinus10
		if (hpPct(partner) > 90) return; // no change
		if (hpPct(partner) > 75) { if (ctx.aiRand(256) >= 64) add(ctx, 3); return; } // 25% +3
		if (hpPct(partner) > 50) { if (ctx.aiRand(256) >= 128) add(ctx, 3); return; } // 50% +3
		if (ctx.aiRand(256) >= 192) add(ctx, 3); // 75% +3
	};
	const electricAbsorption = () => {
		if (abChk(ctx, partner, 'motordrive') === 'have') {
			if (ctx.aiRand(256) < 160) return; // 62.5% no change
			if (statStage(partner, 'spe') === 12) return add(ctx, -30); // +6 speed
			return add(ctx, 3);
		}
		if (abChk(ctx, partner, 'voltabsorb') === 'have') return absorbLadder();
		return add(ctx, -30);
	};
	const fireAbsorption = () => {
		if (abChk(ctx, partner, 'flashfire') === 'have') {
			// IfActivatedFlashFire: moveEffectsData.flashFire → fork volatile.
			if (partner.volatiles.flashfire) return add(ctx, -30);
			return add(ctx, 3);
		}
		return add(ctx, -30);
	};
	const waterAbsorption = () => {
		if (abChk(ctx, partner, 'waterabsorb') === 'have' || abChk(ctx, partner, 'dryskin') === 'have') {
			return absorbLadder();
		}
		return add(ctx, -30);
	};

	// IfBattlerFainted ATTACKER_PARTNER (battlersSwitchingMask read → fork
	// fainted flag, documented deviation).
	if (partner.fainted) return add(ctx, -30);
	const id = ctx.moves[ctx.slot].id;
	if (!comparisonMade(ctx, id)) {
		return partnerStatusMove();
	}
	const mtype = norm(ctx.move(id) && ctx.move(id).type); // LOAD_MOVE_TYPE: static dex type
	if (mtype === 'fire') return fireAbsorption();
	if (mtype === 'electric') return electricAbsorption();
	if (mtype === 'water') return waterAbsorption();
	if (id === 'fling') return; // TagStrategy_PartnerTrick: PopOrEnd (no change)
	return add(ctx, -30); // fall-through: TagStrategy_ScoreMinus30

	function partnerStatusMove() {
		const eff = ctx.effectOf(id);
		if (id === 'skillswap') {
			const ab = scrAb(ctx, partner);
			if (ab === 'truant' || ab === 'slowstart') return add(ctx, 10);
			const myAb = scrAb(ctx, ctx.active);
			if (myAb !== 'levitate') return giveAccuracyIncrease();
			if (ab === 'levitate') return add(ctx, -30);
			const t = typesOf(partner);
			if (t[0] !== 'electric') return giveAccuracyIncrease();
			add(ctx, 1); // +1 per Electric type slot → mono-Electric partner scores twice
			if (t[1] !== 'electric') return giveAccuracyIncrease();
			add(ctx, 1);
			return end;
		}
		if (id === 'willowisp') {
			if (abChk(ctx, partner, 'flashfire') === 'have') return fireAbsorption();
			if (abChk(ctx, partner, 'guts') !== 'have') return add(ctx, -30);
			if (partner.status) return add(ctx, -30); // IfStatus MON_CONDITION_ANY
			if (hasType(partner, 'fire')) return add(ctx, -30);
			if (partnerItem === 'flameorb' || partnerItem === 'toxicorb') return add(ctx, -30);
			if (hpPct(partner) < 81) return add(ctx, -30);
			return add(ctx, 5);
		}
		if (id === 'thunderwave') {
			if (hasType(partner, 'ground')) return add(ctx, -30);
			if (abChk(ctx, partner, 'motordrive') === 'have') return electricAbsorption();
			if (abChk(ctx, partner, 'voltabsorb') === 'have') return electricAbsorption();
			return add(ctx, -30);
		}
		if (eff === 'BATTLE_EFFECT_STATUS_BADLY_POISON' || eff === 'BATTLE_EFFECT_STATUS_POISON') {
			// BUG note (script.s:7548): no check the partner is poison-immune.
			if (abChk(ctx, partner, 'poisonheal') !== 'have') return add(ctx, -30);
			if (partner.status) return add(ctx, -30);
			if (partnerItem === 'toxicorb') return add(ctx, -30);
			if (hpPct(partner) > 91) return add(ctx, -30); // comment says 81% — code 91 wins
			return add(ctx, 5);
		}
		if (id === 'helpinghand') {
			if (hpPct(partner) === 0) return add(ctx, -30);
			let go = hpPct(partner) > 50;
			if (!go) go = ctx.engine.speedRank().indexOf(partner) < 1; // "moves first"
			if (go) {
				if (ctx.aiRand(256) < 64) return add(ctx, -1); // 25% −1
				return add(ctx, 2); // 75% +2
			}
			return end;
		}
		if (id === 'swagger') {
			// Own Tempo is NOT considered (decomp note, script.s:7596).
			// team item strings normalize toID-style: "Persim Berry" → persimberry,
			// "Lum Berry" → lumberry.
			if (partnerItem !== 'persimberry' && partnerItem !== 'lumberry') return add(ctx, -30);
			if (statStage(partner, 'atk') > 7) return end; // ≥ +2 attack
			return add(ctx, 3);
		}
		if (id === 'trick' || id === 'switcheroo') return end; // TagStrategy_PartnerTrick
		if (id === 'gastroacid') {
			if (ctx.engine.battle.suppressingAbility(partner)) return add(ctx, -30);
			const ab = scrAb(ctx, partner);
			if (ab === 'truant' || ab === 'slowstart') return add(ctx, 5);
			return end;
		}
		if (id === 'acupressure') {
			if (abChk(ctx, partner, 'simple') === 'have') {
				for (const k of ['atk', 'def', 'spe', 'spa', 'spd', 'evasion', 'accuracy']) {
					if (statStage(partner, k) > 8) return add(ctx, -10); // any ≥ +3
				}
			} else {
				for (const k of ['atk', 'def', 'spe', 'spa', 'spd', 'evasion', 'accuracy']) {
					if (statStage(partner, k) === 12) return add(ctx, -30); // any +6
				}
			}
			if (hpPct(partner) < 51) return add(ctx, -1);
			if (hpPct(partner) > 90) {
				if (ctx.aiRand(256) < 80) return end; // 68.75% +2
				return add(ctx, 2);
			}
			// QUIRK (script.s:7660-7668): the 51-90 band rolls 50% to bail,
			// then FALLS THROUGH into the >90 band's +2 leaf (overall +2 =
			// 50% × 68.75%; spec's "31.25%" prose is the misread).
			if (ctx.aiRand(256) < 128) return end;
			if (ctx.aiRand(256) < 80) return end;
			return add(ctx, 2);
		}
		return add(ctx, -30); // any other status move
	}

	function giveAccuracyIncrease() {
		const myAb = scrAb(ctx, ctx.active);
		if (myAb !== 'compoundeyes' && myAb !== 'noguard') return add(ctx, -30);
		if (PARTNER_ACCURACY_MOVES.some((mv) => knowsMove(partner, mv))) return add(ctx, 3);
		return add(ctx, -30);
	}
}

const targetIsPartner = (ctx) =>
	ctx.battle.battleType === 'doubles' && !!ctx.foeActive && ctx.foeActive.side === ctx.side;

module.exports = {
	id: 'check_hp',
	bit: 8,

	decide(ctx) {
		ctx.eachSlot(() => {
			if (targetIsPartner(ctx)) { // CheckHP reuses TagStrategy_Partner
				const partner = ctx.side.active[1];
				if (partner) partnerSubroutine(ctx, partner);
				return;
			}
			const eff = ctx.effectOf(ctx.moves[ctx.slot].id);
			// Round 1 — attacker HP band. `>70`, `>30`, else (exact decomp bands).
			const a = hpPct(ctx.active);
			const t1 = a > 70 ? ATTACKER_HIGH : a > 30 ? ATTACKER_MED : ATTACKER_LOW;
			if (t1.has(eff) && ctx.aiRand(256) >= 50) add(ctx, -2); // 80.5% of −2
			// Round 2 — defender HP band (ALWAYS runs; TryScoreMinus2 falls
			// through into CheckHP_Target). Same roll → up to −4 per move.
			const d = hpPct(ctx.foeActive);
			const t2 = d > 70 ? TARGET_HIGH : d > 30 ? TARGET_MED : TARGET_LOW;
			if (t2.has(eff) && ctx.aiRand(256) >= 50) add(ctx, -2);
		});
	},
};
