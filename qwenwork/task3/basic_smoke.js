'use strict';
// basic_smoke.js — AI_FLAG_BASIC scenario proofs (hand-computed from the
// spec constants in TRAINER_AI.md §1/§3; scores captured per decision by
// wrapping Engine.prototype.decide like ai_test.js prngNeutrality).
//
// Expected scores derive from the s8 pipeline: init 100 per usable slot,
// then the documented penalties. Every scenario pins the p2 (AI) scores of
// the FIRST move decision exactly, plus the allowed final choices.
//
// Run: node qwenwork/task3/basic_smoke.js

const { runAI, pkm } = require('./ai_test.js');
const { Engine } = require('./ai/engine.js');

// Run one battle; pre(battle) mutates live state right before the first
// MOVE decision (boosts/HP set-up); returns the per-move-decision captures.
async function captureAI({ team1, team2, pre, seed = [7, 3, 11, 5] }) {
	const seen = [];
	const orig = Engine.prototype.decide;
	Engine.prototype.decide = function (req) {
		if (!req.wait && req.active && !this.__preDone) {
			this.__preDone = true;
			if (pre) pre(this.battle);
		}
		const r = orig.call(this, req);
		if (typeof r === 'string' && r.startsWith('move '))
			seen.push({ scores: this.scores.slice(), choice: r });
		return r;
	};
	let res;
	try { res = await runAI({ seed, team1, team2, engineOpts: { ai: 'basic' } }); }
	finally { Engine.prototype.decide = orig; }
	return { seen, res };
}

let fails = 0;
function check(name, ok, detail) {
	console.log(`${ok ? 'ok  ' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
	if (!ok) fails++;
}

// [karatechop, rockslide, swordsdance, bulkup] vs a Ghost:
//  slot0 karatechop: eligible (HighCritical, power 50), Fighting→Ghost is
//  immune on the real gen4 chart → mask&IMMUNE → -10 → 90 (immunity check,
//  script.s:68). slots1-3: rockslide neutral (eligible, no penalty); the two
//  status moves are NO_COMPARISON (immunity skipped), fresh stages → 100.
async function s1_immune() {
	const { seen, res } = await captureAI({
		team1: [pkm('duskull', ['shadowball', 'confuseray', 'nightshade', 'disable'])],
		team2: [pkm('machoke', ['karatechop', 'rockslide', 'swordsdance', 'bulkup'])],
	});
	check('S1 immune damaging move scores 90, neutrals 100',
		seen.length > 0 && JSON.stringify(seen[0].scores) === JSON.stringify([90, 100, 100, 100]),
		`scores=${seen[0] && seen[0].scores}`);
	check('S1 AI never picks the immune move (slot 1)',
		seen.length > 0 && ['move 2', 'move 3', 'move 4'].includes(seen[0].choice),
		`choice=${seen[0] && seen[0].choice}`);
	check('S1 clean battle', res.errors === 0, `${res.errors} p2 errors`);
}

// [earthquake, watergun, hardhammer, psychic] vs Gligar (Levitate,
// Ground/Flying — 4x water-weak):
//  earthquake: eff sets LEVITATED → IMMUNE mask → -10; then the ability
//  block: Ground & Levitate → -12 (101-104) → 78.
//  watergun: not immune, but the ported bug at 106-109 (should branch on
//  Dry Skin) gives Water & Levitate → -12 → 88 — the "Levitate takes the
//  -12" quirk (it also explains why the Ground move is hit twice).
//  hammerarm/psychic: neutral hits, no dispatch entry → 100.
async function s2_levitate() {
	const { seen, res } = await captureAI({
		team1: [pkm('gligar', ['hyperfang', 'wingattack', 'rockslide', 'crunch'], { ability: 'levitate' })],
		team2: [pkm('machoke', ['earthquake', 'watergun', 'hammerarm', 'psychic'])],
	});
	check('S2 Levitate: ground move 78 (-10 immune -12 ability), water move 88 (buggy -12)',
		seen.length > 0 && JSON.stringify(seen[0].scores) === JSON.stringify([78, 88, 100, 100]),
		`scores=${seen[0] && seen[0].scores}`);
	check('S2 AI avoids both penalized moves',
		seen.length > 0 && ['move 3', 'move 4'].includes(seen[0].choice),
		`choice=${seen[0] && seen[0].choice}`);
	check('S2 clean battle', res.errors === 0, `${res.errors} p2 errors`);
}

// [growl, tackle, karatechop, leechseed] vs a Soundproof Electrode:
//  growl ∈ the 11 sound moves and the defender has Soundproof → -10 (115-131);
//  the ATK_DOWN check routine adds nothing (target atk stage internal 6, no
//  Hyper Cutter / Clear Body / White Smoke) → 90.
//  others → 100 (leechseed: Magic Guard no, no grass slots; NO_COMPARISON).
async function s3_soundproof() {
	const { seen, res } = await captureAI({
		team1: [pkm('electrode', ['spark', 'tackle', 'doubleteam', 'refresh'], { ability: 'soundproof' })],
		team2: [pkm('machoke', ['growl', 'tackle', 'karatechop', 'leechseed'])],
	});
	check('S3 soundproof: Growl 90, others 100',
		seen.length > 0 && JSON.stringify(seen[0].scores) === JSON.stringify([90, 100, 100, 100]),
		`scores=${seen[0] && seen[0].scores}`);
	check('S3 AI does not open with the sound move',
		seen.length > 0 && seen[0].choice !== 'move 1', `choice=${seen[0] && seen[0].choice}`);
	check('S3 clean battle', res.errors === 0, `${res.errors} p2 errors`);
}

// CheckHighStatStage / two-stat families vs the Simple variants.
// Swords Dance (ATK_UP_2) + Bulk Up (ATK_DEF_UP) with the attacker's ATK at:
//  A) internal 12 (+6), no Simple: SD -10 → 90; BulkUp: first stat at 12 →
//     -10, second (def, internal 6) nothing → 90.
//  B) internal 10 (+4), Simple: SD: 10 > 8 → -10 → 90; BulkUp -10 → 90.
//  C) internal 10 (+4), no Simple: 10 !== 12 → NO penalty → 100 / 100.
async function s4_highStage() {
	const foe = () => [pkm('electrode', ['spark', 'tackle', 'doubleteam', 'refresh'], { ability: 'soundproof' })];
	const mk = (ability) => () => [pkm('machoke', ['swordsdance', 'bulkup', 'karatechop', 'tackle'], { ability })];
	const preAtk = (b) => { b.sides[1].active[0].boosts.atk = 6; };
	const preAtk4 = (b) => { b.sides[1].active[0].boosts.atk = 4; };

	const a = await captureAI({ team1: foe(), team2: mk('')(), pre: preAtk });
	check('S4a HighStatStage +6 non-Simple: Swords Dance & Bulk Up penalized 90',
		a.seen.length > 0 && JSON.stringify(a.seen[0].scores) === JSON.stringify([90, 90, 100, 100]),
		`scores=${a.seen[0] && a.seen[0].scores}`);

	const b = await captureAI({ team1: foe(), team2: mk('simple')(), pre: preAtk4 });
	check('S4b HighStatStage +4 with Simple (internal 10 > 8): penalized 90',
		b.seen.length > 0 && JSON.stringify(b.seen[0].scores) === JSON.stringify([90, 90, 100, 100]),
		`scores=${b.seen[0] && b.seen[0].scores}`);

	const c = await captureAI({ team1: foe(), team2: mk('')(), pre: preAtk4 });
	check('S4c HighStatStage +4 without Simple (10 !== 12): no penalty',
		c.seen.length > 0 && JSON.stringify(c.seen[0].scores) === JSON.stringify([100, 100, 100, 100]),
		`scores=${c.seen[0] && c.seen[0].scores}`);
}

// CheckBellyDrum (335-337): -10 when the attacker's HP% < 51.
//  E1) hp = 40% → [90,100,100,100]
//  E2) hp = ceil(51%) → pct(hp*100/maxhp) ≥ 51 → [100,100,100,100]
async function s5_bellydrum() {
	const foe = () => [pkm('electrode', ['spark', 'tackle', 'doubleteam', 'refresh'], { ability: 'soundproof' })];
	const team = () => [pkm('machoke', ['bellydrum', 'karatechop', 'tackle', 'leechseed'])];
	const low = (b) => { const p = b.sides[1].active[0]; p.hp = (p.maxhp * 40 / 100) | 0; };
	const edge = (b) => { const p = b.sides[1].active[0]; p.hp = Math.ceil(p.maxhp * 0.51); };

	const a = await captureAI({ team1: foe(), team2: team(), pre: low });
	check('S5a BellyDrum at 40% HP penalized 90',
		a.seen.length > 0 && JSON.stringify(a.seen[0].scores) === JSON.stringify([90, 100, 100, 100]),
		`scores=${a.seen[0] && a.seen[0].scores}`);
	check('S5a AI picks a non-BellyDrum move',
		a.seen.length > 0 && a.seen[0].choice !== 'move 1', `choice=${a.seen[0] && a.seen[0].choice}`);

	const b = await captureAI({ team1: foe(), team2: team(), pre: edge });
	check('S5b BellyDrum at ≥51% HP not penalized 100',
		b.seen.length > 0 && b.seen[0].scores[0] === 100,
		`scores=${b.seen[0] && b.seen[0].scores}`);
}

// Wonder Guard (shedinja): a neutral/immune move takes -10 (mask) and the
// -12 Wonder Guard line (class 0 ∉ {80,160}); the SE Flamewheel (class 80)
// passes both immunity branches untouched. Stat-down routines add nothing
// (Leer → DEF_DOWN checks, Smokescreen → ACC_DOWN routine, all clean).
async function s6_wonderguard() {
	const { seen, res } = await captureAI({
		team1: [pkm('shedinja', ['shadowclaw', 'confuseray', 'slash', 'swordsdance'], { ability: 'wonderguard' })],
		team2: [pkm('magby', ['flamewheel', 'scratch', 'leer', 'smokescreen'])],
	});
	check('S6 Wonder Guard: immune Scratch 78 (-10 -12), SE Flamewheel 100',
		seen.length > 0 && JSON.stringify(seen[0].scores) === JSON.stringify([100, 78, 100, 100]),
		`scores=${seen[0] && seen[0].scores}`);
	check('S6 AI never picks Scratch',
		seen.length > 0 && seen[0].choice !== 'move 2', `choice=${seen[0] && seen[0].choice}`);
	check('S6 clean battle', res.errors === 0, `${res.errors} p2 errors`);
}

// OHKO (Fissure, ONE_HIT_KO): skips the damage comparison (56-58) but runs
// the immunity checks → Ground vs Levitate Gligar: mask LEVITATED → -10,
// Ground&Levitate → -12; then CheckOHKOWouldFail: not immune-class? class 0
// → -10 MORE, Sturdy no, level tie no → 100-10-12-10 = 68.
// vs non-Levitate (Electrode, level 50 tie): class 0? Ground→Electric is
// neutral, no WG → class 40; no ability line → OHKOWouldFail: class ≠ 0, no
// Sturdy, level 50 = 50 → no penalty → 100.
async function s7_ohko() {
	const a = await captureAI({
		team1: [pkm('gligar', ['hyperfang', 'wingattack', 'rockslide', 'crunch'], { ability: 'levitate' })],
		team2: [pkm('machoke', ['fissure', 'watergun', 'hammerarm', 'psychic'])],
	});
	check('S7a OHKO vs Levitate: 68 (-10 immune -12 levitate -10 class-0 in CheckOHKOWouldFail)',
		a.seen.length > 0 && a.seen[0].scores[0] === 68,
		`scores=${a.seen[0] && a.seen[0].scores}`);

	const b = await captureAI({
		team1: [pkm('electrode', ['spark', 'tackle', 'doubleteam', 'refresh'], { ability: 'soundproof' })],
		team2: [pkm('machoke', ['fissure', 'karatechop', 'hammerarm', 'psychic'])],
	});
	check('S7b OHKO vs clean target: 100 (immunity path runs, nothing fires)',
		b.seen.length > 0 && b.seen[0].scores[0] === 100,
		`scores=${b.seen[0] && b.seen[0].scores}`);
}

// Soundproof Mold-Breaker exemption + the s8-wrapped Magnitude quirk:
// Magnitude carries effect BATTLE_EFFECT_PSYWAVE → CheckMagnitude: the
// Mold-Breaker guard tests the stale calcTemp (0/1/2 — never an ability
// enum), so the commented exception is DEAD and the defender's Levitate
// -10 lands even though the attacker has Mold Breaker. (A "fixed" guard
// would leave Magnitude at 100 here.) Magnitude is eligible (effect in
// neither list, fork basePower 70 > 1); as an MB hit eff() sets no
// LEVITATED flag (decomp-consistent: MB ignores the ability), so the ONLY
// penalty is the CheckMagnitude -10 → 90.
async function s8_magnitudeMB() {
	const { seen } = await captureAI({
		team1: [pkm('gligar', ['hyperfang', 'wingattack', 'rockslide', 'crunch'], { ability: 'levitate' })],
		team2: [pkm('machoke', ['magnitude', 'watergun', 'hammerarm', 'psychic'], { ability: 'moldbreaker' })],
	});
	check('S8 CheckMagnitude dead MB guard: Mold Breaker still eats the Levitate -10 (90)',
		seen.length > 0 && seen[0].scores[0] === 90,
		`scores=${seen[0] && seen[0].scores}`);
}

// Hail +8 Ice Body quirk: defender Ice Body -8, attacker Ice Body +8 →
async function s9_hail() {
	// A glass-cannon defender keeps the battle finite (Ice Beam 2× resolves
	// fast); its ability carries the -8, the attacker's Ice Body the +8.
	const { seen } = await captureAI({
		team1: [pkm('raichu', ['thunderbolt', 'thunder', 'substitute', 'agility'], { ability: 'icebody' })],
		team2: [pkm('articuno', ['hail', 'icebeam', 'peck', 'tailwind'], { ability: 'icebody' })],
	});
	check('S9 Hail: defender Ice Body -8 cancelled by attacker Ice Body +8 → 100',
		seen.length > 0 && seen[0].scores[0] === 100,
		`scores=${seen[0] && seen[0].scores}`);
}

// HelpingHand singles penalty + Trick Room tie/latency semantics are cheap
// here: Helping Hand → -10 in singles (892-896); Tailwind with no Trick
// Room and no Tailwind up → 100.
async function s10_singles() {
	const { seen } = await captureAI({
		team1: [pkm('electrode', ['spark', 'tackle', 'doubleteam', 'refresh'], { ability: 'soundproof' })],
		team2: [pkm('machoke', ['helpinghand', 'tailwind', 'karatechop', 'tackle'])],
	});
	check('S10 Helping Hand -10 in singles; Tailwind clean',
		seen.length > 0 && JSON.stringify(seen[0].scores) === JSON.stringify([90, 100, 100, 100]),
		`scores=${seen[0] && seen[0].scores}`);
}

(async () => {
	await s1_immune();
	await s2_levitate();
	await s3_soundproof();
	await s4_highStage();
	await s5_bellydrum();
	await s6_wonderguard();
	await s7_ohko();
	await s8_magnitudeMB();
	await s9_hail();
	await s10_singles();
	console.log(fails === 0 ? 'PASS' : `FAIL (${fails})`);
	process.exit(fails === 0 ? 0 : 1);
})().catch((e) => { console.error('FATAL:', e); process.exit(1); });
