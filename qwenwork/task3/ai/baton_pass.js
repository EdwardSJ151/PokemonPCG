'use strict';
// AI_FLAG_BATON_PASS — BatonPass_Main (pokeplatinum script.s:6551-6633,
// spec TRAINER_AI.md §3 "BatonPass_Main"). Ported line-by-line from the
// script source; code (not comments/spec) is authoritative.
//
// Decomp dice: IfRandomLessThan N == (RandNext() % 256) < N
// (trainer_ai.c:635-644) — every "(N=x)" in the spec is the RAW THRESHOLD
// against 256, so the "31.25% (N=80)" jump is aiRand(256) < 80.
//
// Shared labels (script.s:1567-1618): ScorePlusN/ScoreMinusN are
// `AddToMoveScore ±N; PopOrEnd`. AICmd_AddToMoveScore (trainer_ai.c:683)
// adds and then CLAMPS negative results to 0 — the s8 container wraps at
// ±127 first. Reproduced by add() below.

// decomp sRiskyMoves (trainer_ai.c:30-42 — spec §1 "sNoDamageCalcMoveEffects")
// and sAltPowerCalcMoves (trainer_ai.c:47-58 — spec §1 "sAltPowerMoveEffects").
// Each module keeps its own copy: the decomp scripts are self-contained.
const NO_DAMAGE_CALC = new Set([
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
const ALT_POWER_CALC = new Set([
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

// FlagMoveDamageScore gate (trainer_ai.c:1119-1165): a damage comparison is
// made iff effect ∈ altPower, or (raw power > 1 and effect ∉ noDamageCalc).
// Returns true = "comparison was made" = the decomp's non-NO_COMPARISON state.
function comparisonMade(ctx, id) {
	const eff = ctx.effectOf(id);
	const m = ctx.move(id);
	return ALT_POWER_CALC.has(eff) || (!!m && m.basePower > 1 && !NO_DAMAGE_CALC.has(eff));
}

// AddToMoveScore: s8 add, then clamp <0 → 0 (trainer_ai.c:683-692).
function add(ctx, v) {
	ctx.addScore(v);
	if (ctx.scores[ctx.slot] < 0) ctx.scores[ctx.slot] = 0;
}

// IfHPPercentLessThan etc.: curHP * 100 / maxHP, C integer division
// (trainer_ai.c:695-707) — plain truncation, NOT the decompDivide ±1 clamp.
const hpPct = (p) => (p && p.maxhp ? Math.floor(p.hp * 100 / p.maxhp) : 0);

// IfTargetIsPartner (trainer_ai.c:2737): `(attacker&1)===(defender&1)`.
// Engine buildCtx always pairs our active against the opposing active, so
// this never fires in singles (no partner exists) nor through the engine's
// defender pairing; kept as the structural doubles gate.
const targetIsPartner = (ctx) =>
	ctx.battle.battleType === 'doubles' && !!ctx.foeActive && ctx.foeActive.side === ctx.side;

module.exports = {
	id: 'baton_pass',
	bit: 6,

	decide(ctx) {
		// IfMoveEffectKnown AI_BATTLER_ATTACKER, PASS_STATS_AND_STATUS:
		// scans the attacker's four MOVES (battleMons.moves — pp/disabled
		// slots included), not the observed set (trainer_ai.c:1846-1868).
		const knowsEffect = (eff) => ctx.active.moveSlots.some(
			(ms) => ms && ms.id && ctx.effectOf(ms.id) === eff);

		ctx.eachSlot(() => {
			if (targetIsPartner(ctx)) return; // IfTargetIsPartner Terminate

			// CountAlivePartyBattlers ATTACKER (trainer_ai.c:1233-1260):
			// alive party mons excluding the active (and, in doubles, the
			// partner) slot.
			const others = ctx.side.pokemon.filter(
				(m) => !m.fainted && m !== ctx.active &&
					!(ctx.battle.battleType === 'doubles' && m === ctx.side.active[1]));
			if (others.length === 0) return; // IfLoadedEqualTo 0 → BatonPass_Terminate

			const id = ctx.moves[ctx.slot].id;
			// FlagMoveDamageScore FALSE; IfLoadedNotEqualTo NO_COMPARISON →
			// BatonPass_Terminate: damaging/comparable moves are ignored.
			if (comparisonMade(ctx, id)) return;

			// Attacker without Baton Pass: 80/256 = 31.25% bail to the SHARED
			// Risky_Terminate (script.s:6520 — plain PopOrEnd, same effect).
			if (!knowsEffect('BATTLE_EFFECT_PASS_STATS_AND_STATUS') && ctx.aiRand(256) < 80) return;

			// ── BatonPass_EvalMove ─────────────────────────────────────────
			if (id === 'swordsdance' || id === 'dragondance' || id === 'calmmind' || id === 'nastyplot') {
				setupAtHighHP(); // jump; SetupAtHighHP ends every path in PopOrEnd
				return;
			}

			if (ctx.effectOf(id) === 'BATTLE_EFFECT_PROTECT') { // Protect AND Detect share it
				// BatonPass_EvalProtect: last used move ∈ {Protect, Detect} → −2…
				const prev = ctx.lastUsedOf(ctx.active);
				if (prev === 'protect' || prev === 'detect') { add(ctx, -2); return; }
				add(ctx, 2); // …else +2; PopOrEnd
				return;
			}

			if (id === 'batonpass') {
				// BatonPass_EvalBatonPass: turn 0 (first battle turn) → −2.
				if (ctx.turn - 1 === 0) { add(ctx, -2); return; }
				// Internal stage = fork boost + 6 (stage > 8 = ≥ +3, > 7 = ≥ +2,
				// > 6 = ≥ +1). FIRST match awards once, Attack before SpAttack;
				// comment 6616 ("+1 for each positive stat stage") is WRONG —
				// the code awards a single AddToMoveScore (spec quirk preserved).
				const b = ctx.active.boosts;
				const atk = b.atk + 6;
				const spa = b.spa + 6;
				if (atk > 8) { add(ctx, 3); return; }
				if (atk > 7) { add(ctx, 2); return; }
				if (atk > 6) { add(ctx, 1); return; }
				if (spa > 8) { add(ctx, 3); return; }
				if (spa > 7) { add(ctx, 2); return; }
				if (spa > 6) { add(ctx, 1); return; }
				return; // PopOrEnd — no stage, no score
			}

			// Any other non-damaging move: 20/256 = 7.8125% no change.
			if (ctx.aiRand(256) < 20) return;
			add(ctx, 3);
			// QUIRK (script.s:6589-6591): NO PopOrEnd here — control FALLS
			// THROUGH into BatonPass_SetupAtHighHP and adds a further
			// +5 / −10 / +1. The comment (6587) only mentions the +3.
			setupAtHighHP();

			// BatonPass_SetupAtHighHP. LoadTurnCount = battleCtx->totalTurns,
			// incremented only in TurnEnd (battle_controller_player.c:1853) →
			// decomp totalTurns === fork battle.turn - 1.
			function setupAtHighHP() {
				if (ctx.turn - 1 === 0) { add(ctx, 5); return; } // ScorePlus5
				if (hpPct(ctx.active) < 60) { add(ctx, -10); return; } // ScoreMinus10
				add(ctx, 1); // ScorePlus1
			}
		});
	},
};
