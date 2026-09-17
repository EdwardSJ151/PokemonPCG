// Throwaway probe 2: actions.getDamage, request JSON, side conditions, weather, abilityState.
'use strict';
const Sim = require('/home/pressprexx/Code/GamingResearch/PokemonPCG/pokemon-showdown/dist/sim');
const P = (label, v) => console.log(`${label}: ${typeof v === 'object' ? JSON.stringify(v) : v}`);

const IVS = { hp: 31, atk: 31, def: 31, spe: 31, spa: 31, spd: 31 };
const team1 = [
	{ species: 'arcanine', level: 50, ability: 'flashfire', item: '', ivs: IVS,
		evs: { hp: 0, atk: 252, def: 0, spe: 0, spa: 0, spd: 0 },
		moves: ['flareblitz', 'fly', 'spikes', 'reflect'] },
	{ species: 'gengar', level: 50, ability: 'levitate', item: '', ivs: IVS,
		evs: { hp: 0, atk: 0, def: 0, spe: 0, spa: 252, spd: 0 },
		moves: ['thunderbolt', 'raindance', 'gravity', 'safeguard'] },
];
const team2 = [
	{ species: 'gyarados', level: 50, ability: 'intimidate', item: '', ivs: IVS,
		evs: { hp: 252, atk: 0, def: 0, spe: 0, spa: 0, spd: 0 },
		moves: ['surf', 'protect', 'protect', 'protect'] },
	{ species: 'snorlax', level: 50, ability: 'thickfat', item: '', ivs: IVS,
		evs: { hp: 252, atk: 0, def: 0, spe: 0, spa: 0, spd: 0 },
		moves: ['surf', 'protect', 'protect', 'protect'] },
];

const base = new Sim.BattleStream();
const streams = Sim.getPlayerStreams(base);
base.write(`>start ${JSON.stringify({ formatid: 'gen4customgame', seed: [7, 3, 11, 5] })}`);
base.write(`>player p1 ${JSON.stringify({ name: 'You', team: team1 })}`);
base.write(`>player p2 ${JSON.stringify({ name: 'Cpu', team: team2 })}`);
if (base.errorBuf && base.errorBuf.length) P('stream errors', base.errorBuf.map(e => String(e.message || e)));
const battle = base.battle;

P('battle.format', { id: battle.format.id, mod: battle.format.mod, exists: battle.format.exists, gen: battle.dex.gen });
P('battle.turn', battle.turn);
P('battle.prngSeed', battle.prngSeed);
P('battle.prng class', battle.prng && battle.prng.constructor.name);
P('battle.prng.rng', battle.prng && battle.prng.rng && { name: battle.prng.rng.constructor.name, seed: battle.prng.rng.seed });
P('battle.actions', battle.actions && Object.keys(battle.actions).filter(k => /damage|crit/i.test(k)));
P('actions.getDamage type', battle.actions && typeof battle.actions.getDamage);

const reqLines = () => battle.log.filter(l => l.startsWith('|request|'));
let seen = 0;
function answer() {
	const rl = reqLines();
	while (seen < rl.length) {
		const req = JSON.parse(rl[seen].slice('|request|'.length));
		const side = (seen % 2 === 0) ? 'p1' : 'p2';
		seen++;
		if (seen === 1) {
			P('request#0 keys', Object.keys(req));
			P('request#0', JSON.stringify(req).slice(0, 1800));
		}
		let choice = req.teamPreview ? 'default' : 'move 1';
		// scripted: turn 1 p1 = spikes, then reflect...; keep it simple: move 1 each time
		// except turn 2 where p1 uses fly (charge) to expose the volatile
		if (req.turn === 2 && side === 'p1') choice = 'move 2';
		streams[side].write(choice);
	}
}
// advance a few turns, printing side conditions / volatiles / weather as they appear
for (let t = 0; t < 6; t++) {
	answer();
	const a1 = battle.sides[0].active[0];
	const a2 = battle.sides[1].active[0];
	P(`turn ${t + 1}`, {
		battleTurn: battle.turn,
		p1active: a1 && a1.name, p2active: a2 && a2.name,
		p1vol: a1 && Object.keys(a1.volatiles),
		p2vol: a2 && Object.keys(a2.volatiles),
		p1side: Object.keys(battle.sides[0].sideConditions || {}),
		p2side: Object.keys(battle.sides[1].sideConditions || {}),
		weather: battle.field.weather,
		effectiveWeather: battle.field.effectiveWeather && battle.field.effectiveWeather(),
		fieldVol: Object.keys(battle.field.volatiles || {}),
	});
}

// damage estimation test: Arcanine (atk 252) Flare Blitz vs Snorlax
const src = battle.sides[0].pokemon[0];
const def = battle.sides[1].active[0];
const b = battle;
const seed = b.prng.rng.seed;
const saved = [seed[0], seed[1], seed[2], seed[3]];
const origAdd = b.add;
b.add = () => {};
let n;
try {
	const move = b.dex.getActiveMove('flareblitz');
	move.willCrit = false;
	n = b.actions.getDamage(src, def, move, true);
} finally {
	b.add = origAdd;
	seed[0] = saved[0]; seed[1] = saved[1]; seed[2] = saved[2]; seed[3] = saved[3];
}
P('getDamage flareblitz vs snorlax', { n, seedRestored: JSON.stringify(seed) === JSON.stringify(saved) });

// abilityState keys + set fields
const p = battle.sides[0].pokemon[0];
P('abilityState keys', Object.keys(p.abilityState));
P('abilityState.suppressed', p.abilityState.suppressed);
P('set keys', p.set && Object.keys(p.set));
P('set ivs', p.set && p.set.ivs);
P('set gender', p.set && p.set.gender);

// getHiddenPower helper signature
const hp = battle.dex.getHiddenPower ? battle.dex.getHiddenPower({ hp: 31, atk: 31, def: 31, spa: 31, spd: 31, spe: 31 }) : 'n/a';
P('getHiddenPower all31', hp);

// condition objects via dex
for (const id of ['spikes', 'stealthrock', 'toxicspikes', 'reflect', 'lightscreen', 'mist', 'safeguard', 'luckychant', 'tailwind', 'trickroom', 'futuresight', 'gravity', 'perishsong', 'taunt', 'substitute', 'leechseed', 'trapped', 'ingrain', 'magnetrise', 'meanlook', 'bide', 'embargo', 'gastroacid', 'stockpile', 'nightmare', 'foresight', 'lockon', 'disable', 'encore', 'torment', 'imprison', 'focusenergy', 'healblock', 'roost', 'curse']) {
	const c = b.dex.getCondition ? b.dex.getCondition(id) : (b.dex.data && b.dex.data.Conditions && b.dex.data.Conditions[id]);
	P(`cond ${id}`, c ? { exists: c.exists, name: c.name, onSideChange: !!c.onSideChange } : 'MISSING');
}
