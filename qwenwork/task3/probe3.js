// Throwaway probe 3: dual substream consumers; inspect request JSON, live volatiles,
// side conditions, weather, ability suppression in a gen4 battle.
'use strict';
const Sim = require('../../pokemon-showdown/dist/sim');
const P = (label, v) => console.log(`${label}: ${typeof v === 'object' ? JSON.stringify(v) : v}`);

const IVS = { hp: 31, atk: 31, def: 31, spe: 31, spa: 31, spd: 31 };
const team1 = [
	{ species: 'arcanine', level: 50, ability: 'flashfire', item: '', ivs: IVS,
		evs: { hp: 0, atk: 252, def: 0, spe: 0, spa: 0, spd: 0 },
		moves: ['flareblitz', 'fly', 'spikes', 'protect'] },
	{ species: 'gengar', level: 50, ability: 'gastroacid', item: '', ivs: IVS,
		evs: { hp: 0, atk: 0, def: 0, spe: 0, spa: 252, spd: 0 },
		moves: ['gastroacid', 'raindance', 'taunt', 'safeguard'] },
];
const team2 = [
	{ species: 'gyarados', level: 50, ability: 'intimidate', item: '', ivs: IVS,
		evs: { hp: 252, atk: 0, def: 0, spe: 0, spa: 0, spd: 0 },
		moves: ['surf', 'protect', 'protect', 'protect'] },
];

async function main() {
	const base = new Sim.BattleStream();
	const streams = Sim.getPlayerStreams(base);
	base.write(`>start ${JSON.stringify({ formatid: 'gen4customgame', seed: [7, 3, 11, 5] })}`);
	base.write(`>player p1 ${JSON.stringify({ name: 'You', team: team1 })}`);
	base.write(`>player p2 ${JSON.stringify({ name: 'Cpu', team: team2 })}`);
	const battle = base.battle;

	let turnCounter = 0;
	let reqCount = 0;
	let stopped = false;
	const done = new Promise(res => {
		base.on?.('end', res);
		setTimeout(res, 6000);
	});
	const stop = () => { if (!stopped) { stopped = true; resDone(); } };
	let resDone; const done2 = new Promise(r => resDone = r);

	// p2 consumer: answer with surf/protect, print first request
	(async () => {
		for await (const chunk of streams.p2) {
			if (typeof chunk !== 'string') continue;
			for (const line of chunk.split('\n')) {
				if (!line.trim()) continue;
				if (line.startsWith('|turn|')) turnCounter = Math.max(turnCounter, Number(line.slice(7)));
				else if (line.startsWith('|request|') && !stopped) {
					reqCount++;
					const req = JSON.parse(line.slice('|request|'.length));
					if (reqCount === 1) P('p2 first request', JSON.stringify(req));
					streams.p2.write(req.forceSwitch || !req.active ? 'move 1' : 'move 1');
					if (turnCounter >= 8) stop();
				}
			}
		}
	})();
	// p1 consumer: scripted
	const p1script = { 1: 'move 2', 2: 'move 2', 3: 'move 3', 4: 'switch 2', 5: 'move 1', 6: 'move 2', 7: 'move 3', 8: 'move 4' };
	(async () => {
		for await (const chunk of streams.p1) {
			if (typeof chunk !== 'string') continue;
			for (const line of chunk.split('\n')) {
				if (!line.trim()) continue;
				if (line.startsWith('|turn|')) turnCounter = Math.max(turnCounter, Number(line.slice(7)));
				else if (line.startsWith('|request|') && !stopped) {
					streams.p1.write(p1script[turnCounter] || 'move 4');
					if (turnCounter >= 8) stop();
				}
			}
		}
	})();
	await Promise.race([done, done2]);

	P('final battle.turn', battle.turn);
	P('log tail', battle.log.slice(-20));
	for (const [idx, side] of battle.sides.entries()) {
		P(`side${idx} sideConditions`, Object.keys(side.sideConditions || {}));
		for (const p of side.active || []) {
			if (!p) continue;
			const tv = p.volatiles.twoturnmove;
			P(`side${idx} active ${p.name}`, {
				vol: Object.keys(p.volatiles),
				twoTurnMove: tv ? { moveId: tv.move && tv.move.id } : null,
				ability: p.ability,
				abilityState: p.abilityState ? { id: p.abilityState.id, suppressed: p.abilityState.suppressed } : null,
				status: p.status,
			});
		}
	}
	P('weather', { id: battle.field.weather, effective: battle.field.effectiveWeather() });
	P('dex.getCondition type', typeof battle.dex.getCondition);
	P('pokemon.addVolatile type', typeof battle.sides[0].active[0].addVolatile);
	P('requests seen (p2)', reqCount);
	if (base.errorBuf && base.errorBuf.length) P('stream errors', base.errorBuf.map(e => String(e.message || e)));
}
main().catch(e => console.error('FATAL', e));
