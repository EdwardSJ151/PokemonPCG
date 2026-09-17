#!/usr/bin/env node
/**
 * battle_test.js — Gen 4 mechanic smoke tests (AGENT_PLAN.md, Task 1)
 *
 * Six tiny single battles against the local pokemon-showdown sim. Each battle
 * is built to trigger exactly one mechanic; the raw protocol is scanned for
 * that mechanic's signature line(s). Showdown owns every battle rule — this
 * script only supplies fixed L50 / zero-IV teams and a minimal policy (cycle
 * moves; optional scripted switch). Same seed each battle => deterministic.
 *
 *   1. super-effective 4x : Gengar Thunderbolt  vs Gyarados (Water/Flying)
 *   2. self stat drop     : Lucario Close Combat (user atk/def -1)
 *   3. stat boost (2)     : Garchomp Swords Dance  (atk +2)
 *   4. stat boost (1/1)   : Gyarados  Dragon Dance (atk +1, spe +1)
 *   5. item recovery      : Blissey Leftovers (+1/16 HP per turn)
 *   6. ability on switch  : Gyarados  Intimidate  (opponent atk -1)
 *
 *   node qwenwork/task1/battle_test.js
 */
'use strict';

const Sim = require('/home/pressprexx/Code/GamingResearch/PokemonPCG/pokemon-showdown/dist/sim');

const SEED = [7, 3, 11, 5];
const ZERO = { hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 };

function pkm(species, { ability, item, nature, moves }) {
	return { species, level: 50, ability, item, nature, ivs: ZERO, moves };
}

// Minimal policy: cycle that side's moves round-robin; optionally switch on
// a given turn. `side` is a per-side cursor holder.
function respond(stream, req, side, opts) {
	if (req.teamPreview === true) {
		const n = req.side.pokemon.length;
		const slots = []; for (let i = 0; i < n; i++) slots.push(i + 1);
		return stream.write('team ' + (n > 9 ? slots.join(', ') : slots.join('')));
	}
	const party = req.side.pokemon;
	const avail = [];
	party.forEach((p, i) => { if (!p.active && Number((p.condition || '0/0').split('/')[0]) > 0) avail.push(i + 1); });
	if (req.forceSwitch || !req.active) {
		const s = avail.length ? avail[side.cursor % avail.length] : null;
		side.cursor++;
		return stream.write(s ? `switch ${s}` : 'default');
	}
	if (opts && opts.switchOnTurn && req.turn === opts.switchOnTurn && avail.length) {
		const s = avail[side.cursor % avail.length]; side.cursor++;
		return stream.write(`switch ${s}`);
	}
	const act = Array.isArray(req.active) ? req.active[0] : null;
	const moves = (act && act.moves) || [];
	for (let k = 0; k < moves.length; k++) {
		const i = ((side.cursor + k) % moves.length + moves.length) % moves.length;
		if (!moves[i].disabled) {
			side.cursor = i + 1;
			return stream.write(`move ${i + 1}`);
		}
	}
	return stream.write('default');
}

// Run one battle to completion; returns the full omniscient line list.
// Player streams are ObjectReadStreams (no .on), consumed via for-await.
function runBattle(teamA, teamB, optsA = {}, optsB = {}) {
	const streams = Sim.getPlayerStreams(new Sim.BattleStream());
	const sides = { p1: { cursor: 0 }, p2: { cursor: 0 } };
	const lines = [];
	const ended = new Promise((resolve, reject) => {
		(async () => {
			for await (const chunk of streams.omniscient) {
				for (const line of chunk.split('\n')) {
					lines.push(line);
					if (/^\|(win|tie|end)\|/.test(line)) return resolve(line);
				}
			}
			resolve(lines[lines.length - 1] || '');
		})().catch(reject);
	});
	for (const pid of ['p1', 'p2']) {
		(async () => {
			for await (const chunk of streams[pid]) {
				for (const line of chunk.split('\n')) {
					const m = line.match(/^\|request\|(.*)$/);
					if (m) respond(streams[pid], JSON.parse(m[1]), sides[pid], pid === 'p1' ? optsA : optsB);
				}
			}
		})();
	}
	streams.omniscient.write(`>start ${JSON.stringify({ formatid: 'gen4customgame', seed: SEED })}`);
	streams.omniscient.write(`>player p1 ${JSON.stringify({ name: 'Team A', team: teamA })}`);
	streams.omniscient.write(`>player p2 ${JSON.stringify({ name: 'Team B', team: teamB })}`);
	const guard = setTimeout(() => { console.error('[aborted] battle did not finish in 30s'); process.exit(1); }, 30000);
	return ended.finally(() => clearTimeout(guard)).then(() => lines);
}

const RESULTS = [];
async function test(name, teamA, teamB, predicates, optsA, optsB) {
	console.log(`\n=== ${name} ===`);
	const lines = await runBattle(teamA, teamB, optsA, optsB);
	let all = true;
	for (const { label, re } of predicates) {
		const hits = lines.filter(l => re.test(l));
		const ok = hits.length > 0;
		all = all && ok;
		console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}`);
		hits.slice(0, 3).forEach(h => console.log(`        ${h}`));
	}
	RESULTS.push({ name, ok: all });
}

(async () => {
	// 1. Thunderbolt is 4x on Gyarados (Electric 2x Water x 2x Flying, Gen 4).
	await test('super-effective 4x : Gengar Thunderbolt vs Gyarados',
		[pkm('Gengar', { ability: 'Levitate', nature: 'Timid', moves: ['Thunderbolt'] })],
		[pkm('Gyarados', { ability: 'Intimidate', nature: 'Adamant', moves: ['Waterfall'] })],
		[{ label: '|-supereffective| on Gyarados', re: /\|-?supereffective\|p2a: Gyarados/ }]);

	// 2. Close Combat lowers the USER's stats. In this clone's move data
	//    (modern effect) that is def and spd. Blissey's Will-O-Wisp fails
	//    on Steel (zero damage), so Lucario survives and lands it.
	await test('self stat drop : Lucario Close Combat',
		[pkm('Lucario', { ability: 'Steadfast', nature: 'Jolly', moves: ['Close Combat'] })],
		[pkm('Blissey', { ability: 'Natural Cure', nature: 'Calm', moves: ['Will-O-Wisp'] })],
		[
			{ label: 'Lucario def -1', re: /\|-?unboost\|p1a: Lucario\|def\|1/ },
			{ label: 'Lucario spd -1', re: /\|-?unboost\|p1a: Lucario\|spd\|1/ },
		]);

	// 3. Swords Dance: atk +2.
	await test('stat boost : Garchomp Swords Dance (atk +2)',
		[pkm('Garchomp', { ability: 'Sand Veil', nature: 'Adamant', moves: ['Swords Dance'] })],
		[pkm('Blissey', { ability: 'Natural Cure', nature: 'Calm', moves: ['Thunder Wave'] })],
		[{ label: 'Garchomp atk +2', re: /\|-?boost\|p1a: Garchomp\|atk\|2/ }]);
	// 4. Dragon Dance: atk +1 and spe +1.
	await test('stat boost : Gyarados Dragon Dance (atk +1, spe +1)',
		[pkm('Gyarados', { ability: 'Intimidate', nature: 'Adamant', moves: ['Dragon Dance'] })],
		[pkm('Blissey', { ability: 'Natural Cure', nature: 'Calm', moves: ['Will-O-Wisp'] })],
		[
			{ label: 'Gyarados atk +1', re: /\|-?boost\|p1a: Gyarados\|atk\|1/ },
			{ label: 'Gyarados spe +1', re: /\|-?boost\|p1a: Gyarados\|spe\|1/ },
		]);

	// 5. Leftovers: +1/16 max HP at end of each turn it took damage.
	await test('item recovery : Blissey Leftovers',
		[pkm('Blissey', { ability: 'Natural Cure', item: 'Leftovers', nature: 'Calm', moves: ['Seismic Toss'] })],
		[pkm('Gengar', { ability: 'Levitate', nature: 'Timid', moves: ['Flamethrower'] })],
		[{ label: 'Blissey heals from Leftovers', re: /\|-?heal\|p1a: Blissey\|.*[Ll]eftovers/ }]);

	// 6. Intimidate on switch-in lowers the opponent's atk. p2 leads with
	//    Rotom (its Thunder Wave fails on Garchomp/Ground) and switches to
	//    Gyarados on turn 2, triggering Intimidate.
	await test('ability on switch : Gyarados Intimidate (Garchomp atk -1)',
		[pkm('Garchomp', { ability: 'Sand Veil', nature: 'Adamant', moves: ['Dragon Claw'] })],
		[pkm('Rotom', { ability: 'Levitate', nature: 'Timid', moves: ['Thunder Wave'] }),
		 pkm('Gyarados', { ability: 'Intimidate', nature: 'Adamant', moves: ['Waterfall'] })],
		[{ label: 'Garchomp atk -1 (Intimidate)', re: /\|-?unboost\|p1a: Garchomp\|atk\|1/ }],
		{}, { switchOnTurn: 2 });

	console.log('\n================ SUMMARY ================');
	let failed = 0;
	for (const r of RESULTS) {
		console.log(`  ${r.ok ? 'PASS' : 'FAIL'}  ${r.name}`);
		if (!r.ok) failed++;
	}
	console.log(`  ${RESULTS.length - failed}/${RESULTS.length} mechanics confirmed`);
	process.exit(failed ? 1 : 0);
})().catch(err => { console.error('FATAL:', err); process.exit(1); });
