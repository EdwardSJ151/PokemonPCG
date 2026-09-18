'use strict';
// AI_FLAG_PRIORITIZE_EXTREMES — PrioritizeExtremes_Main (script 6496-6517).
// Spec: TRAINER_AI.md §3 doc 2068-2077.
//
// Partner -> terminate (singles-only engine — never fires; spec §3 preamble).
// Applies only when FlagMoveDamageScore USE_MAX_DAMAGE reports
// AI_NO_COMPARISON_MADE, i.e. the current move gets no standard damage calc
// (not alt-power and (power <= 1 or a no-calc effect) — sNoDamageCalcMove-
// Effects / sAltPowerMoveEffects, spec §1). Then 60.9% of +2:
// IfRandomLessThan 100 skip, else +2 (156/256 = 60.9%; the decomp comment's
// "~61%").
//
// The script ends normally (DONE -> next move); no BREAK/Escape anywhere —
// ctx.cancel is never set (see the long note in eval_attack.js).

const P = 'BATTLE_EFFECT_';

// trainer_ai.c:31-42 (spec §1).
const NO_CALC = new Set([
	P + 'HALVE_DEFENSE', P + 'RECOVER_DAMAGE_SLEEP', P + 'CHARGE_TURN_HIGH_CRIT',
	P + 'CHARGE_TURN_HIGH_CRIT_FLINCH', P + 'RECHARGE_AFTER', P + 'CHARGE_TURN_DEF_UP',
	P + 'SKIP_CHARGE_TURN_IN_SUN', P + 'SPIT_UP', P + 'HIT_LAST_WHIFF_IF_HIT',
	P + 'LOWER_OWN_ATK_AND_DEF', P + 'DECREASE_POWER_WITH_LESS_USER_HP',
	P + 'HIT_FIRST_IF_TARGET_ATTACKING', P + 'RECOIL_HALF',
]);
// trainer_ai.c:44-56 (spec §1).
const ALT_POWER = new Set([
	P + 'RANDOM_POWER_BASED_ON_IVS', P + 'POWER_BASED_ON_LOW_SPEED',
	P + 'NATURAL_GIFT', P + 'JUDGEMENT', P + '40_DAMAGE_FLAT',
	P + 'LEVEL_DAMAGE_FLAT', P + 'RANDOM_DAMAGE_1_TO_150_LEVEL',
	P + 'POWER_BASED_ON_FRIENDSHIP', P + 'POWER_BASED_ON_LOW_FRIENDSHIP',
	P + '20_DAMAGE_FLAT', P + 'INCREASE_POWER_WITH_WEIGHT',
]);

// TrainerAI_CalcAllDamage eligibility (2799-2849) for one move id.
function eligible(ctx, id) {
	if (!id) return false;
	const eff = ctx.effectOf(id);
	if (eff && ALT_POWER.has(eff)) return true;
	if (eff && NO_CALC.has(eff)) return false;
	return ctx.move(id).basePower > 1;
}

// FlagMoveDamageScore USE_MAX_DAMAGE result. This module consumes only the
// AI_NO_COMPARISON_MADE (0) leg — whether the current move gets any damage
// calc at all — so the other slots' damage values are never needed; no
// extra rolls are taken (the decomp's CalcAllDamage runs while computing the
// flag, but USE_MAX_DAMAGE passes roll 100 — no dice there either).
function noComparisonMade(ctx) {
	return !eligible(ctx, ctx.moves[ctx.slot].id);
}

module.exports = {
	id: 'prioritize_extremes',
	bit: 5,
	decide(ctx) {
		ctx.eachSlot(() => {
			if (!noComparisonMade(ctx)) return;
			if (ctx.aiRand(256) >= 100) ctx.addScore(2); // 60.9%
		});
	},
};
