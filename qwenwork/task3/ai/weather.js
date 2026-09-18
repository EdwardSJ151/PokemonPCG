'use strict';
// AI_FLAG_WEATHER — Weather_Main (pokeplatinum script.s:7966-8006, spec §3).
// Ported from the script source; code beats spec/comments.
//
// Dice: IfRandomLessThan N == (RandNext() % 256) < N (trainer_ai.c:635).
// AddToMoveScore: s8 add then clamp <0 → 0 (trainer_ai.c:683-692).
//
// QUIRK (script.s:7973-7981): after the four weather-effect dispatches
// there is NO terminator — a move that is not one of the four weather-setup
// effects FALLS THROUGH INTO Weather_Sun and is only bailed out by the
// "sunny already up" check. So on the battle's first turn (decomp
// totalTurns 0) EVERY move other than the four setup moves (and Sunny Day)
// takes the Weather_ScorePlus5 gate: +5 when it is the attacker's first
// turn in battle and the field is not already sunny. The spec §3 summary
// ("Sun/Rain/Sandstorm/Hail setup moves…") misses this fall-through; the
// code is ported literally.

// AddToMoveScore semantics (see above).
function add(ctx, v) {
	ctx.addScore(v);
	if (ctx.scores[ctx.slot] < 0) ctx.scores[ctx.slot] = 0;
}

// LoadCurrentWeather (trainer_ai.c:1521-1546): SEQUENTIAL ifs against the
// raw WEATHER_IS_* field flags (later assignments win). The raw field is
// used, NOT ctx.weather (which routes through the fork's effectiveWeather()
// and blanks under Cloud Nine / Air Lock) — the decomp macros read the raw
// weather regardless of suppression. Deviation note: this fork has no fog
// (no 'fog' weather/move exists), so the AI_WEATHER_DEEP_FOG arm is
// unreachable; it is kept structurally for fidelity.
function loadCurrentWeather(ctx) {
	const w = ctx.battle.field.weather || 'clear'; // fork ids below
	let cur = 'clear';
	if (w === 'raindance') cur = 'rain';
	if (w === 'sandstorm') cur = 'sand';
	if (w === 'sunnyday') cur = 'sun';
	if (w === 'hail') cur = 'hail';
	if (w === 'fog') cur = 'fog'; // unreachable in this fork (documented gap)
	return cur;
}

const targetIsPartner = (ctx) =>
	ctx.battle.battleType === 'doubles' && !!ctx.foeActive && ctx.foeActive.side === ctx.side;

module.exports = {
	id: 'weather',
	bit: 9,

	decide(ctx) {
		ctx.eachSlot(() => {
			if (targetIsPartner(ctx)) return; // IfTargetIsPartner Terminate

			// LoadTurnCount; IfLoadedNotEqualTo 0 → Weather_Terminate.
			// totalTurns increments only at TurnEnd → decomp count = turn - 1.
			if (ctx.turn - 1 !== 0) return;

			const id = ctx.moves[ctx.slot].id;
			const eff = ctx.effectOf(id);

			// Dispatch by weather effect; anything unmatched falls into the
			// 'sun' arm (the decomp's label fall-through — see header).
			let check = 'sun';
			if (eff === 'BATTLE_EFFECT_WEATHER_RAIN') check = 'rain';
			else if (eff === 'BATTLE_EFFECT_WEATHER_SANDSTORM') check = 'sand';
			else if (eff === 'BATTLE_EFFECT_WEATHER_HAIL') check = 'hail';

			// Each arm compares the CURRENT weather against ITS OWN effect's
			// weather only (Weather_Sun/Rain/Sand/Hail all read LoadCurrentWeather
			// then IfLoadedEqualTo). The fall-through arms therefore test 'sun'.
			if (loadCurrentWeather(ctx) === check) return; // Weather_Terminate
			// Weather_ScorePlus5 (script.s:7999-8003): LoadIsFirstTurnInBattle —
			// fakeOutTurnNumber (= totalTurns+1 stamped at the switch-out that sent
			// the mon out) NOT less than totalTurns. Fork mapping: the observer
			// stamps entryTurn = battle.turn at the mon's first request after its
			// |switch| (leads stamp at turn 1), so first-decision-since-entry ⇔
			// entryTurn === turn. (Decomp's gate stays TRUE for two turns —
			// unreachable here because this block only ever runs at turn 1.)
			if (ctx.entryTurn(ctx.active) !== ctx.turn) return;
			add(ctx, 5);
		});
	},
};
