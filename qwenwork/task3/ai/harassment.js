'use strict';
// AI_FLAG_HARRASSMENT — Harrassment_Main (pokeplatinum script.s:8008-8050,
// spec §3 "Harrassment_Main"). Ported from the script source.
//
// Dice: IfRandomLessThan N == (RandNext() % 256) < N (trainer_ai.c:635).
// The spec prose says "35 entries"; the table (script.s:8027-8061) holds
// exactly 34 TableEntry lines — code wins, all 34 listed below verbatim.

// Harrassment_Effects (script.s:8027-8061), verbatim decomp order/names.
const EFFECTS = new Set([
	'BATTLE_EFFECT_STATUS_SLEEP',
	'BATTLE_EFFECT_ATK_DOWN',
	'BATTLE_EFFECT_DEF_DOWN',
	'BATTLE_EFFECT_ACC_DOWN',
	'BATTLE_EFFECT_EVA_DOWN',
	'BATTLE_EFFECT_STATUS_CONFUSE',
	'BATTLE_EFFECT_ATK_DOWN_2',
	'BATTLE_EFFECT_DEF_DOWN_2',
	'BATTLE_EFFECT_SPEED_DOWN_2',
	'BATTLE_EFFECT_SP_DEF_DOWN_2',
	'BATTLE_EFFECT_STATUS_POISON',
	'BATTLE_EFFECT_STATUS_PARALYZE',
	'BATTLE_EFFECT_STATUS_LEECH_SEED',
	'BATTLE_EFFECT_ENCORE',
	'BATTLE_EFFECT_DECREASE_LAST_MOVE_PP',
	'BATTLE_EFFECT_SET_SPIKES',
	'BATTLE_EFFECT_ATK_UP_2_STATUS_CONFUSION',
	'BATTLE_EFFECT_INFATUATE',
	'BATTLE_EFFECT_TORMENT',
	'BATTLE_EFFECT_SP_ATK_UP_CAUSE_CONFUSION',
	'BATTLE_EFFECT_STATUS_BURN',
	'BATTLE_EFFECT_NATURE_POWER',
	'BATTLE_EFFECT_STATUS_SLEEP_NEXT_TURN',
	'BATTLE_EFFECT_REMOVE_HELD_ITEM',
	'BATTLE_EFFECT_MAKE_SHARED_MOVES_UNUSEABLE',
	'BATTLE_EFFECT_SECRET_POWER',
	'BATTLE_EFFECT_CONFUSE_ALL',
	'BATTLE_EFFECT_ATK_DEF_DOWN',
	'BATTLE_EFFECT_CAMOUFLAGE',
	'BATTLE_EFFECT_PREVENT_ITEM_USE',
	'BATTLE_EFFECT_TRANSFER_STATUS',
	'BATTLE_EFFECT_TOXIC_SPIKES',
	'BATTLE_EFFECT_REMOVE_HAZARDS_SCREENS_EVA_DOWN',
	'BATTLE_EFFECT_SP_ATK_DOWN_2_OPPOSITE_GENDER',
]);

// AddToMoveScore: s8 add, clamp <0 → 0 (trainer_ai.c:683-692).
function add(ctx, v) {
	ctx.addScore(v);
	if (ctx.scores[ctx.slot] < 0) ctx.scores[ctx.slot] = 0;
}

const targetIsPartner = (ctx) =>
	ctx.battle.battleType === 'doubles' && !!ctx.foeActive && ctx.foeActive.side === ctx.side;

module.exports = {
	id: 'harassment',
	bit: 10,

	decide(ctx) {
		ctx.eachSlot(() => {
			if (targetIsPartner(ctx)) return; // IfTargetIsPartner Terminate
			// LoadCurrentMoveEffect; IfLoadedNotInTable Harrassment_Effects →
			// Harrassment_Terminate.
			if (!EFFECTS.has(ctx.effectOf(ctx.moves[ctx.slot].id))) return;
			// 128/256 = 50% of +2; the other 50% bail unchanged.
			if (ctx.aiRand(256) < 128) return;
			add(ctx, 2);
		});
	},
};
