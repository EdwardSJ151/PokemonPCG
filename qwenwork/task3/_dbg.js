'use strict';
// temp debug copy of smoke.js with full logging
const Sim = require('/home/pressprexx/Code/GamingResearch/PokemonPCG/pokemon-showdown/dist/sim');
const { Engine } = require('./ai/engine.js');
const ZERO = { hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 };
const pkm = (species, moves, item = '') => ({ species, level: 50, ability: '', item, nature: 'quiet', ivs: ZERO, moves });
const TEAM_A = [pkm('gyarados', ['waterfall', 'dragonrush', 'outrage', 'icefang']),
	pkm('snorlax', ['body slam', 'selfdestruct', 'rest', 'encore']),
	pkm('gengar', ['shadowball', 'thunderbolt', 'sludgebomb', 'willowisp'])];
const TEAM_B = [pkm('articuno', ['iciclespear', 'flashcannon', 'agility', 'roost']),
	pkm('groudon', ['earthquake', 'stoneedge', 'eruption', 'roar']),
	pkm('mewtwo', ['psystrike', 'psychic', 'aurasphere', 'recover'])];
const stream = new Sim.BattleStream();
const streams = Sim.getPlayerStreams(stream);
(async () => {
	for await (const chunk of streams.omniscient) {
		for (const line of chunk.split('\n')) console.error('O:', line.slice(0, 120));
	}
})().catch(() => {});
(async () => {
	for await (const chunk of streams.p1) {
		for (const line of chunk.split('\n')) {
			console.error('P1:', line.slice(0, 160));
			if (line.match(/^\|request\|/)) streams.p1.write('default');
		}
	}
})().catch(() => {});
let engine = null, n = 0;
(async () => {
	for await (const chunk of streams.p2) {
		for (const line of chunk.split('\n')) {
			console.error('P2:', line.slice(0, 160));
			const m = line.match(/^\|request\|(.*)$/);
			if (!m) continue;
			const req = JSON.parse(m[1]);
			if (!engine) engine = new Engine(stream.battle, { mask: 1, items: [] });
			const choice = engine.decide(req);
			console.error(`  -> decision (${n++}):`, choice, 'wait=' + req.wait, 'preview=' + req.teamPreview, 'active=' + !!(req.active && req.active[0]));
			if (choice === null) continue;
			streams.p2.write(choice);
		}
	}
})().catch((e) => console.error('P2 ERR', e));
streams.omniscient.write(`>start ${JSON.stringify({ formatid: 'gen4customgame', seed: [7, 3, 11, 5] })}`);
streams.omniscient.write(`>player p1 ${JSON.stringify({ name: 'Team A', team: TEAM_A })}`);
streams.omniscient.write(`>player p2 ${JSON.stringify({ name: 'Team B', team: TEAM_B })}`);
setTimeout(() => process.exit(0), 6000);
