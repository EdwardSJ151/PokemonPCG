'use strict';
// AI_FLAG_SETUP_FIRST_TURN — SetupFirstTurn_Main (script 6414-6495).
// Spec: TRAINER_AI.md §3 doc 2055-2067.
//
// Partner -> terminate (never fires: singles-only engine, spec §3 preamble).
// Not turn 0 -> terminate: the decomp's battleCtx->totalTurns is 0-based,
// the fork's battle.turn is 1 at the first move request (probed) — decomp
// "turn 0" == ctx.turn === 1. The gate sits inside the per-move script, so
// "terminate" (PopOrEnd = DONE, spec §2; NOT BREAK — see eval_attack.js's
// identical note) just ends that move's script; net effect off turn 1 is a
// no-op, and later flags still run.
// The move's effect must be in the 61-entry SetupFirstTurn_SetupEffects
// table (6432-6494). Then 68.75% of +2: IfRandomLessThan 80 skip, else +2
// (176/256).
//
// Table reconstruction note: the spec summarises the entries ("all single-
// and double-stage stat-up/down effects" + the named list). Enum names are
// taken verbatim from the fork effect table (ai/data_moves.js) — decomp-only
// entries with no fork move carrying the effect (e.g. a one-stage ACC/EVA
// counterpart where the fork data has none, the duplicated CAMOUFLAGE row
// at 6474/6487 — a Set dedups it, and Work Up whose fork effect id has no
// carrier) are unobservable through the fork data and are omitted or, where
// the enum exists in data_moves.js, included. Observable behaviour for every
// real fork move matches the spec list.

const P = 'BATTLE_EFFECT_';

// All single- and double-stage stat up/down effects (pure stage effects,
// incl. the two-stat combined enums — Calm Mind is on the spec's named list
// and is exactly such a combined enum). The fork data has no single-stage
// SP_ATK_DOWN and no bare SP_ATK_DOWN_2 enum (only the distinct
// SP_ATK_DOWN_2_OPPOSITE_GENDER), so those table legs are unobservable.
const SETUP_EFFECTS = new Set([
	// attack
	P + 'ATK_UP', P + 'ATK_UP_2', P + 'ATK_DOWN', P + 'ATK_DOWN_2',
	// defense
	P + 'DEF_UP', P + 'DEF_UP_2', P + 'DEF_DOWN', P + 'DEF_DOWN_2',
	// special attack (no single-stage SP_ATK_DOWN in the fork data)
	P + 'SP_ATK_UP', P + 'SP_ATK_UP_2',
	// special defense (no single-stage SP_DEF_UP/DOWN enums in the fork data)
	P + 'SP_DEF_UP_2', P + 'SP_DEF_DOWN_2',
	// speed (no single-stage SPEED_UP enum in the fork data)
	P + 'SPEED_UP_2', P + 'SPEED_DOWN', P + 'SPEED_DOWN_2',
	// accuracy / evasion (no ACC_UP / ACC_DOWN_2 / bare EVA_UP_2 enums)
	P + 'ACC_DOWN', P + 'EVA_UP', P + 'EVA_DOWN',
	// combined-stat single-stage enums
	P + 'ATK_DEF_UP', P + 'ATK_DEF_DOWN', P + 'ATK_SPD_UP', P + 'DEF_SPD_UP',
	P + 'SP_ATK_SP_DEF_UP',
	// named entries from the spec list:
	P + 'CONVERSION',                      // Conversion
	P + 'SET_LIGHT_SCREEN',                // Light Screen
	P + 'SET_REFLECT',                     // Reflect
	P + 'SET_SUBSTITUTE',                  // Substitute
	P + 'STATUS_LEECH_SEED',               // Leech Seed
	P + 'EVA_UP_2_MINIMIZE',               // Minimize
	P + 'CURSE',                           // Curse
	P + 'ATK_UP_2_STATUS_CONFUSION',       // Swagger
	P + 'CAMOUFLAGE',                      // Camouflage (listed twice, 6474/6487)
	P + 'STATUS_SLEEP_NEXT_TURN',          // Yawn
	P + 'DEF_UP_DOUBLE_ROLLOUT_POWER',     // Defense Curl
	P + 'TORMENT',                         // Torment
	P + 'STATUS_BURN',                     // Will-O-Wisp
	P + 'RESTORE_HP_EVERY_TURN',           // Aqua Ring
	P + 'DISABLE',                         // Disable (Spite is DECREASE_LAST_MOVE_PP)
	P + 'SET_SPIKES',                      // Spikes
	P + 'CRIT_UP_2',                       // Focus Energy (crit "stage")
	P + 'GIVE_GROUND_IMMUNITY',            // Magnet Rise
	P + 'REMOVE_HAZARDS_SCREENS_EVA_DOWN', // Defog
	P + 'WHIRLPOOL',                       // Whirlpool
]);

module.exports = {
	id: 'setup_first_turn',
	bit: 3,
	decide(ctx) {
		ctx.eachSlot(() => {
			if (ctx.turn !== 1) return; // decomp turn 0 (0-based) == fork turn 1
			const eff = ctx.effectOf(ctx.moves[ctx.slot].id);
			if (eff && SETUP_EFFECTS.has(eff)) {
				if (ctx.aiRand(256) >= 80) ctx.addScore(2); // 68.75%
			}
		});
	},
};
