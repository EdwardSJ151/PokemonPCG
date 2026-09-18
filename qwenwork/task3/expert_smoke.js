'use strict';
// expert_smoke.js — targeted behavioral proof for ai/expert.js
// (AI_FLAG_EXPERT / Expert_Main).
//
// Method: 1v1 battles (p1 = fork 'default' policy, p2 = expert solo), with
// the engine's AI LCG (engine.rand.randNext) pinned to a CONSTANT so every
// decomp IfRandomLessThan n gate resolves hand-computably:
//   rnd(ctx, n) fires (jump taken)  ⟺  (C % 256) < n.
// With ai:'expert' solo the score base is 100 per usable slot (no
// eval_attack), so every assertion is exactly 100 + the decomp deltas.
// Slot 0 is p2's first move; snapshots are taken AFTER Engine.decide on the
// first live (non-team-preview) request.

const { runAI, pkm } = require('./ai_test.js');
const { Engine } = require('./ai/engine.js');

let snapshots = [];
let mutate = null;      // one-shot hook before the first live decide
let mutated = false;
let randCalls = 0;
let randVal = 100;

const origDecide = Engine.prototype.decide;
Engine.prototype.decide = function (req) {
	const live = !!(this.battle.sides[1].active && this.battle.sides[1].active[0]);
	if (live && mutate && !mutated) { mutate(this); mutated = true; }
	const r = origDecide.call(this, req);
	if (live) snapshots.push({ scores: this.scores.slice(), randCalls });
	return r;
};

async function scenario({ team1, team2, rand = 100, seed = [7, 3, 11, 5], before = null, firstMutate = null }) {
	snapshots = []; mutated = false; randCalls = 0; randVal = rand; mutate = firstMutate;
	await runAI({
		engineOpts: { ai: 'expert' },
		team1, team2, seed,
		onEngine: (eng) => {
			eng.rand.randNext = () => { randCalls++; return randVal; };
			if (before) before(eng);
		},
	});
	const snap = snapshots[0];
	if (!snap) throw new Error('no live decision captured');
	return snap;
}

let fails = 0;
function check(name, got, want) {
	const ok = JSON.stringify(got) === JSON.stringify(want);
	if (!ok) fails++;
	console.log(`${ok ? 'ok  ' : 'FAIL'}  ${name.padEnd(50)} got=${JSON.stringify(got)} want=${JSON.stringify(want)}`);
}

(async () => {
	// --- S1 RecoilMove (Double-Edge, RECOIL_HALF): rockhead → +1 ------------
	// normal vs torkoal(fire): class 40 → not "no damage" → ability branch.
	// No rolls consumed at all by this routine.
	let s = await scenario({
		team1: [pkm('torkoal', ['fireblast'])],
		team2: [pkm('rampardos', ['doubleedge'], { ability: 'rockhead' })],
	});
	check('S1 rockhead recoil +1', s.scores[0], 101);
	check('S1 baseline rolls (4 init + tiebreak)', s.randCalls, 5);

	s = await scenario({
		team1: [pkm('torkoal', ['fireblast'])],
		team2: [pkm('rampardos', ['doubleedge'], { ability: 'moldbreaker' })],
	});
	check('S1b no rockhead → +0', s.scores[0], 100);

	// --- S2 Thief: leftovers (hprestoregradual) vs empty -------------------
	// good hold → rnd(50): C=100 → 100<50 false → +1. no hold → −2, no roll.
	s = await scenario({
		team1: [pkm('blissey', ['tackle'], { item: 'leftovers' })],
		team2: [pkm('aipom', ['thief'])],
		rand: 100,
	});
	check('S2a thief good item +1', s.scores[0], 101);
	check('S2a gate roll', s.randCalls, 6);
	s = await scenario({
		team1: [pkm('blissey', ['tackle'])],
		team2: [pkm('aipom', ['thief'])],
	});
	check('S2b thief no item −2', s.scores[0], 98);
	check('S2b baseline only', s.randCalls, 5);

	// --- S3 Toxic (ToxicLeechSeed): no damaging move → checks skipped ------
	s = await scenario({
		team1: [pkm('snorlax', ['bodyslam'])],
		team2: [pkm('gastrodon', ['toxic'])],
	});
	check('S3 toxic neutral board +0', s.scores[0], 100);
	check('S3 baseline only', s.randCalls, 5);

	// --- S4 SleepTalk: asleep +10 (decomp global ScorePlus10) / −5 ---------
	s = await scenario({
		team1: [pkm('snorlax', ['bodyslam'])],
		team2: [pkm('audino', ['sleeptalk'])],
		before: (eng) => { eng.battle.sides[1].pokemon[0].status = 'slp'; },
	});
	check('S4a asleep → +10', s.scores[0], 110);
	s = await scenario({
		team1: [pkm('snorlax', ['bodyslam'])],
		team2: [pkm('audino', ['sleeptalk'])],
	});
	check('S4b awake → −5', s.scores[0], 95);

	// --- S5 Curse (ghost user): HP>80 → 0 ; HP<=80 → −1 --------------------
	s = await scenario({
		team1: [pkm('torkoal', ['fireblast'])],
		team2: [pkm('dusclops', ['curse'])],
	});
	check('S5a ghost HP 100% → +0', s.scores[0], 100);
	s = await scenario({
		team1: [pkm('torkoal', ['fireblast'])],
		team2: [pkm('dusclops', ['curse'])],
		before: (eng) => { const p = eng.battle.sides[1].pokemon[0]; p.hp = Math.ceil(p.maxhp / 2); },
	});
	check('S5b ghost HP 50% → −1', s.scores[0], 99);

	// --- S6 Synthesis at full HP: no bad weather → −2 skipped, then the
	// Recovery fallthrough: HP==100 → flat −3, zero extra rolls -------------
	s = await scenario({
		team1: [pkm('torkoal', ['fireblast'])],
		team2: [pkm('tropius', ['synthesis'])],
	});
	check('S6 synthesis full HP → −3', s.scores[0], 97);
	check('S6 baseline only', s.randCalls, 5);

	// --- S7 Trump Card pp==1 → +3, no rolls --------------------------------
	s = await scenario({
		team1: [pkm('torkoal', ['fireblast'])],
		team2: [pkm('electivire', ['trumpcard'])],
		firstMutate: (eng) => { eng.battle.sides[1].active[0].moveSlots[0].pp = 1; },
	});
	check('S7 trumpcard pp1 → +3', s.scores[0], 103);
	check('S7 baseline only', s.randCalls, 5);

	// --- S8 Fling poison barb (power 70 > 60) → TryPlus1(64): C=100 not
	// skipped → +1, one roll ------------------------------------------------
	s = await scenario({
		team1: [pkm('torkoal', ['fireblast'])],
		team2: [pkm('medicham', ['fling'], { item: 'poisonbarb' })],
		rand: 100,
	});
	check('S8 fling 70-power → +1', s.scores[0], 101);
	check('S8 gate roll', s.randCalls, 6);

	// --- S9 Hail with Blizzard known, sunny board ---------------------------
	// weather forced to sunnyday before the lead switch (inner focus does not
	// touch it): Hail → +1 (replaces weather) +2 (attacker knows Blizzard)
	// = +3. Slot 1 (Blizzard/BLIZZARD effect): ice vs fire(torkoal) = half →
	// "no damage" → TryMinus3 rnd(50): C=100 not skipped → −3.
	s = await scenario({
		team1: [pkm('torkoal', ['fireblast'])],
		team2: [pkm('glalie', ['hail', 'blizzard'], { ability: 'innerfocus' })],
		rand: 100,
		before: (eng) => { eng.battle.field.weather = 'sunnyday'; },
	});
	check('S9 hail slot +3', s.scores[0], 103);
	check('S9 blizzard slot −3', s.scores[1], 97);

	// --- S10 Spikes hazardSetup: rnd(128) flat gate -------------------------
	// C=100 → 100<128 jump taken → flat +0. C=200 → falls through → +1
	// (no roar/whirlwind known → no second roll).
	s = await scenario({
		team1: [pkm('torkoal', ['fireblast'])],
		team2: [pkm('cloyster', ['spikes'])],
		rand: 100,
	});
	check('S10a spikes 50% gate → +0', s.scores[0], 100);
	check('S10a gate roll', s.randCalls, 6);
	s = await scenario({
		team1: [pkm('torkoal', ['fireblast'])],
		team2: [pkm('cloyster', ['spikes'])],
		rand: 200,
	});
	check('S10b spikes → +1', s.scores[0], 101);
	check('S10b only the gate roll', s.randCalls, 6);

	// --- S11 Focus Punch first-turn gate: the +1 roll is suppressed on the
	// attacker's first turn in battle (IfLoadedNotEqualTo FALSE, End).
	s = await scenario({
		team1: [pkm('snorlax', ['bodyslam'])],
		team2: [pkm('machoke', ['focuspunch'])],
		rand: 220, // 220<200 false: a broken gate would add +1 here
	});
	check('S11 focuspunch first turn → +0', s.scores[0], 100);
	check('S11 baseline only', s.randCalls, 5);

	// --- S12 Acupressure mid band (50<hp<=90): rnd(128) fires → End.
	// C=200: 200<128 false → TryPlus1: 200<64 false → +1. -------------------
	// --- S13 foe-knowledge scan (EXPERT-DEV 16 workaround): turn 1 the foe
	// has not moved yet (no knowledge → typing-only coin path), turn 2 it is
	// KNOWN to know Earthquake → MagnetRise +1 (+typing/coin follow-up).
	// p1 blissey uses Earthquake on turn 1 (neutral, small damage: glalie
	// stays > 50% HP); C=200: rnd(128) not skipped → coin branch adds +1.
	// Turn 1: no knowledge, glalie not ground → coin → +1 → 101.
	// Turn 2: knows earthquake → +1, still not ground → coin +1 → 102.
	s = await scenario({
		team1: [pkm('torkoal', ['fireblast'])],
		team2: [pkm('wynaut', ['acupressure'])],
		rand: 200,
		before: (eng) => { const p = eng.battle.sides[1].pokemon[0]; p.hp = Math.ceil(p.maxhp * 0.75); },
	});
	check('S12 acupressure 75% HP → +1', s.scores[0], 101);
	s = await scenario({
		team1: [pkm('blissey', ['earthquake'])],
		team2: [pkm('glalie', ['magnetrise'])],
		rand: 200,
	});
	check('S13 turn1 no knowledge → coin +1', s.scores[0], 101);
	{
		// second live decision of the same battle
		snapshots = []; mutated = true; randCalls = 0; randVal = 200;
		await runAI({
			engineOpts: { ai: 'expert' },
			team1: [pkm('blissey', ['earthquake'])], team2: [pkm('glalie', ['magnetrise'])],
			seed: [7, 3, 11, 5],
			onEngine: (eng) => { eng.rand.randNext = () => { randCalls++; return 200; }; },
		});
		check('S13 turn2 earthquake known → +1 more', (snapshots[1] || {}).scores && snapshots[1].scores[0], 102);
	}

	console.log(fails === 0 ? 'PASS' : `FAIL (${fails})`);
	process.exit(fails === 0 ? 0 : 1);
})().catch((e) => { console.error('FATAL:', e); process.exit(1); });
