'use strict';
// Voluntary-switch ROLL-SUCCESS coverage (session-3 gap: the probes only
// exercised roll-FAIL paths). Technique: inject the AI dice for the FIRST
// decisions by shadowing engine.randNext — in these scenarios the first
// randNext call inside shouldSwitch is the gated roll (earlier sub-checks
// early-return without consuming dice), so the queue pins success/failure
// deterministically; the real LCG resumes afterwards (assertions only look
// at the first decision).
//
// Scenarios (spec §4 / engine shouldSwitch order):
//   S1 aiOnlyIneffectiveMoves 66% PASS — magnemite (all-Electric, chart-immune
//      vs Ground/Rock onix, numMoves=2) → switch to zapdos (surf, 4x SE):
//      dice 0 → 0%3<2 → switch. Slot order: slot1 gengar has NO SE damaging
//      move (its dice never roll), slot2 zapdos surf rolls → chosen.
//   S2 aiCannotDamageWonderGuard 66% PASS — snorlax lead (Normal moves NVE vs
//      Flying, none SE) vs ability-wonderguard zapdos → switch to onix
//      (rockslide SE): dice 0 → 0%3<2 → switch.
//   S3 same state as S1, dice 2 → 2%3 !< 2 → ROLL FAILS → no switch; the
//      decision falls through to move evaluation (first choice is a move).
//
//   node qwenwork/task3/switch_smoke.js
const { runAI, pkm } = require('./ai_test.js');

let ok = true;
const chk = (c, m) => { console.log(`${c ? 'ok  ' : 'FAIL'}  ${m}`); if (!c) ok = false; };

// Shadow randNext with a scripted prefix queue, and capture every decision.
function instrument(eng, queue) {
	const real = Object.getPrototypeOf(eng);
	const q = queue.slice();
	eng.randNext = function () { return q.length ? q.shift() : real.randNext.call(eng); };
	eng.randN = function (n) { return this.randNext() % n; };
	const decisions = [];
	const orig = eng.decide.bind(eng);
	eng.decide = function (req) {
		const c = orig(req);
		decisions.push({ req, c });
		return c;
	};
	return { decisions };
}

(async () => {
	// S1: only-ineffective → switch on roll pass
	const team1_s1 = [pkm('onix', ['rockslide', 'earthquake', 'body slam', 'roar']), pkm('snorlax', ['bodyslam', 'rest', 'earthquake', 'swordsdance'])];
	const team2_s1 = [
		pkm('magnemite', ['thunderbolt', 'thunder', 'rest'], { nature: 'modest' }),
		pkm('gengar', ['shadowball', 'thunderbolt', 'willowisp', 'hypnosis']),
		pkm('zapdos', ['surf', 'drillpeck', 'heatwave', 'thunderbolt']),
	];
	const s1 = await runAI({
		team1: team1_s1, team2: team2_s1,
		engineOpts: {},
		onEngine: (eng) => { globalThis.__s1 = instrument(eng, [0]); },
	});
	const first1 = globalThis.__s1.decisions.find((d) => d.c && d.c.startsWith('switch')) || globalThis.__s1.decisions[0];
	chk(first1 && first1.c === 'switch 3', `S1 onlyIneffective PASS-roll → switch 3 (got ${first1 && first1.c})`);
	chk(s1.transcript.some((l) => l.startsWith('|switch|p2a: Zapdos')), 'S1 Zapdos actually switched in');

	// S2: wonder-guard 66% PASS-roll
	const team1_s2 = [pkm('zapdos', ['thunderbolt', 'drillpeck', 'heatwave', 'rest'], { ability: 'wonderguard' }), pkm('snorlax', ['bodyslam', 'rest', 'earthquake', 'swordsdance'])];
	const team2_s2 = [
		pkm('snorlax', ['bodyslam', 'return', 'rest', 'earthquake'], { nature: 'bold' }),
		pkm('machop', ['karatechop', 'crosschop', 'bulkup', 'rest']),
		pkm('onix', ['rockslide', 'earthquake', 'body slam', 'roar']),
	];
	await runAI({
		team1: team1_s2, team2: team2_s2,
		engineOpts: {},
		onEngine: (eng) => { globalThis.__s2 = instrument(eng, [0]); },
	});
	const first2 = globalThis.__s2.decisions[0];
	chk(first2 && first2.c === 'switch 3', `S2 wonderGuard PASS-roll → switch 3 (got ${first2 && first2.c})`);

	// S3: same state as S1, dice FAIL the 66% → must NOT switch (first decision is a move)
	await runAI({
		team1: team1_s1, team2: team2_s1,
		engineOpts: {},
		onEngine: (eng) => { globalThis.__s3 = instrument(eng, [2, 2]); },
	});
	const first3 = globalThis.__s3.decisions[0];
	chk(first3 && /^move /.test(first3.c), `S3 onlyIneffective FAIL-roll → move (got ${first3 && first3.c})`);

	console.log(ok ? 'PASS' : 'FAIL');
})().catch((e) => { console.error('FATAL:', e); process.exit(1); });
