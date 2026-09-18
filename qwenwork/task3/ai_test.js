'use strict';
// ai_test.js — task 3 validation harness (PLAN.md "Validation plan").
//
// Exports runAI(opts): runs a complete gen4customgame singles battle
// in-process (BattleStream — the smoke.js-pinned pattern: omniscient carries
// the transcript for collection/end detection, p1 answers every request with
// 'default', p2 is driven by Engine.decide per |request|). Scenario files
// per module `require('./ai_test.js')` and assert on the returned state.
//
// CLI — completeness + determinism over a module set:
//   node ai_test.js basic                  — one battle, p2 = Engine{ai:'basic'}
//   node ai_test.js basic,weather          — module set (canonical order applies)
//   node ai_test.js full                   — Frontier profile (basic+eval_attack+expert)
//   node ai_test.js none                   — empty thinkingMask (score-100 random tie)
//   node ai_test.js all                    — the full T1 matrix (11 solo + full +
//                                            basic,weather + none)
// Per run the checks are: battle reaches |win|/|tie|; zero |error| lines on
// the p2 side; every p2 choice matches the legal-choice regex; every
// non-wait request answered; and TWO same-seed runs produce byte-identical
// transcripts (covers the AI dice, the damage estimates and the PRNG-snapshot
// invariant). T3 (PRNG neutrality) asserts battle.prng.rng.seed is unchanged
// across every decide() call.

const Sim = require('../../pokemon-showdown/dist/sim');
const { Engine } = require('./ai/engine.js');

const ZERO = { hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 };
const pkm = (species, moves, extra = {}) => ({
	species, level: 50, item: '', nature: 'serious', ivs: ZERO, moves,
	// dex ability #1 — gen4 Gengar MUST have Levitate (an abilityless team
	// silently turns Earthquake-on-Gengar from immune into a super-effective
	// hit and blinds every ability-reading AI module). Override per set.
	ability: (Sim.Dex.mod('gen4').species.get(species).abilities || {})['0'] || '',
	...extra,
});

// Standard completeness pair (T1): deliberately broad effect coverage —
// status, setup, hazards, phaze, healing, priority, sleep — so every loaded
// module's checks actually execute against live state.
const TEAM_P1 = [
	pkm('snorlax', ['bodyslam', 'earthquake', 'swordsdance', 'rest'], { nature: 'bold' }),
	pkm('gengar', ['shadowball', 'thunderbolt', 'willowisp', 'hypnosis']),
	pkm('zapdos', ['thunderbolt', 'drillpeck', 'heatwave', 'lightscreen']),
];
const TEAM_P2 = [
	pkm('machoke', ['karatechop', 'focuspunch', 'bulkup', 'rest']),
	pkm('onix', ['rockslide', 'earthquake', 'stealthrock', 'roar']),
	pkm('gengar', ['shadowball', 'thunderbolt', 'suckerpunch', 'protect']),
];

const CHOICE_RE = /^(move [1-4]|switch [1-6]|team .*|default)$/;

// opts: { seed, team1, team2, engineOpts { ai | modules, items, … },
//         p1Choice, onEngine(engine) }
async function runAI(opts = {}) {
	const seed = opts.seed || [7, 3, 11, 5];
	const team1 = opts.team1 || TEAM_P1;
	const team2 = opts.team2 || TEAM_P2;
	const stream = new Sim.BattleStream();
	const streams = Sim.getPlayerStreams(stream);
	const transcript = [];
	const choices = [];
	const p2 = { errors: 0, requests: 0, needed: 0 };
	let engine = null;
	const ended = new Promise((resolve, reject) => {
		(async () => {
			for await (const chunk of streams.omniscient) {
				for (const line of chunk.split('\n')) {
					transcript.push(line);
					if (/^\|(win|tie|end)\|/.test(line)) return resolve(line);
				}
			}
			resolve(transcript[transcript.length - 1] || '');
		})().catch(reject);
	});
	// p1: default policy
	(async () => {
		for await (const chunk of streams.p1) {
			for (const line of chunk.split('\n')) {
				if (line.match(/^\|request\|/)) streams.p1.write(opts.p1Choice || 'default');
			}
		}
	})().catch(() => {});
	// p2: the AI
	(async () => {
		for await (const chunk of streams.p2) {
			for (const line of chunk.split('\n')) {
				if (line.startsWith('|error|')) { p2.errors++; continue; }
				const m = line.match(/^\|request\|(.*)$/);
				if (!m) continue;
				p2.requests++;
				if (!m[1].includes('{"wait":true')) p2.needed++;
				const req = JSON.parse(m[1]);
				if (!engine) {
					engine = new Engine(stream.battle, opts.engineOpts || {});
					if (opts.onEngine) opts.onEngine(engine);
				}
				const choice = engine.decide(req);
				if (choice === null) continue;
				choices.push(choice);
				streams.p2.write(choice);
			}
		}
	})().catch(() => {});
	streams.omniscient.write(`>start ${JSON.stringify({ formatid: 'gen4customgame', seed })}`);
	streams.omniscient.write(`>player p1 ${JSON.stringify({ name: 'Team A', team: team1 })}`);
	streams.omniscient.write(`>player p2 ${JSON.stringify({ name: 'Team B', team: team2 })}`);
	const guard = setTimeout(() => { console.error('[aborted] battle did not finish in 30s'); process.exit(1); }, 30000);
	const win = await ended.finally(() => clearTimeout(guard));
	return { win, choices, transcript, errors: p2.errors, requests: p2.requests, needed: p2.needed, engine };
}

// T1+T2 per module set.
async function completeness(label, engineOpts) {
	const a = await runAI({ engineOpts });
	const b = await runAI({ engineOpts });
	const errs = [];
	if (!/^\|(win|tie)\|/.test(a.win)) errs.push(`no result (${a.win})`);
	if (a.errors) errs.push(`${a.errors} p2 |error| line(s)`);
	const bad = a.choices.filter((c) => !CHOICE_RE.test(c));
	if (bad.length) errs.push(`illegal choices: ${bad.join(',')}`);
	if (a.choices.length < a.needed) errs.push(`unanswered requests (${a.choices.length}/${a.needed})`);
	// |t:| lines carry real wall-clock seconds — same-second runs would pass
	// by luck; strip them (everything else in a seeded battle is deterministic).
	const strip = (t) => t.filter((l) => !/^\|t:\|/.test(l)).join('\n');
	if (strip(a.transcript) !== strip(b.transcript)) errs.push('NOT DETERMINISTIC');
	console.log(`${errs.length ? 'FAIL' : 'ok  '}  ${label.padEnd(44)} choices=${String(a.choices.length).padStart(3)} result=${a.win.replace(/\|/g, '')}`);
	for (const e of errs) console.log(`      · ${e}`);
	return errs.length === 0;
}

// T3 — PRNG neutrality: the engine PRNG must not advance inside decide()
// (damage estimates snapshot/restore it; the AI uses its own dice).
async function prngNeutrality(engineOpts) {
	let checked = 0, broken = 0;
	const orig = Engine.prototype.decide;
	Engine.prototype.decide = function (req) {
		if (this.battle.prng && this.battle.prng.rng && this.battle.prng.rng.seed) {
			const before = this.battle.prng.rng.seed.join(',');
			const r = orig.call(this, req);
			checked++;
			if (this.battle.prng.rng.seed.join(',') !== before) broken++;
			return r;
		}
		return orig.call(this, req);
	};
	try { await runAI({ engineOpts }); } finally { Engine.prototype.decide = orig; }
	const ok = checked > 0 && broken === 0;
	console.log(`${ok ? 'ok  ' : 'FAIL'}  PRNG neutrality: ${checked} decisions, ${broken} seed changes`);
	return ok;
}

if (require.main === module) {
(async () => {
	const arg = process.argv[2];
	if (!arg) {
		console.log('usage: node ai_test.js <ids|full|none|all>   (ids: comma-separated module ids)');
		process.exit(2);
	}
	let allOk = true;
	const run = async (label, opts) => { allOk = await completeness(label, opts) && allOk; };
	if (arg === 'all') {
		for (const id of ['basic', 'eval_attack', 'expert', 'setup_first_turn', 'risky',
			'prioritize_extremes', 'baton_pass', 'tag_strategy', 'check_hp', 'weather',
			'harassment']) await run(`solo ${id}`, { ai: id });
		await run('profile full', { ai: 'full' });
		await run('profile basic,weather', { ai: 'basic,weather'.split(',') });
		await run('none (empty mask)', {});
		allOk = await prngNeutrality({ ai: 'basic' }) && allOk;
	} else {
		await run(`[${arg}] p2 AI vs default`, arg === 'none' ? {} : { ai: arg === 'full' ? 'full' : arg.split(',') });
		allOk = await prngNeutrality(arg === 'none' ? {} : { ai: arg === 'full' ? 'full' : arg.split(',') }) && allOk;
	}
	console.log(allOk ? 'PASS' : 'FAIL');
	process.exit(allOk ? 0 : 1);
})().catch((e) => { console.error('FATAL:', e); process.exit(1); });
}

module.exports = { runAI, completeness, prngNeutrality, pkm, TEAM_P1, TEAM_P2 };
