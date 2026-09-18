'use strict';
// AI_FLAG_RISKY — Risky_Main (script 6518-6560).
// Spec: TRAINER_AI.md §3 doc 2078-2089.
//
// Partner -> terminate (singles-only engine — never fires; spec §3 preamble).
// The move's effect must be in the 25-entry Risky_RiskyEffects table
// (6533-6559); then 50% of +2: IfRandomLessThan 128 skip, else +2.
// The script ends normally (DONE -> next move); Risky_Terminate (label 6530,
// reused by BatonPass) is PopOrEnd, NOT BREAK — ctx.cancel is never set
// (see the long note in eval_attack.js).
//
// Table: 25 spec entries. Shell Smash has no carrier move in this fork
// (the fork dex/data_moves has no "shellsmash"), so its decomp enum cannot
// be named from the fork data — omitted as unobservable. Every other entry
// maps to an effect id present in ai/data_moves.js. ("Shell Smash-style
// random stat up" = RANDOM_STAT_UP_2, carried by Acupressure.)

const P = 'BATTLE_EFFECT_';

const RISKY_EFFECTS = new Set([
	P + 'STATUS_SLEEP',                 // Sleep (Hypnosis/Spore/Sleep Powder/Lovely Kiss)
	P + 'HALVE_DEFENSE',                // Explosion / Self-Destruct
	P + 'COPY_MOVE',                    // Mirror Move
	P + 'ONE_HIT_KO',                   // OHKO (Fissure/Horn Drill/Guillotine/Sheer Cold)
	P + 'HIGH_CRITICAL',                // high-critical hits (Karate Chop, Aeroblast, ...)
	P + 'STATUS_CONFUSE',               // Confuse (Confuse Ray/Sweet Kiss/Dynamic Punch)
	P + 'LEARN_MOVE_PERMANENT',         // Sketch
	P + 'LEVEL_DAMAGE_FLAT',            // Seismic Toss
	P + 'COUNTER',                      // Counter
	P + 'KO_MON_THAT_DEFEATED_USER',    // Destiny Bond
	P + 'ATK_UP_2_STATUS_CONFUSION',    // Swagger
	P + 'INFATUATE',                    // Attract
	P + 'USER_SP_ATK_DOWN_2',           // Draco Meteor
	// (Shell Smash — no fork carrier move; see header note)
	P + 'MAX_ATK_LOSE_HALF_MAX_HP',     // Belly Drum
	P + 'MIRROR_COAT',                  // Mirror Coat
	P + 'HIT_LAST_WHIFF_IF_HIT',        // Focus Punch
	P + 'DOUBLE_POWER_IF_HIT',          // Avalanche
	P + 'SET_SPIKES',                   // Spikes
	P + 'POWER_BASED_ON_LOW_SPEED',     // Gyro Ball
	P + 'RANDOM_STAT_UP_2',             // Shell Smash-style random stat up (Acupressure)
	P + 'METAL_BURST',                  // Metal Burst
	P + 'DOUBLE_POWER_IF_TARGET_HIT',   // Assurance
	P + 'USE_MOVE_FIRST',               // Me First
	P + 'HIT_FIRST_IF_TARGET_ATTACKING', // Sucker Punch
]);

module.exports = {
	id: 'risky',
	bit: 4,
	decide(ctx) {
		ctx.eachSlot(() => {
			const eff = ctx.effectOf(ctx.moves[ctx.slot].id);
			if (eff && RISKY_EFFECTS.has(eff)) {
				if (ctx.aiRand(256) >= 128) ctx.addScore(2); // 50%
			}
		});
	},
};
