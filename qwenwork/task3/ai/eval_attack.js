'use strict';
// AI_FLAG_EVAL_ATTACK — EvalAttack_Main (script 6350-6413).
// Spec: TRAINER_AI.md §3 doc 2034-2054. "Reward the biggest hitter."
//
// Per usable move slot:
//   Kill branch — IfCurrentMoveKills USE_MAX_DAMAGE (roll 100, non-crit): the
//   move must pass the §1 damage-model eligibility (effect in sAltPower, or
//   power > 1 with an effect outside sNoDamageCalcMoveEffects); an immune
//   move deals 0 and never kills (spec op 53).
//     * HALVE_DEFENSE (Explosion/Self-Destruct) -> terminate, no bonus.
//       Decomp-faithful dead code: the effect is in sNoDamageCalcMoveEffects,
//       so the kill test can never jump for those moves (spec quirk note).
//     * HIT_LAST_WHIFF_IF_HIT (Focus Punch) / HIT_FIRST_IF_TARGET_ATTACKING
//       (Sucker Punch) / HIT_IN_3_TURNS (Future Sight) -> IfRandomLessThan
//       170 skip, else +4 (86/256 = 33.6%). The first two are no-calc
//       effects, so only Future Sight ever reaches this — the decomp note.
//     * BATTLE_EFFECT_PRIORITY_1 -> +2: the code checks the EFFECT, not the
//       move's priority value (spec parenthetical). The doc's "a further +2
//       on top of the +4" describes the code layout (the +2 sits after the
//       +4 block, no terminate between); no move's effect matches both sets,
//       so in practice one bonus applies, never both.
//   Then the kill branch ends (see Terminate note).
//   Otherwise ("the move must deal the highest max damage"):
//     FlagMoveDamageScore USE_MAX_DAMAGE. AI_NOT_HIGHEST_DAMAGE -> global
//     ScoreMinus1 helper (1579-1581): -1, then terminate. AI_NO_COMPARISON_-
//     MADE (current move ineligible for a damage calc) counts as "non-
//     comparable" and continues like the top move.
//     * Explosion/Focus-Punch/Sucker-Punch effects -> IfRandomLessThan 51
//       skip, else -2 (205/256 = 80.1%).
//     * quad-effective (effectiveness class 160) -> IfRandomLessThan 80 skip,
//       else +2 (176/256 = 68.75%), stacking on top of the -2 (spec:
//       "stacking on top") — both dice can roll for one move.
//     Terminate.
//
// TERMINATE NOTE: "terminate" = the decomp `Terminate` label (8102-8105) =
// PopOrEnd — at the top level of the flag script it sets DONE, i.e. this
// move's script ends and the SAME flag continues with the next move slot
// (spec §2 EvalMoves loop, 249: "if DONE -> moveSlot++; if moveSlot < 4 and
// BREAK not set -> back to INIT"). It is NOT the BREAK flag — only `Escape`
// (op 61) sets DONE|ESCAPE|BREAK — so ctx.cancel is never set in this module:
// later slots and later flags keep running. (The task contract glosses
// "BREAK/Terminate" as one thing; the spec §2/§3 semantics — the source of
// truth — separate them. Reported to Main.)
//
// Partner -> terminate: every *_Main opens with IfTargetIsPartner, which
// never fires in this singles-only engine (spec §3 preamble).

const P = 'BATTLE_EFFECT_';

// trainer_ai.c:31-42 (spec §1) — effects that never get a damage calc.
const NO_CALC = new Set([
	P + 'HALVE_DEFENSE', P + 'RECOVER_DAMAGE_SLEEP', P + 'CHARGE_TURN_HIGH_CRIT',
	P + 'CHARGE_TURN_HIGH_CRIT_FLINCH', P + 'RECHARGE_AFTER', P + 'CHARGE_TURN_DEF_UP',
	P + 'SKIP_CHARGE_TURN_IN_SUN', P + 'SPIT_UP', P + 'HIT_LAST_WHIFF_IF_HIT',
	P + 'LOWER_OWN_ATK_AND_DEF', P + 'DECREASE_POWER_WITH_LESS_USER_HP',
	P + 'HIT_FIRST_IF_TARGET_ATTACKING', P + 'RECOIL_HALF',
]);
// trainer_ai.c:44-56 (spec §1) — special power formulas. Decomp and
// data_moves.js agree on the JUDGEMENT spelling.
const ALT_POWER = new Set([
	P + 'RANDOM_POWER_BASED_ON_IVS', P + 'POWER_BASED_ON_LOW_SPEED',
	P + 'NATURAL_GIFT', P + 'JUDGEMENT', P + '40_DAMAGE_FLAT',
	P + 'LEVEL_DAMAGE_FLAT', P + 'RANDOM_DAMAGE_1_TO_150_LEVEL',
	P + 'POWER_BASED_ON_FRIENDSHIP', P + 'POWER_BASED_ON_LOW_FRIENDSHIP',
	P + '20_DAMAGE_FLAT', P + 'INCREASE_POWER_WITH_WEIGHT',
]);

const KILL_BONUS_EFFECTS = new Set([
	P + 'HIT_LAST_WHIFF_IF_HIT', P + 'HIT_FIRST_IF_TARGET_ATTACKING',
	P + 'HIT_IN_3_TURNS',
]);
const TOP_PENALTY_EFFECTS = new Set([
	P + 'HALVE_DEFENSE', P + 'HIT_LAST_WHIFF_IF_HIT', P + 'HIT_FIRST_IF_TARGET_ATTACKING',
]);

// TrainerAI_CalcAllDamage eligibility (spec §1, 2799-2849) for one move id.
function eligible(ctx, id) {
	if (!id) return false;
	const eff = ctx.effectOf(id);
	if (eff && ALT_POWER.has(eff)) return true;
	if (eff && NO_CALC.has(eff)) return false;
	return ctx.move(id).basePower > 1;
}

// FlagMoveDamageScore USE_MAX_DAMAGE (1012): 0 = AI_NO_COMPARISON_MADE
// (current move not eligible), 1 = AI_NOT_HIGHEST_DAMAGE (another eligible
// move strictly outs-deals it), 2 = AI_MOVE_IS_HIGHEST_DAMAGE. Max-damage
// mode = plain fork dmg() per slot, no rolls (contract / ROLL_FOR_DAMAGE 0).
// CalcAllDamage walks all 4 slots with moves[i] != MOVE_NONE — no pp check.
function damageScore(ctx) {
	const cur = ctx.moves[ctx.slot].id;
	if (!eligible(ctx, cur)) return 0;
	const base = ctx.dmg(ctx.active, ctx.foeActive, cur);
	for (let i = 0; i < 4; i++) {
		if (i === ctx.slot) continue;
		const id = ctx.moves[i] && ctx.moves[i].id;
		if (!eligible(ctx, id)) continue;
		if (ctx.dmg(ctx.active, ctx.foeActive, id) > base) return 1;
	}
	return 2;
}

// IfCurrentMoveKills USE_MAX_DAMAGE (1525): eligible + damage >= target HP.
function kills(ctx, id) {
	return eligible(ctx, id) && ctx.dmg(ctx.active, ctx.foeActive, id) >= ctx.foeActive.hp;
}

module.exports = {
	id: 'eval_attack',
	bit: 1,
	decide(ctx) {
		ctx.eachSlot(() => {
			const id = ctx.moves[ctx.slot].id;
			const eff = ctx.effectOf(id);
			if (kills(ctx, id)) {
				if (eff === P + 'HALVE_DEFENSE') return; // terminate, no bonus
				if (eff && KILL_BONUS_EFFECTS.has(eff)) {
					if (ctx.aiRand(256) >= 170) ctx.addScore(4); // 33.6%
				} else if (eff === P + 'PRIORITY_1') {
					ctx.addScore(2);
				}
				return; // kill branch ends here — the else branch is skipped
			}
			if (damageScore(ctx) === 1) {
				ctx.subScore(1); // global ScoreMinus1 helper: -1 and terminate
				return;
			}
			if (eff && TOP_PENALTY_EFFECTS.has(eff)) {
				if (ctx.aiRand(256) >= 51) ctx.subScore(2); // 80.1%
			}
			if (ctx.eff(id, ctx.foeActive).class === 160) {
				if (ctx.aiRand(256) >= 80) ctx.addScore(2); // 68.75%, stacks
			}
			// terminate (script end for this move)
		});
	},
};
