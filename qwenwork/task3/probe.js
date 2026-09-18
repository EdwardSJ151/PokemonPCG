// Throwaway probe: pin the fork engine surface for the task3 AI port.
'use strict';
const Sim = require('../../pokemon-showdown/dist/sim');

const out = [];
const P = (label, v) => console.log(`${label}: ${typeof v === 'object' ? JSON.stringify(v) : v}`);

const dex = Sim.Dex.forGen(4);
P('gen4 dex.gen', dex.gen);
const fmt = Sim.Dex.formats.get('gen4customgame', true);
P('gen4customgame format', { id: fmt.id, exists: fmt.exists, mod: fmt.mod, gameType: fmt.gameType, teamPreview: fmt.teamPreview });
const fmt2 = Sim.Dex.formats.get('gen4randombattle', true);
P('gen4randombattle format', { id: fmt2.id, exists: fmt2.exists, mod: fmt2.mod, teamPreview: fmt2.teamPreview });

// --- moves of interest ---
const moves = ['struggle', 'fly', 'dive', 'dig', 'bounce', 'bide', 'hyperbeam', 'gigaimpact',
	'skulbash', 'roarofthesea', 'breakout', 'solarbeam', 'focuspunch', 'chargebeam',
	'skyattack', 'weatherball', 'naturalgift', 'judgment', 'hiddenpower', 'fissure', 'horndrill',
	'gyroball', 'seismictoss', 'psywave', 'dragonrage', 'sonicboom', 'magnitude', 'return',
	'pursuit', 'fling', 'naturepower', 'meteorbeam', 'wrap', 'bind', 'firespin', 'whirlpool',
	'infestation', 'shellburst', 'rage', 'thunder', 'raindance', 'perishsong', 'curse', 'trickroom'];
for (const id of moves) {
	const m = dex.moves.get(id);
	if (!m.exists) { P(`move ${id}`, 'MISSING'); continue; }
	P(`move ${id}`, {
		type: m.type, cat: m.category, bp: m.basePower, acc: m.accuracy,
		first: m.firstTurn || null, second: m.secondTurn || null,
		dmg: m.damage || null, cb: !!m.onBasePower,
	});
}

// --- items: which ids exist in the fork? ---
const itemIds = ['potion', 'superpotion', 'hyperpotion', 'maxpotion', 'fullrestore', 'fullheal',
	'antidote', 'awakening', 'burnheal', 'iceheal', 'paralyzeheal', 'parlyzheal',
	'revive', 'maxrevive', 'healball', 'healpowder', 'xattack', 'xdefense', 'xspecial',
	'xspattack', 'xspdefense', 'xspeed', 'xaccuracy', 'guardspec', 'blacksludge',
	'lifeorb', 'expertbelt', 'dreadplate', 'dracoplate', 'earthplate', 'fistplate', 'flameplate',
	'icicleplate', 'insectplate', 'ironplate', 'meadowplate', 'mindplate', 'skyplate',
	'splashplate', 'spookyplate', 'stoneplate', 'toxicplate', 'zapplate', 'adamantorb',
	'flameorb', 'griseousorb', 'lustrousorb', 'cheriberry', 'chestoberry', 'pechaberry',
	'wacanberry', 'occaberry', 'quickclaw'];
const have = [];
for (const id of itemIds) if (dex.items.get(id).exists) have.push(id);
P('items present', have);
const missing = itemIds.filter(id => !dex.items.get(id).exists);
P('items missing', missing);

// --- abilities: which ids exist? ---
const abilities = ['moldbreaker', 'wonderguard', 'filter', 'solidrock', 'wiseglass', 'tintedlens',
	'levitate', 'cloudnine', 'airlock', 'insomnia', 'vitalspirit', 'limber', 'voltabsorb',
	'waterabsorb', 'flashfire', 'simple', 'guts', 'truant', 'multitype', 'slowstart', 'stench',
	'runaway', 'pickup', 'honeygather', 'owntempo', 'noguard', 'clearbody', 'whitesmoke',
	'keeneye', 'hypercutter', 'speedboost', 'suctioncups', 'magicguard', 'scrappy', 'magnetpull',
	'shadowtag', 'arenatrap', 'naturalcure', 'swiftswim', 'leafguard', 'hydration', 'flowergift',
	'solarpower', 'icebody', 'stall', 'motordrive', 'dryskin', 'damp', 'sturdy', 'soundproof',
	'poisonheal', 'raindish', 'sandveil', 'sandforce', 'static', 'lightningrod', 'lightningrod',
	'multiscale', 'regenerator'];
const ab = [];
for (const id of abilities) if (dex.abilities.get(id).exists) ab.push(id);
P('abilities present', ab);
P('abilities missing', abilities.filter(id => !dex.abilities.get(id).exists));

// --- conditions (volatiles/side conditions) ---
const conds = ['trapped', 'trapper', 'partiallytrapped', 'meanlook', 'ingrain', 'magnetrise',
	'perishsong', 'embargo', 'healblock', 'protect', 'detect', 'endure', 'substitute', 'taunt',
	'encore', 'disable', 'stockpile', 'nightmare', 'foresight', 'lockon', 'leechseed', 'torment',
	'imprison', 'gastroacid', 'bide', 'curse', 'roost', 'focusenergy', 'safeguard', 'trickroom',
	'futuresight', 'doomdesire', 'gravity', 'partiallytrapped', 'wrap', 'bound', 'wrapped',
	'spikes', 'toxicspikes', 'stealthrock', 'reflect', 'lightscreen', 'mist', 'luckychant', 'tailwind'];
const ok = [];
for (const id of conds) if (dex.data.Conditions[id]) ok.push(id);
P('conditions present', ok);
P('conditions missing', conds.filter(id => !dex.data.Conditions[id]));

// --- live battle probe ---
const base = new Sim.BattleStream();
const streams = Sim.getPlayerStreams(base);
const IVS = { hp: 31, atk: 31, def: 31, spe: 31, spa: 31, spd: 31 };
const team1 = [
	{ species: 'arcanine', level: 50, ability: 'flashfire', item: 'lifeorb', ivs: IVS,
		evs: { hp: 0, atk: 252, def: 0, spe: 0, spa: 4, spd: 0 },
		moves: ['flareblitz', 'extremespeed', 'roar', 'protect'] },
	{ species: 'poliwrath', level: 50, ability: 'damp', item: 'blacksludge', ivs: IVS,
		evs: { hp: 252, atk: 0, def: 0, spe: 0, spa: 4, spd: 0 },
		moves: ['surf', 'hydropump', 'raindance', 'protect'] },
];
const team2 = [
	{ species: 'magcargo', level: 50, ability: 'flamebody', item: '', ivs: IVS,
		evs: { hp: 0, atk: 0, def: 252, spe: 0, spa: 0, spd: 0 },
		moves: ['magmastorm', 'eruption', 'sunnyday', 'protect'] },
	{ species: 'wailord', level: 50, ability: 'waterveil', item: '', ivs: IVS,
		evs: { hp: 252, atk: 0, def: 0, spe: 0, spa: 0, spd: 0 },
		moves: ['whirlpool', 'surf', 'protect', 'rest'] },
];
base.write(`>start ${JSON.stringify({ formatid: 'gen4customgame', seed: [7, 3, 11, 5] })}`);
base.write(`>player p1 ${JSON.stringify({ name: 'You', team: team1 })}`);
if (base.errorBuf && base.errorBuf.length) P('stream errors', base.errorBuf.map(e => String(e.message || e)));
base.write(`>player p2 ${JSON.stringify({ name: 'Cpu', team: team2 })}`);

const battle = base.battle;
// advance the battle: answer requests as they appear in the log.
// Requests are emitted in side order (p1 then p2) each round.
let seenRequests = 0;
for (let round = 0; round < 4; round++) {
	const rl = battle.log.filter(l => l.startsWith('|request|'));
	while (seenRequests < rl.length) {
		const req = JSON.parse(rl[seenRequests].slice('|request|'.length));
		const side = (seenRequests % 2 === 0) ? 'p1' : 'p2';
		seenRequests++;
		P(`request#${seenRequests - 1} ${side}`, {
			teamPreview: !!req.teamPreview, turn: req.turn, mode: req.mode,
			active: req.active && req.active.map(a => a.name),
			active0move: req.active && req.active[0] && req.active[0].move && req.active[0].move[0],
		});
		streams[side].write(req.teamPreview ? 'default' : (req.mode === 'switch' ? 'switch 1' : 'move 1'));
		const rl2 = battle.log.filter(l => l.startsWith('|request|'));
		if (rl2.length === seenRequests) break;
	}
}
P('log tail', battle.log.slice(-8));
const p1 = battle.sides[0].active && battle.sides[0].active[0];
const p2 = battle.sides[1].active && battle.sides[1].active[0];
if (p1) {
	P('p1 active keys', Object.keys(p1).filter(k => !k.startsWith('_') && typeof p1[k] !== 'function'));
	P('p1 hp/maxhp', { hp: p1.hp, maxhp: p1.maxhp, baseMaxhp: p1.baseMaxhp, fainted: p1.fainted });
	P('p1 gender', p1.gender);
	P('p1 ability', { ability: p1.ability, abilityState: p1.abilityState });
	P('p1 boosts', p1.boosts);
	P('p1 volatiles', Object.keys(p1.volatiles || {}));
	P('p1 moveSlots', p1.moveSlots);
	P('p1 item', p1.item);
	P('p1 ivs', p1.ivs);
	P('p1 lastMoveUsed', p1.lastMoveUsed);
	P('p1 attackedBy', p1.attackedBy);
	P('p1 newlySwitched', p1.newlySwitched);
	P('p1 statuses', Object.keys(p1.statuses || {}));
}
if (battle.sides[1]) {
	P('p2 sideConditions', battle.sides[1].sideConditions && Object.keys(battle.sides[1].sideConditions));
	P('p2 slotConditions', battle.sides[1].slotConditions && Object.keys(battle.sides[1].slotConditions));
}
P('field weather', { weather: battle.field.weather, effective: battle.field.effectiveWeather && battle.field.effectiveWeather() });
P('field volatiles', Object.keys(battle.field.volatiles || {}));
P('dex hp helper', typeof dex.getHiddenPower);
P('getDamage fn', typeof battle.getDamage);
P('heal fn', typeof battle.heal);
P('boost fn', typeof battle.boost);
if (p1) P('pokemon.cureStatus', typeof p1.cureStatus);

console.log(out.join('\n'));
