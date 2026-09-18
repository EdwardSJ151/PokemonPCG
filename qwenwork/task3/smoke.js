'use strict';
// Smoke: gen4 singles battle, p2 driven by the AI engine (decide() on each
// request), p1 sends 'default'. Beyond the skeleton invariants (battle
// completes, zero p2 |error| lines, every non-wait request answered) the two
// scenarios pin PostKOSwitchIn:
//   A (type advantage): fragile lead magnemite (Electric/Steel) grinds down
//     against the Normal defender until it faints; the bench has machoke
//     (Fighting mono: 2x on Normal, scores 160), onix (Rock/Ground: 120),
//     gengar (Ghost/Poison: 80) → the first p2 switch MUST be machoke, the
//     unique stage-1 max with an SE move.
//   B (tie → earlier party order): machoke (team slot 1) and primeape
//     (team slot 2) both score 160 and both own SE moves → the first p2
//     switch MUST be machoke (strict `<` keeps the earlier slot).
// Temporary — replaced by ai_test.js in the integration phase.
const Sim = require('../../pokemon-showdown/dist/sim');
const { Engine } = require('./ai/engine.js');

const ZERO = { hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 };
const pkm = (species, moves, nature = 'quiet') => ({ species, level: 50, ability: '', item: '', nature, ivs: ZERO, moves });

const TEAM_A = [
	pkm('snorlax', ['body slam', 'selfdestruct', 'rest', 'encore'], 'bold'),
	pkm('gengar', ['shadowball', 'thunderbolt', 'sludgebomb', 'willowisp']),
	pkm('zapdos', ['thunderbolt', 'drillpeck', 'heatwave', 'rest']),
];
const TEAM_B_A = [
	pkm('magnemite', ['thunderbolt', 'swift', 'thunderwave', 'rest'], 'modest'),
	pkm('machoke', ['karate chop', 'cross chop', 'focus punch', 'rest']),
	pkm('onix', ['rockslide', 'earthquake', 'body slam', 'roar']),
	pkm('gengar', ['shadowball', 'thunderbolt', 'sludgebomb', 'willowisp']),
];
const TEAM_B_B = [
	pkm('magnemite', ['thunderbolt', 'swift', 'thunderwave', 'rest'], 'modest'),
	pkm('machoke', ['karate chop', 'cross chop', 'focus punch', 'rest']),
	pkm('primeape', ['karate chop', 'cross chop', 'low kick', 'focus punch']),
];

const MASK = 1; // BASIC

async function run(teamB) {
	const stream = new Sim.BattleStream();
	const streams = Sim.getPlayerStreams(stream);
	const p2reqs = { all: 0, need: 0 };
	const p2errors = { n: 0 };
	const omni = [];
	const ended = new Promise((resolve, reject) => {
		(async () => {
			for await (const chunk of streams.omniscient) {
				for (const line of chunk.split('\n')) {
					omni.push(line);
					if (/^\|(win|tie|end)\|/.test(line)) return resolve(line);
				}
			}
			resolve(omni[omni.length - 1] || '');
		})().catch(reject);
	});
	// p1: default policy
	(async () => {
		for await (const chunk of streams.p1) {
			for (const line of chunk.split('\n')) {
				if (line.match(/^\|request\|/)) streams.p1.write('default');
			}
		}
	})();
	// p2: the AI
	let engine = null, aiChoices = 0;
	(async () => {
		for await (const chunk of streams.p2) {
			for (const line of chunk.split('\n')) {
				if (line.startsWith('|error|')) { p2errors.n++; continue; }
				const m = line.match(/^\|request\|(.*)$/);
				if (!m) continue;
				p2reqs.all++;
				if (!m[1].includes('{"wait":true')) p2reqs.need++;
				const req = JSON.parse(m[1]);
				if (!engine) {
					const battle = stream.battle;
					if (!battle) { console.error('FATAL: no live battle on stream'); process.exit(1); }
					engine = new Engine(battle, { mask: MASK, items: [] });
				}
				const choice = engine.decide(req);
				if (choice === null) continue;
				aiChoices++;
				streams.p2.write(choice);
			}
		}
	})();
	streams.omniscient.write(`>start ${JSON.stringify({ formatid: 'gen4customgame', seed: [7, 3, 11, 5] })}`);
	streams.omniscient.write(`>player p1 ${JSON.stringify({ name: 'Team A', team: TEAM_A })}`);
	streams.omniscient.write(`>player p2 ${JSON.stringify({ name: 'Team B', team: teamB })}`);
	const guard = setTimeout(() => { console.error('[aborted] battle did not finish in 30s'); process.exit(1); }, 30000);
	const win = await ended.finally(() => clearTimeout(guard));
	const switches = omni.filter((l) => l.startsWith('|switch|p2a: ')).map((l) => l.split('|')[2].slice('p2a: '.length)).slice(1);
	return { win, aiChoices, p2errors: p2errors.n, need: p2reqs.need, all: p2reqs.all, switches };
}

(async () => {
	const rA = await run(TEAM_B_A);
	const rB = await run(TEAM_B_B);
	console.log('A (type advantage):');
	console.log(`  p2 forced switches   : ${rA.switches.join('  ')}`);
	console.log(`  ai choices           : ${rA.aiChoices}   non-wait requests ${rA.need}   p2 errors ${rA.p2errors}`);
	console.log('B (tie -> earlier party order):');
	console.log(`  p2 forced switches   : ${rB.switches.join('  ')}`);
	console.log(`  ai choices           : ${rB.aiChoices}   non-wait requests ${rB.need}   p2 errors ${rB.p2errors}`);
	const firstA = rA.switches[0] || '';
	const firstB = rB.switches[0] || '';
	const okA = rA.p2errors === 0 && rA.need === rA.aiChoices && rA.aiChoices > 0
		&& /^\|win\||^\|tie\|/.test(rA.win) && firstA.includes('Machoke');
	const okB = rB.p2errors === 0 && rB.need === rB.aiChoices && rB.aiChoices > 0
		&& /^\|win\||^\|tie\|/.test(rB.win) && firstB.includes('Machoke') && !firstB.includes('Primeape');
	console.log(okA && okB ? 'PASS' : 'FAIL');
	process.exit(okA && okB ? 0 : 1);
})().catch((e) => { console.error('FATAL:', e); process.exit(1); });
