'use strict';
// small_b_smoke.js — Task-3 SmallModules-B scenarios (baton_pass,
// tag_strategy, check_hp, weather, harassment).
//
// Every score below is HAND-COMPUTED from the ported decomp scripts.
// Dice-dependent branches are asserted as exact formulas over the RECORDED
// aiRand draws (Engine.prototype.randN wrapper) — the run is deterministic,
// so each scenario is a byte-identical replay. Dice-consuming sites are
// isolated: initEval/pickSwitch use randNext (unwrapped); all MODULE dice
// and the pickMove tie-break go through randN.
//
// Hand-computed dice counts per turn-1 decision:
//   check_hp @ ≤30% HP  : 1 die per slot whose effect ∈ attacker LOW table
//                         (defender at 100% → TARGET_HIGH empty → 0 dice)
//   check_hp @ >70% HP  : 1 die per slot whose effect ∈ attacker HIGH table
//   baton_pass          : 0 module dice (attacker knows Baton Pass; every
//                         branch is dice-free)
//   weather             : 0 module dice (no dice ops exist in the block)
//   harassment          : 1 die per in-table effect
//   tag_strategy        : exactly the documented singles-quirk dice
//
// Run: node qwenwork/task3/small_b_smoke.js

const { pkm } = require('./ai_test.js');
const { Engine } = require('./ai/engine.js');
// Each scenario gets a clean decisions/dice log.
async function runAI(cfg) { decisions.length = 0; diceLog.length = 0; return _runAIRaw(cfg); }
const _runAIRaw = require('./ai_test.js').runAI;

let decisions = [];
let diceLog = [];
const origDecide = Engine.prototype.decide;
const origRandN = Engine.prototype.randN;
Engine.prototype.randN = function (n) {
	const v = origRandN.call(this, n);
	diceLog.push({ n, v });
	return v;
};
Engine.prototype.decide = function (req) {
	// engine.__hook (set by the scenario's onEngine) runs ONCE, before the
	// first decide — HP/weather fixtures land before turn 1 evaluation.
	if (this.__hook) { const h = this.__hook; this.__hook = null; h(this); }
	const d0 = diceLog.length;
	const choice = origDecide.call(this, req);
	// this.scores exists only for move-evaluation decisions
	if (this.scores) {
		decisions.push({
			turn: this.battle.turn, choice: choice || null,
			scores: this.scores.slice(), dice: diceLog.slice(d0),
		});
	}
	return choice;
};
let fails = 0; let checks = 0;
function expect(label, cond, detail) {
	checks++;
	if (!cond) {
		fails++;
		console.log(`  FAIL  ${label}${detail === undefined ? '' : ` — got ${JSON.stringify(detail)}`}`);
	} else {
		console.log(`  ok    ${label}`);
	}
}
const d256 = (d) => d.dice.filter((x) => x.n === 256); // module dice (pick uses n ≤ 4)

(async () => {
// ═══ S1a: check_hp — attacker ≤30% HP band, LOW tables ═══════════════════
// shellder @25% HP vs fresh munchlax (defender >70% → TARGET_HIGH EMPTY →
// zero round-2 dice). Slots: swordsdance (ATK_UP_2 ∈ ATTACKER_LOW), tackle
// (HIT ∉ any table), rest (REST ∈ HIGH ONLY — gate must flip vs S1b),
// agility (SPEED_UP_2 ∈ LOW). Expected: dice ONLY for slots 0 and 3.
{
	console.log('S1a check_hp low band');
	const { errors } = await runAI({
		seed: [7, 3, 11, 5],
		team1: [pkm('munchlax', ['tackle', 'tackle', 'tackle', 'tackle'])],
		team2: [pkm('shellder', ['swordsdance', 'tackle', 'rest', 'agility']), pkm('machop', ['tackle', 'tackle', 'tackle', 'tackle'])],
		engineOpts: { ai: 'check_hp' },
		onEngine: async (e) => { e.__hook = (eng) => { const s = eng.battle.sides[1].pokemon[0]; s.hp = Math.floor(s.maxhp / 4); }; },
	});
	const d = decisions.find((x) => x.turn === 1);
	expect('no p2 errors', errors === 0, errors);
	expect('hp<30 gate: exactly 2 module dice (LOW-table slots only)', d256(d).length === 2, d.dice);
	const sd = d256(d)[0].v >= 50 ? 98 : 100; // 80.5% of −2
	const ag = d256(d)[1].v >= 50 ? 98 : 100;
	expect('scores == hand-computed [sd,100,100,ag]',
		d.scores[0] === sd && d.scores[1] === 100 && d.scores[2] === 100 && d.scores[3] === ag,
		d.scores);
	expect('rest NOT discouraged at low HP (HIGH-only table)', d.scores[2] === 100, d.scores);
}

// ═══ S1b: check_hp — attacker >70% HP band, HIGH table ═══════════════════
// Full-HP attacker: rest ∈ ATTACKER_HIGH → 1 die; swordsdance/agility ∉
// HIGH → 100. The band gate flips S1a's discouraged set exactly.
{
	console.log('S1b check_hp high band');
	await runAI({
		seed: [7, 3, 11, 5],
		team1: [pkm('munchlax', ['tackle', 'tackle', 'tackle', 'tackle'])],
		team2: [pkm('shellder', ['swordsdance', 'tackle', 'rest', 'agility']), pkm('machop', ['tackle', 'tackle', 'tackle', 'tackle'])],
		engineOpts: { ai: 'check_hp' },
	});
	const d = decisions.find((x) => x.turn === 1);
	expect('hp>70 gate: exactly 1 module die (rest only)', d256(d).length === 1, d.dice);
	expect('swordsdance/agility untouched at high HP', d.scores[0] === 100 && d.scores[3] === 100, d.scores);
	expect('rest == 98/100 per recorded die', d.scores[2] === (d256(d)[0].v >= 50 ? 98 : 100), d.scores);
}

// ═══ S2: baton_pass — turn-1 +5/−2/+2 and banked-stage turn-2 ════════════
// venomoth [swordsdance, tackle, protect, batonpass] (knows Baton Pass →
// no 31.25% dice). Turn 1: turnCount 0 → swordsdance +5 (SetupAtHighHP),
// tackle damaging → skipped (100), protect +2 (lastUsed ≠ Protect/Detect),
// batonpass −2 (turn 0). Zero module dice; pick has ONE winner (105) →
// forced 'move 1'. Turn 2 (+2 atk banked, HP ≥60): swordsdance +1, tackle
// 100, protect +2, batonpass stage 8 > 7 → +2; tie 102 between slots 2/3.
{
	console.log('S2 baton_pass');
	await runAI({
		seed: [7, 3, 11, 5],
		team1: [pkm('munchlax', ['tackle', 'tackle', 'tackle', 'tackle'])],
		team2: [pkm('venomoth', ['swordsdance', 'tackle', 'protect', 'batonpass']), pkm('machop', ['tackle', 'tackle', 'tackle', 'tackle'])],
		engineOpts: { ai: 'baton_pass' },
	});
	const d1 = decisions.find((x) => x.turn === 1);
	const d2 = decisions.find((x) => x.turn === 2);
	expect('turn1 scores == [105,100,102,98]', JSON.stringify(d1.scores) === '[105,100,102,98]', d1.scores);
	expect('turn1 zero 256-module dice', d256(d1).length === 0, d1.dice);
	expect('turn1 forced choice move 1 (unique 105)', d1.choice === 'move 1', d1.choice);
	expect('turn2 scores == [101,100,102,102]', JSON.stringify(d2.scores) === '[101,100,102,102]', d2.scores);
	expect('turn2 choice ∈ {move 3, move 4} (102 tie)', d2.choice === 'move 3' || d2.choice === 'move 4', d2.choice);
	expect('turn2 zero module dice', d256(d2).length === 0, d2.dice);
}

// ═══ S3: weather — turn-1 fall-through +5 everywhere; turn-2 inert ═══════
// chinchou [raindance, thunderbolt, tackle, surf] vs munchlax, weather
// clear, turn-1 entry. Fall-through quirk: the three non-rain moves reach
// the Weather_Sun arm (not sunny → pass) and ALL four take +5 on the
// attacker's first turn → 105×4, zero dice, pick coin among four.
// Turn 2: totalTurns ≠ 0 → every slot stays 100, zero dice.
{
	console.log('S3 weather');
	await runAI({
		seed: [7, 3, 11, 5],
		team1: [pkm('munchlax', ['tackle', 'tackle', 'tackle', 'tackle'])],
		team2: [pkm('chinchou', ['raindance', 'thunderbolt', 'tackle', 'surf']), pkm('machop', ['tackle', 'tackle', 'tackle', 'tackle'])],
		engineOpts: { ai: 'weather' },
	});
	const d1 = decisions.find((x) => x.turn === 1);
	const d2 = decisions.find((x) => x.turn === 2);
	expect('turn1 all four scores 105 (quirk: damaging moves too)',
		JSON.stringify(d1.scores) === '[105,105,105,105]', d1.scores);
	expect('turn1 zero module dice', d256(d1).length === 0, d1.dice);
	expect('turn2 scores all 100 (turn≠0 terminate)', d2.scores.every((s) => s === 100), d2.scores);
	expect('turn2 zero module dice', d256(d2).length === 0, d2.dice);
}

// ═══ S3b: weather — rain already up: Rain Day terminates, others +5 ══════
{
	console.log('S3b weather (rain active)');
	await runAI({
		seed: [7, 3, 11, 5],
		team1: [pkm('munchlax', ['tackle', 'tackle', 'tackle', 'tackle'])],
		team2: [pkm('chinchou', ['raindance', 'thunderbolt', 'tackle', 'surf']), pkm('machop', ['tackle', 'tackle', 'tackle', 'tackle'])],
		engineOpts: { ai: 'weather' },
		onEngine: async (e) => { e.__hook = (eng) => { eng.battle.field.weather = 'raindance'; }; },
	});
	const d1 = decisions.find((x) => x.turn === 1);
	expect('raindance slot stays 100 (weather already raining)', d1.scores[0] === 100, d1.scores);
	expect('other slots +5 → 105', d1.scores.slice(1).every((s) => s === 105), d1.scores);
	expect('choice avoids raindance', d1.choice !== 'move 1', d1.choice);
}

// ═══ S4: harassment — 50% of +2 on in-table status moves only ════════════
// shuppet [willowisp(STATUS_BURN∈), thunderwave(PARALYZE∈), tackle(HIT∉),
// growl(ATK_DOWN∈)] → dice only for slots 0,1,3 (in order); tackle stays
// exactly 100.
{
	console.log('S4 harassment');
	await runAI({
		seed: [7, 3, 11, 5],
		team1: [pkm('munchlax', ['tackle', 'tackle', 'tackle', 'tackle'])],
		team2: [pkm('shuppet', ['willowisp', 'thunderwave', 'tackle', 'growl']), pkm('machop', ['tackle', 'tackle', 'tackle', 'tackle'])],
		engineOpts: { ai: 'harassment' },
	});
	const d = decisions.find((x) => x.turn === 1);
	const md = d256(d);
	expect('exactly 3 module dice (tackle skipped)', md.length === 3, d.dice);
	const f = (v) => (v >= 128 ? 102 : 100); // 50% of +2
	expect('scores == [f(d0),f(d1),100,f(d2)]',
		d.scores[0] === f(md[0].v) && d.scores[1] === f(md[1].v) && d.scores[2] === 100 && d.scores[3] === f(md[2].v),
		d.scores);
}

// magcargo (Fire/Rock) [earthquake, watergun, flamethrower, tackle] vs
// shellder — the fork dex types it mono-WATER (probed; fork deviation from
// the real Water/Rock) — turn 1.
//  • EQ: ground vs Water → ×1 (the decomp chart has no ground→water row
//    either) → class 40. Highest damage: EQ 100×1 vs flamethrower
//    95×1.5 STAB × ½ = 71-equiv → EQ highest → ScoreMove coin: v < 128
//    jumps to CheckBeforeScoring (class 40 → no bonus) with NO +1; else
//    +1 and BYPASSes CheckBeforeScoring. Both paths end in the Earthquake
//    routine → zeroed (singles) partner → "else −3": score = 97 (v<128)
//    or 98.
//  • watergun: non-Surf Water move, defender-partner ability reads
//    AI_UNKNOWN ≠ AI_NOT_HAVE → the −1 Storm Drain fall-through ALWAYS
//    fires: 99 (spec quirk: "defender's partner has Storm Drain → −1").
//    Its ×½ class never penalizes: the TryScoreMinus1 partner-out gate
//    passes trivially in singles.
//  • flamethrower: not-highest → CheckBeforeScoring (class 20: fire vs
//    Water = ½) → CheckFire → no Flash Fire / not Lava Plume → 100.
//  • tackle: Normal ×1 → 100, zero dice.
// Total module dice: EXACTLY 1. Max = 100 tie slots 2/3 → choice move 3|4.
{
	console.log('S5 tag_strategy');
	await runAI({
		seed: [7, 3, 11, 5],
		team1: [pkm('shellder', ['tackle', 'tackle', 'tackle', 'tackle'])],
		team2: [pkm('magcargo', ['earthquake', 'watergun', 'flamethrower', 'tackle']), pkm('machop', ['tackle', 'tackle', 'tackle', 'tackle'])],
		engineOpts: { ai: 'tag_strategy' },
	});
	const d = decisions.find((x) => x.turn === 1);
	const md = d256(d);
	expect('exactly 1 module die (EQ ScoreMove coin)', md.length === 1, d.dice);
	expect('EQ = 97 (coin skipped bonus) | 98 (coin gave +1)', d.scores[0] === (md[0].v < 128 ? 97 : 98), d.scores);
	expect('flamethrower/tackle untouched (100)', d.scores[2] === 100 && d.scores[3] === 100, d.scores);
	expect('choice ∈ {move 3, move 4}', d.choice === 'move 3' || d.choice === 'move 4', d.choice);
}

	console.log(fails ? `FAIL (${fails}/${checks})` : `PASS (${checks} checks)`);
	process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FATAL:', e); process.exit(1); });
