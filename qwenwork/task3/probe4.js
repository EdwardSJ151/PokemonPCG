// Throwaway probe 4: apply side/pseudo/volatile/weather conditions directly on a live
// gen4 battle and print the resulting storage shapes.
'use strict';
const Sim = require('/home/pressprexx/Code/GamingResearch/PokemonPCG/pokemon-showdown/dist/sim');
const P = (label, v) => console.log(`${label}: ${typeof v === 'object' ? JSON.stringify(v) : v}`);

const IVS = { hp: 31, atk: 31, def: 31, spe: 31, spa: 31, spd: 31 };
const team1 = [
	{ species: 'arcanine', level: 50, ability: 'flashfire', item: '', ivs: IVS,
		evs: { hp: 0, atk: 252, def: 0, spe: 0, spa: 0, spd: 0 },
		moves: ['flareblitz', 'extremespeed', 'roar', 'protect'] },
];
const team2 = [
	{ species: 'gyarados', level: 50, ability: 'intimidate', item: '', ivs: IVS,
		evs: { hp: 252, atk: 0, def: 0, spe: 0, spa: 0, spd: 0 },
		moves: ['surf', 'protect', 'protect', 'protect'] },
];

const base = new Sim.BattleStream();
Sim.getPlayerStreams(base);
base.write(`>start ${JSON.stringify({ formatid: 'gen4customgame', seed: [7, 3, 11, 5] })}`);
base.write(`>player p1 ${JSON.stringify({ name: 'You', team: team1 })}`);
base.write(`>player p2 ${JSON.stringify({ name: 'Cpu', team: team2 })}`);
const battle = base.battle;
const side1 = battle.sides[0];
const side2 = battle.sides[1];
const p1 = side1.active[0];
const p2 = side2.active[0];
const src = 'debug';

for (const id of ['spikes', 'toxicspikes', 'stealthrock', 'reflect', 'lightscreen', 'mist', 'safeguard', 'luckychant', 'tailwind', 'futuresight']) {
	const r = side1.addSideCondition(id, src);
	P(`addSideCondition ${id}`, r);
}
for (const id of ['trickroom', 'gravity']) {
	const r = battle.field.addPseudoWeather ? battle.field.addPseudoWeather(id, src) : 'no addPseudoWeather';
	P(`addPseudoWeather ${id}`, r);
}
for (const id of ['raindance', 'sunnyday', 'sandstorm', 'hail']) {
	const r = battle.field.setWeather ? battle.field.setWeather(id, p1, null) : 'no setWeather';
	P(`setWeather ${id}`, { r, fieldWeather: battle.field.weather });
}
for (const id of ['perishsong', 'stockpile', 'nightmare', 'foresight', 'meanlook', 'ingrain', 'magnetrise',
	'taunt', 'substitute', 'leechseed', 'disable', 'encore', 'torment', 'bide', 'trapped', 'focusenergy',
	'gastroacid', 'roost', 'curse', 'healblock', 'partiallytrapped', 'twoturnmove', 'mustrecharge', 'futuremove']) {
	const r = p2.addVolatile(id, p1);
	P(`addVolatile ${id}`, { r, inMap: !!p2.volatiles[id] });
}
P('side1 sideConditions', Object.keys(side1.sideConditions));
const spikes = side1.sideConditions['spikes'];
P('spikes state', spikes ? { layers: spikes.effectState ? spikes.effectState.layers : spikes.layers, duration: spikes.duration } : null);
const tss = side1.sideConditions['toxicspikes'];
P('toxicspikes state', tss ? { layers: tss.effectState ? tss.effectState.layers : tss.layers } : null);
P('pseudoWeather keys', Object.keys(battle.field.pseudoWeather || {}));
P('weather', battle.field.weather);
for (const [k, v] of Object.entries(p2.volatiles)) {
	P(`volatile ${k}`, {
		duration: v.duration,
		layers: v.effectState ? v.effectState.layers : undefined,
		move: v.effectState ? v.effectState.move : undefined,
		pp: v.effectState ? v.effectState.pp : undefined,
		dmg: v.effectState ? v.effectState.dmg : undefined,
	});
}
P('p2 ignoringAbility', typeof p2.ignoringAbility === 'function' ? p2.ignoringAbility() : 'n/a');
P('p1 ignoringAbility', p1.ignoringAbility());
// status conditions
p2.setStatus('brn'); P('setStatus brn', { status: p2.status, has: p2.hasStatus ? p2.hasStatus('brn') : 'n/a' });
p2.setStatus('slp'); P('setStatus slp', { status: p2.status, nightmare: !!p2.volatiles['nightmare'] });
// heal / boost / cureStatus
const healed = p2.heal(30, p1, battle.dex.getMove ? battle.dex.getMove('healbell') : null);
P('heal(30)', { healed, hp: p2.hp, maxhp: p2.maxhp });
P('cureStatus', p2.cureStatus(false), 'status now:', p2.status);
battle.boost({ atk: 2 }, p1, p1, null);
P('boost atk+2 on p1', p1.boosts);
P('calcStat spe', p1.calculateStat('spe', p1.boosts.spe, 1));
P('getTypes', p1.getTypes());
P('hasType', p1.hasType('fire'), p1.hasType('ghost'));
P('dex.conditions.get exist', battle.dex.conditions && battle.dex.conditions.get('perishsong') && battle.dex.conditions.get('perishsong').exists);
P('dex.moves.get perishsong', battle.dex.moves.get('perishsong') && battle.dex.moves.get('perishsong').exists);
