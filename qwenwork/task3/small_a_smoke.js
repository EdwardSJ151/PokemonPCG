'use strict';
// small_a_smoke.js — Task-3 SmallModulesA scenarios for eval_attack (bit 1),
// setup_first_turn (bit 3), risky (bit 4), prioritize_extremes (bit 5).
//
// Each scenario pins the FIRST p2 move request of a fresh battle and, for
// setup_first_turn, the second one. Expected scores/dice are NOT snapshotted
// from the engine: they are recomputed here by mirroring the AICpuLcg stream
// (16-bit LCG, s = s*1103515245 + 24691 mod 2^32, output s>>>16 — ai/rand.js)
// from the engine rand state captured at initEval entry of that request:
//   draw 1..4   initEval rolls[i] = 100 - draw % 16
//   draw ...    module gates, per-slot in eachSlot order (dice only at the
//               documented blocks: IfRandomLessThan N,skip -> delta iff
//               draw % 256 >= N)
//   draw last   MainSingles pick: randN(len) over the max-tied usable slots
// Expected scores therefore start at 100 per usable slot plus the mirrored
// deltas; the expected choice is recomputed with the decomp tie rule.
//
// Determinism: two same-seed runs must produce transcript-identical battles
// after stripping the wall-clock `|t:|` line (ai_test's raw compare flakes
// when the two runs straddle a second boundary — harness issue reported).

const { runAI, pkm } = require('./ai_test.js');
const { Engine } = require('./ai/engine.js');
const { AICpuLcg } = require('./ai/rand.js');

let failures = 0;
function check(name, cond, detail) {
	console.log(`${cond ? 'ok  ' : 'FAIL'}  ${name}${detail ? `  [${detail}]` : ''}`);
	if (!cond) failures++;
}

// Runs a battle; captures the first (and second, if any) p2 MOVE request:
// choice, scores, rolls, turn — plus the AI LCG state right before initEval.
async function capture(ai, team1, team2, seed) {
	const cap = {};
	const oi = Engine.prototype.initEval;
	const od = Engine.prototype.decide;
	let n = 0;
	Engine.prototype.initEval = function (p) {
		if (n === 0 && cap.randState === undefined) cap.randState = this.rand.s;
		return oi.call(this, p);
	};
	Engine.prototype.decide = function (req) {
		const r = od.call(this, req);
		if (req.active) {
			const rec = { choice: r, scores: this.scores.slice(), rolls: this.rolls.slice(), turn: this.battle.turn };
			if (n === 0) { cap.first = rec; n = 1; } else if (n === 1) { cap.second = rec; n = 2; }
		}
		return r;
	};
	let res;
	try {
		res = await runAI({ engineOpts: { ai }, team1, team2, seed });
	} finally {
		Engine.prototype.initEval = oi;
		Engine.prototype.decide = od;
	}
	if (!/^\|(win|tie)\|/.test(res.win)) throw new Error(`battle unresolved: ${res.win}`);
	if (res.errors) throw new Error(`${res.errors} p2 |error| lines`);
	if (!cap.first) throw new Error('no p2 move request captured');
	return { cap, res };
}

// Mirror the LCG from the captured pre-initEval state.
function mirror(cap) { return new AICpuLcg(cap.randState); }

// Assert the 4 initEval rolls match the mirrored draws.
function checkRolls(label, m, rolls) {
	for (let i = 0; i < 4; i++) {
		const expect = 100 - (m.randNext() % 16);
		check(`${label}: rolls[${i}] matches LCG`, rolls[i] === expect, `${rolls[i]} vs ${expect}`);
	}
}

// Expected MainSingles pick from expected scores (decomp tie rule + one
// randN(len) draw). Slots are all usable (4-move test sets).
function expectPick(m, scores) {
	const best = Math.max(...scores);
	const list = [0, 1, 2, 3].filter((i) => scores[i] === best);
	return `move ${list[m.randN(list.length)] + 1}`;
}

const SNORLAX = pkm('snorlax', ['bodyslam', 'earthquake', 'swordsdance', 'rest']);
const BACKUP_GENGAR = pkm('gengar', ['shadowball', 'thunderbolt', 'willowisp', 'hypnosis']);
const stripT = (t) => t.filter((l) => !/^\|t:\|/.test(l)).join('\n');

async function scenario(label, ai, team1, team2, seed, assert) {
	const { cap, res } = await capture(ai, team1, team2, seed);
	const second = await runAI({ engineOpts: { ai }, team1, team2, seed }); // timestamp-only determinism run
	check(`${label}: battle content deterministic (minus |t:|)`, stripT(res.transcript) === stripT(second.transcript));
	assert(cap, res);
}

(async () => {
	// ---------------------------------------------------------------- S1
	// eval_attack, all four slots damage-eligible, none KOs a full-HP
	// Snorlax (Onix Atk 45 @L50 vs ~250 HP — no dice in the kill branch;
	// and the kill branch never fires so the N=170/N=51/N=80 blocks all
	// stay unentered except the quad-effective check, which never matches:
	// ground/steel/dark/rock vs pure Normal are x1/x1/x1/x(1/2)).
	// Earthquake (100, x1) strictly outs-DMs Iron Head (80 x1), Crunch
	// (80 x1) and Rock Slide (75 x1/2) -> those three hit the ScoreMinus1
	// helper (-1, terminate-this-move). EQ: top move -> no penalty effect,
	// not quad -> untouched at 100. Unique max -> 'move 1'.
	await scenario('S1 eval_attack top-damage', 'eval_attack',
		[SNORLAX], [pkm('onix', ['earthquake', 'ironhead', 'crunch', 'rockslide']), BACKUP_GENGAR],
		[7, 3, 11, 5], (cap) => {
			const m = mirror(cap);
			checkRolls('S1', m, cap.first.rolls);
			const scores = cap.first.scores;
			check('S1 scores == [100,99,99,99] (EQ top; others ScoreMinus1)',
				scores.join(',') === '100,99,99,99', scores.join(','));
			// one draw for the unique-max tie list (randN(1)) -> move 1
			check('S1 choice == predicted pick', cap.first.choice === expectPick(m, scores),
				`${cap.first.choice}`);
		});

	// ---------------------------------------------------------------- S2
	// setup_first_turn: Swords Dance (slot 0, ATK_UP_2) and Bulk Up
	// (slot 2, ATK_DEF_UP) are in the table; Karate Chop (HIT) and Rest
	// (REST) are not. Gate N=80: +2 iff mirrored draw % 256 >= 80.
	// Second move request (turn 2): gate says terminate -> script ends per
	// move (PopOrEnd/DONE, NOT BREAK) -> module scores nothing anywhere.
	await scenario('S2 setup_first_turn turn-1 gates', 'setup_first_turn',
		[SNORLAX], [pkm('machoke', ['swordsdance', 'karatechop', 'bulkup', 'rest']), BACKUP_GENGAR],
		[13, 29, 7, 3], (cap) => {
			check('S2 first request is turn 1 (decomp turn 0)', cap.first.turn === 1, `turn=${cap.first.turn}`);
			const m = mirror(cap);
			checkRolls('S2', m, cap.first.rolls);
			const g0 = m.randNext(), g2 = m.randNext(); // gate dice in slot order
			const scores = [
				100 + (g0 % 256 >= 80 ? 2 : 0), 100, 100 + (g2 % 256 >= 80 ? 2 : 0), 100,
			];
			check(`S2 scores match gates (g0=${g0}, g2=${g2}; +2 iff draw%256>=80)`,
				cap.first.scores.join(',') === scores.join(','),
				`${cap.first.scores.join(',')} vs ${scores.join(',')}`);
			check('S2 choice == predicted pick', cap.first.choice === expectPick(m, scores),
				`${cap.first.choice}`);
			check('S2 turn-2 request: module fully inactive (all 100s)',
				cap.second && cap.second.turn === 2 && cap.second.scores.join(',') === '100,100,100,100',
				cap.second ? `turn=${cap.second.turn} scores=${cap.second.scores.join(',')}` : 'no second request');
		});

	// ---------------------------------------------------------------- S3
	// prioritize_extremes: Earthquake (top) and Iron Head (not-top) both
	// make a comparison -> NO dice, NO score change (the -1 penalty is
	// eval_attack's, not this flag's). Bulk Up and Stealth Rock get no
	// damage calc at all (power 0) -> AI_NO_COMPARISON_MADE -> 60.9% gate
	// (N=100) each: +2 iff draw % 256 >= 100.
	await scenario('S3 prioritize_extremes no-comparison gates', 'prioritize_extremes',
		[SNORLAX], [pkm('onix', ['earthquake', 'ironhead', 'bulkup', 'stealthrock']), BACKUP_GENGAR],
		[5, 23, 9, 17], (cap) => {
			const m = mirror(cap);
			checkRolls('S3', m, cap.first.rolls);
			const g2 = m.randNext(), g3 = m.randNext();
			const scores = [100, 100, 100 + (g2 % 256 >= 100 ? 2 : 0), 100 + (g3 % 256 >= 100 ? 2 : 0)];
			check(`S3 scores match gates (g2=${g2}, g3=${g3}; +2 iff draw%256>=100; comparable moves untouched)`,
				cap.first.scores.join(',') === scores.join(','),
				`${cap.first.scores.join(',')} vs ${scores.join(',')}`);
			check('S3 choice == predicted pick', cap.first.choice === expectPick(m, scores),
				`${cap.first.choice}`);
		});

	// ---------------------------------------------------------------- S4
	// risky: Hypnosis (STATUS_SLEEP) and Belly Drum (MAX_ATK_LOSE_HALF_MAX_HP)
	// are in Risky_RiskyEffects -> 50% gate (N=128) each. Shadow Ball
	// (LOWER_SP_DEF_HIT) and Protect (PROTECT) are not -> untouched.
	await scenario('S4 risky effect gates', 'risky',
		[SNORLAX], [pkm('gengar', ['hypnosis', 'shadowball', 'protect', 'bellydrum']), BACKUP_GENGAR],
		[31, 7, 3, 41], (cap) => {
			const m = mirror(cap);
			checkRolls('S4', m, cap.first.rolls);
			const g0 = m.randNext(), g3 = m.randNext();
			const scores = [100 + (g0 % 256 >= 128 ? 2 : 0), 100, 100, 100 + (g3 % 256 >= 128 ? 2 : 0)];
			check(`S4 scores match gates (g0=${g0}, g3=${g3}; +2 iff draw%256>=128)`,
				cap.first.scores.join(',') === scores.join(','),
				`${cap.first.scores.join(',')} vs ${scores.join(',')}`);
			check('S4 choice == predicted pick', cap.first.choice === expectPick(m, scores),
				`${cap.first.choice}`);
		});

	console.log(failures ? `FAIL (${failures} check(s))` : 'PASS');
	process.exit(failures ? 1 : 0);
})().catch((e) => { console.error('FATAL:', e); process.exit(1); });
